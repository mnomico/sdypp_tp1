"""Hit #1 — Proceso A: cliente TCP que se conecta con B y lo saluda."""

import argparse
import socket
import sys

from comun import config
from comun.protocolo import LectorDeMensajes, enviar_mensaje
from comun.registro import configurar

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT1", 9001)
SALUDO_POR_DEFECTO = config.texto("TP1_SALUDO", "Hola B, soy A")
TIMEOUT_SEGUNDOS = config.decimal("TP1_TIMEOUT", 5.0)


def saludar(host, puerto, saludo=SALUDO_POR_DEFECTO, timeout=TIMEOUT_SEGUNDOS):
    """Se conecta a B, envía el saludo y devuelve la respuesta."""
    with socket.create_connection((host, puerto), timeout=timeout) as sock:
        enviar_mensaje(sock, saludo)
        return LectorDeMensajes(sock).leer_mensaje()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hit #1 - Proceso A (cliente TCP)")
    parser.add_argument("--host", default=HOST_POR_DEFECTO)
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    parser.add_argument("--saludo", default=SALUDO_POR_DEFECTO)
    args = parser.parse_args(argv)

    logger, _ = configurar("hit1.cliente_a")
    logger.info("Conectando a B en %s:%s", args.host, args.puerto)
    respuesta = saludar(args.host, args.puerto, args.saludo)
    logger.info("Respuesta de B: %s", respuesta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
