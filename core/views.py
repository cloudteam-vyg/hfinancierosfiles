import datetime

from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from files.models import Customer, FileArchive, Person

UPLOADS_CHART_DAYS = 14


def healthz(request):
    """Usado por CHECKS de Dokku para zero-downtime deploys."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok")


@login_required
def dashboard(request):
    now = timezone.now()
    today = now.date()
    # El estado de procesamiento ya no se muestra en ninguna parte (la subida
    # es síncrona: el archivo queda disponible al instante). En su lugar el
    # panel resume lo que sí importa del expediente: vencimientos.
    expiring_soon = FileArchive.objects.filter(
        due_date__gte=today, due_date__lte=today + datetime.timedelta(days=30),
    ).count()
    expired = FileArchive.objects.filter(due_date__lt=today).count()
    upcoming = list(
        FileArchive.objects.filter(due_date__gte=today)
        .select_related("customer").order_by("due_date")[:6]
    )

    counts_by_day = dict(
        FileArchive.objects.filter(uploaded_at__gte=now - datetime.timedelta(days=UPLOADS_CHART_DAYS - 1))
        .annotate(day=TruncDate('uploaded_at'))
        .values('day')
        .annotate(total=Count('id'))
        .values_list('day', 'total')
    )
    days = [today - datetime.timedelta(days=offset) for offset in range(UPLOADS_CHART_DAYS - 1, -1, -1)]
    max_count = max(counts_by_day.values(), default=0) or 1
    uploads_by_day = [
        {
            'label': day.strftime('%-d %b') if hasattr(day, 'strftime') else str(day),
            'count': counts_by_day.get(day, 0),
            'height_pct': round(counts_by_day.get(day, 0) / max_count * 100),
            'is_today': day == today,
        }
        for day in days
    ]

    context = {
        'total_customers': Customer.objects.count(),
        'total_persons': Person.objects.count(),
        'total_files': FileArchive.objects.count(),
        'expiring_soon': expiring_soon,
        'expired': expired,
        'upcoming': upcoming,
        'uploads_by_day': uploads_by_day,
    }
    return render(request, 'dashboard.html', context)
