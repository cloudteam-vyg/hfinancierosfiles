web: gunicorn core.wsgi:application --bind 0.0.0.0:5000 --worker-class gevent --workers 3 --timeout 300 --graceful-timeout 30 --access-logfile - --error-logfile - --log-level info
worker: celery -A core worker --loglevel=info --pool=gevent --concurrency=10
release: python manage.py migrate --noinput