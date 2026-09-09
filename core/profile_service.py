"""HTTP client for the separate public-profile Convex deployment."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import requests
from PyQt6.QtCore import QSettings

from core.profile_models import normalize_public_document


class ProfileServiceError(RuntimeError):
    def __init__(self, message: str, code: str = "unknown", status: int = 0, extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.status_code = status
        self.extra = extra or {}


def get_profile_service_url() -> str:
    settings = QSettings("SafeLauncher", "SafeLauncher")
    return str(
        os.environ.get("SAFELAUNCHER_PROFILE_SERVICE_URL", "")
        or settings.value("profile_service_url", "", type=str)
    ).strip().rstrip("/")


class ProfileServiceClient:
    def __init__(self, site_url: str | None = None, owner_token: str = ""):
        self.site_url = (site_url if site_url is not None else get_profile_service_url()).strip().rstrip("/")
        self.owner_token = str(owner_token or "").strip()
        self.session = requests.Session()

    @property
    def configured(self) -> bool:
        return self.site_url.startswith(("http://", "https://"))

    def _request(self, method: str, path: str, *, json_body: dict | None = None, owner: bool = False) -> dict:
        if not self.configured:
            raise ProfileServiceError("Public profile service is not configured.", "unconfigured")
        headers = {"Accept": "application/json"}
        if owner:
            if not self.owner_token:
                raise ProfileServiceError("Profile owner token is missing.", "owner_token_missing")
            headers["Authorization"] = f"Bearer {self.owner_token}"
        try:
            response = self.session.request(
                method, f"{self.site_url}{path}", json=json_body, headers=headers,
                timeout=(5, 12),
            )
        except requests.RequestException as exc:
            raise ProfileServiceError(f"Profile service is unreachable: {exc}", "unreachable") from exc
        try:
            decoded = response.json()
        except ValueError:
            decoded = {}
        payload = decoded if isinstance(decoded, dict) else {}
        if response.status_code >= 400:
            raise ProfileServiceError(
                str(payload.get("error") or f"Profile service returned HTTP {response.status_code}"),
                str(payload.get("code") or "http_error"), response.status_code,
                payload,
            )
        return payload

    def create(self, handle: str, profile: dict[str, Any]) -> dict:
        return self._request("POST", "/api/profile/v1", json_body={"handle": handle, "profile": profile, "ownerToken": self.owner_token})

    def fetch(self, handle: str) -> dict:
        requested_handle = str(handle or "").strip().lower()
        payload = self._request("GET", f"/api/profile/v1/{quote(requested_handle, safe='')}")
        document = normalize_public_document(payload.get("profile"))
        if document is None or document.get("handle") != requested_handle:
            raise ProfileServiceError("The public profile is invalid or corrupt.", "invalid_profile")
        document["revision"] = max(0, int(payload.get("revision", 0) or 0))
        return document

    def update(self, handle: str, profile: dict[str, Any], revision: int) -> dict:
        return self._request(
            "PUT", f"/api/profile/v1/{quote(handle, safe='')}",
            json_body={"profile": profile, "revision": int(revision)}, owner=True,
        )

    def delete(self, handle: str) -> dict:
        return self._request("DELETE", f"/api/profile/v1/{quote(handle, safe='')}", owner=True)

    def rotate_token(self, handle: str, new_token: str) -> dict:
        """Replace the owner token without ever returning it from the service."""
        new_token = str(new_token or "").strip()
        if len(new_token) < 32 or len(new_token) > 256:
            raise ProfileServiceError("The new profile owner token is invalid.", "invalid_token")
        return self._request(
            "POST", f"/api/profile/v1/{quote(handle, safe='')}/rotate-token",
            json_body={"ownerToken": new_token}, owner=True,
        )
