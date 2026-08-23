"""Delimitación de mensajes sobre TCP.

TCP es un flujo de bytes sin límites de mensaje: un `send` del emisor no se
corresponde necesariamente con un `recv` del receptor. Delimitamos cada mensaje
con '\\n' y acumulamos en un buffer hasta encontrarlo.
"""

CODIFICACION = "utf-8"
DELIMITADOR = b"\n"
MAX_BYTES_MENSAJE = 64 * 1024


class ConexionCerrada(Exception):
    """El par cerró la conexión antes de completar un mensaje."""


class MensajeDemasiadoLargo(Exception):
    """El par envió más de MAX_BYTES_MENSAJE sin delimitador."""


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
                del self._buffer[: corte + 1]
                return crudo.decode(CODIFICACION)

            if len(self._buffer) > self._max_bytes:
                raise MensajeDemasiadoLargo(
                    f"mensaje sin delimitador supera {self._max_bytes} bytes"
                )

            trozo = self._sock.recv(4096)
            if not trozo:
                raise ConexionCerrada("el par cerró la conexión")
            self._buffer.extend(trozo)
