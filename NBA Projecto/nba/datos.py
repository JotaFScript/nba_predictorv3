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
from datetime import date, datetime
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
    datos = _get_json(URL_GAMELOG + "?" + urllib.parse.urlencode(params), HEADERS_STATS)
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


def actualizar_datos(progreso=print, hoy: date | None = None, pausa: float = 1.5) -> dict:
    """Descarga lo que falta (y siempre la temporada en curso). Devuelve un resumen."""
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

    descargadas, errores = [], []
    for i, anio in enumerate(pendientes):
        etq = config.etiqueta_temporada(anio)
        progreso(f"Descargando temporada {etq} ({i + 1}/{len(pendientes)})...")
        try:
            nuevas = descargar_temporada(anio)
            if nuevas:
                por_temp[anio] = nuevas
                descargadas.append(etq)
            elif anio != actual:
                errores.append(f"{etq}: sin datos")
        except Exception as e:  # noqa: BLE001 - se informa y se continúa
            errores.append(f"{etq}: {e}")
        if i < len(pendientes) - 1:
            time.sleep(pausa)

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
    datos = _get_json(URL_CALENDARIO, HEADERS_CDN)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps(datos), encoding="utf-8")
    except OSError:
        pass
    return datos


def partidos_del_dia(fecha: date) -> list[dict]:
    """Partidos programados para `fecha` (calendario oficial; respaldo: scoreboard)."""
    try:
        cal = _cargar_calendario()
        out = []
        for gd in cal["leagueSchedule"]["gameDates"]:
            for g in gd.get("games", []):
                d = _juego_a_dict(g)
                if d and d["_fecha"] == fecha:
                    out.append(d)
        if out:
            return out
        # Sin coincidencias: puede ser un día sin juegos o un calendario viejo.
        if _calendario_cubre(cal, fecha):
            return []
    except (ErrorDatos, KeyError, TypeError, ValueError):
        pass
    url = URL_SCOREBOARD + "?" + urllib.parse.urlencode({"GameDate": fecha.isoformat(), "LeagueID": "00"})
    datos = _get_json(url, HEADERS_STATS)
    juegos = (datos.get("scoreboard") or {}).get("games", [])
    out = [d for d in (_juego_a_dict(g) for g in juegos) if d]
    return out


def _calendario_cubre(cal: dict, fecha: date) -> bool:
    anio = config.anio_temporada(fecha)
    return str(cal.get("leagueSchedule", {}).get("seasonYear", ""))[:4] == str(anio)
