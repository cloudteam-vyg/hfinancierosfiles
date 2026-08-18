import io
import shutil
import tempfile

from pypdf import PdfReader, PdfWriter

from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ActivityType, ArchiveClass, ClassName, Customer, FileArchive
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
        # Un usuario nuevo se asigna automáticamente al grupo "Usuarios
        # estándar" (ver authentication/signals.py), que SÍ tiene
        # change_filearchive -- hay que quitarlo explícitamente para
        # simular a alguien realmente sin el permiso.
        user.groups.clear()
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

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

    def _payload(self):
        return {
            "archive_class": self.archive_class.pk,
            "customer": self.customer.pk,
            "name": "Sin archivo",
            "opening_date": "", "due_date": "",
        }

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
    """CRUD de "Clase de cliente" en el frontend propio: mismo patrón y
    mismas reglas de permisos que Cliente/Persona."""

    def setUp(self):
        self.classname = ClassName.objects.create(name="Persona moral")

    def _login(self, *codenames):
        user = User.objects.create_user(f"cn_{'_'.join(codenames) or 'plain'}", password="x")
        user.groups.clear()  # el grupo estándar se asigna solo; aquí se controla
        for codename in codenames:
            user.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(user)
        return user

    def test_list_is_visible_to_any_authenticated_user(self):
        self._login()
        resp = self.client.get(reverse("files:classname-list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Persona moral")

    def test_create_requires_permission(self):
        self._login()
        resp = self.client.get(reverse("files:classname-create"))
        self.assertEqual(resp.status_code, 403)

    def test_create_with_permission(self):
        self._login("add_classname")
        resp = self.client.post(
            reverse("files:classname-create"),
            {"name": "Persona física", "description": "Contribuyente individual"},
        )
        self.assertRedirects(resp, reverse("files:classname-list"))
        self.assertTrue(ClassName.objects.filter(name="Persona física").exists())

    def test_update_with_permission(self):
        self._login("change_classname")
        resp = self.client.post(
            reverse("files:classname-update", args=[self.classname.pk]),
            {"name": "Persona moral S.A.", "description": ""},
        )
        self.assertRedirects(resp, reverse("files:classname-list"))
        self.classname.refresh_from_db()
        self.assertEqual(self.classname.name, "Persona moral S.A.")

    def test_delete_requires_permission_not_granted_to_standard_group(self):
        # El grupo "Usuarios estándar" nunca recibe delete (signals.py).
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
