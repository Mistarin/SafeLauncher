"""Auth0 Device Authorization session for the centralized profile service.

The central profile service is a different trust domain from the user's
private SafeLauncherCloud deployment.  This module deliberately owns only the
OIDC session used for public-profile ownership.  Access tokens are kept in
memory; the long-lived rotating refresh token is stored through the existing
OS credential-store abstraction.
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Callable, Any
from urllib.parse import urljoin

import requests
from PyQt6.QtCore import QSettings

from core.secret_store import delete_secret, get_secret, set_secret


OFFICIAL_AUTH0_ISSUER = "https://dev-712dm7e8q0c2tie3.us.auth0.com"
OFFICIAL_AUTH0_CLIENT_ID = "wja9mk07Ykw3xvey2V47QGO0olzwRdl6"
OFFICIAL_AUTH0_AUDIENCE = "https://profiles.safelauncher.app"


class CentralAuthError(RuntimeError):
    """A recoverable central OIDC configuration or authentication error."""

    def __init__(self, message: str, code: str = "auth_error"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CentralAuthConfig:
    issuer: str
    client_id: str
    audience: str

    @property
    def configured(self) -> bool:
        return bool(self.issuer and self.client_id and self.audience)

    @property
    def issuer_base(self) -> str:
        return self.issuer.rstrip("/") + "/"

    @property
    def device_code_url(self) -> str:
        return urljoin(self.issuer_base, "oauth/device/code")

    @property
    def token_url(self) -> str:
        return urljoin(self.issuer_base, "oauth/token")

    @property
    def userinfo_url(self) -> str:
        return urljoin(self.issuer_base, "userinfo")


def _settings() -> QSettings:
    return QSettings("SafeLauncher", "SafeLauncher")


def get_central_auth_config() -> CentralAuthConfig:
    """Read non-secret Auth0 settings from environment/build config.

    These values identify a public Auth0 native application and API.  They are
    not credentials.  The private client secret is intentionally unsupported.
    """
    settings = _settings()
    issuer = str(
        os.environ.get("SAFELAUNCHER_AUTH0_ISSUER", "")
        or settings.value("central_auth_issuer", "", type=str)
        or OFFICIAL_AUTH0_ISSUER
    ).strip()
    client_id = str(
        os.environ.get("SAFELAUNCHER_AUTH0_CLIENT_ID", "")
        or settings.value("central_auth_client_id", "", type=str)
        or OFFICIAL_AUTH0_CLIENT_ID
    ).strip()
    audience = str(
        os.environ.get("SAFELAUNCHER_AUTH0_AUDIENCE", "")
        or settings.value("central_auth_audience", "", type=str)
        or OFFICIAL_AUTH0_AUDIENCE
    ).strip()
    if issuer and not issuer.startswith("https://"):
        issuer = ""
    return CentralAuthConfig(issuer=issuer.rstrip("/"), client_id=client_id, audience=audience)


_REFRESH_TOKEN_SECRET = "central_profile_refresh_token"
_ACCESS_TOKEN_SKEW = 60


class CentralAuthSession:
    """Thread-safe Auth0 session with device login and refresh rotation."""

    def __init__(self, config: CentralAuthConfig | None = None, session: requests.Session | None = None):
        self.config = config or get_central_auth_config()
        self.http = session or requests.Session()
        self._lock = threading.RLock()
        self._access_token = ""
        self._access_expires_at = 0.0
        self._refresh_available: bool | None = None
        self._last_token_refresh_at = 0.0

    def close(self) -> None:
        """Release the Auth0 HTTP connection pool at application shutdown."""
        try:
            self.http.close()
        except Exception:
            pass

    def __del__(self):
        self.close()

    @property
    def configured(self) -> bool:
        return self.config.configured

    @property
    def signed_in(self) -> bool:
        with self._lock:
            if self._access_token:
                return True
            if self._refresh_available is None:
                self._refresh_available = bool(get_secret(_REFRESH_TOKEN_SECRET))
            return self._refresh_available

    def clear(self) -> None:
        with self._lock:
            self._access_token = ""
            self._access_expires_at = 0.0
            self._refresh_available = False
            delete_secret(_REFRESH_TOKEN_SECRET)

    def _set_tokens(self, payload: dict[str, Any], *, mark_refresh: bool = False) -> str:
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise CentralAuthError("The identity provider returned no access token.", "token_missing")
        try:
            expires_in = max(60, int(payload.get("expires_in", 3600) or 3600))
        except (TypeError, ValueError, OverflowError):
            expires_in = 3600
        refresh_token = str(payload.get("refresh_token") or "").strip()
        if refresh_token and not set_secret(_REFRESH_TOKEN_SECRET, refresh_token):
            raise CentralAuthError("SafeLauncher could not securely store the central login.", "secure_store_failed")
        with self._lock:
            if refresh_token:
                self._refresh_available = True
            self._access_token = access_token
            self._access_expires_at = time.time() + expires_in
            # Device login and refresh both install an access token, but only
            # a refresh should activate the short reuse window below.  If the
            # first API request after device login gets a transient 401, it
            # must be allowed to consume the refresh token once instead of
            # retrying the same newly-issued token.
            if mark_refresh:
                self._last_token_refresh_at = time.monotonic()
        return access_token

    def _post_form(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        response = None
        try:
            response = self.http.post(url, data=data, headers={"Accept": "application/json"}, timeout=(5, 15))
        except requests.RequestException as exc:
            raise CentralAuthError(f"The identity provider is unreachable: {exc}", "unreachable") from exc
        try:
            try:
                value = response.json()
            except ValueError:
                value = {}
            payload = value if isinstance(value, dict) else {}
            if response.status_code >= 400:
                code = str(payload.get("error") or "identity_provider_error")
                description = str(payload.get("error_description") or "The identity provider rejected the request.")
                normalized_description = description.lower()
                if code == "unauthorized_client" and "resource server" in normalized_description:
                    raise CentralAuthError(
                        "Auth0 has not authorized this SafeLauncher application to use the central profile API. "
                        "In the Auth0 Dashboard, open Applications → APIs, select the API with identifier "
                        f"{self.config.audience}, and grant this Native Application user-delegated access. "
                        "Then retry sign-in.",
                        "api_not_authorized",
                    )
                if code == "unauthorized_client":
                    raise CentralAuthError(
                        "Auth0 rejected this application. Confirm it is a Native Application with the Device Code "
                        "and Refresh Token grant types enabled, then retry sign-in.",
                        "application_not_authorized",
                    )
                raise CentralAuthError(description, code)
            return payload
        finally:
            try:
                response.close()
            except Exception:
                pass

    def device_login(self, progress: Callable[[str], None] | None = None, *, open_browser: bool = True) -> str:
        """Run Auth0 Device Authorization Flow until the user approves it."""
        if not self.configured:
            raise CentralAuthError(
                "Central profile authentication is not configured for this build.",
                "not_configured",
            )
        payload = self._post_form(
            self.config.device_code_url,
            {
                "client_id": self.config.client_id,
                "audience": self.config.audience,
                "scope": "openid profile offline_access",
            },
        )
        device_code = str(payload.get("device_code") or "").strip()
        verification = str(payload.get("verification_uri_complete") or payload.get("verification_uri") or "").strip()
        user_code = str(payload.get("user_code") or "").strip()
        if not device_code or not verification or not user_code:
            raise CentralAuthError("The identity provider returned an incomplete device challenge.", "device_response_invalid")
        try:
            expires_in = max(60, int(payload.get("expires_in", 600) or 600))
            interval = max(5, int(payload.get("interval", 5) or 5))
        except (TypeError, ValueError, OverflowError):
            expires_in, interval = 600, 5
        if progress:
            progress(f"Open {verification} and approve device code {user_code}.")
        if open_browser:
            try:
                webbrowser.open(verification, new=2)
            except Exception:
                pass

        deadline = time.monotonic() + expires_in
        while time.monotonic() < deadline:
            time.sleep(interval)
            try:
                token_payload = self._post_form(
                    self.config.token_url,
                    {
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": self.config.client_id,
                    },
                )
            except CentralAuthError as exc:
                if exc.code == "authorization_pending":
                    continue
                if exc.code == "slow_down":
                    interval = min(interval + 5, 30)
                    continue
                if exc.code in {"access_denied", "expired_token", "authorization_expired"}:
                    raise
                raise
            return self._set_tokens(token_payload)
        raise CentralAuthError("The central sign-in request expired before it was approved.", "authorization_expired")

    def _refresh(self) -> str:
        refresh_token = get_secret(_REFRESH_TOKEN_SECRET)
        if not refresh_token:
            with self._lock:
                self._access_token = ""
                self._access_expires_at = 0.0
                self._refresh_available = False
            return ""
        payload = self._post_form(
            self.config.token_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.config.client_id,
                "audience": self.config.audience,
            },
        )
        return self._set_tokens(payload, mark_refresh=True)

    def access_token(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            if not force_refresh and self._access_token and self._access_expires_at - time.time() > _ACCESS_TOKEN_SKEW:
                return self._access_token
            # A 401 can cause several background requests to ask for a forced
            # refresh at once. With Auth0 refresh-token rotation only one of
            # those requests may consume the rotating refresh token. Reuse a
            # token refreshed moments ago instead of rotating it twice.
            if force_refresh and self._access_token and time.monotonic() - self._last_token_refresh_at < 5:
                return self._access_token
        with self._lock:
            # Serialize refreshes so concurrent profile workers never reuse a
            # rotated refresh token at the same time.
            if not force_refresh and self._access_token and self._access_expires_at - time.time() > _ACCESS_TOKEN_SKEW:
                return self._access_token
            try:
                return self._refresh()
            except CentralAuthError as exc:
                if exc.code in {"invalid_grant", "invalid_token", "unauthorized"}:
                    self.clear()
                raise

    def authorization_header(self, *, force_refresh: bool = False) -> str:
        token = self.access_token(force_refresh=force_refresh)
        if not token:
            raise CentralAuthError("Sign in to manage your public profile.", "not_signed_in")
        return f"Bearer {token}"

    def userinfo(self) -> dict[str, Any]:
        """Return the authenticated OIDC claims used for first-login setup.

        Only the short-lived access token is sent to Auth0.  The caller should
        persist a derived public handle, never the email or raw identity
        claims.  A single refresh retry handles an access token that expires
        between ``authorization_header`` and this request.
        """
        auth_header = self.authorization_header()
        for attempt in range(2):
            response = None
            try:
                response = self.http.get(
                    self.config.userinfo_url,
                    headers={"Accept": "application/json", "Authorization": auth_header},
                    timeout=(5, 15),
                )
            except requests.RequestException as exc:
                raise CentralAuthError(f"The identity provider is unreachable: {exc}", "unreachable") from exc
            try:
                try:
                    value = response.json()
                except ValueError:
                    value = {}
                if response.status_code == 401 and attempt == 0:
                    auth_header = self.authorization_header(force_refresh=True)
                    continue
                if response.status_code >= 400 or not isinstance(value, dict):
                    raise CentralAuthError(
                        str(value.get("message") or "The identity provider rejected the identity request.")
                        if isinstance(value, dict) else "The identity provider returned an invalid identity response.",
                        "userinfo_failed",
                    )
                return value
            finally:
                try:
                    response.close()
                except Exception:
                    pass
        raise CentralAuthError("The identity provider rejected the identity request.", "userinfo_failed")
