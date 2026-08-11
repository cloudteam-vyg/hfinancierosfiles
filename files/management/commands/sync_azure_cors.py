from django.conf import settings
from django.core.management.base import BaseCommand

from azure.storage.blob import CorsRule

from files import azure_client


class Command(BaseCommand):
    help = "Sincroniza las reglas CORS del servicio Blob de Azure (idempotente)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        rule = CorsRule(
            allowed_origins=list(settings.AZURE_CORS_ALLOWED_ORIGINS),
            allowed_methods=["PUT", "GET", "HEAD", "OPTIONS"],
            allowed_headers=["x-ms-*", "content-type"],
            exposed_headers=["x-ms-*", "etag", "content-md5"],
            max_age_in_seconds=3600,
        )
        self.stdout.write(f"Orígenes: {rule.allowed_origins}")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("dry-run: sin cambios."))
            return

        # Pasar solo `cors`: los demás elementos (logging, métricas,
        # retención) quedan como estén; el SDK preserva los None.
        azure_client.get_blob_service_client().set_service_properties(cors=[rule])
        self.stdout.write(self.style.SUCCESS("Reglas CORS aplicadas."))
