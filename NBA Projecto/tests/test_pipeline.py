"""Pruebas del pipeline con datos sintéticos: python tests/test_pipeline.py"""
import copy
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generar_demo import generar
from nba import evaluacion
from nba.features import Motor, construir_partidos
from nba.predictor import Predictor


def test_sin_fuga():
    """Cambiar el marcador de un partido no debe alterar SUS variables previas."""
    regs = generar(temporadas=2)
    partidos = construir_partidos(regs)
    filas = Motor().procesar(partidos)
    k = len(partidos) // 2
    objetivo = partidos[k]
    antes = next(f for f in filas if f.gid == objetivo.gid).f
    regs2 = copy.deepcopy(regs)
    for r in regs2:
        if r["GAME_ID"] == objetivo.gid:
            r["PTS"] += 40
    despues = next(f for f in Motor().procesar(construir_partidos(regs2)) if f.gid == objetivo.gid).f
    assert antes == despues, "¡Fuga de información!"
    print("OK sin fuga de información")


def test_consistencia_entrenamiento_inferencia():
    """Las variables de inferencia deben ser idénticas a las usadas al entrenar."""
    partidos = construir_partidos(generar(temporadas=2))
    ult = partidos[-1]
    m_full = Motor()
    f_full = {f.gid: f for f in m_full.procesar(partidos)}[ult.gid].f
    m = Motor()
    m.procesar(partidos[:-1])
    f_inf = m.variables(ult.local, ult.visitante, ult.fecha, ult.temporada)
    assert all(abs(f_full[k] - f_inf[k]) < 1e-9 for k in f_full)
    print("OK variables de entrenamiento == variables de inferencia")


def test_modelo_y_evaluacion():
    t0 = time.time()
    regs = generar(temporadas=7)
    pred = Predictor.construir(regs, progreso=lambda m: None)
    print(f"Entrenamiento: {time.time() - t0:.1f}s  | filas={len(pred.filas)} | {pred.modelo.hiperparametros}")
    inf = evaluacion.evaluar(pred.filas, progreso=lambda m: print("  ", m))
    print(evaluacion.formatear_texto(inf))
    per = inf["periodos"][-1]
    tab = {m["clave"]: m for m in per["modelos"]}
    assert tab["modelo"]["acc"] > tab["local_siempre"]["acc"]
    assert tab["modelo"]["logloss"] < tab["local_siempre"]["logloss"]
    assert per["margen"]["mae_modelo"] < per["margen"]["mae_constante"]
    print(f"Tiempo total: {time.time() - t0:.1f}s")

    a, b = pred.motor.estados["BOS"], None
    r = pred.predecir("BOS", "LAL", pred.motor.ultima_fecha + timedelta(days=1), linea=-5.5)
    assert abs(r["prob_local"] + r["prob_visita"] - 1) < 1e-9
    assert abs(sum(d["p"] for d in r["distribucion"]) - 1) < 1e-9
    r2 = pred.predecir("BOS", "LAL", pred.motor.ultima_fecha + timedelta(days=1), ajuste_local=-5)
    assert r2["margen"] < r["margen"] and r2["prob_local"] < r["prob_local"]
    print("OK predicción:", {k: round(v, 3) for k, v in r.items() if isinstance(v, float)})


if __name__ == "__main__":
    test_sin_fuga()
    test_consistencia_entrenamiento_inferencia()
    test_modelo_y_evaluacion()
    print("\nTODO OK")
