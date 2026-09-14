"""Despliegue — Pruebas de integración del arranque del contenedor."""

import json
import signal
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

from despliegue import arranque


def _puerto_libre():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _leer_health(puerto):
    with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/health", timeout=2) as respuesta:
        return json.loads(respuesta.read().decode("utf-8"))


class TestVerificar(unittest.TestCase):
    """`--verificar` es lo que corre el HEALTHCHECK de la imagen."""

    def test_falla_si_no_hay_nadie_escuchando(self):
        self.assertEqual(arranque.verificar(_puerto_libre()), 1)


class TestNombre(unittest.TestCase):
    """El pipeline verifica el despliegue por el commit que muestra el /health."""

    def test_sin_version(self):
        self.assertEqual(arranque.nombre_nodo_d(""), "NodoD-nube")

    def test_con_version_abreviada_a_siete(self):
        self.assertEqual(arranque.nombre_nodo_d("0123456789abcdef"), "NodoD-nube@0123456")


class TestArranqueIntegracion(unittest.TestCase):
    """Levanta el arranque como proceso, igual que lo hace el contenedor."""

    def setUp(self):
        self.puerto_health = _puerto_libre()
        self.puerto_d = _puerto_libre()
        self.proceso = subprocess.Popen(
            [
                sys.executable, "-m", "despliegue.arranque",
                "--puerto-health", str(self.puerto_health),
                "--puerto-d", str(self.puerto_d),
                "--nodos-c", "2",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def tearDown(self):
        if self.proceso.poll() is None:
            self.proceso.kill()
            self.proceso.wait()

    def _esperar_nodos(self, cantidad, plazo=20.0):
        limite = time.monotonic() + plazo
        estado = None
        while time.monotonic() < limite:
            try:
                estado = _leer_health(self.puerto_health)
                if estado["cantidad_nodos_c_registrados"] >= cantidad:
                    return estado
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.2)
        self.fail(f"D no llegó a {cantidad} nodos C registrados: {estado}")

    def test_levanta_d_con_los_nodos_c_y_se_apaga_limpio(self):
        estado = self._esperar_nodos(2)
        self.assertEqual(estado["servicio"], "hit6-nodo-d")
        self.assertEqual(estado["estado"], "ok")
        # Cada C escucha en loopback en un puerto aleatorio distinto.
        self.assertEqual({n["ip"] for n in estado["nodos"]}, {"127.0.0.1"})
        self.assertEqual(len({n["puerto"] for n in estado["nodos"]}), 2)
        self.assertEqual({n["nombre"] for n in estado["nodos"]}, {"C1", "C2"})

        # Lo mismo que corre el HEALTHCHECK, contra este health.
        self.assertEqual(arranque.verificar(self.puerto_health), 0)

        # Docker apaga con SIGTERM: tiene que salir con 0 y liberar el puerto.
        self.proceso.send_signal(signal.SIGTERM)
        self.assertEqual(self.proceso.wait(timeout=10), 0)
        with self.assertRaises((urllib.error.URLError, OSError)):
            _leer_health(self.puerto_health)


if __name__ == "__main__":
    unittest.main()
