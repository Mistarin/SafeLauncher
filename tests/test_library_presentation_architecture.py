from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QScrollArea
from database import GameRecord
from core.library_controller import LibraryController, LibraryQuery
from core.request_contracts import RequestPriority
from ui.library_card_renderer import LibraryCardRenderer, LibraryCardActions
from ui.library_navigation_controller import LibraryNavigationController, LibraryNavigationViews


class LibraryPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def navigation(self, mode='grid'):
        root = QWidget()
        layout = QVBoxLayout(root)
        widgets = [QWidget(root) for _ in range(8)]
        header, collection, inspector, reveal, sidebar, footer, profile, unused = widgets
        profile.show_owner = Mock()
        scroll = QScrollArea(root)
        for widget in [scroll, header, collection, inspector, reveal, sidebar, footer, profile]:
            layout.addWidget(widget)
        state = SimpleNamespace(mode=mode, virtual=False, collection=True)
        host = Mock()
        nav = LibraryNavigationController(
            LibraryNavigationViews(host, scroll, header, collection, inspector, reveal,
                                   sidebar, footer, profile, layout),
            mode=lambda: state.mode, virtual=lambda: state.virtual,
            collection_active=lambda: state.collection, update_detail=Mock(),
            update_compact=Mock(), show_inspector=Mock(), has_selection=lambda: True)
        self.addCleanup(root.deleteLater)
        return nav, state, root

    def test_grid_detail_back_restores_all_geometry(self):
        nav, state, root = self.navigation()
        nav.library(); nav.detail()
        self.assertTrue(nav.views.header.isHidden())
        nav.library()
        self.assertFalse(nav.views.header.isHidden())
        self.assertFalse(nav.views.collection.isHidden())
        self.assertEqual(nav.views.layout.getContentsMargins(), (18, 14, 18, 14))
        self.assertEqual(nav.views.layout.spacing(), 12)
        self.assertEqual(nav.views.scroll.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def test_profile_roundtrip_preserves_hidden_sidebar_and_view_mode(self):
        nav, state, root = self.navigation('compact')
        nav.views.sidebar.hide()
        nav.library(); nav.show_profile(); nav.show_profile()
        nav.library()  # Background refresh must not reopen library behind profile.
        self.assertTrue(nav.views.scroll.isHidden())
        nav.close_profile()
        self.assertTrue(nav.views.sidebar.isHidden())
        self.assertTrue(nav.views.header.isHidden())
        self.assertEqual(nav.views.layout.getContentsMargins(), (0, 0, 0, 0))
        self.assertEqual(state.mode, 'compact')

    def test_repeated_profile_roundtrips_do_not_accumulate_layout_changes(self):
        nav, state, root = self.navigation()
        for _ in range(5):
            nav.show_profile(); nav.close_profile()
            self.assertEqual(nav.views.layout.spacing(), 12)
            self.assertEqual(nav.views.layout.getContentsMargins(), (18, 14, 18, 14))
        self.assertFalse(nav.profile_active)

    def test_virtual_grid_remains_virtual_after_detail_back(self):
        nav, state, root = self.navigation()
        state.virtual = True
        nav.detail(); nav.library()
        nav.views.host.set_mode.assert_called_with('grid', use_virtual=True)

    def renderer(self, online=False):
        parent = QWidget()
        host = Mock(grid_container=parent)
        cache = Mock(cache_dir='/missing')
        cache.get_icon_cached_path.return_value = ''
        cache.get_hero_cached_path.return_value = ''
        actions = LibraryCardActions(*(Mock() for _ in range(6)))
        renderer = LibraryCardRenderer(host, cache, Mock(), network_allowed=lambda: online, actions=actions)
        self.addCleanup(renderer.clear)
        self.addCleanup(parent.deleteLater)
        return renderer

    def test_virtual_render_keeps_ids_without_constructing_cards(self):
        renderer = self.renderer()
        games = [GameRecord(1, 'One', '', '', 'umu'), GameRecord(2, 'Two', '', '', 'umu')]
        snapshot = LibraryController().build_snapshot(games, LibraryQuery())
        renderer.render(snapshot, {2}, use_virtual=True)
        self.assertEqual(set(renderer.cards), {1, 2})
        renderer.host.set_grid_widgets.assert_called_once_with([])
        renderer.host.render_snapshot.assert_called_once_with(snapshot, '/missing', {2})
        renderer.artwork.request_auto.assert_not_called()

    def test_card_signals_use_injected_actions(self):
        renderer = self.renderer()
        snapshot = LibraryController().build_snapshot([GameRecord(1, 'One', '', '', 'umu')], LibraryQuery())
        renderer.render(snapshot, {1}, use_virtual=False)
        renderer.cards[1].clicked.emit(1)
        renderer.actions.select.assert_called_once_with(1)
        renderer.clear()
        self.assertEqual(renderer.cards, {})

    def test_active_game_card_uses_stop_action(self):
        renderer = self.renderer()
        renderer.running_games = lambda: {1}
        snapshot = LibraryController().build_snapshot([GameRecord(1, 'One', '', '', 'umu')], LibraryQuery())
        with patch('ui.components.banner_card.get_icon', return_value=QIcon()) as get_icon:
            renderer.render(snapshot, set(), use_virtual=False)
        card = renderer.cards[1]
        self.assertTrue(card.is_running)
        self.assertEqual(card.btn_card_play.toolTip(), 'Stop One')
        self.assertEqual(get_icon.call_args.args[0], 'ph.stop-fill')

    def test_running_ids_are_forwarded_to_virtual_renderer(self):
        renderer = self.renderer()
        renderer.running_games = lambda: {1, 3}
        snapshot = LibraryController().build_snapshot([GameRecord(1, 'One', '', '', 'umu')], LibraryQuery())
        renderer.render(snapshot, set(), use_virtual=True)
        renderer.host.set_running_game_ids.assert_called_once_with({1, 3})

    def test_archived_games_never_schedule_artwork(self):
        renderer = self.renderer(online=True)
        game = GameRecord(1, 'Archived', '', '', 'umu', is_archived=1)
        snapshot = LibraryController().build_snapshot([game], LibraryQuery(filter_mode='archived'))
        renderer.render(snapshot, set(), use_virtual=True)
        renderer.prefetch([game])
        renderer.artwork.request_auto.assert_not_called()
        renderer.artwork.request_hero.assert_not_called()
        renderer.artwork.request_icon.assert_not_called()
        renderer.cache.get_icon_cached_path.assert_not_called()

    def test_visible_artwork_is_requested_at_foreground_priority(self):
        renderer = self.renderer(online=True)
        games = [
            (index, f"Game {index}", "", "", "umu", "", str(index), 0, 0,
             0, "", "", "", "", 0, "", "", 0, "")
            for index in range(1, 31)
        ]
        renderer.prefetch(games, visible_ids={3, 5})
        auto_calls = renderer.artwork.request_auto.call_args_list
        self.assertEqual([call.args[0] for call in auto_calls], [3, 5])
        self.assertTrue(all(call.kwargs["priority"] == RequestPriority.NORMAL
                            for call in auto_calls))
        self.assertEqual(renderer.artwork.request_hero.call_count, 2)
        self.assertEqual(renderer.artwork.request_icon.call_count, 2)
