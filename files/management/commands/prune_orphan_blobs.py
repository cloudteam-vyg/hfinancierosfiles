from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.core.management.base import BaseCommand

from files import azure_client
from files.models import FileArchive


class Command(BaseCommand):
    help = (
        "Borra blobs commiteados en Azure sin fila correspondiente en FileArchive "
        "(subida completa pero nunca se dio click en Guardar). Los bloques sin "
        "commitear por una pestaña cerrada a medio subir los recolecta Azure solo "
        "a los 7 días -- este comando no puede ni necesita tocarlos."
    )

    def add_arguments(self, parser):
        parser.add_argument("--older-than-hours", type=int, default=24)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=options["older_than_hours"])
        container = azure_client.get_blob_service_client().get_container_client(
            settings.AZURE_CONTAINER_NAME
        )
        known = set(FileArchive.objects.values_list("blob_path", flat=True))

        removed = 0
        prefix = f"{settings.AZURE_BLOB_PREFIX}/"
        for blob in container.list_blobs(name_starts_with=prefix):
            if blob.name in known:
                continue
            if blob.creation_time and blob.creation_time > cutoff:
                continue  # margen para subidas en curso
            self.stdout.write(f"huérfano: {blob.name} ({blob.size} bytes)")
            if not options["dry_run"]:
                azure_client.delete_blob_quietly(blob.name)
            removed += 1

        self.stdout.write(self.style.SUCCESS(f"{removed} blob(s) huérfano(s)"))
