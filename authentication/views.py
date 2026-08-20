from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from authentication.signals import MANAGED_MODELS

# Orden de prioridad para elegir un solo rol a mostrar cuando un usuario
# está en más de uno de los 4 grupos (Django permite pertenecer a varios).
ROLE_PRIORITY = ("Admin", "Colaborador", "Estandar", "Basico")

PERMISSION_PREFIXES = ("view", "add", "change")


def _role_for(user):
    if user.is_superuser:
        return None
    names = set(user.groups.values_list("name", flat=True))
    for candidate in ROLE_PRIORITY:
        if candidate in names:
            return candidate
    return None


@login_required
def me_view(request):
    """Espejo en JSON de lo que los templates ya calculan con `perms`: rol
    activo (el de mayor prioridad si el usuario está en más de un grupo) y,
    por cada modelo gestionado, qué puede ver/crear/editar. No usa DRF -- es
    una vista de función plana, igual que los endpoints quick-create de
    `files/views.py`.
    """
    user = request.user
    permissions = {
        model_name: {
            prefix: user.has_perm(f"{app_label}.{prefix}_{model_name}")
            for prefix in PERMISSION_PREFIXES
        }
        for app_label, model_name in MANAGED_MODELS
    }
    return JsonResponse({
        "username": user.get_username(),
        "is_superuser": user.is_superuser,
        "role": _role_for(user),
        "permissions": permissions,
    })
