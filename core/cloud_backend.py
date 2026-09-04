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
import time
import threading
from typing import Optional, Dict, Any

import requests
from PyQt6.QtCore import QSettings

from core import save_crypto
from core.logger import get_logger
from core.version import MIN_CONVEX_BACKEND_VERSION, is_version_outdated

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
        except Exception as e:
            logger.debug(f"Local cloud backend discovery failed: {e}")
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


def _sanitize_chars(game_name: str) -> str:
    return "".join(
        c for c in game_name
        if ("a" <= c.lower() <= "z") or ("0" <= c <= "9") or c in "-_ "
    ).strip()


def legacy_name_key(game_name: str) -> str:
    """Original lossy key ([A-Za-z0-9-_ ], truncated) — kept to find saves
    uploaded before collision-proof keys existed."""
    return _sanitize_chars(game_name)[:128]


def normalize_name_key(game_name: str) -> str:
    """Collision-proof mirror of server-side lib/api.ts sanitizeNameKey.

    Names that already fit the safe charset keep their historical key, so
    existing cloud saves stay reachable. Names containing stripped characters
    ("Dark Souls: Remastered") or truncation get a short raw-name hash suffix,
    so two distinct game names can never sanitize to the same key.
    """
    cleaned = _sanitize_chars(game_name)
    if not cleaned:
        return ""
    lossy = len(cleaned) > 128
    for c in game_name:
        if not (("a" <= c.lower() <= "z") or ("0" <= c <= "9") or c in "-_ "):
            lossy = True
            break
    if lossy:
        suffix = hashlib.sha256(game_name.encode("utf-8")).hexdigest()[:6]
        cleaned = cleaned[:121] + "-" + suffix
    return cleaned


class ConvexSaveBackend:
    def __init__(self, site_url: Optional[str] = None, secret_key: Optional[str] = None):
        self._site_url = site_url
        self._secret_key = secret_key
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._data_key_cache: Optional[str] = None

    @property
    def site_url(self) -> str:
        if self._site_url is not None:
            return self._site_url.rstrip("/")
        return get_site_url()

    @property
    def secret_key(self) -> str:
        if self._secret_key is not None:
            return self._secret_key.strip()
        settings = QSettings("SafeLauncher", "SafeLauncher")
        return str(settings.value("cloud_secret_key", "", type=str) or os.environ.get("SAFELAUNCHER_SECRET_KEY", "")).strip()

    # ------------------------------------------------------------------ #
    # Low-level request plumbing                                         #
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, *, json_body=None, **kwargs) -> requests.Response:
        site = self.site_url
        if not site:
            raise CloudBackendError("Convex endpoint not configured.", "no_endpoint")

        secret_key = self.secret_key
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

    def revoke_device(self, device_id: str) -> bool:
        """Revoke a device on the backend so it no longer registers or counts."""
        resp = self._request(
            "DELETE",
            "/api/devices",
            json_body={"deviceId": device_id},
        )
        return bool(self._check(resp, "Revoke device").get("revoked"))

    def check_health(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Ping backend health endpoint and return latency, status, and version parity."""
        return check_backend_health(self.site_url, self.secret_key, timeout=timeout)

    def import_cloud_save_local(self, cloud_zip_path: str, destination: str, game_path: str = "") -> bool:
        """Extract a downloaded plaintext zip using the shared importer."""
        from core.zip_backup import ZipBackupManager
        ok = ZipBackupManager().import_save(cloud_zip_path, destination, game_path=game_path)
        return ok


def check_backend_health(
    url: Optional[str] = None,
    secret_key: Optional[str] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Probe /api/health on Convex backend, measuring roundtrip latency and verifying version parity."""
    if url is None:
        endpoint = get_site_url().rstrip("/")
    else:
        endpoint = url.strip().rstrip("/")

    if endpoint and not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"

    if not endpoint:
        return {
            "healthy": False,
            "status": "unconfigured",
            "latency_ms": -1,
            "version": "unknown",
            "is_outdated": False,
            "min_version": MIN_CONVEX_BACKEND_VERSION,
            "error": "No Convex site URL configured",
        }

    key = secret_key
    if key is None:
        settings = QSettings("SafeLauncher", "SafeLauncher")
        key = str(settings.value("cloud_secret_key", "") or "").strip()

    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["X-SafeLauncher-Key"] = key

    t0 = time.monotonic()
    try:
        resp = requests.get(f"{endpoint}/api/health", headers=headers, timeout=timeout)
        latency_ms = max(1, int((time.monotonic() - t0) * 1000))

        if resp.status_code == 200:
            data = {}
            try:
                data = resp.json()
            except Exception:
                pass
            ver = str(data.get("version") or "").strip()
            if not ver:
                try:
                    vresp = requests.get(f"{endpoint}/api/version", headers=headers, timeout=2.0)
                    if vresp.status_code == 200:
                        ver = str(vresp.json().get("version") or "").strip()
                except Exception:
                    pass
            if not ver:
                ver = "1.0.0"

            outdated = is_version_outdated(ver, MIN_CONVEX_BACKEND_VERSION)
            return {
                "healthy": True,
                "status": "connected",
                "latency_ms": latency_ms,
                "version": ver,
                "is_outdated": outdated,
                "min_version": MIN_CONVEX_BACKEND_VERSION,
                "error": None,
            }
        elif resp.status_code == 404:
            # Pre-health-check legacy deployment
            return {
                "healthy": True,
                "status": "legacy",
                "latency_ms": latency_ms,
                "version": "1.0.0",
                "is_outdated": is_version_outdated("1.0.0", MIN_CONVEX_BACKEND_VERSION),
                "min_version": MIN_CONVEX_BACKEND_VERSION,
                "error": "Legacy backend: /api/health not implemented",
            }
        elif resp.status_code in (401, 403):
            return {
                "healthy": False,
                "status": "unauthorized",
                "latency_ms": latency_ms,
                "version": "unknown",
                "is_outdated": False,
                "min_version": MIN_CONVEX_BACKEND_VERSION,
                "error": f"Unauthorized (HTTP {resp.status_code}): Secret Key is missing or invalid",
            }
        else:
            return {
                "healthy": False,
                "status": "error",
                "latency_ms": latency_ms,
                "version": "unknown",
                "is_outdated": False,
                "min_version": MIN_CONVEX_BACKEND_VERSION,
                "error": f"Backend returned HTTP {resp.status_code}",
            }
    except Exception as e:
        return {
            "healthy": False,
            "status": "unreachable",
            "latency_ms": -1,
            "version": "unknown",
            "is_outdated": False,
            "min_version": MIN_CONVEX_BACKEND_VERSION,
            "error": str(e),
        }


__all__ = [
    "CloudBackendError", "ConvexSaveBackend", "get_site_url",
    "normalize_name_key", "MAX_SAVE_BYTES", "QUOTA_BYTES",
    "check_backend_health",
]
