"""Hit #4 — pruebas del nodo C que actúa como cliente y servidor a la vez."""

import json
import logging
import socket
import time
import unittest
import urllib.request

from comun.health import iniciar_health
from comun.protocolo import LectorDeMensajes, enviar_mensaje
from hit4.nodo_c import NodoC


class TestNodoC(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test.hit4")
        self.logger.addHandler(logging.NullHandler())

    def _crear(self, nombre):
        nodo = NodoC("127.0.0.1", 0, self.logger, nombre)
        self.addCleanup(nodo.detener)
        return nodo

    def _esperar(self, condicion, timeout=5):
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if condicion():
                return True
            time.sleep(0.01)
        return False

    def _par_de_nodos(self):
        """Dos nodos, cada uno configurado con la dirección del otro."""
        uno = self._crear("C1")
        dos = self._crear("C2")
        uno.configurar_par("127.0.0.1", dos.puerto)
        dos.configurar_par("127.0.0.1", uno.puerto)
        return uno, dos

    def test_dos_nodos_se_saludan_mutuamente(self):
        uno, dos = self._par_de_nodos()
        uno.iniciar(espera_inicial=0.01)
        dos.iniciar(espera_inicial=0.01)

        listos = self._esperar(
            lambda: uno.estado()["respuestas_recibidas"] >= 1
            and dos.estado()["respuestas_recibidas"] >= 1
        )
        self.assertTrue(listos, "ambos nodos deben recibir respuesta del otro")

        estado_uno, estado_dos = uno.estado(), dos.estado()
        # Cada nodo saluda por su canal saliente y atiende el entrante del par.
        self.assertGreaterEqual(estado_uno["saludos_enviados"], 1)
        self.assertGreaterEqual(estado_uno["saludos_recibidos"], 1)
        self.assertGreaterEqual(estado_dos["saludos_enviados"], 1)
        self.assertGreaterEqual(estado_dos["saludos_recibidos"], 1)
        self.assertEqual(estado_uno["canal_saliente"], "conectado")
        self.assertEqual(estado_dos["canal_saliente"], "conectado")

    def test_son_dos_canales_independientes(self):
        """Cada sentido usa su propia conexión TCP."""
        uno, dos = self._par_de_nodos()
        uno.iniciar(espera_inicial=0.01)
        dos.iniciar(espera_inicial=0.01)

        self.assertTrue(
            self._esperar(
                lambda: uno.estado()["conexiones_atendidas"] >= 1
                and dos.estado()["conexiones_atendidas"] >= 1
            )
        )
        # Un canal entrante en cada nodo, alimentado por el saliente del otro.
        self.assertEqual(uno.estado()["conexiones_activas"], 1)
        self.assertEqual(dos.estado()["conexiones_activas"], 1)

    def test_el_nodo_espera_a_un_par_que_todavia_no_existe(self):
        """C debe tolerar arrancar antes que su par, sin abortar."""
        libre = socket.socket()
        libre.bind(("127.0.0.1", 0))
        puerto_futuro = libre.getsockname()[1]
        libre.close()

        solitario = self._crear("C-solo")
        solitario.configurar_par("127.0.0.1", puerto_futuro)
        solitario.iniciar(espera_inicial=0.01, espera_maxima=0.05)

        self.assertTrue(
            self._esperar(lambda: solitario.estado()["canal_saliente"] == "reintentando")
        )
        self.assertEqual(solitario.estado()["estado"], "ok", "debe seguir escuchando")

        # Al aparecer el par, el canal saliente se establece sin reiniciar el nodo.
        tardio = NodoC("127.0.0.1", puerto_futuro, self.logger, "C-tardio")
        self.addCleanup(tardio.detener)
        tardio.iniciar(espera_inicial=0.01)

        self.assertTrue(
            self._esperar(lambda: solitario.estado()["canal_saliente"] == "conectado"),
            "al aparecer el par, el canal saliente debe restablecerse",
        )

    def test_sigue_escuchando_si_el_par_se_cae(self):
        uno, dos = self._par_de_nodos()
        uno.iniciar(espera_inicial=0.01, espera_maxima=0.05)
        dos.iniciar(espera_inicial=0.01, espera_maxima=0.05)
        self.assertTrue(self._esperar(lambda: uno.estado()["respuestas_recibidas"] >= 1))

        dos.detener()

        self.assertTrue(
            self._esperar(lambda: uno.estado()["canal_saliente"] == "reintentando")
        )
        self.assertEqual(uno.estado()["estado"], "ok")

        # Un tercero todavía puede saludar al nodo que quedó en pie.
        with socket.create_connection(("127.0.0.1", uno.puerto), timeout=5) as sock:
            enviar_mensaje(sock, "saludo de un tercero")
            self.assertIn("saludo de un tercero", LectorDeMensajes(sock).leer_mensaje())

    def test_health_expone_ambos_lados(self):
        uno, dos = self._par_de_nodos()
        uno.iniciar(espera_inicial=0.01)
        dos.iniciar(espera_inicial=0.01)
        self.assertTrue(self._esperar(lambda: uno.estado()["respuestas_recibidas"] >= 1))

        http = iniciar_health(0, uno.estado, host="127.0.0.1")
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        url = f"http://127.0.0.1:{http.server_address[1]}/health"

        with urllib.request.urlopen(url, timeout=5) as resp:
            estado = json.loads(resp.read())

        self.assertEqual(estado["servicio"], "hit4-nodo-c")
        self.assertEqual(estado["nombre"], "C1")
        self.assertEqual(estado["par"], f"127.0.0.1:{dos.puerto}")
        self.assertGreaterEqual(estado["saludos_enviados"], 1)
        self.assertGreaterEqual(estado["saludos_recibidos"], 1)


if __name__ == "__main__":
    unittest.main()
