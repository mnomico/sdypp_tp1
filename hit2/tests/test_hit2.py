"""Hit #2 — pruebas unitarias y de integración de la reconexión de A."""

import logging
import socket
import struct
import threading
import time
import unittest

from comun.protocolo import ConexionCerrada, LectorDeMensajes, enviar_mensaje
from hit2 import cliente_a, servidor_b


class TestBackoff(unittest.TestCase):
    def test_la_espera_se_duplica(self):
        self.assertEqual(cliente_a.siguiente_espera(0.5, 5.0), 1.0)

    def test_la_espera_tiene_tope(self):
        self.assertEqual(cliente_a.siguiente_espera(4.0, 5.0), 5.0)


class TestIntegracionHit2(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("test.hit2")
        self.logger.addHandler(logging.NullHandler())
        self.esperas = []

    def _dormir_falso(self, segundos):
        self.esperas.append(segundos)

    def test_a_reconecta_y_resaluda_cuando_b_muere(self):
        """B responde y cierra el canal de golpe; A debe reconectar y resaludar."""
        escucha = servidor_b.crear_socket_servidor("127.0.0.1", 0)
        self.addCleanup(escucha.close)
        puerto = escucha.getsockname()[1]
        saludos = []

        def b_que_muere():
            # Dos vidas sucesivas de B: atiende, responde y se corta.
            for _ in range(2):
                conexion, _ = escucha.accept()
                with conexion:
                    saludos.append(LectorDeMensajes(conexion).leer_mensaje())
                    enviar_mensaje(conexion, "respuesta de B")

        hilo_b = threading.Thread(target=b_que_muere, daemon=True)
        hilo_b.start()

        respuestas = cliente_a.ejecutar(
            "127.0.0.1",
            puerto,
            self.logger,
            saludo="Hola B, soy A",
            max_ciclos=2,
            espera_inicial=0.01,
            dormir=self._dormir_falso,
        )
        hilo_b.join(timeout=5)

        self.assertEqual(respuestas, ["respuesta de B"] * 2, "A debe saludar tras reconectar")
        self.assertEqual(saludos, ["Hola B, soy A"] * 2)
        self.assertEqual(self.esperas, [0.01], "debe esperar antes de reintentar")

    def test_no_reconecta_mientras_b_sigue_vivo(self):
        """Regresión: el timeout de socket no debe provocar reconexiones espurias.

        Con un timeout corto y un B que se mantiene abierto sin decir nada, A debe
        quedarse vigilando el canal. Si el timeout alcanzara a la espera, A daría
        por caído a un B sano y volvería a conectarse una y otra vez.
        """
        escucha = servidor_b.crear_socket_servidor("127.0.0.1", 0)
        self.addCleanup(escucha.close)
        puerto = escucha.getsockname()[1]
        conexiones = []
        fin = threading.Event()

        def b_silencioso():
            while not fin.is_set():
                try:
                    conexion, _ = escucha.accept()
                except OSError:
                    return
                conexiones.append(conexion)
                lector = LectorDeMensajes(conexion)
                try:
                    enviar_mensaje(conexion, "hola " + lector.leer_mensaje())
                except (ConexionCerrada, OSError):
                    return

        hilo_b = threading.Thread(target=b_silencioso, daemon=True)
        hilo_b.start()
        self.addCleanup(fin.set)

        hilo_a = threading.Thread(
            target=cliente_a.ejecutar,
            args=("127.0.0.1", puerto, self.logger),
            kwargs={"max_ciclos": 5, "timeout": 0.2, "espera_inicial": 0.01,
                    "dormir": self._dormir_falso},
            daemon=True,
        )
        hilo_a.start()
        hilo_a.join(timeout=1.5)

        self.assertTrue(hilo_a.is_alive(), "A debe seguir vigilando el canal, no reconectar")
        self.assertEqual(len(conexiones), 1, f"A se reconecto {len(conexiones)} veces con B vivo")
        self.assertEqual(self.esperas, [])
        for conexion in conexiones:
            conexion.close()

    def test_salir_tras_respuesta_no_espera_el_cierre(self):
        escucha = servidor_b.crear_socket_servidor("127.0.0.1", 0)
        self.addCleanup(escucha.close)
        puerto = escucha.getsockname()[1]

        hilo_b = threading.Thread(
            target=servidor_b.atender_una_conexion, args=(escucha, self.logger), daemon=True
        )
        hilo_b.start()

        comienzo = time.monotonic()
        respuestas = cliente_a.ejecutar(
            "127.0.0.1", puerto, self.logger, max_ciclos=1, esperar_cierre=False
        )

        self.assertEqual(len(respuestas), 1)
        self.assertLess(time.monotonic() - comienzo, 2, "no debe esperar el cierre de B")
        hilo_b.join(timeout=2)

    def test_b_termina_ordenado_si_a_muere_de_golpe(self):
        """Regresión: un `kill -9` sobre A llega como RST (OSError), no como
        cierre ordenado. B tiene que terminar limpio, no con un traceback."""
        escucha = servidor_b.crear_socket_servidor("127.0.0.1", 0)
        self.addCleanup(escucha.close)
        puerto = escucha.getsockname()[1]
        resultado = {}

        def correr_b():
            try:
                resultado["saludos"] = servidor_b.atender_una_conexion(escucha, self.logger)
            except BaseException as error:  # noqa: BLE001 - se reporta al test
                resultado["error"] = error

        hilo_b = threading.Thread(target=correr_b, daemon=True)
        hilo_b.start()

        abrupto = socket.create_connection(("127.0.0.1", puerto), timeout=5)
        enviar_mensaje(abrupto, "Hola B, soy A")
        LectorDeMensajes(abrupto).leer_mensaje()
        # SO_LINGER en 0 fuerza un RST al cerrar: equivale a matar A.
        abrupto.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        abrupto.close()

        hilo_b.join(timeout=5)
        self.assertFalse(hilo_b.is_alive(), "B debe terminar, no quedar colgado")
        self.assertNotIn("error", resultado, f"B murio por excepcion: {resultado.get('error')!r}")
        self.assertEqual(resultado["saludos"], ["Hola B, soy A"])

    def test_b_descarta_lineas_ilegibles_sin_cortar(self):
        """Bytes que no son UTF-8 no deben tumbar el canal ni el proceso."""
        escucha = servidor_b.crear_socket_servidor("127.0.0.1", 0)
        self.addCleanup(escucha.close)
        puerto = escucha.getsockname()[1]
        recibidos = {}

        hilo_b = threading.Thread(
            target=lambda: recibidos.update(
                saludos=servidor_b.atender_una_conexion(escucha, self.logger)
            ),
            daemon=True,
        )
        hilo_b.start()

        with socket.create_connection(("127.0.0.1", puerto), timeout=5) as sock:
            sock.sendall(b"\xff\xfe\n")  # linea ilegible: se descarta
            enviar_mensaje(sock, "Hola B, soy A")
            self.assertIn("Hola B, soy A", LectorDeMensajes(sock).leer_mensaje())

        hilo_b.join(timeout=5)
        self.assertEqual(recibidos["saludos"], ["Hola B, soy A"])

    def test_a_reintenta_si_b_nunca_esta_disponible(self):
        """Sin B escuchando, A no debe abortar: reintenta con backoff creciente."""
        libre = socket.socket()
        libre.bind(("127.0.0.1", 0))
        puerto_muerto = libre.getsockname()[1]
        libre.close()

        respuestas = cliente_a.ejecutar(
            "127.0.0.1",
            puerto_muerto,
            self.logger,
            max_ciclos=3,
            espera_inicial=0.01,
            espera_maxima=1.0,
            dormir=self._dormir_falso,
        )

        self.assertEqual(respuestas, [])
        self.assertEqual(self.esperas, [0.01, 0.02])


if __name__ == "__main__":
    unittest.main()
