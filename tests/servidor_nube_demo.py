"""Servidor en modo nube con datos sintéticos (para revisar la interfaz): puerto 8796."""
import sys, tempfile
from pathlib import Path
raiz = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(raiz)); sys.path.insert(0, str(raiz / "tests"))
from nba import config, datos, servidor
from generar_demo import generar
tmp = Path(tempfile.mkdtemp())
config.DIR_DATOS = tmp; config.CSV_HISTORICO = tmp / "h.csv"; config.CACHE_CALENDARIO = tmp / "c.json"
config.RUTAS_LEGADAS = []; config.NUBE = True
datos.escribir_csv(config.CSV_HISTORICO, generar(temporadas=7))
import os; os.environ["PORT"] = "8796"
servidor.servir()
