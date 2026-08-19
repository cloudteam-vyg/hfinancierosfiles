import logging

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

STANDARD_GROUP_NAME = "Usuarios estándar"

MANAGED_MODELS = (
    ("files", "customer"),
    ("files", "person"),
    ("files", "filearchive"),
    ("files", "archiveclass"),
    ("files", "classname"),
    ("files", "activitytype"),
    ("files", "persontype"),
)
MANAGED_PERMISSION_PREFIXES = ("add", "change")  # explícitamente SIN "delete" ni "view"


def ensure_standard_group():
    """Crea (si falta) el grupo STANDARD_GROUP_NAME y fija su conjunto de
    permisos de forma idempotente (add/change sobre los siete modelos de
    MANAGED_MODELS -- Customer, Person, FileArchive, ArchiveClass, ClassName,
    ActivityType y PersonType --, nunca delete). Llamarlo N veces da siempre el
    mismo resultado, ya que usa .set() en vez de .add().

    Los tres catálogos de cliente (ClassName, ActivityType, PersonType)
    necesitan `add` porque su única vía de alta es el modal de alta rápida del
    formulario de Cliente: sin el permiso, un usuario estándar no podría crear
    el catálogo que su propio formulario le ofrece.

    Devuelve el Group si tuvo éxito, o None si algún ContentType/Permission
    todavía no existe (p. ej. las migraciones de `files` no han corrido) --
    en ese caso no crea el grupo a medias, se reintenta más adelante (próximo
    post_save de usuario, próximo post_migrate, o el management command).
    """
    permissions = []
    for app_label, model_name in MANAGED_MODELS:
        try:
            content_type = ContentType.objects.get_by_natural_key(app_label, model_name)
        except ContentType.DoesNotExist:
            logger.warning(
                "ensure_standard_group: ContentType %s.%s no existe todavía.",
                app_label, model_name,
            )
            return None

        for prefix in MANAGED_PERMISSION_PREFIXES:
            codename = f"{prefix}_{model_name}"
            try:
                permissions.append(
                    Permission.objects.get(content_type=content_type, codename=codename)
                )
            except Permission.DoesNotExist:
                logger.warning("ensure_standard_group: permiso %s.%s no existe todavía.", app_label, codename)
                return None

    group, _ = Group.objects.get_or_create(name=STANDARD_GROUP_NAME)
    group.permissions.set(permissions)
    return group


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def assign_standard_group_to_new_user(sender, instance, created, **kwargs):
    """Al crear un usuario NUEVO que no sea superusuario, lo asigna al
    grupo estándar (creándolo/actualizándolo primero si es necesario). No
    hace nada en updates (created=False) ni retroactivamente sobre usuarios
    ya existentes -- ver management command ensure_standard_group.
    """
    if not created or instance.is_superuser:
        return

    try:
        group = ensure_standard_group()
    except DatabaseError:
        # Contexto típico: fixtures/tests tempranos donde las tablas de
        # auth/contenttypes aún no existen. No debe reventar la creación
        # del usuario.
        logger.exception("No se pudo asignar el grupo estándar a %s", instance)
        return

    if group is not None:
        instance.groups.add(group)
