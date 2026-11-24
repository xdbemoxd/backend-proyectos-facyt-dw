# ---- ETAPA 1: Compilación (Builder) ----
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Solo lo necesario para compilar dependencias con extensiones en C.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --prefix=/install -r requirements.txt


# ---- ETAPA 2: Imagen final ----
FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENVIRONMENT=production

# libpq5: cliente de PostgreSQL en tiempo de ejecución.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos '' appuser

WORKDIR /app

COPY --from=builder /install /usr/local

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

# /health es público y no toca la BD. Sin curl: se usa Python.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:' + __import__('os').getenv('PORT','8000') + '/health', timeout=4).status == 200 else 1)"

# Render asigna el puerto en $PORT; en local se usa 8000.
CMD ["sh", "-c", "exec python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --no-server-header --proxy-headers"]
