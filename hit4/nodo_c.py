"""Hit #4 — Nodo C: un único programa que es cliente y servidor a la vez.

Refactor de A y B en un solo ejecutable. Cada instancia recibe por parámetros la
dirección donde escucha saludos y la dirección de otro nodo C. Con dos instancias
configuradas cada una con los datos de la otra, ambas se saludan mutuamente: cada
una abre un canal saliente (como cliente) y atiende el canal entrante del par
(como servidor).

  C1 --saludo--> C2      (C1 cliente, C2 servidor)
  C1 <--saludo-- C2      (C2 cliente, C1 servidor)

Son dos conexiones TCP independientes, una por sentido.
"""

import argparse
import socket
import sys
import threading
import time

from comun import config
from comun.health import iniciar_health
from comun.protocolo import (
    ConexionCerrada,
    LectorDeMensajes,
    MensajeDemasiadoLargo,
    enviar_mensaje,
)
from comun.registro import configurar

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT4", 9004)
PUERTO_HEALTH_POR_DEFECTO = config.entero("TP1_PUERTO_HEALTH", 8080)
ESPERA_INICIAL = config.decimal("TP1_ESPERA_INICIAL", 0.5)
ESPERA_MAXIMA = config.decimal("TP1_ESPERA_MAXIMA", 5.0)
TIMEOUT_CONEXION = config.decimal("TP1_TIMEOUT", 5.0)
CONEXIONES_EN_ESPERA = 16


