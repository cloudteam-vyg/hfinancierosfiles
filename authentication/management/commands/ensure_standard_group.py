from django.core.management.base import BaseCommand

from authentication.signals import STANDARD_GROUP_NAME, ensure_standard_group


class Command(BaseCommand):
    help = (
        f"Crea (si falta) el grupo '{STANDARD_GROUP_NAME}' y fija sus permisos "
        "add/change sobre Customer, Person y FileArchive. Idempotente."
    )

    def handle(self, *args, **options):
        group = ensure_standard_group()
        if group is None:
            self.stderr.write(self.style.WARNING(
                "No se pudo crear/actualizar el grupo: algún ContentType/Permission "
                "no existe todavía. ¿Corriste `migrate` para la app `files`?"
            ))
            return
        codenames = ", ".join(p.codename for p in group.permissions.all())
        self.stdout.write(self.style.SUCCESS(f"Grupo '{group.name}' actualizado con: {codenames}"))
