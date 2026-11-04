"""Construcción de partidos y variables previas a cada juego.

Un único motor recorre el historial en orden cronológico. Para cada partido
calcula las variables con lo que se sabía ANTES de jugarse y solo después
actualiza el estado de los equipos. Así entrenamiento y predicción comparten
exactamente el mismo código y no hay fuga de información.
"""
from __future__ import annotations

from datetime import date, datetime

from . import config

# ---- Variables -----------------------------------------------------------
FEATURES_MODELO = [
    "elo_d", "net5_d", "net20_d", "ortg10_d", "drtg10_adv", "win10_d",
    "venue_d", "rest_d", "b2b_h", "b2b_a", "g7_d",
]
FEATURES_TOTAL = ["naive_total", "pace_avg", "b2b_h", "b2b_a"]

# Agrupación para explicar la predicción (evita factores confusos como "no está en back-to-back").
GRUPOS = {
    "Fuerza general (Elo)": ["elo_d"],
    "Forma reciente": ["net5_d", "net20_d", "win10_d"],
    "Ataque y defensa (10 juegos)": ["ortg10_d", "drtg10_adv"],
    "Rendimiento en casa / de visita": ["venue_d"],
    "Descanso y calendario": ["rest_d", "b2b_h", "b2b_a", "g7_d"],
}

ELO_INICIAL = 1500.0
ELO_K = 20.0
ELO_VENTAJA_LOCAL = 100.0
ELO_REGRESION = 0.75          # fracción del Elo que se conserva entre temporadas
VENTANA_MAX = 20


class Partido:
    __slots__ = ("gid", "fecha", "temporada", "local", "visitante", "pts_l", "pts_v",
                 "ortg_l", "ortg_v", "pace", "nombre_l", "nombre_v")


class Fila:
    """Un partido ya procesado: variables previas + resultado."""
    __slots__ = ("gid", "fecha", "temporada", "local", "visitante", "f",
                 "gana_local", "margen", "total")


class EstadoEquipo:
    __slots__ = ("elo", "hist", "neto_casa", "neto_fuera", "ultima", "temporada", "w", "l")

    def __init__(self):
        self.elo = ELO_INICIAL
        # (fecha_ordinal, pts, pts_rival, neto, ortg, drtg, ritmo, gano)
        self.hist: list[tuple] = []
        self.neto_casa: list[float] = []
        self.neto_fuera: list[float] = []
        self.ultima: date | None = None
        self.temporada: int | None = None
        self.w = 0
        self.l = 0

    def copia(self) -> "EstadoEquipo":
        n = EstadoEquipo()
        n.elo, n.hist = self.elo, list(self.hist)
        n.neto_casa, n.neto_fuera = list(self.neto_casa), list(self.neto_fuera)
        n.ultima, n.temporada, n.w, n.l = self.ultima, self.temporada, self.w, self.l
        return n


# ---- Partidos a partir de registros por equipo ---------------------------
def _poseciones(r: dict) -> float | None:
    try:
        return r["FGA"] + 0.44 * r["FTA"] - r["OREB"] + r["TOV"]
    except (TypeError, KeyError):
        return None


def construir_partidos(registros: list[dict]) -> list[Partido]:
    por_id: dict[str, dict] = {}
    for r in registros:
        mu = str(r.get("MATCHUP") or "")
        lado = "L" if "vs." in mu else ("V" if "@" in mu else None)
        if lado and r.get("PTS") is not None:
            por_id.setdefault(r["GAME_ID"], {})[lado] = r

    partidos = []
    for gid, d in por_id.items():
        if "L" not in d or "V" not in d:
            continue
        L, V = d["L"], d["V"]
        p = Partido()
        p.gid = gid
        p.fecha = datetime.strptime(str(L["GAME_DATE"])[:10], "%Y-%m-%d").date()
        sid = str(L.get("SEASON_ID") or "")
        p.temporada = int(sid[1:]) if len(sid) == 5 and sid.isdigit() else config.anio_temporada(p.fecha)
        p.local, p.visitante = L["TEAM_ABBREVIATION"], V["TEAM_ABBREVIATION"]
        p.nombre_l, p.nombre_v = L.get("TEAM_NAME"), V.get("TEAM_NAME")
        p.pts_l, p.pts_v = float(L["PTS"]), float(V["PTS"])
        pl, pv = _poseciones(L), _poseciones(V)
        poss = (pl + pv) / 2 if pl and pv and pl > 0 and pv > 0 else None
        if poss is None:
            poss = 99.0
        minutos = (L.get("MIN") or 240.0) / 5.0
        p.pace = poss * 48.0 / max(minutos, 48.0)
        p.ortg_l = 100.0 * p.pts_l / poss
        p.ortg_v = 100.0 * p.pts_v / poss
        partidos.append(p)
    partidos.sort(key=lambda p: (p.fecha, p.gid))
    return partidos


# ---- Motor ---------------------------------------------------------------
def _media(hist, idx, k):
    tramo = hist[-k:]
    return sum(t[idx] for t in tramo) / len(tramo)


def _elo_regresado(elo: float) -> float:
    return ELO_REGRESION * elo + (1 - ELO_REGRESION) * ELO_INICIAL


