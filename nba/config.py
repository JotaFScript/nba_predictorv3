"""Rutas, constantes y catálogo de equipos."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

# Modo nube (Render): se activa solo con RENDER=true (Render lo define) o NBA_NUBE=1.
NUBE = bool(os.environ.get("RENDER")) or os.environ.get("NBA_NUBE") == "1"
REFRESCO_HORAS = float(os.environ.get("NBA_REFRESCO_HORAS", "6"))   # actualización automática en la nube

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATOS = RAIZ / "datos"
CSV_HISTORICO = DIR_DATOS / "nba_historico.csv"
CACHE_CALENDARIO = DIR_DATOS / "calendario.json"

# Ubicaciones donde pudo quedar el CSV de la versión anterior.
RUTAS_LEGADAS = [
    Path(r"C:\NBA Projecto\nba_historico.csv"),
    RAIZ / "nba_historico.csv",
    RAIZ.parent / "nba_historico.csv",
]

PRIMERA_TEMPORADA = 2019      # año de inicio de la primera temporada a descargar
MIN_JUEGOS_PREVIOS = 5        # juegos previos mínimos de cada equipo para usar un partido

EQUIPOS = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "LA Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}


def anio_temporada(fecha: date) -> int:
    """Año de inicio de la temporada a la que pertenece una fecha (sep-dic: ese año)."""
    return fecha.year if fecha.month >= 9 else fecha.year - 1


def etiqueta_temporada(anio: int) -> str:
    return f"{anio}-{(anio + 1) % 100:02d}"


def temporadas_a_descargar(hoy: date | None = None, primera: int = PRIMERA_TEMPORADA):
    hoy = hoy or date.today()
    return list(range(primera, anio_temporada(hoy) + 1))
