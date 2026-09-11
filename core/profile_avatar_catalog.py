"""Validation and bounded caching for the public avatar catalog."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QStandardPaths

from core.profile_models import normalize_avatar_id


MAX_AVATAR_CATALOG_ITEMS = 128
MAX_AVATAR_LABEL_LENGTH = 80
MAX_AVATAR_CATEGORY_LENGTH = 32
AVATAR_CATALOG_CACHE_TTL = 24 * 60 * 60
MAX_CACHED_AVATAR_FILES = 24
MAX_CACHED_AVATAR_BYTES = 12 * 1024 * 1024


def normalize_avatar_catalog(value: Any) -> list[dict[str, Any]] | None:
    """Validate the public catalog response before it reaches Qt."""
    if not isinstance(value, list) or len(value) > MAX_AVATAR_CATALOG_ITEMS:
        return None
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        avatar_id = normalize_avatar_id(raw.get("id"))
        if not avatar_id or avatar_id in seen:
            continue
        label = " ".join(str(raw.get("label", avatar_id)).split())[:MAX_AVATAR_LABEL_LENGTH]
        category = " ".join(str(raw.get("category", "Standard")).split())[:MAX_AVATAR_CATEGORY_LENGTH]
        if not label:
            label = avatar_id
        if not category:
            category = "Standard"
        try:
            order = int(raw.get("order", len(normalized)))
            width = int(raw.get("width", 0))
            height = int(raw.get("height", 0))
            size = int(raw.get("bytes", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if not 0 <= order <= 10_000:
            continue
        digest = str(raw.get("sha256", "") or "").strip().lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            continue
        if not 1 <= width <= 2048 or not 1 <= height <= 2048:
            continue
        if width * height > 4_000_000 or not 1 <= size <= 512 * 1024:
            continue
        normalized.append({
            "id": avatar_id,
            "label": label,
            "category": category,
            "order": order,
            "sha256": digest,
            "width": width,
            "height": height,
            "bytes": size,
        })
        seen.add(avatar_id)
    normalized.sort(key=lambda item: (item["order"], item["id"]))
    return normalized


def avatar_cache_directory() -> Path:
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    return Path(root or tempfile.gettempdir()) / "profile_avatars"


def avatar_catalog_cache_path() -> Path:
    return avatar_cache_directory() / "catalog.json"


def avatar_image_cache_path(avatar_id: str, sha256: str) -> Path:
    normalized = normalize_avatar_id(avatar_id)
    digest = str(sha256 or "").strip().lower()
    if not normalized or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("invalid avatar cache key")
    return avatar_cache_directory() / f"{normalized}-{digest}.image"


def load_cached_avatar_catalog(now: float | None = None) -> list[dict[str, Any]] | None:
    path = avatar_catalog_cache_path()
    try:
        if now is None:
            now = time.time()
        if now - path.stat().st_mtime > AVATAR_CATALOG_CACHE_TTL:
            return None
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, TypeError, ValueError):
        return None
    value = payload.get("avatars") if isinstance(payload, dict) else None
    return normalize_avatar_catalog(value)


def save_cached_avatar_catalog(catalog: list[dict[str, Any]]) -> None:
    normalized = normalize_avatar_catalog(catalog)
    if normalized is None:
        return
    directory = avatar_cache_directory()
    temporary: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, raw_path = tempfile.mkstemp(prefix="catalog-", suffix=".tmp", dir=directory)
        temporary = Path(raw_path)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"avatars": normalized}, stream, separators=(",", ":"), sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, avatar_catalog_cache_path())
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def read_cached_avatar(avatar_id: str, sha256: str) -> bytes:
    try:
        path = avatar_image_cache_path(avatar_id, sha256)
    except ValueError:
        return b""
    try:
        data = path.read_bytes()
    except OSError:
        return b""
    if not 0 < len(data) <= 512 * 1024:
        return b""
    return data if hashlib.sha256(data).hexdigest() == str(sha256).strip().lower() else b""


def save_cached_avatar(avatar_id: str, sha256: str, data: bytes) -> None:
    normalized = normalize_avatar_id(avatar_id)
    digest = str(sha256 or "").strip().lower()
    if (
        not normalized
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or len(data) == 0
        or len(data) > 512 * 1024
        or hashlib.sha256(data).hexdigest() != digest
    ):
        return
    directory = avatar_cache_directory()
    temporary: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, raw_path = tempfile.mkstemp(prefix="avatar-", suffix=".tmp", dir=directory)
        temporary = Path(raw_path)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, avatar_image_cache_path(normalized, digest))
        _prune_avatar_images()
    except OSError:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _prune_avatar_images() -> None:
    directory = avatar_cache_directory()
    try:
        entries = []
        for path in directory.glob("*.image"):
            try:
                entries.append((path.stat().st_mtime, path.stat().st_size, path))
            except OSError:
                continue
        entries.sort(key=lambda item: item[0], reverse=True)
        total = 0
        keep: list[Path] = []
        for _mtime, size, path in entries:
            if len(keep) >= MAX_CACHED_AVATAR_FILES or total + size > MAX_CACHED_AVATAR_BYTES:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            keep.append(path)
            total += size
    except OSError:
        return
