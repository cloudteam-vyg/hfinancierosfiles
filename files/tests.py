from pathlib import Path

from django.test import SimpleTestCase, TestCase

# Create your tests here.

ADMIN_JS = Path(__file__).parent / "static/files/js/azure_direct_upload.js"
SHARED_JS = Path(__file__).parent / "static/files/js/azure_chunked_uploader.js"
ADMIN_END_MARKER = "// ---------- integración con el formulario del Admin"
SHARED_END_MARKER = "window.HFAzureUploader"


def _engine_body(text, end_marker):
    """Extrae solo el motor de subida (todo entre "use strict"; y el
    marcador de cierre propio de cada archivo), ignorando los encabezados
    de comentario y los envoltorios IIFE, que legítimamente difieren entre
    el archivo del Admin y el módulo compartido."""
    _, _, after_strict = text.partition('"use strict";')
    body, _, _ = after_strict.partition(end_marker)
    return body.strip()


class SharedUploadEngineSyncTest(SimpleTestCase):
    """files/static/files/js/azure_direct_upload.js (Admin, estabilizado)
    y files/static/files/js/azure_chunked_uploader.js (frontend nuevo) son
    dos copias deliberadas del mismo motor de subida por bloques (ver
    files/frontend_forms.py y el plan de implementación). Esta prueba
    falla si alguien corrige un bug de reintentos/SAS en un archivo y se
    olvida del otro.
    """

    def test_admin_and_shared_engine_have_not_diverged(self):
        admin_body = _engine_body(ADMIN_JS.read_text(), ADMIN_END_MARKER)
        shared_body = _engine_body(SHARED_JS.read_text(), SHARED_END_MARKER)
        self.assertEqual(
            admin_body,
            shared_body,
            "El motor de subida (reintentos, SAS, put block) divergió entre "
            "azure_direct_upload.js y azure_chunked_uploader.js. Sincroniza "
            "ambos archivos antes de continuar.",
        )
