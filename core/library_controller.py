"""Shared library query and derived-state model.

Views should render this snapshot rather than independently deciding which
games are visible.  The legacy tuple adapter keeps the current renderers
compatible while the presentation APIs are migrated incrementally.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from core.disk_utils import peek_dir_size


@dataclass(frozen=True)
class LibraryQuery:
    search: str = ""
    filter_mode: str = "all"
    collection: str = ""
    sort_index: int = 0


@dataclass(frozen=True)
class LibraryItemState:
    game: tuple
    is_missing: bool
    playtime_seconds: int
    is_favorite: bool
    is_archived: bool
    update_available: bool = False
    cloud_status: Any = None

    @property
    def game_id(self) -> int:
        return int(self.game[0])

    @property
    def name(self) -> str:
        return str(self.game[1] or "")

    def as_legacy_tuple(self) -> tuple:
        """Compatibility shape consumed by existing list/grid renderers."""
        return self.game, self.is_missing, self.playtime_seconds, self.is_favorite


@dataclass(frozen=True)
class LibrarySnapshot:
    query: LibraryQuery
    items: tuple[LibraryItemState, ...] = ()
    total_games: int = 0
    empty_message: str = ""
    collection_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def legacy_items(self) -> list[tuple]:
        return [item.as_legacy_tuple() for item in self.items]

    @property
    def visible_ids(self) -> set[int]:
        return {item.game_id for item in self.items}

    @property
    def update_status_map(self) -> dict[int, bool]:
        return {item.game_id: item.update_available for item in self.items}

    @property
    def cloud_status_map(self) -> dict[int, tuple]:
        return {
            item.game_id: (item.cloud_status, None, None)
            for item in self.items
            if item.cloud_status is not None
        }


class LibraryController:
    """Own library filtering, sorting, and derived item state."""

    def build_snapshot(
        self,
        games: Sequence[tuple],
        query: LibraryQuery,
        update_status: Mapping[int, bool] | None = None,
        cloud_status: Mapping[int, Any] | None = None,
    ) -> LibrarySnapshot:
        update_status = update_status or {}
        cloud_status = cloud_status or {}
        normalized_search = (query.search or "").strip().lower()
        normalized_filter = query.filter_mode or "all"
        normalized_collection = (query.collection or "").strip()

        collection_counts: dict[str, int] = {}
        for game in games:
            if len(game) > 17 and game[17]:
                continue
            collection = str(game[13]).strip() if len(game) > 13 else ""
            if collection:
                collection_counts[collection] = collection_counts.get(collection, 0) + 1

        items: list[LibraryItemState] = []
        for game in games:
            if len(game) < 7:
                continue
            game_id, name, path, executable, mode = game[:5]
            is_favorite = bool(game[8]) if len(game) > 8 and game[8] else False
            is_archived = bool(game[17]) if len(game) > 17 and game[17] else False

            searchable = " ".join(
                str(value or "")
                for value in (
                    name,
                    game[10] if len(game) > 10 else "",
                    executable,
                    game[6] if len(game) > 6 else "",
                    mode,
                    game[12] if len(game) > 12 else "",
                )
            ).lower()
            if normalized_search and normalized_search not in searchable:
                continue

            folder_exists = os.path.exists(path) if path else False
            full_executable = os.path.join(path, executable) if path and executable else path
            executable_exists = os.path.exists(full_executable) if full_executable else False
            is_missing = not (folder_exists and (executable_exists or not executable))

            collection = str(game[13]).strip() if len(game) > 13 else ""
            if normalized_collection and collection != normalized_collection:
                continue

            if normalized_filter == "archived":
                if not is_archived:
                    continue
            else:
                if is_archived:
                    continue
                if normalized_filter == "installed" and is_missing:
                    continue
                if normalized_filter == "favorites" and not is_favorite:
                    continue

            items.append(LibraryItemState(
                game=game,
                is_missing=is_missing,
                playtime_seconds=int(game[7] or 0) if len(game) > 7 else 0,
                is_favorite=is_favorite,
                is_archived=is_archived,
                update_available=bool(update_status.get(int(game_id), False)),
                cloud_status=(cloud_status.get(int(game_id), (None,))[0]
                              if cloud_status.get(int(game_id)) else None),
            ))

        if query.sort_index == 1:
            items.sort(key=lambda item: item.playtime_seconds, reverse=True)
        elif query.sort_index == 2:
            items.sort(
                key=lambda item: item.game[14] if len(item.game) > 14 and item.game[14] else item.game_id,
                reverse=True,
            )
        elif query.sort_index == 3:
            items.sort(key=lambda item: peek_dir_size(item.game[2]) or 0, reverse=True)
        elif query.sort_index == 4:
            items.sort(key=lambda item: str(item.game[4] or "").lower())
        else:
            items.sort(key=lambda item: item.name.lower())

        empty_message = ""
        if not items:
            if normalized_search:
                empty_message = f"No games matching '{query.search.strip()}'"
            elif normalized_filter == "favorites":
                empty_message = "No favorite games added yet"
            elif normalized_filter == "archived":
                empty_message = "No archived games found."
            elif normalized_filter == "installed":
                empty_message = "No installed games found."
            else:
                empty_message = "No games matching selected filter."
            if normalized_collection:
                empty_message = (
                    f"Collection '{normalized_collection}' is empty."
                    if normalized_filter == "all"
                    else f"No games in collection '{normalized_collection}' for this filter."
                )

        return LibrarySnapshot(
            query=query,
            items=tuple(items),
            total_games=len(games),
            empty_message=empty_message,
            collection_counts=dict(sorted(collection_counts.items(), key=lambda item: item[0].lower())),
        )
