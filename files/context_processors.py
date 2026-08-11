from django.db.models import Sum

from .models import FileArchive


def storage_stats(request):
    """Expone el total real almacenado (GB) a todos los templates que
    extiendan app_base.html (mini-stat de la sidebar). No hay concepto de
    cuota en la app -- se muestra el total real, sin un límite ficticio.
    """
    if not request.user.is_authenticated:
        return {}
    total_bytes = FileArchive.objects.aggregate(total=Sum("file_size"))["total"] or 0
    return {"storage_used_gb": round(total_bytes / (1024 ** 3), 1)}
