"""Small, bounded connectivity probe used by the focused-window UX."""

from __future__ import annotations

import requests


DEFAULT_CONNECTIVITY_URL = "https://connectivitycheck.gstatic.com/generate_204"


def probe_internet(*, timeout: float = 3.0, url: str = DEFAULT_CONNECTIVITY_URL) -> tuple[bool, str]:
    """Return whether a public connectivity endpoint can be reached.

    This intentionally does not use the configured cloud backend: a backend
    outage is not necessarily an internet outage.  The caller receives a
    stable, user-safe reason while the exception itself remains in logs owned
    by the request layer.
    """
    try:
        response = requests.get(
            url,
            timeout=max(0.5, float(timeout)),
            allow_redirects=False,
            headers={"Cache-Control": "no-cache"},
        )
        try:
            reachable = 200 <= int(response.status_code) < 500
        finally:
            response.close()
        return reachable, "" if reachable else "The connectivity check was rejected."
    except requests.RequestException:
        return False, "The internet connection could not be reached."


__all__ = ["DEFAULT_CONNECTIVITY_URL", "probe_internet"]
