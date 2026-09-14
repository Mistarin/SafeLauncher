"""Request-manager resources for achievement resolution and persistence.

The achievement providers remain responsible for local-file discovery and
remote schema parsing.  This service owns the application-facing request
boundary: stable keys, worker database lifetimes, batch submission, and
explicit invalidation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from core.achievement_coordinator import coordinated_resolve, invalidate
from core.achievement_persistence import persist_resolution
from core.request_contracts import (
    CancellationToken,
    RequestKey,
    RequestPriority,
    RequestSpec,
    ResourceResult,
)


@dataclass(frozen=True, slots=True)
class AchievementTarget:
    """Local inputs needed to resolve one game's achievement state."""

    game_id: int
    app_id: str
    game_path: str = ""
    proton_path: str = ""


class AchievementResourceService:
    """Build managed achievement requests without any Qt dependency."""

    def __init__(self, request_manager, *, db_factory: Callable | None = None):
        self.request_manager = request_manager
        self._db_factory = db_factory

    @staticmethod
    def key_for(target: AchievementTarget) -> RequestKey:
        return RequestKey(
            "achievement-status",
            f"{str(target.app_id).strip()}:{int(target.game_id)}",
            "v1",
        )

    def _db(self, db_path: str | None):
        if self._db_factory is not None:
            return self._db_factory(db_path)
        from database import GameDatabase

        return GameDatabase(db_path) if db_path else GameDatabase()

    def status_spec(
        self,
        target: AchievementTarget,
        *,
        db_path: str | None = None,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int = 0,
        tag: str = "",
        download_icons: bool = False,
    ) -> RequestSpec:
        """Create a request that resolves and persists one game's state."""
        key = self.key_for(target)

        def load(token: CancellationToken):
            token.raise_if_cancelled()
            db = self._db(db_path)
            try:
                resolution = coordinated_resolve(
                    target.app_id,
                    target.game_path,
                    target.proton_path,
                    download_icons=download_icons,
                    request_manager=self.request_manager,
                    schema_cache=getattr(self.request_manager, "cache", None),
                )
                persist_resolution(db, target.game_id, target.app_id, resolution)
                unlocked_count, total_count, percentage = db.get_achievement_stats(
                    target.game_id
                )
                if total_count == 0 and resolution.schema:
                    persist_resolution(db, target.game_id, target.app_id, resolution)
                    unlocked_count, total_count, percentage = db.get_achievement_stats(
                        target.game_id
                    )
                recent = db.get_recent_unlocked_achievements(target.game_id, limit=5)
                token.raise_if_cancelled()
                return (
                    resolution,
                    unlocked_count,
                    total_count,
                    percentage,
                    recent,
                )
            finally:
                db.close()

        return RequestSpec(
            key,
            load,
            priority=priority,
            generation=int(generation),
            metadata={
                "allow_offline": True,
                "tag": str(tag),
                "download_icons": bool(download_icons),
            },
            timeout_seconds=60,
        )

    def request_status(self, target: AchievementTarget, **kwargs):
        return self.request_manager.submit(self.status_spec(target, **kwargs))

    def request_many(
        self,
        targets: Iterable[AchievementTarget],
        *,
        db_path: str | None = None,
        priority: RequestPriority = RequestPriority.NORMAL,
        generation: int = 0,
        tag: str = "",
        on_complete: Callable[[list[ResourceResult]], None] | None = None,
    ):
        specs = []
        seen = set()
        for target in targets:
            key = self.key_for(target)
            if key in seen:
                continue
            seen.add(key)
            specs.append(
                self.status_spec(
                    target,
                    db_path=db_path,
                    priority=priority,
                    generation=generation,
                    tag=tag,
                )
            )
        return self.request_manager.request_many(specs, on_complete=on_complete)

    def invalidate(self, target: AchievementTarget) -> bool:
        invalidate(target.app_id, target.game_path, target.proton_path)
        return self.request_manager.invalidate(self.key_for(target))


__all__ = ["AchievementResourceService", "AchievementTarget"]
