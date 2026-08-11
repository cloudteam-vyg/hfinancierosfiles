from celery import Task, shared_task
from celery.utils.log import get_task_logger
from django.core.cache import cache
from django.utils import timezone

from azure.core.exceptions import HttpResponseError, ServiceRequestError, ServiceResponseError

from .models import FileArchive

logger = get_task_logger(__name__)

AZURE_RETRYABLE_EXCEPTIONS = (
    ServiceRequestError,
    ServiceResponseError,
    HttpResponseError,
    ConnectionError,
    TimeoutError,
)

LOCK_TIMEOUT_SECONDS = 60 * 15  # cota superior generosa de duración esperada


class RecordErrorOnFailureTask(Task):
    """Base común: si la tarea falla de forma terminal (reintentos agotados,
    o una excepción que autoretry_for no cubre), Celery llama a on_failure()
    UNA sola vez y aquí se deja constancia en el propio FileArchive.

    autoretry_for intercepta la excepción ANTES de que un except propio la
    vea -- por eso el registro de error vive aquí, no en un try/except
    dentro del cuerpo de la tarea (eso le "robaría" la excepción al
    mecanismo de reintento automático).
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        file_archive_id = args[0] if args else kwargs.get("file_archive_id")
        if not file_archive_id:
            return
        FileArchive.objects.filter(pk=file_archive_id).update(
            upload_status=FileArchive.UploadStatus.ERROR,
            error_message=str(exc) or exc.__class__.__name__,
            error_traceback=einfo.traceback if einfo is not None else "",
            celery_task_id=task_id,
        )
        logger.error("FileArchive %s: fallo terminal en %s: %s", file_archive_id, task_id, exc)


@shared_task(
    bind=True,
    base=RecordErrorOnFailureTask,
    autoretry_for=AZURE_RETRYABLE_EXCEPTIONS,
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def run_post_processing(self, file_archive_id):
    lock_key = f"post_processing:lock:{file_archive_id}"
    if not cache.add(lock_key, "1", timeout=LOCK_TIMEOUT_SECONDS):
        logger.info("FileArchive %s tiene otra ejecución en curso; se omite ésta.", file_archive_id)
        return None

    try:
        try:
            obj = FileArchive.objects.get(pk=file_archive_id)
        except FileArchive.DoesNotExist:
            logger.warning("FileArchive %s ya no existe; se descarta.", file_archive_id)
            return None

        if obj.upload_status == FileArchive.UploadStatus.COMPLETED:
            return str(obj.pk)  # idempotente: ejecución duplicada

        obj.upload_status = FileArchive.UploadStatus.PROCESSING
        obj.celery_task_id = self.request.id
        obj.save(update_fields=["upload_status", "celery_task_id", "updated_at"])

        # --- Punto de extensión ---
        # Aquí se agregaría lógica de negocio futura (OCR, validación de
        # contenido, notificaciones, indexado, miniaturas, etc.). Hoy es
        # intencionalmente mínimo: el blob ya está verificado en Azure
        # (ver FileArchiveAdminForm.clean()), solo queda confirmar el
        # cierre del ciclo de vida.
        # ---------------------------

        obj.upload_status = FileArchive.UploadStatus.COMPLETED
        obj.processed_at = timezone.now()
        # Limpia cualquier error de un intento anterior: COMPLETED debe
        # significar un estado limpio sin importar quién invocó la tarea
        # (la acción "Reintentar" ya lo hace, pero la tarea no debe
        # depender de eso para ser correcta por sí misma).
        obj.error_message = None
        obj.error_traceback = None
        obj.save(update_fields=[
            "upload_status", "processed_at", "error_message", "error_traceback", "updated_at",
        ])
        return str(obj.pk)
    finally:
        cache.delete(lock_key)
