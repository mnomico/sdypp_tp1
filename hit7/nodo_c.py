"""Hit #7 — Nodo C para sistema de inscripciones por ventanas.

Node C se registra en el Nodo D para participar en la SIGUIENTE ventana de tiempo.
Al registrarse o consultar activos, recibe únicamente los nodos C activos de la VENTANA ACTUAL.
Saluda a los nodos de la ventana actual y escucha en su puerto aleatorio los saludos entrantes.
"""

import argparse
import socket
import sys
import threading
import time

from comun import config, mensajes
from comun.health import iniciar_health
from comun.protocolo import (
    ConexionCerrada,
    LectorDeMensajes,
    MensajeDemasiadoLargo,
    enviar_mensaje,
)
from comun.registro import configurar

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
D_HOST_POR_DEFECTO = config.texto("TP1_D_HOST", "127.0.0.1")
D_PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT7", 9700)
PUERTO_HEALTH_POR_DEFECTO = config.entero("TP1_PUERTO_HEALTH", 8080)
TIMEOUT_CONEXION = config.decimal("TP1_TIMEOUT", 5.0)
CONEXIONES_EN_ESPERA = 16


class NodoC:
    """Nodo C para Hit 7: Se inscribe para la siguiente ventana y saluda a los activos de la actual."""

    def __init__(
        self,
        d_host,
        d_puerto,
        logger,
        host=HOST_POR_DEFECTO,
        nombre=None,
        intervalo_consulta=None,
        backlog=CONEXIONES_EN_ESPERA,
    ):
        self.d_host = d_host
        self.d_puerto = int(d_puerto)
        self._logger = logger
        self.intervalo_consulta = intervalo_consulta

        # Socket de escucha en puerto 0 (puerto aleatorio asignado por el SO)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, 0))
        self._socket.listen(backlog)

        self.host, self.puerto = self._socket.getsockname()
        self.nombre = nombre or f"C[{self.host}:{self.puerto}]"

        self._detenido = threading.Event()
        self._lock = threading.Lock()
        self._inicio = time.monotonic()
        self._hilos = []
        self._conexiones = set()
        self._activos_conectados = set()
        self._saludos_recibidos = 0
        self._saludos_enviados = 0
        self._respuestas_recibidas = 0
        self._conexiones_activas = 0
        self._conexiones_atendidas = 0
        self._mensajes_invalidos = 0

    # ------------------------------------------------------------------ estado

    def estado(self):
        with self._lock:
            return {
                "servicio": "hit7-nodo-c",
                "formato_mensajes": "json",
                "nombre": self.nombre,
                "estado": "detenido" if self._detenido.is_set() else "ok",
                "uptime_segundos": round(time.monotonic() - self._inicio, 3),
                "escuchando_en": f"{self.host}:{self.puerto}",
                "nodo_d": f"{self.d_host}:{self.d_puerto}",
                "saludos_recibidos": self._saludos_recibidos,
                "saludos_enviados": self._saludos_enviados,
                "respuestas_recibidas": self._respuestas_recibidas,
                "conexiones_activas": self._conexiones_activas,
                "conexiones_atendidas": self._conexiones_atendidas,
                "mensajes_invalidos": self._mensajes_invalidos,
            }

    def construir_respuesta(self, saludo):
        return mensajes.crear_respuesta(self.nombre, saludo)

    def construir_saludo(self):
        return mensajes.crear_saludo(self.nombre)

    # -------------------------------------------------------- comunicacion con D

    def registrar_en_nodo_d(self, timeout=TIMEOUT_CONEXION):
        """Inscribe este nodo C en D para la SIGUIENTE ventana y obtiene los ACTIVOS de la actual."""
        self._logger.info(
            "[inscripcion] inscribiendose en D (%s:%s) para la proxima ventana...",
            self.d_host, self.d_puerto
        )
        with socket.create_connection((self.d_host, self.d_puerto), timeout=timeout) as sock:
            registro_msg = mensajes.crear_registro(self.nombre, self.host, self.puerto)
            enviar_mensaje(sock, mensajes.serializar(registro_msg))

            lector = LectorDeMensajes(sock)
            crudo_resp = lector.leer_mensaje()
            respuesta = mensajes.deserializar(crudo_resp)

            if respuesta.get("tipo") != mensajes.TIPO_REGISTRO_RESPUESTA:
                raise RuntimeError(f"Respuesta inesperada de D: {respuesta}")

            nodos_activos = respuesta.get("nodos", [])
            self._logger.info(
                "[inscripcion] registrado para proxima ventana. Nodos activos ventana actual: %d (%s)",
                len(nodos_activos), nodos_activos
            )
            return nodos_activos

    def consultar_activos_en_d(self, timeout=TIMEOUT_CONEXION):
        """Consulta en D los nodos C activos en la ventana actual."""
        with socket.create_connection((self.d_host, self.d_puerto), timeout=timeout) as sock:
            consulta_msg = mensajes.crear_consulta_activos(self.nombre)
            enviar_mensaje(sock, mensajes.serializar(consulta_msg))

            lector = LectorDeMensajes(sock)
            respuesta = mensajes.deserializar(lector.leer_mensaje())
            return respuesta.get("nodos_activos", [])

    # ----------------------------------------------------------- lado servidor

    def _atender(self, conexion, direccion):
        with self._lock:
            self._conexiones.add(conexion)
            self._conexiones_activas += 1
            self._conexiones_atendidas += 1
        try:
            with conexion:
                lector = LectorDeMensajes(conexion)
                while not self._detenido.is_set():
                    crudo = lector.leer_mensaje()
                    try:
                        saludo = mensajes.deserializar(crudo)
                    except mensajes.MensajeInvalido as error:
                        with self._lock:
                            self._mensajes_invalidos += 1
                        self._logger.warning(
                            "[entrante] mensaje invalido de %s:%s: %s", *direccion, error
                        )
                        continue

                    with self._lock:
                        self._saludos_recibidos += 1
                    self._logger.info(
                        "[entrante] %s de %s: %s",
                        saludo.get("tipo"), saludo.get("origen"), saludo.get("contenido"),
                    )
                    respuesta = self.construir_respuesta(saludo)
                    enviar_mensaje(conexion, mensajes.serializar(respuesta))
        except ConexionCerrada:
            self._logger.info("[entrante] %s:%s cerro la conexion", *direccion)
        except (MensajeDemasiadoLargo, OSError) as error:
            self._logger.warning("[entrante] error con %s:%s: %s", *direccion, error)
        finally:
            with self._lock:
                self._conexiones.discard(conexion)
                self._conexiones_activas -= 1

    def _aceptar_conexiones(self):
        self._logger.info("%s escuchando en %s:%s (puerto aleatorio)", self.nombre, self.host, self.puerto)
        while not self._detenido.is_set():
            try:
                conexion, direccion = self._socket.accept()
            except OSError:
                if self._detenido.is_set():
                    break
                self._logger.exception("[entrante] fallo el accept; se sigue escuchando")
                continue
            hilo = threading.Thread(
                target=self._atender, args=(conexion, direccion), daemon=True
            )
            hilo.start()
            self._hilos.append(hilo)

    # ------------------------------------------------------------ lado cliente

    def _saludar_a_par(self, host, puerto, timeout=TIMEOUT_CONEXION):
        key = (host, int(puerto))
        with self._lock:
            if key in self._activos_conectados:
                return
            self._activos_conectados.add(key)

        self._logger.info("[saliente] saludando a par activo %s:%s", host, puerto)
        try:
            with socket.create_connection((host, int(puerto)), timeout=timeout) as sock:
                lector = LectorDeMensajes(sock)
                saludo = self.construir_saludo()
                enviar_mensaje(sock, mensajes.serializar(saludo))
                with self._lock:
                    self._saludos_enviados += 1
                self._logger.info(
                    "[saliente] saludo enviado a %s:%s (id=%s): %s",
                    host, puerto, saludo["id"], saludo["contenido"],
                )

                respuesta = mensajes.deserializar(lector.leer_mensaje())
                with self._lock:
                    self._respuestas_recibidas += 1
                self._logger.info(
                    "[saliente] respuesta de %s (%s:%s): %s",
                    respuesta.get("origen"), host, puerto, respuesta.get("contenido"),
                )
        except (ConexionCerrada, OSError, mensajes.MensajeInvalido) as error:
            self._logger.warning(
                "[saliente] error al saludar a par activo %s:%s: %s", host, puerto, error
            )

    def saludar_nodos_activos(self, nodos_activos):
        for nodo in nodos_activos:
            h = nodo.get("ip")
            p = nodo.get("puerto")
            if h and p and not (h == self.host and int(p) == self.puerto):
                hilo = threading.Thread(
                    target=self._saludar_a_par, args=(h, p), daemon=True
                )
                hilo.start()
                self._hilos.append(hilo)

    def _loop_consulta_periodica(self):
        while not self._detenido.is_set():
            if self._detenido.wait(timeout=self.intervalo_consulta):
                break
            try:
                activos = self.consultar_activos_en_d()
                self.saludar_nodos_activos(activos)
            except Exception as error:
                self._logger.warning("[consulta-activos] error al consultar activos en D: %s", error)

    # --------------------------------------------------------------- ciclo vida

    def iniciar(self, timeout=TIMEOUT_CONEXION):
        # 1. Servidor de escucha
        servidor = threading.Thread(target=self._aceptar_conexiones, daemon=True)
        servidor.start()
        self._hilos.append(servidor)

        # 2. Inscripción en D (para la ventana siguiente) y obtención de activos actuales
        try:
            activos = self.registrar_en_nodo_d(timeout=timeout)
        except Exception as error:
            self._logger.error("[inscripcion] fallo al inscribirse en D: %s", error)
            activos = []

        # 3. Saludar a los nodos de la ventana actual
        self.saludar_nodos_activos(activos)

        # 4. Hilo de consulta periódica opcional
        if self.intervalo_consulta:
            consultador = threading.Thread(target=self._loop_consulta_periodica, daemon=True)
            consultador.start()
            self._hilos.append(consultador)

    def detener(self):
        self._detenido.set()
        self._socket.close()

        with self._lock:
            abiertos = list(self._conexiones)

        for sock in abiertos:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def esperar(self, duracion=None):
        self._detenido.wait(timeout=duracion)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hit #7 - Nodo C con sistema de inscripciones por ventanas"
    )
    parser.add_argument("--d-host", "--host-d", default=D_HOST_POR_DEFECTO,
                        help="IP del programa D (registro/coordinador de ventanas)")
    parser.add_argument("--d-puerto", "--puerto-d", type=int, default=D_PUERTO_POR_DEFECTO,
                        help="puerto TCP del programa D")
    parser.add_argument("--host", default=HOST_POR_DEFECTO, help="IP de escucha local del nodo C")
    parser.add_argument("--nombre", default=None, help="identificador del nodo C")
    parser.add_argument("--intervalo-consulta", type=float, default=None,
                        help="segundos para reconsultar activos en D periódicamente")
    parser.add_argument("--puerto-health", type=int, default=PUERTO_HEALTH_POR_DEFECTO)
    parser.add_argument("--sin-health", action="store_true")
    parser.add_argument("--duracion", type=float, default=None,
                        help="segundos a ejecutar antes de salir")
    args = parser.parse_args(argv)

    logger, _ = configurar("hit7.nodo_c")
    nodo = NodoC(
        d_host=args.d_host,
        d_puerto=args.d_puerto,
        logger=logger,
        host=args.host,
        nombre=args.nombre,
        intervalo_consulta=args.intervalo_consulta,
    )

    if not args.sin_health:
        iniciar_health(args.puerto_health, nodo.estado)
        logger.info("Health disponible en http://%s:%s/health", args.host, args.puerto_health)

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
