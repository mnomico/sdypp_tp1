"""Hit #1 — Proceso B: servidor TCP que espera el saludo de A y lo responde.

Atiende una única conexión y termina. La resiliencia frente a la caída del
cliente se incorpora recién en el Hit #3.
"""

import argparse
import socket
import sys

from comun import config
from comun.protocolo import LectorDeMensajes, enviar_mensaje
from comun.registro import configurar

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT1", 9001)


def construir_respuesta(saludo):
    return f"Hola A, soy B. Recibi tu saludo: {saludo}"


def crear_socket_servidor(host, puerto):
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((host, puerto))
    servidor.listen(1)
    return servidor


def atender_una_conexion(servidor, logger):
    """Acepta una conexión, responde el saludo y devuelve (saludo, respuesta)."""
    conexion, direccion = servidor.accept()
    logger.info("Conexion aceptada desde %s:%s", *direccion)
    with conexion:
        saludo = LectorDeMensajes(conexion).leer_mensaje()
        logger.info("Saludo recibido: %s", saludo)
        respuesta = construir_respuesta(saludo)
        enviar_mensaje(conexion, respuesta)
        logger.info("Respuesta enviada: %s", respuesta)
    return saludo, respuesta


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hit #1 - Proceso B (servidor TCP)")
    parser.add_argument("--host", default=HOST_POR_DEFECTO)
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    args = parser.parse_args(argv)

    logger, _ = configurar("hit1.servidor_b")
    with crear_socket_servidor(args.host, args.puerto) as servidor:
        logger.info("B escuchando en %s:%s", args.host, args.puerto)
        atender_una_conexion(servidor, logger)
    logger.info("B finalizo tras atender una conexion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
