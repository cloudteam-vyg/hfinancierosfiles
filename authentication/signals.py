import logging

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

LEGACY_GROUP_NAME = "Usuarios estándar"

ROLE_GROUPS = ("Admin", "Colaborador", "Estandar", "Basico")
DEFAULT_ROLE_GROUP_NAME = "Estandar"

MANAGED_MODELS = (
    ("files", "customer"),
    ("files", "person"),
    ("files", "filearchive"),
    ("files", "archiveclass"),
    ("files", "classname"),
    ("files", "activitytype"),
    ("files", "persontype"),
)

# Matriz de permisos por rol -- nunca incluye "delete": ningún rol puede
# eliminar, sin excepción (los superusuarios de Django siguen sin verse
# afectados por esto, ya que ignoran has_perm por completo).
ROLE_PERMISSION_PREFIXES = {
    "Admin": ("view", "add", "change"),
    "Colaborador": ("view", "add", "change"),
    "Estandar": ("view", "add"),
    "Basico": ("view",),
}


def _permissions_for_prefixes(prefixes):
    """Resuelve los Permission de MANAGED_MODELS para los prefijos dados
    (p. ej. ("view", "add")). Devuelve None si algún ContentType/Permission
    todavía no existe (p. ej. las migraciones de `files` no han corrido) --
    quien llama debe abortar sin crear/actualizar nada a medias.
    """
    permissions = []
    for app_label, model_name in MANAGED_MODELS:
        try:
            content_type = ContentType.objects.get_by_natural_key(app_label, model_name)
        except ContentType.DoesNotExist:
            logger.warning(
                "_permissions_for_prefixes: ContentType %s.%s no existe todavía.",
                app_label, model_name,
            )
            return None

        for prefix in prefixes:
            codename = f"{prefix}_{model_name}"
            try:
                permissions.append(
                    Permission.objects.get(content_type=content_type, codename=codename)
                )
            except Permission.DoesNotExist:
                logger.warning(
                    "_permissions_for_prefixes: permiso %s.%s no existe todavía.",
                    app_label, codename,
                )
                return None

    return permissions


def ensure_role_groups():
    """Crea (si falta) los cuatro grupos de rol (ROLE_GROUPS) y fija el
    conjunto de permisos de cada uno de forma idempotente, según la matriz
    ROLE_PERMISSION_PREFIXES sobre los siete modelos de MANAGED_MODELS.
    Llamarlo N veces da siempre el mismo resultado, ya que usa .set() en vez
    de .add().

    Los tres catálogos de cliente (ClassName, ActivityType, PersonType)
    necesitan `add` en Estandar/Colaborador/Admin porque su única vía de alta
    es el modal de alta rápida del formulario de Cliente: sin el permiso, un
    usuario no podría crear el catálogo que su propio formulario le ofrece.

    Devuelve un dict {nombre_de_grupo: Group} si tuvo éxito, o None si algún
    rol no pudo resolver sus permisos todavía -- en ese caso no crea ningún
    grupo a medias (ni siquiera los otros tres), se reintenta más adelante
    (próximo post_save de usuario, próximo post_migrate, o el management
    command).
    """
    groups = {}
    for name in ROLE_GROUPS:
        permissions = _permissions_for_prefixes(ROLE_PERMISSION_PREFIXES[name])
        if permissions is None:
            return None
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set(permissions)
        groups[name] = group
    return groups


def migrate_legacy_group_members():
    """Migración de una sola vez (NO se conecta a post_migrate ni a ningún
    signal): mueve a los miembros del grupo retirado LEGACY_GROUP_NAME hacia
    "Estandar" y borra ese grupo si existe. También mueve a "Estandar" a
    cualquier usuario no-superusuario que no esté en ninguno de los
    ROLE_GROUPS (p. ej. creado por fixture/shell antes de este cambio), para
    que nadie quede más restringido que Basico por accidente.

    Solo debe invocarse explícitamente desde `setup_groups` -- borrar un
    Group automáticamente en cada post_migrate (incluidas las corridas de
    tests/CI) sería un efecto secundario sorpresivo para una limpieza que
    solo debe pasar una vez, en el deploy.

    Devuelve un dict con el resultado: {"migrated_users": [...],
    "deleted_legacy_group": bool}.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()

    groups = ensure_role_groups()
    if groups is None:
        return None

    estandar = groups["Estandar"]
    migrated_users = []

    legacy_group = Group.objects.filter(name=LEGACY_GROUP_NAME).first()
    if legacy_group is not None:
        for user in legacy_group.user_set.all():
            user.groups.add(estandar)
            migrated_users.append(user)
        legacy_group.delete()

    role_group_ids = {group.pk for group in groups.values()}
    orphaned_users = User.objects.filter(is_superuser=False).exclude(groups__pk__in=role_group_ids)
    for user in orphaned_users:
        user.groups.add(estandar)
        if user not in migrated_users:
            migrated_users.append(user)

    return {
        "migrated_users": migrated_users,
        "deleted_legacy_group": legacy_group is not None,
    }


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def assign_default_group_to_new_user(sender, instance, created, **kwargs):
    """Al crear un usuario NUEVO que no sea superusuario, lo asigna al grupo
    de rol por defecto ("Estandar"), creándolo/actualizándolo primero si es
    necesario. No hace nada en updates (created=False) ni retroactivamente
    sobre usuarios ya existentes -- ver management command setup_groups.
    """
    if not created or instance.is_superuser:
        return

    try:
        groups = ensure_role_groups()
    except DatabaseError:
        # Contexto típico: fixtures/tests tempranos donde las tablas de
        # auth/contenttypes aún no existen. No debe reventar la creación
        # del usuario.
        logger.exception("No se pudo asignar el grupo por defecto a %s", instance)
        return

    if groups is not None:
        instance.groups.add(groups[DEFAULT_ROLE_GROUP_NAME])
