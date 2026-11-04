"""Pruebas de la capa de datos con la red simulada: python tests/test_datos.py"""
import sys
import tempfile
from datetime import date
from pathlib import Path

raiz = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(raiz)); sys.path.insert(0, str(raiz / "tests"))
from nba import config, datos
from generar_demo import generar

tmp = Path(tempfile.mkdtemp())
config.DIR_DATOS = tmp; config.CSV_HISTORICO = tmp / "h.csv"; config.CACHE_CALENDARIO = tmp / "cal.json"
config.RUTAS_LEGADAS = [tmp / "viejo" / "nba_historico.csv"]


def respuesta_gamelog(anio):
    filas = [r for r in generar(primera=2019, temporadas=8) if r["SEASON_ID"] == f"2{anio}"]
    cab = list(filas[0])
    return {"resultSets": [{"name": "LeagueGameLog", "headers": cab, "rowSet": [[r[c] for c in cab] for r in filas]}]}


def test_actualizar_incremental():
    llamadas = []

    def falso(url, headers, **k):
        anio = int(url.split("Season=")[1][:4])
        llamadas.append(anio)
        return respuesta_gamelog(anio)

    datos._get_json = falso
    r = datos.actualizar_datos(progreso=lambda m: None, hoy=date(2022, 1, 15), pausa=0)
    assert llamadas == [2019, 2020, 2021], llamadas      # solo hasta la temporada en curso
    assert r["registros"] > 0 and not r["errores"]
    llamadas.clear()
    r = datos.actualizar_datos(progreso=lambda m: None, hoy=date(2022, 1, 15), pausa=0)
    assert llamadas == [2021], llamadas                  # solo se refresca la temporada en curso
    llamadas.clear()
    datos.actualizar_datos(progreso=lambda m: None, hoy=date(2023, 6, 1), pausa=0)
    assert llamadas == [2022], llamadas                  # 2021 ya estaba completa: no se vuelve a bajar
    # Simula un caché de 2021 cortado a mitad de temporada: debe volver a descargarse.
    filas = [f for f in datos.leer_csv(config.CSV_HISTORICO)
             if not (f["SEASON_ID"] == "22021" and f["GAME_DATE"] > "2022-01-31")]
    datos.escribir_csv(config.CSV_HISTORICO, filas)
    llamadas.clear()
    datos.actualizar_datos(progreso=lambda m: None, hoy=date(2023, 6, 1), pausa=0)
    assert llamadas == [2021, 2022], llamadas
    filas = datos.cargar_registros()
    claves = {(f["GAME_ID"], f["TEAM_ID"]) for f in filas}
    assert len(claves) == len(filas), "hay duplicados"
    print("OK actualización incremental:", len(filas), "registros")


def test_error_parcial():
    config.CSV_HISTORICO.unlink()
    def falso(url, headers, **k):
        if "stats.nba.com" not in url:
            raise datos.ErrorDatos("sin ESPN en esta prueba")
        anio = int(url.split("Season=")[1][:4])
        if anio == 2020:
            raise datos.ErrorDatos("sin red")
        return respuesta_gamelog(anio)
    datos._get_json = falso
    r = datos.actualizar_datos(progreso=lambda m: None, hoy=date(2021, 3, 1), pausa=0)
    assert r["errores"] and "2020-21" in r["errores"][0] and r["descargadas"] == ["2019-20"]
    print("OK un fallo parcial no tumba la descarga:", r["errores"])


def test_calendario():
    cal = {"leagueSchedule": {"seasonYear": "2026-27", "gameDates": [{"gameDate": "10/31/2026 00:00:00", "games": [
        {"gameId": "1", "gameCode": "20261031/LALBOS", "gameStatus": 1, "gameStatusText": "7:30 pm ET",
         "homeTeam": {"teamTricode": "BOS"}, "awayTeam": {"teamTricode": "LAL"}},
        {"gameId": "2", "gameCode": "20261031/MIADEN", "gameStatus": 3, "gameStatusText": "Final",
         "homeTeam": {"teamTricode": "DEN", "score": 110}, "awayTeam": {"teamTricode": "MIA", "score": 99}}]}]}}
    datos._get_json = lambda url, headers, **k: cal
    js = datos.partidos_del_dia(date(2026, 10, 31))
    assert [(j["visitante"], j["local"], j["estado"]) for j in js] == [("LAL", "BOS", "7:30 pm ET"), ("MIA", "DEN", "Final")]
    assert js[1]["pts_local"] == 110
    assert datos.partidos_del_dia(date(2026, 11, 1)) == []      # día sin juegos en un calendario vigente
    print("OK parser de calendario")


