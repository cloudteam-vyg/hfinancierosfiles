"""Acceso centralizado a Azure Blob Storage.

Ningún otro módulo del proyecto debe importar `azure.storage.blob`
directamente. Todo pasa por aquí: facilita mockear en tests y evita
duplicar la lectura de `settings.AZURE_*`.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from urllib.parse import quote

from django.conf import settings

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas

# Solo aceptamos extensiones alfanuméricas cortas: evita que
# `original_filename` inyecte "/", "..", query strings o nombres raros en
# la ruta del blob.
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,10}$")

# Formato canónico de blob_path (usado también para validar renovaciones de
# SAS): "<prefijo>/<uuid>[.ext]".
BLOB_PATH_RE = re.compile(
    r"^[a-z0-9\-]{1,32}/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"(\.[A-Za-z0-9]{1,10})?$"
)


def _account_url() -> str:
    return f"https://{settings.AZURE_ACCOUNT_NAME}.blob.core.windows.net"


def get_blob_service_client() -> BlobServiceClient:
    return BlobServiceClient(account_url=_account_url(), credential=settings.AZURE_ACCOUNT_KEY)


def get_blob_client(blob_path: str, container_name: str | None = None):
    container = container_name or settings.AZURE_CONTAINER_NAME
    return get_blob_service_client().get_blob_client(container=container, blob=blob_path)


def build_blob_url(blob_path: str, container_name: str | None = None) -> str:
    container = container_name or settings.AZURE_CONTAINER_NAME
    return f"{_account_url()}/{container}/{quote(blob_path, safe='/')}"


def build_blob_path(file_id, original_filename: str) -> str:
    """Ruta determinística del blob a partir del UUID del registro.

    El cliente nunca elige esta ruta: el servidor la recalcula y exige
    igualdad antes de aceptar el alta (bloquea path traversal / colisiones).
    """
    suffix = PurePosixPath(original_filename or "").suffix.lower()
    if not _SAFE_SUFFIX.match(suffix):
        suffix = ""
    return f"{settings.AZURE_BLOB_PREFIX}/{file_id}{suffix}"


def build_upload_sas_url(blob_path: str, *, expiry_minutes: int | None = None) -> tuple[str, datetime]:
    """URL con SAS de ESCRITURA para un blob que todavía NO existe.

    Autoriza el flujo Put Block + Put Block List (subida por bloques),
    necesario para archivos grandes.

    - `write=True` es REQUERIDO y SUFICIENTE (cubre tanto crear como
      sobrescribir). NO se usa `create=True`: ese permiso solo está
      soportado desde x-ms-version 2026-04-06, y azure-storage-blob==12.14.0
      firma con sv=2021-08-06 -> daría 403 AuthorizationPermissionMismatch.
    - SAS por blob individual (no por contenedor): mínimo privilegio. Una
      SAS filtrada solo permite sobrescribir ESE blob concreto.
    - Sin read/list/delete: no sirve para exfiltrar ni borrar nada.
    """
    container = settings.AZURE_CONTAINER_NAME
    minutes = expiry_minutes or settings.AZURE_UPLOAD_SAS_EXPIRY_MINUTES
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(minutes=minutes)

    sas_token = generate_blob_sas(
        account_name=settings.AZURE_ACCOUNT_NAME,
        container_name=container,
        blob_name=blob_path,
        account_key=settings.AZURE_ACCOUNT_KEY,
        permission=BlobSasPermissions(write=True),
        expiry=expiry,
        start=now - timedelta(minutes=5),  # absorbe desfase de reloj
        protocol="https",
    )
    return f"{build_blob_url(blob_path, container)}?{sas_token}", expiry


def build_download_sas_url(blob_path: str, original_filename: str = "", *, expiry_minutes: int | None = None) -> str:
    """SAS de LECTURA, forzando descarga en vez de render inline.

    `content_type`/`content_disposition` se firman en la SAS (params
    rsct/rscd), así que sobrescriben lo que el cliente haya puesto en el
    blob al subirlo. Esto neutraliza que alguien suba un .html con
    x-ms-blob-content-type: text/html y consiga un XSS almacenado servido
    desde *.blob.core.windows.net.
    """
    container = settings.AZURE_CONTAINER_NAME
    minutes = expiry_minutes or settings.AZURE_DOWNLOAD_SAS_EXPIRY_MINUTES
    now = datetime.now(timezone.utc)
    safe_name = re.sub(r'[^A-Za-z0-9._\- ]', "_", original_filename or "archivo")

    sas_token = generate_blob_sas(
        account_name=settings.AZURE_ACCOUNT_NAME,
        container_name=container,
        blob_name=blob_path,
        account_key=settings.AZURE_ACCOUNT_KEY,
        permission=BlobSasPermissions(read=True),
        expiry=now + timedelta(minutes=minutes),
        start=now - timedelta(minutes=5),
        protocol="https",
        content_type="application/octet-stream",
        content_disposition=f'attachment; filename="{safe_name}"',
    )
    return f"{build_blob_url(blob_path, container)}?{sas_token}"


def get_uploaded_blob_properties(blob_path: str):
    """Propiedades del blob si existe y está COMMITEADO; None si no.

    Clave de seguridad: un blob con solo bloques sin commitear no es visible
    para Get Blob Properties. Por tanto un resultado no-None aquí prueba que
    Put Block List se ejecutó de verdad.
    """
    try:
        return get_blob_client(blob_path).get_blob_properties()
    except ResourceNotFoundError:
        return None


def delete_blob_quietly(blob_path: str) -> bool:
    try:
        get_blob_client(blob_path).delete_blob(delete_snapshots="include")
        return True
    except ResourceNotFoundError:
        return False
