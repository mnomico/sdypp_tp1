"""Registro de actividades (logs) en memoria y en disco."""

import logging
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMATO = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
CAPACIDAD_MEMORIA = 500
DIRECTORIO_LOGS = Path(__file__).resolve().parent.parent / "logs"


class HandlerEnMemoria(logging.Handler):
    """Conserva los últimos `capacidad` registros formateados en RAM."""

    def __init__(self, capacidad=CAPACIDAD_MEMORIA):
        super().__init__()
        self.registros = deque(maxlen=capacidad)

    def emit(self, record):
        self.registros.append(self.format(record))

    def ultimos(self, cantidad=None):
        if cantidad is None:
            return list(self.registros)
        return list(self.registros)[-cantidad:]


def configurar(nombre, archivo=None, nivel=logging.INFO, directorio=None):
    """Devuelve (logger, handler_en_memoria) con salida a consola, disco y RAM."""
    logger = logging.getLogger(nombre)
    logger.setLevel(nivel)
    # Cerrar antes de descartar: `clear()` saca los handlers de la lista pero el
    # archivo de log queda abierto, y los descriptores se acumulan si se
    # reconfigura el mismo logger (en Windows eso además bloquea la rotación).
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.propagate = False

    formateador = logging.Formatter(FORMATO)

    consola = logging.StreamHandler()
    consola.setFormatter(formateador)
    logger.addHandler(consola)

    memoria = HandlerEnMemoria()
    memoria.setFormatter(formateador)
    logger.addHandler(memoria)

    destino = Path(directorio) if directorio else DIRECTORIO_LOGS
    destino.mkdir(parents=True, exist_ok=True)
    disco = RotatingFileHandler(
        destino / (archivo or f"{nombre}.log"),
        maxBytes=1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    disco.setFormatter(formateador)
    logger.addHandler(disco)

    return logger, memoria
