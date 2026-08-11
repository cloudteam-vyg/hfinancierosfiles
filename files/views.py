import json
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from . import azure_client
from .frontend_forms import FileArchiveUploadForm
from .models import Customer, FileArchive, Person
from .tasks import run_post_processing

# =============================================================================
# Cliente (Customer)
# =============================================================================

CUSTOMER_FIELDS = (
    "classname", "name", "group", "email", "phone_number", "address",
    "country", "activity_type", "date_of_constitution", "web_site", "word_clave",
)


class CustomerListView(LoginRequiredMixin, ListView):
    model = Customer
    template_name = "files/customer_list.html"
    context_object_name = "customers"
    paginate_by = 25
    ordering = ("name",)
    # Sin permission_required a propósito: el grupo "Usuarios estándar" solo
    # tiene add/change (ver authentication/signals.py), no view_customer.
    # Cualquier usuario autenticado puede listar.


class CustomerCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Customer
    fields = CUSTOMER_FIELDS
    template_name = "files/customer_form.html"
    permission_required = "files.add_customer"
    success_url = reverse_lazy("files:customer-list")


class CustomerUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Customer
    fields = CUSTOMER_FIELDS
    template_name = "files/customer_form.html"
    permission_required = "files.change_customer"
    success_url = reverse_lazy("files:customer-list")


class CustomerDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Customer
    template_name = "files/customer_confirm_delete.html"
    permission_required = "files.delete_customer"
    success_url = reverse_lazy("files:customer-list")


# =============================================================================
# Persona (Person)
# =============================================================================

PERSON_FIELDS = ("customer", "name", "position", "email", "phone_number")


class PersonListView(LoginRequiredMixin, ListView):
    model = Person
    template_name = "files/person_list.html"
    context_object_name = "persons"
    paginate_by = 25
    ordering = ("name",)


class PersonCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Person
    fields = PERSON_FIELDS
    template_name = "files/person_form.html"
    permission_required = "files.add_person"
    success_url = reverse_lazy("files:person-list")


class PersonUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Person
    fields = PERSON_FIELDS
    template_name = "files/person_form.html"
    permission_required = "files.change_person"
    success_url = reverse_lazy("files:person-list")


class PersonDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = Person
    template_name = "files/person_confirm_delete.html"
    permission_required = "files.delete_person"
    success_url = reverse_lazy("files:person-list")


# =============================================================================
# Archivo (FileArchive) -- subida directa navegador -> Azure, fuera del Admin
# =============================================================================

FILE_ARCHIVE_PAGE_SIZE = 25
STATUS_POLL_ID_CAP = 50


@login_required
def file_archive_list_view(request):
    # Sin has_perm: igual que Cliente/Persona, cualquier autenticado puede ver.
    qs = FileArchive.objects.select_related("customer", "archive_class").order_by("-uploaded_at")

    query = (request.GET.get("q") or "").strip()
    if query:
        qs = qs.filter(name__icontains=query)

    status = (request.GET.get("status") or "").strip().upper()
    valid_statuses = set(FileArchive.UploadStatus.values)
    if status not in valid_statuses:
        status = ""
    if status:
        qs = qs.filter(upload_status=status)

    page = Paginator(qs, FILE_ARCHIVE_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "files/filearchive_list.html", {
        "page_obj": page,
        "query": query,
        "status": status,
        "status_choices": FileArchive.UploadStatus.choices,
    })


@login_required
def file_archive_status_view(request):
    raw_ids = (request.GET.get("ids") or "").split(",")
    valid_ids = []
    for raw in raw_ids:
        raw = raw.strip()
        if raw:
            try:
                valid_ids.append(uuid.UUID(raw))
            except ValueError:
                pass
    # Tope duro independiente de la paginación del cliente: protege contra
    # una query string armada a mano con miles de ids.
    valid_ids = valid_ids[:STATUS_POLL_ID_CAP]

    rows = FileArchive.objects.filter(pk__in=valid_ids).values(
        "pk", "upload_status", "error_message"
    )
    data = {
        str(row["pk"]): {
            "status": row["upload_status"],
            "status_display": FileArchive.UploadStatus(row["upload_status"]).label,
            "error_message": row["error_message"] or "",
        }
        for row in rows
    }
    response = JsonResponse(data)
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def file_archive_upload_sas_view(request):
    """Equivalente de FileArchiveAdmin.upload_sas_view (files/admin.py, que
    no se toca) pero fuera del Admin: no exige is_staff
    (admin_site.admin_view() no aplica aquí), solo el permiso de modelo --
    para dar acceso a usuarios de negocio sin acceso al panel de Admin.
    """
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


@login_required
@permission_required("files.add_filearchive", raise_exception=True)
def file_archive_upload_view(request):
    if request.method == "POST":
        form = FileArchiveUploadForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            # Sustituye el UUID que puso default=uuid.uuid4 por el que acuñó
            # file_archive_upload_sas_view() y que ya da nombre al blob real.
            obj.pk = form.cleaned_data["file_archive_id"]

            props = getattr(form, "_azure_props", None)
            if props is not None:
                # La fuente de verdad es Azure, no lo que declaró el cliente.
                obj.file_size = props.size
                if props.content_settings and props.content_settings.content_type:
                    obj.content_type = props.content_settings.content_type

            obj.uploaded_by = request.user
            obj.upload_status = FileArchive.UploadStatus.PENDING

            with transaction.atomic():
                obj.save()
                # on_commit: el worker no debe ver la fila antes de que la
                # transacción confirme (evita un DoesNotExist intermitente).
                transaction.on_commit(lambda: run_post_processing.delay(str(obj.pk)))

            return redirect("files:archive-list")
    else:
        form = FileArchiveUploadForm()

    return render(request, "files/upload.html", {
        "form": form,
        "max_upload_size_mb": settings.MAX_UPLOAD_SIZE_MB,
    })


class FileArchiveDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = FileArchive
    template_name = "files/filearchive_confirm_delete.html"
    permission_required = "files.delete_filearchive"
    success_url = reverse_lazy("files:archive-list")

    def form_valid(self, form):
        # Si no se limpia el blob, queda huérfano y facturando en Azure
        # para siempre -- azure_client.delete_blob_quietly ya existe
        # exactamente para este caso.
        azure_client.delete_blob_quietly(self.object.blob_path)
        return super().form_valid(form)
