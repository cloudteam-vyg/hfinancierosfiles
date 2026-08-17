import io
import logging
import posixpath
from datetime import date

from pypdf import PdfReader, PdfWriter

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .frontend_forms import (
    FileArchiveEditForm, FileArchiveUploadForm, QuickArchiveClassForm, QuickCustomerForm,
)
from .models import ActivityType, ClassName, Customer, FileArchive, Person
from .uploads import stamp_upload_metadata


logger = logging.getLogger(__name__)


def _safe_attachment_filename(name):
    # Un filename con \r/\n (craftable a mano vía multipart) rompe el header
    # Content-Disposition (BadHeaderError -> 500) en las vistas de archivo.
    return (name or "").replace("\r", "").replace("\n", "")


def _response_filename(obj):
    """Nombre a exponer en Content-Disposition, ya saneado."""
    return _safe_attachment_filename(obj.original_filename or obj.file.name)


def _open_file_or_404(obj):
    """Abre los bytes del archivo o levanta Http404 dejando rastro.

    Una fila puede existir con su archivo ausente en disco (volumen de
    media/ sin montar -- ver DEPLOY.md -- o borrado externo). Para el
    usuario es un 404 indistinguible de cualquier otro, así que el log es
    la única forma de diagnosticarlo.
    """
    try:
        return obj.file.open("rb")
    except (FileNotFoundError, OSError):
        logger.warning("FileArchive %s: bytes ausentes en disco (%s)", obj.pk, obj.file.name)
        raise Http404

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
# Archivo (FileArchive) -- subida multipart al servidor, fuera del Admin
# =============================================================================

FILE_ARCHIVE_PAGE_SIZE = 25

# Whitelist server-side por extensión -- nunca se confía en obj.content_type
# (dato del navegador de quien subió el archivo). Todo lo que no esté aquí
# nunca llega a file_archive_preview_view: el frontend muestra la tarjeta de
# metadatos + descarga directamente, sin pedir esta vista.
PREVIEWABLE_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".txt": "text/plain; charset=utf-8",
    # Office: no se renderizan nativamente por el navegador -- el frontend
    # los pide vía fetch()+ArrayBuffer y los procesa con las librerías
    # vendorizadas (docx-preview/SheetJS), nunca asignando iframe.src.
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# Tope de tamaño para previews que viajan COMPLETAS al navegador (imágenes y
# Office): por encima, tarjeta de metadatos + descarga. No aplica a PDF (se
# trunca server-side a PDF_PREVIEW_PAGE_LIMIT páginas, ver abajo) ni a .txt
# (solo se leen los primeros TEXT_PREVIEW_MAX_BYTES). El frontend hace el
# mismo chequeo con el data-size ya renderizado por fila.
MAX_PREVIEW_BYTES = 20 * 1024 * 1024

# Política estricta: TODA preview de PDF se limita a las primeras
# PDF_PREVIEW_PAGE_LIMIT páginas (protege navegador y servidor: Django 4.2
# no soporta Range requests en FileResponse, así que un PDF grande se
# re-descargaría completo en cada seek del visor). El resultado se cachea en
# disco junto al original (_pdf_cache_names): truncar con pypdf es CPU-bound
# y bajo gevent bloquearía el worker si se repitiera en cada clic.
PDF_PREVIEW_PAGE_LIMIT = 5

# Tope para intentar truncar un PDF. Se deriva del límite de subida: si la
# app aceptó el archivo, debe poder previsualizarlo -- un tope menor dejaba
# sin preview a archivos perfectamente válidos (un PDF real de 118 MB / 257
# páginas se truncaba en 0.11 s usando 8 MB de RAM, porque pypdf lee el xref
# y extrae solo las páginas pedidas en vez de cargar todo el documento).
MAX_PDF_TRUNCATE_SOURCE_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

# ~5 páginas de texto plano (≈3.000 caracteres/página). El servidor nunca
# lee más que esto de un .txt, sin importar su tamaño real en disco.
TEXT_PREVIEW_MAX_BYTES = 16 * 1024


