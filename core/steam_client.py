"""Qt-free transport client for public Steam metadata.

The request manager owns when these methods run.  This client owns HTTP,
response validation, and conversion of Steam responses into small application
values so UI workers do not need to know Steam's endpoint shapes.
"""

from __future__ import annotations

from typing import Any
import html
import re

import requests

from core.request_contracts import classify_remote_error
from core.steam_ids import normalize_steam_app_id


class SteamClientError(RuntimeError):
    """Raised when Steam returns an unusable metadata response."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = int(status_code or 0)
        self.category = classify_remote_error(self).value


class SteamClient:
    STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
    APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
    BUILD_INFO_URL = "https://api.steamcmd.net/v1/info"
    NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"

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
        app_id = normalize_steam_app_id(app_id)
        if not app_id:
            raise SteamClientError("Steam app details requires a numeric AppID")
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
        app_id = normalize_steam_app_id(app_id)
        if not app_id:
            raise SteamClientError("Steam build metadata requires a numeric AppID")
        response = self.session.get(f"{self.BUILD_INFO_URL}/{app_id}", timeout=timeout)
        try:
            if response.status_code != 200:
                raise SteamClientError(
                    f"Steam build metadata returned HTTP {response.status_code}",
                    response.status_code,
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise SteamClientError("Steam build metadata was not valid JSON") from exc
            # Third-party payloads change shape; anything unexpected is a
            # reportable error, never an AttributeError from deep inside.
            depots = _object(_object(_object(payload, "data"), app_id), "depots")
            public = _object(_object(depots, "branches"), "public")
            build_id = str(public.get("buildid", "") or "").strip()
            try:
                updated = int(public.get("timeupdated", 0) or 0)
            except (TypeError, ValueError):
                updated = 0
            return build_id, updated
        finally:
            response.close()

    def news(self, app_id: str | int, *, count: int = 5, timeout: float = 8) -> list[dict[str, Any]]:
        """Recent news/patch notes for an app (public ISteamNews endpoint, no key)."""
        app_id = normalize_steam_app_id(app_id)
        if not app_id:
            raise SteamClientError("Steam news requires a numeric AppID")
        response = self.session.get(
            self.NEWS_URL,
            params={"appid": app_id, "count": max(1, min(20, int(count))), "maxlength": 400, "format": "json"},
            timeout=timeout,
        )
        try:
            if response.status_code != 200:
                raise SteamClientError(f"Steam news returned HTTP {response.status_code}", response.status_code)
            try:
                payload = response.json()
            except ValueError as exc:
                raise SteamClientError("Steam news was not valid JSON") from exc
            items = _object(payload, "appnews").get("newsitems") or []
            if not isinstance(items, list):
                raise SteamClientError("Steam news payload was malformed")
            return [_news_item(item, app_id) for item in items if isinstance(item, dict)]
        finally:
            response.close()


_TAG_RE = re.compile(r"\[/?[a-zA-Z0-9_\-=\"' ./:]*\]|<[^>]*>")


def _object(container: Any, key: str) -> dict[str, Any]:
    """Return ``container[key]`` as a dict; a missing or non-dict value means "no data"."""
    if not isinstance(container, dict):
        raise SteamClientError("Steam response had an unexpected shape")
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def _news_item(item: dict[str, Any], app_id: str) -> dict[str, Any]:
    """Normalize one news entry into display-safe plain text."""
    text = html.unescape(_TAG_RE.sub(" ", str(item.get("contents") or "")))
    text = " ".join(text.split())
    try:
        published = int(item.get("date") or 0)
    except (TypeError, ValueError, OverflowError):
        published = 0
    return {
        "gid": str(item.get("gid") or ""),
        "title": " ".join(html.unescape(str(item.get("title") or "")).split())[:300],
        "text": text[:600],
        "author": str(item.get("author") or "")[:120],
        "date": published,
        # Always link to Steam's own news page: external feed URLs are not ours to open.
        "url": f"https://store.steampowered.com/news/app/{app_id}",
    }


__all__ = ["SteamClient", "SteamClientError"]
