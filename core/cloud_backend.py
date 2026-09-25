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
import base64
import json
import os
import tempfile
import time
import threading
from typing import Any, BinaryIO, Dict, Optional

import requests
from PyQt6.QtCore import QSettings
from urllib.parse import urlsplit, urlunsplit

from core import save_crypto
from core.logger import get_logger
from core.version import MIN_CONVEX_BACKEND_VERSION, is_version_outdated
from core.secret_store import get_secret
from core.request_contracts import classify_remote_error
from core.save_models import SaveOperationCancelled

logger = get_logger("CloudBackend")

QUOTA_BYTES = 1024 * 1024 * 1024    # 1 GB free tier per private deployment
BASE_FREE_QUOTA_BYTES = QUOTA_BYTES
# A single archive may use the complete free-tier allocation. Larger storage
# is an account/referral entitlement, not an artificial 50 MB per-save cap.
MAX_SAVE_BYTES = QUOTA_BYTES
_DOWNLOAD_STREAM_TIMEOUT = (10, 60)


class _ProgressUploadFile:
    """File-like upload body with a stable length and cooperative progress.

    ``requests`` treats an iterator as a chunked body even when callers add a
    ``Content-Length`` header themselves.  That produces both
    ``Transfer-Encoding: chunked`` and ``Content-Length`` on the wire, which
    Convex's signed storage upload endpoint rejects with HTTP 400.  A real
    file-like body lets requests calculate and send only ``Content-Length``.
    """

    def __init__(self, path: str, size: int, cancel_check=None, progress_callback=None):
        self._stream: BinaryIO = open(path, "rb")
        self._size = int(size)
        self._sent = 0
        self._cancel_check = cancel_check
        self._progress_callback = progress_callback

    def __len__(self) -> int:
        return self._size

    def tell(self) -> int:
        return self._stream.tell()

    def fileno(self) -> int:
        return self._stream.fileno()

    def read(self, amount: int = -1) -> bytes:
        if self._cancel_check and self._cancel_check():
            raise SaveOperationCancelled()
        chunk = self._stream.read(amount)
        if chunk:
            self._sent += len(chunk)
            if self._progress_callback is not None:
                self._progress_callback(self._sent / max(1, self._size))
        return chunk

    def close(self) -> None:
        self._stream.close()


# Default endpoint (configured per-user via QSettings or 'safelauncher --setup-cloud')
DEFAULT_SITE_URL = ""


