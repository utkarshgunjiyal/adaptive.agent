"""Local disk storage abstraction.

In preview we persist original PDF bytes to a per-user directory on disk.
In the Docker Compose "production" variant this same interface is backed by
MinIO (see ``docker-compose.yml`` and the sibling ``storage_minio.py`` stub).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.config import settings

_ROOT = Path(settings.storage_dir)


def _user_dir(user_id: str) -> Path:
    d = _ROOT / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def put_object(user_id: str, key: str, data: bytes) -> str:
    """Write bytes under ``<storage>/<user_id>/<key>``. Returns the storage path."""
    path = _user_dir(user_id) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return str(path)


def get_object(user_id: str, key: str) -> bytes:
    path = _user_dir(user_id) / key
    with open(path, "rb") as fh:
        return fh.read()


def delete_user_object(user_id: str, key: str) -> None:
    path = _user_dir(user_id) / key
    if path.exists():
        os.remove(path)


def wipe_user_storage(user_id: str) -> None:
    d = _ROOT / user_id
    if d.exists():
        shutil.rmtree(d)
