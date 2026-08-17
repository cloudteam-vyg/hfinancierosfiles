"""Reglas compartidas del alta de archivos.

Viven aquí porque hay DOS puertas de entrada para subir un archivo -- el
frontend propio (files/views.py::file_archive_upload_view) y el Admin
(files/admin.py::FileArchiveAdmin.save_model) -- y ambas deben aplicar
exactamente el mismo límite de tamaño y sellar los mismos metadatos. Cuando
estas reglas estaban duplicadas, cambiarlas en un sitio y no en el otro no
rompía ningún test.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.template.defaultfilters import filesizeformat

from .models import FileArchive


def validate_upload_size(uploaded):
    """Rechaza archivos por encima de MAX_UPLOAD_SIZE_MB.

    Es el mismo tope que nginx/Django aplican a nivel de request (ver
    DEPLOY.md); aquí se comprueba de nuevo para dar un mensaje útil en vez
    de un 413 crudo.
    """
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if uploaded.size > max_bytes:
        raise ValidationError(
            f"El archivo pesa {filesizeformat(uploaded.size)}; "
            f"el máximo es {settings.MAX_UPLOAD_SIZE_MB} MB."
        )
    return uploaded


def stamp_upload_metadata(obj, uploaded, user):
    """Copia al registro los datos del archivo recibido y lo deja PENDING.

    `content_type` se guarda tal como lo declaró el navegador: es dato no
    confiable y NUNCA debe usarse para servir el archivo (ver
    PREVIEWABLE_EXTENSIONS en files/views.py, que resuelve el tipo por
    extensión). Se conserva solo como información.
    """
    obj.original_filename = uploaded.name
    obj.file_size = uploaded.size
    obj.content_type = uploaded.content_type or ""
    obj.uploaded_by = user
    obj.upload_status = FileArchive.UploadStatus.PENDING
    return obj
