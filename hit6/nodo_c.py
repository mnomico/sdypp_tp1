"""Hit #6 — Nodo C con registro dinámico mediante Nodo D.

Node C inicia su escucha en un puerto aleatorio (asignado por el SO al hacer bind en el puerto 0),
se comunica con el Nodo D para registrar su IP y puerto aleatorio, recibe de D la lista de los
demás nodos C actualmente en ejecución, y se conecta a cada uno de ellos para enviar el saludo.
"""

import argparse
import socket
import sys
import threading
import time

from comun import config, mensajes
from comun.health import iniciar_health_opcional
from comun.protocolo import (
    ConexionCerrada,
    ErrorDeProtocolo,
    LectorDeMensajes,
    MensajeDemasiadoLargo,
    MensajeIlegible,
    enviar_mensaje,
)
from comun.registro import configurar

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
D_HOST_POR_DEFECTO = config.texto("TP1_D_HOST", "127.0.0.1")
D_PUERTO_POR_DEFECTO = config.entero("TP1_D_PUERTO", 9600)
PUERTO_HEALTH_POR_DEFECTO = config.entero("TP1_PUERTO_HEALTH", 8080)
ESPERA_INICIAL = config.decimal("TP1_ESPERA_INICIAL", 0.5)
ESPERA_MAXIMA = config.decimal("TP1_ESPERA_MAXIMA", 5.0)
TIMEOUT_CONEXION = config.decimal("TP1_TIMEOUT", 5.0)
TIMEOUT_INACTIVIDAD = config.decimal("TP1_TIMEOUT_INACTIVIDAD", 60.0)
CONEXIONES_EN_ESPERA = 16


