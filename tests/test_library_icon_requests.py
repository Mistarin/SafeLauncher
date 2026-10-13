"""Library icons are resolved through the request manager, not on the UI thread."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.artwork_resource_service import ArtworkResourceService, ArtworkTarget
from core.request_contracts import RequestPriority
from ui.library_card_renderer import LibraryCardRenderer


class IconSpecTests(unittest.TestCase):
    def test_icon_spec_runs_while_offline_because_local_extraction_needs_no_network(self):
        service = ArtworkResourceService(Mock(), client=Mock())
        spec = service.icon_spec(ArtworkTarget(1, "Game", "", "/games/game.exe"))
        self.assertTrue(spec.metadata.get("allow_offline"))


class RendererArtworkRequestTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.banner = Path(self._tmp.name, "banner.png")
        self.banner.write_bytes(b"png")
        self.icon = Path(self._tmp.name, "icon.png")
        self.icon.write_bytes(b"png")
        self.renderer = LibraryCardRenderer.__new__(LibraryCardRenderer)
        self.renderer.artwork = Mock()
        self.renderer.network_allowed = Mock(return_value=True)

    @staticmethod
    def _game(banner: str, icon: str):
        # Index layout used by the renderer: 0 id, 1 name, 2 path, 3 exe,
        # 5 banner, 6 steam id, 18 icon.
        game = [7, "Game", "/games", "game.exe", "", str(banner), "123"]
        game += [""] * 11
        game.append(str(icon))
        return tuple(game)

    def test_cached_banner_with_missing_icon_requests_only_the_icon(self):
        game = self._game(self.banner, Path(self.banner.parent, "missing.png"))
        self.renderer._request_game_artwork(game, RequestPriority.NORMAL)
        self.renderer.artwork.request_icon.assert_called_once_with(
            7, "Game", "123", "/games/game.exe", priority=RequestPriority.NORMAL
        )
        self.renderer.artwork.request_auto.assert_not_called()

    def test_missing_banner_uses_the_combined_auto_request_once(self):
        game = self._game(Path(self.banner.parent, "missing.png"), self.icon)
        self.renderer._request_game_artwork(game, RequestPriority.NORMAL)
        self.renderer.artwork.request_auto.assert_called_once()
        self.renderer.artwork.request_icon.assert_not_called()

    def test_offline_icon_request_still_runs_and_missing_banner_waits_for_network(self):
        self.renderer.network_allowed = Mock(return_value=False)
        icon_only = self._game(self.banner, Path(self.banner.parent, "missing.png"))
        self.assertTrue(self.renderer._request_game_artwork(icon_only, RequestPriority.NORMAL))
        self.renderer.artwork.request_icon.assert_called_once()

        needs_banner = self._game(Path(self.banner.parent, "missing.png"), self.icon)
        self.assertFalse(self.renderer._request_game_artwork(needs_banner, RequestPriority.NORMAL))
        self.renderer.artwork.request_auto.assert_not_called()

    def test_fully_cached_game_makes_no_request(self):
        self.renderer._request_game_artwork(self._game(self.banner, self.icon), RequestPriority.NORMAL)
        self.renderer.artwork.request_auto.assert_not_called()
        self.renderer.artwork.request_icon.assert_not_called()


if __name__ == "__main__":
    unittest.main()
