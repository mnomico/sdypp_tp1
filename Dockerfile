# Imagen única del TP1: trae todos los hits, y por defecto levanta el despliegue
# público (Nodo D del Hit #6 + nodos C, ver despliegue/arranque.py).
#
#   docker build -t sdypp-tp1 .
#   docker run --rm -p 8080:8080 sdypp-tp1                  # /health en http://localhost:8080/health
#   docker run --rm sdypp-tp1 python -m hit1.servidor_b     # cualquier otro hit, misma imagen

# --- Etapa 1: dependencias (gRPC trae wheels binarias; se instalan una sola vez) ---
FROM python:3.13-slim AS constructor

WORKDIR /app
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# --- Etapa 2: imagen final, sin pip ni herramientas de compilación ---
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Commit que trae la imagen: el pipeline lo pasa con --build-arg y el Nodo D lo muestra
# en su nombre (NodoD-nube@<sha>), que es como se verifica qué versión está sirviendo.
ARG TP1_VERSION=""
ENV TP1_VERSION=$TP1_VERSION
LABEL org.opencontainers.image.source="https://github.com/mnomico/sdypp_tp1" \
      org.opencontainers.image.description="TP1 Sistemas Distribuidos (UNLu) - Grupo Cerberus" \
      org.opencontainers.image.revision="$TP1_VERSION"

# Usuario sin privilegios: el proceso nunca corre como root dentro del contenedor.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin tp1

WORKDIR /app
COPY --from=constructor /opt/venv /opt/venv
COPY --chown=tp1:tp1 . .
# Los logs en disco (comun/registro.py) van a /app/logs; tiene que poder escribirlo tp1.
RUN mkdir -p logs && chown tp1:tp1 logs

USER tp1

# Puerto HTTP del /health (variable PORT; el arranque la lee, default 8080).
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -m despliegue.arranque --verificar

CMD ["python", "-m", "despliegue.arranque"]
