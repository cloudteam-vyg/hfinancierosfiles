import os
import uuid
from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords


def file_archive_upload_to(instance, filename):
    # Subcarpeta por UUID: evita colisiones entre archivos de distintos
    # clientes/clases con el mismo nombre.
    return f"file_archives/{instance.id}/{filename}"

# Los tres catálogos (ClassName, ActivityType, ArchiveClass) llevan
# `ordering` a nivel de Meta y no en cada llamada: el orden de las opciones
# de un <select> lo decidían tres sitios distintos con tres políticas
# distintas (_upload_page_context con .order_by("name"), FileArchiveEditForm
# con lo suyo, y FileArchiveUploadForm sin nada -> orden físico de Postgres,
# que se rebaraja tras cualquier UPDATE). Con esto, todo ModelChoiceField y
# todo changelist del admin quedan predecibles sin que cada call site se
# acuerde. NOTA: addAndSelectOption() del JS hace appendChild, así que una
# opción recién creada desde un modal se queda al final hasta la siguiente
# carga -- eso es del cliente, no de aquí.
class ClassName(models.Model):
    name = models.CharField(verbose_name="Nombre", max_length=100)
    description = models.TextField(verbose_name="Descripción", blank=True, null=True)

    def __str__(self):
        return self.name
    class Meta:
        verbose_name = "Clase de cliente"
        verbose_name_plural = "Clases de cliente"
        ordering = ("name",)


class ActivityType(models.Model):
    name = models.CharField(verbose_name="Nombre", max_length=100)
    description = models.TextField(verbose_name="Descripción", blank=True, null=True)

    def __str__(self):
        return self.name
    class Meta:
        verbose_name = "Tipo de actividad"
        verbose_name_plural = "Tipos de actividad"
        ordering = ("name",)


class Customer(models.Model):
    classname = models.ForeignKey(ClassName, on_delete=models.CASCADE, related_name='customers', verbose_name="Clase de cliente")
    # Obligatorio a nivel de MODELO, no solo de formulario: antes era
    # null=True/blank=True y solo QuickCustomerForm lo forzaba a required, así
    # que /clientes/nuevo/ y el admin sí dejaban crear clientes sin nombre y
    # aparecían en todos los <select> como "Sin Nombre - None - X". Arreglarlo
    # en otro formulario habría dejado abierto el camino número tres (shell,
    # fixtures, bulk_create): la regla vive en la única capa que nadie puede
    # saltarse. Ver migración 0003.
    name = models.CharField(verbose_name="Nombre", max_length=100)
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
        # Esta cadena ES la etiqueta del <option> en los <select> de cliente
        # (subida, edición de archivo, personas) y la que devuelve el endpoint
        # de alta rápida en su campo "label" -- un None que se cuele aquí es
        # visible para el usuario, no un detalle de depuración. `group` sigue
        # siendo opcional, así que se arma con las partes presentes en vez de
        # interpolar a ciegas: antes salía "Cliente - None - Comercio".
        partes = [self.name]
        if self.group:
            partes.append(self.group)
        partes.append(self.activity_type.name)
        return " - ".join(partes)

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
    name = models.CharField(verbose_name="Nombre", max_length=100)
    description = models.TextField(verbose_name="Descripción", blank=True, null=True)

    def __str__(self):
        return self.name
    class Meta:
        verbose_name = "Clase de archivo"
        verbose_name_plural = "Clases de archivo"
        ordering = ("name",)




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

    # Poblados por el flujo de subida (ver files/forms.py, files/frontend_forms.py
    # y files/admin.py). `file` es un FileField real: Django lo asigna solo
    # durante la validación del ModelForm (_post_clean), no hace falta código
    # manual para guardarlo. La protección contra edición manual posterior
    # vive en el ModelAdmin (readonly_fields en el form de edición).
    file = models.FileField(verbose_name="Archivo", upload_to=file_archive_upload_to, max_length=255, blank=True)
    original_filename = models.CharField(verbose_name="Nombre original", max_length=255, blank=True)
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

    @property
    def extension(self):
        # Usada por la vista de previsualización (whitelist server-side, ver
        # files/views.py::PREVIEWABLE_EXTENSIONS) y por el template de lista
        # -- nunca por content_type, que es dato del navegador de quien subió
        # el archivo y no es de confiar.
        name = self.original_filename or self.file.name
        return os.path.splitext(name)[1].lower()

    class Meta:
        verbose_name = "3 - Archivo"
        verbose_name_plural = "3 - Archivos"
