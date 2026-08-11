from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse

from . import azure_client
from .models import FileArchive


class FileArchiveAdminForm(forms.ModelForm):
    """Recoge el resultado de la subida directa navegador -> Azure.

    `upload_widget` es un <input type=file> que NUNCA se envía al servidor:
    solo dispara el uploader JS (ver files/static/files/js/azure_direct_upload.js).
    Este último rellena los campos ocultos cuando el blob ya está commiteado
    en Azure, y solo entonces se habilita "Guardar".
    """

    upload_widget = forms.CharField(
        label="Archivo",
        required=False,
        help_text=(
            "Selecciona el archivo. Se sube directamente a Azure Blob Storage; "
            f"espera a que la barra llegue al 100% antes de guardar. "
            f"Tamaño máximo: {settings.MAX_UPLOAD_SIZE_MB} MB."
        ),
        widget=forms.FileInput(attrs={"class": "hf-direct-upload"}),
    )
    file_archive_id = forms.UUIDField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = FileArchive
        fields = (
            "customer", "archive_class", "name", "opening_date", "due_date",
            "original_filename", "blob_path", "file_size", "content_type",
        )
        widgets = {
            "original_filename": forms.HiddenInput,
            "blob_path": forms.HiddenInput,
            "file_size": forms.HiddenInput,
            "content_type": forms.HiddenInput,
        }

    class Media:
        css = {"all": ("files/css/direct_upload.css",)}
        js = ("files/js/azure_direct_upload.js",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # OJO: FileArchive.id tiene default=uuid.uuid4, así que incluso una
        # instancia nueva SIN GUARDAR ya tiene .pk poblado en memoria (los
        # defaults callables se evalúan en __init__, no al guardar) -- por
        # eso NO se puede usar "self.instance.pk" para distinguir alta de
        # edición. La señal correcta es _state.adding: True hasta el primer
        # save() exitoso, independientemente de si la PK ya tiene valor.
        if "upload_widget" in self.fields:
            widget = self.fields["upload_widget"].widget
            widget.attrs["data-sas-endpoint"] = reverse("admin:files_filearchive_upload_sas")
            widget.attrs["data-max-upload-size-mb"] = settings.MAX_UPLOAD_SIZE_MB
            if not self.instance._state.adding:
                # Edición de metadatos: el archivo no se re-sube.
                del self.fields["upload_widget"]

    def clean(self):
        cleaned = super().clean()
        if not self.instance._state.adding:
            return cleaned

        file_id = cleaned.get("file_archive_id")
        blob_path = cleaned.get("blob_path")
        original_filename = cleaned.get("original_filename")
        file_size = cleaned.get("file_size")

        if not (file_id and blob_path and original_filename and file_size):
            raise ValidationError(
                "Debes seleccionar un archivo y esperar a que termine de subirse antes de guardar."
            )

        # El cliente no elige la ruta: se recalcula y se exige igualdad
        # (bloquea path traversal y sobrescritura de blobs ajenos).
        if blob_path != azure_client.build_blob_path(file_id, original_filename):
            raise ValidationError("La ruta del archivo no es válida.")

        max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        if file_size <= 0 or file_size > max_bytes:
            raise ValidationError(
                f"El tamaño declarado está fuera del límite de {settings.MAX_UPLOAD_SIZE_MB} MB."
            )

        if FileArchive.objects.filter(Q(pk=file_id) | Q(blob_path=blob_path)).exists():
            raise ValidationError("Este archivo ya fue registrado.")

        # Defensa real contra un cliente que mienta o falle en silencio: una
        # sola llamada barata a Azure prueba que Put Block List se ejecutó
        # de verdad y que el tamaño coincide con lo declarado.
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
