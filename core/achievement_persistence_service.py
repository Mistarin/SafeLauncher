"""Application boundary for durable local achievement persistence."""

from __future__ import annotations

from dataclasses import dataclass

from core.achievement_persistence import persist_resolution


@dataclass(frozen=True, slots=True)
class AchievementProjection:
    unlocked_count: int
    total_count: int
    percentage: float
    recent: tuple


@dataclass(frozen=True, slots=True)
class AchievementPersistenceResult:
    changed: bool
    claimed: bool = False
    projection: AchievementProjection | None = None


class AchievementPersistenceService:
    """Own durable achievement writes while SQLite remains authoritative."""

    def __init__(self, database):
        self.database = database

    def schema(self, game_id: int) -> list[dict]:
        return list(self.database.get_game_achievements(int(game_id)))

    def projection(self, game_id: int) -> AchievementProjection:
        unlocked, total, percentage = self.database.get_achievement_stats(int(game_id))
        recent = self.database.get_recent_unlocked_achievements(int(game_id), limit=5)
        return AchievementProjection(
            int(unlocked or 0),
            int(total or 0),
            float(percentage or 0),
            tuple(recent or ()),
        )

    def stats_and_schema(self, game_id: int) -> tuple[AchievementProjection, list[dict]]:
        """Return the durable achievement projection used by compact/detail UI."""
        return self.projection(game_id), self.schema(game_id)

    def unlock(
        self,
        game_id: int,
        api_name: str,
        unlock_time: float,
        *,
        provenance: str = "local_emulator",
        verified: bool = False,
        source_format: str = "",
        source_path: str = "",
    ) -> AchievementPersistenceResult:
        """Persist one unlock and claim its notification exactly once."""
        game_id = int(game_id)
        self.database.unlock_achievement(
            game_id,
            str(api_name),
            float(unlock_time or 0),
            provenance=str(provenance or "local_emulator"),
            verified=bool(verified),
            source_format=str(source_format or ""),
            source_path=str(source_path or ""),
        )
        claimed = bool(self.database.claim_achievement_notification(game_id, str(api_name)))
        if not claimed:
            return AchievementPersistenceResult(False, False)
        return AchievementPersistenceResult(True, True, self.projection(game_id))

    def record_state(
        self,
        game_id: int,
        app_id: str,
        state: dict,
        *,
        provenance: str = "local_emulator",
        verified: bool = False,
        source_format: str = "json",
        source_path: str = "",
    ) -> AchievementPersistenceResult:
        """Persist a watcher snapshot and return its updated projection."""
        changed = bool(self.database.record_achievement_state(
            int(game_id),
            str(app_id),
            state,
            provenance=str(provenance or "local_emulator"),
            verified=bool(verified),
            source_format=str(source_format or ""),
            source_path=str(source_path or ""),
        ))
        return AchievementPersistenceResult(changed, projection=self.projection(game_id))

    def save_schema(self, game_id: int, app_id: str, achievements: list[dict]) -> int:
        return int(self.database.save_achievement_schema(
            int(game_id), str(app_id), list(achievements or [])
        ) or 0)

    def arm_notification(self, game_id: int, api_name: str) -> bool:
        return bool(self.database.arm_achievement_notification(int(game_id), str(api_name)))

    def persist_resolution(self, game_id: int, app_id: str, resolution) -> int:
        return int(persist_resolution(self.database, int(game_id), str(app_id), resolution) or 0)


__all__ = [
    "AchievementPersistenceResult",
    "AchievementPersistenceService",
    "AchievementProjection",
]
