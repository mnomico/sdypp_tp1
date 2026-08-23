"""Hit #3 — pruebas de integración del servidor B persistente."""

import json
import logging
import socket
import struct
import time
import unittest
import urllib.request

from comun.health import iniciar_health
from comun.protocolo import LectorDeMensajes, enviar_mensaje
from hit3 import servidor_b


def saludar_y_leer(puerto, saludo):
    with socket.create_connection(("127.0.0.1", puerto), timeout=5) as sock:
        enviar_mensaje(sock, saludo)
        return LectorDeMensajes(sock).leer_mensaje()


class TestIntegracionHit3(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test.hit3")
        self.logger.addHandler(logging.NullHandler())
        self.servidor = servidor_b.ServidorB("127.0.0.1", 0, self.logger)
        self.servidor.iniciar_en_hilo()
        self.addCleanup(self.servidor.detener)
        self.puerto = self.servidor.puerto

    def _esperar(self, condicion, timeout=5):
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if condicion():
                return True
            time.sleep(0.01)
        return False

    def test_b_sigue_vivo_despues_de_que_a_corta_de_golpe(self):
        """Un RST del cliente no debe tumbar al servidor."""
        abrupto = socket.create_connection(("127.0.0.1", self.puerto), timeout=5)
        enviar_mensaje(abrupto, "Hola B, soy A")
        LectorDeMensajes(abrupto).leer_mensaje()
        # SO_LINGER en 0 fuerza un RST al cerrar, simulando un kill del proceso A.
        abrupto.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        abrupto.close()

        self.assertTrue(self._esperar(lambda: self.servidor.estado()["conexiones_activas"] == 0))
        self.assertIn("Hola B, soy A", saludar_y_leer(self.puerto, "Hola B, soy A"))
        self.assertEqual(self.servidor.estado()["estado"], "ok")

    def test_atiende_varias_conexiones_sucesivas(self):
        for i in range(3):
            self.assertIn(f"saludo {i}", saludar_y_leer(self.puerto, f"saludo {i}"))
        self.assertEqual(self.servidor.estado()["conexiones_atendidas"], 3)

    def test_atiende_clientes_concurrentes(self):
        primero = socket.create_connection(("127.0.0.1", self.puerto), timeout=5)
        self.addCleanup(primero.close)
        enviar_mensaje(primero, "cliente uno")
        self.assertIn("cliente uno", LectorDeMensajes(primero).leer_mensaje())

        self.assertIn("cliente dos", saludar_y_leer(self.puerto, "cliente dos"))

        enviar_mensaje(primero, "cliente uno otra vez")
        self.assertIn("cliente uno otra vez", LectorDeMensajes(primero).leer_mensaje())

    def test_health_refleja_el_estado_del_servidor(self):
        http = iniciar_health(0, self.servidor.estado, host="127.0.0.1")
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        puerto_http = http.server_address[1]

        saludar_y_leer(self.puerto, "para el health")
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto_http}/health", timeout=5) as resp:
            estado = json.loads(resp.read())

        self.assertEqual(estado["servicio"], "hit3-servidor-b")
        self.assertEqual(estado["estado"], "ok")
        self.assertGreaterEqual(estado["conexiones_atendidas"], 1)
        self.assertGreaterEqual(estado["uptime_segundos"], 0)


if __name__ == "__main__":
    unittest.main()
