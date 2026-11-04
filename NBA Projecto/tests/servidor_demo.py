"""Arranca el servidor con datos SINTÉTICOS y calendario falso (para probar la interfaz sin internet)."""
import sys
from datetime import date, timedelta
from pathlib import Path

raiz = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(raiz)); sys.path.insert(0, str(raiz / "tests"))
import tempfile
from nba import config, datos
from nba import servidor
from generar_demo import generar

tmp = Path(tempfile.mkdtemp())
config.DIR_DATOS = tmp; config.CSV_HISTORICO = tmp / "nba_historico.csv"; config.CACHE_CALENDARIO = tmp / "cal.json"
config.RUTAS_LEGADAS = []
datos.config = config
if "--vacio" not in sys.argv:
    datos.escribir_csv(config.CSV_HISTORICO, generar(temporadas=7))

def falso(fecha):
    eq = ["BOS", "LAL", "GSW", "MIA", "DEN", "NYK", "OKC", "PHX", "DAL", "MIL"]
    out = []
    for i in range(0, 8, 2):
        fin = fecha < date.today() or i == 0
        out.append({"id": str(i), "local": eq[i], "visitante": eq[i + 1], "estado": "Final" if fin else "7:30 pm ET",
                    "pts_local": 112 if fin else None, "pts_visitante": 104 if fin else None, "_fecha": fecha})
    return out
datos.partidos_del_dia = falso
servidor.datos.actualizar_datos = lambda progreso=print, **k: (progreso("Simulando descarga..."), {"registros": 1, "descargadas": [], "errores": ["sin red (demo)"]})[1]
servidor.servir(8799, abrir_navegador=False, vigilar=False)
