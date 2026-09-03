"""Hit #8 — Nodo D: Registro de contactos con gRPC / Protocol Buffers.

Implementa el servicio RegistroD en gRPC con Protobuf. Mantiene la lista de
nodos C registrados en RAM y atiende llamadas RPC Registrar y ConsultarActivos.
"""

import argparse
from concurrent import futures
from datetime import datetime, timezone
import sys
import threading
import time
import uuid

import grpc

from comun import config
from comun.health import iniciar_health_opcional
from comun.registro import configurar
from hit8.proto import servicio_pb2, servicio_pb2_grpc

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
PUERTO_POR_DEFECTO = config.entero("TP1_D_PUERTO_HIT8", 9608)
PUERTO_HEALTH_POR_DEFECTO = config.entero("TP1_PUERTO_HEALTH_D", 8086)
VERSION_PROTOCOLO = 1


def _ahora():
    return datetime.now(timezone.utc).isoformat()


class RegistroDServicer(servicio_pb2_grpc.RegistroDServicer):
    """Implementación del servicer gRPC para el nodo D."""

    def __init__(self, nodo):
        self._nodo = nodo

    def Registrar(self, request, context):
        return self._nodo._atender_registrar_grpc(request, context)

    def ConsultarActivos(self, request, context):
        return self._nodo._atender_consultar_grpc(request, context)


class NodoD:
    """Nodo D: Directorio y registro centralizado gRPC."""

    def __init__(
        self,
        host=HOST_POR_DEFECTO,
        puerto=PUERTO_POR_DEFECTO,
        logger=None,
        nombre="NodoD",
        max_workers=10,
    ):
        self._logger = logger
        self.host = host
        self.puerto_solicitado = puerto
        self.nombre = nombre

        self._nodos_registrados = []
        self._detenido = threading.Event()
        self._lock = threading.Lock()
        self._inicio = time.monotonic()
        self._registros_atendidos = 0
        self._consultas_atendidas = 0
        self._mensajes_invalidos = 0

        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
        servicio_pb2_grpc.add_RegistroDServicer_to_server(
            RegistroDServicer(self), self._server
        )
        self.puerto = self._server.add_insecure_port(f"{self.host}:{self.puerto_solicitado}")

    def estado(self):
        with self._lock:
            cantidad = len(self._nodos_registrados)
            nodos_copia = [dict(n) for n in self._nodos_registrados]
            uptime = round(time.monotonic() - self._inicio, 3)
            estado_str = "detenido" if self._detenido.is_set() else "ok"
            return {
                "servicio": "hit8-nodo-d",
                "formato_mensajes": "protobuf-grpc",
                "nombre": self.nombre,
                "estado": estado_str,
                "estado_general": estado_str,
                "uptime": uptime,
                "uptime_segundos": uptime,
                "escuchando_en": f"{self.host}:{self.puerto}",
                "cantidad_nodos_c_registrados": cantidad,
                "nodos_c_registrados": cantidad,
                "nodos": nodos_copia,
                "registros_atendidos": self._registros_atendidos,
                "consultas_atendidas": self._consultas_atendidas,
                "mensajes_invalidos": self._mensajes_invalidos,
            }

    def _atender_registrar_grpc(self, request, context):
        if not request.ip or not request.puerto:
            with self._lock:
                self._mensajes_invalidos += 1
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Faltan IP o puerto del nodo C")
            return servicio_pb2.RegistroRespuesta()

        c_ip = request.ip
        c_puerto = request.puerto
        c_origen = request.origen or f"C[{c_ip}:{c_puerto}]"

        with self._lock:
            self._registros_atendidos += 1
            # Otros nodos ya registrados (excluyendo al que se registra)
            otros = [
                servicio_pb2.InfoNodo(
                    ip=n["ip"], puerto=n["puerto"], nombre=n.get("nombre", "")
                )
                for n in self._nodos_registrados
                if not (n["ip"] == c_ip and n["puerto"] == c_puerto)
            ]

            # Agregar a la lista si no existía
            if not any(n["ip"] == c_ip and n["puerto"] == c_puerto for n in self._nodos_registrados):
                self._nodos_registrados.append(
                    {"ip": c_ip, "puerto": c_puerto, "nombre": c_origen}
                )

        if self._logger:
            self._logger.info(
                "[registro] nodo %s registrado en %s:%s (total registrados: %d)",
                c_origen,
                c_ip,
                c_puerto,
                len(self._nodos_registrados),
            )

        return servicio_pb2.RegistroRespuesta(
            version=VERSION_PROTOCOLO,
            id=str(uuid.uuid4()),
            tipo="registro_respuesta",
            origen=self.nombre,
            contenido=f"Registro exitoso de {c_origen}",
            en_respuesta_a=request.id,
            nodos=otros,
            timestamp=_ahora(),
        )

    def _atender_consultar_grpc(self, request, context):
        with self._lock:
            self._consultas_atendidas += 1
            activos = [
                servicio_pb2.InfoNodo(
                    ip=n["ip"], puerto=n["puerto"], nombre=n.get("nombre", "")
                )
                for n in self._nodos_registrados
            ]

        return servicio_pb2.ConsultaActivosRespuesta(
            version=VERSION_PROTOCOLO,
            id=str(uuid.uuid4()),
            tipo="consulta_activos_respuesta",
            origen=self.nombre,
            contenido="Listado de nodos activos",
            en_respuesta_a=request.id,
            nodos_activos=activos,
            timestamp=_ahora(),
        )

    def iniciar(self):
        self._server.start()
        if self._logger:
            self._logger.info(
                "%s servidor gRPC escuchando en %s:%s",
                self.nombre,
                self.host,
                self.puerto,
            )

    def detener(self, grace=0.5):
        self._detenido.set()
        self._server.stop(grace=grace)

    def esperar(self, duracion=None):
        self._detenido.wait(timeout=duracion)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hit #8 - Nodo D con gRPC y Protocol Buffers"
    )
    parser.add_argument("--host", default=HOST_POR_DEFECTO, help="IP donde escuchar")
    parser.add_argument(
        "--puerto",
        type=int,
        default=PUERTO_POR_DEFECTO,
        help="puerto gRPC donde escuchar registros",
    )
    parser.add_argument("--nombre", default="NodoD", help="identificador del registro D")
    parser.add_argument(
        "--puerto-health", type=int, default=PUERTO_HEALTH_POR_DEFECTO
    )
    parser.add_argument("--sin-health", action="store_true")
    parser.add_argument(
        "--duracion",
        type=float,
        default=None,
        help="segundos a ejecutar antes de salir",
    )
    args = parser.parse_args(argv)

    logger, _ = configurar("hit8.nodo_d")
    nodo = NodoD(
        host=args.host,
        puerto=args.puerto,
        logger=logger,
        nombre=args.nombre,
    )

    if not args.sin_health:
        iniciar_health_opcional(args.puerto_health, nodo.estado, args.host, logger)

    nodo.iniciar()
    try:
        nodo.esperar(args.duracion)
    except KeyboardInterrupt:
        logger.info("%s finalizado por el usuario", nodo.nombre)
    finally:
        nodo.detener()
    return 0


if __name__ == "__main__":
    sys.exit(main())
