"""Hit #6 — Nodo D: Registro de contactos.

Nodo D actúa como un registro centralizado donde los nodos C reportan su IP
y puerto de escucha al iniciar. Mantiene un arreglo en RAM (inicialmente vacío)
con las instancias de C en ejecución y expone un endpoint HTTP /health.
"""

import argparse
import socket
import sys
import threading
import time

from comun import config, mensajes
from comun.health import iniciar_health
from comun.protocolo import (
    ConexionCerrada,
    LectorDeMensajes,
    MensajeDemasiadoLargo,
    enviar_mensaje,
)
from comun.registro import configurar

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT6", 9600)
PUERTO_HEALTH_POR_DEFECTO = config.entero("TP1_PUERTO_HEALTH_D", 8086)
CONEXIONES_EN_ESPERA = 16


class NodoD:
    """Nodo D: Registro de contactos en memoria para nodos C."""

    def __init__(self, host, puerto, logger, nombre="NodoD", backlog=CONEXIONES_EN_ESPERA):
        self._logger = logger
        self.nombre = nombre
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, puerto))
        self._socket.listen(backlog)
        self.host, self.puerto = self._socket.getsockname()

        # Registro en RAM de los nodos C en ejecución (inicialmente vacío)
        self._nodos_registrados = []

        self._detenido = threading.Event()
        self._lock = threading.Lock()
        self._inicio = time.monotonic()
        self._hilos = []
        self._conexiones = set()
        self._conexiones_activas = 0
        self._conexiones_atendidas = 0
        self._mensajes_invalidos = 0

    # ------------------------------------------------------------------ estado

    def estado(self):
        with self._lock:
            cantidad = len(self._nodos_registrados)
            nodos_copia = [dict(n) for n in self._nodos_registrados]
            uptime = round(time.monotonic() - self._inicio, 3)
            estado_str = "detenido" if self._detenido.is_set() else "ok"
            return {
                "servicio": "hit6-nodo-d",
                "nombre": self.nombre,
                "estado": estado_str,
                "estado_general": estado_str,
                "uptime": uptime,
                "uptime_segundos": uptime,
                "escuchando_en": f"{self.host}:{self.puerto}",
                "cantidad_nodos_c_registrados": cantidad,
                "nodos_c_registrados": cantidad,
                "nodos": nodos_copia,
                "conexiones_activas": self._conexiones_activas,
                "conexiones_atendidas": self._conexiones_atendidas,
                "mensajes_invalidos": self._mensajes_invalidos,
            }

    # ------------------------------------------------------------------ servidor

    def _atender_registro(self, conexion, direccion):
        with self._lock:
            self._conexiones.add(conexion)
            self._conexiones_activas += 1
            self._conexiones_atendidas += 1
        try:
            with conexion:
                lector = LectorDeMensajes(conexion)
                crudo = lector.leer_mensaje()
                try:
                    mensaje = mensajes.deserializar(crudo)
                except mensajes.MensajeInvalido as error:
                    with self._lock:
                        self._mensajes_invalidos += 1
                    self._logger.warning(
                        "[registro] mensaje invalido de %s:%s: %s", *direccion, error
                    )
                    return

                if mensaje.get("tipo") != mensajes.TIPO_REGISTRO:
                    with self._lock:
                        self._mensajes_invalidos += 1
                    self._logger.warning(
                        "[registro] tipo no esperado '%s' de %s:%s",
                        mensaje.get("tipo"), *direccion
                    )
                    return

                c_ip = mensaje.get("ip")
                c_puerto = mensaje.get("puerto")
                c_origen = mensaje.get("origen", f"C[{c_ip}:{c_puerto}]")

                if not c_ip or not c_puerto:
                    with self._lock:
                        self._mensajes_invalidos += 1
                    self._logger.warning(
                        "[registro] faltan ip o puerto en mensaje de %s:%s", *direccion
                    )
                    return

                with self._lock:
                    # Copia de los otros nodos C ya registrados hasta el momento
                    otros_nodos = [
                        {"ip": n["ip"], "puerto": n["puerto"]}
                        for n in self._nodos_registrados
                        if not (n["ip"] == c_ip and n["puerto"] == c_puerto)
                    ]

                    # Se registra este nodo C en la lista en RAM si no estaba previamente
                    ya_registrado = any(
                        n["ip"] == c_ip and n["puerto"] == c_puerto
                        for n in self._nodos_registrados
                    )
                    if not ya_registrado:
                        self._nodos_registrados.append(
                            {"ip": c_ip, "puerto": c_puerto, "nombre": c_origen}
                        )

                self._logger.info(
                    "[registro] nodo %s registrado en %s:%s (total registrados: %d)",
                    c_origen, c_ip, c_puerto, len(self._nodos_registrados)
                )

                # Responder con las IPs y puertos de los otros nodos C
                respuesta = mensajes.crear_registro_respuesta(
                    self.nombre, mensaje, otros_nodos
                )
                enviar_mensaje(conexion, mensajes.serializar(respuesta))

        except ConexionCerrada:
            self._logger.info("[registro] %s:%s cerro la conexion", *direccion)
        except (MensajeDemasiadoLargo, OSError) as error:
            self._logger.warning("[registro] error con %s:%s: %s", *direccion, error)
        finally:
            with self._lock:
                self._conexiones.discard(conexion)
                self._conexiones_activas -= 1

    def _aceptar_conexiones(self):
        self._logger.info("%s escuchando registros en %s:%s", self.nombre, self.host, self.puerto)
        while not self._detenido.is_set():
            try:
                conexion, direccion = self._socket.accept()
            except OSError:
                if self._detenido.is_set():
                    break
                self._logger.exception("[registro] fallo el accept; se sigue escuchando")
                continue
            hilo = threading.Thread(
                target=self._atender_registro, args=(conexion, direccion), daemon=True
            )
            hilo.start()
            self._hilos.append(hilo)

    # --------------------------------------------------------------- ciclo vida

    def iniciar(self):
        servidor = threading.Thread(target=self._aceptar_conexiones, daemon=True)
        servidor.start()
        self._hilos.append(servidor)

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
        description="Hit #6 - Nodo D: Registro de contactos"
    )
    parser.add_argument("--host", default=HOST_POR_DEFECTO, help="IP donde escuchar registros")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO,
                        help="puerto TCP donde escuchar registros de nodos C")
    parser.add_argument("--nombre", default="NodoD", help="identificador del registro D")
    parser.add_argument("--puerto-health", type=int, default=PUERTO_HEALTH_POR_DEFECTO)
    parser.add_argument("--sin-health", action="store_true")
    parser.add_argument("--duracion", type=float, default=None,
                        help="segundos a ejecutar antes de salir")
    args = parser.parse_args(argv)

    logger, _ = configurar("hit6.nodo_d")
    nodo = NodoD(args.host, args.puerto, logger, args.nombre)

    if not args.sin_health:
        iniciar_health(args.puerto_health, nodo.estado)
        logger.info("Health disponible en http://%s:%s/health", args.host, args.puerto_health)

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
