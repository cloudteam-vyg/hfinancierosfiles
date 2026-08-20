import io
import shutil
import tempfile
from datetime import date

from pypdf import PdfReader, PdfWriter

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from .frontend_forms import CustomerForm
from .models import (
    ActivityType, ArchiveClass, ClassName, Customer, FileArchive, PersonType,
)
from .views import (
    MAX_PDF_TRUNCATE_SOURCE_BYTES, MAX_PREVIEW_BYTES, PDF_PREVIEW_PAGE_LIMIT,
    TEXT_PREVIEW_MAX_BYTES, _pdf_cache_names,
)

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="hf_test_media_")


def _make_pdf_bytes(num_pages):
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class ArchiveFixtureTestCase(TestCase):
    """Base de las suites de archivos: cliente + clase de archivo listos y
    un MEDIA_ROOT temporal que se borra al terminar.

    El MEDIA_ROOT temporal NO es opcional: sin él los tests escribirían en
    el media/ real del proyecto, que contiene archivos subidos por usuarios.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        classname = ClassName.objects.create(name="Clase")
        activity = ActivityType.objects.create(name="Actividad")
        cls.customer = Customer.objects.create(
            classname=classname, activity_type=activity, name="Cliente de prueba",
            date_of_constitution="2020-01-01",
        )
        cls.archive_class = ArchiveClass.objects.create(name="Clase de archivo")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)


class FileArchivePreviewDownloadTests(ArchiveFixtureTestCase):
    """files/views.py::file_archive_preview_view y file_archive_download_view.

    Primera cobertura real de esta superficie de seguridad: whitelist por
    extensión (nunca el content_type declarado al subir), headers de la
    respuesta, y que la vista de descarga no perdió su protección anterior.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = User.objects.create_user("preview_user", password="x")

    def setUp(self):
        self.client.force_login(self.user)

    def _make_archive(self, filename, content_type, file_size=None):
        upload = SimpleUploadedFile(filename, b"contenido de prueba", content_type=content_type)
        obj = FileArchive.objects.create(
            archive_class=self.archive_class, customer=self.customer, name=filename,
            file=upload, original_filename=filename, content_type=content_type,
            file_size=file_size if file_size is not None else upload.size,
        )
        return obj

    def test_preview_rejects_non_whitelisted_extension(self):
        obj = self._make_archive("paquete.zip", "application/zip")
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_preview_ignores_stored_content_type_lie(self):
        # El archivo dice ser PNG por su extensión, pero content_type
        # almacenado miente diciendo que es HTML -- la vista debe ignorar
        # ese dato y usar el Content-Type fijo de la whitelist.
        obj = self._make_archive("imagen.png", "text/html")
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")

    def test_preview_sets_security_headers(self):
        obj = self._make_archive("imagen.png", "image/png")
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp["X-Frame-Options"], "SAMEORIGIN")
        self.assertIn("inline", resp["Content-Disposition"])

    def test_preview_rejects_oversized_file(self):
        obj = self._make_archive("grande.png", "image/png", file_size=MAX_PREVIEW_BYTES + 1)
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 404)

    def _make_pdf_archive(self, name, num_pages, file_size=None, encrypt=False):
        writer = PdfWriter()
        for _ in range(num_pages):
            writer.add_blank_page(width=200, height=200)
        if encrypt:
            # Caso típico de PDF legal/firmado: contraseña de usuario VACÍA
            # con contraseña de propietario -- se abre con decrypt("").
            writer.encrypt(user_password="", owner_password="clave-propietario")
        buffer = io.BytesIO()
        writer.write(buffer)
        pdf_bytes = buffer.getvalue()
        upload = SimpleUploadedFile(name, pdf_bytes, content_type="application/pdf")
        return FileArchive.objects.create(
            archive_class=self.archive_class, customer=self.customer, name=name,
            file=upload, original_filename=name, content_type="application/pdf",
            file_size=file_size if file_size is not None else len(pdf_bytes),
        )

    def _preview_pages(self, resp):
        return len(PdfReader(io.BytesIO(b"".join(resp.streaming_content))).pages)

    def test_preview_pdf_always_truncates_to_page_limit(self):
        # Política estricta: TODO PDF (aunque pese poco) se limita a las
        # primeras PDF_PREVIEW_PAGE_LIMIT páginas.
        obj = self._make_pdf_archive("normal.pdf", PDF_PREVIEW_PAGE_LIMIT + 3)
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertEqual(resp["X-Preview-Truncated"], "1")
        self.assertEqual(self._preview_pages(resp), PDF_PREVIEW_PAGE_LIMIT)

    def test_preview_pdf_under_page_limit_served_complete(self):
        obj = self._make_pdf_archive("corto.pdf", PDF_PREVIEW_PAGE_LIMIT - 2)
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("X-Preview-Truncated", resp)
        self.assertEqual(self._preview_pages(resp), PDF_PREVIEW_PAGE_LIMIT - 2)

    def test_preview_pdf_oversized_declared_still_works(self):
        # El caso reportado: un PDF cuyo file_size declarado supera el tope
        # de preview -- antes daba tarjeta de "demasiado grande", ahora se
        # trunca igual que cualquier PDF.
        obj = self._make_pdf_archive(
            "grande.pdf", PDF_PREVIEW_PAGE_LIMIT + 3, file_size=MAX_PREVIEW_BYTES + 1,
        )
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._preview_pages(resp), PDF_PREVIEW_PAGE_LIMIT)

    def test_preview_pdf_result_is_cached_on_disk(self):
        obj = self._make_pdf_archive("cacheado.pdf", PDF_PREVIEW_PAGE_LIMIT + 3)
        trunc_name, _ = _pdf_cache_names(obj)
        storage = obj.file.storage
        self.assertFalse(storage.exists(trunc_name))

        first = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(first.status_code, 200)
        self.assertTrue(storage.exists(trunc_name))

        second = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second["X-Preview-Truncated"], "1")
        self.assertEqual(self._preview_pages(second), PDF_PREVIEW_PAGE_LIMIT)

    def test_preview_pdf_encrypted_with_empty_user_password_renders(self):
        obj = self._make_pdf_archive("firmado.pdf", PDF_PREVIEW_PAGE_LIMIT + 2, encrypt=True)
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._preview_pages(resp), PDF_PREVIEW_PAGE_LIMIT)

    def test_preview_pdf_above_old_100mb_ceiling_still_works(self):
        # Regresión: el techo de truncado era un 100 MB arbitrario, menor que
        # el límite real de subida (MAX_UPLOAD_SIZE_MB), así que un PDF válido
        # de ~119 MB daba 404 sin intentar nada. pypdf lo trunca en ~0.1 s.
        self.assertGreater(MAX_PDF_TRUNCATE_SOURCE_BYTES, 100 * 1024 * 1024)
        obj = self._make_pdf_archive(
            "muy-grande.pdf", PDF_PREVIEW_PAGE_LIMIT + 4, file_size=119 * 1024 * 1024,
        )
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._preview_pages(resp), PDF_PREVIEW_PAGE_LIMIT)

    def test_pdf_ceiling_matches_upload_limit(self):
        # Si se puede subir, se debe poder previsualizar.
        self.assertEqual(
            MAX_PDF_TRUNCATE_SOURCE_BYTES, settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        )

    def test_preview_rejects_pdf_beyond_truncate_ceiling(self):
        obj = self._make_archive(
            "enorme.pdf", "application/pdf", file_size=MAX_PDF_TRUNCATE_SOURCE_BYTES + 1,
        )
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_preview_of_corrupt_pdf_404s_not_500s(self):
        # El archivo en disco no es un PDF real -- _truncated_pdf_preview
        # debe fallar limpio (None) y la vista debe caer a 404, no romper.
        obj = self._make_archive("corrupto.pdf", "application/pdf")
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_preview_txt_truncated_to_byte_limit(self):
        content = b"a" * (TEXT_PREVIEW_MAX_BYTES * 2)
        upload = SimpleUploadedFile("notas.txt", content, content_type="text/plain")
        obj = FileArchive.objects.create(
            archive_class=self.archive_class, customer=self.customer, name="notas.txt",
            file=upload, original_filename="notas.txt", content_type="text/plain",
            file_size=len(content),
        )
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["X-Preview-Truncated"], "1")
        self.assertIn("text/plain", resp["Content-Type"])
        self.assertEqual(len(resp.content), TEXT_PREVIEW_MAX_BYTES)

    def test_preview_txt_small_served_complete(self):
        upload = SimpleUploadedFile("hola.txt", b"hola mundo", content_type="text/plain")
        obj = FileArchive.objects.create(
            archive_class=self.archive_class, customer=self.customer, name="hola.txt",
            file=upload, original_filename="hola.txt", content_type="text/plain",
            file_size=10,
        )
        resp = self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("X-Preview-Truncated", resp)
        self.assertEqual(resp.content, b"hola mundo")

    def test_delete_view_removes_pdf_preview_cache(self):
        root = User.objects.create_superuser("root_cache_cleanup", "r@example.com", "x")
        self.client.force_login(root)

        obj = self._make_pdf_archive("por-borrar.pdf", PDF_PREVIEW_PAGE_LIMIT + 3)
        self.client.get(reverse("files:archive-preview", args=[obj.pk]))
        trunc_name, _ = _pdf_cache_names(obj)
        storage = obj.file.storage
        file_name = obj.file.name
        self.assertTrue(storage.exists(trunc_name))

        resp = self.client.post(reverse("files:archive-delete", args=[obj.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(storage.exists(trunc_name))
        self.assertFalse(storage.exists(file_name))

    def test_download_still_forces_octet_stream_attachment(self):
        # Guardia de regresión del fix de seguridad de una fase anterior:
        # la vista de descarga NUNCA debe servir inline ni con el
        # content_type declarado, sin importar la extensión.
        obj = self._make_archive("imagen.png", "image/png")
        resp = self.client.get(reverse("files:archive-download", args=[obj.pk]))
        self.assertEqual(resp["Content-Type"], "application/octet-stream")
        self.assertIn("attachment", resp["Content-Disposition"])


class FileArchiveUpdateViewTests(ArchiveFixtureTestCase):
    def setUp(self):
        upload = SimpleUploadedFile("doc.pdf", b"contenido", content_type="application/pdf")
        self.obj = FileArchive.objects.create(
            archive_class=self.archive_class, customer=self.customer, name="doc.pdf",
            file=upload, original_filename="doc.pdf", content_type="application/pdf",
            file_size=upload.size,
        )
        self.url = reverse("files:archive-update", args=[self.obj.pk])

    def test_requires_change_permission(self):
        user = User.objects.create_user("sin_permiso", password="x")
        # Un usuario nuevo se asigna automáticamente al grupo "Estandar"
        # (ver authentication/signals.py), que SÍ tiene change_filearchive
        # -- hay que quitarlo explícitamente para simular a alguien
        # realmente sin el permiso.
        user.groups.clear()
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_edit_form_offers_archive_class_quick_create(self):
        user = User.objects.create_user("edita_con_catalogo", password="x")
        user.groups.clear()
        for codename in ("change_filearchive", "add_archiveclass"):
            user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(user)

        html = self.client.get(self.url).content.decode()
        self.assertIn('id="archive-class-modal"', html)
        self.assertIn('data-modal-open="archive-class-modal"', html)
        # Único contrato JS<->Django de este modal: si el nombre del campo del
        # ModelForm cambia, el modal deja de funcionar en silencio y solo esta
        # aserción se enteraría.
        self.assertIn('data-target-select="id_archive_class"', html)
        self.assertIn("quick_create_modals", html)  # whitenoise le pone hash al nombre

    def test_edit_form_hides_quick_create_without_permission(self):
        user = User.objects.create_user("edita_sin_catalogo", password="x")
        # groups.clear() es imprescindible: el grupo por defecto ("Estandar")
        # concede add_archiveclass (ver el comentario de test_requires_change_permission).
        user.groups.clear()
        user.user_permissions.add(Permission.objects.get(codename="change_filearchive"))
        self.client.force_login(user)

        html = self.client.get(self.url).content.decode()
        self.assertNotIn('id="archive-class-modal"', html)
        self.assertNotIn('data-modal-open="archive-class-modal"', html)

    def test_next_param_rejects_javascript_scheme(self):
        # Guardia de regresión: request.GET.next se renderiza en el template
        # (hidden input + href de "Cancelar") -- sin validar el esquema,
        # ?next=javascript:... es XSS. La vista debe descartarlo, no solo
        # escapar HTML (Django no escapa esquemas de URL).
        user = User.objects.create_user("con_permiso_next1", password="x")
        user.user_permissions.add(Permission.objects.get(codename="change_filearchive"))
        self.client.force_login(user)

        payload = "javascript:alert(document.cookie)"
        resp = self.client.get(self.url + "?next=" + payload)
        html = resp.content.decode()
        self.assertNotIn(payload, html)

    def test_next_param_preserves_valid_internal_url(self):
        user = User.objects.create_user("con_permiso_next2", password="x")
        user.user_permissions.add(Permission.objects.get(codename="change_filearchive"))
        self.client.force_login(user)

        next_url = "/archivos/?preview=" + str(self.obj.pk)
        resp = self.client.get(self.url + "?next=" + next_url)
        self.assertIn(next_url, resp.content.decode())

    def test_edit_updates_metadata_and_records_history(self):
        user = User.objects.create_user("con_permiso", password="x")
        user.user_permissions.add(Permission.objects.get(codename="change_filearchive"))
        self.client.force_login(user)

        original_file_name = self.obj.file.name
        resp = self.client.post(self.url, {
            "archive_class": self.archive_class.pk,
            "customer": self.customer.pk,
            "name": "Nombre actualizado",
            "contact": "Juana Pérez",
            "opening_date": "", "due_date": "",
            # `file` no es un campo del form -- esto no debe reemplazar el
            # archivo original aunque se envíe.
            "file": SimpleUploadedFile("otro.pdf", b"otro contenido"),
        })
        self.assertEqual(resp.status_code, 302)

        self.obj.refresh_from_db()
        self.assertEqual(self.obj.name, "Nombre actualizado")
        self.assertEqual(self.obj.file.name, original_file_name)

        last_record = self.obj.history.first()
        self.assertEqual(last_record.history_user, user)


class FileArchiveListDateFilterTests(ArchiveFixtureTestCase):
    """files/views.py::file_archive_list_view -- filtro por rango de
    opening_date/due_date, combinado con el filtro de estado existente."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = User.objects.create_user("date_filter_user", password="x")

        cls.old = FileArchive.objects.create(
            archive_class=cls.archive_class, customer=cls.customer, name="viejo",
            file=SimpleUploadedFile("viejo.pdf", b"x"), original_filename="viejo.pdf",
            opening_date="2024-01-01", due_date="2024-06-01",
        )
        cls.new = FileArchive.objects.create(
            archive_class=cls.archive_class, customer=cls.customer, name="nuevo",
            file=SimpleUploadedFile("nuevo.pdf", b"x"), original_filename="nuevo.pdf",
            opening_date="2025-06-01", due_date="2025-12-01",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _names(self, resp):
        return {obj.name for obj in resp.context["page_obj"]}

    def test_filters_by_opening_date_range(self):
        resp = self.client.get(reverse("files:archive-list"), {
            "opening_date_from": "2025-01-01", "opening_date_to": "2025-12-31",
        })
        self.assertEqual(self._names(resp), {"nuevo"})

    def test_filters_by_due_date_range(self):
        resp = self.client.get(reverse("files:archive-list"), {
            "due_date_from": "2024-01-01", "due_date_to": "2024-12-31",
        })
        self.assertEqual(self._names(resp), {"viejo"})

    def test_filters_combine_without_resetting_each_other(self):
        resp = self.client.get(reverse("files:archive-list"), {
            "q": "nuevo", "opening_date_from": "2025-01-01",
        })
        self.assertEqual(self._names(resp), {"nuevo"})
        self.assertEqual(resp.context["query"], "nuevo")
        self.assertEqual(resp.context["opening_date_from"], "2025-01-01")

    def test_invalid_date_param_is_ignored_not_500(self):
        resp = self.client.get(reverse("files:archive-list"), {"opening_date_from": "not-a-date"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["opening_date_from"], "")
        self.assertEqual(self._names(resp), {"viejo", "nuevo"})

    def test_base_querystring_preserves_date_filters_for_pagination(self):
        resp = self.client.get(reverse("files:archive-list"), {
            "q": "nuevo", "due_date_from": "2025-01-01",
        })
        qs = resp.context["base_querystring"]
        self.assertIn("q=nuevo", qs)
        self.assertIn("due_date_from=2025-01-01", qs)

    def test_list_page_shows_no_processing_status(self):
        # Guardia: el estado de procesamiento ya no debe aparecer en la
        # interfaz -- ni como columna, ni como filtro, ni como badge.
        html = self.client.get(reverse("files:archive-list")).content.decode()
        for token in ("Pendiente", "Procesando", "badge-", "name=\"status\""):
            self.assertNotIn(token, html)


class FileArchiveUploadValidationTests(ArchiveFixtureTestCase):
    """El campo `file` es blank=True en el modelo (el form de edición lo
    excluye), así que sin required=True explícito el ModelForm lo aceptaba
    vacío y la vista reventaba en uploaded.name -> 500."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.url = reverse("files:archive-upload")

    def setUp(self):
        user = User.objects.create_user("uploader", password="x")
        user.user_permissions.add(Permission.objects.get(codename="add_filearchive"))
        self.client.force_login(user)

    def _payload(self, **overrides):
        # Todo válido MENOS lo que cada test quiera romper: si al payload le
        # faltara además `contact`, los tests de `file` pasarían por el motivo
        # equivocado.
        payload = {
            "archive_class": self.archive_class.pk,
            "customer": self.customer.pk,
            "name": "Sin archivo",
            "contact": "Juana Pérez",
            "opening_date": "", "due_date": "",
        }
        payload.update(overrides)
        return payload

    def test_ajax_upload_without_file_returns_400_not_500(self):
        resp = self.client.post(
            self.url, self._payload(), HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("file", resp.json()["errors"])
        self.assertFalse(FileArchive.objects.filter(name="Sin archivo").exists())

    def test_plain_upload_without_file_rerenders_form_not_500(self):
        resp = self.client.post(self.url, self._payload())
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].errors.get("file"))
        self.assertFalse(FileArchive.objects.filter(name="Sin archivo").exists())


class FileArchiveMissingBytesTests(ArchiveFixtureTestCase):
    """Fila en BD cuyo archivo ya no está en disco (volumen de media sin
    montar, borrado externo): debe ser 404 limpio en preview y descarga, no
    un 500 -- el frontend lo muestra como tarjeta de error."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = User.objects.create_user("missing_bytes_user", password="x")

    def setUp(self):
        self.client.force_login(self.user)
        upload = SimpleUploadedFile("fantasma.png", b"fake png", content_type="image/png")
        self.obj = FileArchive.objects.create(
            archive_class=self.archive_class, customer=self.customer, name="fantasma.png",
            file=upload, original_filename="fantasma.png", content_type="image/png",
            file_size=upload.size,
        )
        # Borra los bytes dejando la fila apuntando a una ruta inexistente.
        self.obj.file.storage.delete(self.obj.file.name)

    def test_preview_404s_when_bytes_missing(self):
        resp = self.client.get(reverse("files:archive-preview", args=[self.obj.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_download_404s_when_bytes_missing(self):
        resp = self.client.get(reverse("files:archive-download", args=[self.obj.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_pdf_preview_404s_when_bytes_missing(self):
        pdf = FileArchive.objects.create(
            archive_class=self.archive_class, customer=self.customer, name="fantasma.pdf",
            file=SimpleUploadedFile("fantasma.pdf", _make_pdf_bytes(2)),
            original_filename="fantasma.pdf", content_type="application/pdf", file_size=100,
        )
        pdf.file.storage.delete(pdf.file.name)
        resp = self.client.get(reverse("files:archive-preview", args=[pdf.pk]))
        self.assertEqual(resp.status_code, 404)


class ClassNameCrudTests(TestCase):
    """Mantenimiento de "Clase de cliente" en el frontend propio.

    Solo lista/editar/eliminar: el alta no tiene pantalla, vive en el modal de
    alta rápida (ver files/urls.py y CustomerFormQuickCreateTests).
    """

    def setUp(self):
        self.classname = ClassName.objects.create(name="Persona moral")

    def _login(self, *codenames):
        user = User.objects.create_user(f"cn_{'_'.join(codenames) or 'plain'}", password="x")
        user.groups.clear()  # el grupo "Estandar" se asigna solo; aquí se controla
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(user)
        return user

    def test_list_is_visible_to_any_authenticated_user(self):
        self._login()
        resp = self.client.get(reverse("files:classname-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Persona moral")

    def test_list_renders_for_user_with_add_permission(self):
        """La lista NO puede llevar un {% url %} a la ruta de alta retirada.

        El 200 ES la aserción: el botón "Nueva clase" vivía dentro de un
        {% if perms.files.add_classname %}, y {% url %} levanta NoReverseMatch
        al renderizar -> 500 para todo usuario del grupo "Estandar" (que
        concede add_classname). test_list_is_visible_to_any_authenticated_user no lo
        detecta porque entra sin permisos y el {% if %} nunca se evalúa.
        """
        self._login("add_classname")
        resp = self.client.get(reverse("files:classname-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Nueva clase")

    def test_create_route_no_longer_exists(self):
        # Se fija el NOMBRE (para que ninguna plantilla lo resucite) y la URL
        # (para que no la absorba un patrón hermano: "nueva" no es int, así que
        # <int:pk>/editar/ no puede).
        with self.assertRaises(NoReverseMatch):
            reverse("files:classname-create")
        self._login("add_classname")
        self.assertEqual(self.client.get("/clases-cliente/nueva/").status_code, 404)

    def test_update_with_permission(self):
        self._login("change_classname")
        resp = self.client.post(
            reverse("files:classname-update", args=[self.classname.pk]),
            {"name": "Persona moral S.A.", "description": ""},
        )
        self.assertRedirects(resp, reverse("files:classname-list"))
        self.classname.refresh_from_db()
        self.assertEqual(self.classname.name, "Persona moral S.A.")

    def test_delete_requires_permission_not_granted_to_any_role_group(self):
        # Ningún grupo de rol recibe delete (signals.py).
        self._login("add_classname", "change_classname")
        resp = self.client.post(reverse("files:classname-delete", args=[self.classname.pk]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(ClassName.objects.filter(pk=self.classname.pk).exists())

    def test_delete_page_warns_about_dependent_customers(self):
        activity = ActivityType.objects.create(name="Actividad")
        Customer.objects.create(
            classname=self.classname, activity_type=activity, name="Cliente ligado",
            date_of_constitution="2020-01-01",
        )
        self._login("delete_classname")
        resp = self.client.get(reverse("files:classname-delete", args=[self.classname.pk]))
        self.assertEqual(resp.context["customer_count"], 1)
        self.assertContains(resp, "se eliminarán también")


class ActivityTypeCrudTests(TestCase):
    """Mantenimiento de "Tipo de actividad", mismo patrón que Clase de cliente:
    sin pantalla de alta, solo lista/editar/eliminar."""

    def setUp(self):
        self.activity = ActivityType.objects.create(name="Comercio")

    def _login(self, *codenames):
        user = User.objects.create_user(f"at_{'_'.join(codenames) or 'plain'}", password="x")
        user.groups.clear()
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(user)
        return user

    def test_list_is_visible_to_any_authenticated_user(self):
        self._login()
        resp = self.client.get(reverse("files:activitytype-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Comercio")

    def test_list_renders_for_user_with_add_permission(self):
        # Mismo caso de 500 por {% url %} muerto que en ClassNameCrudTests.
        self._login("add_activitytype")
        resp = self.client.get(reverse("files:activitytype-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Nuevo tipo")

    def test_create_route_no_longer_exists(self):
        with self.assertRaises(NoReverseMatch):
            reverse("files:activitytype-create")
        self._login("add_activitytype")
        self.assertEqual(self.client.get("/tipos-actividad/nuevo/").status_code, 404)

    def test_delete_warns_about_dependent_customers(self):
        classname = ClassName.objects.create(name="Clase")
        Customer.objects.create(
            classname=classname, activity_type=self.activity, name="Cliente ligado",
            date_of_constitution="2020-01-01",
        )
        self._login("delete_activitytype")
        resp = self.client.get(reverse("files:activitytype-delete", args=[self.activity.pk]))
        self.assertEqual(resp.context["customer_count"], 1)


class PersonTypeCrudTests(TestCase):
    """Mantenimiento de "Tipo de persona": mismo patrón que los otros dos
    catálogos (sin pantalla de alta), pero su FK en Customer es SET_NULL y no
    CASCADE, así que el aviso de borrado dice otra cosa."""

    def setUp(self):
        self.person_type = PersonType.objects.create(name="Persona moral")

    def _login(self, *codenames):
        user = User.objects.create_user(f"pt_{'_'.join(codenames) or 'plain'}", password="x")
        user.groups.clear()
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(user)
        return user

    def test_list_is_visible_to_any_authenticated_user(self):
        self._login()
        resp = self.client.get(reverse("files:persontype-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Persona moral")

    def test_list_renders_for_user_with_add_permission(self):
        # Mismo caso de 500 por {% url %} muerto que en ClassNameCrudTests: el
        # grupo "Estandar" concede add_persontype, así que este es el camino real.
        self._login("add_persontype")
        resp = self.client.get(reverse("files:persontype-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Nuevo tipo de persona")

    def test_create_route_no_longer_exists(self):
        with self.assertRaises(NoReverseMatch):
            reverse("files:persontype-create")
        self._login("add_persontype")
        self.assertEqual(self.client.get("/tipos-persona/nuevo/").status_code, 404)

    def test_delete_counts_dependent_customers_without_promising_cascade(self):
        classname = ClassName.objects.create(name="Clase")
        activity = ActivityType.objects.create(name="Actividad")
        Customer.objects.create(
            classname=classname, activity_type=activity, person_type=self.person_type,
            name="Cliente ligado", date_of_constitution="2020-01-01",
        )
        self._login("delete_persontype")
        resp = self.client.get(reverse("files:persontype-delete", args=[self.person_type.pk]))
        self.assertEqual(resp.context["customer_count"], 1)
        # El aviso de los otros catálogos ("se eliminarán también, junto con sus
        # archivos") sería FALSO aquí: SET_NULL no borra nada.
        self.assertNotContains(resp, "junto con sus archivos")

    def test_delete_sets_customers_person_type_to_null_instead_of_deleting_them(self):
        """La razón de ser del SET_NULL: limpiar un catálogo no puede llevarse
        clientes por delante (y con ellos, en cascada, sus archivos)."""
        classname = ClassName.objects.create(name="Clase")
        activity = ActivityType.objects.create(name="Actividad")
        cliente = Customer.objects.create(
            classname=classname, activity_type=activity, person_type=self.person_type,
            name="Cliente ligado", date_of_constitution="2020-01-01",
        )
        self._login("delete_persontype")
        resp = self.client.post(reverse("files:persontype-delete", args=[self.person_type.pk]))
        self.assertEqual(resp.status_code, 302)
        cliente.refresh_from_db()
        self.assertIsNone(cliente.person_type)


class CustomerFormQuickCreateTests(TestCase):
    """Los modales de alta rápida dentro del formulario de Cliente."""

    def _login(self, *codenames):
        user = User.objects.create_user(f"qc_{'_'.join(codenames) or 'plain'}", password="x")
        user.groups.clear()
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(user)
        return user

    def test_form_shows_all_catalog_modals_when_user_can_create_catalogs(self):
        self._login("add_customer", "add_classname", "add_activitytype", "add_persontype")
        html = self.client.get(reverse("files:customer-create")).content.decode()
        self.assertIn('id="classname-modal"', html)
        self.assertIn('id="activitytype-modal"', html)
        self.assertIn('id="persontype-modal"', html)
        self.assertIn('data-modal-open="activitytype-modal"', html)
        # El modal escribe la opción nueva en ESTE id: si el <select> cambiara de
        # nombre, addAndSelectOption() haría un no-op silencioso.
        self.assertIn('data-target-select="id_person_type"', html)
        self.assertIn("quick_create_modals", html)  # whitenoise le pone hash al nombre

    def test_modals_hidden_for_user_without_catalog_permission(self):
        self._login("add_customer")
        html = self.client.get(reverse("files:customer-create")).content.decode()
        self.assertNotIn('id="classname-modal"', html)
        self.assertNotIn('id="activitytype-modal"', html)
        self.assertNotIn('id="persontype-modal"', html)
        self.assertNotIn('data-modal-open="persontype-modal"', html)

    def test_person_type_quick_create_returns_id_and_label(self):
        self._login("add_persontype")
        resp = self.client.post(
            reverse("files:persontype-quick-create"),
            {"name": "Persona física", "description": "Contribuyente individual"},
        )
        self.assertEqual(resp.status_code, 201)
        obj = PersonType.objects.get(name="Persona física")
        self.assertEqual(resp.json(), {"id": obj.pk, "label": "Persona física"})
        self.assertEqual(obj.description, "Contribuyente individual")

    def test_person_type_quick_create_requires_permission(self):
        self._login()
        self.assertEqual(
            self.client.post(reverse("files:persontype-quick-create"), {"name": "X"}).status_code,
            403,
        )

    def test_person_type_quick_create_rejects_duplicate_name_case_insensitively(self):
        # Hereda la regla de _QuickCatalogForm: se fija aquí porque el catálogo
        # nuevo podría haberse cableado a un ModelForm plano sin darse cuenta.
        PersonType.objects.create(name="Persona moral")
        self._login("add_persontype")
        resp = self.client.post(reverse("files:persontype-quick-create"), {"name": "PERSONA MORAL"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("name", resp.json()["errors"])
        self.assertEqual(PersonType.objects.count(), 1)

    def test_activity_type_quick_create_returns_id_and_label(self):
        self._login("add_activitytype")
        resp = self.client.post(
            reverse("files:activitytype-quick-create"), {"name": "Manufactura"},
        )
        self.assertEqual(resp.status_code, 201)
        obj = ActivityType.objects.get(name="Manufactura")
        self.assertEqual(resp.json(), {"id": obj.pk, "label": "Manufactura"})

    def test_classname_quick_create_returns_id_and_label(self):
        self._login("add_classname")
        resp = self.client.post(reverse("files:classname-quick-create"), {"name": "Persona física"})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["label"], "Persona física")

    def test_quick_create_requires_permission(self):
        self._login()
        self.assertEqual(
            self.client.post(reverse("files:activitytype-quick-create"), {"name": "X"}).status_code,
            403,
        )

    def test_quick_create_reports_validation_errors(self):
        self._login("add_activitytype")
        resp = self.client.post(reverse("files:activitytype-quick-create"), {"name": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("name", resp.json()["errors"])

    # --- description en los tres catálogos -----------------------------------
    # Se afirma el valor GUARDADO, nunca el 201: antes de exponer el campo en el
    # ModelForm, el endpoint ya devolvía 201 y descartaba la descripción en
    # silencio, así que un test sobre el status code habría pasado en verde sin
    # que el campo llegara a la base de datos.

    def test_classname_quick_create_stores_description(self):
        self._login("add_classname")
        resp = self.client.post(
            reverse("files:classname-quick-create"),
            {"name": "Persona física", "description": "Contribuyente individual"},
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            ClassName.objects.get(name="Persona física").description,
            "Contribuyente individual",
        )

    def test_activity_type_quick_create_stores_description(self):
        self._login("add_activitytype")
        resp = self.client.post(
            reverse("files:activitytype-quick-create"),
            {"name": "Manufactura", "description": "Transformación de bienes"},
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            ActivityType.objects.get(name="Manufactura").description,
            "Transformación de bienes",
        )

    def test_archive_class_quick_create_stores_description(self):
        self._login("add_archiveclass")
        resp = self.client.post(
            reverse("files:archive-class-quick-create"),
            {"name": "Contrato", "description": "Documentos contractuales"},
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(
            ArchiveClass.objects.get(name="Contrato").description,
            "Documentos contractuales",
        )

    def test_quick_create_without_description_still_works(self):
        self._login("add_classname")
        resp = self.client.post(reverse("files:classname-quick-create"), {"name": "Sin descripción"})
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(ClassName.objects.get(name="Sin descripción").description)

    def test_quick_create_rejects_duplicate_name_case_insensitively(self):
        # name no lleva unique=True en la base (ver ARCHITECTURE.md): con el alta
        # a un clic desde tres sitios, dos "Comercio" se crean sin esfuerzo y el
        # <select> acaba con dos opciones idénticas de pk distinto.
        ClassName.objects.create(name="Comercio")
        self._login("add_classname")
        resp = self.client.post(reverse("files:classname-quick-create"), {"name": "comercio"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("name", resp.json()["errors"])
        self.assertEqual(ClassName.objects.filter(name__iexact="comercio").count(), 1)

    # --- Customer.name obligatorio en TODOS los caminos ----------------------

    def _customer_payload(self, **overrides):
        classname = ClassName.objects.create(name="Clase")
        activity = ActivityType.objects.create(name="Actividad")
        payload = {
            "classname": classname.pk,
            "activity_type": activity.pk,
            "date_of_constitution": "2020-01-01",
            "name": "ACME",
            "group": "", "email": "", "phone_number": "", "address": "",
            "country": "", "web_site": "", "word_clave": "",
            # person_type y notes son opcionales: van vacíos justo para probar
            # que el alta completa funciona sin ellos.
            "person_type": "", "notes": "",
        }
        payload.update(overrides)
        return payload

    def test_customer_create_view_rejects_blank_name(self):
        """Este es el bug que arregla la migración 0003.

        Antes, este POST creaba un cliente que se mostraba como
        "Sin Nombre - None - Actividad" en todos los <select> de la app: el
        modal exigía el nombre pero el alta completa y el admin no.
        """
        self._login("add_customer")
        resp = self.client.post(reverse("files:customer-create"), self._customer_payload(name=""))
        self.assertEqual(resp.status_code, 200)  # re-render, no redirect
        self.assertIn("name", resp.context["form"].errors)
        self.assertEqual(Customer.objects.count(), 0)

    def test_customer_quick_create_requires_name(self):
        # Prueba que la obligatoriedad sobrevivió a borrar el
        # `self.fields["name"].required = True` manual: ahora viene del modelo.
        self._login("add_customer")
        payload = self._customer_payload(name="")
        del payload["group"]
        resp = self.client.post(reverse("files:customer-quick-create"), payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("name", resp.json()["errors"])

    def test_customer_model_rejects_blank_name(self):
        classname = ClassName.objects.create(name="Clase")
        activity = ActivityType.objects.create(name="Actividad")
        cliente = Customer(
            classname=classname, activity_type=activity,
            date_of_constitution="2020-01-01", name="",
        )
        with self.assertRaises(ValidationError) as ctx:
            cliente.full_clean()
        self.assertIn("name", ctx.exception.message_dict)

    def test_customer_quick_create_label_identifies_the_customer(self):
        """El label va directo a un <option> vía addAndSelectOption()."""
        self._login("add_customer")
        payload = self._customer_payload(name="ACME")
        del payload["group"]
        resp = self.client.post(reverse("files:customer-quick-create"), payload)
        self.assertEqual(resp.status_code, 201)
        label = resp.json()["label"]
        self.assertIn("ACME", label)
        # group es opcional y el modal no lo pide: __str__ no debe interpolar None.
        self.assertNotIn("None", label)

    # --- orden de las opciones ----------------------------------------------

    def test_archive_class_options_are_alphabetical(self):
        # archive_class era el único <select> sin order_by explícito: devolvía el
        # orden físico de Postgres, que se rebaraja tras cualquier UPDATE.
        ArchiveClass.objects.create(name="Zeta")
        ArchiveClass.objects.create(name="Alfa")
        self._login("add_filearchive")
        resp = self.client.get(reverse("files:archive-upload"))
        nombres = [c.name for c in resp.context["form"].fields["archive_class"].queryset]
        self.assertEqual(nombres, sorted(nombres))

    # --- modales anidados en /archivos/subir/ --------------------------------

    def test_upload_page_offers_nested_catalog_modals(self):
        """El callejón sin salida que abre retirar la pantalla de alta.

        Sin estos modales de segundo nivel, un usuario con los catálogos vacíos
        abre "+ Nuevo cliente" y se encuentra <select> que no puede rellenar
        desde ninguna parte.
        """
        self._login(
            "add_filearchive", "add_customer",
            "add_classname", "add_activitytype", "add_persontype",
        )
        html = self.client.get(reverse("files:archive-upload")).content.decode()

        # El modal padre y los tres hijos, cada uno con su id derivado de modal_id.
        self.assertIn('id="customer-modal"', html)
        self.assertIn('id="qc-classname-modal"', html)
        self.assertIn('id="qc-activitytype-modal"', html)
        self.assertIn('id="qc-persontype-modal"', html)

        # Los "+" viven DENTRO del cuerpo del modal de cliente y apuntan a los hijos.
        self.assertIn('data-modal-open="qc-classname-modal"', html)
        self.assertIn('data-modal-open="qc-activitytype-modal"', html)
        self.assertIn('data-modal-open="qc-persontype-modal"', html)

        # Los hijos escriben en los <select> del padre, no en los de la página.
        # Los ids son los que emite el CustomerForm del modal con auto_id="qc-%s",
        # de ahí el guion BAJO (son nombres de campo de Django, no slugs).
        self.assertIn('data-target-select="qc-classname"', html)
        self.assertIn('data-target-select="qc-activity_type"', html)
        self.assertIn('data-target-select="qc-person_type"', html)

        # Y el pie de mantenimiento, ahora que el catálogo salió del sidebar.
        self.assertIn(reverse("files:classname-list"), html)

    def test_upload_page_nested_modals_follow_permissions(self):
        self._login("add_filearchive", "add_customer")
        html = self.client.get(reverse("files:archive-upload")).content.decode()
        self.assertIn('id="customer-modal"', html)
        # El botón y su modal se emiten bajo la MISMA condición: si divergen, el
        # botón abriría un modal inexistente (openModal hace un no-op silencioso).
        self.assertNotIn('id="qc-classname-modal"', html)
        self.assertNotIn('data-modal-open="qc-classname-modal"', html)
        self.assertNotIn('id="qc-persontype-modal"', html)
        self.assertNotIn('data-modal-open="qc-persontype-modal"', html)

    def test_forms_opt_into_searchable_selects(self):
        # El umbral viaja en el data-*, no en una constante del JS.
        self._login("add_customer")
        html = self.client.get(reverse("files:customer-create")).content.decode()
        self.assertIn("data-hf-searchable", html)
        self.assertIn('data-hf-searchable-min-options="8"', html)
        self.assertIn("data-hf-validate", html)
        self.assertIn("hf_searchable_select", html)  # whitenoise le pone hash al nombre

    # --- etiquetas en español en las pantallas que sobreviven ---------------

    def test_classname_update_form_labels_are_spanish(self):
        classname = ClassName.objects.create(name="Persona moral")
        self._login("change_classname")
        html = self.client.get(
            reverse("files:classname-update", args=[classname.pk])
        ).content.decode()
        self.assertIn(">Nombre", html)
        self.assertNotIn("Description", html)


class CustomerModalParityTests(TestCase):
    """El modal "+ Nuevo cliente" captura LO MISMO que /clientes/nuevo/.

    El bug que arregla: el modal tenía su propio QuickCustomerForm de 4 campos,
    así que `person_type` y `notes` llegaron al modelo y solo aparecieron en la
    pantalla dedicada. Quien daba de alta un cliente desde la subida capturaba
    menos datos y no tenía forma de enterarse.
    """

    def setUp(self):
        # Los catálogos se crean UNA vez y se reutilizan: si cada llamada a
        # _full_payload() los creara, dos clientes distintos apuntarían a filas
        # distintas con el mismo nombre y la comparación de paridad de abajo
        # fallaría por un motivo que no tiene nada que ver con lo que prueba.
        self.classname = ClassName.objects.create(name="Clase")
        self.activity_type = ActivityType.objects.create(name="Actividad")
        self.person_type = PersonType.objects.create(name="Persona moral")

    def _login(self, *codenames):
        user = User.objects.create_user(f"par_{'_'.join(codenames) or 'plain'}", password="x")
        user.groups.clear()
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(user)
        return user

    def _full_payload(self, **overrides):
        payload = {
            "classname": self.classname.pk,
            "activity_type": self.activity_type.pk,
            "person_type": self.person_type.pk,
            "name": "ACME",
            "group": "Grupo ACME",
            "email": "contacto@acme.example",
            "phone_number": "5555555555",
            "address": "Av. Reforma 1",
            "country": "México",
            "date_of_constitution": "2020-01-01",
            "web_site": "https://acme.example",
            "word_clave": "acme, holding",
            "notes": "Cliente heredado de 2019.",
        }
        payload.update(overrides)
        return payload

    # --- paridad de campos ---------------------------------------------------

    def test_modal_renders_every_field_of_the_dedicated_form(self):
        """Recorre CustomerForm en vez de listar los campos a mano.

        Es lo que hace que este test detecte la divergencia el día que alguien
        añada un campo al modelo: una lista escrita aquí envejecería igual que
        envejeció la plantilla del modal.
        """
        self._login("add_filearchive", "add_customer")
        html = self.client.get(reverse("files:archive-upload")).content.decode()
        for field_name in CustomerForm.base_fields:
            # auto_id="qc-%s": ese es el id que el modal emite para cada campo.
            self.assertIn(f'id="qc-{field_name}"', html, msg=f"falta {field_name} en el modal")

    def test_modal_ids_do_not_collide_with_the_upload_form(self):
        """FileArchiveUploadForm también tiene un campo `name`.

        Sin el auto_id="qc-%s" habría dos id="id_name" en la misma página y uno
        de los dos <label for> apuntaría al control equivocado.
        """
        self._login("add_filearchive", "add_customer")
        html = self.client.get(reverse("files:archive-upload")).content.decode()
        self.assertEqual(html.count('id="id_name"'), 1)   # el del archivo
        self.assertEqual(html.count('id="qc-name"'), 1)   # el del cliente

    def test_modal_is_wide_and_uses_the_two_column_grid(self):
        self._login("add_filearchive", "add_customer")
        html = self.client.get(reverse("files:archive-upload")).content.decode()
        self.assertIn("modal-content--wide", html)
        self.assertIn("hf-modal-grid", html)
        # Los de texto largo cruzan las dos columnas, derivados del widget.
        self.assertIn("form-field--wide", html)

    def test_wide_fields_are_derived_from_the_widget(self):
        form = CustomerForm()
        self.assertEqual(sorted(form.wide_field_names()), ["address", "notes"])

    # --- persistencia completa -----------------------------------------------

    def test_quick_create_persists_every_field(self):
        self._login("add_customer")
        resp = self.client.post(reverse("files:customer-quick-create"), self._full_payload())
        self.assertEqual(resp.status_code, 201)

        cliente = Customer.objects.get(name="ACME")
        self.assertEqual(cliente.group, "Grupo ACME")
        self.assertEqual(cliente.email, "contacto@acme.example")
        self.assertEqual(cliente.phone_number, "5555555555")
        self.assertEqual(cliente.address, "Av. Reforma 1")
        self.assertEqual(cliente.country, "México")
        self.assertEqual(cliente.date_of_constitution, date(2020, 1, 1))
        self.assertEqual(cliente.web_site, "https://acme.example")
        self.assertEqual(cliente.word_clave, "acme, holding")
        self.assertEqual(cliente.notes, "Cliente heredado de 2019.")
        self.assertEqual(cliente.person_type.name, "Persona moral")
        self.assertEqual(resp.json(), {"id": cliente.pk, "label": str(cliente)})

    def test_quick_create_uses_the_same_form_as_the_dedicated_page(self):
        """Los dos caminos aceptan la MISMA carga y guardan lo mismo.

        Si alguien vuelve a introducir un form recortado para el modal, uno de
        los dos POST empieza a ignorar campos y este test lo ve.
        """
        self._login("add_customer")
        self.client.post(reverse("files:customer-quick-create"), self._full_payload())
        self.client.post(
            reverse("files:customer-create"),
            self._full_payload(name="ACME 2", email="otro@acme.example"),
        )
        desde_modal = Customer.objects.get(name="ACME")
        desde_pantalla = Customer.objects.get(name="ACME 2")
        for campo in CustomerForm.base_fields:
            if campo in ("name", "email"):
                continue  # los únicos que difieren, por la constraint unique
            self.assertEqual(
                getattr(desde_modal, campo), getattr(desde_pantalla, campo),
                msg=f"{campo} no coincide entre el modal y la pantalla dedicada",
            )

    # --- validación de obligatorios ------------------------------------------

    def test_quick_create_requires_date_of_constitution(self):
        self._login("add_customer")
        resp = self.client.post(
            reverse("files:customer-quick-create"), self._full_payload(date_of_constitution=""),
        )
        self.assertEqual(resp.status_code, 400)
        # La clave ES el name del control en el DOM: así la mapea showErrors()
        # para pintar el error sobre ese campo en vez de en el resumen.
        self.assertIn("date_of_constitution", resp.json()["errors"])
        self.assertFalse(Customer.objects.exists())

    def test_quick_create_accepts_a_payload_with_only_required_fields(self):
        """Los nueve campos nuevos son OPCIONALES: el camino rápido sobrevive."""
        self._login("add_customer")
        resp = self.client.post(reverse("files:customer-quick-create"), {
            "classname": self.classname.pk,
            "activity_type": self.activity_type.pk,
            "name": "Mínimo",
            "date_of_constitution": "2020-01-01",
        })
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Customer.objects.filter(name="Mínimo").exists())

    def test_quick_create_reports_optional_field_format_errors(self):
        # Un campo que antes el modal no mostraba: su error tiene que llegar con
        # su propia clave, no como un mensaje suelto de formulario.
        self._login("add_customer")
        resp = self.client.post(
            reverse("files:customer-quick-create"), self._full_payload(email="no-es-un-correo"),
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("email", resp.json()["errors"])

    def test_quick_customer_form_no_longer_exists(self):
        # Se fija su ausencia: volver a introducirlo es reintroducir la
        # divergencia que este cambio elimina.
        from files import frontend_forms

        self.assertFalse(hasattr(frontend_forms, "QuickCustomerForm"))


class CustomerFormFieldsTests(TestCase):
    """El formulario completo de /clientes/: widget de fecha, notas y tipo de
    persona.

    Todo esto depende de que las vistas usen form_class=CustomerForm y no
    `fields = (...)`: con `fields`, Django construye el ModelForm sin widgets y
    date_of_constitution vuelve a ser un <input type="text">.
    """

    def _login(self, *codenames):
        user = User.objects.create_user(f"cf_{'_'.join(codenames) or 'plain'}", password="x")
        user.groups.clear()
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(user)
        return user

    def _payload(self, **overrides):
        payload = {
            "classname": ClassName.objects.create(name="Clase").pk,
            "activity_type": ActivityType.objects.create(name="Actividad").pk,
            "date_of_constitution": "2020-01-01",
            "name": "ACME",
            "group": "", "email": "", "phone_number": "", "address": "",
            "country": "", "web_site": "", "word_clave": "",
            "person_type": "", "notes": "",
        }
        payload.update(overrides)
        return payload

    # --- fecha de constitución/nacimiento -----------------------------------

    def test_create_form_renders_native_date_picker(self):
        self._login("add_customer")
        html = self.client.get(reverse("files:customer-create")).content.decode()
        self.assertIn('type="date"', html)
        self.assertIn('name="date_of_constitution"', html)
        # El <input> de la fecha, específicamente: assertIn('type="date"') a
        # secas pasaría en verde por los filtros de fecha de otra pantalla.
        self.assertRegex(html, r'<input type="date"[^>]*name="date_of_constitution"')

    def test_date_label_covers_both_constitution_and_birth(self):
        # La etiqueta se deriva del verbose_name del modelo: si alguien la
        # escribe a mano en el template, este test sigue pasando pero el Admin
        # deja de coincidir. Por eso se comprueba también el modelo.
        self._login("add_customer")
        html = self.client.get(reverse("files:customer-create")).content.decode()
        self.assertIn("Fecha de constitución/Nacimiento", html)
        self.assertEqual(
            Customer._meta.get_field("date_of_constitution").verbose_name,
            "Fecha de constitución/Nacimiento",
        )

    def test_date_persists_as_date_object(self):
        self._login("add_customer")
        resp = self.client.post(reverse("files:customer-create"), self._payload())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Customer.objects.get(name="ACME").date_of_constitution, date(2020, 1, 1))

    def test_edit_form_prefills_the_date_in_iso_format(self):
        """Sin format="%Y-%m-%d" en el widget, Django serializa la fecha con el
        formato de es-mx ("1 de enero de 2020"), el <input type="date"> lo
        descarta y el campo sale VACÍO -- guardar entonces borraba el dato."""
        self._login("add_customer", "change_customer")
        self.client.post(reverse("files:customer-create"), self._payload())
        cliente = Customer.objects.get(name="ACME")
        html = self.client.get(
            reverse("files:customer-update", args=[cliente.pk])
        ).content.decode()
        self.assertIn('value="2020-01-01"', html)

    # --- notas ---------------------------------------------------------------

    def test_notes_are_optional_and_saved(self):
        self._login("add_customer")
        resp = self.client.post(
            reverse("files:customer-create"), self._payload(notes="Cliente heredado de 2019."),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Customer.objects.get(name="ACME").notes, "Cliente heredado de 2019.")

    def test_notes_empty_is_accepted(self):
        self._login("add_customer")
        resp = self.client.post(reverse("files:customer-create"), self._payload(notes=""))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Customer.objects.get(name="ACME").notes)

    def test_notes_field_carries_the_models_max_length(self):
        # El maxlength llega al HTML derivado del max_length del modelo, no
        # escrito a mano en el template (misma regla que los topes de subida).
        self._login("add_customer")
        html = self.client.get(reverse("files:customer-create")).content.decode()
        self.assertRegex(html, r'<textarea[^>]*name="notes"[^>]*maxlength="300"')

    def test_notes_longer_than_300_is_rejected(self):
        self._login("add_customer")
        resp = self.client.post(reverse("files:customer-create"), self._payload(notes="x" * 301))
        self.assertEqual(resp.status_code, 200)  # re-render, no redirect
        self.assertIn("notes", resp.context["form"].errors)
        self.assertEqual(Customer.objects.count(), 0)

    # --- tipo de persona ----------------------------------------------------

    def test_person_type_is_optional(self):
        self._login("add_customer")
        resp = self.client.post(reverse("files:customer-create"), self._payload(person_type=""))
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(Customer.objects.get(name="ACME").person_type)

    def test_person_type_is_saved_when_provided(self):
        person_type = PersonType.objects.create(name="Persona moral")
        self._login("add_customer")
        resp = self.client.post(
            reverse("files:customer-create"), self._payload(person_type=person_type.pk),
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Customer.objects.get(name="ACME").person_type, person_type)


class FileArchiveContactTests(ArchiveFixtureTestCase):
    """FileArchive.contact: obligatorio, tope de 50 y visible en la vista previa.

    Los dos puntos de entrada de subida (frontend propio y Admin) tienen que
    pedirlo igual, así que se comprueban los dos formularios.
    """

    def setUp(self):
        user = User.objects.create_user("contacto", password="x")
        user.user_permissions.add(Permission.objects.get(codename="add_filearchive"))
        self.client.force_login(user)

    def _upload_payload(self, **overrides):
        payload = {
            "archive_class": self.archive_class.pk,
            "customer": self.customer.pk,
            "name": "Con contacto",
            "contact": "Juana Pérez",
            "opening_date": "", "due_date": "",
            "file": SimpleUploadedFile("doc.txt", b"contenido"),
        }
        payload.update(overrides)
        return payload

    def test_upload_succeeds_with_contact(self):
        resp = self.client.post(reverse("files:archive-upload"), self._upload_payload())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(FileArchive.objects.get(name="Con contacto").contact, "Juana Pérez")

    def test_upload_without_contact_is_rejected(self):
        resp = self.client.post(reverse("files:archive-upload"), self._upload_payload(contact=""))
        self.assertEqual(resp.status_code, 200)  # re-render, no redirect
        self.assertIn("contact", resp.context["form"].errors)
        self.assertFalse(FileArchive.objects.filter(name="Con contacto").exists())

    def test_ajax_upload_without_contact_returns_400(self):
        resp = self.client.post(
            reverse("files:archive-upload"), self._upload_payload(contact=""),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("contact", resp.json()["errors"])
        self.assertFalse(FileArchive.objects.filter(name="Con contacto").exists())

    def test_upload_with_contact_over_50_chars_is_rejected(self):
        resp = self.client.post(
            reverse("files:archive-upload"), self._upload_payload(contact="x" * 51),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("contact", resp.context["form"].errors)
        self.assertFalse(FileArchive.objects.filter(name="Con contacto").exists())

    def test_upload_page_asks_for_contact_with_the_models_max_length(self):
        # El maxlength es lo que hace que hf_form_validate.js pueda avisar de
        # tooLong sin que el JS conozca el número.
        html = self.client.get(reverse("files:archive-upload")).content.decode()
        self.assertRegex(html, r'<input[^>]*name="contact"[^>]*maxlength="50"')
        self.assertIn("Contacto", html)

    def test_admin_upload_form_also_requires_contact(self):
        """Los dos caminos de alta deben exigir lo mismo: el Admin no puede
        crear filas sin contacto que el frontend no dejaría crear."""
        from .forms import FileArchiveAdminForm

        form = FileArchiveAdminForm(data={
            "archive_class": self.archive_class.pk,
            "customer": self.customer.pk,
            "name": "Desde el admin",
            "opening_date": "", "due_date": "",
        }, files={"file": SimpleUploadedFile("doc.txt", b"contenido")})
        self.assertFalse(form.is_valid())
        self.assertIn("contact", form.errors)

    def test_edit_form_requires_contact(self):
        user = User.objects.create_user("editor_contacto", password="x")
        user.user_permissions.add(Permission.objects.get(codename="change_filearchive"))
        self.client.force_login(user)

        obj = FileArchive.objects.create(
            customer=self.customer, archive_class=self.archive_class, name="Viejo",
            contact="", file=SimpleUploadedFile("viejo.txt", b"x"),
        )
        resp = self.client.post(reverse("files:archive-update", args=[obj.pk]), {
            "archive_class": self.archive_class.pk,
            "customer": self.customer.pk,
            "name": "Viejo editado",
            "contact": "",
            "opening_date": "", "due_date": "",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("contact", resp.context["form"].errors)

    def test_list_exposes_contact_to_the_preview_panel(self):
        # data-contact es de donde lo lee filearchive_split.js para la cabecera
        # de la vista previa; no hay columna en la rejilla.
        FileArchive.objects.create(
            customer=self.customer, archive_class=self.archive_class, name="Con contacto",
            contact="Juana Pérez", file=SimpleUploadedFile("doc.txt", b"x"),
        )
        html = self.client.get(reverse("files:archive-list")).content.decode()
        self.assertIn('data-contact="Juana Pérez"', html)
