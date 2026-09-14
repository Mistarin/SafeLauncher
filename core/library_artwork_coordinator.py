"""Qt-free artwork request coordination for the library presentation.

``ArtworkResourceService`` owns transport, caching, and request-manager
integration.  This module owns the library-level concerns that used to live in
``MainWindow``: stable target preparation, request grouping, attempted-game
deduplication, and the lifetime of UI binding objects supplied by the caller.

The coordinator deliberately does not import Qt.  A UI can attach any binding
object to a plan, while tests and non-Qt callers can exercise the request
grouping logic in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.artwork_resource_service import ArtworkResourceService, ArtworkTarget
from core.request_contracts import RequestKey, RequestPriority


@dataclass(frozen=True, slots=True)
class ArtworkRequestPlan:
    """Prepared artwork request plus whether it needs a new subscription."""

    kind: str
    key: RequestKey
    target: ArtworkTarget
    priority: RequestPriority
    new_binding: bool


class LibraryArtworkCoordinator:
    """Coordinate shared artwork requests for library rows and detail views.

    Requests are grouped by the resource key produced by
    :class:`ArtworkResourceService`, so multiple local game rows that resolve
    to the same Steam identity share one request and one UI subscription.
    Binding objects are opaque to this class; the Qt edge owns their creation
    and callback delivery.
    """

    _KINDS = ("auto", "hero", "icon")

    def __init__(self, service: ArtworkResourceService):
        self.service = service
        self._attempted: dict[str, set[int]] = {
            "auto": set(),
            "hero": set(),
            "icon": set(),
        }
        self._game_ids: dict[str, dict[RequestKey, set[int]]] = {
            kind: {} for kind in self._KINDS
        }
        self._bindings: dict[str, dict[RequestKey, Any]] = {
            kind: {} for kind in self._KINDS
        }
        # A plan can be prepared before the Qt binding is attached.  Reserve
        # the key immediately so two rows arriving in the same refresh still
        # agree that only the first row owns subscription creation.
        self._planned_bindings: dict[str, set[RequestKey]] = {
            kind: set() for kind in self._KINDS
        }

    @staticmethod
    def _identity(target: ArtworkTarget) -> str:
        identity = str(target.steam_id or "").strip()
        if not identity or identity == "0":
            identity = "name:" + str(target.game_name or "").strip().casefold()
        return identity

    def _spec(self, kind: str, target: ArtworkTarget, priority: RequestPriority):
        if kind == "auto":
            return self.service.auto_spec(target, priority=priority)
        if kind == "hero":
            return self.service.hero_spec(target, priority=priority)
        if kind == "icon":
            return self.service.icon_spec(target, priority=priority)
        raise ValueError(f"Unknown artwork request kind: {kind}")

    def prepare(
        self,
        kind: str,
        target: ArtworkTarget,
        *,
        priority: RequestPriority,
        mark_attempted: bool = False,
    ) -> ArtworkRequestPlan | None:
        """Prepare and register a target, returning ``None`` for duplicates.

        ``mark_attempted`` is used by all library-level fetch paths that make
        one attempt per local game.  It is especially important for automatic
        artwork: the shared resource key may represent several local games,
        but each game must still be guarded against repeated auto-discovery.
        """
        if kind not in self._KINDS:
            raise ValueError(f"Unknown artwork request kind: {kind}")
        if mark_attempted:
            attempted = self._attempted[kind]
            if int(target.game_id) in attempted:
                return None
            attempted.add(int(target.game_id))

        identity = self._identity(target)
        if identity == "name:":
            return None

        spec = self._spec(kind, target, priority)
        key = spec.key
        self._game_ids[kind].setdefault(key, set()).add(int(target.game_id))
        new_binding = (
            key not in self._bindings[kind]
            and key not in self._planned_bindings[kind]
        )
        self._planned_bindings[kind].add(key)
        return ArtworkRequestPlan(
            kind=kind,
            key=key,
            target=target,
            priority=priority,
            new_binding=new_binding,
        )

    def request(self, plan: ArtworkRequestPlan):
        """Submit a prepared plan through the existing artwork service."""
        if plan.kind == "auto":
            return self.service.request_auto(plan.target, priority=plan.priority)
        if plan.kind == "hero":
            return self.service.request_hero(plan.target, priority=plan.priority)
        if plan.kind == "icon":
            return self.service.request_icon(plan.target, priority=plan.priority)
        raise ValueError(f"Unknown artwork request kind: {plan.kind}")

    def attach_binding(self, plan: ArtworkRequestPlan, binding: Any) -> None:
        """Store an opaque UI binding for the plan's shared resource."""
        self._bindings[plan.kind][plan.key] = binding

    def binding(self, kind: str, key: RequestKey):
        return self._bindings[kind].get(key)

    def game_ids(self, kind: str, key: RequestKey) -> tuple[int, ...]:
        return tuple(self._game_ids[kind].get(key, ()))

    def discard(self, kind: str, key: RequestKey):
        """Forget one completed/failed group and return its binding."""
        binding = self._bindings[kind].pop(key, None)
        self._game_ids[kind].pop(key, None)
        self._planned_bindings[kind].discard(key)
        return binding

    def close(self) -> list[Any]:
        """Detach all UI bindings and clear request-group bookkeeping."""
        bindings = [
            binding
            for groups in self._bindings.values()
            for binding in groups.values()
        ]
        for kind in self._KINDS:
            self._bindings[kind].clear()
            self._game_ids[kind].clear()
            self._planned_bindings[kind].clear()
        for attempted in self._attempted.values():
            attempted.clear()
        return bindings


__all__ = ["ArtworkRequestPlan", "LibraryArtworkCoordinator"]
