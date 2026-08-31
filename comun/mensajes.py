"""Serialización y deserialización de mensajes en JSON (Hit #5).

Cada mensaje viaja como un objeto JSON en una sola línea (formato JSON Lines):
`json.dumps` escapa los saltos de línea del contenido como `\\n`, así que nunca
emite un '\\n' literal y el delimitador de `comun.protocolo` sigue siendo seguro.

Ejemplo de un saludo y su respuesta:

    {"version": 1, "id": "...", "tipo": "saludo", "origen": "C1",
     "contenido": "Hola, soy C1", "timestamp": "2026-03-17T12:00:00+00:00"}

    {"version": 1, "id": "...", "tipo": "respuesta", "origen": "C2",
     "contenido": "Hola C1, soy C2...", "en_respuesta_a": "...", "timestamp": "..."}
"""

import json
import uuid
from datetime import datetime, timezone

VERSION_PROTOCOLO = 1
TIPO_SALUDO = "saludo"
TIPO_RESPUESTA = "respuesta"
TIPO_REGISTRO = "registro"
TIPO_REGISTRO_RESPUESTA = "registro_respuesta"
TIPO_CONSULTA_ACTIVOS = "consulta_activos"
TIPO_CONSULTA_ACTIVOS_RESPUESTA = "consulta_activos_respuesta"
CAMPOS_OBLIGATORIOS = ("tipo", "origen", "contenido")


class MensajeInvalido(Exception):
    """El texto recibido no es un mensaje JSON válido para este protocolo."""


def _ahora():
    return datetime.now(timezone.utc).isoformat()


def crear_saludo(origen, contenido=None):
    return {
        "version": VERSION_PROTOCOLO,
        "id": str(uuid.uuid4()),
        "tipo": TIPO_SALUDO,
        "origen": origen,
        "contenido": contenido or f"Hola, soy {origen}",
        "timestamp": _ahora(),
    }


def crear_respuesta(origen, saludo, contenido=None):
    """Construye la respuesta a un saludo ya deserializado."""
    remitente = saludo.get("origen", "desconocido")
    return {
        "version": VERSION_PROTOCOLO,
        "id": str(uuid.uuid4()),
        "tipo": TIPO_RESPUESTA,
        "origen": origen,
        "contenido": contenido or f"Hola {remitente}, soy {origen}. Recibi tu saludo.",
        "en_respuesta_a": saludo.get("id"),
        "timestamp": _ahora(),
    }


def crear_registro(origen, ip, puerto, contenido=None):
    return {
        "version": VERSION_PROTOCOLO,
        "id": str(uuid.uuid4()),
        "tipo": TIPO_REGISTRO,
        "origen": origen,
        "contenido": contenido or f"Registro de {origen} en {ip}:{puerto}",
        "ip": ip,
        "puerto": puerto,
        "timestamp": _ahora(),
    }


def crear_registro_respuesta(origen, registro, nodos, contenido=None):
    remitente = registro.get("origen", "desconocido")
    return {
        "version": VERSION_PROTOCOLO,
        "id": str(uuid.uuid4()),
        "tipo": TIPO_REGISTRO_RESPUESTA,
        "origen": origen,
        "contenido": contenido or f"Registro exitoso de {remitente}",
        "en_respuesta_a": registro.get("id"),
        "nodos": nodos,
        "timestamp": _ahora(),
    }


def crear_consulta_activos(origen, contenido=None):
    return {
        "version": VERSION_PROTOCOLO,
        "id": str(uuid.uuid4()),
        "tipo": TIPO_CONSULTA_ACTIVOS,
        "origen": origen,
        "contenido": contenido or f"Consulta de activos por {origen}",
        "timestamp": _ahora(),
    }


def crear_consulta_activos_respuesta(origen, consulta, nodos_activos, contenido=None):
    return {
        "version": VERSION_PROTOCOLO,
        "id": str(uuid.uuid4()),
        "tipo": TIPO_CONSULTA_ACTIVOS_RESPUESTA,
        "origen": origen,
        "contenido": contenido or "Listado de nodos activos",
        "en_respuesta_a": consulta.get("id"),
        "nodos_activos": nodos_activos,
        "timestamp": _ahora(),
    }


def serializar(mensaje):
    """Convierte el mensaje a una línea JSON. `ensure_ascii=False` conserva los
    acentos como UTF-8 en vez de inflarlos a secuencias \\uXXXX."""
    return json.dumps(mensaje, ensure_ascii=False, separators=(",", ":"))


def deserializar(texto):
    """Devuelve el mensaje como diccionario, validando su forma."""
    try:
        mensaje = json.loads(texto)
    except json.JSONDecodeError as error:
        raise MensajeInvalido(f"JSON mal formado: {error}") from error

    if not isinstance(mensaje, dict):
        raise MensajeInvalido(f"se esperaba un objeto JSON, llego {type(mensaje).__name__}")

    faltantes = [campo for campo in CAMPOS_OBLIGATORIOS if campo not in mensaje]
    if faltantes:
        raise MensajeInvalido(f"faltan campos obligatorios: {', '.join(faltantes)}")

    return mensaje


def tamano_en_bytes(mensaje):
    """Bytes que ocupa el mensaje serializado (útil para comparar con Protobuf)."""
    return len(serializar(mensaje).encode("utf-8"))
