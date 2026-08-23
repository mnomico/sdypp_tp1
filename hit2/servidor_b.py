"""Hit #2 — Proceso B: mantiene abierto el canal con A y responde cada saludo.

Sigue atendiendo una única conexión: si A se desconecta, B termina. Esa
limitación es justamente la que resuelve el Hit #3.
"""

import argparse
import socket
import sys

from comun import config
from comun.protocolo import ConexionCerrada, LectorDeMensajes, enviar_mensaje
from comun.registro import configurar

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT2", 9002)


def construir_respuesta(saludo):
    return f"Hola A, soy B. Recibi tu saludo: {saludo}"


def crear_socket_servidor(host, puerto):
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.bind((host, puerto))
    servidor.listen(1)
    return servidor


def atender_una_conexion(servidor, logger):
    """Atiende una conexión hasta que el cliente la cierre. Devuelve los saludos."""
    conexion, direccion = servidor.accept()
    logger.info("Conexion aceptada desde %s:%s", *direccion)
    saludos = []
    with conexion:
        lector = LectorDeMensajes(conexion)
        while True:
            try:
                saludo = lector.leer_mensaje()
            except ConexionCerrada:
                logger.info("A cerro la conexion")
                break
            logger.info("Saludo recibido: %s", saludo)
            saludos.append(saludo)
            enviar_mensaje(conexion, construir_respuesta(saludo))
    return saludos


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hit #2 - Proceso B (servidor TCP)")
    parser.add_argument("--host", default=HOST_POR_DEFECTO)
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    args = parser.parse_args(argv)

    logger, _ = configurar("hit2.servidor_b")
    with crear_socket_servidor(args.host, args.puerto) as servidor:
        logger.info("B escuchando en %s:%s", args.host, args.puerto)
        atender_una_conexion(servidor, logger)
    logger.info("B finalizo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
