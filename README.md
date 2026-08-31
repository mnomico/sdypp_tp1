# Sistemas Distribuidos y Programación Paralela - TP 1

## Nombre del grupo: Cerberus

### Integrantes:

- Salvador Baez

- Mateo Nomico

- Tomás Resnik

## Lenguaje de programación: Python

Requiere **Python 3.11 o superior**. No se usan dependencias externas: todo se
resuelve con la biblioteca estándar (`socket`, `threading`, `logging`,
`http.server`, `unittest`), por lo que `pip install -r requirements.txt` no
instala nada en los Hits #1 a #3.

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

Cada carpeta tiene su propio `README.md` con el diagrama de arquitectura, las
instrucciones de ejecución y las decisiones de diseño.

## Estructura

Cada hit es una carpeta autocontenida: código, `README.md` y sus propias pruebas.

```
.
├── comun/               # Código compartido entre hits
│   ├── protocolo.py     # Delimitación de mensajes sobre el flujo TCP
│   ├── registro.py      # Logs en memoria y disco
│   ├── health.py        # Endpoint HTTP /health
│   ├── config.py        # Variables de entorno / .env
│   ├── mensajes.py      # Serialización JSON (desde el Hit #5)
│   └── tests/
├── hit1/                # Cliente y servidor TCP básicos
│   ├── servidor_b.py
│   ├── cliente_a.py
│   ├── tests/
│   └── README.md
├── hit2/                # Reconexión automática del cliente
│   └── ... (misma estructura)
├── hit3/                # Servidor persistente + health check
│   └── ... (misma estructura)
├── hit4/                # Nodo C: cliente y servidor simultáneos
│   ├── nodo_c.py
│   ├── tests/
│   └── README.md
├── hit5/                # Nodo C con mensajes JSON
│   └── ... (misma estructura)
├── hit6/                # Nodo D como registro de contactos y puerto aleatorio
│   ├── nodo_d.py
│   ├── nodo_c.py
│   ├── tests/
│   └── README.md
├── hit7/                # Sistema de inscripciones por ventanas de 1 min
│   ├── nodo_d.py
│   ├── nodo_c.py
│   ├── tests/
│   └── README.md
├── .env.example         # Plantilla de configuración (el .env real no se versiona)
├── requirements.txt
└── .github/workflows/ci.yml
```

## Configuración

Los parámetros se resuelven con la precedencia **argumento de línea de comandos >
variable de entorno > valor por defecto**, así que el proyecto corre sin configurar
nada. Para fijar valores propios:

```bash
cp .env.example .env      # editar a gusto; .env no se versiona
```

| Variable | Descripción | Default |
|---|---|---|
| `TP1_HOST` | Dirección de escucha (`0.0.0.0` al desplegar) | `127.0.0.1` |
| `TP1_PUERTO_HIT1` … `HIT5` | Puerto TCP de cada hit | `9001` … `9005` |
| `TP1_PUERTO_HEALTH` | Puerto del endpoint `/health` | `8080` |
| `TP1_SALUDO` | Mensaje que A envía a B | `Hola B, soy A` |
| `TP1_TIMEOUT` | Timeout de socket (s) | `5.0` |
| `TP1_ESPERA_INICIAL` / `TP1_ESPERA_MAXIMA` | Backoff de reintentos (s) | `0.5` / `5.0` |

No hay direcciones ni credenciales hardcodeadas. En la nube, los valores se
inyectan como variables de entorno y los secrets vienen de GitHub Secrets /
Secret Manager, nunca de un archivo versionado.

## Ejecución rápida

Todos los programas se ejecutan desde la raíz del repositorio, sin IDE:

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

# Hit #6 — Nodo D (registro) y múltiples nodos C con puerto aleatorio
python -m hit6.nodo_d --puerto 9600 --puerto-health 8086
python -m hit6.nodo_c --d-host 127.0.0.1 --d-puerto 9600 --nombre C1
python -m hit6.nodo_c --d-host 127.0.0.1 --d-puerto 9600 --nombre C2
python -m hit6.nodo_c --d-host 127.0.0.1 --d-puerto 9600 --nombre C3
curl http://127.0.0.1:8086/health

# Hit #7 — Sistema de inscripciones por ventanas (1 min) y persistencia JSON
python -m hit7.nodo_d --puerto 9700 --puerto-health 8087
python -m hit7.nodo_c --d-host 127.0.0.1 --d-puerto 9700 --nombre C1
python -m hit7.nodo_c --d-host 127.0.0.1 --d-puerto 9700 --nombre C2
curl http://127.0.0.1:8087/health
cat logs/inscripciones_hit7.json
```

Cada programa acepta `--help` con el detalle de sus parámetros.

## Pruebas

Las pruebas de cada hit viven en su propia carpeta (`hit1/tests/`, `hit2/tests/`, …).
Siempre se ejecutan desde la raíz del repositorio:

```bash
python -m unittest discover -s . -t . -v       # toda la suite
python -m unittest discover -s hit3 -t . -v    # sólo un hit
python -m unittest discover -s comun -t . -v   # sólo el módulo compartido
```

## Registros de actividad

Los servicios escriben en tres destinos a la vez: consola, un buffer circular en
memoria (últimos 500 registros) y archivos rotativos en `logs/` (1 MB por archivo,
3 de respaldo). El directorio `logs/` está excluido del control de versiones.

## Health check

El nodo persistente del Hit #3 expone `GET /health` devolviendo JSON con el estado
del servicio, su uptime y los contadores de conexiones. Es el endpoint que se usa
para verificar el despliegue.

## Integración continua

El pipeline de GitHub Actions (`.github/workflows/ci.yml`) corre en cada push y
pull request a `main`:

1. **gitleaks** — escanea el repositorio y falla si detecta un secret hardcodeado.
2. **Pruebas** — la suite completa sobre Python 3.11, 3.12 y 3.13.
3. **Prueba de humo** — levanta los procesos de verdad, mata a A con `kill -9` y
   verifica que B siga respondiendo el health check.

## Seguridad

- No se versionan credenciales ni archivos `.env`: sólo la plantilla `.env.example`
  con valores de ejemplo (ver `.gitignore`).
- No hay direcciones, puertos ni secrets hardcodeados: todo sale de variables de
  entorno con valores por defecto de desarrollo.
- Las credenciales de despliegue se gestionan con GitHub Secrets y autenticación
  vía OIDC contra el proveedor de nube, sin claves estáticas en el repositorio.
- `gitleaks` corre en cada push y hace fallar el pipeline si detecta un secret.
