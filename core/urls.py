"""Rutas raíz del proyecto. El detalle de cada app vive en su propio urls.py."""
from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('healthz/', views.healthz, name='healthz'),  # usado por CHECKS de Dokku
    path('admin/', admin.site.urls),
    path('', include('authentication.urls')),  # /login/, /logout/
    path('', include('files.urls')),           # /archivos/, /clientes/, /personas/, /api/
]
