"""Qt-free achievement request coordination for library presentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.achievement_resource_service import AchievementResourceService, AchievementTarget
from core.request_contracts import RequestKey


@dataclass(frozen=True, slots=True)
class AchievementRequestPlan:
    """Stable resource identity and subscription ownership for one target."""

    key: RequestKey
    target: AchievementTarget
    new_binding: bool


class LibraryAchievementCoordinator:
    """Group achievement consumers without depending on Qt or widgets."""

    def __init__(self, service: AchievementResourceService):
        self.service = service
        self._callbacks: dict[RequestKey, tuple[int, str]] = {}
        self._bindings: dict[RequestKey, Any] = {}
        self._planned_bindings: set[RequestKey] = set()
        self._batch_in_flight = False

    @property
    def batch_in_flight(self) -> bool:
        return self._batch_in_flight

    def set_batch_in_flight(self, value: bool) -> None:
        self._batch_in_flight = bool(value)

    def prepare(self, target: AchievementTarget) -> AchievementRequestPlan:
        key = self.service.key_for(target)
        self._callbacks[key] = (int(target.game_id), str(target.app_id))
        new_binding = key not in self._bindings and key not in self._planned_bindings
        self._planned_bindings.add(key)
        return AchievementRequestPlan(key, target, new_binding)

    def callback_data(self, key: RequestKey) -> tuple[int, str] | None:
        return self._callbacks.get(key)

    def attach_binding(self, plan_or_key: AchievementRequestPlan | RequestKey, binding: Any) -> None:
        key = plan_or_key.key if isinstance(plan_or_key, AchievementRequestPlan) else plan_or_key
        self._bindings[key] = binding

    def binding(self, key: RequestKey):
        return self._bindings.get(key)

    def close(self) -> list[Any]:
        bindings = list(self._bindings.values())
        self._bindings.clear()
        self._callbacks.clear()
        self._planned_bindings.clear()
        self._batch_in_flight = False
        return bindings


__all__ = ["AchievementRequestPlan", "LibraryAchievementCoordinator"]