def test_csv_legado():
    viejo = config.RUTAS_LEGADAS[0]
    viejo.parent.mkdir(parents=True)
    datos.escribir_csv(viejo, generar(temporadas=1))
    if config.CSV_HISTORICO.exists():
        config.CSV_HISTORICO.unlink()
    assert len(datos.cargar_registros()) > 2000 and config.CSV_HISTORICO.exists()
    print("OK adopta el CSV de la versión anterior")


if __name__ == "__main__":
    test_actualizar_incremental(); test_error_parcial(); test_calendario(); test_csv_legado()
    print("\nTODO OK")


# ---------------------------------------------------------------- ESPN --
def _espn_json(partidos, tipo=2):
    ev = []
    for i, (loc, vis, pl, pv, final) in enumerate(partidos):
        ev.append({"id": f"4{i:05d}", "season": {"type": tipo},
                   "status": {"type": {"name": "STATUS_FINAL" if final else "STATUS_SCHEDULED", "completed": final,
                                       "shortDetail": "Final" if final else "11/4 - 7:30 PM EST"}},
                   "competitions": [{"competitors": [
                       {"homeAway": "home", "score": str(pl), "team": {"abbreviation": loc, "displayName": loc + " X"}},
                       {"homeAway": "away", "score": str(pv), "team": {"abbreviation": vis, "displayName": vis + " X"}}]}]})
    return {"events": ev}


def test_respaldo_espn_actualiza_datos():
    """stats.nba.com bloqueado (como en Render): la temporada en curso se completa con ESPN."""
    from datetime import timedelta
    config.CSV_HISTORICO.unlink(missing_ok=True)
    base = generar(primera=2019, temporadas=8)
    datos.escribir_csv(config.CSV_HISTORICO, [r for r in base if r["SEASON_ID"] != "22026"])  # semilla sin la actual
    hoy = date(2026, 11, 4)

    def falso(url, headers, **k):
        if "stats.nba.com" in url:
            raise datos.ErrorDatos("bloqueado")
        dia = url.split("dates=")[1][:8]
        if dia == "20261103":
            return _espn_json([("BOS", "LAL", 110, 101, True), ("GS", "NY", 99, 104, True), ("DEN", "MIA", 0, 0, False)])
        return _espn_json([])
    datos._get_json = falso
    antes = len(datos.cargar_registros())
    r = datos.actualizar_datos(progreso=lambda m: None, hoy=hoy, pausa=0)
    assert any("ESPN" in d and "+2" in d for d in r["descargadas"]), r
    filas = datos.cargar_registros()
    assert len(filas) == antes + 4
    nuevas = [f for f in filas if f["GAME_ID"].startswith("ESPN")]
    assert {f["TEAM_ABBREVIATION"] for f in nuevas} == {"BOS", "LAL", "GSW", "NYK"}   # GS->GSW, NY->NYK
    assert all(f["SEASON_ID"] == "22026" and f["GAME_DATE"] == "2026-11-03" for f in nuevas)
    # segunda ejecución: no duplica los mismos partidos
    r2 = datos.actualizar_datos(progreso=lambda m: None, hoy=hoy, pausa=0)
    assert len(datos.cargar_registros()) == antes + 4, "duplicó partidos de ESPN"
    # el modelo debe poder usar filas sin FGA/FTA (solo marcador)
    from nba.features import construir_partidos
    assert len(construir_partidos(datos.cargar_registros())) > 0
    print("OK respaldo ESPN (sin duplicar, abreviaturas corregidas):", r["descargadas"])


def test_cartelera_con_respaldo():
    datos._CACHE_DIA.clear(); datos._FALLOS.clear()
    config.CACHE_CALENDARIO.unlink(missing_ok=True)

    def falso(url, headers, **k):
        if "espn.com" in url:
            return _espn_json([("BOS", "LAL", 0, 0, False), ("GS", "NY", 0, 0, False)])
        raise datos.ErrorDatos("bloqueado")
    datos._get_json = falso
    js = datos.partidos_del_dia(date(2026, 11, 5))
    assert [(j["visitante"], j["local"]) for j in js] == [("LAL", "BOS"), ("NYK", "GSW")]
    assert js[0]["estado"] == "11/4 - 7:30 PM EST" and js[0]["pts_local"] is None
    # si todas las fuentes fallan, el error dice cuáles
    datos._CACHE_DIA.clear(); datos._FALLOS.clear(); config.CACHE_CALENDARIO.unlink(missing_ok=True)
    datos._get_json = lambda *a, **k: (_ for _ in ()).throw(datos.ErrorDatos("sin red"))
    try:
        datos.partidos_del_dia(date(2026, 11, 6))
        raise AssertionError("debía fallar")
    except datos.ErrorDatos as e:
        assert "ESPN" in str(e) and "calendario NBA" in str(e)
    print("OK cartelera con respaldo ESPN y errores descriptivos")


if __name__ == "__main__":
    test_respaldo_espn_actualiza_datos(); test_cartelera_con_respaldo()
    print("TODO OK (ESPN)")
