"""Stockage des artefacts.

Le front ne télécharge jamais depuis le DGX directement : il reçoit une
URL présignée. Quand MinIO n'est pas configuré, on retombe sur un
répertoire local servi par l'API, avec la même signature d'appel, pour
que le reste du code ignore la différence.
"""

from __future__ import annotations

import logging
import mimetypes
import shutil
import uuid
from pathlib import Path

from config import settings

log = logging.getLogger("factory.storage")

_client = None


def _minio():
    global _client
    if _client is not None:
        return _client
    if not settings.has_minio:
        return None
    try:
        from minio import Minio

        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_key,
            secret_key=settings.minio_secret,
            secure=settings.minio_secure,
        )
        if not _client.bucket_exists(settings.minio_bucket):
            _client.make_bucket(settings.minio_bucket)
        log.info("MinIO en place, seau « %s »", settings.minio_bucket)
    except Exception as exc:  # noqa: BLE001
        log.error("MinIO injoignable (%s) : repli sur le disque local", exc)
        _client = None
    return _client


def _local_root() -> Path:
    root = Path(settings.local_store)
    root.mkdir(parents=True, exist_ok=True)
    return root


def put(source: Path | str, key: str | None = None) -> str:
    """Range un fichier et rend la clé qui permettra de le relire."""
    source = Path(source)
    key = key or f"{uuid.uuid4().hex[:12]}{source.suffix}"

    client = _minio()
    if client is not None:
        ctype = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        client.fput_object(settings.minio_bucket, key, str(source), content_type=ctype)
        return key

    target = _local_root() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return key


def url_for(key: str) -> str:
    """URL de lecture. Présignée et limitée dans le temps sous MinIO ;
    servie par l'API en mode local."""
    client = _minio()
    if client is not None:
        from datetime import timedelta

        return client.presigned_get_object(
            settings.minio_bucket, key, expires=timedelta(seconds=settings.presign_seconds)
        )
    return f"/artifacts/{key}"


def local_path(key: str) -> Path | None:
    """Chemin sur disque, seulement en mode local. Refuse toute clé qui
    tenterait de sortir du répertoire de stockage."""
    if _minio() is not None:
        return None
    root = _local_root().resolve()
    candidate = (root / key).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate
