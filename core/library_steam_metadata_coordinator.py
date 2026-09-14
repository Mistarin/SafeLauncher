"""Qt-free grouping for library Steam metadata resources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.request_contracts import RequestKey, RequestPriority
from core.steam_resource_service import SteamResourceService


@dataclass(frozen=True, slots=True)
class SteamBuildRequestPlan:
    key: RequestKey
    game_id: int
    local_build_id: str
    local_build_date: int
    priority: RequestPriority
    new_binding: bool


@dataclass(frozen=True, slots=True)
class SteamTagRequestPlan:
    key: RequestKey
    game_id: int
    priority: RequestPriority
    new_binding: bool


class LibrarySteamMetadataCoordinator:
    """Group build/tag consumers while leaving UI rendering in MainWindow."""

    def __init__(self, service: SteamResourceService):
        self.service = service
        self._build_games: dict[RequestKey, dict[int, tuple[str, int]]] = {}
        self._tag_games: dict[RequestKey, set[int]] = {}
        self._bindings: dict[str, dict[RequestKey, Any]] = {"build": {}, "tag": {}}
        self._planned: dict[str, set[RequestKey]] = {"build": set(), "tag": set()}

    def prepare_build(
        self,
        game_id: int,
        app_id: str,
        local_build_id: str,
        local_build_date: int,
        *,
        priority: RequestPriority,
    ) -> SteamBuildRequestPlan:
        key = self.service.build_key(app_id)
        self._build_games.setdefault(key, {})[int(game_id)] = (
            str(local_build_id or "").strip(), int(local_build_date or 0)
        )
        new_binding = key not in self._bindings["build"] and key not in self._planned["build"]
        self._planned["build"].add(key)
        return SteamBuildRequestPlan(
            key, int(game_id), str(local_build_id or "").strip(),
            int(local_build_date or 0), priority, new_binding
        )

    def prepare_tags(
        self,
        game_id: int,
        game_name: str,
        *,
        priority: RequestPriority,
    ) -> SteamTagRequestPlan:
        key = self.service.tags_key(game_name)
        self._tag_games.setdefault(key, set()).add(int(game_id))
        new_binding = key not in self._bindings["tag"] and key not in self._planned["tag"]
        self._planned["tag"].add(key)
        return SteamTagRequestPlan(key, int(game_id), priority, new_binding)

    def attach_binding(self, kind: str, plan_or_key, binding: Any) -> None:
        key = plan_or_key.key if hasattr(plan_or_key, "key") else plan_or_key
        self._bindings[kind][key] = binding

    def binding(self, kind: str, key: RequestKey):
        return self._bindings[kind].get(key)

    def build_games(self, key: RequestKey):
        return self._build_games.get(key, {})

    def tag_game_ids(self, key: RequestKey):
        return tuple(self._tag_games.get(key, ()))

    def build_keys(self):
        return tuple(self._build_games)

    def close(self) -> list[Any]:
        bindings = [binding for groups in self._bindings.values() for binding in groups.values()]
        for kind in ("build", "tag"):
            self._bindings[kind].clear()
            self._planned[kind].clear()
        self._build_games.clear()
        self._tag_games.clear()
        return bindings


__all__ = [
    "LibrarySteamMetadataCoordinator",
    "SteamBuildRequestPlan",
    "SteamTagRequestPlan",
]
