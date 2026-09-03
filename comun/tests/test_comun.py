"""Pruebas unitarias del módulo común."""

import json
import logging
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from comun import config
from comun.health import iniciar_health
from comun.protocolo import (
    ConexionCerrada,
    ErrorDeProtocolo,
    LectorDeMensajes,
    MensajeDemasiadoLargo,
    MensajeIlegible,
    enviar_mensaje,
)
from comun.registro import HandlerEnMemoria, configurar


class SocketFalso:
    """Socket mínimo que entrega los trozos indicados y acumula lo enviado."""

    def __init__(self, trozos):
        self._trozos = list(trozos)
        self.enviado = b""

    def recv(self, _tam):
        return self._trozos.pop(0) if self._trozos else b""

    def sendall(self, datos):
        self.enviado += datos


class TestProtocolo(unittest.TestCase):
    def test_lee_un_mensaje_completo(self):
        lector = LectorDeMensajes(SocketFalso([b"hola\n"]))
        self.assertEqual(lector.leer_mensaje(), "hola")

    def test_reensambla_mensaje_partido_en_varios_recv(self):
        lector = LectorDeMensajes(SocketFalso([b"ho", b"la mun", b"do\n"]))
        self.assertEqual(lector.leer_mensaje(), "hola mundo")

    def test_separa_dos_mensajes_llegados_en_un_solo_recv(self):
        lector = LectorDeMensajes(SocketFalso([b"uno\ndos\n"]))
        self.assertEqual(lector.leer_mensaje(), "uno")
        self.assertEqual(lector.leer_mensaje(), "dos")

    def test_conexion_cerrada_sin_delimitador(self):
        lector = LectorDeMensajes(SocketFalso([b"incompleto"]))
        with self.assertRaises(ConexionCerrada):
            lector.leer_mensaje()

    def test_rechaza_mensaje_sin_delimitador_demasiado_largo(self):
        relleno = b"x" * 20
        lector = LectorDeMensajes(SocketFalso([relleno] * 5), max_bytes=32)
        with self.assertRaises(MensajeDemasiadoLargo):
            lector.leer_mensaje()

    def test_enviar_agrega_el_delimitador(self):
        sock = SocketFalso([])
        enviar_mensaje(sock, "hola")
        self.assertEqual(sock.enviado, b"hola\n")

    def test_soporta_acentos_y_enie(self):
        sock = SocketFalso([])
        enviar_mensaje(sock, "saludo con ñ y á")
        lector = LectorDeMensajes(SocketFalso([sock.enviado]))
        self.assertEqual(lector.leer_mensaje(), "saludo con ñ y á")

    def test_bytes_que_no_son_utf8_dan_error_de_protocolo(self):
        """Regresión: un UnicodeDecodeError crudo se escapaba de los `except`
        de los nodos (hereda de ValueError, no de OSError) y mataba el hilo."""
        lector = LectorDeMensajes(SocketFalso([b"\xff\xfe\n"]))
        with self.assertRaises(MensajeIlegible) as ctx:
            lector.leer_mensaje()
        self.assertIsInstance(ctx.exception, ErrorDeProtocolo)

    def test_tras_una_linea_ilegible_se_puede_seguir_leyendo(self):
        """La línea defectuosa debe quedar consumida: si no, quien haga
        `continue` tras el error entraría en un bucle infinito sobre el mismo
        buffer."""
        lector = LectorDeMensajes(SocketFalso([b"\xff\xfe\nsigo vivo\n"]))
        with self.assertRaises(MensajeIlegible):
            lector.leer_mensaje()
        self.assertEqual(lector.leer_mensaje(), "sigo vivo")

    def test_todos_los_errores_de_lectura_comparten_una_base(self):
        """Permite a los bucles supervisores atrapar la familia completa."""
        for excepcion in (ConexionCerrada, MensajeDemasiadoLargo, MensajeIlegible):
            self.assertTrue(issubclass(excepcion, ErrorDeProtocolo), excepcion)


