"""Pure library interaction and rendering state.

The Qt views are intentionally not state owners.  This module keeps the
current query inputs and the derived immutable snapshot together so every
renderer observes the same library result.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from core.game_status import GameStatusState
from core.library_controller import LibraryController, LibraryQuery, LibrarySnapshot


class LibrarySelectionModel:
    def __init__(self):
        self._selected_ids = set()

    @property
    def ids(self) -> set[int]:
        return set(self._selected_ids)

    def click(self, game_id: int, additive: bool = False) -> set[int]:
        if additive:
            if game_id in self._selected_ids:
                self._selected_ids.remove(game_id)
            else:
                self._selected_ids.add(game_id)
        else:
            self._selected_ids = {game_id}
        return self.ids

    def replace(self, game_ids) -> set[int]:
        self._selected_ids = {int(game_id) for game_id in game_ids}
        return self.ids

    def clear(self) -> None:
        self._selected_ids.clear()

    def contains(self, game_id: int) -> bool:
        return game_id in self._selected_ids


class LibraryStateStore:
    """Single owner for library inputs, selection, and derived snapshot."""

    def __init__(self, controller: LibraryController | None = None):
        self.controller = controller or LibraryController()
        self.games: tuple[tuple, ...] = ()
        self.query = LibraryQuery()
        self.update_status: Mapping[int, bool] = {}
        self.cloud_status: Mapping[int, Any] = {}
        self.status: Mapping[int, GameStatusState] = {}
        self.selection = LibrarySelectionModel()
        self.snapshot = LibrarySnapshot(query=self.query)

    @property
    def selected_ids(self) -> set[int]:
        return self.selection.ids

    def set_inputs(
        self,
        games: Sequence[tuple],
        query: LibraryQuery,
        update_status: Mapping[int, bool] | None = None,
        cloud_status: Mapping[int, Any] | None = None,
        status: Mapping[int, GameStatusState] | None = None,
    ) -> LibrarySnapshot:
        """Replace all query inputs and derive one immutable snapshot."""
        self.games = tuple(games)
        self.query = query
        self.update_status = update_status or {}
        self.cloud_status = cloud_status or {}
        self.status = status or {}
        self.snapshot = self.controller.build_snapshot(
            self.games,
            self.query,
            update_status=self.update_status,
            cloud_status=self.cloud_status,
            status=self.status,
        )
        self.selection.replace(self.selected_ids.intersection(self.snapshot.visible_ids))
        return self.snapshot

    def rebuild(self) -> LibrarySnapshot:
        """Re-derive the snapshot after a status/cache change."""
        return self.set_inputs(
            self.games,
            self.query,
            self.update_status,
            self.cloud_status,
            self.status,
        )
