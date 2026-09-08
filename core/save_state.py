"""Authoritative in-memory save state shared by library and Save Manager UI."""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SaveGameState:
    cloud_status: Any = None
    local_stats: Any = None
    cloud_stats: Any = None
    snapshot: Any = None
    detected_locations: tuple = ()
    history: tuple = ()
    operation: Any = None


class SaveStateStore(MutableMapping):
    """Own save state while retaining the old ``dict[game_id]`` view.

    Existing library code reads ``(status, local_stats, cloud_stats)``. That
    compatibility view remains, but richer save state is stored beside it
    rather than in unrelated widget fields and temporary worker results.
    """

    def __init__(self):
        self._states: dict[int, SaveGameState] = {}

    def __getitem__(self, game_id: int):
        state = self._states[game_id]
        return state.cloud_status, state.local_stats, state.cloud_stats

    def __setitem__(self, game_id: int, value):
        status, local_stats, cloud_stats = value
        state = self._states.setdefault(int(game_id), SaveGameState())
        state.cloud_status = status
        state.local_stats = local_stats
        state.cloud_stats = cloud_stats
        if getattr(local_stats, "snapshot", None) is not None:
            state.snapshot = local_stats.snapshot

    def __delitem__(self, game_id: int):
        del self._states[game_id]

    def __iter__(self) -> Iterator[int]:
        return iter(self._states)

    def __len__(self) -> int:
        return len(self._states)

    def clear(self) -> None:
        self._states.clear()

    def state(self, game_id: int) -> SaveGameState:
        return self._states.setdefault(int(game_id), SaveGameState())

    def set_cloud_status(self, game_id: int, status, local_stats=None, cloud_stats=None) -> None:
        state = self.state(game_id)
        state.cloud_status = status
        state.local_stats = local_stats
        state.cloud_stats = cloud_stats
        state.snapshot = getattr(local_stats, "snapshot", None)

    def set_snapshot(self, game_id: int, snapshot) -> None:
        self.state(game_id).snapshot = snapshot

    def set_locations(self, game_id: int, locations) -> None:
        self.state(game_id).detected_locations = tuple(locations or ())

    def set_history(self, game_id: int, history) -> None:
        self.state(game_id).history = tuple(history or ())

    def set_operation(self, game_id: int, operation) -> None:
        self.state(game_id).operation = operation


__all__ = ["SaveGameState", "SaveStateStore"]