class CloudBackendError(Exception):
    """Cloud operation failure carrying a machine-readable code."""

    def __init__(self, message: str, code: str = "unknown", status: int = 0,
                 extra: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.status_code = status
        self.extra = extra or {}
        self.category = classify_remote_error(self).value


def describe_cloud_error(error: Exception) -> str:
    """Return actionable guidance for a cloud error."""
    status = getattr(error, "status_code", 0) or getattr(error, "status", 0)
    code = str(getattr(error, "code", "") or "")
    if status == 404:
        return (
            "Cloud endpoint not found (HTTP 404). The Site URL is reachable, "
            "but this deployment is missing the SafeLauncher API endpoint. "
            "Check that the URL is the deployed *.convex.site URL (not a "
            "dashboard or *.convex.cloud URL), then redeploy the latest "
            "SafeLauncherCloud backend with `npm install` and `npx convex deploy --yes`. "
            "After deployment, run the backend probe again."
        )
    if status in (401, 403):
        return (
            "Cloud authentication failed. Check the Secret Access Key in "
            "Settings → Cloud and make sure it matches the deployed backend."
        )
    if status == 413 or code in ("payload_too_large", "save_too_large"):
        return (
            "The cloud backend rejected this save because it is too large. "
            "Older deployments still enforce the former 50 MB limit; choose "
            "Setup Cloud → Redeploy existing backend and deploy SafeLauncherCloud "
            "v1.7.0 or newer. The current free-tier limit is 1 GB total storage."
        )
    if status == 507 or code == "quota_exceeded":
        return (
            "Cloud storage quota exceeded. The free tier is 1 GB; remove old cloud "
            "generations or use the referral expansion option before uploading again."
        )
    return str(error)


def normalize_site_url(value: str | None) -> str:
    """Normalize Convex client URLs to the HTTP-function ``.convex.site`` host.

    Convex CLI commonly writes ``CONVEX_URL`` as the client/database host
    (``*.convex.cloud``), while SafeLauncher calls HTTP actions such as
    ``/api/health`` on the paired ``*.convex.site`` host.  Treating the cloud
    URL as a site URL makes a successful deploy look incomplete because the
    health probe returns no SafeLauncher API response.
    """
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return raw
    hostname = (parsed.hostname or "").lower()
    if not hostname.endswith(".convex.cloud"):
        return raw
    normalized_host = hostname[: -len(".convex.cloud")] + ".convex.site"
    if parsed.port is not None:
        normalized_host = f"{normalized_host}:{parsed.port}"
    netloc = normalized_host
    if parsed.username or parsed.password:
        # This should never be needed for a Convex URL, but preserve parsing
        # semantics without logging or inventing credential data.
        auth = parsed.username or ""
        if parsed.password is not None:
            auth += f":{parsed.password}"
        netloc = f"{auth}@{netloc}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)).rstrip("/")


def get_site_url() -> str:
    settings = QSettings("SafeLauncher", "SafeLauncher")
    url = str(
        os.environ.get("SAFELAUNCHER_CONVEX_SITE_URL", "")
        or settings.value("convex_site_url", "", type=str)
        or DEFAULT_SITE_URL
    )
    url = normalize_site_url(url)
    if not url:
        try:
            from core.cloud_detector import discover_local_cloud_backend
            discovered = discover_local_cloud_backend()
            if discovered:
                return normalize_site_url(discovered)
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

    def close(self) -> None:
        """Close the pooled HTTP transport after in-flight work has finished."""
        with self._lock:
            try:
                self.session.close()
            except Exception:
                pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @property
    def site_url(self) -> str:
        if self._site_url is not None:
            return normalize_site_url(self._site_url)
        return get_site_url()

    @property
    def secret_key(self) -> str:
        if self._secret_key is not None:
            return self._secret_key.strip()
        return get_secret("cloud_secret_key", os.environ.get("SAFELAUNCHER_SECRET_KEY", ""), legacy_name="cloud_secret_key")

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
        finally:
            # JSON endpoints are fully consumed here. Close at the transport
            # boundary so pooled connections are not retained per operation.
            try:
                resp.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Account / metadata                                                 #
    # ------------------------------------------------------------------ #

    def account(self) -> dict:
        """Quota overview: {bytesUsed, quotaBytes, games:[…], concurrentDevices, devices:[…]}."""
        return self._check(self._request("GET", "/api/me", timeout=6), "Account fetch")

    def heartbeat(self) -> dict:
        """Send a lightweight heartbeat ping to register presence."""
        dev_id, dev_name, dev_plat = get_device_identity()
        return self._check(
            self._request(
                "POST",
                "/api/heartbeat",
                json_body={"deviceId": dev_id, "deviceName": dev_name, "platform": dev_plat},
                timeout=6,
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
        return self._check(self._request("GET", "/api/games", timeout=6), "Listing")

    def get_game_metadata(self, name_key: str) -> dict:
        """Fetch the separate encrypted SafeLauncher metadata record."""
        response = self._check(
            self._request("GET", f"/api/games/{requests.utils.quote(name_key)}/metadata", timeout=6),
            "Metadata fetch",
        )
        encoded = response.get("data")
        if not encoded:
            return {"revision": response.get("revision"), "metadata": {}}
        try:
            plaintext = save_crypto.decrypt_save(base64.b64decode(encoded), self.data_key_b64())
            metadata = json.loads(plaintext.decode("utf-8"))
            return {"revision": response.get("revision"), "metadata": metadata if isinstance(metadata, dict) else {}}
        except Exception as e:
            raise CloudBackendError(f"Metadata decrypt failed: {e}", "metadata_corrupt") from e

    def put_game_metadata(self, name_key: str, metadata: dict, revision=None) -> dict:
        """Encrypt and conditionally replace the separate metadata record."""
        plaintext = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
        encoded = base64.b64encode(save_crypto.encrypt_save(plaintext, self.data_key_b64())).decode("ascii")
        body = {"data": encoded}
        if revision is not None:
            body["revision"] = revision
        return self._check(
            self._request("PUT", f"/api/games/{requests.utils.quote(name_key)}/metadata", json_body=body, timeout=10),
            "Metadata upload",
        )

    def get_achievement_profile(self) -> dict:
        """Fetch the encrypted account-wide append-only achievement ledger."""
        response = self._check(self._request("GET", "/api/profile/achievements", timeout=6), "Achievement profile fetch")
        encoded = response.get("data")
        if not encoded:
            return {"revision": response.get("revision", 0), "profile": {}}
        try:
            plaintext = save_crypto.decrypt_save(base64.b64decode(encoded), self.data_key_b64())
            profile = json.loads(plaintext.decode("utf-8"))
            return {"revision": response.get("revision", 0), "profile": profile if isinstance(profile, dict) else {}}
        except Exception as e:
            raise CloudBackendError(f"Achievement profile decrypt failed: {e}", "profile_corrupt") from e

    def get_profile(self) -> dict:
        """Fetch the encrypted generalized launcher profile."""
        response = self._check(self._request("GET", "/api/profile", timeout=6), "Launcher profile fetch")
        encoded = response.get("data")
        if not encoded:
            return {"revision": response.get("revision", 0), "profile": {}}
        try:
            plaintext = save_crypto.decrypt_save(base64.b64decode(encoded), self.data_key_b64())
            profile = json.loads(plaintext.decode("utf-8"))
            return {"revision": response.get("revision", 0), "profile": profile if isinstance(profile, dict) else {}}
        except Exception as e:
            raise CloudBackendError(f"Launcher profile decrypt failed: {e}", "profile_corrupt") from e

    def put_achievement_profile(self, profile: dict, revision=None) -> dict:
        """Encrypt and conditionally replace the account-wide achievement ledger."""
        plaintext = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
        encoded = base64.b64encode(save_crypto.encrypt_save(plaintext, self.data_key_b64())).decode("ascii")
        body = {"data": encoded}
        if revision is not None:
            body["revision"] = revision
        return self._check(
            self._request("PUT", "/api/profile/achievements", json_body=body, timeout=10),
            "Achievement profile upload",
        )

    def put_profile(self, profile: dict, revision=None) -> dict:
        """Encrypt and conditionally replace the generalized launcher profile."""
        plaintext = json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
        encoded = base64.b64encode(save_crypto.encrypt_save(plaintext, self.data_key_b64())).decode("ascii")
        body = {"data": encoded}
        if revision is not None:
            body["revision"] = revision
        return self._check(
            self._request("PUT", "/api/profile", json_body=body, timeout=10),
            "Launcher profile upload",
        )

    # ------------------------------------------------------------------ #
    # Upload                                                             #
    # ------------------------------------------------------------------ #

    def upload_plaintext_zip(self, name_key: str, display_name: str,
                             plaintext_zip_path: str,
                             source_max_mtime: float,
                             cancel_check=None,
                             progress_callback=None,
                             existing_listing: dict | None = None) -> dict:
        """Encrypt + upload a zipped save archive; returns confirm result."""
        if cancel_check and cancel_check():
            raise SaveOperationCancelled()
        plain_sha = hashlib.sha256()
        plain_size = 0
        try:
            with open(plaintext_zip_path, "rb") as source:
                while True:
                    if cancel_check and cancel_check():
                        raise SaveOperationCancelled()
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    plain_size += len(chunk)
                    if plain_size > MAX_SAVE_BYTES:
                        raise CloudBackendError(
                            f"Save archive ({plain_size / (1024*1024):.1f} MB) exceeds the maximum allowed size ({MAX_SAVE_BYTES / (1024*1024):.0f} MB).",
                            "payload_too_large", 413,
                        )
                    plain_sha.update(chunk)
        except SaveOperationCancelled:
            raise
        except CloudBackendError:
            raise
        except OSError as e:
            raise CloudBackendError(f"Could not read staged save: {e}") from e
        plain_sha = plain_sha.hexdigest()
        device_id, device_name, device_platform = get_device_identity()

        # Name-key resolution normally fetched this listing already. Reusing
        # it avoids a second serialized cloud request on every upload; direct
        # backend callers still get the safe fallback request.
        listing = existing_listing if existing_listing is not None else self.list_games()
        existing = next((g for g in listing.get("games", []) if g.get("nameKey") == name_key), None)
        if existing and existing.get("versions"):
            matched = next((v for v in existing["versions"] if v.get("plainSha256") == plain_sha), None)
            if matched:
                logger.info(
                    f"Cloud already holds identical save for '{name_key}' as existing v{matched.get('version')} "
                    f"(SHA: {plain_sha[:8]}...). Skipping duplicate upload."
                )
                return {"skipped": True, "version": matched.get("version"), "existingVersion": matched.get("version")}

        envelope_path = None
        try:
            envelope_fd, envelope_path = tempfile.mkstemp(
                prefix=".sl-save-", suffix=".enc", dir=os.path.dirname(os.path.abspath(plaintext_zip_path))
            )
            os.close(envelope_fd)
            try:
                _digest, encrypted_plain_size, declared = save_crypto.encrypt_save_file(
                    plaintext_zip_path,
                    envelope_path,
                    self.data_key_b64(),
                    max_plaintext_bytes=MAX_SAVE_BYTES,
                    cancel_check=cancel_check,
                )
            except SaveOperationCancelled:
                raise
            except save_crypto.SaveCryptoError as exc:
                raise CloudBackendError(str(exc), "encryption_failed") from exc
            if encrypted_plain_size != plain_size or _digest != plain_sha:
                raise CloudBackendError("Staged save changed while it was being encrypted.", "local_save_changed")
            init = self._check(
                self._request(
                    "POST",
                    f"/api/games/{requests.utils.quote(name_key)}/init-upload",
                    json_body={
                        "displayName": display_name,
                        "plainSha256": plain_sha,
                        "sourceMaxMtime": int(source_max_mtime),
                        "declaredSizeBytes": declared,
                        "createdDeviceId": device_id,
                        "createdDeviceName": device_name,
                        "createdDevicePlatform": device_platform,
                        "uploadedDeviceId": device_id,
                        "uploadedDeviceName": device_name,
                        "uploadedDevicePlatform": device_platform,
                    },
                ),
                "Upload init",
            )

            transfer_session = requests.Session()
            upload_body = _ProgressUploadFile(
                envelope_path,
                declared,
                cancel_check=cancel_check,
                progress_callback=progress_callback,
            )
            try:
                post = transfer_session.post(
                    init["uploadUrl"],
                    data=upload_body,
                    headers={
                        "Content-Type": "application/octet-stream",
                    },
                    timeout=(10, 120),
                )
            finally:
                upload_body.close()
                transfer_session.close()
            if post.status_code != 200:
                detail = ""
                try:
                    payload = post.json()
                    if isinstance(payload, dict):
                        detail = str(payload.get("error") or payload.get("message") or "").strip()
                except (ValueError, AttributeError):
                    detail = (post.text or "").strip()
                if detail:
                    detail = " ".join(detail.split())[:512]
                try:
                    post.close()
                except Exception:
                    pass
                raise CloudBackendError(
                    f"Save upload rejected ({post.status_code}){': ' + detail if detail else '.'}", "upload_failed",
                    post.status_code,
                )
            try:
                storage_id = post.json().get("storageId")
            except (ValueError, AttributeError):
                storage_id = None
            finally:
                try:
                    post.close()
                except Exception:
                    pass
            if not storage_id:
                raise CloudBackendError("Upload succeeded but no valid id was returned.",
                                        "upload_failed", 502)

            return self._check(
                self._request(
                    "POST",
                    f"/api/games/{requests.utils.quote(name_key)}/confirm-upload",
                    json_body={"saveId": init["saveId"], "storageId": storage_id},
                ),
                "Upload confirm",
            )
        finally:
            if envelope_path:
                try:
                    os.unlink(envelope_path)
                except OSError:
                    pass

    # ------------------------------------------------------------------ #
    # Download                                                           #
    # ------------------------------------------------------------------ #

    def download_to_temp(self, name_key: str,
                         version: Optional[int] = None,
                         cancel_check=None,
                         progress_callback=None) -> tuple[str, dict]:
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
        fd_closed = False

        try:
            transfer_session = requests.Session()
            try:
                stream_req = transfer_session.get(
                    ref["url"], stream=True, timeout=_DOWNLOAD_STREAM_TIMEOUT
                )
                with stream_req as resp:
                    if resp.status_code != 200:
                        raise CloudBackendError("Blob fetch failed.", "download_failed",
                                                resp.status_code)
                    total = 0
                    limit = MAX_SAVE_BYTES * 2
                    expected = int(resp.headers.get("Content-Length", 0) or 0)
                    with os.fdopen(fd, "wb") as out:
                        fd_closed = True
                        for chunk in resp.iter_content(chunk_size=65536):
                            if cancel_check and cancel_check():
                                raise SaveOperationCancelled()
                            total += len(chunk)
                            if total > limit:
                                raise CloudBackendError("Blob exceeds expected size cap.")
                            out.write(chunk)
                            if progress_callback is not None and expected > 0:
                                progress_callback(min(1.0, total / expected))
            finally:
                transfer_session.close()
        except Exception:
            if not fd_closed:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(enc_path)
            except OSError:
                pass
            raise

        plain_path = None
        try:
            fd2, plain_path = tempfile.mkstemp(prefix=".sl-save-", suffix=".zip", dir=dest_dir)
            os.close(fd2)
            save_crypto.decrypt_save_file(
                enc_path,
                plain_path,
                self.data_key_b64(),
                max_plaintext_bytes=MAX_SAVE_BYTES,
                cancel_check=cancel_check,
            )
            return plain_path, {
                "version": ref.get("version"),
                "sizeBytes": ref.get("sizeBytes"),
                "fileCount": ref.get("fileCount"),
                "createdAt": ref.get("createdAt", 0),
                "uploadedDeviceName": ref.get("uploadedDeviceName", ""),
            }
        finally:
            try:
                os.unlink(enc_path)
            except OSError:
                pass
            if plain_path and not os.path.isfile(plain_path):
                try:
                    os.unlink(plain_path)
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
        """Restore a downloaded archive through the shared safety boundary."""
        from core.ludusavi_detector import SaveLocation
        from core.save_restore_service import (
            restore_archive_with_safety_backup,
            safety_backup_path,
        )

        current = (
            [SaveLocation("Current local save", destination, os.path.isdir(destination))]
            if os.path.lexists(destination) else []
        )
        result = restore_archive_with_safety_backup(
            cloud_zip_path,
            destination,
            game_name="Cloud save",
            game_path=game_path,
            current_locations=current,
            backup_zip_path=safety_backup_path(
                os.path.dirname(os.path.abspath(destination)), "cloud-save"
            ),
            operation="Restore cloud save",
        )
        return bool(result.success)


def check_backend_health(
    url: Optional[str] = None,
    secret_key: Optional[str] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Probe /api/health on Convex backend, measuring roundtrip latency and verifying version parity."""
    if url is None:
        endpoint = get_site_url().rstrip("/")
    else:
        endpoint = normalize_site_url(url)

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
        key = get_secret("cloud_secret_key", os.environ.get("SAFELAUNCHER_SECRET_KEY", ""), legacy_name="cloud_secret_key")

    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["X-SafeLauncher-Key"] = key

    t0 = time.monotonic()
    resp = None
    version_response = None
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
                    version_response = vresp
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
    finally:
        for response in (resp, version_response):
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass


__all__ = [
    "CloudBackendError", "describe_cloud_error", "ConvexSaveBackend", "get_site_url",
    "normalize_name_key", "MAX_SAVE_BYTES", "QUOTA_BYTES", "BASE_FREE_QUOTA_BYTES",
    "check_backend_health",
]