def _truncated_pdf_preview(handle):
    """Genera un PDF en memoria con solo las primeras PDF_PREVIEW_PAGE_LIMIT
    páginas de `handle`. Devuelve (BytesIO listo para leer, se_truncó) o
    (None, False) si el PDF no se pudo procesar -- el llamador cae al
    404/tarjeta de metadatos normal en ese caso.
    """
    try:
        reader = PdfReader(handle)
        if reader.is_encrypted:
            # Muchos PDF legales/firmados van "protegidos" con contraseña de
            # propietario pero contraseña de usuario VACÍA -- esos sí se
            # pueden abrir. Si decrypt("") no alcanza, se degrada a tarjeta.
            if not reader.decrypt(""):
                return None, False
        total_pages = len(reader.pages)
        writer = PdfWriter()
        for i in range(min(PDF_PREVIEW_PAGE_LIMIT, total_pages)):
            writer.add_page(reader.pages[i])
        buffer = io.BytesIO()
        writer.write(buffer)
        buffer.seek(0)
        return buffer, total_pages > PDF_PREVIEW_PAGE_LIMIT
    except Exception:
        # Deliberadamente amplio: pypdf lanza tipos arbitrarios (KeyError,
        # AttributeError, RecursionError, struct.error...) ante PDFs
        # malformados u hostiles del mundo real. Este helper NUNCA debe
        # tumbar la vista con un 500: cualquier fallo de parseo significa
        # "sin preview", no un bug nuestro.
        return None, False


def _apply_preview_headers(response, truncated=False):
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-cache"
    if truncated:
        # Leído por filearchive_split.js (fetch same-origin, sin CORS) para
        # mostrar el aviso "primeras N páginas" solo cuando aplica de verdad.
        response["X-Preview-Truncated"] = "1"
    return response


def _pdf_cache_names(obj):
    # El cache vive en la misma carpeta UUID del original (una carpeta por
    # archivo, ver file_archive_upload_to). Dos nombres distintos codifican
    # si el original tenía más páginas que el límite, sin re-parsear.
    base = posixpath.dirname(obj.file.name)
    return (
        posixpath.join(base, "_hf_preview5_trunc.pdf"),
        posixpath.join(base, "_hf_preview5_full.pdf"),
    )


def _cached_pdf_preview_response(obj, content_type):
    """Sirve la preview ya generada en disco, o None si aún no existe."""
    storage = obj.file.storage
    for cache_name, truncated in zip(_pdf_cache_names(obj), (True, False)):
        if cache_name == obj.file.name or not storage.exists(cache_name):
            continue
        try:
            handle = storage.open(cache_name, "rb")
        except (FileNotFoundError, OSError):
            # El cache desapareció entre exists() y open(): se regenera.
            logger.info("FileArchive %s: cache de preview ilegible (%s)", obj.pk, cache_name)
            continue
        response = FileResponse(
            handle, as_attachment=False,
            filename=_response_filename(obj), content_type=content_type,
        )
        return _apply_preview_headers(response, truncated=truncated)
    return None


def _store_pdf_preview(obj, buffer, truncated):
    """Guarda la preview generada para no volver a truncar en cada clic."""
    trunc_name, full_name = _pdf_cache_names(obj)
    cache_name = trunc_name if truncated else full_name
    if cache_name == obj.file.name:
        return
    try:
        obj.file.storage.save(cache_name, ContentFile(buffer.getvalue()))
    except OSError as exc:
        # Mejor esfuerzo: si el guardado falla (disco lleno, permisos,
        # volumen de solo lectura) igual se sirve el buffer recién
        # generado; solo se pierde el cache y se pagará el truncado otra vez.
        logger.warning("FileArchive %s: no se pudo cachear la preview (%s)", obj.pk, exc)


def _pdf_preview_response(obj, content_type):
    if obj.file_size and obj.file_size > MAX_PDF_TRUNCATE_SOURCE_BYTES:
        raise Http404

    cached = _cached_pdf_preview_response(obj, content_type)
    if cached is not None:
        return cached

    with _open_file_or_404(obj) as source:
        buffer, truncated = _truncated_pdf_preview(source)
    if buffer is None:
        raise Http404

    _store_pdf_preview(obj, buffer, truncated)

    buffer.seek(0)
    response = FileResponse(
        buffer, as_attachment=False,
        filename=_response_filename(obj), content_type=content_type,
    )
    return _apply_preview_headers(response, truncated=truncated)


def _text_preview_response(obj, content_type):
    with _open_file_or_404(obj) as handle:
        chunk = handle.read(TEXT_PREVIEW_MAX_BYTES + 1)
    truncated = len(chunk) > TEXT_PREVIEW_MAX_BYTES
    text = chunk[:TEXT_PREVIEW_MAX_BYTES].decode("utf-8", errors="replace")
    response = HttpResponse(text, content_type=content_type)
    return _apply_preview_headers(response, truncated=truncated)