def _vista(st: EstadoEquipo, fecha: date, temporada: int, en_casa: bool) -> dict:
    h = st.hist
    elo = st.elo if st.temporada in (None, temporada) else _elo_regresado(st.elo)
    descanso = min((fecha - st.ultima).days - 1, 4) if st.ultima else 4
    ordinal = fecha.toordinal()
    juegos7 = sum(1 for t in h[-6:] if 1 <= ordinal - t[0] <= 7)
    venue = st.neto_casa if en_casa else st.neto_fuera
    return {
        "elo": elo,
        "net5": _media(h, 3, 5), "net20": _media(h, 3, 20),
        "ortg10": _media(h, 4, 10), "drtg10": _media(h, 5, 10), "pace10": _media(h, 6, 10),
        "win10": _media(h, 7, 10),
        "venue": (sum(venue[-10:]) / len(venue[-10:])) if venue else _media(h, 3, 10),
        "rest": max(descanso, 0), "g7": juegos7,
    }


def _combinar(h: dict, a: dict) -> dict:
    return {
        "elo_d": h["elo"] - a["elo"],
        "net5_d": h["net5"] - a["net5"],
        "net20_d": h["net20"] - a["net20"],
        "ortg10_d": h["ortg10"] - a["ortg10"],
        "drtg10_adv": a["drtg10"] - h["drtg10"],
        "win10_d": h["win10"] - a["win10"],
        "venue_d": h["venue"] - a["venue"],
        "rest_d": h["rest"] - a["rest"],
        "b2b_h": 1.0 if h["rest"] == 0 else 0.0,
        "b2b_a": 1.0 if a["rest"] == 0 else 0.0,
        "g7_d": h["g7"] - a["g7"],
        "pace_avg": (h["pace10"] + a["pace10"]) / 2,
        "naive_total": ((h["pace10"] + a["pace10"]) / 2 / 100.0)
                       * (h["ortg10"] + a["drtg10"] + a["ortg10"] + h["drtg10"]) / 2,
    }


class Motor:
    def __init__(self):
        self.estados: dict[str, EstadoEquipo] = {}
        self.ultima_fecha: date | None = None
        self.nombres: dict[str, str] = {}

    def _estado(self, equipo: str) -> EstadoEquipo:
        return self.estados.setdefault(equipo, EstadoEquipo())

    # --- variables para un partido (pasado o futuro) con el estado actual ---
    def variables(self, local: str, visitante: str, fecha: date, temporada: int | None = None):
        temporada = config.anio_temporada(fecha) if temporada is None else temporada
        sl, sv = self.estados.get(local), self.estados.get(visitante)
        minimo = config.MIN_JUEGOS_PREVIOS
        if not sl or not sv or len(sl.hist) < minimo or len(sv.hist) < minimo:
            return None
        return _combinar(_vista(sl, fecha, temporada, True), _vista(sv, fecha, temporada, False))

    def _actualizar(self, p: Partido):
        sl, sv = self._estado(p.local), self._estado(p.visitante)
        for st in (sl, sv):
            if st.temporada is not None and st.temporada != p.temporada:
                st.elo = _elo_regresado(st.elo)
                st.w = st.l = 0
            st.temporada = p.temporada

        # Elo con margen de victoria (estilo FiveThirtyEight)
        dif = sl.elo + ELO_VENTAJA_LOCAL - sv.elo
        esperado = 1.0 / (1.0 + 10 ** (-dif / 400.0))
        gano_l = 1.0 if p.pts_l > p.pts_v else 0.0
        margen = abs(p.pts_l - p.pts_v)
        dif_ganador = dif if gano_l else -dif
        mult = ((margen + 3.0) ** 0.8) / (7.5 + 0.006 * dif_ganador)
        delta = ELO_K * mult * (gano_l - esperado)
        sl.elo += delta
        sv.elo -= delta

        ord_ = p.fecha.toordinal()
        ortg_l, ortg_v = p.ortg_l, p.ortg_v
        for st, pts, opp, ortg, drtg, gano, casa in (
            (sl, p.pts_l, p.pts_v, ortg_l, ortg_v, gano_l, True),
            (sv, p.pts_v, p.pts_l, ortg_v, ortg_l, 1.0 - gano_l, False),
        ):
            neto = ortg - drtg
            st.hist.append((ord_, pts, opp, neto, ortg, drtg, p.pace, gano))
            if len(st.hist) > VENTANA_MAX:
                del st.hist[0]
            lista = st.neto_casa if casa else st.neto_fuera
            lista.append(neto)
            if len(lista) > 10:
                del lista[0]
            st.ultima = p.fecha
            if gano:
                st.w += 1
            else:
                st.l += 1
        self.ultima_fecha = p.fecha
        if p.nombre_l:
            self.nombres[p.local] = p.nombre_l
        if p.nombre_v:
            self.nombres[p.visitante] = p.nombre_v

    def procesar(self, partidos: list[Partido]) -> list[Fila]:
        filas = []
        for p in partidos:
            f = self.variables(p.local, p.visitante, p.fecha, p.temporada)
            if f is not None:
                fila = Fila()
                fila.gid, fila.fecha, fila.temporada = p.gid, p.fecha, p.temporada
                fila.local, fila.visitante, fila.f = p.local, p.visitante, f
                fila.gana_local = 1 if p.pts_l > p.pts_v else 0
                fila.margen = p.pts_l - p.pts_v
                fila.total = p.pts_l + p.pts_v
                filas.append(fila)
            self._actualizar(p)
        return filas
