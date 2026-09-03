# Hit #3 — Servidor B persistente

> Modifique el código de B para que si el proceso A cierra la conexión
> (por ejemplo matando el proceso) siga funcionando.

## Arquitectura

```mermaid
graph TB
    subgraph B["Proceso B"]
        L["Hilo principal<br/>accept() en bucle"]
        H1["Hilo cliente 1"]
        H2["Hilo cliente 2"]
        HTTP["Hilo health<br/>HTTP :8080/health"]
        L -->|"lanza"| H1
        L -->|"lanza"| H2
    end
    A1["Cliente A (1)"] <-->|"TCP :9003"| H1
    A2["Cliente A (2)"] <-->|"TCP :9003"| H2
    OP["Monitoreo"] -->|"GET /health"| HTTP
```

El hilo principal **sólo acepta**; cada cliente se atiende en su propio hilo, así un
corte abrupto de A afecta a ese hilo y nunca al bucle de `accept()`.

## Ejecución

```bash
python -m hit3.servidor_b --puerto 9003 --puerto-health 8080   # terminal 1
python -m hit3.cliente_a  --puerto 9003                        # terminal 2 (varios en paralelo)

kill -9 <pid de A>                        # verificación del enunciado
curl http://127.0.0.1:8080/health
python -m hit3.cliente_a --puerto 9003    # B lo sigue atendiendo
```

```json
{
  "servicio": "hit3-servidor-b", "estado": "ok", "uptime_segundos": 9.516,
  "puerto_tcp": 9003, "conexiones_activas": 0, "conexiones_atendidas": 2,
  "saludos_recibidos": 2, "mensajes_ilegibles": 0
}
```

Parámetros de B: `--host`, `--puerto`, `--puerto-health`, `--sin-health`. A: los
mismos del Hit #2. Por entorno o `.env`: `TP1_HOST`, `TP1_PUERTO_HIT3`,
`TP1_PUERTO_HEALTH`, `TP1_TIMEOUT_INACTIVIDAD` — ver [`.env.example`](../.env.example).
Al desplegar en la nube, `TP1_HOST=0.0.0.0`: vale para el puerto TCP **y** para el
`/health`, que escucha en la misma interfaz.

## Decisiones de diseño

- **Un hilo por conexión.** El modelo más simple que cumple el requisito y el que mejor expone el aislamiento de fallos: la excepción que provoca la muerte de A queda contenida en su hilo. A esta escala el costo es irrelevante; a mayor escala correspondería `selectors`/`asyncio`.
- **Toda excepción del cliente se atrapa y se registra, sin excepciones.** `ConexionCerrada`, `OSError` (incluye el `RST` de un `kill -9`) y `MensajeDemasiadoLargo` terminan sólo ese hilo. Cierra la lista un `except Exception`: una excepción no prevista mataría el hilo con un traceback en `stderr` que **no pasa por el logger**, así que no quedaría ni en `logs/` ni en el buffer en memoria — justo el caso que uno necesita ver. El `finally` descuenta la conexión activa para que las métricas no se desincronicen.
- **Bytes ilegibles se descartan, no cortan.** `LectorDeMensajes` traduce todo fallo de lectura a la familia `ErrorDeProtocolo`; una línea que no es UTF-8 se consume, se cuenta en `mensajes_ilegibles` y se sigue. Que sea una excepción del protocolo y no un `UnicodeDecodeError` crudo es lo que permite atraparla: `UnicodeDecodeError` hereda de `ValueError`, así que ningún `except OSError` lo ve.
- **El bucle de `accept()` también tolera fallos:** si falla por una causa transitoria se registra y se sigue escuchando; sólo corta cuando `detener()` cerró el socket a propósito.
- **Timeout de inactividad por canal (60 s).** Un cliente que conecta y nunca habla retiene un hilo indefinidamente; con suficientes conexiones mudas el servidor se queda sin capacidad sin que se haya caído nada.
- **Estado protegido con un `Lock`:** varios hilos tocan los contadores y sin él el `/health` reportaría números erróneos.
- **Health sobre HTTP, no sobre el puerto TCP.** Es lo que entienden los balanceadores y las sondas de nube. Corre en un hilo daemon aparte y expone uptime y contadores además del estado.
- **El `/health` escucha en la misma interfaz que el servicio.** Antes se ataba siempre a `0.0.0.0`: con `--host 127.0.0.1` el endpoint quedaba publicado en toda la LAN aunque el servicio TCP sólo aceptara conexiones locales. Publicarlo pasa a ser una decisión explícita.
- **Si el puerto del health está ocupado, el servicio arranca igual** y lo registra. Que la falta de observabilidad tumbe al servicio observado es al revés de lo que se busca.
- **A no cambia respecto del Hit #2:** permite ver el efecto combinado, A sobrevive a la caída de B *y* B a la de A.

## Pruebas

```bash
python -m unittest discover -s hit3 -t . -v
```

Corte abrupto del cliente (`RST` con `SO_LINGER` en 0) · conexiones sucesivas y
concurrentes · bytes ilegibles contados sin cortar el canal · cliente mudo cerrado
por inactividad · el JSON del health refleja las conexiones atendidas.