def _parse_date_param(request, name):
    # Igual que el manejo de "status" abajo: un valor ausente/inválido se
    # trata como si no se hubiera mandado -- nunca se pasa un string sin
    # validar al ORM (evita depender de que el backend de DB rechace bien
    # un valor malformado) y nunca se re-muestra un valor que no se aplicó.
    raw = (request.GET.get(name) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _read_file_archive_filters(request):
    """Extrae y valida los filtros de la query string.

    Devuelve un dict con los valores YA validados: los inválidos quedan
    vacíos para no re-mostrarlos en el formulario ni pasarlos al ORM.
    """
    return {
        "query": (request.GET.get("q") or "").strip(),
        "opening_date_from": _parse_date_param(request, "opening_date_from"),
        "opening_date_to": _parse_date_param(request, "opening_date_to"),
        "due_date_from": _parse_date_param(request, "due_date_from"),
        "due_date_to": _parse_date_param(request, "due_date_to"),
    }


def _apply_file_archive_filters(qs, filters):
    """Aplica los filtros ya validados al queryset (acumulativos)."""
    if filters["query"]:
        qs = qs.filter(name__icontains=filters["query"])

    for key, lookup in (
        ("opening_date_from", "opening_date__gte"),
        ("opening_date_to", "opening_date__lte"),
        ("due_date_from", "due_date__gte"),
        ("due_date_to", "due_date__lte"),
    ):
        if filters[key]:
            qs = qs.filter(**{lookup: filters[key]})
    return qs


def _querystring_without_page(request):
    """Query string actual sin "page", para los links de paginación.

    Así avanzar de página nunca pierde un filtro activo, presente o futuro,
    sin enumerar cada parámetro a mano en el template.
    """
    querydict = request.GET.copy()
    querydict.pop("page", None)
    return querydict.urlencode()


@login_required
def file_archive_list_view(request):
    # Sin has_perm: igual que Cliente/Persona, cualquier autenticado puede ver.
    qs = FileArchive.objects.select_related("customer", "archive_class").order_by("-uploaded_at")
    filters = _read_file_archive_filters(request)
    qs = _apply_file_archive_filters(qs, filters)

    page = Paginator(qs, FILE_ARCHIVE_PAGE_SIZE).get_page(request.GET.get("page"))
    dates = {
        key: value.isoformat() if value else ""
        for key, value in filters.items()
        if key.endswith("_from") or key.endswith("_to")
    }
    return render(request, "files/filearchive_list.html", {
        "page_obj": page,
        "query": filters["query"],
        "base_querystring": _querystring_without_page(request),
        # Los mismos topes que aplica el servidor, para que el JS no pida una
        # preview condenada a 404 -- y para que no puedan desincronizarse
        # duplicando el número en el JS (fue justo lo que dejó sin preview a
        # un PDF válido de 118 MB).
        "max_preview_bytes": MAX_PREVIEW_BYTES,
        "max_pdf_bytes": MAX_PDF_TRUNCATE_SOURCE_BYTES,
        "pdf_page_limit": PDF_PREVIEW_PAGE_LIMIT,
        **dates,
    })


def _upload_page_context(form):
    return {
        "form": form,
        "max_upload_size_mb": settings.MAX_UPLOAD_SIZE_MB,
        # Para el <select> de classname/activity_type del modal "+ Nuevo
        # cliente" -- son catálogos pequeños, se listan enteros sin paginar.
        "classnames": ClassName.objects.order_by("name"),
        "activity_types": ActivityType.objects.order_by("name"),
    }


def _create_file_archive(form, uploaded, user):
    """Persiste el registro. El archivo queda disponible de inmediato.

    Ya no se encola nada en Celery: la escritura en disco es síncrona y no
    hay trabajo pesado que diferir, así que un archivo nunca queda "en
    proceso". `files/tasks.py::run_post_processing` sigue existiendo por si
    vuelve a hacer falta un pipeline asíncrono.
    """
    obj = stamp_upload_metadata(form.save(commit=False), uploaded, user)
    obj.save()
    return obj


@login_required
@permission_required("files.add_filearchive", raise_exception=True)
def file_archive_upload_view(request):
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if request.method != "POST":
        return render(request, "files/upload.html", _upload_page_context(FileArchiveUploadForm()))

    form = FileArchiveUploadForm(request.POST, request.FILES)
    uploaded = form.cleaned_data.get("file") if form.is_valid() else None
    if uploaded is None and form.is_valid():
        # El form ya lo exige (required=True); esto es defensa en profundidad
        # para que nunca vuelva a ser un AttributeError/500.
        form.add_error("file", "Selecciona un archivo para subir.")

    if not form.is_valid() or uploaded is None:
        if is_ajax:
            return JsonResponse({"success": False, "errors": form.errors}, status=400)
        return render(request, "files/upload.html", _upload_page_context(form))

    _create_file_archive(form, uploaded, request.user)
    if is_ajax:
        return JsonResponse({"success": True, "redirect_url": reverse("files:archive-list")})
    return redirect("files:archive-list")


# =============================================================================
# Alta rápida (modales de /archivos/subir/) -- JSON, sin redirección
# =============================================================================

@login_required
@require_POST
@permission_required("files.add_customer", raise_exception=True)
def customer_quick_create_view(request):
    form = QuickCustomerForm(request.POST)
    if form.is_valid():
        obj = form.save()
        return JsonResponse({"id": obj.pk, "label": str(obj)}, status=201)
    return JsonResponse({"errors": form.errors}, status=400)


@login_required
@require_POST
@permission_required("files.add_archiveclass", raise_exception=True)
def archive_class_quick_create_view(request):
    form = QuickArchiveClassForm(request.POST)
    if form.is_valid():
        obj = form.save()
        return JsonResponse({"id": obj.pk, "label": str(obj)}, status=201)
    return JsonResponse({"errors": form.errors}, status=400)


class FileArchiveDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteView):
    model = FileArchive
    template_name = "files/filearchive_confirm_delete.html"
    permission_required = "files.delete_filearchive"
    success_url = reverse_lazy("files:archive-list")

    def form_valid(self, form):
        if self.object.file:
            # Limpia también el cache de preview de PDF (si existe) para no
            # dejar huérfanos en la carpeta UUID del archivo.
            storage = self.object.file.storage
            for cache_name in _pdf_cache_names(self.object):
                if cache_name == self.object.file.name:
                    continue
                try:
                    storage.delete(cache_name)
                except OSError as exc:
                    # No debe impedir el borrado del registro: como mucho
                    # queda un archivo de cache huérfano en disco.
                    logger.warning(
                        "FileArchive %s: no se pudo borrar el cache %s (%s)",
                        self.object.pk, cache_name, exc,
                    )
            self.object.file.delete(save=False)
        return super().form_valid(form)


