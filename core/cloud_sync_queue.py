"""Durable, coalescing queue for local-first cloud metadata sync."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from database import _APP_DATA_DIR


class PendingCloudSyncQueue:
    """Persist pending cloud writes without storing credentials or payloads.

    The local SQLite database remains the source of truth. Queue entries only
    record that a context/operation needs another reconciliation attempt and
    the local digest observed when it was queued. Repeated edits coalesce into
    one entry per context and operation.
    """

    VERSION = 1
    MAX_ITEMS = 64

    def __init__(self, path: str | os.PathLike | None = None) -> None:
        self.path = Path(path) if path else Path(_APP_DATA_DIR) / "pending_cloud_sync.json"
        self._lock = threading.RLock()

    def enqueue(
        self,
        context: str,
        operation: str = "profile",
        *,
        local_digest: str = "",
        queued_at: float | None = None,
    ) -> bool:
        context = str(context or "").strip()
        operation = str(operation or "").strip()
        if not context or not operation:
            return False
        with self._lock:
            document = self._read()
            items = self._items(document)
            now = float(time.time() if queued_at is None else queued_at)
            replacement = {
                "context": context[:256],
                "operation": operation[:64],
                "local_digest": str(local_digest or "")[:128],
                "queued_at": max(0.0, now),
            }
            replaced = False
            for index, item in enumerate(items):
                if item["context"] == replacement["context"] and item["operation"] == replacement["operation"]:
                    items[index] = replacement
                    replaced = True
                    break
            if not replaced:
                items.append(replacement)
            items = sorted(items, key=lambda item: item["queued_at"])[-self.MAX_ITEMS:]
            return self._write({"version": self.VERSION, "items": items})

    def pending(self, context: str | None = None, operation: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = self._items(self._read())
        if context is not None:
            items = [item for item in items if item["context"] == str(context)]
        if operation is not None:
            items = [item for item in items if item["operation"] == str(operation)]
        return [dict(item) for item in items]

    def acknowledge(
        self,
        context: str,
        operation: str = "profile",
        *,
        local_digest: str | None = None,
    ) -> bool:
        """Remove a completed entry, preserving a newer queued digest."""
        with self._lock:
            document = self._read()
            items = self._items(document)
            kept = []
            removed = False
            for item in items:
                matches = item["context"] == str(context) and item["operation"] == str(operation)
                digest_matches = local_digest is None or item["local_digest"] == str(local_digest)
                if matches and digest_matches:
                    removed = True
                    continue
                kept.append(item)
            if removed:
                self._write({"version": self.VERSION, "items": kept})
            return removed

    def clear(self) -> bool:
        with self._lock:
            return self._write({"version": self.VERSION, "items": []})

    def _read(self) -> dict[str, Any]:
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                value = json.load(stream)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    @classmethod
    def _items(cls, document: dict[str, Any]) -> list[dict[str, Any]]:
        raw_items = document.get("items", []) if isinstance(document, dict) else []
        result = []
        seen = set()
        for raw in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(raw, dict):
                continue
            context = str(raw.get("context", "") or "").strip()[:256]
            operation = str(raw.get("operation", "") or "").strip()[:64]
            if not context or not operation or (context, operation) in seen:
                continue
            seen.add((context, operation))
            try:
                queued_at = max(0.0, float(raw.get("queued_at", 0) or 0))
            except (TypeError, ValueError, OverflowError):
                queued_at = 0.0
            result.append({
                "context": context,
                "operation": operation,
                "local_digest": str(raw.get("local_digest", "") or "")[:128],
                "queued_at": queued_at,
            })
            if len(result) >= cls.MAX_ITEMS:
                break
        return result

    def _write(self, document: dict[str, Any]) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                self.path.parent.chmod(0o700)
            except OSError:
                pass
            fd, temporary = tempfile.mkstemp(
                prefix=".pending-cloud-",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(document, stream, separators=(",", ":"), sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            return True
        except OSError:
            return False


__all__ = ["PendingCloudSyncQueue"]
