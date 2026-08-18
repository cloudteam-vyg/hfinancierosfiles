# syntax=docker/dockerfile:1.7
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=core.settings

# psycopg2-binary, cryptography y gevent publican wheels manylinux para
# glibc (Debian slim) -> no se necesita build-essential/gcc/libpq-dev.
# tzdata sí hace falta: TIME_ZONE="America/Mexico_City" requiere la base de
# datos IANA disponible en el contenedor.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# collectstatic corre en el BUILD, nunca en el "release:" del Procfile: los
# cambios de filesystem del release phase de Dokku no se persisten a los
# contenedores web/worker reales, así que el manifest de whitenoise nunca
# llegaría si se generara ahí. Los valores de entorno de abajo son solo
# para que Django pueda importar settings.py sin ImproperlyConfigured
# durante el build -- Dokku los sobreescribe en runtime con los reales.
RUN SECRET_KEY="build-time-placeholder" \
    DEBUG="False" \
    DATABASE_URL="postgres://build:build@localhost:5432/build" \
    ALLOWED_HOSTS="localhost" \
    python manage.py collectstatic --noinput --clear

# UID/GID FIJOS (10001): necesario para que el volumen persistente de Dokku
# (storage:mount en /app/media) funcione -- Dokku ya no hace chown automático
# para apps basadas en Dockerfile desde la 0.34.0, así que el número tiene
# que ser estable y conocido para poder hacer chown del lado del host.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/media \
    && chown -R app:app /app

USER app

# Si no hay EXPOSE, Dokku asume que la app escucha en el puerto 5000. Se
# deja explícito para fijar el contrato con el Procfile (--bind
# 0.0.0.0:${PORT:-5000}) -- Dokku no interpola ${PORT} en EXPOSE en build time.
EXPOSE 5000

# Sin CMD/ENTRYPOINT fijo a propósito: con Dockerfile + Procfile presentes,
# Dokku usa las líneas web/worker/release del Procfile para el comando real
# de cada proceso. Este CMD es solo un fallback para `docker run` suelto.
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:5000"]
