"""Prywatny magazyn plików: lokalny katalog (dev/test) albo Supabase Storage (service_role)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import httpx

from .config import settings


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("Nieprawidłowa ścieżka pliku.")
        return p

    def put(self, key: str, src_path: str) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_path, dest)

    def fetch(self, key: str, dest_path: str) -> None:
        shutil.copyfile(self._path(key), dest_path)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class SupabaseStorage:
    """Dostęp wyłącznie kluczem service_role z backendu; bucket jest prywatny."""

    def __init__(self, url: str, key: str, bucket: str):
        self.base = f"{url}/storage/v1/object/{bucket}"
        self.headers = {"Authorization": f"Bearer {key}", "apikey": key}

    def put(self, key: str, src_path: str) -> None:
        with open(src_path, "rb") as fh:
            r = httpx.post(f"{self.base}/{key}", content=fh.read(), timeout=120,
                           headers={**self.headers, "Content-Type": "application/octet-stream", "x-upsert": "true"})
        r.raise_for_status()

    def fetch(self, key: str, dest_path: str) -> None:
        with httpx.stream("GET", f"{self.base}/{key}", headers=self.headers, timeout=120) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as out:
                for chunk in r.iter_bytes():
                    out.write(chunk)

    def delete(self, key: str) -> None:
        httpx.delete(f"{self.base}/{key}", headers=self.headers, timeout=60)


_storage = None


def storage():
    global _storage
    if _storage is None:
        if settings.storage_mode == "supabase":
            _storage = SupabaseStorage(settings.supabase_url, settings.supabase_service_role_key,
                                       settings.storage_bucket)
        else:
            _storage = LocalStorage(settings.storage_dir)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None


def object_key(org_id: str, kind: str, object_id: str, filename: str) -> str:
    return os.path.join(str(org_id), kind, str(object_id), filename).replace("\\", "/")
