"""Hit #8 — Nodo C con comunicación gRPC / Protocol Buffers.

Reemplaza la comunicación por sockets TCP y serialización JSON manual (Hit #5)
por llamadas remotas gRPC (MensajeriaC) y mensajes estructurados definidos en Protobuf.
Admite tanto topología par a par (estilo Hit #5) como descubrimiento dinámico
a través del Nodo D (RegistroD).

  C1 -- gRPC Saludar(MensajeSaludo) --> C2  (MensajeRespuesta)
  C1 <-- gRPC Saludar(MensajeSaludo) -- C2  (MensajeRespuesta)
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
PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT8", 9008)
D_HOST_POR_DEFECTO = config.texto("TP1_D_HOST", "127.0.0.1")
D_PUERTO_POR_DEFECTO = config.entero("TP1_D_PUERTO_HIT8", 9608)
PUERTO_HEALTH_POR_DEFECTO = config.entero("TP1_PUERTO_HEALTH", 8080)
ESPERA_INICIAL = config.decimal("TP1_ESPERA_INICIAL", 0.5)
ESPERA_MAXIMA = config.decimal("TP1_ESPERA_MAXIMA", 5.0)
TIMEOUT_RPC = config.decimal("TP1_TIMEOUT", 5.0)
VERSION_PROTOCOLO = 1


def _ahora():
    return datetime.now(timezone.utc).isoformat()


class MensajeriaCServicer(servicio_pb2_grpc.MensajeriaCServicer):
    """Implementación del servicio gRPC MensajeriaC del lado servidor."""

    def __init__(self, nodo):
        self._nodo = nodo

    def Saludar(self, request, context):
        return self._nodo._atender_saludo_grpc(request, context)


class NodoC:
    """Nodo C con servidor y cliente gRPC."""

    def __init__(
        self,
        host,
        puerto,
        logger,
        nombre=None,
        d_host=None,
        d_puerto=None,
        max_workers=10,
    ):
        self._logger = logger
        self.host = host
        self.puerto_solicitado = puerto
        self.d_host = d_host
        self.d_puerto = int(d_puerto) if d_puerto else None

        self._detenido = threading.Event()
        self._lock = threading.Lock()
        self._inicio = time.monotonic()
        self._hilos = []
        self.par = None
        self._pares_registrados = []

        self._canal_saliente = "sin_par"
        self._saludos_recibidos = 0
        self._saludos_enviados = 0
        self._respuestas_recibidas = 0
        self._mensajes_invalidos = 0
        self._mensajes_ignorados = 0

        # Servidor gRPC
        self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
        servicio_pb2_grpc.add_MensajeriaCServicer_to_server(
            MensajeriaCServicer(self), self._server
        )
        self.puerto = self._server.add_insecure_port(f"{self.host}:{self.puerto_solicitado}")
        self.nombre = nombre or f"C[{self.host}:{self.puerto}]"

    # ------------------------------------------------------------------ estado

    def configurar_par(self, host, puerto):
        """Fija el nodo C al que este nodo va a saludar de forma continua (estilo Hit #5)."""
        self.par = (host, int(puerto))

    def estado(self):
        with self._lock:
            return {
                "servicio": "hit8-nodo-c",
                "formato_mensajes": "protobuf-grpc",
                "nombre": self.nombre,
                "estado": "detenido" if self._detenido.is_set() else "ok",
                "uptime_segundos": round(time.monotonic() - self._inicio, 3),
                "escuchando_en": f"{self.host}:{self.puerto}",
                "par": f"{self.par[0]}:{self.par[1]}" if self.par else None,
                "canal_saliente": self._canal_saliente,
                "saludos_recibidos": self._saludos_recibidos,
                "saludos_enviados": self._saludos_enviados,
                "respuestas_recibidas": self._respuestas_recibidas,
                "mensajes_invalidos": self._mensajes_invalidos,
                "mensajes_ignorados": self._mensajes_ignorados,
                "pares_descubiertos": list(self._pares_registrados),
            }

    # ----------------------------------------------------------- lado servidor

    def _atender_saludo_grpc(self, request, context):
        """Atiende la llamada RPC Saludar."""
        if not request.origen or not request.contenido:
            with self._lock:
                self._mensajes_invalidos += 1
            self._logger.warning(
                "[entrante] RPC Saludar invalido de %s: faltan campos requeridos",
                context.peer(),
            )
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Faltan campos requeridos: origen o contenido")
            return servicio_pb2.MensajeRespuesta()

        if request.tipo and request.tipo != "saludo":
            with self._lock:
                self._mensajes_ignorados += 1
            self._logger.info(
                "[entrante] mensaje de tipo '%s' de %s ignorado (se esperaba 'saludo')",
                request.tipo,
                request.origen,
            )
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"Tipo '{request.tipo}' no soportado en este endpoint")
            return servicio_pb2.MensajeRespuesta()

        with self._lock:
            self._saludos_recibidos += 1

        self._logger.info(
            "[entrante] saludo de %s (id=%s): %s",
            request.origen,
            request.id,
            request.contenido,
        )

        # Retornar respuesta estructurada Protobuf
        return servicio_pb2.MensajeRespuesta(
            version=VERSION_PROTOCOLO,
            id=str(uuid.uuid4()),
            tipo="respuesta",
            origen=self.nombre,
            contenido=f"Hola {request.origen}, soy {self.nombre}. Recibi tu saludo.",
            en_respuesta_a=request.id,
            timestamp=_ahora(),
        )

    # ------------------------------------------------------------ lado cliente

    def enviar_saludo_rpc(self, host, puerto, timeout=TIMEOUT_RPC):
        """Envía un saludo único vía gRPC al nodo indicado y retorna la respuesta."""
        saludo = servicio_pb2.MensajeSaludo(
            version=VERSION_PROTOCOLO,
            id=str(uuid.uuid4()),
            tipo="saludo",
            origen=self.nombre,
            contenido=f"Hola, soy {self.nombre}",
            timestamp=_ahora(),
        )
        tamano_bytes = saludo.ByteSize()

        with grpc.insecure_channel(f"{host}:{puerto}") as channel:
            stub = servicio_pb2_grpc.MensajeriaCStub(channel)
            with self._lock:
                self._saludos_enviados += 1
            self._logger.info(
                "[saliente] saludo gRPC enviado a %s:%s (id=%s, %s bytes): %s",
                host,
                puerto,
                saludo.id,
                tamano_bytes,
                saludo.contenido,
            )

            respuesta = stub.Saludar(saludo, timeout=timeout)
            with self._lock:
                self._respuestas_recibidas += 1
            self._logger.info(
                "[saliente] respuesta de %s (id=%s, en_respuesta_a=%s, %s bytes): %s",
                respuesta.origen,
                respuesta.id,
                respuesta.en_respuesta_a,
                respuesta.ByteSize(),
                respuesta.contenido,
            )
            return respuesta

    def _saludar_al_par_continuo(self, espera_inicial, espera_maxima, timeout):
        """Bucle de saludo y supervisión continuo para topología de par fijo (Hit #5)."""
        host, puerto = self.par
        espera = espera_inicial

        while not self._detenido.is_set():
            try:
                self.enviar_saludo_rpc(host, puerto, timeout=timeout)
                with self._lock:
                    self._canal_saliente = "conectado"
                espera = espera_inicial
            except grpc.RpcError as error:
                if self._detenido.is_set():
                    break
                with self._lock:
                    self._canal_saliente = "reintentando"
                self._logger.warning(
                    "[saliente] llamada gRPC a %s:%s fallo (%s: %s). Reintento en %.1fs",
                    host,
                    puerto,
                    error.code(),
                    error.details(),
                    espera,
                )
            except Exception:
                if self._detenido.is_set():
                    break
                with self._lock:
                    self._canal_saliente = "degradado"
                self._logger.exception(
                    "[saliente] error inesperado con %s:%s. Reintento en %.1fs",
                    host,
                    puerto,
                    espera,
                )

            # Espera antes del siguiente saludo o reintento
            self._detenido.wait(espera)
            espera = min(espera * 2, espera_maxima)

        with self._lock:
            self._canal_saliente = "detenido"

    # ------------------------------------------------- registro dinámico con D

    def registrar_en_nodo_d(self, timeout=TIMEOUT_RPC):
        """Se comunica vía gRPC con el Nodo D para registrarse y obtener la lista de pares."""
        self._logger.info(
            "[registro-d] registrandose en D (%s:%s) con IP=%s, puerto=%s vía gRPC...",
            self.d_host,
            self.d_puerto,
            self.host,
            self.puerto,
        )
        with grpc.insecure_channel(f"{self.d_host}:{self.d_puerto}") as channel:
            stub = servicio_pb2_grpc.RegistroDStub(channel)
            solicitud = servicio_pb2.RegistroSolicitud(
                version=VERSION_PROTOCOLO,
                id=str(uuid.uuid4()),
                tipo="registro",
                origen=self.nombre,
                contenido=f"Registro gRPC de {self.nombre} en {self.host}:{self.puerto}",
                ip=self.host,
                puerto=self.puerto,
                timestamp=_ahora(),
            )
            respuesta = stub.Registrar(solicitud, timeout=timeout)
            nodos = [
                {"ip": n.ip, "puerto": n.puerto, "nombre": n.nombre}
                for n in respuesta.nodos
            ]
            with self._lock:
                self._pares_registrados = list(nodos)
            self._logger.info(
                "[registro-d] registrado exitosamente vía gRPC. Pares recibidos: %d (%s)",
                len(nodos),
                nodos,
            )
            return nodos

    def saludar_a_pares(self, pares):
        """Envía saludos gRPC a cada par descubierto a través de D."""
        for par in pares:
            p_host = par.get("ip")
            p_puerto = par.get("puerto")
            if p_host and p_puerto:
                try:
                    self.enviar_saludo_rpc(p_host, p_puerto)
                except Exception as e:
                    self._logger.warning(
                        "[saliente] error al saludar al par descubierto %s:%s: %s",
                        p_host,
                        p_puerto,
                        e,
                    )

    # --------------------------------------------------------------- ciclo vida

    def _registrar_hilo(self, hilo):
        with self._lock:
            self._hilos = [h for h in self._hilos if h.is_alive()]
            self._hilos.append(hilo)

    def iniciar(
        self,
        espera_inicial=ESPERA_INICIAL,
        espera_maxima=ESPERA_MAXIMA,
        timeout=TIMEOUT_RPC,
    ):
        # 1. Iniciar servidor gRPC
        self._server.start()
        self._logger.info(
            "%s servidor gRPC escuchando en %s:%s",
            self.nombre,
            self.host,
            self.puerto,
        )

        # 2. Si tiene par fijo configurado (estilo Hit #5), inicia el hilo de saludo continuo
        if self.par:
            cliente = threading.Thread(
                target=self._saludar_al_par_continuo,
                args=(espera_inicial, espera_maxima, timeout),
                daemon=True,
            )
            cliente.start()
            self._registrar_hilo(cliente)

        # 3. Si tiene nodo D configurado (estilo Hit #6), se registra dinámicamente y saluda
        elif self.d_host and self.d_puerto:
            try:
                pares = self.registrar_en_nodo_d(timeout=timeout)
            except Exception as error:
                self._logger.error("[registro-d] fallo la registracion en D: %s", error)
                pares = []
            self.saludar_a_pares(pares)
        else:
            self._logger.warning(
                "%s sin par ni nodo D configurado: solo escucha RPCs", self.nombre
            )

    def detener(self, grace=0.5):
        self._detenido.set()
        self._server.stop(grace=grace)

    def esperar(self, duracion=None):
        self._detenido.wait(timeout=duracion)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hit #8 - Nodo C con gRPC y Protocol Buffers"
    )
    parser.add_argument("--host", default=HOST_POR_DEFECTO, help="IP donde escuchar")
    parser.add_argument(
        "--puerto",
        type=int,
        default=PUERTO_POR_DEFECTO,
        help="puerto gRPC donde escuchar saludos (0 para puerto aleatorio)",
    )
    parser.add_argument("--par-host", default=None, help="IP del otro nodo C")
    parser.add_argument(
        "--par-puerto", type=int, default=None, help="puerto gRPC del otro nodo C"
    )
    parser.add_argument(
        "--d-host",
        "--host-d",
        default=None,
        help="IP del programa D (registro de contactos gRPC)",
    )
    parser.add_argument(
        "--d-puerto",
        "--puerto-d",
        type=int,
        default=None,
        help="puerto gRPC del programa D",
    )
    parser.add_argument("--nombre", default=None, help="identificador del nodo")
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

    logger, _ = configurar("hit8.nodo_c")
    nodo = NodoC(
        host=args.host,
        puerto=args.puerto,
        logger=logger,
        nombre=args.nombre,
        d_host=args.d_host,
        d_puerto=args.d_puerto,
    )

    if args.par_host and args.par_puerto:
        nodo.configurar_par(args.par_host, args.par_puerto)

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
