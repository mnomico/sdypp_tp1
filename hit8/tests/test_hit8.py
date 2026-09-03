"""Hit #8 — Pruebas de comunicación gRPC y mensajes Protocol Buffers."""

import logging
import time
import unittest

import grpc

from hit8.nodo_c import NodoC
from hit8.nodo_d import NodoD
from hit8.proto import servicio_pb2, servicio_pb2_grpc


class TestProtobufFormato(unittest.TestCase):
    """Pruebas unitarias de serialización y campos Protobuf."""

    def test_mensaje_saludo_campos_y_serializacion(self):
        saludo = servicio_pb2.MensajeSaludo(
            version=1,
            id="test-id-123",
            tipo="saludo",
            origen="C1",
            contenido="Hola, soy C1",
            timestamp="2026-09-03T16:00:00+00:00",
        )
        raw = saludo.SerializeToString()
        self.assertIsInstance(raw, bytes)
        self.assertGreater(len(raw), 0)

        deserializado = servicio_pb2.MensajeSaludo()
        deserializado.ParseFromString(raw)
        self.assertEqual(deserializado.id, "test-id-123")
        self.assertEqual(deserializado.origen, "C1")
        self.assertEqual(deserializado.contenido, "Hola, soy C1")
        self.assertEqual(deserializado.version, 1)

    def test_mensaje_respuesta_campos_y_serializacion(self):
        respuesta = servicio_pb2.MensajeRespuesta(
            version=1,
            id="test-resp-456",
            tipo="respuesta",
            origen="C2",
            contenido="Hola C1, soy C2.",
            en_respuesta_a="test-id-123",
            timestamp="2026-09-03T16:00:01+00:00",
        )
        raw = respuesta.SerializeToString()
        deserializado = servicio_pb2.MensajeRespuesta.FromString(raw)
        self.assertEqual(deserializado.id, "test-resp-456")
        self.assertEqual(deserializado.en_respuesta_a, "test-id-123")
        self.assertEqual(deserializado.origen, "C2")

    def test_comparacion_tamano_binario_vs_json(self):
        saludo_proto = servicio_pb2.MensajeSaludo(
            version=1,
            id="95ae61fd-84b9-4674-8b01-52504b28ba57",
            tipo="saludo",
            origen="C1",
            contenido="Hola, soy C1",
            timestamp="2026-09-03T16:00:00.000000+00:00",
        )
        tamano_proto = saludo_proto.ByteSize()
        # En JSON el mismo saludo ocupa 161 bytes; en Protobuf ocupa 100 bytes (~38% ahorro)
        self.assertLess(tamano_proto, 120)
        self.assertEqual(tamano_proto, 100)


class TestNodoCGrpc(unittest.TestCase):
    """Pruebas de integración: nodos C comunicándose vía gRPC."""

    def setUp(self):
        self.logger = logging.getLogger("test.hit8")
        self.logger.addHandler(logging.NullHandler())

    def _crear_c(self, nombre):
        nodo = NodoC("127.0.0.1", 0, self.logger, nombre=nombre)
        self.addCleanup(nodo.detener)
        return nodo

    def _crear_d(self, nombre="NodoD"):
        nodo = NodoD("127.0.0.1", 0, self.logger, nombre=nombre)
        self.addCleanup(nodo.detener)
        return nodo

    def _esperar(self, condicion, timeout=5):
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            if condicion():
                return True
            time.sleep(0.01)
        return False

    def test_dos_nodos_se_saludan_en_grpc(self):
        uno = self._crear_c("C1")
        dos = self._crear_c("C2")
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
        self.assertEqual(uno.estado()["formato_mensajes"], "protobuf-grpc")
        self.assertEqual(uno.estado()["estado"], "ok")

    def test_llamada_grpc_directa_con_stub(self):
        servidor = self._crear_c("C-servidor")
        servidor.iniciar()

        with grpc.insecure_channel(f"127.0.0.1:{servidor.puerto}") as channel:
            stub = servicio_pb2_grpc.MensajeriaCStub(channel)
            saludo = servicio_pb2.MensajeSaludo(
                version=1,
                id="req-1",
                tipo="saludo",
                origen="C-cliente",
                contenido="Hola desde stub gRPC",
                timestamp="2026-09-03T16:00:00+00:00",
            )
            respuesta = stub.Saludar(saludo, timeout=5)

            self.assertEqual(respuesta.tipo, "respuesta")
            self.assertEqual(respuesta.origen, "C-servidor")
            self.assertEqual(respuesta.en_respuesta_a, "req-1")
            self.assertIn("C-cliente", respuesta.contenido)

    def test_rechazo_mensaje_grpc_invalido_o_tipo_incompatible(self):
        servidor = self._crear_c("C-servidor")
        servidor.iniciar()

        with grpc.insecure_channel(f"127.0.0.1:{servidor.puerto}") as channel:
            stub = servicio_pb2_grpc.MensajeriaCStub(channel)
            # 1. Faltan campos requeridos
            invalido = servicio_pb2.MensajeSaludo(version=1, id="1", origen="", contenido="")
            with self.assertRaises(grpc.RpcError) as ctx:
                stub.Saludar(invalido, timeout=5)
            self.assertEqual(ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

            # 2. Tipo incompatible (ej. respuesta enviada a endpoint de saludo)
            ajeno = servicio_pb2.MensajeSaludo(
                version=1, id="2", tipo="respuesta", origen="C-otro", contenido="Hola"
            )
            with self.assertRaises(grpc.RpcError) as ctx:
                stub.Saludar(ajeno, timeout=5)
            self.assertEqual(ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

        self.assertTrue(self._esperar(lambda: servidor.estado()["mensajes_invalidos"] >= 1))
        self.assertTrue(self._esperar(lambda: servidor.estado()["mensajes_ignorados"] >= 1))

    def test_descubrimiento_y_saludo_dinamico_con_nodo_d(self):
        d = self._crear_d("NodoD")
        d.iniciar()

        c1 = NodoC("127.0.0.1", 0, self.logger, nombre="C1", d_host="127.0.0.1", d_puerto=d.puerto)
        self.addCleanup(c1.detener)
        c1.iniciar()

        c2 = NodoC("127.0.0.1", 0, self.logger, nombre="C2", d_host="127.0.0.1", d_puerto=d.puerto)
        self.addCleanup(c2.detener)
        c2.iniciar()

        # D debe tener 2 nodos registrados
        self.assertTrue(self._esperar(lambda: d.estado()["cantidad_nodos_c_registrados"] == 2))

        # C2 debe haber recibido a C1 como par y haberle enviado saludo
        self.assertTrue(self._esperar(lambda: c2.estado()["saludos_enviados"] >= 1))
        self.assertTrue(self._esperar(lambda: c1.estado()["saludos_recibidos"] >= 1))


if __name__ == "__main__":
    unittest.main()
