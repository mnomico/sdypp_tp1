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
