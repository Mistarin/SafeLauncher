"""Lightweight, privacy-friendly telemetry client for SafeLauncher.

Sends an anonymous ping on application startup to record active installation
metrics and version adoption. No personal data or game save data is ever transmitted.
"""

import os
import uuid
import threading
import requests
from PyQt6.QtCore import QSettings

from core.logger import get_logger

logger = get_logger("Telemetry")

# Default central analytics endpoint
_CENTRAL_TELEMETRY_URL = os.environ.get(
    "SAFELAUNCHER_TELEMETRY_URL",
    "https://moonlit-sockeye-565.eu-west-1.convex.site/api/telemetry/ping"
)


def _get_anonymous_client_id() -> str:
    """Retrieve or generate a persistent pseudonymous client identifier."""
    settings = QSettings("SafeLauncher", "SafeLauncher")
    client_id = settings.value("telemetry_client_id", "", type=str).strip()
    if not client_id:
        client_id = str(uuid.uuid4())
        settings.setValue("telemetry_client_id", client_id)
    return client_id


def ping_central_telemetry(app_version: str):
    """Send non-blocking anonymous heartbeat ping in a background thread."""
    def _worker():
        try:
            settings = QSettings("SafeLauncher", "SafeLauncher")
            if not settings.value("telemetry_enabled", True, type=bool):
                return

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
        except Exception as e:
            logger.debug(f"Telemetry heartbeat note: {e}")

    threading.Thread(
        target=_worker,
        daemon=True,
        name="SafeLauncher-TelemetryPing"
    ).start()
