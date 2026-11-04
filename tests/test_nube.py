"""Prueba del modo nube (Render) con datos sintéticos: python tests/test_nube.py"""
import http.client
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

raiz = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(raiz)); sys.path.insert(0, str(raiz / "tests"))
from nba import config, datos, servidor
from generar_demo import generar

tmp = Path(tempfile.mkdtemp())
config.DIR_DATOS = tmp; config.CSV_HISTORICO = tmp / "h.csv"; config.CACHE_CALENDARIO = tmp / "c.json"
config.RUTAS_LEGADAS = []; config.NUBE = True
datos.escribir_csv(config.CSV_HISTORICO, generar(temporadas=7))

srv = servidor.crear_servidor(8797, "127.0.0.1")
threading.Thread(target=srv.serve_forever, daemon=True).start()
servidor.APP.lanzar("Cargando modelo", servidor.APP.tarea_inicio)


def pedir(metodo, ruta, cuerpo=None, host="nba-predictor.onrender.com", origen=None):
    c = http.client.HTTPConnection("127.0.0.1", 8797, timeout=20)
    h = {"Host": host, "Content-Type": "application/json"}
    if origen:
        h["Origin"] = origen
    c.request(metodo, ruta, json.dumps(cuerpo) if cuerpo is not None else None, h)
    r = c.getresponse()
    return r.status, r.read()


assert pedir("GET", "/salud")[0] == 200
assert pedir("HEAD", "/salud")[0] == 200
for _ in range(60):                       # espera a que cargue y evalúe
    st = json.loads(pedir("GET", "/api/estado")[1])
    if st["listo"] and not st["trabajo"]["activo"]:
        break
    time.sleep(1)
assert st["listo"] and st["nube"] is True and st["trabajo"]["error"] is None, st
code, cuerpo = pedir("GET", "/api/informe")
inf = json.loads(cuerpo)["informe"]
assert inf and {m["clave"] for m in inf["periodos"][0]["modelos"]} == {"local_siempre", "elo", "modelo"}
assert "original" not in json.dumps(inf)
assert pedir("GET", "/")[0] == 200                                   # host de Render aceptado
for ruta in ("/api/actualizar", "/api/evaluar", "/api/salir"):
    assert pedir("POST", ruta, {})[0] == 403, ruta                   # sin acciones administrativas públicas
dato = {"local": "BOS", "visitante": "LAL", "fecha": "2026-10-31"}
assert pedir("POST", "/api/predecir", dato, origen="https://nba-predictor.onrender.com")[0] == 200
assert pedir("POST", "/api/predecir", dato, origen="https://malo.example")[0] == 403   # otro origen
print("OK modo nube: salud, host de Render, informe automático, rutas admin bloqueadas, mismo origen")
