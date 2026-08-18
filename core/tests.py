"""Pruebas de configuración de despliegue.

Cubren fallos que solo aparecen en producción (detrás del proxy de Dokku) y
que ninguna prueba funcional detectaría en local, donde se sirve por http.
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

# "testserver" lo inyecta el runner de Django en ALLOWED_HOSTS al correr las
# pruebas; no es un dominio real del despliegue.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "10.0.2.2", "[::1]", "testserver"}


class ProxyAndCsrfSettingsTests(TestCase):
    def test_proxy_ssl_header_is_configured(self):
        # Sin esto Django ve la petición como http (nginx termina el TLS y
        # reenvía en plano): is_secure() False, URLs absolutas en http y el
        # chequeo de Origin de CSRF comparando contra el esquema equivocado.
        self.assertEqual(
            settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https")
        )

    def test_every_public_host_has_an_https_trusted_origin(self):
        # Django >= 4.0 exige el origen CON esquema para aceptar un POST.
        for host in settings.ALLOWED_HOSTS:
            if host in LOCAL_HOSTS or host.startswith("."):
                continue
            self.assertIn(
                f"https://{host}",
                settings.CSRF_TRUSTED_ORIGINS,
                f"Falta https://{host} en CSRF_TRUSTED_ORIGINS: los formularios "
                f"POST servidos en ese dominio devolverían 403.",
            )


@override_settings(
    ALLOWED_HOSTS=["hfiles-dev.cloudteam.net"],
    CSRF_TRUSTED_ORIGINS=["https://hfiles-dev.cloudteam.net"],
)
class LoginCsrfBehindProxyTests(TestCase):
    """Reproduce el 403 real: POST del login con Origin https a través del
    proxy. El cliente va con enforce_csrf_checks para que el middleware de
    CSRF actúe de verdad (por defecto los tests lo desactivan)."""

    HOST = "hfiles-dev.cloudteam.net"

    def setUp(self):
        User.objects.create_user("csrf_user", password="clave-de-prueba")
        self.client = Client(enforce_csrf_checks=True)

    def test_login_post_with_https_origin_is_accepted(self):
        url = reverse("authentication:login")
        self.client.get(url, HTTP_HOST=self.HOST, secure=True)
        token = self.client.cookies["csrftoken"].value

        resp = self.client.post(
            url,
            {"username": "csrf_user", "password": "clave-de-prueba",
             "csrfmiddlewaretoken": token},
            HTTP_HOST=self.HOST,
            HTTP_ORIGIN=f"https://{self.HOST}",
            HTTP_X_FORWARDED_PROTO="https",
            secure=True,
        )
        self.assertNotEqual(resp.status_code, 403, "CSRF rechazó un login legítimo")
        self.assertEqual(resp.status_code, 302, "el login no redirigió tras autenticar")

    def test_login_post_from_foreign_origin_is_still_rejected(self):
        # La protección debe seguir viva: un origen ajeno tiene que fallar.
        url = reverse("authentication:login")
        self.client.get(url, HTTP_HOST=self.HOST, secure=True)
        token = self.client.cookies["csrftoken"].value

        resp = self.client.post(
            url,
            {"username": "csrf_user", "password": "clave-de-prueba",
             "csrfmiddlewaretoken": token},
            HTTP_HOST=self.HOST,
            HTTP_ORIGIN="https://sitio-atacante.example",
            HTTP_X_FORWARDED_PROTO="https",
            secure=True,
        )
        self.assertEqual(resp.status_code, 403)
