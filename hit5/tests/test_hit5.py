"""Hit #5 — pruebas de la serialización JSON y del nodo C que la usa."""

import json
import logging
import socket
import time
import unittest

from comun import mensajes
from comun.protocolo import LectorDeMensajes, enviar_mensaje
from hit5.nodo_c import NodoC


class TestSerializacion(unittest.TestCase):
    """Pruebas unitarias del formato de mensajes."""

    def test_el_saludo_tiene_los_campos_del_protocolo(self):
        saludo = mensajes.crear_saludo("C1")
        self.assertEqual(saludo["tipo"], "saludo")
        self.assertEqual(saludo["origen"], "C1")
        self.assertEqual(saludo["version"], mensajes.VERSION_PROTOCOLO)
        self.assertIn("C1", saludo["contenido"])
        self.assertTrue(saludo["id"])
        self.assertTrue(saludo["timestamp"])

    def test_cada_saludo_lleva_un_id_distinto(self):
        self.assertNotEqual(
            mensajes.crear_saludo("C1")["id"], mensajes.crear_saludo("C1")["id"]
        )

    def test_la_respuesta_referencia_al_saludo(self):
        saludo = mensajes.crear_saludo("C1")
        respuesta = mensajes.crear_respuesta("C2", saludo)
        self.assertEqual(respuesta["tipo"], "respuesta")
        self.assertEqual(respuesta["origen"], "C2")
        self.assertEqual(respuesta["en_respuesta_a"], saludo["id"])
        self.assertIn("C1", respuesta["contenido"], "debe nombrar a quien saludo")

    def test_ida_y_vuelta_conserva_el_mensaje(self):
        original = mensajes.crear_saludo("C1", "saludo con ñ, acentos y \"comillas\"")
        self.assertEqual(mensajes.deserializar(mensajes.serializar(original)), original)

    def test_serializa_en_una_sola_linea(self):
        """El framing por '\\n' exige que un mensaje no contenga saltos literales."""
        texto = mensajes.serializar(mensajes.crear_saludo("C1", "dos\nlineas"))
        self.assertNotIn("\n", texto)
        self.assertEqual(mensajes.deserializar(texto)["contenido"], "dos\nlineas")

    def test_conserva_los_acentos_como_utf8(self):
        texto = mensajes.serializar(mensajes.crear_saludo("C1", "canción"))
        self.assertIn("canción", texto, "no debe escapar a \\uXXXX")

    def test_rechaza_json_mal_formado(self):
        with self.assertRaises(mensajes.MensajeInvalido):
            mensajes.deserializar("{no es json")

    def test_rechaza_json_que_no_es_un_objeto(self):
        with self.assertRaises(mensajes.MensajeInvalido):
            mensajes.deserializar("[1, 2, 3]")

    def test_rechaza_mensaje_sin_campos_obligatorios(self):
        with self.assertRaises(mensajes.MensajeInvalido) as ctx:
            mensajes.deserializar(json.dumps({"tipo": "saludo"}))
        self.assertIn("origen", str(ctx.exception))

    def test_informa_el_tamano_en_bytes(self):
        saludo = mensajes.crear_saludo("C1")
        self.assertEqual(
            mensajes.tamano_en_bytes(saludo),
            len(mensajes.serializar(saludo).encode("utf-8")),
        )


