"""Hit #1 — pruebas unitarias y de integración."""

import logging
import threading
import unittest

from hit1 import cliente_a, servidor_b


class TestRespuesta(unittest.TestCase):
    def test_la_respuesta_incluye_el_saludo_recibido(self):
        self.assertIn("Hola B, soy A", servidor_b.construir_respuesta("Hola B, soy A"))


class TestIntegracionHit1(unittest.TestCase):
    """A se conecta a B, lo saluda y B le responde."""

    def setUp(self):
        self.logger = logging.getLogger("test.hit1")
        self.logger.addHandler(logging.NullHandler())
        self.servidor = servidor_b.crear_socket_servidor("127.0.0.1", 0)
        self.addCleanup(self.servidor.close)
        self.puerto = self.servidor.getsockname()[1]

    def test_b_responde_el_saludo_de_a(self):
        recibido = {}

        def correr_servidor():
            recibido["datos"] = servidor_b.atender_una_conexion(self.servidor, self.logger)

        hilo = threading.Thread(target=correr_servidor)
        hilo.start()

        respuesta = cliente_a.saludar("127.0.0.1", self.puerto, "Hola B, soy A")
        hilo.join(timeout=5)

        self.assertFalse(hilo.is_alive())
        saludo_visto, respuesta_enviada = recibido["datos"]
        self.assertEqual(saludo_visto, "Hola B, soy A")
        self.assertEqual(respuesta, respuesta_enviada)
        self.assertIn("Hola B, soy A", respuesta)


if __name__ == "__main__":
    unittest.main()
