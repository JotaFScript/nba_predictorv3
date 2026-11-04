"""Servidor HTTP local (solo biblioteca estándar) que sirve la interfaz y la API JSON."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, config, datos, evaluacion
from .predictor import Predictor

WEB = config.RAIZ / "web"
INACTIVIDAD_MAX = 300          # segundos sin que el navegador consulte -> el programa se cierra


class App:
    """Estado compartido de la aplicación."""

    def __init__(self):
        self.predictor: Predictor | None = None
        self.informe: dict | None = None
        self.sin_datos = False
        self.n_registros = 0
        self.trabajo = {"activo": False, "nombre": "", "mensaje": "", "error": None}
        self.lock = threading.Lock()
        self.ultimo_acceso = time.time()
        self.servidor: ThreadingHTTPServer | None = None

    # ---- trabajos en segundo plano ----
    def lanzar(self, nombre: str, funcion) -> bool:
        with self.lock:
            if self.trabajo["activo"]:
                return False
            self.trabajo = {"activo": True, "nombre": nombre, "mensaje": "Iniciando...", "error": None}

        def progreso(msg):
            self.trabajo["mensaje"] = msg

        def correr():
            try:
                funcion(progreso)
                self.trabajo["mensaje"] = "Listo"
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.trabajo["error"] = str(e) or e.__class__.__name__
            finally:
                self.trabajo["activo"] = False

        threading.Thread(target=correr, daemon=True).start()
        return True

    def _reconstruir(self, progreso):
        progreso("Leyendo historial...")
        registros = datos.cargar_registros()
        if not registros:
            self.sin_datos, self.predictor = True, None
            progreso("Aún no hay datos: pulsa «Actualizar datos» para descargarlos.")
            return
        self.sin_datos = False
        if self.predictor is not None and len(registros) == self.n_registros:
            progreso("Sin datos nuevos: el modelo ya está al día.")
            return
        predictor = Predictor.construir(registros, progreso)
        informe = None
        if config.NUBE:         # en la nube no hay botón: la evaluación se calcula sola
            informe = evaluacion.evaluar(predictor.filas, progreso)
        self.predictor, self.n_registros = predictor, len(registros)
        self.informe = informe

    def tarea_inicio(self, progreso):
        self._reconstruir(progreso)

    def tarea_actualizar(self, progreso):
        resumen = datos.actualizar_datos(progreso)
        self._reconstruir(progreso)
        extra = f" Avisos: {'; '.join(resumen['errores'])}" if resumen["errores"] else ""
        progreso(f"Datos actualizados ({resumen['registros']} registros).{extra}")

    def tarea_evaluar(self, progreso):
        if not self.predictor:
            raise RuntimeError("Primero carga los datos.")
        self.informe = evaluacion.evaluar(self.predictor.filas, progreso)


APP = App()


def _num(valor, defecto=0.0):
    try:
        return float(valor) if valor not in (None, "") else defecto
    except (TypeError, ValueError):
        raise ValueError(f"Valor numérico no válido: {valor!r}")


def _parsear_fecha(texto: str | None) -> date:
    if not texto:
        return date.today()
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("La fecha debe tener formato AAAA-MM-DD.")


class Manejador(BaseHTTPRequestHandler):
    server_version = "NBAPredictor/" + __version__

    def log_message(self, *args):       # silencio en consola
        pass

    # ---- utilidades ----
    def _enviar(self, codigo: int, cuerpo: bytes, tipo: str):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, obj, codigo=200):
        self._enviar(codigo, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, mensaje: str, codigo=400):
        self._json({"error": mensaje}, codigo)

    def _host_valido(self) -> bool:
        host = self.headers.get("Host") or ""
        origen = self.headers.get("Origin")
        if config.NUBE:         # detrás del proxy de Render: solo se exige mismo origen
            return origen is None or urlparse(origen).netloc == host
        puerto = self.server.server_address[1]
        if host not in (f"127.0.0.1:{puerto}", f"localhost:{puerto}"):
            return False
        return origen in (None, f"http://127.0.0.1:{puerto}", f"http://localhost:{puerto}")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- rutas ----
    def do_GET(self):
        APP.ultimo_acceso = time.time()
        if urlparse(self.path).path == "/salud":     # health check de Render (responde aunque el modelo cargue)
            return self._enviar(200, b"ok", "text/plain")
        if not self._host_valido():
            return self._error("Acceso no permitido", 403)
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                return self._enviar(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/estado":
                return self._json(self._estado())
            if url.path == "/api/cartelera":
                return self._cartelera(_parsear_fecha(q.get("fecha")))
            if url.path == "/api/ranking":
                return self._json({"ranking": APP.predictor.ranking() if APP.predictor else []})
            if url.path == "/api/informe":
                return self._json({"informe": APP.informe})
            return self._error("No encontrado", 404)
        except ValueError as e:
            return self._error(str(e))
        except datos.ErrorDatos as e:
            return self._error(str(e), 502)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._error(f"Error interno: {e}", 500)

    def do_POST(self):
        APP.ultimo_acceso = time.time()
        if not self._host_valido():
            return self._error("Acceso no permitido", 403)
        try:
            largo = int(self.headers.get("Content-Length") or 0)
            cuerpo = json.loads(self.rfile.read(largo) or b"{}") if largo else {}
            ruta = urlparse(self.path).path
            if config.NUBE and ruta in ("/api/actualizar", "/api/evaluar", "/api/salir"):
                return self._error("No disponible en la versión en línea", 403)
            if ruta == "/api/actualizar":
                return self._json({"ok": APP.lanzar("Actualizando datos", APP.tarea_actualizar)})
            if ruta == "/api/evaluar":
                return self._json({"ok": APP.lanzar("Evaluando modelo", APP.tarea_evaluar)})
            if ruta == "/api/predecir":
                return self._predecir(cuerpo)
            if ruta == "/api/salir":
                self._json({"ok": True})
                threading.Thread(target=APP.servidor.shutdown, daemon=True).start()
                return
            return self._error("No encontrado", 404)
        except ValueError as e:
            return self._error(str(e))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._error(f"Error interno: {e}", 500)

    # ---- implementación ----
    def _estado(self):
        p = APP.predictor
        return {
            "version": __version__, "hoy": date.today().isoformat(), "nube": config.NUBE,
            "listo": p is not None, "sin_datos": APP.sin_datos,
            "trabajo": APP.trabajo, "informe_listo": APP.informe is not None,
            "partidos": p.n_partidos if p else 0,
            "ultima_fecha": p.motor.ultima_fecha.isoformat() if p else None,
            "equipos": p.equipos_activos() if p else [],
            "modelo": p.modelo.hiperparametros if p else None,
        }

    def _predecir(self, c: dict):
        if not APP.predictor:
            raise ValueError("El modelo aún no está listo.")
        linea = c.get("linea")
        r = APP.predictor.predecir(
            str(c.get("local", "")), str(c.get("visitante", "")), _parsear_fecha(c.get("fecha")),
            _num(c.get("aj_local")), _num(c.get("aj_visita")),
            _num(linea) if linea not in (None, "") else None)
        self._json(r)

    def _cartelera(self, fecha: date):
        if not APP.predictor:
            raise ValueError("El modelo aún no está listo.")
        juegos = datos.partidos_del_dia(fecha)
        for g in juegos:
            g.pop("_fecha", None)
            try:
                g["pred"] = APP.predictor.predecir(g["local"], g["visitante"], fecha)
                g["nombre_local"], g["nombre_visitante"] = g["pred"]["nombre_local"], g["pred"]["nombre_visitante"]
            except ValueError as e:
                g["pred"], g["aviso"] = None, str(e)
                g["nombre_local"] = APP.predictor.nombre(g["local"])
                g["nombre_visitante"] = APP.predictor.nombre(g["visitante"])
        self._json({"fecha": fecha.isoformat(), "partidos": juegos,
                    "pasada": fecha < date.today()})


def _vigilar_inactividad():
    while True:
        time.sleep(10)
        if time.time() - APP.ultimo_acceso > INACTIVIDAD_MAX and not APP.trabajo["activo"]:
            print("Sin actividad del navegador: cerrando.")
            APP.servidor.shutdown()
            return


def crear_servidor(puerto: int = 8765, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    for p in ([puerto] if config.NUBE else range(puerto, puerto + 20)):
        try:
            srv = ThreadingHTTPServer((host, p), Manejador)
            srv.daemon_threads = True
            APP.servidor = srv
            return srv
        except OSError:
            continue
    raise OSError("No hay puertos libres entre %d y %d" % (puerto, puerto + 19))


def _planificador():
    """Nube: refresca los datos periódicamente (si la NBA bloquea la descarga, se conserva el modelo actual)."""
    time.sleep(45)
    while True:
        while APP.trabajo["activo"]:
            time.sleep(5)
        APP.lanzar("Actualizando datos", APP.tarea_actualizar)
        time.sleep(max(0.1, config.REFRESCO_HORAS) * 3600)


def servir(puerto: int = 8765, abrir_navegador: bool = True, vigilar: bool = True):
    import webbrowser
    if config.NUBE:
        puerto, host, abrir_navegador, vigilar = int(os.environ.get("PORT", "10000")), "0.0.0.0", False, False
    else:
        host = "127.0.0.1"
    srv = crear_servidor(puerto, host)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    APP.lanzar("Cargando modelo", APP.tarea_inicio)
    if vigilar:
        threading.Thread(target=_vigilar_inactividad, daemon=True).start()
    if config.NUBE:
        threading.Thread(target=_planificador, daemon=True).start()
    print(f"NBA Predictor en {'0.0.0.0:%d (nube)' % srv.server_address[1] if config.NUBE else url}  (Ctrl+C para salir)", flush=True)
    if abrir_navegador:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
