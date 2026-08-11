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
    AZURE_ACCOUNT_NAME="build" \
    AZURE_ACCOUNT_KEY="build" \
    AZURE_CONTAINER_NAME="build" \
    CELERY_BROKER_URL="redis://localhost:6379/1" \
    CELERY_RESULT_BACKEND="redis://localhost:6379/1" \
    python manage.py collectstatic --noinput --clear

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app \
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
