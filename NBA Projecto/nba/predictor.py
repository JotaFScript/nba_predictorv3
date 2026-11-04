"""Fachada que une datos + motor + modelo para el servidor y los tests."""
from __future__ import annotations

from datetime import date
from statistics import NormalDist

from . import config
from .features import Motor, construir_partidos
from .modelo import Modelo


class Predictor:
    def __init__(self, motor: Motor, modelo: Modelo, filas: list, n_partidos: int):
        self.motor, self.modelo, self.filas, self.n_partidos = motor, modelo, filas, n_partidos

    @classmethod
    def construir(cls, registros: list[dict], progreso=print) -> "Predictor":
        progreso("Procesando partidos...")
        partidos = construir_partidos(registros)
        if len(partidos) < 400:
            raise ValueError("El historial tiene muy pocos partidos; pulsa «Actualizar datos».")
        motor = Motor()
        filas = motor.procesar(partidos)
        progreso("Entrenando modelo...")
        modelo = Modelo.entrenar(filas)
        return cls(motor, modelo, filas, len(partidos))

    # ---- consultas ----
    def nombre(self, abrev: str) -> str:
        return self.motor.nombres.get(abrev) or config.EQUIPOS.get(abrev, abrev)

    def equipos_activos(self) -> list[dict]:
        ultima = max((s.temporada or 0) for s in self.motor.estados.values())
        activos = [a for a, s in self.motor.estados.items() if s.temporada == ultima]
        if len(activos) < 20:                       # inicio de temporada: usa también la anterior
            activos = [a for a, s in self.motor.estados.items() if (s.temporada or 0) >= ultima - 1]
        return sorted(({"abrev": a, "nombre": self.nombre(a)} for a in activos), key=lambda e: e["nombre"])

    def predecir(self, local: str, visitante: str, fecha: date | None = None,
                 ajuste_local: float = 0.0, ajuste_visita: float = 0.0, linea: float | None = None) -> dict:
        if local == visitante:
            raise ValueError("El equipo local y el visitante deben ser distintos.")
        fecha = fecha or date.today()
        f = self.motor.variables(local, visitante, fecha)
        if f is None:
            raise ValueError("No hay historial suficiente de alguno de los equipos.")
        r = self.modelo.predecir(f, ajuste_local, ajuste_visita)
        r.update({"local": local, "visitante": visitante,
                  "nombre_local": self.nombre(local), "nombre_visitante": self.nombre(visitante),
                  "fecha": fecha.isoformat(), "contexto": self._contexto(local, visitante, fecha)})
        m, s = r["margen"], r["sigma"]
        N = NormalDist()
        cdf = lambda x: N.cdf((x - m) / s)
        r["distribucion"] = [
            {"etiqueta": "Visitante por 11+", "p": cdf(-10.5)},
            {"etiqueta": "Visitante por 6–10", "p": cdf(-5.5) - cdf(-10.5)},
            {"etiqueta": "Visitante por 1–5", "p": cdf(0) - cdf(-5.5)},
            {"etiqueta": "Local por 1–5", "p": cdf(5.5) - cdf(0)},
            {"etiqueta": "Local por 6–10", "p": cdf(10.5) - cdf(5.5)},
            {"etiqueta": "Local por 11+", "p": 1 - cdf(10.5)},
        ]
        r["intervalo80"] = [m - 1.2816 * s, m + 1.2816 * s]
        if linea is not None:
            # linea = handicap del local (ej. -5.5: el local da 5.5). Cubre si margen > -linea.
            r["linea"] = {"valor": linea, "prob_cubre_local": 1 - cdf(-linea)}
        return r

    def _contexto(self, local: str, visitante: str, fecha: date) -> dict:
        ctx = {}
        for lado, eq in (("local", local), ("visita", visitante)):
            st = self.motor.estados[eq]
            ctx[f"elo_{lado}"] = round(st.elo)
            ctx[f"descanso_{lado}"] = max((fecha - st.ultima).days - 1, 0) if st.ultima else None
            ctx[f"forma_{lado}"] = "".join("G" if t[7] else "P" for t in st.hist[-5:])
        return ctx

    def ranking(self) -> list[dict]:
        ultima = max((s.temporada or 0) for s in self.motor.estados.values())
        filas = []
        for a, s in self.motor.estados.items():
            if s.temporada != ultima or not s.hist:
                continue
            h = s.hist[-10:]
            filas.append({
                "abrev": a, "nombre": self.nombre(a), "elo": round(s.elo),
                "record": f"{s.w}-{s.l}",
                "neto10": sum(t[3] for t in h) / len(h),
                "ataque10": sum(t[4] for t in h) / len(h),
                "defensa10": sum(t[5] for t in h) / len(h),
                "forma": "".join("G" if t[7] else "P" for t in s.hist[-5:]),
            })
        filas.sort(key=lambda r: -r["elo"])
        for i, r in enumerate(filas, 1):
            r["puesto"] = i
        return filas
