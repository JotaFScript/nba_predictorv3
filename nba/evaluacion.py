"""Backtesting cronológico: mide el modelo frente a referencias simples (siempre local, solo Elo)."""
from __future__ import annotations

import math

from .modelo import Modelo, ModeloLineal, logloss


def metricas(y, p) -> dict:
    n = len(y)
    return {
        "acc": sum((pi >= 0.5) == bool(yi) for yi, pi in zip(y, p)) / n,
        "logloss": logloss(y, p),
        "brier": sum((pi - yi) ** 2 for yi, pi in zip(y, p)) / n,
    }


def _entrenar_y_predecir(train, test, acum):
    """Entrena con `train`, predice `test` y agrega todo a `acum` (para pooling)."""
    modelo = Modelo.entrenar(train)
    solo_elo = ModeloLineal.ajustar(train, ["elo_d"], "logit", 1.0, None, lambda fi: fi.gana_local)
    tasa_local = sum(fi.gana_local for fi in train) / len(train)
    margen_medio = sum(fi.margen for fi in train) / len(train)
    total_medio = sum(fi.total for fi in train) / len(train)
    for fi in test:
        pr = modelo.predecir(fi.f)
        acum["y"].append(fi.gana_local)
        acum["p_modelo"].append(pr["prob_local"])
        acum["p_elo"].append(solo_elo.predecir(fi.f))
        acum["p_local"].append(tasa_local)
        acum["m_real"].append(fi.margen)
        acum["m_pred"].append(pr["margen"])
        acum["m_const"].append(margen_medio)
        acum["t_real"].append(fi.total)
        acum["t_pred"].append(pr["total"])
        acum["t_const"].append(total_medio)
    return modelo


def _acum_vacio():
    return {k: [] for k in ("y", "p_modelo", "p_elo", "p_local",
                            "m_real", "m_pred", "m_const", "t_real", "t_pred", "t_const")}


def _resumir(a: dict, nombre: str, detalle: str) -> dict:
    n = len(a["y"])
    mae = lambda r, p: sum(abs(x - y) for x, y in zip(r, p)) / n
    modelos = [
        ("local_siempre", "Siempre gana el local", a["p_local"]),
        ("elo", "Solo Elo", a["p_elo"]),
        ("modelo", "Modelo", a["p_modelo"]),
    ]
    tabla = [{"clave": k, "nombre": nom, **metricas(a["y"], p)} for k, nom, p in modelos]
    err = [abs(r - p) for r, p in zip(a["m_real"], a["m_pred"])]
    cubos = []
    for lo, hi in ((0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 1.01)):
        idx = [i for i, p in enumerate(a["p_modelo"]) if lo <= max(p, 1 - p) < hi]
        if idx:
            aciertos = sum((a["p_modelo"][i] >= 0.5) == bool(a["y"][i]) for i in idx)
            conf = sum(max(a["p_modelo"][i], 1 - a["p_modelo"][i]) for i in idx) / len(idx)
            cubos.append({"rango": f"{int(lo * 100)}–{min(int(hi * 100), 100)}%", "n": len(idx),
                          "confianza_media": conf, "acierto": aciertos / len(idx)})
    return {
        "nombre": nombre, "detalle": detalle, "n": n, "modelos": tabla,
        "margen": {
            "mae_modelo": mae(a["m_real"], a["m_pred"]),
            "mae_constante": mae(a["m_real"], a["m_const"]),
            "rmse_modelo": math.sqrt(sum(e * e for e in err) / n),
            "dentro_10": sum(e <= 10 for e in err) / n,
        },
        "total": {"mae_modelo": mae(a["t_real"], a["t_pred"]),
                  "mae_constante": mae(a["t_real"], a["t_const"])},
        "confianza": cubos,
    }


def evaluar(filas, progreso=print) -> dict:
    """Evaluación (1) división cronológica 80/20; (2) walk-forward por temporada."""
    informe = {"n_partidos": len(filas), "periodos": []}

    progreso("Evaluación 1/2: división 80/20 cronológica...")
    corte = int(len(filas) * 0.8)
    a = _acum_vacio()
    _entrenar_y_predecir(filas[:corte], filas[corte:], a)
    informe["periodos"].append(_resumir(
        a, "División cronológica 80/20",
        f"Entrena con el {corte * 100 // len(filas)}% más antiguo y prueba con el 20% más reciente."))

    temporadas = sorted({fi.temporada for fi in filas})
    pruebas = [t for i, t in enumerate(temporadas) if i >= 2][-3:]
    pruebas = [t for t in pruebas if sum(fi.temporada == t for fi in filas) >= 150]
    if pruebas:
        a, por_temp = _acum_vacio(), []
        for k, t in enumerate(pruebas):
            progreso(f"Evaluación 2/2: temporada {t}-{(t + 1) % 100:02d} ({k + 1}/{len(pruebas)})...")
            train = [fi for fi in filas if fi.temporada < t]
            test = [fi for fi in filas if fi.temporada == t]
            parcial = _acum_vacio()
            _entrenar_y_predecir(train, test, parcial)
            for key in a:
                a[key].extend(parcial[key])
            por_temp.append({
                "temporada": f"{t}-{(t + 1) % 100:02d}", "n": len(test),
                "acc_elo": metricas(parcial["y"], parcial["p_elo"])["acc"],
                "acc_modelo": metricas(parcial["y"], parcial["p_modelo"])["acc"],
            })
        res = _resumir(a, "Validación temporada a temporada (walk-forward)",
                       "Cada temporada se predice entrenando solo con las anteriores: la prueba más realista.")
        res["por_temporada"] = por_temp
        informe["periodos"].append(res)
    return informe


def formatear_texto(informe: dict) -> str:
    out = [f"Partidos utilizables: {informe['n_partidos']}"]
    for per in informe["periodos"]:
        out += ["", f"== {per['nombre']} (n={per['n']}) ==", per["detalle"],
                f"{'Modelo':<30}{'Acierto':>9}{'LogLoss':>9}{'Brier':>8}"]
        for m in per["modelos"]:
            out.append(f"{m['nombre']:<30}{m['acc'] * 100:>8.2f}%{m['logloss']:>9.4f}{m['brier']:>8.4f}")
        mg, tt = per["margen"], per["total"]
        out.append(f"Margen: error medio {mg['mae_modelo']:.2f} pts (referencia constante {mg['mae_constante']:.2f}); "
                   f"{mg['dentro_10'] * 100:.0f}% de los juegos con error <= 10 pts")
        out.append(f"Total de puntos: error medio {tt['mae_modelo']:.2f} (referencia {tt['mae_constante']:.2f})")
        for t in per.get("por_temporada", []):
            out.append(f"  {t['temporada']}: solo Elo {t['acc_elo'] * 100:.1f}% -> modelo {t['acc_modelo'] * 100:.1f}% (n={t['n']})")
    return "\n".join(out)
