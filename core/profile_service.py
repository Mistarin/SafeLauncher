"""HTTP client for the centralized public-profile service.

Public profile reads are anonymous. Owner and social operations use the
central Auth0 access token through :class:`CentralAuthSession`; the legacy
owner-token argument is retained only for the one-time migration path and
for old test/embedded callers.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote, urlsplit

import requests
from PyQt6.QtCore import QSettings

from core.central_auth import CentralAuthError, CentralAuthSession
from core.profile_models import (
    HANDLE_RE,
    normalize_avatar_asset_id,
    normalize_avatar_id,
    normalize_public_document,
    normalize_social_snapshot,
    normalize_username_handle,
)
from core.profile_avatar_catalog import normalize_avatar_catalog


OFFICIAL_PROFILE_GATEWAY_URL = "https://profilegateway.vercel.app"


def is_local_service_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and not parsed.username
        and not parsed.password
    )


class ProfileServiceError(RuntimeError):
    def __init__(self, message: str, code: str = "unknown", status: int = 0, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.status_code = status
        self.extra = extra or {}


def get_profile_service_url() -> str:
    """Return the official gateway, with explicit build/local overrides.

    Older releases stored the raw Convex HTTP-action URL in QSettings. That
    origin is intentionally no longer accepted by the production client: all
    production traffic must pass through the gateway so the Convex deployment
    remains unreachable to ordinary clients.
    """
    settings = QSettings("SafeLauncher", "SafeLauncher")
    environment_override = str(os.environ.get("SAFELAUNCHER_PROFILE_SERVICE_URL", "") or "").strip()
    if environment_override:
        return environment_override.rstrip("/")
    local_override = str(settings.value("profile_service_url", "", type=str) or "").strip()
    if is_local_service_url(local_override):
        return local_override.rstrip("/")
    return OFFICIAL_PROFILE_GATEWAY_URL


class ProfileServiceClient:
    def __init__(
        self,
        site_url: str | None = None,
        owner_token: str = "",
        auth_session: CentralAuthSession | None = None,
    ):
        self.site_url = (site_url if site_url is not None else get_profile_service_url()).strip().rstrip("/")
        self.legacy_owner_token = str(owner_token or "").strip()
        self.auth_session = auth_session
        self.session = requests.Session()

    def close(self) -> None:
        """Close the per-operation HTTP pool.

        Profile clients are intentionally short-lived in worker tasks. Relying
        on ``requests.Session`` garbage collection leaves sockets around until
        a later GC cycle, which is especially visible after repeated profile
        refreshes.
        """
        try:
            self.session.close()
        except Exception:
            pass

    def __enter__(self) -> "ProfileServiceClient":
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()

    def __del__(self):
        self.close()

    @property
    def configured(self) -> bool:
        try:
            parsed = urlsplit(self.site_url)
        except ValueError:
            return False
        if parsed.username or parsed.password or not parsed.hostname:
            return False
        return parsed.scheme == "https" or is_local_service_url(self.site_url)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        owner: bool = False,
        legacy_owner: bool = False,
        retry_auth: bool = True,
        _auth_header: str = "",
    ) -> dict:
        if not self.configured:
            raise ProfileServiceError("Public profile service is not configured.", "unconfigured")
        headers = {"Accept": "application/json"}
        if owner:
            if _auth_header:
                headers["Authorization"] = _auth_header
            elif self.auth_session is not None:
                try:
                    headers["Authorization"] = self.auth_session.authorization_header()
                except CentralAuthError as exc:
                    raise ProfileServiceError(str(exc), exc.code, 401) from exc
            elif legacy_owner:
                if not self.legacy_owner_token:
                    raise ProfileServiceError("Profile migration token is missing.", "owner_token_missing", 401)
                headers["Authorization"] = f"Bearer {self.legacy_owner_token}"
            else:
                raise ProfileServiceError("Central profile authentication is required.", "not_signed_in", 401)
        try:
            response = self.session.request(
                method,
                f"{self.site_url}{path}",
                json=json_body,
                headers=headers,
                timeout=(5, 12),
            )
        except requests.RequestException as exc:
            raise ProfileServiceError(f"Profile service is unreachable: {exc}", "unreachable") from exc

        try:
            if response.status_code == 401 and owner and retry_auth and self.auth_session is not None:
                try:
                    refreshed_header = self.auth_session.authorization_header(force_refresh=True)
                except CentralAuthError as exc:
                    raise ProfileServiceError(str(exc), exc.code, 401) from exc
                return self._request(
                    method,
                    path,
                    json_body=json_body,
                    owner=owner,
                    legacy_owner=legacy_owner,
                    retry_auth=False,
                    _auth_header=refreshed_header,
                )

            try:
                decoded = response.json()
            except ValueError:
                decoded = {}
            payload = decoded if isinstance(decoded, dict) else {}
            if response.status_code >= 400:
                message = str(payload.get("error") or f"Profile service returned HTTP {response.status_code}")
                raise ProfileServiceError(
                    message,
                    str(payload.get("code") or "http_error"),
                    response.status_code,
                    payload,
                )
            return payload
        finally:
            try:
                response.close()
            except Exception:
                pass

    # --- public reads -----------------------------------------------------
    def check_handle_availability(self, handle: str) -> bool:
        """Check whether an unpublished profile handle is currently free."""
        raw_handle = str(handle or "").strip().lstrip("@").lower()
        requested_handle = (
            raw_handle
            if HANDLE_RE.fullmatch(raw_handle)
            else normalize_username_handle(raw_handle)
        )
        if not requested_handle or not HANDLE_RE.fullmatch(requested_handle):
            raise ProfileServiceError("The profile handle is invalid.", "invalid_handle", 400)
        payload = self._request(
            "GET",
            f"/api/profile/v1/handles/{quote(requested_handle, safe='')}/availability",
        )
        if payload.get("handle") != requested_handle or not isinstance(payload.get("available"), bool):
            raise ProfileServiceError("The profile service returned an invalid handle availability response.", "invalid_response")
        return bool(payload["available"])

    def fetch(self, handle: str) -> dict:
        raw_handle = str(handle or "").strip().lstrip("@").lower()
        requested_handle = (
            raw_handle
            if HANDLE_RE.fullmatch(raw_handle)
            else normalize_username_handle(raw_handle)
        )
        if not requested_handle or not HANDLE_RE.fullmatch(requested_handle):
            raise ProfileServiceError("The profile handle is invalid.", "invalid_handle", 400)
        payload = self._request("GET", f"/api/profile/v1/{quote(requested_handle, safe='')}")
        document = normalize_public_document(payload.get("profile"))
        if document is None or document.get("handle") != requested_handle:
            raise ProfileServiceError("The public profile is invalid or corrupt.", "invalid_profile")
        document["revision"] = max(0, int(payload.get("revision", 0) or 0))
        return document

    def list_avatar_catalog(self) -> list[dict[str, Any]]:
        """Fetch the public, developer-managed avatar catalog."""
        payload = self._request("GET", "/api/profile/v2/avatars")
        catalog = normalize_avatar_catalog(payload.get("avatars"))
        if catalog is None:
            raise ProfileServiceError(
                "The profile service returned an invalid avatar catalog.",
                "invalid_avatar_catalog",
            )
        return catalog

    def avatar_url(self, avatar_id: str) -> str:
        asset_id = normalize_avatar_asset_id(avatar_id)
        normalized = str(asset_id) if asset_id is not None else normalize_avatar_id(avatar_id)
        if not normalized:
            raise ProfileServiceError("The avatar identifier is invalid.", "invalid_avatar", 400)
        return f"{self.site_url}/api/profile/v2/avatars/{quote(normalized, safe='')}"

    def fetch_avatar_bytes(self, avatar_id: str) -> bytes:
        """Fetch one bounded image through the profile gateway."""
        url = self.avatar_url(avatar_id)
        try:
            response = self.session.get(
                url,
                headers={"Accept": "image/png,image/*;q=0.8"},
                timeout=(5, 12),
                stream=True,
            )
        except requests.RequestException as exc:
            raise ProfileServiceError(f"Profile service is unreachable: {exc}", "unreachable") from exc
        try:
            if response.status_code != 200:
                raise ProfileServiceError(
                    f"Profile service returned HTTP {response.status_code}",
                    "avatar_fetch_failed",
                    response.status_code,
                )
            if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                raise ProfileServiceError("The profile service returned a non-image avatar.", "invalid_avatar_response")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > 512 * 1024:
                raise ProfileServiceError("The avatar response is too large.", "avatar_too_large")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=32 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > 512 * 1024:
                    raise ProfileServiceError("The avatar response is too large.", "avatar_too_large")
                chunks.append(chunk)
            return b"".join(chunks)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProfileServiceError("The avatar response is invalid.", "invalid_avatar_response") from exc
        finally:
            response.close()

    def fetch_avatar_batch(
        self,
        avatar_ids: Any,
        *,
        max_items: int = 128,
        max_total_bytes: int = 12 * 1024 * 1024,
    ) -> dict[str, bytes]:
        """Fetch a bounded avatar set through one reusable HTTP session.

        Individual catalog entries are allowed to disappear without making
        the rest of the selector unusable. The caller can therefore render
        whatever was cached or downloaded while preserving the catalog order.
        """
        requested: list[str] = []
        seen: set[str] = set()
        for value in list(avatar_ids or [])[:max(0, int(max_items))]:
            normalized = normalize_avatar_id(value)
            if normalized and normalized not in seen:
                requested.append(normalized)
                seen.add(normalized)
        downloaded: dict[str, bytes] = {}
        total = 0
        for avatar_id in requested:
            try:
                data = self.fetch_avatar_bytes(avatar_id)
            except ProfileServiceError:
                continue
            if not data or total + len(data) > max_total_bytes:
                continue
            downloaded[avatar_id] = data
            total += len(data)
        return downloaded

    # --- Auth0-owned profile API -----------------------------------------
    def current_profile(self) -> dict[str, Any] | None:
        payload = self._request("GET", "/api/profile/v2/me", owner=True)
        raw = payload.get("profile")
        if raw is None:
            return None
        document = normalize_public_document(raw)
        if document is None:
            raise ProfileServiceError("The authenticated profile is invalid or corrupt.", "invalid_profile")
        document["revision"] = max(0, int(payload.get("revision", 0) or 0))
        return document

    def create_profile(self, profile: dict[str, Any]) -> dict:
        return self._request("POST", "/api/profile/v2/me", json_body={"profile": profile}, owner=True)

    def update_profile(self, profile: dict[str, Any], revision: int) -> dict:
        return self._request(
            "PUT",
            "/api/profile/v2/me",
            json_body={"profile": profile, "revision": int(revision)},
            owner=True,
        )

    def delete_profile(self) -> dict:
        return self._request("DELETE", "/api/profile/v2/me", owner=True)

    def claim_legacy_profile(self, handle: str, owner_token: str) -> dict:
        token = str(owner_token or "").strip()
        if len(token) < 32 or len(token) > 256:
            raise ProfileServiceError("The legacy profile token is invalid.", "invalid_token", 400)
        return self._request(
            "POST",
            "/api/profile/v2/me/claim",
            json_body={"handle": str(handle or "").strip().lower(), "ownerToken": token},
            owner=True,
        )

    # --- Legacy compatibility -------------------------------------------
    def _ensure_legacy_mode(self) -> None:
        if self.auth_session is not None:
            raise ProfileServiceError(
                "Legacy owner-token operations cannot use a central Auth0 session.",
                "legacy_auth_boundary",
            )

    def create(self, handle: str, profile: dict[str, Any]) -> dict:
        """Legacy owner-token create; new code must use create_profile."""
        self._ensure_legacy_mode()
        return self._request(
            "POST",
            "/api/profile/v1",
            json_body={"handle": handle, "profile": profile, "ownerToken": self.legacy_owner_token},
            owner=True,
            legacy_owner=True,
        )

    def update(self, handle: str, profile: dict[str, Any], revision: int) -> dict:
        self._ensure_legacy_mode()
        return self._request(
            "PUT",
            f"/api/profile/v1/{quote(str(handle).strip().lower(), safe='')}",
            json_body={"profile": profile, "revision": int(revision)},
            owner=True,
            legacy_owner=True,
        )

    def delete(self, handle: str) -> dict:
        self._ensure_legacy_mode()
        return self._request(
            "DELETE",
            f"/api/profile/v1/{quote(str(handle).strip().lower(), safe='')}",
            owner=True,
            legacy_owner=True,
        )

    def rotate_token(self, handle: str, new_token: str) -> dict:
        self._ensure_legacy_mode()
        new_token = str(new_token or "").strip()
        if len(new_token) < 32 or len(new_token) > 256:
            raise ProfileServiceError("The new profile owner token is invalid.", "invalid_token")
        return self._request(
            "POST",
            f"/api/profile/v1/{quote(str(handle).strip().lower(), safe='')}/rotate-token",
            json_body={"ownerToken": new_token},
            owner=True,
            legacy_owner=True,
        )

    # --- Social API ------------------------------------------------------
    def get_social(self, handle: str | None = None) -> dict[str, Any]:
        path = "/api/profile/v2/me/friends"
        if self.auth_session is None:
            legacy_handle = str(handle or "").strip().lower()
            path = f"/api/profile/v1/{quote(legacy_handle, safe='')}/friends"
        payload = self._request("GET", path, owner=True, legacy_owner=self.auth_session is None)
        snapshot = normalize_social_snapshot(payload)
        if snapshot is None:
            raise ProfileServiceError("The social profile response is invalid or corrupt.", "invalid_social")
        return snapshot

    def send_friend_request(self, handle: str | None, target_handle: str) -> dict:
        if self.auth_session is not None:
            path = "/api/profile/v2/me/friend-requests"
        else:
            path = f"/api/profile/v1/{quote(str(handle).strip().lower(), safe='')}/friend-requests"
        return self._request(
            "POST", path,
            json_body={"targetHandle": str(target_handle).strip().lower()},
            owner=True,
            legacy_owner=self.auth_session is None,
        )

    def respond_friend_request(self, handle: str | None, request_id: str, action: str) -> dict:
        action = str(action or "").strip().lower()
        if action not in {"accept", "decline", "cancel"}:
            raise ProfileServiceError("The friend request action is invalid.", "invalid_action")
        if self.auth_session is not None:
            path = f"/api/profile/v2/me/friend-requests/{quote(str(request_id).strip(), safe='')}/{action}"
        else:
            path = f"/api/profile/v1/{quote(str(handle).strip().lower(), safe='')}/friend-requests/{quote(str(request_id).strip(), safe='')}/{action}"
        return self._request("POST", path, owner=True, legacy_owner=self.auth_session is None)

    def remove_friend(self, handle: str | None, friend_handle: str) -> dict:
        if self.auth_session is not None:
            path = f"/api/profile/v2/me/friends/{quote(str(friend_handle).strip().lower(), safe='')}"
        else:
            path = f"/api/profile/v1/{quote(str(handle).strip().lower(), safe='')}/friends/{quote(str(friend_handle).strip().lower(), safe='')}"
        return self._request("DELETE", path, owner=True, legacy_owner=self.auth_session is None)

    def block_user(self, handle: str | None, target_handle: str) -> dict:
        if self.auth_session is not None:
            path = "/api/profile/v2/me/blocks"
        else:
            path = f"/api/profile/v1/{quote(str(handle).strip().lower(), safe='')}/blocks"
        return self._request(
            "POST", path,
            json_body={"targetHandle": str(target_handle).strip().lower()},
            owner=True,
            legacy_owner=self.auth_session is None,
        )

    def unblock_user(self, handle: str | None, target_handle: str) -> dict:
        if self.auth_session is not None:
            path = f"/api/profile/v2/me/blocks/{quote(str(target_handle).strip().lower(), safe='')}"
        else:
            path = f"/api/profile/v1/{quote(str(handle).strip().lower(), safe='')}/blocks/{quote(str(target_handle).strip().lower(), safe='')}"
        return self._request("DELETE", path, owner=True, legacy_owner=self.auth_session is None)
