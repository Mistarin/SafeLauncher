"""Library card construction and renderer lifetime, not query or process policy."""
import os
from dataclasses import dataclass
from typing import Callable

from PyQt6.QtCore import QTimer

from core.request_contracts import RequestPriority
from ui.components.banner_card import GameBannerWidget
from ui.components.virtual_grid import BannerProxy


@dataclass(frozen=True)
class LibraryCardActions:
    select: Callable
    open_detail: Callable
    favorite: Callable
    launch: Callable
    cloud_menu: Callable
    cloud_action: Callable


class LibraryCardRenderer:
    def __init__(self, host, artwork_cache, artwork, *, network_allowed, actions):
        self.host, self.cache, self.artwork = host, artwork_cache, artwork
        self.network_allowed, self.actions = network_allowed, actions
        self.cards = {}
        self._prefetch_games = []
        self._background_games = []
        self._visible_requested: set[int] = set()
        self._prefetch_epoch = 0

    def clear(self):
        self._prefetch_epoch += 1
        self._prefetch_games = []
        self._background_games = []
        self._visible_requested.clear()
        for card in tuple(self.cards.values()):
            try:
                card.hide()
                card.setParent(None)
                card.deleteLater()
            except (RuntimeError, AttributeError):
                pass  # Virtualized BannerProxy has no QWidget lifetime.
        self.cards.clear()

    def render(self, snapshot, selected_ids, *, use_virtual):
        self.clear()
        widgets = []
        for item in snapshot.items:
            game = item.game
            game_id, name, path, executable, mode, banner_url, steam_id = game[:7]
            icon = game[18] if len(game) > 18 else ""
            full_exe = os.path.join(path, executable) if path and executable else ""
            banner = banner_url if banner_url and os.path.exists(banner_url) else None
            if not use_virtual and not item.is_archived and (not icon or not os.path.exists(icon)):
                cached = self.cache.get_icon_cached_path(
                    steam_id=steam_id, game_name=name, exe_path=full_exe, game_id=game_id)
                icon = cached if cached and os.path.exists(cached) else ""
            if item.is_archived:
                banner, icon = None, ""
            if use_virtual:
                self.cards[game_id] = BannerProxy(game_id, self.host.virtual_grid)
            else:
                card = GameBannerWidget(game_id, name, banner, item.playtime_seconds,
                    version=game[15] if len(game) > 15 else "", icon_path=icon,
                    parent=self.host.grid_container)
                card.set_missing(item.is_missing)
                card.set_update_status(item.update_available, source=item.status.update_source,
                                       checked_at=item.status.update_checked_at)
                card.set_favorite(item.is_favorite)
                card.set_selected(game_id in selected_ids)
                if item.cloud_status is not None:
                    card.set_cloud_status(item.cloud_status)
                card.clicked.connect(self.actions.select)
                card.doubleClicked.connect(self.actions.open_detail)
                card.favoriteClicked.connect(self.actions.favorite)
                card.launchClicked.connect(self.actions.launch)
                card.rightClicked.connect(self.actions.cloud_menu)
                card.cloudActionRequested.connect(self.actions.cloud_action)
                widgets.append(card)
                self.cards[game_id] = card
        self.host.set_grid_widgets(widgets)
        self.host.render_snapshot(snapshot, self.cache.cache_dir, selected_ids)

    @staticmethod
    def _game_id(game) -> int:
        return int(game[0])

    def prefetch(self, games, *, visible_ids=None):
        if not self.network_allowed():
            return
        self._prefetch_epoch += 1
        epoch = self._prefetch_epoch
        self._prefetch_games = [game for game in games if not (len(game) > 17 and game[17])]
        self._background_games = list(self._prefetch_games)
        self._visible_requested.clear()
        if not self._prefetch_games:
            return
        if visible_ids is None:
            try:
                current_visible = set(self.host.visible_game_ids())
            except (AttributeError, TypeError, RuntimeError):
                current_visible = set()
        else:
            current_visible = set(visible_ids)
        if not current_visible:
            current_visible = {self._game_id(game) for game in self._prefetch_games[:24]}
        current_visible = [
            self._game_id(game)
            for game in self._prefetch_games
            if self._game_id(game) in current_visible
        ][:24]
        self.request_visible_artwork(set(current_visible))
        QTimer.singleShot(700, lambda epoch=epoch: self._schedule_background_batch(epoch))

    def request_visible_artwork(self, visible_ids=None):
        """Load artwork for the current viewport before lower-priority work."""
        if not self.network_allowed() or not self._prefetch_games:
            return
        if visible_ids is None:
            try:
                visible = set(self.host.visible_game_ids())
            except (AttributeError, TypeError, RuntimeError):
                visible = set()
        else:
            visible = set(visible_ids)
        if not visible:
            return
        ordered_visible = [
            self._game_id(game)
            for game in self._prefetch_games
            if self._game_id(game) in visible
        ][:24]
        visible = set(ordered_visible)
        for game in self._prefetch_games:
            game_id = self._game_id(game)
            if game_id not in visible or game_id in self._visible_requested:
                continue
            self._visible_requested.add(game_id)
            self._request_game_artwork(game, RequestPriority.NORMAL)

    def _schedule_background_batch(self, epoch):
        if epoch != self._prefetch_epoch or not self.network_allowed():
            return
        batch, self._background_games = self._background_games[:12], self._background_games[12:]
        for game in batch:
            self._request_game_artwork(game, RequestPriority.BACKGROUND)
        if self._background_games:
            QTimer.singleShot(250, lambda epoch=epoch: self._schedule_background_batch(epoch))

    def _request_game_artwork(self, game, priority):
        game_id, name, path, executable = game[:4]
        steam_id = game[6] if len(game) > 6 else ""
        full_exe = os.path.join(path, executable) if path and executable else ""
        banner = game[5] if len(game) > 5 else ""
        icon = game[18] if len(game) > 18 else ""
        if not banner or not os.path.exists(banner) or not icon or not os.path.exists(icon):
            self.artwork.request_auto(game_id, name, full_exe, str(steam_id or ""), priority=priority)
        if not self.cache.get_hero_cached_path(steam_id=steam_id, game_name=name,
                                               exe_path=full_exe, game_id=game_id):
            self.artwork.request_hero(game_id, name, steam_id, full_exe, priority=priority)
        if not icon or not os.path.exists(icon):
            self.artwork.request_icon(game_id, name, str(steam_id or ""), full_exe,
                                      priority=priority)
