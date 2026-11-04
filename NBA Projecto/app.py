"""Punto de entrada: python app.py  (abre la interfaz en tu navegador)."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nba.servidor import servir  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="NBA Predictor Pro")
    ap.add_argument("--puerto", type=int, default=8765)
    ap.add_argument("--sin-navegador", action="store_true", help="no abrir el navegador automáticamente")
    args = ap.parse_args()
    servir(args.puerto, abrir_navegador=not args.sin_navegador)


if __name__ == "__main__":
    main()
