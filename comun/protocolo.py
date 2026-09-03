"""Delimitación de mensajes sobre TCP.

TCP es un flujo de bytes sin límites de mensaje: un `send` del emisor no se
corresponde necesariamente con un `recv` del receptor. Delimitamos cada mensaje
con '\\n' y acumulamos en un buffer hasta encontrarlo.

Todos los errores de lectura se traducen a excepciones propias de este módulo
(`ConexionCerrada`, `MensajeDemasiadoLargo`, `MensajeIlegible`). Quien use
`LectorDeMensajes` no debería tener que atrapar excepciones de la biblioteca
estándar: si se filtrara una, moriría el hilo que la ignore.
"""

CODIFICACION = "utf-8"
DELIMITADOR = b"\n"
MAX_BYTES_MENSAJE = 64 * 1024


class ErrorDeProtocolo(Exception):
    """Base de los errores de lectura del protocolo."""


class ConexionCerrada(ErrorDeProtocolo):
    """El par cerró la conexión antes de completar un mensaje."""


class MensajeDemasiadoLargo(ErrorDeProtocolo):
    """El par envió más de MAX_BYTES_MENSAJE sin delimitador."""


class MensajeIlegible(ErrorDeProtocolo):
    """El par envió bytes que no son texto válido en la codificación acordada."""


def enviar_mensaje(sock, texto):
    sock.sendall(texto.encode(CODIFICACION) + DELIMITADOR)


class LectorDeMensajes:
    """Lee mensajes delimitados por '\\n' desde un socket."""

    def __init__(self, sock, max_bytes=MAX_BYTES_MENSAJE):
        self._sock = sock
        self._max_bytes = max_bytes
        self._buffer = bytearray()

    def leer_mensaje(self):
        while True:
            corte = self._buffer.find(DELIMITADOR)
            if corte != -1:
                crudo = bytes(self._buffer[:corte])
                # El descarte del buffer va ANTES de decodificar: si los bytes
                # son ilegibles, la línea defectuosa ya quedó consumida y el
                # llamador puede seguir leyendo el resto del canal.
                del self._buffer[: corte + 1]
                try:
                    return crudo.decode(CODIFICACION)
                except UnicodeDecodeError as error:
                    raise MensajeIlegible(
                        f"bytes no decodificables como {CODIFICACION}: {error}"
                    ) from error

            if len(self._buffer) > self._max_bytes:
                raise MensajeDemasiadoLargo(
                    f"mensaje sin delimitador supera {self._max_bytes} bytes"
                )

            trozo = self._sock.recv(4096)
            if not trozo:
                raise ConexionCerrada("el par cerró la conexión")
            self._buffer.extend(trozo)
