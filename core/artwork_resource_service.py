"""Managed artwork resources backed by the ArtworkClient transport."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import os

from core.artwork_client import ArtworkClient
from core.cache_policy import cache_policy
from core.request_contracts import CancellationToken, RequestKey, RequestPriority, RequestSpec


@dataclass(frozen=True, slots=True)
class ArtworkTarget:
    game_id: int
    game_name: str
    steam_id: str = ""
    exe_path: str = ""

    @property
    def identity(self) -> str:
        steam_id = str(self.steam_id or "").strip()
        if steam_id and steam_id != "0":
            return steam_id
        return "name:" + str(self.game_name or "").strip().casefold()


class ArtworkResourceService:
    """Create deduplicated, cached artwork requests for visible resources."""

    ARTWORK_TTL_SECONDS = cache_policy("artwork").max_age_seconds
    SEARCH_TTL_SECONDS = cache_policy("artwork-search").max_age_seconds

    def __init__(self, request_manager, *, client: ArtworkClient | None = None):
        self.request_manager = request_manager
        self.client = client or ArtworkClient()
        self._owns_client = client is None

    @staticmethod
    def _key(kind: str, identity: str, variant: str) -> RequestKey:
        return RequestKey(kind, identity, variant)

    @staticmethod
    def search_key(game_name: str) -> RequestKey:
        """Return a stable search key without embedding user-entered text."""
        identity = str(game_name or "").strip().casefold()
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return RequestKey("artwork-search", digest or "empty", "steam-store-v1")

    @staticmethod
    def banner_key(url: str) -> RequestKey:
        """Return a stable banner key without putting a CDN URL in cache state."""
        value = str(url or "").strip()
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        return RequestKey("artwork-banner", digest or "empty", "selected-v1")

    def search_spec(
        self,
        game_name: str,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int = 0,
    ) -> RequestSpec:
        game_name = str(game_name or "").strip()
        key = self.search_key(game_name)

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            value = self.client.search_game(game_name)
            token.raise_if_cancelled()
            return value

        return RequestSpec(
            key,
            load,
            priority=priority,
            generation=int(generation),
            timeout_seconds=15,
            metadata={"resource_type": "artwork-search"},
        )

    def banner_spec(
        self,
        url: str,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int = 0,
    ) -> RequestSpec:
        url = str(url or "").strip()
        key = self.banner_key(url)

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            value = self._transport_without_cache(self.client.download_banner, url)
            token.raise_if_cancelled()
            return value or ""

        return RequestSpec(
            key,
            load,
            priority=priority,
            generation=int(generation),
            timeout_seconds=30,
            metadata={"resource_type": "artwork-banner"},
        )

    def auto_spec(self, target: ArtworkTarget, *, priority: RequestPriority = RequestPriority.BACKGROUND, generation: int = 0) -> RequestSpec:
        key = self._key("artwork-auto", target.identity, "portrait-and-icon-v1")
        shared_cache = getattr(self.request_manager, "cache", None)
        search_key = self.search_key(target.game_name)

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            search_entry = shared_cache.get(search_key) if shared_cache is not None else None
            if search_entry is not None and search_entry.is_fresh(self.SEARCH_TTL_SECONDS):
                search = search_entry.value
            else:
                search = self.client.search_game(target.game_name)
                if shared_cache is not None:
                    shared_cache.put(search_key, search, content_type="application/json")
            token.raise_if_cancelled()
            banner_path = ""
            resolved_appid = target.identity if not target.identity.startswith("name:") else ""
            primary = search.get("primary") if isinstance(search, dict) else None
            if isinstance(primary, dict):
                resolved_appid = str(primary.get("appid") or resolved_appid or "")
                banner_url = str(primary.get("banner_url") or "")
                if banner_url:
                    cache_path = getattr(self.client, "banner_cache_path", None)
                    existing = cache_path(banner_url) if callable(cache_path) else None
                    if self._path_cache_validator(str(existing or "")):
                        banner_path = str(existing)
                    else:
                        banner_path = self._transport_without_cache(
                            self.client.download_banner, banner_url
                        ) or ""
            token.raise_if_cancelled()
            icon_cache_path = getattr(self.client, "get_icon_cached_path", None)
            existing_icon = icon_cache_path(
                steam_id=resolved_appid,
                game_name=target.game_name,
                exe_path=target.exe_path,
                game_id=target.game_id,
            ) if callable(icon_cache_path) else None
            if self._path_cache_validator(str(existing_icon or "")):
                icon_path = str(existing_icon)
            else:
                icon_path = self._transport_without_cache(
                    self.client.fetch_and_cache_game_icon,
                    target.game_id,
                    resolved_appid,
                    target.game_name,
                    exe_path=target.exe_path,
                ) or ""
            token.raise_if_cancelled()
            return (
                banner_path,
                int(resolved_appid) if resolved_appid.isdigit() else 0,
                icon_path,
            )

        return RequestSpec(key, load, priority=priority, generation=int(generation), timeout_seconds=45)

    def hero_spec(self, target: ArtworkTarget, *, priority: RequestPriority = RequestPriority.NORMAL, generation: int = 0) -> RequestSpec:
        key = self._key("artwork-hero", target.identity, "wide-v1")

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            value = self._transport_without_cache(
                self.client.download_hero_banner,
                target.steam_id,
                target.game_id,
                target.game_name,
                exe_path=target.exe_path,
            )
            token.raise_if_cancelled()
            return value

        return RequestSpec(key, load, priority=priority, generation=int(generation), timeout_seconds=30)

    def icon_spec(self, target: ArtworkTarget, *, priority: RequestPriority = RequestPriority.NORMAL, generation: int = 0) -> RequestSpec:
        key = self._key("artwork-icon", target.identity, "icon-v1")

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            value = self._transport_without_cache(
                self.client.fetch_and_cache_game_icon,
                target.game_id,
                target.steam_id,
                target.game_name,
                exe_path=target.exe_path,
            )
            token.raise_if_cancelled()
            return value

        return RequestSpec(key, load, priority=priority, generation=int(generation), timeout_seconds=30)

    def request_auto(self, target: ArtworkTarget, **kwargs):
        return self._request_cached(
            self.auto_spec(target, **kwargs),
            cache_validator=self._auto_cache_validator,
        )

    def request_search(self, game_name: str, **kwargs):
        return self._request_cached(
            self.search_spec(game_name, **kwargs),
            max_age_seconds=self.SEARCH_TTL_SECONDS,
        )

    def request_banner(self, url: str, **kwargs):
        cache_path = getattr(self.client, "banner_cache_path", None)
        return self._request_cached(
            self.banner_spec(url, **kwargs),
            max_age_seconds=self.ARTWORK_TTL_SECONDS,
            legacy_value=cache_path(str(url or "")) if callable(cache_path) else None,
            cache_validator=self._path_cache_validator,
            content_type="text/plain",
        )

    def request_hero(self, target: ArtworkTarget, **kwargs):
        cache_path = getattr(self.client, "get_hero_cached_path", None)
        return self._request_cached(
            self.hero_spec(target, **kwargs),
            legacy_value=cache_path(
                steam_id=target.steam_id,
                game_name=target.game_name,
                exe_path=target.exe_path,
                game_id=target.game_id,
            ) if callable(cache_path) else None,
            cache_validator=self._path_cache_validator,
            content_type="text/plain",
        )

    def request_icon(self, target: ArtworkTarget, **kwargs):
        cache_path = getattr(self.client, "get_icon_cached_path", None)
        return self._request_cached(
            self.icon_spec(target, **kwargs),
            legacy_value=cache_path(
                steam_id=target.steam_id,
                game_name=target.game_name,
                exe_path=target.exe_path,
                game_id=target.game_id,
            ) if callable(cache_path) else None,
            cache_validator=self._path_cache_validator,
            content_type="text/plain",
        )

    @staticmethod
    def _path_cache_validator(value) -> bool:
        if not isinstance(value, str) or not value or not os.path.isfile(value):
            return False
        try:
            return os.path.getsize(value) > 0
        except OSError:
            return False

    @staticmethod
    def _auto_cache_validator(value) -> bool:
        if not isinstance(value, (tuple, list)) or len(value) < 3:
            return False
        paths = [str(value[0] or ""), str(value[2] or "")]
        return all(not path or ArtworkResourceService._path_cache_validator(path) for path in paths)

    @staticmethod
    def _transport_without_cache(method, *args, **kwargs):
        """Use raw transport in production while supporting older clients."""
        try:
            supports_flag = "use_cache" in inspect.signature(method).parameters
        except (TypeError, ValueError):
            supports_flag = False
        if supports_flag:
            kwargs["use_cache"] = False
        return method(*args, **kwargs)

    def _request_cached(
        self,
        spec: RequestSpec,
        *,
        max_age_seconds: float | None = None,
        content_type: str = "application/json",
        legacy_value=None,
        cache_validator=None,
    ):
        cache = getattr(self.request_manager, "cache", None)
        if cache is None:
            return self.request_manager.submit(spec)
        if legacy_value:
            if isinstance(legacy_value, os.PathLike):
                legacy_value = str(legacy_value)
            if self._path_cache_validator(legacy_value) and cache.get(spec.key) is None:
                cache.put(spec.key, str(legacy_value), content_type=content_type)
        return self.request_manager.cached_request(
            spec,
            cache,
            max_age_seconds=(
                self.ARTWORK_TTL_SECONDS
                if max_age_seconds is None
                else max_age_seconds
            ),
            stale_while_revalidate=True,
            cache_validator=cache_validator,
            content_type=content_type,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


__all__ = ["ArtworkResourceService", "ArtworkTarget"]
