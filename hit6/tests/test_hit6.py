"""Hit #6 — Pruebas unitarias e integración de Nodo D y Nodo C."""

import json
import logging
import time
import unittest
import urllib.request

from comun import mensajes
from hit6.nodo_c import NodoC
from hit6.nodo_d import NodoD


class TestMensajesRegistro(unittest.TestCase):
    """Pruebas unitarias del formato de mensajes de registro."""

    def test_crear_registro(self):
        reg = mensajes.crear_registro("C1", "127.0.0.1", 12345)
        self.assertEqual(reg["tipo"], "registro")
        self.assertEqual(reg["origen"], "C1")
        self.assertEqual(reg["ip"], "127.0.0.1")
        self.assertEqual(reg["puerto"], 12345)
        self.assertTrue(reg["id"])
        self.assertTrue(reg["timestamp"])

    def test_crear_registro_respuesta(self):
        reg = mensajes.crear_registro("C1", "127.0.0.1", 12345)
        nodos = [{"ip": "127.0.0.1", "puerto": 54321}]
        resp = mensajes.crear_registro_respuesta("NodoD", reg, nodos)
        self.assertEqual(resp["tipo"], "registro_respuesta")
        self.assertEqual(resp["origen"], "NodoD")
        self.assertEqual(resp["en_respuesta_a"], reg["id"])
        self.assertEqual(resp["nodos"], nodos)


class TestHit6Integracion(unittest.TestCase):
    """Pruebas de integración para Nodo D y Nodo C en Hit 6."""

    def setUp(self):
        self.logger = logging.getLogger("test.hit6")
        self.logger.addHandler(logging.NullHandler())

    def _esperar(self, condicion, timeout=5):
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if condicion():
                return True
            time.sleep(0.02)
        return False

    def test_nodo_d_inicia_con_lista_vacia_en_ram(self):
        nodo_d = NodoD("127.0.0.1", 0, self.logger)
        self.addCleanup(nodo_d.detener)
        st = nodo_d.estado()
        self.assertEqual(st["cantidad_nodos_c_registrados"], 0)
        self.assertEqual(st["nodos"], [])
        self.assertEqual(st["estado_general"], "ok")

    def test_multiples_instancias_de_c_se_registran_en_d_y_se_saludan(self):
        # 1. Crear nodo D
        nodo_d = NodoD("127.0.0.1", 0, self.logger)
        nodo_d.iniciar()
        self.addCleanup(nodo_d.detener)

        # 2. Levantar C1
        c1 = NodoC(nodo_d.host, nodo_d.puerto, self.logger, nombre="C1")
        self.assertNotEqual(c1.puerto, 0, "Debe haber asignado un puerto aleatorio")
        c1.iniciar()
        self.addCleanup(c1.detener)

        # C1 debe registrarse en D
        self.assertTrue(
            self._esperar(lambda: nodo_d.estado()["cantidad_nodos_c_registrados"] == 1)
        )

        # 3. Levantar C2
        c2 = NodoC(nodo_d.host, nodo_d.puerto, self.logger, nombre="C2")
        self.assertNotEqual(c2.puerto, c1.puerto, "Cada C debe tener su propio puerto aleatorio")
        c2.iniciar()
        self.addCleanup(c2.detener)

        self.assertTrue(
            self._esperar(lambda: nodo_d.estado()["cantidad_nodos_c_registrados"] == 2)
        )

        # 4. Levantar C3
        c3 = NodoC(nodo_d.host, nodo_d.puerto, self.logger, nombre="C3")
        c3.iniciar()
        self.addCleanup(c3.detener)

        self.assertTrue(
            self._esperar(lambda: nodo_d.estado()["cantidad_nodos_c_registrados"] == 3)
        )

        # 5. Verificar que los saludos entre nodos C hayan sido intercambiados
        # C2 recibe C1 de D y lo saluda => C2 envia 1, C1 recibe 1
        # C3 recibe C1 y C2 de D y los saluda => C3 envia 2, C1 y C2 reciben 1 mas
        self.assertTrue(
            self._esperar(
                lambda: (
                    c2.estado()["saludos_enviados"] >= 1
                    and c3.estado()["saludos_enviados"] >= 2
                    and c1.estado()["saludos_recibidos"] >= 2
                )
            )
        )

        # Verificar el estado final de D
        st_d = nodo_d.estado()
        self.assertEqual(st_d["cantidad_nodos_c_registrados"], 3)
        self.assertEqual(st_d["nodos_c_registrados"], 3)
        self.assertEqual(len(st_d["nodos"]), 3)

    def test_health_endpoint_http_nodo_d(self):
        # Probar endpoint HTTP /health del nodo D
        nodo_d = NodoD("127.0.0.1", 0, self.logger)
        nodo_d.iniciar()
        self.addCleanup(nodo_d.detener)

        # Iniciar servidor health en puerto aleatorio 0
        from http.server import ThreadingHTTPServer
        from comun.health import _crear_handler
        import threading

        servidor_health = ThreadingHTTPServer(("127.0.0.1", 0), _crear_handler(nodo_d.estado))
        hilo = threading.Thread(target=servidor_health.serve_forever, daemon=True)
        hilo.start()
        self.addCleanup(servidor_health.shutdown)

        health_host, health_puerto = servidor_health.socket.getsockname()

        # Registrar un C en D
        c1 = NodoC(nodo_d.host, nodo_d.puerto, self.logger, nombre="C1")
        c1.iniciar()
        self.addCleanup(c1.detener)

        self.assertTrue(
            self._esperar(lambda: nodo_d.estado()["cantidad_nodos_c_registrados"] == 1)
        )

        # Hacer request a /health
        url = f"http://{health_host}:{health_puerto}/health"
        with urllib.request.urlopen(url) as respuesta:
            self.assertEqual(respuesta.status, 200)
            datos = json.loads(respuesta.read().decode("utf-8"))

        self.assertEqual(datos["servicio"], "hit6-nodo-d")
        self.assertEqual(datos["estado_general"], "ok")
        self.assertEqual(datos["cantidad_nodos_c_registrados"], 1)
        self.assertIn("uptime_segundos", datos)
        self.assertEqual(len(datos["nodos"]), 1)
        self.assertEqual(datos["nodos"][0]["ip"], c1.host)
        self.assertEqual(datos["nodos"][0]["puerto"], c1.puerto)


if __name__ == "__main__":
    unittest.main()
