"""Lightweight, privacy-friendly telemetry client for SafeLauncher.

Sends an anonymous ping on application startup to record active installation
metrics and version adoption. No personal data or game save data is ever transmitted.
"""

import os
import uuid
import requests
from PyQt6.QtCore import QSettings

from core.logger import get_logger
from core.profile_service import OFFICIAL_PROFILE_GATEWAY_URL

logger = get_logger("Telemetry")

# The desktop client talks to the public gateway only. The Convex origin is a
# server-side Vercel setting and must never be shipped in the application.
_CENTRAL_TELEMETRY_URL = os.environ.get(
    "SAFELAUNCHER_TELEMETRY_URL",
    f"{OFFICIAL_PROFILE_GATEWAY_URL}/api/telemetry/ping",
)


def _get_anonymous_client_id() -> str:
    """Retrieve or generate a persistent pseudonymous client identifier."""
    settings = QSettings("SafeLauncher", "SafeLauncher")
    client_id = settings.value("telemetry_client_id", "", type=str).strip()
    if not client_id:
        client_id = str(uuid.uuid4())
        settings.setValue("telemetry_client_id", client_id)
    return client_id


def send_central_telemetry(app_version: str) -> bool:
    """Send one bounded anonymous heartbeat in the caller's managed worker."""
    from core.network_policy import automatic_network_allowed
    if not automatic_network_allowed():
        return False
    resp = None
    try:
        settings = QSettings("SafeLauncher", "SafeLauncher")
        if not settings.value("telemetry_enabled", True, type=bool):
            return False

        payload = {
            "clientId": _get_anonymous_client_id(),
            "appVersion": str(app_version),
            "platform": "linux",
        }
        resp = requests.post(
            _CENTRAL_TELEMETRY_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=4,
        )
        if resp.status_code == 200:
            logger.debug("Central telemetry heartbeat delivered.")
            return True
    except Exception as e:
        logger.debug(f"Telemetry heartbeat note: {e}")
    finally:
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
    return False
