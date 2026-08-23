"""Hit #3 — Proceso A: mismo cliente resiliente del Hit #2 apuntando al B persistente.

Se mantiene la reconexión con backoff para poder verificar que B sobrevive a
que A sea terminado abruptamente y siga aceptando nuevas conexiones.
"""

import argparse
import socket
import sys
import time

from comun import config
from comun.protocolo import ConexionCerrada, LectorDeMensajes, enviar_mensaje
from comun.registro import configurar

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT3", 9003)
SALUDO_POR_DEFECTO = config.texto("TP1_SALUDO", "Hola B, soy A")
ESPERA_INICIAL = config.decimal("TP1_ESPERA_INICIAL", 0.5)
ESPERA_MAXIMA = config.decimal("TP1_ESPERA_MAXIMA", 5.0)
TIMEOUT_CONEXION = config.decimal("TP1_TIMEOUT", 5.0)


def siguiente_espera(espera, espera_maxima=ESPERA_MAXIMA):
    return min(espera * 2, espera_maxima)


def ejecutar(
    host,
    puerto,
    logger,
    saludo=SALUDO_POR_DEFECTO,
    max_ciclos=None,
    espera_inicial=ESPERA_INICIAL,
    espera_maxima=ESPERA_MAXIMA,
    timeout=TIMEOUT_CONEXION,
    dormir=time.sleep,
    esperar_cierre=True,
):
    respuestas = []
    espera = espera_inicial
    ciclos = 0

    while max_ciclos is None or ciclos < max_ciclos:
        ciclos += 1
        try:
            with socket.create_connection((host, puerto), timeout=timeout) as sock:
                logger.info("Conectado a B en %s:%s", host, puerto)
                espera = espera_inicial
                lector = LectorDeMensajes(sock)
                enviar_mensaje(sock, saludo)
                logger.info("Saludo enviado: %s", saludo)
                respuesta = lector.leer_mensaje()
                logger.info("Respuesta de B: %s", respuesta)
                respuestas.append(respuesta)

                if not esperar_cierre:
                    break
                # El timeout protege el saludo y su respuesta, pero la vigilancia
                # del canal debe ser indefinida: con timeout, A se reconectaria
                # cada `timeout` segundos aunque B siguiera perfectamente vivo.
                sock.settimeout(None)
                lector.leer_mensaje()
        except (ConexionCerrada, OSError) as error:
            logger.warning(
                "Comunicacion con B interrumpida (%s). Reintento en %.1fs",
                error,
                espera,
            )
            if max_ciclos is None or ciclos < max_ciclos:
                dormir(espera)
                espera = siguiente_espera(espera, espera_maxima)

    return respuestas


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hit #3 - Proceso A (cliente TCP)")
    parser.add_argument("--host", default=HOST_POR_DEFECTO)
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO)
    parser.add_argument("--saludo", default=SALUDO_POR_DEFECTO)
    parser.add_argument("--max-ciclos", type=int, default=None)
    parser.add_argument(
        "--salir-tras-respuesta",
        action="store_true",
        help="terminar apenas B responde, sin quedar vigilando el canal",
    )
    args = parser.parse_args(argv)

    logger, _ = configurar("hit3.cliente_a")
    try:
        ejecutar(
            args.host,
            args.puerto,
            logger,
            args.saludo,
            args.max_ciclos,
            esperar_cierre=not args.salir_tras_respuesta,
        )
    except KeyboardInterrupt:
        logger.info("A finalizado por el usuario")
    return 0


if __name__ == "__main__":
    sys.exit(main())
