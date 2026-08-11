"""Formularios del frontend público (fuera de /admin/).

FileArchiveUploadForm intencionalmente NO reutiliza FileArchiveAdminForm
(files/forms.py): esa clase está acoplada a modelform_factory/
ModelAdmin.get_fields() y a la lógica de "quitar upload_widget en edición"
que no aplica aquí (esta vista solo crea, nunca edita). La lógica de
negocio en clean() SÍ es una copia intencional de
FileArchiveAdminForm.clean() -- files/forms.py no se modifica, así que no
hay forma de compartir ese método sin tocarlo. Si esa restricción se
levanta algún día, ambos clean() deberían delegar en un validador común en
azure_client.py.
"""
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q

from . import azure_client
from .models import FileArchive


class FileArchiveUploadForm(forms.ModelForm):
    file_archive_id = forms.UUIDField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = FileArchive
        fields = (
            "archive_class", "customer", "name", "opening_date", "due_date",
            "original_filename", "blob_path", "file_size", "content_type",
        )
        widgets = {
            "original_filename": forms.HiddenInput,
            "blob_path": forms.HiddenInput,
            "file_size": forms.HiddenInput,
            "content_type": forms.HiddenInput,
        }

    def clean(self):
        cleaned = super().clean()
        file_id = cleaned.get("file_archive_id")
        blob_path = cleaned.get("blob_path")
        original_filename = cleaned.get("original_filename")
        file_size = cleaned.get("file_size")

        if not (file_id and blob_path and original_filename and file_size):
            raise ValidationError(
                "Debes seleccionar un archivo y esperar a que termine de subirse antes de guardar."
            )

        if blob_path != azure_client.build_blob_path(file_id, original_filename):
            raise ValidationError("La ruta del archivo no es válida.")

        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if file_size <= 0 or file_size > max_bytes:
            raise ValidationError(
                f"El tamaño declarado está fuera del límite de {settings.MAX_UPLOAD_SIZE_MB} MB."
            )

        if FileArchive.objects.filter(Q(pk=file_id) | Q(blob_path=blob_path)).exists():
            raise ValidationError("Este archivo ya fue registrado.")

        props = azure_client.get_uploaded_blob_properties(blob_path)
        if props is None:
            raise ValidationError(
                "El archivo no se encuentra en Azure: la subida no terminó correctamente. "
                "Vuelve a seleccionarlo e inténtalo de nuevo."
            )
        if props.size != file_size:
            raise ValidationError(
                f"El tamaño en Azure ({props.size} bytes) no coincide con el declarado "
                f"({file_size} bytes). Vuelve a subir el archivo."
            )

        self._azure_props = props
        return cleaned
