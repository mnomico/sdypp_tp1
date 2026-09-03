# Sistemas Distribuidos y Programación Paralela - TP 1

**Grupo Cerberus** — Salvador Baez · Mateo Nomico · Tomás Resnik
**Lenguaje:** Python (≥ 3.11, sólo biblioteca estándar)

No hay dependencias externas: todo se resuelve con `socket`, `threading`, `logging`,
`http.server` y `unittest`, así que `pip install -r requirements.txt` no instala nada
en los Hits #1 a #7. Las primeras dependencias aparecen en el #8 (`grpcio`).

## Estado de los hits

| Hit | Consigna | Estado |
|---|---|---|
| [#1](hit1/) | Servidor TCP B que responde el saludo de A | Completo |
| [#2](hit2/) | A reconecta y resaluda si B cae | Completo |
| [#3](hit3/) | B sigue funcionando si A cierra la conexión | Completo |
| [#4](hit4/) | Refactor de A y B en un único programa C | Completo |
| [#5](hit5/) | Mensajes en formato JSON | Completo |
| [#6](hit6/) | Nodo D como registro de contactos | Completo |
| [#7](hit7/) | Sistema de inscripciones por ventanas de 1 min | Completo |
| #8 | Migración a gRPC / Protobuf | Pendiente |

Cada carpeta tiene su `README.md` con el diagrama de arquitectura, las instrucciones
de ejecución y las decisiones de diseño.

## Estructura

Cada hit es una carpeta autocontenida: código, `README.md` y sus propias pruebas.

```
comun/                  # Código compartido
  protocolo.py          #   Delimitación de mensajes sobre el flujo TCP
  mensajes.py           #   Serialización JSON (desde el Hit #5)
  registro.py           #   Logs en memoria y disco
  health.py             #   Endpoint HTTP /health
  config.py             #   Variables de entorno / .env
hit1/ … hit3/           # servidor_b.py + cliente_a.py + tests/ + README.md
hit4/ … hit5/           # nodo_c.py + tests/ + README.md
hit6/ … hit7/           # nodo_d.py + nodo_c.py + tests/ + README.md
.env.example            # Plantilla de configuración (el .env real no se versiona)
.github/workflows/ci.yml
```

## Configuración

Precedencia **argumento CLI > variable de entorno > default**, así que el proyecto
corre sin configurar nada. Para fijar valores propios: `cp .env.example .env`.

| Variable | Descripción | Default |
|---|---|---|
| `TP1_HOST` | Interfaz de escucha del servicio **y del `/health`** | `127.0.0.1` |
| `TP1_PUERTO_HIT1` … `HIT5` | Puerto TCP de cada hit | `9001` … `9005` |
| `TP1_PUERTO_HIT6` / `HIT7` | Puerto TCP del Nodo D de cada hit | `9600` / `9700` |
| `TP1_D_HOST` / `TP1_D_PUERTO` | Nodo D que usan los C del Hit #6 | `127.0.0.1` / `9600` |
| `TP1_DURACION_VENTANA` | Ventana de inscripción del Hit #7 (s) | `60.0` |
| `TP1_PUERTO_HEALTH` | `/health` de los nodos C | `8080` |
| `TP1_PUERTO_HEALTH_D` / `_D_HIT7` | `/health` del Nodo D | `8086` / `8087` |
| `TP1_SALUDO` | Mensaje que A envía a B | `Hola B, soy A` |
| `TP1_TIMEOUT` | Timeout de socket (s) | `5.0` |
| `TP1_TIMEOUT_INACTIVIDAD` | Corte de un canal entrante mudo (s) | `60.0` |
| `TP1_ESPERA_INICIAL` / `_MAXIMA` | Backoff de reintentos (s) | `0.5` / `5.0` |

No hay direcciones ni credenciales hardcodeadas. En la nube los valores se inyectan
como variables de entorno y los secrets vienen de GitHub Secrets / Secret Manager.

## Ejecución rápida

Todo se ejecuta desde la raíz del repositorio, sin IDE. Cada programa acepta `--help`.

```bash
# Hit #1
python -m hit1.servidor_b --puerto 9001     # terminal 1
python -m hit1.cliente_a  --puerto 9001     # terminal 2

# Hit #2 — matar B con kill -9 y volver a levantarlo: A reconecta solo
python -m hit2.servidor_b --puerto 9002
python -m hit2.cliente_a  --puerto 9002

# Hit #3 — matar A con kill -9: B sigue funcionando
python -m hit3.servidor_b --puerto 9003 --puerto-health 8080
python -m hit3.cliente_a  --puerto 9003
curl http://127.0.0.1:8080/health

# Hit #4 — dos nodos C que se saludan mutuamente (uno por terminal)
python -m hit4.nodo_c --puerto 9401 --par-host 127.0.0.1 --par-puerto 9402 --nombre C1 --puerto-health 8401
python -m hit4.nodo_c --puerto 9402 --par-host 127.0.0.1 --par-puerto 9401 --nombre C2 --puerto-health 8402

# Hit #5 — igual que el #4, pero los mensajes viajan en JSON
python -m hit5.nodo_c --puerto 9501 --par-host 127.0.0.1 --par-puerto 9502 --nombre C1 --puerto-health 8501
python -m hit5.nodo_c --puerto 9502 --par-host 127.0.0.1 --par-puerto 9501 --nombre C2 --sin-health

# Hit #6 — Nodo D (registro) y N nodos C con puerto aleatorio
python -m hit6.nodo_d --puerto 9600 --puerto-health 8086
python -m hit6.nodo_c --d-host 127.0.0.1 --d-puerto 9600 --nombre C1 --puerto-health 8081
python -m hit6.nodo_c --d-host 127.0.0.1 --d-puerto 9600 --nombre C2 --puerto-health 8082
curl http://127.0.0.1:8086/health

# Hit #7 — inscripciones por ventanas de 1 min, persistidas en JSON
python -m hit7.nodo_d --puerto 9700 --puerto-health 8087
python -m hit7.nodo_c --d-host 127.0.0.1 --d-puerto 9700 --nombre C1 --puerto-health 8081
curl http://127.0.0.1:8087/health
cat logs/inscripciones_hit7.json
```

## Pruebas

```bash
python -m unittest discover -s . -t . -v       # toda la suite
python -m unittest discover -s hit3 -t . -v    # sólo un hit
python -m unittest discover -s comun -t . -v   # sólo el módulo compartido
```

## Registros de actividad

Tres destinos a la vez: consola, buffer circular en memoria (últimos 500 registros) y
archivos rotativos en `logs/` (1 MB, 3 de respaldo). `logs/` no se versiona.

## Health check

Todos los servicios de larga vida —el B del Hit #3, los C de los Hits #4 a #7 y los D
de los Hits #6 y #7— exponen `GET /health` con JSON: estado, uptime y los contadores
propios de cada rol. Es el endpoint que se usa para verificar el despliegue.

Escucha en la **misma interfaz que el servicio** (`--host` / `TP1_HOST`): con el
default `127.0.0.1` sólo responde localmente, y publicarlo requiere `0.0.0.0`
explícito. Si el puerto está ocupado, el nodo lo registra y sigue funcionando sin el
endpoint. Cada nodo acepta `--puerto-health` y `--sin-health`; al correr varias
instancias en una misma máquina hay que darle a cada una su puerto.

## Integración continua

`.github/workflows/ci.yml`, en cada push y PR a `main`:

1. **gitleaks** — falla si detecta un secret hardcodeado.
2. **Pruebas** — `comun` y los Hits #1 a #7 sobre Python 3.11, 3.12 y 3.13.
3. **Prueba de humo** — levanta los procesos de verdad: A saluda a B; se mata a A con
   `kill -9` y B sigue respondiendo el health; dos C se saludan mutuamente; los
   mensajes viajan en JSON; tres C se descubren a través de D; y las inscripciones del
   Hit #7 quedan persistidas en disco.

## Seguridad

- No se versionan credenciales ni archivos `.env`: sólo la plantilla `.env.example`.
- No hay direcciones, puertos ni secrets hardcodeados: todo sale de variables de
  entorno con defaults de desarrollo.
- Las credenciales de despliegue se gestionan con GitHub Secrets y OIDC contra el
  proveedor de nube, sin claves estáticas en el repositorio.
- `gitleaks` corre en cada push y hace fallar el pipeline si detecta un secret.
