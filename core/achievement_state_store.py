"""Local, Qt-free achievement presentation state for the library UI."""

from __future__ import annotations

import time


class AchievementStateStore:
    """Own cached achievement projections without owning remote requests.

    The database remains authoritative for durable achievement rows.  These
    dictionaries are only the short-lived presentation projection used to
    avoid repeating reads while a library view is active.
    """

    def __init__(self):
        self.status: dict[int, tuple] = {}
        self.resolutions: dict[int, object] = {}
        self.checked_at: dict[int, float] = {}
        self.pending_unlocks: dict[int, dict[str, float]] = {}

    def get_status(self, game_id: int):
        return self.status.get(int(game_id))

    def set_status(self, game_id: int, value) -> None:
        self.status[int(game_id)] = value
        self.checked_at[int(game_id)] = time.time()

    def mark_checked(self, game_id: int) -> None:
        self.checked_at[int(game_id)] = time.time()

    def is_stale(self, game_id: int, max_age_seconds: float, *, now: float | None = None) -> bool:
        checked = self.checked_at.get(int(game_id), 0.0)
        return (float(now if now is not None else time.time()) - checked) > float(max_age_seconds)

    def resolution(self, game_id: int):
        return self.resolutions.get(int(game_id))

    def set_resolution(self, game_id: int, resolution) -> None:
        self.resolutions[int(game_id)] = resolution

    def pending_for(self, game_id: int) -> dict[str, float]:
        return self.pending_unlocks.setdefault(int(game_id), {})

    def pop_pending(self, game_id: int) -> dict[str, float]:
        return self.pending_unlocks.pop(int(game_id), {})

    def clear_game(self, game_id: int) -> None:
        game_id = int(game_id)
        self.status.pop(game_id, None)
        self.resolutions.pop(game_id, None)
        self.checked_at.pop(game_id, None)
        self.pending_unlocks.pop(game_id, None)


__all__ = ["AchievementStateStore"]
