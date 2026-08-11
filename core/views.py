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
    status_breakdown = list(
        FileArchive.objects.values('upload_status')
        .annotate(total=Count('id')).order_by('upload_status')
    )
    total_files = sum(row['total'] for row in status_breakdown)
    by_status = {row['upload_status']: row['total'] for row in status_breakdown}

    counts_by_day = dict(
        FileArchive.objects.filter(uploaded_at__gte=now - datetime.timedelta(days=UPLOADS_CHART_DAYS - 1))
        .annotate(day=TruncDate('uploaded_at'))
        .values('day')
        .annotate(total=Count('id'))
        .values_list('day', 'total')
    )
    today = now.date()
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
        'total_files': total_files,
        'processing_count': by_status.get(FileArchive.UploadStatus.PROCESSING, 0),
        'error_last_24h': FileArchive.objects.filter(
            upload_status=FileArchive.UploadStatus.ERROR,
            updated_at__gte=now - datetime.timedelta(hours=24),
        ).count(),
        'status_breakdown': [
            {**row, 'pct': round(row['total'] / total_files * 100) if total_files else 0}
            for row in status_breakdown
        ],
        'uploads_by_day': uploads_by_day,
    }
    return render(request, 'dashboard.html', context)
