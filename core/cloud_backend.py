"""
Convex-backed cloud save transport for SafeLauncher.

All HTTP happens here: function endpoints on the deployment's .convex.site
domain with optional `Authorization: Bearer <secret_key>` headers. Saves are uploaded as
client-encrypted AES-256-GCM envelopes of the standard safelauncher_manifest
v1 zip produced by ZipBackupManager — byte-compatible with the local-folder
engine's archives so restore semantics stay identical across backends.

Every request carries an explicit timeout; downloads are streamed through a
temp file with a hard size cap and atomic rename; data keys are cached in memory.
"""

import hashlib
import json
import os
import tempfile
from typing import Optional

import requests
from PyQt6.QtCore import QSettings

import threading
from typing import Optional

import requests
from PyQt6.QtCore import QSettings

from core import save_crypto
from core.logger import get_logger

logger = get_logger("CloudBackend")

MAX_SAVE_BYTES = 50 * 1024 * 1024   # 50 MB max per save archive
QUOTA_BYTES = 1024 * 1024 * 1024    # 1 GB per private deployment
_DOWNLOAD_STREAM_TIMEOUT = (10, 60)

# Default endpoint (configured per-user via QSettings or 'safelauncher --setup-cloud')
DEFAULT_SITE_URL = ""


class CloudBackendError(Exception):
    """Cloud operation failure carrying a machine-readable code."""

    def __init__(self, message: str, code: str = "unknown", status: int = 0,
                 extra: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.extra = extra or {}


def get_site_url() -> str:
    settings = QSettings("SafeLauncher", "SafeLauncher")
    url = str(
        os.environ.get("SAFELAUNCHER_CONVEX_SITE_URL", "")
        or settings.value("convex_site_url", "", type=str)
        or DEFAULT_SITE_URL
    ).strip().rstrip("/")
    if not url:
        try:
            from core.cloud_detector import discover_local_cloud_backend
            discovered = discover_local_cloud_backend()
            if discovered:
                return discovered.rstrip("/")
        except Exception:
            pass
    return url


import platform
import uuid

def get_device_identity() -> tuple[str, str, str]:
    """Return (device_id, device_name, platform_name) persisted in QSettings."""
    settings = QSettings("SafeLauncher", "SafeLauncher")
    dev_id = str(settings.value("cloud_device_id", "") or "").strip()
    if not dev_id:
        dev_id = str(uuid.uuid4())[:8]
        settings.setValue("cloud_device_id", dev_id)

    dev_name = str(settings.value("cloud_device_name", "") or "").strip()
    if not dev_name:
        dev_name = platform.node() or "SafeLauncher Device"

    dev_plat = platform.system() or "Linux"
    return dev_id, dev_name, dev_plat


def normalize_name_key(game_name: str) -> str:
    """Mirror of server-side lib/api.ts sanitizeNameKey ([A-Za-z0-9-_ ])."""
    cleaned = "".join(
        c for c in game_name
        if ("a" <= c.lower() <= "z") or ("0" <= c <= "9") or c in "-_ "
    ).strip()
    return cleaned[:128]


class ConvexSaveBackend:
    def __init__(self):
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._data_key_cache: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Low-level request plumbing                                         #
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, *, json_body=None, **kwargs) -> requests.Response:
        site = get_site_url()
        if not site:
            raise CloudBackendError("Convex endpoint not configured.", "no_endpoint")

        secret_key = QSettings("SafeLauncher", "SafeLauncher").value("cloud_secret_key", "", type=str).strip() or os.environ.get("SAFELAUNCHER_SECRET_KEY", "")
        headers = kwargs.pop("headers", {})
        if secret_key:
            headers["X-SafeLauncher-Key"] = secret_key
            headers["Authorization"] = f"Bearer {secret_key}"

        dev_id, dev_name, dev_plat = get_device_identity()
        headers["X-SafeLauncher-Device-Id"] = dev_id
        headers["X-SafeLauncher-Device-Name"] = dev_name
        headers["X-SafeLauncher-Platform"] = dev_plat

        timeout = kwargs.pop("timeout", 20)
        with self._lock:
            resp = self.session.request(
                method,
                f"{site}{path}",
                headers=headers,
                timeout=timeout,
                json=json_body,
                **kwargs,
            )
        return resp

    @staticmethod
    def _check(resp: requests.Response, context: str) -> dict:
        try:
            payload = resp.json()
        except ValueError:
            payload = {}

        if resp.status_code in (401, 403):
            raise CloudBackendError(
                str(payload.get("error") or f"{context}: Authentication failed (check Secret Key)."),
                "auth",
                resp.status_code,
                payload,
            )

        if resp.status_code >= 400:
            raise CloudBackendError(
                str(payload.get("error") or f"{context} failed ({resp.status_code})"),
                str(payload.get("code") or "http_error"),
                resp.status_code,
                {k: v for k, v in payload.items() if k not in ("error", "code")},
            )
        return payload

    # ------------------------------------------------------------------ #
    # Account / metadata                                                 #
    # ------------------------------------------------------------------ #

    def account(self) -> dict:
        """Quota overview: {bytesUsed, quotaBytes, games:[…], concurrentDevices, devices:[…]}."""
        return self._check(self._request("GET", "/api/me"), "Account fetch")

    def heartbeat(self) -> dict:
        """Send a lightweight heartbeat ping to register presence."""
        dev_id, dev_name, dev_plat = get_device_identity()
        return self._check(
            self._request(
                "POST",
                "/api/heartbeat",
                json_body={"deviceId": dev_id, "deviceName": dev_name, "platform": dev_plat},
            ),
            "Heartbeat",
        )

    def data_key_b64(self) -> str:
        if not self._data_key_cache:
            self._data_key_cache = self._check(self._request("GET", "/api/key"), "Key fetch")["dataKeyB64"]
        return self._data_key_cache

    def invalidate_key_cache(self) -> None:
        self._data_key_cache = None

    def list_games(self) -> dict:
        return self._check(self._request("GET", "/api/games"), "Listing")

    # ------------------------------------------------------------------ #
    # Upload                                                             #
    # ------------------------------------------------------------------ #

    def upload_plaintext_zip(self, name_key: str, display_name: str,
                             plaintext_zip_path: str,
                             source_max_mtime: float) -> dict:
        """Encrypt + upload a zipped save archive; returns confirm result."""
        try:
            with open(plaintext_zip_path, "rb") as f:
                plaintext = f.read()
        except OSError as e:
            raise CloudBackendError(f"Could not read staged save: {e}") from e
        plain_sha = hashlib.sha256(plaintext).hexdigest()

        if len(plaintext) > MAX_SAVE_BYTES:
            raise CloudBackendError(
                f"Save archive ({len(plaintext) / (1024*1024):.1f} MB) exceeds maximum allowed size ({MAX_SAVE_BYTES / (1024*1024):.1f} MB).",
                "payload_too_large", 413
            )

        listing = self.list_games()
        existing = next((g for g in listing.get("games", []) if g.get("nameKey") == name_key), None)
        if existing and existing.get("versions"):
            top = existing["versions"][0]
            # ONLY skip if the payload content hash matches identically
            if top.get("plainSha256") == plain_sha:
                logger.info(f"Cloud already holds identical save for '{name_key}'.")
                return {"skipped": True}

        envelope = save_crypto.encrypt_save(plaintext, self.data_key_b64())
        declared = len(envelope)

        init = self._check(
            self._request(
                "POST",
                f"/api/games/{requests.utils.quote(name_key)}/init-upload",
                json_body={
                    "displayName": display_name,
                    "plainSha256": plain_sha,
                    "sourceMaxMtime": int(source_max_mtime),
                    "declaredSizeBytes": declared,
                },
            ),
            "Upload init",
        )

        with self._lock:
            post = self.session.post(
                init["uploadUrl"],
                data=envelope,
                headers={"Content-Type": "application/octet-stream"},
                timeout=(10, 120),
            )
        if post.status_code != 200:
            raise CloudBackendError(
                f"Save upload rejected ({post.status_code}).", "upload_failed",
                post.status_code,
            )
        storage_id = post.json().get("storageId")
        if not storage_id:
            raise CloudBackendError("Upload succeeded but no id was returned.",
                                    "upload_failed", 502)

        return self._check(
            self._request(
                "POST",
                f"/api/games/{requests.utils.quote(name_key)}/confirm-upload",
                json_body={"saveId": init["saveId"], "storageId": storage_id},
            ),
            "Upload confirm",
        )

    # ------------------------------------------------------------------ #
    # Download                                                           #
    # ------------------------------------------------------------------ #

    def download_to_temp(self, name_key: str,
                         version: Optional[int] = None) -> tuple[str, dict]:
        """Fetch + decrypt the latest (or requested) save into a temp zip.

        Returns (plaintext_zip_path, meta{version,sizeBytes}); caller must
        eventually os.unlink the returned file.
        """
        query = f"?version={version}" if version else ""
        ref = self._check(
            self._request("GET", f"/api/games/{requests.utils.quote(name_key)}/download{query}"),
            "Download resolve",
        )

        uid = os.getuid() if hasattr(os, "getuid") else "u"
        dest_dir = os.path.join(tempfile.gettempdir(), f"safelauncher-dl-{uid}")
        os.makedirs(dest_dir, mode=0o700, exist_ok=True)
        fd, enc_path = tempfile.mkstemp(prefix=".sl-save-", suffix=".enc", dir=dest_dir)

        try:
            with self._lock:
                stream_req = self.session.get(ref["url"], stream=True,
                                              timeout=_DOWNLOAD_STREAM_TIMEOUT)
            with stream_req as resp:
                if resp.status_code != 200:
                    raise CloudBackendError("Blob fetch failed.", "download_failed",
                                            resp.status_code)
                total = 0
                limit = MAX_SAVE_BYTES * 2
                with os.fdopen(fd, "wb") as out:
                    for chunk in resp.iter_content(chunk_size=65536):
                        total += len(chunk)
                        if total > limit:
                            raise CloudBackendError("Blob exceeds expected size cap.")
                        out.write(chunk)
        except Exception:
            try:
                os.unlink(enc_path)
            except OSError:
                pass
            raise

        try:
            with open(enc_path, "rb") as f:
                envelope = f.read()
            plaintext = save_crypto.decrypt_save(envelope, self.data_key_b64())
            fd2, plain_path = tempfile.mkstemp(prefix=".sl-save-", suffix=".zip", dir=dest_dir)
            with os.fdopen(fd2, "wb") as out:
                out.write(plaintext)
            os.chmod(plain_path, 0o600)
            return plain_path, {"version": ref["version"], "sizeBytes": ref["sizeBytes"]}
        finally:
            try:
                os.unlink(enc_path)
            except OSError:
                pass

    def delete_generation(self, name_key: str, version: int) -> bool:
        resp = self._request(
            "DELETE",
            f"/api/games/{requests.utils.quote(name_key)}",
            json_body={"version": version},
        )
        return bool(self._check(resp, "Delete").get("deleted"))

    def import_cloud_save_local(self, cloud_zip_path: str, destination: str) -> bool:
        """Extract a downloaded plaintext zip using the shared importer."""
        from core.zip_backup import ZipBackupManager
        ok = ZipBackupManager().import_save(cloud_zip_path, destination)
        return ok


__all__ = [
    "CloudBackendError", "ConvexSaveBackend", "get_site_url",
    "normalize_name_key", "MAX_SAVE_BYTES", "QUOTA_BYTES",
]