@login_required
def file_archive_download_view(request, pk):
    # Sin has_perm/filtro por cliente a propósito: igual que
    # file_archive_list_view, cualquier autenticado puede ver/descargar
    # cualquier archivo (no hay noción de "clientes propios" por usuario en
    # esta app). content_type se fuerza a octet-stream -- obj.content_type
    # viene del navegador del que subió el archivo (no es de confiar) y un
    # HTML/SVG malicioso sniffeado por el navegador del que descarga sería
    # un vector de XSS almacenado si se respetara tal cual.
    obj = get_object_or_404(FileArchive, pk=pk)
    if not obj.file:
        raise Http404
    return FileResponse(
        _open_file_or_404(obj),
        as_attachment=True,
        filename=_response_filename(obj),
        content_type="application/octet-stream",
    )


@login_required
@xframe_options_sameorigin
def file_archive_preview_view(request, pk):
    # Vista separada de la de descarga a propósito: esta SÍ sirve contenido
    # "inline" (para <iframe>/<img> o fetch()), pero solo para una whitelist
    # fija de extensiones con un Content-Type hardcodeado -- nunca el
    # content_type declarado al subir. @xframe_options_sameorigin es
    # necesario porque XFrameOptionsMiddleware aplica DENY por default en
    # todo el sitio (core/settings.py no define X_FRAME_OPTIONS); sin este
    # decorator el <iframe> de previsualización se bloquea en blanco.
    #
    # Política por tipo:
    #   - PDF: SIEMPRE las primeras PDF_PREVIEW_PAGE_LIMIT páginas (con
    #     cache en disco) -- ver _pdf_preview_response.
    #   - .txt: solo los primeros TEXT_PREVIEW_MAX_BYTES.
    #   - Imágenes/Office: archivo completo, con tope MAX_PREVIEW_BYTES.
    obj = get_object_or_404(FileArchive, pk=pk)
    if not obj.file:
        raise Http404

    content_type = PREVIEWABLE_EXTENSIONS.get(obj.extension)
    if content_type is None:
        raise Http404

    if obj.extension == ".pdf":
        return _pdf_preview_response(obj, content_type)
    if obj.extension == ".txt":
        return _text_preview_response(obj, content_type)

    if obj.file_size and obj.file_size > MAX_PREVIEW_BYTES:
        raise Http404
    response = FileResponse(
        _open_file_or_404(obj),
        as_attachment=False,
        filename=_response_filename(obj),
        content_type=content_type,
    )
    return _apply_preview_headers(response)


class FileArchiveUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = FileArchive
    form_class = FileArchiveEditForm
    template_name = "files/filearchive_form.html"
    permission_required = "files.change_filearchive"
    raise_exception = True

    def _validated_next(self):
        # Único punto de validación: el template NUNCA debe leer
        # request.GET.next directamente (un href="{{ request.GET.next }}"
        # sin validar es un vector de XSS vía esquema javascript: -- Django
        # escapa HTML, no esquemas de URL).
        next_url = self.request.POST.get("next") or self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}, require_https=self.request.is_secure()
        ):
            return next_url
        return ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["next_url"] = self._validated_next()
        return context

    def get_success_url(self):
        return self._validated_next() or reverse_lazy("files:archive-list")
