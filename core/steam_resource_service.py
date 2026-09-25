"""Managed Steam metadata resources.

``SteamClient`` owns HTTP and parsing.  This service supplies stable cached
request specifications so duplicate AppID/name lookups share one resource.
"""

from __future__ import annotations

from typing import Callable, Iterable

from core.cache_policy import cache_policy
from core.request_contracts import (
    CancellationToken,
    RequestKey,
    RequestPriority,
    RequestSpec,
)
from core.steam_client import SteamClient
from core.steam_ids import normalize_steam_app_id


class SteamResourceService:
    """Create cached Steam build and store-tag resources."""

    BUILD_TTL_SECONDS = cache_policy("steam-build").max_age_seconds
    TAG_TTL_SECONDS = cache_policy("steam-tags").max_age_seconds
    APP_DETAILS_TTL_SECONDS = cache_policy("steam-app-details").max_age_seconds

    def __init__(self, request_manager, *, client: SteamClient | None = None):
        self.request_manager = request_manager
        self.client = client or SteamClient()
        self._owns_client = client is None

    @staticmethod
    def build_key(app_id: str) -> RequestKey:
        return RequestKey("steam-build", normalize_steam_app_id(app_id), "public-v1")

    @staticmethod
    def tags_key(game_name: str) -> RequestKey:
        identity = str(game_name or "").strip().casefold()
        return RequestKey("steam-tags", identity, "store-v1")

    @staticmethod
    def app_details_key(app_id: str) -> RequestKey:
        return RequestKey("steam-app-details", normalize_steam_app_id(app_id), "store-v1")

    def build_spec(
        self,
        app_id: str,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int = 0,
        tag: str = "",
    ) -> RequestSpec:
        app_id = normalize_steam_app_id(app_id)
        if not app_id:
            raise ValueError("Steam build metadata requires a numeric AppID")
        key = self.build_key(app_id)

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            value = self.client.public_build(app_id)
            token.raise_if_cancelled()
            return value

        return RequestSpec(
            key,
            load,
            priority=priority,
            generation=int(generation),
            metadata={"tag": str(tag), "resource_type": "steam-build"},
            timeout_seconds=15,
        )

    def tags_spec(
        self,
        game_name: str,
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int = 0,
        tag: str = "",
    ) -> RequestSpec:
        game_name = str(game_name or "").strip()
        key = self.tags_key(game_name)

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            value = self.client.tags_for_game(game_name)
            token.raise_if_cancelled()
            return value

        return RequestSpec(
            key,
            load,
            priority=priority,
            generation=int(generation),
            metadata={"tag": str(tag), "resource_type": "steam-tags"},
            timeout_seconds=10,
        )

    def app_details_spec(
        self,
        app_id: str,
        *,
        priority: RequestPriority = RequestPriority.BACKGROUND,
        generation: int = 0,
        tag: str = "",
    ) -> RequestSpec:
        app_id = normalize_steam_app_id(app_id)
        if not app_id:
            raise ValueError("Steam app details requires a numeric AppID")
        key = self.app_details_key(app_id)

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            value = self.client.app_details(app_id)
            token.raise_if_cancelled()
            return value

        return RequestSpec(
            key,
            load,
            priority=priority,
            generation=int(generation),
            metadata={"tag": str(tag), "resource_type": "steam-app-details"},
            timeout_seconds=10,
        )

    def request_build(self, app_id: str, **kwargs):
        spec = self.build_spec(app_id, **kwargs)
        return self._request_cached(spec, self.BUILD_TTL_SECONDS)

    def request_tags(self, game_name: str, **kwargs):
        spec = self.tags_spec(game_name, **kwargs)
        return self._request_cached(spec, self.TAG_TTL_SECONDS)

    def request_app_details(self, app_id: str, **kwargs):
        spec = self.app_details_spec(app_id, **kwargs)
        return self._request_cached(spec, self.APP_DETAILS_TTL_SECONDS)

    def _request_cached(self, spec: RequestSpec, max_age_seconds: float):
        cache = getattr(self.request_manager, "cache", None)
        if cache is None:
            return self.request_manager.submit(spec)
        return self.request_manager.cached_request(
            spec,
            cache,
            max_age_seconds=max_age_seconds,
            stale_while_revalidate=True,
        )

    def request_build_many(
        self,
        app_ids: Iterable[str],
        *,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int = 0,
        tag: str = "",
        on_complete: Callable | None = None,
    ):
        specs = []
        seen = set()
        for app_id in app_ids:
            app_id = normalize_steam_app_id(app_id)
            if not app_id or app_id in seen:
                continue
            seen.add(app_id)
            specs.append(
                self.build_spec(
                    app_id,
                    priority=priority,
                    generation=generation,
                    tag=tag,
                )
            )
        if getattr(self.request_manager, "cache", None) is None:
            return self.request_manager.request_many(specs, on_complete=on_complete)
        return self.request_manager.request_many_cached(
            specs,
            max_age_seconds=self.BUILD_TTL_SECONDS,
            stale_while_revalidate=True,
            on_complete=on_complete,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()


__all__ = ["SteamResourceService"]
