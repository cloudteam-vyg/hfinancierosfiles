import uuid
from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from . import azure_client

class ClassName(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name
    class Meta:
        verbose_name = "Clase de cliente"
        verbose_name_plural = "Clases de cliente"


class ActivityType(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name
    class Meta:
        verbose_name = "Tipo de actividad"
        verbose_name_plural = "Tipos de actividad"


class Customer(models.Model):
    classname = models.ForeignKey(ClassName, on_delete=models.CASCADE, related_name='customers', verbose_name="Clase de cliente")
    name = models.CharField(verbose_name="Nombre", max_length=100, null=True, blank=True)
    group = models.CharField(verbose_name="Grupo", max_length=100, null=True, blank=True)
    email = models.EmailField(verbose_name="Correo electrónico", unique=True, null=True, blank=True)
    phone_number = models.CharField(verbose_name="Número de teléfono", max_length=20, blank=True, null=True)
    address = models.TextField(verbose_name="Dirección", blank=True, null=True)
    country = models.CharField(verbose_name="País", max_length=100, blank=True, null=True)
    activity_type = models.ForeignKey(ActivityType, on_delete=models.CASCADE, related_name='customers', verbose_name="Tipo de actividad")
    date_of_constitution = models.DateField(verbose_name="Fecha de constitución")
    web_site = models.URLField(verbose_name="Sitio web", blank=True, null=True)
    word_clave = models.CharField(verbose_name="Palabras clave", max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.name or 'Sin Nombre'} - {self.group} - {self.activity_type.name}"

    class Meta:
        verbose_name = "1 - Cliente"
        verbose_name_plural = "1 - Clientes"


class Person(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='persons', verbose_name="Cliente")
    name = models.CharField(verbose_name="Nombre", max_length=100)
    position = models.CharField(verbose_name="Cargo", max_length=100, blank=True, null=True)
    email = models.EmailField(verbose_name="Correo electrónico", unique=True, blank=True, null=True)
    phone_number = models.CharField(verbose_name="Número de teléfono", max_length=20, blank=True, null=True)

    def __str__(self):
        return f"{self.name} - {self.position} - {self.customer.name}"

    class Meta:
        verbose_name = "2 - Persona"
        verbose_name_plural = "2 - Personas"


class ArchiveClass(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name
    class Meta:
        verbose_name = "Clase de archivo"
        verbose_name_plural = "Clases de archivo"




class FileArchive(models.Model):
    class UploadStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pendiente'
        PROCESSING = 'PROCESSING', 'Procesando'
        COMPLETED = 'COMPLETED', 'Completado'
        ERROR = 'ERROR', 'Error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    archive_class = models.ForeignKey(ArchiveClass, on_delete=models.CASCADE, related_name='file_archives', verbose_name="Clase de archivo")
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='file_archives', verbose_name="Cliente")
    name = models.CharField(verbose_name="Nombre del archivo", max_length=150)

    # Poblados por el flujo de subida directa navegador -> Azure (ver
    # files/forms.py y files/admin.py). No son editable=False: el ModelForm
    # del Admin los necesita como campos ocultos reales al dar de alta. La
    # protección contra edición manual posterior vive en el ModelAdmin
    # (readonly_fields en el form de edición), no en el modelo.
    original_filename = models.CharField(verbose_name="Nombre original", max_length=255, blank=True)
    blob_path = models.CharField(verbose_name="Ruta en Azure Blob", max_length=512, unique=True, blank=True)
    file_size = models.BigIntegerField(verbose_name="Tamaño (Bytes)", null=True, blank=True)
    content_type = models.CharField(verbose_name="Tipo MIME", max_length=100, blank=True)

    # Puramente de sistema: nunca forman parte de ningún ModelForm, solo los
    # escriben save_model()/tasks.py.
    upload_status = models.CharField(
        max_length=20,
        choices=UploadStatus.choices,
        default=UploadStatus.PENDING,
        verbose_name="Estado de procesamiento",
        editable=False,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='file_archives', verbose_name="Subido por", editable=False,
    )
    celery_task_id = models.CharField(max_length=255, null=True, blank=True, db_index=True, editable=False, verbose_name="ID de tarea Celery")
    error_message = models.TextField(null=True, blank=True, editable=False, verbose_name="Mensaje de error")
    error_traceback = models.TextField(null=True, blank=True, editable=False, verbose_name="Traceback")
    processed_at = models.DateTimeField(null=True, blank=True, editable=False, verbose_name="Procesado en")

    opening_date = models.DateField(verbose_name="Fecha de apertura", blank=True, null=True)
    due_date = models.DateField(verbose_name="Fecha de vencimiento", blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name="Subido en")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Actualizado en")

    history = HistoricalRecords()

    def __str__(self):
        return f"{self.name} - {self.customer.name}"

    def save(self, *args, **kwargs):
        if not self.blob_path:
            self.blob_path = azure_client.build_blob_path(self.id, self.original_filename)
        super().save(*args, **kwargs)

    def get_download_url(self):
        """Genera una URL firmada de lectura temporal.

        No exige upload_status == COMPLETED: por construcción nunca existe
        una fila sin que el blob ya esté verificado en Azure (ver
        FileArchiveAdminForm.clean()). upload_status describe únicamente el
        post-procesamiento, no la disponibilidad del archivo.
        """
        if not self.blob_path:
            return None
        return azure_client.build_download_sas_url(self.blob_path, self.original_filename)

    class Meta:
        verbose_name = "3 - Archivo"
        verbose_name_plural = "3 - Archivos"