class TestRegistro(unittest.TestCase):
    def test_handler_en_memoria_respeta_la_capacidad(self):
        handler = HandlerEnMemoria(capacidad=2)
        handler.setFormatter(logging.Formatter("%(message)s"))
        for mensaje in ("uno", "dos", "tres"):
            handler.emit(logging.LogRecord("t", logging.INFO, "", 0, mensaje, None, None))
        self.assertEqual(handler.ultimos(), ["dos", "tres"])

    def test_escribe_en_memoria_y_en_disco(self):
        with tempfile.TemporaryDirectory() as tmp:
            logger, memoria = configurar("prueba.registro", "prueba.log", directorio=tmp)
            logger.info("mensaje de prueba")
            for handler in logger.handlers:
                handler.flush()

            self.assertTrue(any("mensaje de prueba" in r for r in memoria.ultimos()))
            contenido = (Path(tmp) / "prueba.log").read_text(encoding="utf-8")
            self.assertIn("mensaje de prueba", contenido)

    def test_reconfigurar_cierra_los_handlers_anteriores(self):
        """Regresión: `handlers.clear()` descartaba los handlers sin cerrarlos,
        dejando el archivo de log abierto en cada reconfiguración."""
        with tempfile.TemporaryDirectory() as tmp:
            logger, _ = configurar("prueba.cierre", "cierre.log", directorio=tmp)
            previos = list(logger.handlers)
            configurar("prueba.cierre", "cierre.log", directorio=tmp)

            # Sólo los handlers de archivo: `StreamHandler.close()` no toca
            # stderr a propósito, y cerrar la consola sería un error.
            archivos = [h for h in previos if isinstance(h, logging.FileHandler)]
            self.assertTrue(archivos, "debe haber un handler de archivo")
            for handler in archivos:
                self.assertTrue(
                    handler.stream is None or handler.stream.closed,
                    f"{handler} quedo abierto tras reconfigurar",
                )


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.previo = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self.previo)))

    def test_lee_valores_del_entorno(self):
        os.environ["TP1_PRUEBA_HOST"] = "10.0.0.5"
        os.environ["TP1_PRUEBA_PUERTO"] = "9500"
        os.environ["TP1_PRUEBA_ESPERA"] = "1.5"
        self.assertEqual(config.texto("TP1_PRUEBA_HOST", "127.0.0.1"), "10.0.0.5")
        self.assertEqual(config.entero("TP1_PRUEBA_PUERTO", 9001), 9500)
        self.assertEqual(config.decimal("TP1_PRUEBA_ESPERA", 0.5), 1.5)

    def test_usa_el_default_si_falta_o_esta_vacia(self):
        os.environ.pop("TP1_PRUEBA_AUSENTE", None)
        os.environ["TP1_PRUEBA_VACIA"] = "   "
        self.assertEqual(config.texto("TP1_PRUEBA_AUSENTE", "defecto"), "defecto")
        self.assertEqual(config.texto("TP1_PRUEBA_VACIA", "defecto"), "defecto")

    def test_valor_invalido_cae_al_default_en_vez_de_romper(self):
        os.environ["TP1_PRUEBA_PUERTO"] = "no-es-un-numero"
        self.assertEqual(config.entero("TP1_PRUEBA_PUERTO", 9001), 9001)
        self.assertEqual(config.decimal("TP1_PRUEBA_PUERTO", 0.5), 0.5)

    def test_carga_un_archivo_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / ".env"
            ruta.write_text(
                "# comentario\n"
                "\n"
                "TP1_PRUEBA_DESDE_ENV = 10.1.1.1\n"
                'TP1_PRUEBA_CON_COMILLAS="hola mundo"\n',
                encoding="utf-8",
            )
            os.environ.pop("TP1_PRUEBA_DESDE_ENV", None)
            os.environ.pop("TP1_PRUEBA_CON_COMILLAS", None)
            config.cargar_env(ruta)

            self.assertEqual(os.environ["TP1_PRUEBA_DESDE_ENV"], "10.1.1.1")
            self.assertEqual(os.environ["TP1_PRUEBA_CON_COMILLAS"], "hola mundo")

    def test_el_entorno_real_tiene_prioridad_sobre_el_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / ".env"
            ruta.write_text("TP1_PRUEBA_PRIORIDAD=del-archivo\n", encoding="utf-8")
            os.environ["TP1_PRUEBA_PRIORIDAD"] = "del-entorno"
            config.cargar_env(ruta)

            self.assertEqual(os.environ["TP1_PRUEBA_PRIORIDAD"], "del-entorno")

    def test_env_inexistente_no_falla(self):
        config.cargar_env(Path("/no/existe/.env"))


class TestHealth(unittest.TestCase):
    def _levantar(self, proveedor_estado):
        servidor = iniciar_health(0, proveedor_estado, host="127.0.0.1")
        self.addCleanup(servidor.server_close)
        self.addCleanup(servidor.shutdown)
        return servidor.server_address[1]

    def test_endpoint_devuelve_el_estado_en_json(self):
        puerto = self._levantar(lambda: {"servicio": "prueba", "estado": "ok"})

        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/health", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(json.loads(resp.read()), {"servicio": "prueba", "estado": "ok"})

    def test_ruta_desconocida_devuelve_404(self):
        puerto = self._levantar(dict)

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{puerto}/otra", timeout=5)
        ctx.exception.close()
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
