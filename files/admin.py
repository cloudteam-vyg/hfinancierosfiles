from django.contrib import admin, messages
from django.db import transaction
from django.template.defaultfilters import filesizeformat
from django.utils.html import format_html

from rangefilter.filters import DateTimeRangeFilter
from simple_history.admin import SimpleHistoryAdmin

from .forms import FileArchiveAdminForm
from .models import ClassName, ActivityType, Customer, Person, ArchiveClass, FileArchive
from .tasks import run_post_processing
from .uploads import stamp_upload_metadata

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name', 'group', 'email', 'phone_number', 'activity_type', 'date_of_constitution')
    search_fields = ('name', 'group', 'email', 'phone_number')
    list_filter = ('activity_type',)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ('name', 'position', 'email', 'phone_number', 'customer')
    search_fields = ('name', 'position', 'email', 'phone_number')
    list_filter = ('customer',)

STATUS_COLORS = {
    FileArchive.UploadStatus.PENDING: "#9CA3AF",
    FileArchive.UploadStatus.PROCESSING: "#F59E0B",
    FileArchive.UploadStatus.COMPLETED: "#16A34A",
    FileArchive.UploadStatus.ERROR: "#DC2626",
}

READONLY_ON_EDIT = (
    "original_filename", "file", "file_size", "content_type",
    "upload_status", "uploaded_by", "celery_task_id", "processed_at",
    "error_message", "error_traceback", "uploaded_at", "updated_at",
)


@admin.register(FileArchive)
class FileArchiveAdmin(SimpleHistoryAdmin):
    form = FileArchiveAdminForm
    list_display = ('name', 'customer', 'archive_class', 'status_badge', 'readable_size', 'uploaded_by', 'uploaded_at')
    list_filter = ('upload_status', 'archive_class', 'customer', 'uploaded_by', ('uploaded_at', DateTimeRangeFilter))
    search_fields = ('name', 'original_filename', 'customer__name')
    actions = ['retry_processing']

    def get_fields(self, request, obj=None):
        base = ["archive_class", "customer", "name", "opening_date", "due_date"]
        if obj is None:
            return base + ["file"]
        return base + list(READONLY_ON_EDIT)

    def get_readonly_fields(self, request, obj=None):
        return () if obj is None else READONLY_ON_EDIT

    def status_badge(self, obj):
        color = STATUS_COLORS.get(obj.upload_status, "#6B7280")
        return format_html(
            '<span style="background-color:{}; color:#fff; padding:2px 8px; '
            'border-radius:8px; font-size:11px; font-weight:600;">{}</span>',
            color, obj.get_upload_status_display(),
        )
    status_badge.short_description = "Estado"

    def readable_size(self, obj):
        return filesizeformat(obj.file_size) if obj.file_size else "-"
    readable_size.short_description = "Tamaño"

    def save_model(self, request, obj, form, change):
        if not change:
            # Misma regla que el frontend propio (files/views.py): el form ya
            # exige el archivo en alta, así que aquí siempre viene.
            uploaded = form.cleaned_data.get("file")
            if uploaded:
                stamp_upload_metadata(obj, uploaded, request.user)
            else:
                obj.uploaded_by = request.user
                obj.upload_status = FileArchive.UploadStatus.PENDING

        super().save_model(request, obj, form, change)

        if not change:
            # changeform_view envuelve el guardado en transaction.atomic;
            # encolar sin on_commit haría que el worker pudiera leer la fila
            # antes de que la transacción confirme -> DoesNotExist intermitente.
            transaction.on_commit(lambda: run_post_processing.delay(str(obj.pk)))
            self.message_user(request, f"«{obj.name}» subido. Post-procesamiento en curso.")

    @admin.action(description="Reintentar procesamiento (solo filas en Error)")
    def retry_processing(self, request, queryset):
        count = 0
        for obj in queryset.filter(upload_status=FileArchive.UploadStatus.ERROR):
            obj.upload_status = FileArchive.UploadStatus.PENDING
            obj.error_message = None
            obj.error_traceback = None
            obj.save(update_fields=["upload_status", "error_message", "error_traceback", "updated_at"])
            run_post_processing.delay(str(obj.pk))
            count += 1
        if count:
            self.message_user(request, f"{count} archivo(s) reencolado(s).")
        else:
            self.message_user(request, "Ninguna de las filas seleccionadas estaba en estado Error.", level=messages.WARNING)

@admin.register(ClassName)
class ClassNameAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(ActivityType)
class ActivityTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)




@admin.register(ArchiveClass)
class ArchiveClassAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

