from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "authentication"

urlpatterns = [
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="authentication/login.html",
            # Sin esto el template caía siempre a su |default:300 y anunciaba
            # 300 MB aunque MAX_UPLOAD_SIZE_MB dijera otra cosa (la página de
            # login no pasa por el context processor de la app).
            extra_context={"max_upload_size_mb": settings.MAX_UPLOAD_SIZE_MB},
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Mismo prefijo `api/` que usan los endpoints quick-create de
    # files/urls.py -- sin colisión, pero conviene no duplicar rutas ahí.
    path("api/me/", views.me_view, name="me"),
]
