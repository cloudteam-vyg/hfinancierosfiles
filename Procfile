web: gunicorn core.wsgi:application --bind 0.0.0.0:${PORT:-5000} --worker-class gevent --workers ${WEB_CONCURRENCY:-3} --timeout 60 --graceful-timeout 30 --access-logfile - --error-logfile - --log-level info
worker: celery -A core worker --loglevel=info --pool=gevent --concurrency=${CELERY_CONCURRENCY:-10}
release: python manage.py migrate --noinput
