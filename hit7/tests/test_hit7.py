"""Hit #7 — Pruebas unitarias e integración del sistema de inscripciones por ventanas."""

import json
import logging
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from comun import mensajes
from hit7.nodo_c import NodoC
from hit7.nodo_d import NodoD


class TestMensajesHit7(unittest.TestCase):
    """Pruebas unitarias del formato de mensajes para consulta de activos."""

    def test_crear_consulta_activos(self):
        c = mensajes.crear_consulta_activos("C1")
        self.assertEqual(c["tipo"], "consulta_activos")
        self.assertEqual(c["origen"], "C1")
        self.assertTrue(c["id"])

    def test_crear_consulta_activos_respuesta(self):
        c = mensajes.crear_consulta_activos("C1")
        activos = [{"ip": "127.0.0.1", "puerto": 12345}]
        resp = mensajes.crear_consulta_activos_respuesta("NodoD", c, activos)
        self.assertEqual(resp["tipo"], "consulta_activos_respuesta")
        self.assertEqual(resp["en_respuesta_a"], c["id"])
        self.assertEqual(resp["nodos_activos"], activos)


class TestHit7Integracion(unittest.TestCase):
    """Pruebas de integración del sistema de inscripciones por ventanas."""

    def setUp(self):
        self.logger = logging.getLogger("test.hit7")
        self.logger.addHandler(logging.NullHandler())
        self.temp_dir = tempfile.TemporaryDirectory()
        self.json_path = Path(self.temp_dir.name) / "inscripciones.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _esperar(self, condicion, timeout=5):
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if condicion():
                return True
            time.sleep(0.02)
        return False

    def test_los_registros_van_a_la_siguiente_ventana_y_rotan(self):
        # Nodo D con ventana de 0.3 segundos para probar la rotación rápidamente
        nodo_d = NodoD(
            "127.0.0.1",
            0,
            self.logger,
            duracion_ventana=0.3,
            archivo_inscripciones=self.json_path,
        )
        nodo_d.iniciar()
        self.addCleanup(nodo_d.detener)

        # Estado inicial
        st0 = nodo_d.estado()
        self.assertEqual(st0["cantidad_nodos_activos"], 0)
        self.assertEqual(st0["cantidad_nodos_siguientes"], 0)

        # Levantar C1
        c1 = NodoC(nodo_d.host, nodo_d.puerto, self.logger, nombre="C1")
        c1.iniciar()
        self.addCleanup(c1.detener)

        # C1 debe registrarse en la ventana SIGUIENTE (nodos_siguientes)
        self.assertTrue(
            self._esperar(lambda: nodo_d.estado()["cantidad_nodos_siguientes"] == 1)
        )
        # En la ventana actual sigue habiendo 0 activos
        self.assertEqual(nodo_d.estado()["cantidad_nodos_activos"], 0)

        # Esperar a que ocurra la rotación de ventana (0.3s)
        self.assertTrue(
            self._esperar(
                lambda: (
                    nodo_d.estado()["cantidad_nodos_activos"] == 1
                    and nodo_d.estado()["cantidad_nodos_siguientes"] == 0
                )
            )
        )

        # Verificar que la persitencia JSON se creó y tiene la estructura correcta
        self.assertTrue(self.json_path.exists())
        contenido_json = json.loads(self.json_path.read_text(encoding="utf-8"))
        self.assertEqual(contenido_json["cantidad_nodos_activos"], 1)
        self.assertEqual(len(contenido_json["nodos_activos"]), 1)
        self.assertEqual(contenido_json["nodos_activos"][0]["nombre"], "C1")

    def test_dos_nodos_c_en_distintas_ventanas(self):
        nodo_d = NodoD(
            "127.0.0.1",
            0,
            self.logger,
            duracion_ventana=0.4,
            archivo_inscripciones=self.json_path,
        )
        nodo_d.iniciar()
        self.addCleanup(nodo_d.detener)

        # 1. C1 se inscribe para la siguiente ventana
        c1 = NodoC(nodo_d.host, nodo_d.puerto, self.logger, nombre="C1")
        c1.iniciar()
        self.addCleanup(c1.detener)

        self.assertTrue(
            self._esperar(lambda: nodo_d.estado()["cantidad_nodos_siguientes"] == 1)
        )

        # Rotar ventana manualmente para test
        nodo_d.rotar_ventana()

        # C1 pasa a ser activo en la ventana actual
        self.assertEqual(nodo_d.estado()["cantidad_nodos_activos"], 1)
        self.assertEqual(nodo_d.estado()["cantidad_nodos_siguientes"], 0)

        # 2. C2 se inscribe ahora (para la siguiente ventana)
        c2 = NodoC(nodo_d.host, nodo_d.puerto, self.logger, nombre="C2", intervalo_consulta=0.1)
        c2.iniciar()
        self.addCleanup(c2.detener)

        # Al registrarse, C2 ve los activos de la ventana actual (que es C1) y lo saluda
        self.assertTrue(
            self._esperar(
                lambda: c2.estado()["saludos_enviados"] >= 1 and c1.estado()["saludos_recibidos"] >= 1
            )
        )

        # D reporta C1 activo y C2 en siguientes
        st = nodo_d.estado()
        self.assertEqual(st["cantidad_nodos_activos"], 1)
        self.assertEqual(st["cantidad_nodos_siguientes"], 1)

    def test_health_endpoint_http(self):
        nodo_d = NodoD(
            "127.0.0.1",
            0,
            self.logger,
            duracion_ventana=10.0,
            archivo_inscripciones=self.json_path,
        )
        nodo_d.iniciar()
        self.addCleanup(nodo_d.detener)

        # Servidor health en puerto aleatorio
        from http.server import ThreadingHTTPServer
        from comun.health import _crear_handler
        import threading

        servidor_health = ThreadingHTTPServer(("127.0.0.1", 0), _crear_handler(nodo_d.estado))
        hilo = threading.Thread(target=servidor_health.serve_forever, daemon=True)
        hilo.start()
        self.addCleanup(servidor_health.shutdown)

        h_host, h_puerto = servidor_health.socket.getsockname()

        c1 = NodoC(nodo_d.host, nodo_d.puerto, self.logger, nombre="C1")
        c1.iniciar()
        self.addCleanup(c1.detener)

        self.assertTrue(
            self._esperar(lambda: nodo_d.estado()["cantidad_nodos_siguientes"] == 1)
        )

        url = f"http://{h_host}:{h_puerto}/health"
        with urllib.request.urlopen(url) as respuesta:
            self.assertEqual(respuesta.status, 200)
            datos = json.loads(respuesta.read().decode("utf-8"))

        self.assertEqual(datos["servicio"], "hit7-nodo-d")
        self.assertEqual(datos["cantidad_nodos_activos"], 0)
        self.assertEqual(datos["cantidad_nodos_siguientes"], 1)
        self.assertEqual(datos["nodos_siguientes"][0]["nombre"], "C1")


if __name__ == "__main__":
    unittest.main()
