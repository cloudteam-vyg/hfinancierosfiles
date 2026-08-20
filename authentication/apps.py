from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AuthenticationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'authentication'
    verbose_name = "Autenticación y permisos"

    def ready(self):
        from . import signals  # registra el receiver post_save de User

        def _ensure_groups_after_migrate(sender, **kwargs):
            signals.ensure_role_groups()

        post_migrate.connect(
            _ensure_groups_after_migrate,
            dispatch_uid="authentication.ensure_role_groups_post_migrate",
        )
