"""Pure library interaction state, kept independent from Qt widgets."""


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
