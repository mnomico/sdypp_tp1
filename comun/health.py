"""Endpoint HTTP /health para verificar el estado del servicio."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _crear_handler(proveedor_estado):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] not in ("/health", "/"):
                self.send_error(404)
                return
            cuerpo = json.dumps(proveedor_estado()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, formato, *args):
            pass

    return Handler


def iniciar_health(puerto, proveedor_estado, host="0.0.0.0"):
    """Levanta el endpoint en un hilo daemon y devuelve el servidor HTTP."""
    servidor = ThreadingHTTPServer((host, puerto), _crear_handler(proveedor_estado))
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


def iniciar_health_opcional(puerto, proveedor_estado, host, logger):
    """Igual que `iniciar_health`, pero no voltea el servicio si falla.

    El health check es observabilidad, no la función del nodo: si el puerto está
    ocupado (dos nodos en la misma máquina con el `--puerto-health` por defecto)
    el servicio tiene que seguir andando y avisar, no morir con un traceback.

    Recibe el `host` explícitamente para que el endpoint quede atado a la misma
    interfaz que el servicio: escuchar siempre en 0.0.0.0 publicaría el estado
    del nodo en toda la red aunque se haya pedido `--host 127.0.0.1`.
    """
    try:
        servidor = iniciar_health(puerto, proveedor_estado, host=host)
    except OSError as error:
        logger.error(
            "No se pudo levantar /health en %s:%s (%s). El servicio continua sin el endpoint",
            host, puerto, error,
        )
        return None
    logger.info("Health disponible en http://%s:%s/health", host, puerto)
    return servidor
