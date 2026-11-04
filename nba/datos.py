"""Descarga y caché de datos (solo urllib/csv/json de la biblioteca estándar).

Usa los mismos endpoints públicos que `nba_api`, así que el CSV generado es
compatible con el `nba_historico.csv` de la versión anterior.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config

HEADERS_STATS = {
    "Host": "stats.nba.com",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "close",
    "Referer": "https://stats.nba.com/",
    "Origin": "https://www.nba.com",
}
HEADERS_CDN = {"User-Agent": HEADERS_STATS["User-Agent"], "Accept-Encoding": "gzip"}
HEADERS_ESPN = {"User-Agent": HEADERS_STATS["User-Agent"], "Accept": "application/json",
                "Accept-Encoding": "gzip"}
URL_ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
# ESPN usa abreviaturas distintas de la NBA en algunos equipos.
ESPN_ABREV = {"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}

URL_GAMELOG = "https://stats.nba.com/stats/leaguegamelog"
URL_SCOREBOARD = "https://stats.nba.com/stats/scoreboardv3"
URL_CALENDARIO = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"

COLUMNAS_NUM = ("PTS", "FGA", "FTA", "OREB", "TOV", "MIN")


class ErrorDatos(RuntimeError):
    pass


# ---------------------------------------------------------------- HTTP ----
def _get_json(url: str, headers: dict, intentos: int = 3, timeout: int = 40):
    ultimo = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, OSError) as e:
            ultimo = e
            time.sleep(1.5 * (i + 1))
    raise ErrorDatos(f"No se pudo conectar ({ultimo}). Revisa tu conexión a internet.")


# ------------------------------------------------------------ historico ---
def descargar_temporada(anio: int) -> list[dict]:
    """Registros por equipo y partido de la temporada regular `anio`-`anio+1`."""
    params = {
        "Counter": 0, "DateFrom": "", "DateTo": "", "Direction": "ASC",
        "LeagueID": "00", "PlayerOrTeam": "T",
        "Season": config.etiqueta_temporada(anio),
        "SeasonType": "Regular Season", "Sorter": "DATE",
    }
    datos = _get_json(URL_GAMELOG + "?" + urllib.parse.urlencode(params), HEADERS_STATS,
                      intentos=2, timeout=30)
    conjuntos = datos.get("resultSets") or [datos.get("resultSet")]
    rs = conjuntos[0]
    cab = rs["headers"]
    return [dict(zip(cab, fila)) for fila in rs["rowSet"]]


def leer_csv(ruta: Path) -> list[dict]:
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        filas = list(csv.DictReader(f))
    for r in filas:
        for c in COLUMNAS_NUM:
            v = r.get(c)
            try:
                r[c] = float(v) if v not in (None, "") else None
            except ValueError:
                r[c] = None
    return filas


def escribir_csv(ruta: Path, filas: list[dict]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    campos: list[str] = []
    for r in filas:
        for k in r:
            if k not in campos:
                campos.append(k)
    tmp = ruta.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(filas)
    os.replace(tmp, ruta)


def _anio_de(fila: dict) -> int:
    sid = str(fila.get("SEASON_ID") or "")
    if len(sid) == 5 and sid.isdigit():
        return int(sid[1:])
    f = datetime.strptime(str(fila["GAME_DATE"])[:10], "%Y-%m-%d").date()
    return config.anio_temporada(f)


def validar_columnas(filas: list[dict]) -> None:
    if not filas:
        raise ErrorDatos("El CSV está vacío.")
    faltan = {"GAME_ID", "GAME_DATE", "MATCHUP", "TEAM_ABBREVIATION", "PTS"} - set(filas[0])
    if faltan:
        raise ErrorDatos(f"Al CSV le faltan columnas: {', '.join(sorted(faltan))}")


def adoptar_csv_existente() -> Path | None:
    """Si no hay datos locales, copia el CSV de la versión anterior si se encuentra."""
    if config.CSV_HISTORICO.exists():
        return config.CSV_HISTORICO
    for ruta in config.RUTAS_LEGADAS:
        try:
            if ruta.exists():
                config.DIR_DATOS.mkdir(parents=True, exist_ok=True)
                shutil.copy(ruta, config.CSV_HISTORICO)
                return config.CSV_HISTORICO
        except OSError:
            continue
    return None


def cargar_registros() -> list[dict]:
    ruta = adoptar_csv_existente()
    if ruta is None:
        return []
    filas = leer_csv(ruta)
    validar_columnas(filas)
    return filas


def _fecha_fila(r: dict) -> date:
    return datetime.strptime(str(r["GAME_DATE"])[:10], "%Y-%m-%d").date()


def completar_con_espn(por_temp: dict, hoy: date, progreso=print, max_dias: int = 150, pausa: float = 0.3) -> int:
    """Añade partidos terminados desde ESPN (solo marcador: sin FGA/FTA/etc.). Devuelve cuántos añadió."""
    filas = [r for lista in por_temp.values() for r in lista]
    if not filas:
        return 0
    ultima = max(_fecha_fila(r) for r in filas)
    desde = max(ultima - timedelta(days=1), hoy - timedelta(days=max_dias))
    ids_equipo = {r["TEAM_ABBREVIATION"]: r.get("TEAM_ID") for r in filas if r.get("TEAM_ID")}
    existentes = {(str(r["GAME_DATE"])[:10], r["TEAM_ABBREVIATION"], str(r["MATCHUP"]).split()[-1])
                  for r in filas if "vs." in str(r["MATCHUP"])}
    nuevos, dia = 0, desde
    while dia <= hoy:
        progreso(f"Buscando partidos en ESPN: {dia.isoformat()}...")
        for ev in _espn_eventos(_get_json(_url_espn(dia), HEADERS_ESPN, intentos=2, timeout=20)):
            if not ev["final"] or ev["tipo_temporada"] not in (None, 2):
                continue
            if (dia.isoformat(), ev["local"], ev["visitante"]) in existentes:
                continue
            anio = config.anio_temporada(dia)
            for eq, riv, pts, opp, casa, nombre in (
                (ev["local"], ev["visitante"], ev["pts_local"], ev["pts_visitante"], True, ev["nombre_local"]),
                (ev["visitante"], ev["local"], ev["pts_visitante"], ev["pts_local"], False, ev["nombre_visitante"]),
            ):
                por_temp[anio].append({
                    "SEASON_ID": f"2{anio}", "TEAM_ID": ids_equipo.get(eq, ""), "TEAM_ABBREVIATION": eq,
                    "TEAM_NAME": nombre or eq, "GAME_ID": f"ESPN{ev['id']}", "GAME_DATE": dia.isoformat(),
                    "MATCHUP": f"{eq} vs. {riv}" if casa else f"{eq} @ {riv}",
                    "WL": "W" if pts > opp else "L", "MIN": 240.0, "PTS": float(pts),
                    "PLUS_MINUS": pts - opp, "FUENTE": "espn"})
            nuevos += 1
        dia += timedelta(days=1)
        if dia <= hoy:
            time.sleep(pausa)
    return nuevos


def actualizar_datos(progreso=print, hoy: date | None = None, pausa: float = 1.5) -> dict:
    """Descarga lo que falta (y siempre la temporada en curso). Devuelve un resumen.

    Fuente principal: stats.nba.com (datos completos). Si no responde (típico en servidores en la
    nube), la temporada en curso se completa con los marcadores de ESPN.
    """
    hoy = hoy or date.today()
    existentes = cargar_registros()
    por_temp: dict[int, list[dict]] = defaultdict(list)
    for r in existentes:
        por_temp[_anio_de(r)].append(r)

    actual = config.anio_temporada(hoy)
    pendientes = []
    for anio in config.temporadas_a_descargar(hoy):
        filas = por_temp.get(anio)
        if not filas or anio == actual:
            pendientes.append(anio)
            continue
        ultima = max(str(r["GAME_DATE"])[:10] for r in filas)
        if ultima < f"{anio + 1}-04-05":      # temporada cacheada incompleta
            pendientes.append(anio)

    descargadas, errores, fallos_seguidos = [], [], 0
    for i, anio in enumerate(pendientes):
        etq = config.etiqueta_temporada(anio)
        if fallos_seguidos >= 2:              # stats.nba.com no responde: no esperar por cada temporada
            errores.append(f"{etq}: omitida (stats.nba.com no responde)")
            continue
        progreso(f"Descargando temporada {etq} ({i + 1}/{len(pendientes)})...")
        try:
            nuevas = descargar_temporada(anio)
            fallos_seguidos = 0
            if nuevas:
                por_temp[anio] = nuevas
                descargadas.append(etq)
            elif anio != actual:
                errores.append(f"{etq}: sin datos")
        except Exception as e:  # noqa: BLE001 - se informa y se continúa
            fallos_seguidos += 1
            errores.append(f"{etq}: {e}")
        if i < len(pendientes) - 1 and fallos_seguidos == 0:
            time.sleep(pausa)

    etq_actual = config.etiqueta_temporada(actual)
    if actual in pendientes and etq_actual not in descargadas and por_temp:
        try:
            n = completar_con_espn(por_temp, hoy, progreso, pausa=min(pausa, 0.3))
            if n:
                descargadas.append(f"{etq_actual} (+{n} partidos vía ESPN)")
        except Exception as e:  # noqa: BLE001
            errores.append(f"ESPN: {e}")

    todo = [r for anio in sorted(por_temp) for r in por_temp[anio]]
    vistos, unicos = set(), []
    for r in todo:
        clave = (r["GAME_ID"], r.get("TEAM_ID") or r["TEAM_ABBREVIATION"])
        if clave not in vistos:
            vistos.add(clave)
            unicos.append(r)
    if not unicos:
        raise ErrorDatos("No se pudo descargar ningún dato. " + "; ".join(errores))
    if descargadas:
        escribir_csv(config.CSV_HISTORICO, unicos)
    return {"registros": len(unicos), "descargadas": descargadas, "errores": errores}


# ------------------------------------------------------------ calendario --
def _parsear_codigo(codigo: str):
    m = re.match(r"^(\d{8})/([A-Z]{3})([A-Z]{3})$", codigo or "")
    if not m:
        return None
    f = datetime.strptime(m.group(1), "%Y%m%d").date()
    return f, m.group(2), m.group(3)        # fecha, visitante, local


def _juego_a_dict(g: dict) -> dict | None:
    cod = _parsear_codigo(g.get("gameCode", ""))
    local = (g.get("homeTeam") or {}).get("teamTricode") or (cod[2] if cod else None)
    visit = (g.get("awayTeam") or {}).get("teamTricode") or (cod[1] if cod else None)
    if not local or not visit:
        return None
    final = g.get("gameStatus") == 3
    pl = (g.get("homeTeam") or {}).get("score")
    pv = (g.get("awayTeam") or {}).get("score")
    return {
        "id": g.get("gameId"), "local": local, "visitante": visit,
        "estado": "Final" if final else (g.get("gameStatusText") or "").strip(),
        "pts_local": pl if final else None, "pts_visitante": pv if final else None,
        "_fecha": cod[0] if cod else None,
    }


def _cargar_calendario() -> dict:
    ruta = config.CACHE_CALENDARIO
    if ruta.exists() and time.time() - ruta.stat().st_mtime < 6 * 3600:
        try:
            return json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    datos = _get_json(URL_CALENDARIO, HEADERS_CDN, intentos=1, timeout=15)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(datos), encoding="utf-8")
    except OSError:
        pass
    return datos


def _calendario_cubre(cal: dict, fecha: date) -> bool:
    anio = config.anio_temporada(fecha)
    return str(cal.get("leagueSchedule", {}).get("seasonYear", ""))[:4] == str(anio)


def _desde_calendario_nba(fecha: date):
    cal = _cargar_calendario()
    out = []
    for gd in cal["leagueSchedule"]["gameDates"]:
        for g in gd.get("games", []):
            d = _juego_a_dict(g)
            if d and d["_fecha"] == fecha:
                out.append(d)
    if out or _calendario_cubre(cal, fecha):
        return out
    return None                      # el calendario no cubre esa fecha: probar otra fuente


def _url_espn(fecha: date) -> str:
    return URL_ESPN + "?" + urllib.parse.urlencode({"dates": fecha.strftime("%Y%m%d"), "limit": 100})


def _espn_eventos(js: dict) -> list[dict]:
    out = []
    for ev in js.get("events", []):
        try:
            comp = ev["competitions"][0]
            lados = {c["homeAway"]: c for c in comp["competitors"]}
            tipo = ev["status"]["type"]
            final = tipo.get("completed") is True or tipo.get("name") == "STATUS_FINAL"
            temporada = (ev.get("season") or {}).get("type")
            ab = lambda c: ESPN_ABREV.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
            pts = lambda c: int(float(c.get("score") or 0))
            out.append({
                "id": ev["id"], "final": final, "tipo_temporada": temporada,
                "estado": "Final" if final else (tipo.get("shortDetail") or tipo.get("detail") or "").strip(),
                "local": ab(lados["home"]), "visitante": ab(lados["away"]),
                "nombre_local": lados["home"]["team"].get("displayName"),
                "nombre_visitante": lados["away"]["team"].get("displayName"),
                "pts_local": pts(lados["home"]), "pts_visitante": pts(lados["away"]),
            })
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def _desde_espn(fecha: date):
    juegos = []
    for ev in _espn_eventos(_get_json(_url_espn(fecha), HEADERS_ESPN, intentos=2, timeout=20)):
        juegos.append({"id": ev["id"], "local": ev["local"], "visitante": ev["visitante"],
                       "estado": ev["estado"],
                       "pts_local": ev["pts_local"] if ev["final"] else None,
                       "pts_visitante": ev["pts_visitante"] if ev["final"] else None,
                       "_fecha": fecha})
    return juegos


def _desde_stats_nba(fecha: date):
    url = URL_SCOREBOARD + "?" + urllib.parse.urlencode({"GameDate": fecha.isoformat(), "LeagueID": "00"})
    datos = _get_json(url, HEADERS_STATS, intentos=1, timeout=20)
    juegos = (datos.get("scoreboard") or {}).get("games", [])
    return [d for d in (_juego_a_dict(g) for g in juegos) if d]


_FUENTES = (("calendario NBA", _desde_calendario_nba), ("ESPN", _desde_espn), ("stats.nba.com", _desde_stats_nba))
_FALLOS: dict[str, float] = {}          # fuente -> momento del último fallo (no reintentar durante 10 min)
_CACHE_DIA: dict[date, tuple] = {}


def partidos_del_dia(fecha: date) -> list[dict]:
    """Partidos de `fecha`. Prueba calendario NBA, luego ESPN, luego stats.nba.com."""
    hit = _CACHE_DIA.get(fecha)
    ttl = 3600 if fecha < date.today() else 90
    if hit and time.time() - hit[0] < ttl:
        return [dict(g) for g in hit[1]]
    errores = []
    for respetar_memoria in (True, False):
        for nombre, fuente in _FUENTES:
            if respetar_memoria and time.time() - _FALLOS.get(nombre, 0) < 600:
                continue
            try:
                res = fuente(fecha)
            except Exception as e:  # noqa: BLE001
                _FALLOS[nombre] = time.time()
                errores.append(f"{nombre}: {e}")
                continue
            if res is not None:
                _CACHE_DIA[fecha] = (time.time(), res)
                return [dict(g) for g in res]
        if errores:
            break
    raise ErrorDatos("No se pudo obtener la cartelera. " + " | ".join(errores))
