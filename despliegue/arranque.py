"""Punto de entrada del contenedor: un Nodo D del Hit #6 y N nodos C en la misma imagen.

En la nube sólo hay un puerto publicado (la plataforma lo informa en `PORT`), así que
ahí va el `/health` del Nodo D, que es lo que se verifica desde Internet. El puerto
TCP de registro queda interno al contenedor, y los nodos C corren como procesos
hermanos que se registran contra D: el health muestra entonces nodos reales,
puertos aleatorios reales y saludos reales, no un estado fabricado.

Cada proceso es exactamente el mismo `python -m hitN.nodo_x` que se corre a mano
(ver README), sólo que supervisado desde acá:

    python -m despliegue.arranque              # levanta todo y queda esperando
    python -m despliegue.arranque --verificar  # HEALTHCHECK: 0 si el health responde ok

Variables de entorno (todas con default):
    PORT               puerto HTTP del /health (la plataforma lo fija; default 8080)
    TP1_PUERTO_HIT6    puerto TCP interno del Nodo D (default 9600)
    TP1_DEMO_NODOS_C   cantidad de nodos C a levantar (default 3)
    TP1_VERSION        commit desplegado; se muestra en el nombre del Nodo D. La
                       imagen lo trae horneado (ARG del Dockerfile).
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from comun import config

PUERTO_HEALTH = config.entero("PORT", 8080)
PUERTO_D = config.entero("TP1_PUERTO_HIT6", 9600)
CANTIDAD_NODOS_C = config.entero("TP1_DEMO_NODOS_C", 3)
VERSION = config.texto("TP1_VERSION", "")
ESPERA_ARRANQUE_D = 15.0
GRACIA_APAGADO = 5.0


def _leer_health(puerto, timeout=2.0):
    with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/health", timeout=timeout) as respuesta:
        return json.loads(respuesta.read().decode("utf-8"))


def verificar(puerto=PUERTO_HEALTH):
    """Devuelve 0 si el Nodo D responde `estado: ok`; 1 en cualquier otro caso."""
    try:
        estado = _leer_health(puerto)
    except (urllib.error.URLError, OSError, ValueError) as error:
        print(f"health no disponible: {error}", file=sys.stderr)
        return 1
    if estado.get("estado") != "ok":
        print(f"health respondió {estado.get('estado')!r}", file=sys.stderr)
        return 1
    return 0


def _esperar_health(puerto, plazo):
    limite = time.monotonic() + plazo
    while time.monotonic() < limite:
        try:
            if _leer_health(puerto).get("estado") == "ok":
                return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.25)
    return False


def _lanzar(modulo, *argumentos):
    comando = [sys.executable, "-m", modulo, *argumentos]
    print("[arranque]", " ".join(comando), flush=True)
    return subprocess.Popen(comando)


def _apagar(procesos):
    for proceso in procesos:
        if proceso.poll() is None:
            proceso.terminate()
    limite = time.monotonic() + GRACIA_APAGADO
    for proceso in procesos:
        restante = max(0.0, limite - time.monotonic())
        try:
            proceso.wait(timeout=restante)
        except subprocess.TimeoutExpired:
            proceso.kill()


def nombre_nodo_d(version=VERSION):
    """`NodoD-nube@<commit>`: el /health dice qué versión está corriendo."""
    return f"NodoD-nube@{version[:7]}" if version else "NodoD-nube"


def ejecutar(puerto_health=PUERTO_HEALTH, puerto_d=PUERTO_D, cantidad_c=CANTIDAD_NODOS_C):
    # D escucha en todas las interfaces porque el health tiene que salir del
    # contenedor; los C se atan a loopback: sólo hablan con D y entre ellos.
    nodo_d = _lanzar(
        "hit6.nodo_d",
        "--host", "0.0.0.0",
        "--puerto", str(puerto_d),
        "--puerto-health", str(puerto_health),
        "--nombre", nombre_nodo_d(),
    )
    procesos = [nodo_d]

    detener = []
    signal.signal(signal.SIGTERM, lambda *_: detener.append("SIGTERM"))
    signal.signal(signal.SIGINT, lambda *_: detener.append("SIGINT"))

    try:
        if not _esperar_health(puerto_health, ESPERA_ARRANQUE_D):
            print(f"[arranque] el Nodo D no levantó el /health en {ESPERA_ARRANQUE_D:.0f} s", file=sys.stderr)
            return 1

        for numero in range(1, cantidad_c + 1):
            procesos.append(_lanzar(
                "hit6.nodo_c",
                "--host", "127.0.0.1",
                "--d-host", "127.0.0.1",
                "--d-puerto", str(puerto_d),
                "--nombre", f"C{numero}",
                "--sin-health",
            ))
            time.sleep(0.5)

        print(f"[arranque] listo: Nodo D con /health en :{puerto_health} y {cantidad_c} nodos C", flush=True)

        # El contenedor vive mientras viva D. Si D muere, el contenedor termina con
        # su código y la plataforma lo reinicia; los C que mueran quedan en el log.
        while not detener:
            codigo = nodo_d.poll()
            if codigo is not None:
                print(f"[arranque] el Nodo D terminó con código {codigo}", file=sys.stderr)
                return codigo or 1
            for proceso in procesos[1:]:
                if proceso.poll() is not None and not getattr(proceso, "_avisado", False):
                    print(f"[arranque] un nodo C terminó con código {proceso.returncode}", file=sys.stderr)
                    proceso._avisado = True
            time.sleep(1.0)

        print(f"[arranque] {detener[0]} recibida, apagando", flush=True)
        return 0
    finally:
        _apagar(procesos)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Arranque del despliegue del TP1 (Nodo D + nodos C)")
    parser.add_argument("--verificar", action="store_true",
                        help="no levanta nada: consulta el /health local y sale con 0 si está ok")
    parser.add_argument("--puerto-health", type=int, default=PUERTO_HEALTH)
    parser.add_argument("--puerto-d", type=int, default=PUERTO_D)
    parser.add_argument("--nodos-c", type=int, default=CANTIDAD_NODOS_C)
    args = parser.parse_args(argv)

    if args.verificar:
        return verificar(args.puerto_health)
    return ejecutar(args.puerto_health, args.puerto_d, args.nodos_c)


if __name__ == "__main__":
    sys.exit(main())
