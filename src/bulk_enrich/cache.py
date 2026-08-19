"""Small thread-safe-on-disk JSON cache with atomic gzip writes."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class JsonCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    @staticmethod
    def digest(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _path(self, namespace: str, key: str) -> Path:
        safe_namespace = "".join(
            character for character in namespace if character.isalnum() or character in "-_"
        )
        if not safe_namespace:
            raise ValueError("cache namespace must contain letters or numbers")
        digest = self.digest(key)
        return self.root / safe_namespace / digest[:2] / f"{digest}.json.gz"

    def get(self, namespace: str, key: str, *, ttl_hours: float) -> dict[str, Any] | None:
        path = self._path(namespace, key)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                envelope = json.load(handle)
        except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
            return None

        stored_at = envelope.get("stored_at")
        value = envelope.get("value")
        if not isinstance(stored_at, (int, float)) or not isinstance(value, dict):
            return None
        if ttl_hours >= 0 and time.time() - float(stored_at) > ttl_hours * 3600:
            return None
        return value

    def put(self, namespace: str, key: str, value: dict[str, Any]) -> Path:
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {"stored_at": time.time(), "value": value}
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
            ) as raw_handle:
                temp_path = Path(raw_handle.name)
                with gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as gzip_handle:
                    payload = json.dumps(
                        envelope, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    gzip_handle.write(payload)
                raw_handle.flush()
                os.fsync(raw_handle.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
        return path
