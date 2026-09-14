"""Qt-free transport client for public Steam metadata.

The request manager owns when these methods run.  This client owns HTTP,
response validation, and conversion of Steam responses into small application
values so UI workers do not need to know Steam's endpoint shapes.
"""

from __future__ import annotations

from typing import Any
import requests


class SteamClientError(RuntimeError):
    """Raised when Steam returns an unusable metadata response."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = int(status_code or 0)


class SteamClient:
    STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
    APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
    BUILD_INFO_URL = "https://api.steamcmd.net/v1/info"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "SafeLauncher/1.0")
        self._owns_session = session is None

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def search(self, game_name: str, *, timeout: float = 8) -> list[dict[str, Any]]:
        response = self.session.get(
            self.STORE_SEARCH_URL,
            params={"term": str(game_name or "").strip(), "l": "english", "cc": "US"},
            timeout=timeout,
        )
        try:
            if response.status_code != 200:
                raise SteamClientError(
                    f"Steam search returned HTTP {response.status_code}",
                    response.status_code,
                )
            payload = response.json()
            items = payload.get("items", []) if isinstance(payload, dict) else []
            return [item for item in items if isinstance(item, dict) and item.get("id")]
        finally:
            response.close()

    def app_details(self, app_id: str | int, *, timeout: float = 8) -> dict[str, Any]:
        app_id = str(app_id).strip()
        response = self.session.get(self.APP_DETAILS_URL, params={"appids": app_id}, timeout=timeout)
        try:
            if response.status_code != 200:
                raise SteamClientError(
                    f"Steam app details returned HTTP {response.status_code}",
                    response.status_code,
                )
            payload = response.json()
            entry = payload.get(app_id, {}) if isinstance(payload, dict) else {}
            data = entry.get("data", {}) if isinstance(entry, dict) else {}
            return data if isinstance(data, dict) else {}
        finally:
            response.close()

    def tags_for_game(self, game_name: str, *, timeout: float = 8) -> tuple[list[str], str]:
        items = self.search(game_name, timeout=timeout)
        if not items:
            return [], ""
        app_id = str(items[0].get("id", ""))
        data = self.app_details(app_id, timeout=timeout)
        values = [
            str(item.get("description", ""))
            for group in (data.get("genres", []), data.get("categories", []))
            if isinstance(group, list)
            for item in group
            if isinstance(item, dict) and item.get("description")
        ]
        result = list(dict.fromkeys(values))[:4]
        return result, app_id

    def public_build(self, app_id: str | int, *, timeout: float = 12) -> tuple[str, int]:
        app_id = str(app_id).strip()
        response = self.session.get(f"{self.BUILD_INFO_URL}/{app_id}", timeout=timeout)
        try:
            if response.status_code != 200:
                raise SteamClientError(
                    f"Steam build metadata returned HTTP {response.status_code}",
                    response.status_code,
                )
            payload = response.json()
            app_data = payload.get("data", {}).get(app_id, {}) if isinstance(payload, dict) else {}
            branches = app_data.get("depots", {}).get("branches", {}) if isinstance(app_data, dict) else {}
            public = branches.get("public", {}) if isinstance(branches, dict) else {}
            build_id = str(public.get("buildid", "") or "").strip()
            try:
                updated = int(public.get("timeupdated", 0) or 0)
            except (TypeError, ValueError):
                updated = 0
            return build_id, updated
        finally:
            response.close()


__all__ = ["SteamClient", "SteamClientError"]
