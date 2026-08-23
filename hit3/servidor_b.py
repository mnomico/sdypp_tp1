"""Hit #3 — Proceso B: sigue en funcionamiento aunque A cierre la conexión.

El hilo principal sólo acepta conexiones; cada cliente se atiende en un hilo
propio, de modo que un corte abrupto de A afecta únicamente a ese hilo. Expone
además un endpoint HTTP /health con el estado del servicio.
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
PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT3", 9003)
PUERTO_HEALTH_POR_DEFECTO = config.entero("TP1_PUERTO_HEALTH", 8080)
CONEXIONES_EN_ESPERA = 16


def construir_respuesta(saludo):
    return f"Hola A, soy B. Recibi tu saludo: {saludo}"


class ServidorB:
    def __init__(self, host, puerto, logger, backlog=CONEXIONES_EN_ESPERA):
        self._logger = logger
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, puerto))
        self._socket.listen(backlog)
        self.host, self.puerto = self._socket.getsockname()

        self._detenido = threading.Event()
        self._lock = threading.Lock()
        self._inicio = time.monotonic()
        self._conexiones_activas = 0
        self._conexiones_atendidas = 0
        self._saludos_recibidos = 0

    def estado(self):
        with self._lock:
            return {
                "servicio": "hit3-servidor-b",
                "estado": "detenido" if self._detenido.is_set() else "ok",
                "uptime_segundos": round(time.monotonic() - self._inicio, 3),
                "puerto_tcp": self.puerto,
                "conexiones_activas": self._conexiones_activas,
                "conexiones_atendidas": self._conexiones_atendidas,
                "saludos_recibidos": self._saludos_recibidos,
            }

    def _atender(self, conexion, direccion):
        with self._lock:
            self._conexiones_activas += 1
            self._conexiones_atendidas += 1
        try:
            with conexion:
                lector = LectorDeMensajes(conexion)
                while not self._detenido.is_set():
                    saludo = lector.leer_mensaje()
                    with self._lock:
                        self._saludos_recibidos += 1
                    self._logger.info("Saludo de %s:%s: %s", *direccion, saludo)
                    enviar_mensaje(conexion, construir_respuesta(saludo))
        except ConexionCerrada:
            self._logger.info("Cliente %s:%s cerro la conexion", *direccion)
        except (MensajeDemasiadoLargo, OSError) as error:
            # Aislar el fallo en este hilo es lo que mantiene vivo al servidor.
            self._logger.warning("Error con %s:%s: %s", *direccion, error)
        finally:
            with self._lock:
                self._conexiones_activas -= 1

    def servir_para_siempre(self):
        self._logger.info("B escuchando en %s:%s", self.host, self.puerto)
        while not self._detenido.is_set():
            try:
                conexion, direccion = self._socket.accept()
            except OSError:
                if self._detenido.is_set():
                    break
                self._logger.exception("Fallo el accept; se continua escuchando")
                continue
            self._logger.info("Conexion aceptada desde %s:%s", *direccion)
            threading.Thread(
                target=self._atender, args=(conexion, direccion), daemon=True
            ).start()
        self._logger.info("B dejo de aceptar conexiones")

    def iniciar_en_hilo(self):
        hilo = threading.Thread(target=self.servir_para_siempre, daemon=True)
        hilo.start()
        return hilo

    def detener(self):
        self._detenido.set()
        self._socket.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hit #3 - Proceso B (servidor TCP)")
    parser.add_argument("--host", default=HOST_POR_DEFECTO)
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    parser.add_argument("--puerto-health", type=int, default=PUERTO_HEALTH_POR_DEFECTO)
    parser.add_argument("--sin-health", action="store_true")
    args = parser.parse_args(argv)

    logger, _ = configurar("hit3.servidor_b")
    servidor = ServidorB(args.host, args.puerto, logger)

    if not args.sin_health:
        iniciar_health(args.puerto_health, servidor.estado)
        logger.info("Health disponible en http://%s:%s/health", args.host, args.puerto_health)

    try:
        servidor.servir_para_siempre()
    except KeyboardInterrupt:
        logger.info("B finalizado por el usuario")
    finally:
        servidor.detener()
    return 0


if __name__ == "__main__":
    sys.exit(main())