class NodoC:
    """Nodo C: se registra dinámicamente en D y saluda a sus pares."""

    def __init__(
        self,
        d_host,
        d_puerto,
        logger,
        host=HOST_POR_DEFECTO,
        nombre=None,
        backlog=CONEXIONES_EN_ESPERA,
        timeout_inactividad=TIMEOUT_INACTIVIDAD,
    ):
        self.d_host = d_host
        self.d_puerto = int(d_puerto)
        self._logger = logger
        self._timeout_inactividad = timeout_inactividad

        # Escucha en puerto 0 -> el SO asigna un puerto aleatorio libre
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, 0))
        self._socket.listen(backlog)

        self.host, self.puerto = self._socket.getsockname()
        self.nombre = nombre or f"C[{self.host}:{self.puerto}]"

        self._detenido = threading.Event()
        self._lock = threading.Lock()
        self._inicio = time.monotonic()
        self._hilos = []
        self._conexiones = set()
        self._pares_registrados = []
        self._saludos_recibidos = 0
        self._saludos_enviados = 0
        self._respuestas_recibidas = 0
        self._conexiones_activas = 0
        self._conexiones_atendidas = 0
        self._mensajes_invalidos = 0

    # ------------------------------------------------------------------ estado

    def estado(self):
        with self._lock:
            return {
                "servicio": "hit6-nodo-c",
                "formato_mensajes": "json",
                "nombre": self.nombre,
                "estado": "detenido" if self._detenido.is_set() else "ok",
                "uptime_segundos": round(time.monotonic() - self._inicio, 3),
                "escuchando_en": f"{self.host}:{self.puerto}",
                "nodo_d": f"{self.d_host}:{self.d_puerto}",
                "pares_descubiertos": list(self._pares_registrados),
                "conexiones_activas": self._conexiones_activas,
                "conexiones_atendidas": self._conexiones_atendidas,
                "saludos_recibidos": self._saludos_recibidos,
                "saludos_enviados": self._saludos_enviados,
                "respuestas_recibidas": self._respuestas_recibidas,
                "mensajes_invalidos": self._mensajes_invalidos,
            }

    def construir_respuesta(self, saludo):
        return mensajes.crear_respuesta(self.nombre, saludo)

    def construir_saludo(self):
        return mensajes.crear_saludo(self.nombre)

    # -------------------------------------------------------- registro con D

    def registrar_en_nodo_d(self, timeout=TIMEOUT_CONEXION):
        """Se comunica con D para informarle IP y puerto, y obtener los otros pares."""
        self._logger.info(
            "[registro-d] registrandose en D (%s:%s) con IP=%s, puerto=%s...",
            self.d_host, self.d_puerto, self.host, self.puerto
        )
        with socket.create_connection((self.d_host, self.d_puerto), timeout=timeout) as sock:
            registro_msg = mensajes.crear_registro(self.nombre, self.host, self.puerto)
            enviar_mensaje(sock, mensajes.serializar(registro_msg))

            lector = LectorDeMensajes(sock)
            crudo_resp = lector.leer_mensaje()
            respuesta = mensajes.deserializar(crudo_resp)

            if respuesta.get("tipo") != mensajes.TIPO_REGISTRO_RESPUESTA:
                raise RuntimeError(f"Respuesta inesperada de D: {respuesta}")

            nodos = respuesta.get("nodos", [])
            with self._lock:
                self._pares_registrados = list(nodos)
            self._logger.info(
                "[registro-d] registrado exitosamente. Pares recibidos de D: %d (%s)",
                len(nodos), nodos
            )
            return nodos

    # ----------------------------------------------------------- lado servidor

    def _atender(self, conexion, direccion):
        with self._lock:
            self._conexiones.add(conexion)
            self._conexiones_activas += 1
            self._conexiones_atendidas += 1
        try:
            with conexion:
                conexion.settimeout(self._timeout_inactividad)
                lector = LectorDeMensajes(conexion)
                while not self._detenido.is_set():
                    try:
                        crudo = lector.leer_mensaje()
                    except MensajeIlegible as error:
                        # Bytes que no son UTF-8 se descartan como cualquier otro
                        # mensaje mal formado, sin cortar el canal.
                        with self._lock:
                            self._mensajes_invalidos += 1
                        self._logger.warning(
                            "[entrante] mensaje ilegible de %s:%s: %s", *direccion, error
                        )
                        continue

                    try:
                        saludo = mensajes.deserializar(crudo)
                    except mensajes.MensajeInvalido as error:
                        with self._lock:
                            self._mensajes_invalidos += 1
                        self._logger.warning(
                            "[entrante] mensaje invalido de %s:%s: %s", *direccion, error
                        )
                        continue

                    with self._lock:
                        self._saludos_recibidos += 1
                    self._logger.info(
                        "[entrante] %s de %s: %s",
                        saludo.get("tipo"), saludo.get("origen"), saludo.get("contenido"),
                    )
                    respuesta = self.construir_respuesta(saludo)
                    enviar_mensaje(conexion, mensajes.serializar(respuesta))
        except ConexionCerrada:
            self._logger.info("[entrante] %s:%s cerro la conexion", *direccion)
        except TimeoutError:
            self._logger.info(
                "[entrante] %s:%s sin actividad por %.0fs; se cierra el canal",
                *direccion, self._timeout_inactividad,
            )
        except (MensajeDemasiadoLargo, OSError) as error:
            self._logger.warning("[entrante] error con %s:%s: %s", *direccion, error)
        except Exception:
            self._logger.exception("[entrante] error inesperado con %s:%s", *direccion)
        finally:
            with self._lock:
                self._conexiones.discard(conexion)
                self._conexiones_activas -= 1

    def _registrar_hilo(self, hilo):
        """Anota el hilo y descarta los ya terminados.

        Sin la poda, `_hilos` acumula un objeto Thread por cada conexion
        atendida durante toda la vida del nodo: una fuga de memoria silenciosa
        en un proceso que esta pensado para quedarse corriendo.
        """
        with self._lock:
            self._hilos = [h for h in self._hilos if h.is_alive()]
            self._hilos.append(hilo)

    def _aceptar_conexiones(self):
        self._logger.info("%s escuchando en %s:%s (puerto aleatorio)", self.nombre, self.host, self.puerto)
        while not self._detenido.is_set():
            try:
                conexion, direccion = self._socket.accept()
            except OSError:
                if self._detenido.is_set():
                    break
                self._logger.exception("[entrante] fallo el accept; se sigue escuchando")
                continue
            hilo = threading.Thread(
                target=self._atender, args=(conexion, direccion), daemon=True
            )
            hilo.start()
            self._registrar_hilo(hilo)

    # ------------------------------------------------------------ lado cliente

    def _saludar_a_par(self, host, puerto, timeout=TIMEOUT_CONEXION):
        """Conecta a un par C recibido de D y le envía el saludo."""
        self._logger.info("[saliente] saludando a par %s:%s", host, puerto)
        try:
            with socket.create_connection((host, int(puerto)), timeout=timeout) as sock:
                lector = LectorDeMensajes(sock)
                saludo = self.construir_saludo()
                enviar_mensaje(sock, mensajes.serializar(saludo))
                with self._lock:
                    self._saludos_enviados += 1
                self._logger.info(
                    "[saliente] saludo enviado a %s:%s (id=%s): %s",
                    host, puerto, saludo["id"], saludo["contenido"],
                )

                respuesta = mensajes.deserializar(lector.leer_mensaje())
                with self._lock:
                    self._respuestas_recibidas += 1
                self._logger.info(
                    "[saliente] respuesta de %s (%s:%s): %s",
                    respuesta.get("origen"), host, puerto, respuesta.get("contenido"),
                )
        except (ErrorDeProtocolo, OSError, mensajes.MensajeInvalido) as error:
            self._logger.warning(
                "[saliente] error al saludar a par %s:%s: %s", host, puerto, error
            )

    def saludar_a_todos_los_pares(self, pares):
        """Inicia hilos para saludar a cada uno de los pares recibidos."""
        for par in pares:
            p_host = par.get("ip")
            p_puerto = par.get("puerto")
            if p_host and p_puerto:
                hilo = threading.Thread(
                    target=self._saludar_a_par, args=(p_host, p_puerto), daemon=True
                )
                hilo.start()
                self._registrar_hilo(hilo)

    # --------------------------------------------------------------- ciclo vida

    def iniciar(self, timeout=TIMEOUT_CONEXION):
        # 1. Iniciar servidor de escucha en el puerto aleatorio
        servidor = threading.Thread(target=self._aceptar_conexiones, daemon=True)
        servidor.start()
        self._registrar_hilo(servidor)

        # 2. Registrarse en D y obtener pares
        try:
            pares = self.registrar_en_nodo_d(timeout=timeout)
        except Exception as error:
            self._logger.error("[registro-d] fallo la registracion en D: %s", error)
            pares = []

        # 3. Saludar a cada uno de los pares que D devolvió
        self.saludar_a_todos_los_pares(pares)

    def detener(self):
        self._detenido.set()
        self._socket.close()

        with self._lock:
            abiertos = list(self._conexiones)

        for sock in abiertos:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def esperar(self, duracion=None):
        self._detenido.wait(timeout=duracion)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hit #6 - Nodo C con registro de contactos D"
    )
    parser.add_argument("--d-host", "--host-d", default=D_HOST_POR_DEFECTO,
                        help="IP del programa D (registro de contactos)")
    parser.add_argument("--d-puerto", "--puerto-d", type=int, default=D_PUERTO_POR_DEFECTO,
                        help="puerto TCP del programa D")
    parser.add_argument("--host", default=HOST_POR_DEFECTO, help="IP de escucha local del nodo C")
    parser.add_argument("--nombre", default=None, help="identificador del nodo C")
    parser.add_argument("--puerto-health", type=int, default=PUERTO_HEALTH_POR_DEFECTO)
    parser.add_argument("--sin-health", action="store_true")
    parser.add_argument("--duracion", type=float, default=None,
                        help="segundos a ejecutar antes de salir")
    args = parser.parse_args(argv)

    logger, _ = configurar("hit6.nodo_c")
    nodo = NodoC(
        d_host=args.d_host,
        d_puerto=args.d_puerto,
        logger=logger,
        host=args.host,
        nombre=args.nombre,
    )

    if not args.sin_health:
        iniciar_health_opcional(args.puerto_health, nodo.estado, args.host, logger)

    nodo.iniciar()
    try:
        nodo.esperar(args.duracion)
    except KeyboardInterrupt:
        logger.info("%s finalizado por el usuario", nodo.nombre)
    finally:
        nodo.detener()
    return 0


if __name__ == "__main__":
    sys.exit(main())
