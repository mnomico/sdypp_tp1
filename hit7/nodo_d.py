"""Hit #7 — Nodo D: Sistema de inscripciones por ventanas de tiempo.

Nodo D coordina ventanas de tiempo fijas (por defecto 1 min / 60s).
Mantiene dos registros en RAM:
  1. `nodos_activos`: Nodos C activos en la ventana de tiempo actual.
  2. `nodos_siguientes`: Nodos C registrados para participar en la siguiente ventana.

Cada 60s, D mueve `nodos_siguientes` a `nodos_activos`, vacía `nodos_siguientes` y abre las
inscripciones para la nueva ronda. Además, guarda el estado de inscripciones en un archivo JSON en disco.
"""

import argparse
import json
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from comun import config, mensajes
from comun.health import iniciar_health_opcional
from comun.protocolo import (
    ConexionCerrada,
    LectorDeMensajes,
    MensajeDemasiadoLargo,
    MensajeIlegible,
    enviar_mensaje,
)
from comun.registro import configurar

HOST_POR_DEFECTO = config.texto("TP1_HOST", "127.0.0.1")
PUERTO_POR_DEFECTO = config.entero("TP1_PUERTO_HIT7", 9700)
PUERTO_HEALTH_POR_DEFECTO = config.entero("TP1_PUERTO_HEALTH_D_HIT7", 8087)
DURACION_VENTANA_POR_DEFECTO = config.decimal("TP1_DURACION_VENTANA", 60.0)
TIMEOUT_INACTIVIDAD = config.decimal("TP1_TIMEOUT_INACTIVIDAD", 60.0)
ARCHIVO_INSCRIPCIONES_DEFECTO = (
    Path(__file__).resolve().parent.parent / "logs" / "inscripciones_hit7.json"
)
CONEXIONES_EN_ESPERA = 16


def _ahora_utc_iso():
    return datetime.now(timezone.utc).isoformat()


