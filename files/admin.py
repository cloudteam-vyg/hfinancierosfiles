import json
import uuid
from django.conf import settings
from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.template.defaultfilters import filesizeformat
from django.urls import path
from django.utils.html import format_html

from rangefilter.filters import DateTimeRangeFilter
from simple_history.admin import SimpleHistoryAdmin

from . import azure_client
from .forms import FileArchiveAdminForm
from .models import ClassName, ActivityType, Customer, Person, ArchiveClass, FileArchive
from .tasks import run_post_processing

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
    "original_filename", "blob_path", "file_size", "content_type",
    "upload_status", "uploaded_by", "celery_task_id", "processed_at",
    "error_message", "error_traceback", "uploaded_at", "updated_at",
)


@admin.register(FileArchive)
class FileArchiveAdmin(SimpleHistoryAdmin):
    form = FileArchiveAdminForm
    list_display = ('name', 'customer', 'archive_class', 'status_badge', 'readable_size', 'uploaded_by', 'uploaded_at')
    list_filter = ('upload_status', 'archive_class', 'customer', 'uploaded_by', ('uploaded_at', DateTimeRangeFilter))
    search_fields = ('name', 'original_filename', 'customer__name')
    actions = ['reintentar_procesamiento']

    def get_urls(self):
        # Debe ir ANTES de super(): el patrón <path:object_id>/ del Admin
        # se tragaría "upload-sas/" si se agregara después.
        custom = [
            path(
                "upload-sas/",
                self.admin_site.admin_view(self.upload_sas_view),
                name="files_filearchive_upload_sas",
            ),
        ]
        return custom + super().get_urls()

    def upload_sas_view(self, request):
        """Emite una SAS de escritura para un blob nuevo (o la renueva a
        mitad de una subida en curso). admin_view() ya garantiza
        is_active + is_staff; el permiso de modelo se exige explícitamente
        abajo porque admin_view() NO lo comprueba."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        if not request.user.has_perm("files.add_filearchive"):
            return JsonResponse({"error": "No tienes permiso para dar de alta archivos."}, status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({"error": "Cuerpo JSON inválido."}, status=400)

        filename = (payload.get("filename") or "").strip()[:255]
        try:
            size = int(payload.get("size"))
        except (TypeError, ValueError):
            return JsonResponse({"error": "El campo 'size' es inválido."}, status=400)

        if not filename:
            return JsonResponse({"error": "Falta el nombre del archivo."}, status=400)
        if size <= 0:
            return JsonResponse({"error": "El archivo está vacío."}, status=400)

        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if size > max_bytes:
            return JsonResponse(
                {"error": f"El archivo supera el límite de {settings.MAX_UPLOAD_SIZE_MB} MB."},
                status=400,
            )

        renew_path = (payload.get("renew_blob_path") or "").strip()
        if renew_path:
            # Renovación de una SAS caducada a mitad de subida: solo se
            # re-firma una ruta con el formato canónico de este mismo
            # endpoint, y solo si todavía no hay ningún registro con ese id.
            try:
                file_archive_id = uuid.UUID(payload.get("renew_file_archive_id", ""))
            except (TypeError, ValueError):
                return JsonResponse({"error": "file_archive_id no válido."}, status=400)
            if (not azure_client.BLOB_PATH_RE.match(renew_path)
                    or renew_path != azure_client.build_blob_path(file_archive_id, filename)
                    or FileArchive.objects.filter(pk=file_archive_id).exists()):
                return JsonResponse({"error": "Solicitud de renovación inválida."}, status=400)
            blob_path = renew_path
        else:
            # El UUID se acuña AQUÍ, no en el modelo: así el blob_path queda
            # fijado antes de que exista ninguna fila en la base de datos.
            file_archive_id = uuid.uuid4()
            blob_path = azure_client.build_blob_path(file_archive_id, filename)

        upload_url, expiry = azure_client.build_upload_sas_url(blob_path)
        return JsonResponse({
            "file_archive_id": str(file_archive_id),
            "blob_path": blob_path,
            "upload_url": upload_url,
            "sas_expiry": expiry.isoformat(),
            "block_size": settings.AZURE_UPLOAD_BLOCK_SIZE_MB * 1024 * 1024,
            "max_concurrency": settings.AZURE_UPLOAD_CONCURRENCY,
        })

    def get_fields(self, request, obj=None):
        # OJO: ModelAdmin.get_form() reconstruye el Meta.fields del form a
        # partir de ESTA lista (modelform_factory), sin fusionarlo con el
        # Meta.fields que ya trae FileArchiveAdminForm -- por eso hay que
        # listar aquí también los campos ocultos que llena el JS
        # (original_filename/blob_path/file_size/content_type), o
        # modelform_factory los excluye del form aunque estén declarados
        # como HiddenInput en FileArchiveAdminForm.Meta.
        base = ["archive_class", "customer", "name", "opening_date", "due_date"]
        if obj is None:
            return base + [
                "upload_widget", "file_archive_id",
                "original_filename", "blob_path", "file_size", "content_type",
            ]
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
            # Sustituye el UUID que puso default=uuid.uuid4 por el que acuñó
            # upload_sas_view() y que ya da nombre al blob real en Azure.
            obj.pk = form.cleaned_data["file_archive_id"]

            props = getattr(form, "_azure_props", None)
            if props is not None:
                # La fuente de verdad es Azure, no lo que declaró el cliente.
                obj.file_size = props.size
                if props.content_settings and props.content_settings.content_type:
                    obj.content_type = props.content_settings.content_type

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
    def reintentar_procesamiento(self, request, queryset):
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

