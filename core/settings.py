"""Configuración de Django para HFinancieros Files.

Todo lo sensible o dependiente del entorno se lee del archivo .env vía
django-environ (ver .env.example). Referencia de settings:
https://docs.djangoproject.com/en/4.2/ref/settings/
"""

from pathlib import Path
import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
environ.Env.read_env(BASE_DIR / ".env")
env = environ.Env()


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env.bool("DEBUG", default=False)

# Se lee del entorno para que el servidor mande (Dokku ya tiene ALLOWED_HOSTS
# configurado). Hardcodearlo obliga a un redeploy por cada dominio nuevo.
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=["hfiles-dev.cloudteam.net", "localhost", "127.0.0.1"],
)

# --- Detrás del proxy de Dokku (nginx termina TLS) --------------------------
# nginx recibe la petición por https y la reenvía a gunicorn por http plano.
# Sin esto Django cree que la conexión es insegura: request.is_secure() sería
# False, las URLs absolutas saldrían con http:// y --el síntoma que se ve
# primero-- el chequeo de Origin de CSRF compararía contra
# "http://<host>" mientras el navegador envía "https://<host>" -> 403.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Django >= 4.0 valida el header Origin en toda petición POST y exige que el
# origen incluya el ESQUEMA. Se derivan de ALLOWED_HOSTS para no mantener la
# misma lista de dominios en dos sitios; se omiten los de desarrollo, que
# viajan por http y ya casan con el origen calculado por Django.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "10.0.2.2", "[::1]"}
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[f"https://{host}" for host in ALLOWED_HOSTS
             if host not in _LOCAL_HOSTS and not host.startswith(".")],
)

# Cookies solo por https en producción (el sitio va con HSTS activo). En
# desarrollo se sirve por http, así que se dejan normales.
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# --- Autenticación (frontend fuera del Admin) ---
LOGIN_URL = 'authentication:login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'authentication:login'



# Application definition

INSTALLED_APPS = [
    'admin_interface',
    'rangefilter',
    'colorfield',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'simple_history',
    'files',
    'authentication',

]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'simple_history.middleware.HistoryRequestMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'files.context_processors.storage_stats',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    "default": env.db(),
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/
LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'America/Mexico_City'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}


# --- Media (almacenamiento local, ver files/models.py::file_archive_upload_to) ---
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Subida multipart al servidor (files/views.py::file_archive_upload_view) ---
MAX_UPLOAD_SIZE_MB = env.int("MAX_UPLOAD_SIZE_MB", default=300)

# OJO: DATA_UPLOAD_MAX_MEMORY_SIZE **no** limita el tamaño de los archivos
# subidos -- Django excluye de esa cuenta los datos de un file upload y solo
# mide el resto del cuerpo (los campos de texto). Tenerlo en cientos de MB no
# permitía subir más: solo desactivaba la protección contra un POST enorme sin
# archivos. El tope real de archivo lo aplica validate_upload_size
# (MAX_UPLOAD_SIZE_MB) y, antes que él, client-max-body-size de nginx.
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024   # 5 MB de campos de formulario

# Por encima de esto Django derrama el archivo a un temporal en disco en vez
# de mantenerlo en memoria. Este sí conviene alto: evita escribir en disco los
# adjuntos pequeños, que son la mayoría.
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # 10 MB

# Sin Redis ni Celery: la subida es síncrona (el archivo queda disponible al
# guardarse) y no hay trabajo en segundo plano que encolar. El cache queda en
# el backend local en memoria que Django trae por defecto, que basta porque
# nada de la app cachea; las sesiones viven en la base de datos.