class NodoD:
    """Nodo D para Hit 7: Sistema de inscripciones por ventanas de tiempo con persistencia JSON."""

    def __init__(
        self,
        host,
        puerto,
        logger,
        nombre="NodoD-Hit7",
        duracion_ventana=DURACION_VENTANA_POR_DEFECTO,
        archivo_inscripciones=ARCHIVO_INSCRIPCIONES_DEFECTO,
        backlog=CONEXIONES_EN_ESPERA,
    ):
        self._logger = logger
        self._timeout_inactividad = TIMEOUT_INACTIVIDAD
        self.nombre = nombre
        self.duracion_ventana = float(duracion_ventana)
        self.archivo_inscripciones = Path(archivo_inscripciones)

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((host, puerto))
        self._socket.listen(backlog)
        self.host, self.puerto = self._socket.getsockname()

        # Registros en RAM
        self._nodos_activos = []  # Ventana actual
        self._nodos_siguientes = []  # Ventana siguiente
        self._historial_ventanas = []
        self._ventana_actual_id = _ahora_utc_iso()

        self._detenido = threading.Event()
        self._lock = threading.Lock()
        self._inicio = time.monotonic()
        self._hilos = []
        self._conexiones = set()
        self._conexiones_activas = 0
        self._conexiones_atendidas = 0
        self._mensajes_invalidos = 0

        # Guardar estado inicial en JSON
        self._guardar_json()

    # -------------------------------------------------------- persistencia JSON

    def _guardar_json(self):
        """Guarda en un archivo de texto con formato JSON la información de inscripciones."""
        try:
            self.archivo_inscripciones.parent.mkdir(parents=True, exist_ok=True)
            datos = {
                "servicio": "hit7-nodo-d",
                "actualizado_en": _ahora_utc_iso(),
                "ventana_actual": self._ventana_actual_id,
                "duracion_ventana_segundos": self.duracion_ventana,
                "cantidad_nodos_activos": len(self._nodos_activos),
                "nodos_activos": [dict(n) for n in self._nodos_activos],
                "cantidad_nodos_siguientes": len(self._nodos_siguientes),
                "nodos_siguientes": [dict(n) for n in self._nodos_siguientes],
                "historial_ventanas": self._historial_ventanas[-10:],
            }
            tmp = self.archivo_inscripciones.with_suffix(".tmp")
            tmp.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.archivo_inscripciones)
        except OSError as error:
            self._logger.warning("[persistencia] no se pudo guardar inscripciones JSON: %s", error)

    # ------------------------------------------------------------------ estado

    def estado(self):
        with self._lock:
            cant_activos = len(self._nodos_activos)
            cant_siguientes = len(self._nodos_siguientes)
            uptime = round(time.monotonic() - self._inicio, 3)
            estado_str = "detenido" if self._detenido.is_set() else "ok"
            return {
                "servicio": "hit7-nodo-d",
                "nombre": self.nombre,
                "estado": estado_str,
                "estado_general": estado_str,
                "uptime": uptime,
                "uptime_segundos": uptime,
                "escuchando_en": f"{self.host}:{self.puerto}",
                "ventana_actual": self._ventana_actual_id,
                "duracion_ventana_segundos": self.duracion_ventana,
                "cantidad_nodos_activos": cant_activos,
                "nodos_activos": [dict(n) for n in self._nodos_activos],
                "cantidad_nodos_siguientes": cant_siguientes,
                "nodos_siguientes": [dict(n) for n in self._nodos_siguientes],
                "archivo_inscripciones": str(self.archivo_inscripciones),
                "conexiones_activas": self._conexiones_activas,
                "conexiones_atendidas": self._conexiones_atendidas,
                "mensajes_invalidos": self._mensajes_invalidos,
            }

    # ------------------------------------------------------ rotacion de ventana

    def rotar_ventana(self):
        """Mueve las inscripciones futuras a la ventana presente e inicia nueva ronda."""
        with self._lock:
            # Registrar historial de la ventana que cierra
            self._historial_ventanas.append(
                {
                    "ventana_id": self._ventana_actual_id,
                    "nodos_activos": [dict(n) for n in self._nodos_activos],
                    "fin": _ahora_utc_iso(),
                }
            )

            # Promover futuras -> presentes y reiniciar futuras
            self._nodos_activos = list(self._nodos_siguientes)
            self._nodos_siguientes = []
            self._ventana_actual_id = _ahora_utc_iso()

            self._guardar_json()
            self._logger.info(
                "[ventana] rotacion ejecutada. Ventana actual: %s. Nodos activos: %d. Inscripciones abiertas para proxima ronda.",
                self._ventana_actual_id, len(self._nodos_activos)
            )

    def _loop_rotacion_ventanas(self):
        while not self._detenido.is_set():
            if self._detenido.wait(timeout=self.duracion_ventana):
                break
            self.rotar_ventana()

    # ------------------------------------------------------------------ servidor

    def _atender_conexion(self, conexion, direccion):
        with self._lock:
            self._conexiones.add(conexion)
            self._conexiones_activas += 1
            self._conexiones_atendidas += 1
        try:
            with conexion:
                conexion.settimeout(self._timeout_inactividad)
                lector = LectorDeMensajes(conexion)
                crudo = lector.leer_mensaje()
                try:
                    mensaje = mensajes.deserializar(crudo)
                except mensajes.MensajeInvalido as error:
                    with self._lock:
                        self._mensajes_invalidos += 1
                    self._logger.warning(
                        "[servidor] mensaje invalido de %s:%s: %s", *direccion, error
                    )
                    return

                tipo = mensaje.get("tipo")

                if tipo == mensajes.TIPO_REGISTRO:
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
                        # Anotar en la ventana SIGUIENTE
                        ya_inscrito = any(
                            n["ip"] == c_ip and n["puerto"] == c_puerto
                            for n in self._nodos_siguientes
                        )
                        if not ya_inscrito:
                            self._nodos_siguientes.append(
                                {"ip": c_ip, "puerto": c_puerto, "nombre": c_origen}
                            )

                        self._guardar_json()
                        activos_actuales = [dict(n) for n in self._nodos_activos]

                    self._logger.info(
                        "[registro] %s anotado para la SIGUIENTE ventana. Activos en ventana actual: %d",
                        c_origen, len(activos_actuales)
                    )

                    # Responder a C con las inscripciones ACTIVAS de la ventana actual
                    respuesta = mensajes.crear_registro_respuesta(
                        self.nombre, mensaje, activos_actuales
                    )
                    enviar_mensaje(conexion, mensajes.serializar(respuesta))

                elif tipo == mensajes.TIPO_CONSULTA_ACTIVOS:
                    with self._lock:
                        activos_actuales = [dict(n) for n in self._nodos_activos]

                    respuesta = mensajes.crear_consulta_activos_respuesta(
                        self.nombre, mensaje, activos_actuales
                    )
                    enviar_mensaje(conexion, mensajes.serializar(respuesta))

                else:
                    with self._lock:
                        self._mensajes_invalidos += 1
                    self._logger.warning(
                        "[servidor] tipo de mensaje desconocido '%s' de %s:%s", tipo, *direccion
                    )

        except ConexionCerrada:
            self._logger.info("[servidor] %s:%s cerro la conexion", *direccion)
        except MensajeIlegible as error:
            # Bytes que no son UTF-8: se descarta la peticion, no se cae el hilo.
            with self._lock:
                self._mensajes_invalidos += 1
            self._logger.warning("[servidor] mensaje ilegible de %s:%s: %s", *direccion, error)
        except TimeoutError:
            self._logger.info(
                "[servidor] %s:%s sin actividad por %.0fs; se cierra el canal",
                *direccion, self._timeout_inactividad,
            )
        except (MensajeDemasiadoLargo, OSError) as error:
            self._logger.warning("[servidor] error con %s:%s: %s", *direccion, error)
        except Exception:
            self._logger.exception("[servidor] error inesperado con %s:%s", *direccion)
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
        self._logger.info("%s escuchando en %s:%s", self.nombre, self.host, self.puerto)
        while not self._detenido.is_set():
            try:
                conexion, direccion = self._socket.accept()
            except OSError:
                if self._detenido.is_set():
                    break
                self._logger.exception("[servidor] fallo el accept; se sigue escuchando")
                continue
            hilo = threading.Thread(
                target=self._atender_conexion, args=(conexion, direccion), daemon=True
            )
            hilo.start()
            self._registrar_hilo(hilo)

    # --------------------------------------------------------------- ciclo vida

    def iniciar(self):
        servidor = threading.Thread(target=self._aceptar_conexiones, daemon=True)
        servidor.start()
        self._registrar_hilo(servidor)

        rotador = threading.Thread(target=self._loop_rotacion_ventanas, daemon=True)
        rotador.start()
        self._registrar_hilo(rotador)

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
        description="Hit #7 - Nodo D: Sistema de inscripciones por ventanas de tiempo"
    )
    parser.add_argument("--host", default=HOST_POR_DEFECTO, help="IP donde escuchar registros")
    parser.add_argument("--puerto", type=int, default=PUERTO_POR_DEFECTO,
                        help="puerto TCP de registros")
    parser.add_argument("--nombre", default="NodoD-Hit7", help="identificador del nodo D")
    parser.add_argument("--duracion-ventana", type=float, default=DURACION_VENTANA_POR_DEFECTO,
                        help="duración de la ventana de tiempo en segundos (por defecto 60)")
    parser.add_argument("--archivo-inscripciones", default=str(ARCHIVO_INSCRIPCIONES_DEFECTO),
                        help="ruta del archivo JSON de inscripciones")
    parser.add_argument("--puerto-health", type=int, default=PUERTO_HEALTH_POR_DEFECTO)
    parser.add_argument("--sin-health", action="store_true")
    parser.add_argument("--duracion", type=float, default=None,
                        help="segundos a ejecutar antes de salir")
    args = parser.parse_args(argv)

    logger, _ = configurar("hit7.nodo_d")
    nodo = NodoD(
        host=args.host,
        puerto=args.puerto,
        logger=logger,
        nombre=args.nombre,
        duracion_ventana=args.duracion_ventana,
        archivo_inscripciones=args.archivo_inscripciones,
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
