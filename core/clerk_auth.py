"""
Clerk authentication for the SafeLauncher desktop app (no webview).

Flow: OAuth2 Authorization Code + PKCE (S256) against a Clerk OAuth
Application, opening the SYSTEM browser and completing via a one-shot loopback
callback server on 127.0.0.1:<random port> (RFC 8252 §7.3 semantics as used by
Clerk's own CLI).

Token persistence: ~/.local/share/safelauncher/auth.json with mode 0600 inside
a 0700 directory. Refresh tokens are never logged or included in diagnostics.

Configuration (not secrets): SAFELAUNCHER_CLERK_DOMAIN / _CLIENT_ID env vars,
overridable via QSettings keys clerk_domain / clerk_client_id.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import threading
import time
import urllib.parse
from typing import Optional

import requests
from PyQt6.QtCore import QSettings

from core.logger import get_logger
from database import _APP_DATA_DIR

logger = get_logger("ClerkAuth")

AUTH_FILE = os.path.join(_APP_DATA_DIR, "auth.json")

# Publishable (non-secret) defaults for Martin's instance; overridable via
# QSettings keys clerk_domain / clerk_client_id or SAFELAUNCHER_* env vars.
DEFAULT_CLERK_DOMAIN = "https://upright-stallion-9201.clerk.accounts.dev"
DEFAULT_CLERK_CLIENT_ID = "tthAomibiA7PVISf"

_OAUTH_SCOPES = "profile email offline_access"
_REFRESH_MARGIN_SECONDS = 60
_HTTP_TIMEOUT = 15


class AuthError(Exception):
    """User-facing authentication failure."""


def _clerk_config() -> tuple[str, str]:
    settings = QSettings("SafeLauncher", "SafeLauncher")
    domain = str(
        os.environ.get("SAFELAUNCHER_CLERK_DOMAIN", "")
        or settings.value("clerk_domain", "", type=str)
        or DEFAULT_CLERK_DOMAIN
    ).strip().rstrip("/")
    client_id = str(
        os.environ.get("SAFELAUNCHER_CLERK_CLIENT_ID", "")
        or settings.value("clerk_client_id", "", type=str)
        or DEFAULT_CLERK_CLIENT_ID
    ).strip()
    if not domain or not client_id:
        raise AuthError(
            "Cloud sign-in is not configured yet: missing Clerk domain/client id "
            "(Settings → Cloud)."
        )
    return domain, client_id


# --------------------------------------------------------------------------- #
# Token store                                                                 #
# --------------------------------------------------------------------------- #

def _load_tokens() -> Optional[dict]:
    try:
        if not os.path.isfile(AUTH_FILE):
            return None
        with open(AUTH_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data.get("access_token"):
            return None
        return data
    except Exception as e:
        logger.warning(f"Could not read auth store: {e}")
        return None


def _save_tokens(tokens: dict) -> None:
    os.makedirs(_APP_DATA_DIR, mode=0o700, exist_ok=True)
    tmp = AUTH_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(tokens, f)
        os.replace(tmp, AUTH_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    logger.info("Saved refreshed cloud auth tokens.")


def clear_stored_session() -> None:
    """Forget local tokens entirely (logout)."""
    for path in (AUTH_FILE, AUTH_FILE + ".tmp"):
        try:
            if os.path.isfile(path):
                os.unlink(path)
        except OSError:
            pass


def get_status() -> dict:
    """Cheap, non-network account status for UI display."""
    tokens = _load_tokens()
    if not tokens:
        return {"signed_in": False}
    return {
        "signed_in": True,
        "email": tokens.get("email"),
        "expires_at": tokens.get("expires_at"),
    }


# --------------------------------------------------------------------------- #
# PKCE helpers                                                                #
# --------------------------------------------------------------------------- #

def make_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


# --------------------------------------------------------------------------- #
# Interactive login                                                           #
# --------------------------------------------------------------------------- #

class _CallbackServer(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlparse(self.path)
        self.server.result.update(  # type: ignore[attr-defined]
            params=dict(urllib.parse.parse_qsl(parsed.query)),
            path=parsed.path,
        )
        ok = b"<html><body><h3>SafeLauncher connected.</h3>You can close this tab.</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(ok)))
        self.end_headers()
        self.wfile.write(ok)

    def log_message(self, *args):  # silence stderr noise
        return


def login() -> dict:
    """Run the full interactive login; returns the stored token dict.

    Raises AuthError on configuration errors, user denial, timeout, or any
    token-exchange failure. Network calls carry timeouts throughout.
    """
    domain, client_id = _clerk_config()

    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(24)

    # One-shot loopback listener; the OS picks a free port.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    listener.listen(1)

    authorize_url = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": _OAUTH_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    full_url = f"{domain}/oauth/authorize?{authorize_url}"

    import webbrowser
    webbrowser.open(full_url)
    logger.info(f"Opened system browser for sign-in (loopback port {port}).")

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackServer)
    server.timeout = 300  # five minutes to finish sign-in
    try:
        # Blocks until the browser hits the callback or the timeout expires.
        server.handle_request()
    finally:
        server.server_close()

    got = getattr(server, "result", {})
    params = got.get("params", {})
    if params.get("error"):
        raise AuthError(f"Sign-in was cancelled or failed: {params.get('error')}")
    code = params.get("code")
    if not code:
        raise AuthError("Sign-in did not complete in time.")
    if not secrets.compare_digest(str(params.get("state", "")), state):
        raise AuthError("Sign-in callback failed state validation; please retry.")

    resp = requests.post(
        f"{domain}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=_HTTP_TIMEOUT,
    )
    if resp.status_code != 200:
        raise AuthError("Token exchange failed. Please retry sign-in.")
    payload = resp.json()
    if not payload.get("access_token"):
        raise AuthError("Sign-in response was incomplete.")
    return _persist(payload)


def _persist(payload: dict) -> dict:
    expires_in = int(payload.get("expires_in", 3600))
    tokens = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token"),
        "expires_at": time.time() + expires_in,
        "email": payload.get("email"),
    }
    _save_tokens(tokens)
    return tokens


# --------------------------------------------------------------------------- #
# Session access                                                              #
# --------------------------------------------------------------------------- #

_refresh_lock = threading.Lock()


def refresh_now(force: bool = False) -> None:
    """Refresh synchronously; throws AuthError when unrecoverable."""
    domain, client_id = _clerk_config()
    tokens = _load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        raise AuthError("Not signed in.")
    with _refresh_lock:
        tokens = _load_tokens() or {}
        rt = tokens.get("refresh_token")
        if not rt:
            raise AuthError("Not signed in.")
        # Single flight: another thread may have just refreshed.
        if not force and tokens.get("expires_at", 0) > time.time() + _REFRESH_MARGIN_SECONDS:
            return
        resp = requests.post(
            f"{domain}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "client_id": client_id,
            },
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            clear_stored_session()
            raise AuthError("Session expired; please sign in again.")
        _persist(resp.json())


def get_access_token() -> str:
    """Return a valid access token, refreshing proactively when stale."""
    tokens = _load_tokens()
    if not tokens:
        raise AuthError("Not signed in.")
    if tokens.get("expires_at", 0) <= time.time() + _REFRESH_MARGIN_SECONDS:
        refresh_now()
        tokens = _load_tokens() or {}
    return str(tokens["access_token"])
