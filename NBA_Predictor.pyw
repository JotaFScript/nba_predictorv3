# Doble clic: abre NBA Predictor sin ventana de consola.
import sys
import traceback
from pathlib import Path

raiz = Path(__file__).resolve().parent
sys.path.insert(0, str(raiz))
try:
    from nba.servidor import servir
    servir()
except Exception:
    (raiz / "datos").mkdir(exist_ok=True)
    (raiz / "datos" / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