class TestNodoCJson(unittest.TestCase):
    """Pruebas de integración: dos nodos intercambiando JSON."""

    def setUp(self):
        self.logger = logging.getLogger("test.hit5")
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

    def test_dos_nodos_se_saludan_en_json(self):
        uno, dos = self._crear("C1"), self._crear("C2")
        uno.configurar_par("127.0.0.1", dos.puerto)
        dos.configurar_par("127.0.0.1", uno.puerto)
        uno.iniciar(espera_inicial=0.01)
        dos.iniciar(espera_inicial=0.01)

        self.assertTrue(
            self._esperar(
                lambda: uno.estado()["respuestas_recibidas"] >= 1
                and dos.estado()["respuestas_recibidas"] >= 1
            )
        )
        self.assertEqual(uno.estado()["formato_mensajes"], "json")

    def test_lo_que_viaja_por_el_socket_es_json_valido(self):
        """Se inspecciona el byte a byte del canal, no sólo el resultado."""
        nodo = self._crear("C-servidor")
        nodo.iniciar(espera_inicial=0.01)

        with socket.create_connection(("127.0.0.1", nodo.puerto), timeout=5) as sock:
            saludo = mensajes.crear_saludo("C-cliente")
            enviar_mensaje(sock, mensajes.serializar(saludo))
            crudo = LectorDeMensajes(sock).leer_mensaje()

        respuesta = json.loads(crudo)
        self.assertEqual(respuesta["tipo"], "respuesta")
        self.assertEqual(respuesta["origen"], "C-servidor")
        self.assertEqual(respuesta["en_respuesta_a"], saludo["id"])
        self.assertIn("C-cliente", respuesta["contenido"])

    def test_un_mensaje_invalido_no_tumba_el_nodo(self):
        nodo = self._crear("C-servidor")
        nodo.iniciar(espera_inicial=0.01)

        with socket.create_connection(("127.0.0.1", nodo.puerto), timeout=5) as sock:
            enviar_mensaje(sock, "esto no es json")
            enviar_mensaje(sock, json.dumps({"tipo": "saludo"}))  # faltan campos
            self.assertTrue(self._esperar(lambda: nodo.estado()["mensajes_invalidos"] >= 2))

            # El canal sigue vivo: un mensaje correcto se responde igual.
            saludo = mensajes.crear_saludo("C-cliente")
            enviar_mensaje(sock, mensajes.serializar(saludo))
            respuesta = mensajes.deserializar(LectorDeMensajes(sock).leer_mensaje())

        self.assertEqual(respuesta["en_respuesta_a"], saludo["id"])
        self.assertEqual(nodo.estado()["estado"], "ok")

    def test_bytes_ilegibles_se_cuentan_y_no_cortan_el_canal(self):
        """Regresión: el README promete que un mensaje mal formado se descarta
        sin cortar el canal, pero con bytes que no son UTF-8 el hilo moría con
        un traceback fuera del log y la conexión se caía."""
        nodo = self._crear("C-servidor")
        nodo.iniciar(espera_inicial=0.01)

        with socket.create_connection(("127.0.0.1", nodo.puerto), timeout=5) as sock:
            sock.sendall(b"\xff\xfe\xfa\n")
            self.assertTrue(self._esperar(lambda: nodo.estado()["mensajes_invalidos"] >= 1))

            saludo = mensajes.crear_saludo("C-cliente")
            enviar_mensaje(sock, mensajes.serializar(saludo))
            respuesta = mensajes.deserializar(LectorDeMensajes(sock).leer_mensaje())

        self.assertEqual(respuesta["en_respuesta_a"], saludo["id"])
        self.assertEqual(nodo.estado()["estado"], "ok")

    def test_solo_responde_a_los_mensajes_de_tipo_saludo(self):
        """El protocolo tiene mas de un tipo: contestar a todo con una
        respuesta contaba respuestas ajenas como saludos en el /health."""
        nodo = self._crear("C-servidor")
        nodo.iniciar(espera_inicial=0.01)

        ajeno = mensajes.crear_respuesta("C-otro", mensajes.crear_saludo("C-tercero"))
        with socket.create_connection(("127.0.0.1", nodo.puerto), timeout=5) as sock:
            enviar_mensaje(sock, mensajes.serializar(ajeno))
            self.assertTrue(self._esperar(lambda: nodo.estado()["mensajes_ignorados"] >= 1))

            # El canal sigue abierto y un saludo de verdad se responde igual.
            saludo = mensajes.crear_saludo("C-cliente")
            enviar_mensaje(sock, mensajes.serializar(saludo))
            respuesta = mensajes.deserializar(LectorDeMensajes(sock).leer_mensaje())

        self.assertEqual(respuesta["en_respuesta_a"], saludo["id"])
        self.assertEqual(nodo.estado()["saludos_recibidos"], 1, "solo cuenta saludos reales")

    def test_varios_mensajes_en_un_mismo_segmento(self):
        """Dos JSON pegados en un solo send se separan por el delimitador."""
        nodo = self._crear("C-servidor")
        nodo.iniciar(espera_inicial=0.01)

        primero = mensajes.crear_saludo("C-uno")
        segundo = mensajes.crear_saludo("C-dos")
        crudo = (
            mensajes.serializar(primero) + "\n" + mensajes.serializar(segundo) + "\n"
        ).encode("utf-8")

        with socket.create_connection(("127.0.0.1", nodo.puerto), timeout=5) as sock:
            sock.sendall(crudo)
            lector = LectorDeMensajes(sock)
            una = mensajes.deserializar(lector.leer_mensaje())
            otra = mensajes.deserializar(lector.leer_mensaje())

        self.assertEqual([una["en_respuesta_a"], otra["en_respuesta_a"]],
                         [primero["id"], segundo["id"]])


if __name__ == "__main__":
    unittest.main()
