"""Configuración por variables de entorno.

Los parámetros se resuelven con la precedencia: argumento de línea de comandos >
variable de entorno > valor por defecto. Se admite un archivo `.env` en la raíz
del repositorio (nunca versionado; ver `.env.example`), leído sin dependencias
externas para que el proyecto siga corriendo sin instalar nada.
"""

import os
from pathlib import Path

ARCHIVO_ENV = Path(__file__).resolve().parent.parent / ".env"


def cargar_env(ruta=ARCHIVO_ENV):
    """Carga pares CLAVE=valor de un `.env`. Las variables ya exportadas ganan."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip().strip("\"'"))


def texto(clave, defecto):
    valor = os.environ.get(clave, "").strip()
    return valor or defecto


def entero(clave, defecto):
    try:
        return int(texto(clave, defecto))
    except ValueError:
        return int(defecto)


def decimal(clave, defecto):
    try:
        return float(texto(clave, defecto))
    except ValueError:
        return float(defecto)


cargar_env()
