from django.core.management.base import BaseCommand

from authentication.signals import (
    DEFAULT_ROLE_GROUP_NAME,
    LEGACY_GROUP_NAME,
    ROLE_GROUPS,
    ensure_role_groups,
    migrate_legacy_group_members,
)


class Command(BaseCommand):
    help = (
        f"Crea/actualiza los {len(ROLE_GROUPS)} grupos de rol ({', '.join(ROLE_GROUPS)}) "
        "con su matriz de permisos view/add/change (nunca delete) sobre Customer, Person, "
        "FileArchive, ArchiveClass y PersonActivityType. Además migra a "
        f"'{DEFAULT_ROLE_GROUP_NAME}' a los miembros del grupo retirado '{LEGACY_GROUP_NAME}' "
        "(y lo elimina) y a cualquier usuario sin grupo de rol. Idempotente."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-legacy-migration",
            action="store_true",
            help="Solo crea/actualiza los 4 grupos de rol; no migra usuarios del grupo retirado.",
        )

    def handle(self, *args, **options):
        groups = ensure_role_groups()
        if groups is None:
            self.stderr.write(self.style.WARNING(
                "No se pudo crear/actualizar los grupos: algún ContentType/Permission "
                "no existe todavía. ¿Corriste `migrate` para la app `files`?"
            ))
            return

        for name, group in groups.items():
            codenames = ", ".join(p.codename for p in group.permissions.all())
            self.stdout.write(self.style.SUCCESS(f"Grupo '{name}' actualizado con: {codenames}"))

        if options["skip_legacy_migration"]:
            return

        result = migrate_legacy_group_members()
        if result is None:
            self.stderr.write(self.style.WARNING(
                "No se pudo migrar usuarios del grupo retirado: algún ContentType/Permission "
                "no existe todavía."
            ))
            return

        if not result["migrated_users"] and not result["deleted_legacy_group"]:
            self.stdout.write("No había nada que migrar del grupo retirado.")
            return

        for user in result["migrated_users"]:
            self.stdout.write(f"Usuario '{user}' asignado a '{DEFAULT_ROLE_GROUP_NAME}'.")
        if result["deleted_legacy_group"]:
            self.stdout.write(self.style.SUCCESS(f"Grupo retirado '{LEGACY_GROUP_NAME}' eliminado."))
