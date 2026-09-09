"""Safe, deterministic processing for user profile avatars.

Profile images are public once published.  Keep the processing boundary small:
read bounded bytes, decode with Pillow, remove metadata, crop to a square, and
return a compact self-contained payload.  No source image path is persisted.
"""

from __future__ import annotations

import base64
import hashlib
import io
import warnings
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_AVATAR_BYTES = 256 * 1024
MAX_AVATAR_EDGE = 512
_JPEG_QUALITIES = (88, 82, 76, 70, 64)


class AvatarError(ValueError):
    """Raised when an avatar cannot be safely normalized."""


def _read_source(source: Any) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        raw = bytes(source)
    else:
        try:
            with open(str(source), "rb") as stream:
                raw = stream.read(MAX_SOURCE_BYTES + 1)
        except OSError as exc:
            raise AvatarError(f"Could not read avatar: {exc}") from exc
    if not raw:
        raise AvatarError("The selected avatar is empty.")
    if len(raw) > MAX_SOURCE_BYTES:
        raise AvatarError("The selected avatar is larger than 20 MB.")
    return raw


def normalize_avatar(source: Any) -> dict[str, Any]:
    """Return a bounded, metadata-free JPEG avatar payload.

    The output is deliberately JSON-friendly so it can be stored in the
    encrypted local profile and in the public profile document.
    """
    raw = _read_source(source)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as opened:
                width, height = opened.size
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    raise AvatarError("The selected avatar has unsafe dimensions.")
                image = ImageOps.exif_transpose(opened).convert("RGB")
    except AvatarError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise AvatarError("The selected file is not a supported image.") from exc

    side = min(image.width, image.height)
    left = max(0, (image.width - side) // 2)
    top = max(0, (image.height - side) // 2)
    image = image.crop((left, top, left + side, top + side))
    image.thumbnail((MAX_AVATAR_EDGE, MAX_AVATAR_EDGE), Image.Resampling.LANCZOS)

    encoded = b""
    for edge in (MAX_AVATAR_EDGE, 448, 384, 320):
        candidate = image
        if candidate.width > edge:
            candidate = candidate.resize((edge, edge), Image.Resampling.LANCZOS)
        for quality in _JPEG_QUALITIES:
            output = io.BytesIO()
            candidate.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
            if output.tell() <= MAX_AVATAR_BYTES:
                encoded = output.getvalue()
                break
        if encoded:
            break
    if not encoded:
        raise AvatarError("The avatar could not be compressed below 256 KB.")

    return {
        "mime": "image/jpeg",
        "data_b64": base64.b64encode(encoded).decode("ascii"),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "width": image.width,
        "height": image.height,
        "bytes": len(encoded),
    }


def validate_avatar_payload(value: Any) -> dict[str, Any] | None:
    """Validate an already-normalized avatar without decoding unbounded data."""
    if not isinstance(value, dict):
        return None
    if value.get("mime") != "image/jpeg":
        return None
    encoded = value.get("data_b64")
    if not isinstance(encoded, str) or len(encoded) > ((MAX_AVATAR_BYTES + 2) // 3) * 4 + 16:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    if not raw or len(raw) > MAX_AVATAR_BYTES:
        return None
    if not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
        return None
    digest = hashlib.sha256(raw).hexdigest()
    if str(value.get("sha256", "")) != digest:
        return None
    try:
        width = max(1, min(MAX_AVATAR_EDGE, int(value.get("width", 0))))
        height = max(1, min(MAX_AVATAR_EDGE, int(value.get("height", 0))))
    except (TypeError, ValueError, OverflowError):
        return None
    return {
        "mime": "image/jpeg",
        "data_b64": encoded,
        "sha256": digest,
        "width": width,
        "height": height,
        "bytes": len(raw),
    }
