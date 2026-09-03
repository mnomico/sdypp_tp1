# Hit #5 — Mensajes en formato JSON

> Modifiquen el programa C para que los mensajes se envíen en formato JSON,
> serializando y deserializando al enviar/recibir.

## Arquitectura

Mismo nodo bidireccional del Hit #4; cambia **qué** viaja por los canales.

```mermaid
sequenceDiagram
    participant C1 as Nodo C1
    participant C2 as Nodo C2
    Note over C1: crear_saludo("C1") → dict
    Note over C1: serializar() → '{"tipo":"saludo",...}'
    C1->>C2: línea JSON + "\n"
    Note over C2: deserializar() → dict + validación
    Note over C2: crear_respuesta("C2", saludo) → dict
    Note over C2: serializar() → '{"tipo":"respuesta",...}'
    C2->>C1: línea JSON + "\n"
    Note over C1: deserializar() → dict + correlaciona por id
```

La serialización vive en [`comun/mensajes.py`](../comun/mensajes.py), separada del
transporte ([`comun/protocolo.py`](../comun/protocolo.py)), que sólo delimita.

### Formato

```json
{"version":1,"id":"95ae61fd-…","tipo":"saludo","origen":"C-externo",
 "contenido":"Hola, soy C-externo","timestamp":"2026-08-23T22:49:17.558685+00:00"}

{"version":1,"id":"674d113f-…","tipo":"respuesta","origen":"C1",
 "contenido":"Hola C-externo, soy C1. Recibi tu saludo.",
 "en_respuesta_a":"95ae61fd-…","timestamp":"2026-08-23T22:49:17.559027+00:00"}
```

| Campo | Para qué |
|---|---|
| `version` | Permite evolucionar el protocolo sin romper nodos viejos |
| `id` | Identificador único del mensaje (UUID4) |
| `tipo` | En este hit, `saludo` o `respuesta`. El módulo define además los tipos de registro y consulta que usan los Hits #6 y #7 |
| `origen` | Quién lo envía, sin recortar cadenas de texto |
| `contenido` | El saludo legible |
| `en_respuesta_a` | Correlaciona la respuesta con su saludo |
| `timestamp` | Instante de creación en UTC (ISO 8601) |

## Ejecución

```bash
python -m hit5.nodo_c --puerto 9501 --par-host 127.0.0.1 --par-puerto 9502 \
                      --nombre C1 --puerto-health 8501
python -m hit5.nodo_c --puerto 9502 --par-host 127.0.0.1 --par-puerto 9501 \
                      --nombre C2 --sin-health
```

```
INFO [hit5.nodo_c] [entrante] saludo de C2: Hola, soy C2
INFO [hit5.nodo_c] [saliente] saludo enviado (id=6a695fb7-…, 161 bytes): Hola, soy C1
INFO [hit5.nodo_c] [saliente] respuesta de C2: Hola C1, soy C2. Recibi tu saludo.
```

Para ver el JSON crudo que viaja por el socket:

```bash
python -c "
import socket
from comun import mensajes
from comun.protocolo import LectorDeMensajes, enviar_mensaje
s = socket.create_connection(('127.0.0.1', 9501), timeout=5)
enviar_mensaje(s, mensajes.serializar(mensajes.crear_saludo('C-externo')))
print(LectorDeMensajes(s).leer_mensaje())
"
```

Mismos parámetros que el Hit #4 (`TP1_PUERTO_HIT5` por entorno). El `/health` agrega
`formato_mensajes`, `mensajes_invalidos` (JSON mal formado o bytes ilegibles) y
`mensajes_ignorados` (mensajes válidos de un tipo que este nodo no responde).

## Decisiones de diseño

- **JSON Lines: un objeto por línea.** Se conserva el delimitador `\n` del Hit #1 en vez de inventar un framing nuevo. Es seguro porque `json.dumps` escapa los saltos de línea del contenido y nunca emite uno literal — hay un test que lo fija. La alternativa (prefijo de longitud) sería más eficiente pero ilegible al depurar con `tcpdump` o `nc`.
- **Serialización separada del transporte.** `mensajes.py` no conoce sockets y `protocolo.py` no conoce JSON: las pruebas del formato son unitarias y puras, y el Hit #8 podrá reemplazar la serialización por Protobuf sin tocar el framing.
- **Validar al deserializar, no confiar.** Se verifica que sea JSON válido, que sea un objeto y que tenga `tipo`, `origen` y `contenido`. Lo que no pasa se cuenta en el `/health` y **se descarta sin cortar el canal**: el emisor puede ser un nodo viejo o defectuoso, y eso no debe voltear al receptor.
- **Los bytes ilegibles cuentan como mensaje inválido.** Antes, una línea que no era UTF-8 levantaba un `UnicodeDecodeError` que hereda de `ValueError` y por lo tanto se escapaba de todos los `except`: mataba el hilo con un traceback fuera del log y cortaba el canal, justo lo contrario de lo que promete el punto anterior. Ahora el lector lo traduce a `MensajeIlegible` y se trata como cualquier otro mensaje mal formado.
- **Sólo se responden los mensajes de tipo `saludo`.** Con más de un tipo en el protocolo, contestar a todo hacía que una `respuesta` ajena se contara como saludo recibido y falseaba las métricas.
- **`ensure_ascii=False` + UTF-8:** mantiene los acentos legibles en el cable en vez de inflarlos a `\uXXXX`, que además ocuparía el triple de bytes.
- **`separators=(",", ":")`:** sin espacios superfluos, que son bytes que se pagan en cada mensaje.
- **`id` y `en_respuesta_a`:** correlacionar respuestas con solicitudes es lo que permite tener varias en vuelo por el mismo canal, en vez de asumir que la siguiente respuesta corresponde al último saludo.
- **`version` desde el inicio:** agregarlo después obliga a soportar mensajes sin él.
- **La ganancia sobre el Hit #4.** Antes la respuesta se armaba pegando texto (`"Recibi tu saludo: " + saludo`); ahora se lee `saludo["origen"]`. El receptor entiende **campos**, no una cadena que habría que parsear.
- **`tamano_en_bytes()`** deja medido el costo del formato para la comparación JSON vs Protobuf del Hit #8 (este saludo: 161 bytes).

## Pruebas

```bash
python -m unittest discover -s hit5 -t . -v
```

- **Formato (unitarias):** campos obligatorios, unicidad de `id`, correlación por `en_respuesta_a`, ida y vuelta sin pérdida, una sola línea aun con saltos en el contenido, acentos sin escapar, rechazo de JSON mal formado / que no es objeto / sin campos obligatorios.
- **Integración:** dos nodos se saludan en JSON · se inspecciona el **JSON crudo del socket** · un mensaje inválido no tumba el nodo · **bytes ilegibles se cuentan y el canal sigue vivo** · **sólo se responde a los `saludo`** · dos mensajes pegados en un mismo segmento TCP se separan bien.