class NodoC:
    """Nodo que atiende saludos entrantes y saluda a un par simultáneamente."""

    def __init__(self, host, puerto, logger, nombre=None, backlog=CONEXIONES_EN_ESPERA):
        self._logger = logger
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, puerto))
        self._socket.listen(backlog)
        self.host, self.puerto = self._socket.getsockname()
        self.nombre = nombre or f"C[{self.host}:{self.puerto}]"
        self.par = None

        self._detenido = threading.Event()
        self._lock = threading.Lock()
        self._inicio = time.monotonic()
        self._hilos = []
        self._conexiones = set()
        self._socket_saliente = None
        self._canal_saliente = "sin_par"
        self._saludos_recibidos = 0
        self._saludos_enviados = 0
        self._respuestas_recibidas = 0
        self._conexiones_activas = 0
        self._conexiones_atendidas = 0

    # ------------------------------------------------------------------ estado

    def configurar_par(self, host, puerto):
        """Fija el nodo C al que este nodo va a saludar."""
        self.par = (host, int(puerto))

    def estado(self):
        with self._lock:
            return {
                "servicio": "hit4-nodo-c",
                "nombre": self.nombre,
                "estado": "detenido" if self._detenido.is_set() else "ok",
                "uptime_segundos": round(time.monotonic() - self._inicio, 3),
                "escuchando_en": f"{self.host}:{self.puerto}",
                "par": f"{self.par[0]}:{self.par[1]}" if self.par else None,
                "canal_saliente": self._canal_saliente,
                "conexiones_activas": self._conexiones_activas,
                "conexiones_atendidas": self._conexiones_atendidas,
                "saludos_recibidos": self._saludos_recibidos,
                "saludos_enviados": self._saludos_enviados,
                "respuestas_recibidas": self._respuestas_recibidas,
            }

    def construir_respuesta(self, saludo):
        return f"Hola, soy {self.nombre}. Recibi tu saludo: {saludo}"

    def construir_saludo(self):
        return f"Hola, soy {self.nombre}"

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
                    saludo = lector.leer_mensaje()
                    with self._lock:
                        self._saludos_recibidos += 1
                    self._logger.info("[entrante] saludo de %s:%s: %s", *direccion, saludo)
                    enviar_mensaje(conexion, self.construir_respuesta(saludo))
        except ConexionCerrada:
            self._logger.info("[entrante] %s:%s cerro la conexion", *direccion)
        except (MensajeDemasiadoLargo, OSError) as error:
            self._logger.warning("[entrante] error con %s:%s: %s", *direccion, error)
        finally:
            with self._lock:
                self._conexiones.discard(conexion)
                self._conexiones_activas -= 1

    def _aceptar_conexiones(self):
        self._logger.info("%s escuchando en %s:%s", self.nombre, self.host, self.puerto)
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

    def _saludar_al_par(self, espera_inicial, espera_maxima, timeout, dormir):
        host, puerto = self.par
        espera = espera_inicial

        while not self._detenido.is_set():
            try:
                with socket.create_connection((host, puerto), timeout=timeout) as sock:
                    self._logger.info("[saliente] conectado al par %s:%s", host, puerto)
                    espera = espera_inicial
                    with self._lock:
                        self._canal_saliente = "conectado"
                        self._socket_saliente = sock

                    lector = LectorDeMensajes(sock)
                    saludo = self.construir_saludo()
                    enviar_mensaje(sock, saludo)
                    with self._lock:
                        self._saludos_enviados += 1
                    self._logger.info("[saliente] saludo enviado: %s", saludo)

                    respuesta = lector.leer_mensaje()
                    with self._lock:
                        self._respuestas_recibidas += 1
                    self._logger.info("[saliente] respuesta del par: %s", respuesta)

                    # Igual que en el Hit #2: la vigilancia del canal no lleva
                    # timeout, para no reconectar mientras el par sigue vivo.
                    sock.settimeout(None)
                    lector.leer_mensaje()
            except (ConexionCerrada, OSError) as error:
                if self._detenido.is_set():
                    break
                with self._lock:
                    self._canal_saliente = "reintentando"
                    self._socket_saliente = None
                self._logger.warning(
                    "[saliente] canal con %s:%s interrumpido (%s). Reintento en %.1fs",
                    host, puerto, error, espera,
                )
                dormir(espera)
                espera = min(espera * 2, espera_maxima)

        with self._lock:
            self._canal_saliente = "detenido"

    # --------------------------------------------------------------- ciclo vida

    def iniciar(
        self,
        espera_inicial=ESPERA_INICIAL,
        espera_maxima=ESPERA_MAXIMA,
        timeout=TIMEOUT_CONEXION,
        dormir=time.sleep,
    ):
        servidor = threading.Thread(target=self._aceptar_conexiones, daemon=True)
        servidor.start()
        self._hilos.append(servidor)

        if self.par:
            cliente = threading.Thread(
                target=self._saludar_al_par,
                args=(espera_inicial, espera_maxima, timeout, dormir),
                daemon=True,
            )
            cliente.start()
            self._hilos.append(cliente)
        else:
            self._logger.warning("%s no tiene par configurado: solo escucha", self.nombre)

    def detener(self):
        """Cierra el socket de escucha y corta los canales abiertos."""
        self._detenido.set()
        self._socket.close()

        with self._lock:
            abiertos = list(self._conexiones)
            if self._socket_saliente is not None:
                abiertos.append(self._socket_saliente)

        # `shutdown` desbloquea a los hilos parados en un recv; sin él seguirían
        # esperando datos de un canal que ya nadie va a atender.
        for sock in abiertos:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def esperar(self, duracion=None):
        """Bloquea hasta que se detenga el nodo o venza `duracion` segundos."""
        self._detenido.wait(timeout=duracion)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hit #4 - Nodo C (cliente y servidor simultaneos)"
    )
    parser.add_argument("--host", default=HOST_POR_DEFECTO, help="IP donde escuchar")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO,
                        help="puerto donde escuchar saludos")
    parser.add_argument("--par-host", required=True, help="IP del otro nodo C")
    parser.add_argument("--par-puerto", type=int, required=True,
                        help="puerto del otro nodo C")
    parser.add_argument("--nombre", default=None, help="identificador del nodo")
    parser.add_argument("--puerto-health", type=int, default=PUERTO_HEALTH_POR_DEFECTO)
    parser.add_argument("--sin-health", action="store_true")
    parser.add_argument("--duracion", type=float, default=None,
                        help="segundos a ejecutar antes de salir (por defecto, indefinido)")
    args = parser.parse_args(argv)

    logger, _ = configurar("hit4.nodo_c")
    nodo = NodoC(args.host, args.puerto, logger, args.nombre)
    nodo.configurar_par(args.par_host, args.par_puerto)

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
