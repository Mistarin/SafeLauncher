"""Tests for the UI-managed artwork request lifecycle."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.artwork_resource_service import ArtworkTarget
from core.library_artwork_coordinator import LibraryArtworkCoordinator
from core.request_contracts import RequestKey, RequestPriority, RequestSpec, ResourceStatus
from ui.artwork_controller import ArtworkController


class _Manager:
    def __init__(self):
        self.states = {}

    def state(self, key):
        return self.states.get(key, SimpleNamespace(usable=False, value=None))


class _Service:
    def __init__(self, manager):
        self.request_manager = manager
        self.client = object()
        self.requests = []

    def _spec(self, kind, target, priority):
        return RequestSpec(
            RequestKey("artwork-" + kind, target.identity, "v1"),
            lambda _token: None,
            priority=priority,
        )

    def auto_spec(self, target, *, priority):
        return self._spec("auto", target, priority)

    def hero_spec(self, target, *, priority):
        return self._spec("hero", target, priority)

    def icon_spec(self, target, *, priority):
        return self._spec("icon", target, priority)

    def request_auto(self, target, *, priority):
        self.requests.append(("auto", target, priority))

    def request_hero(self, target, *, priority):
        self.requests.append(("hero", target, priority))

    def request_icon(self, target, *, priority):
        self.requests.append(("icon", target, priority))


class _Signal:
    def connect(self, callback):
        self.callback = callback


class _Fetcher:
    def __init__(self, *_args, **_kwargs):
        self.banner_auto_downloaded = _Signal()
        self.finished = _Signal()
        self.started = 0
        self.interrupted = False

    def start(self):
        self.started += 1

    def isRunning(self):
        return self.started > 0 and not self.interrupted

    def requestInterruption(self):
        self.interrupted = True

    def isInterruptionRequested(self):
        return self.interrupted


class ArtworkControllerTests(unittest.TestCase):
    def setUp(self):
        self.manager = _Manager()
        self.service = _Service(self.manager)
        self.coordinator = LibraryArtworkCoordinator(self.service)
        self.auto_applied = Mock()
        self.hero_applied = Mock()
        self.icon_applied = Mock()
        self.register_worker = Mock()
        self.controller = ArtworkController(
            self.service,
            self.coordinator,
            binding_parent=None,
            network_allowed=lambda: True,
            register_worker=self.register_worker,
            on_auto_artwork=self.auto_applied,
            on_hero_artwork=self.hero_applied,
            on_icon_artwork=self.icon_applied,
        )

    @patch("ui.artwork_controller.bind_resource")
    def test_auto_artwork_submits_once_and_applies_shared_completion(self, bind):
        binding = Mock()
        bind.return_value = binding
        self.controller.request_auto(1, "Example", "/game/game.exe", "480")
        self.controller.request_auto(2, "Example copy", "/game2/game.exe", "480")

        self.assertEqual(len(self.service.requests), 1)
        kind, target, priority = self.service.requests[0]
        self.assertEqual((kind, target, priority), (
            "auto", ArtworkTarget(1, "Example", "480", "/game/game.exe"),
            RequestPriority.BACKGROUND,
        ))
        self.assertEqual(self.coordinator.game_ids("auto", bind.call_args.args[1]), (1, 2))

        result = SimpleNamespace(
            status=ResourceStatus.READY,
            value=("/cache/banner.png", 480, "/cache/icon.png"),
            error="",
        )
        self.controller._on_auto_state(bind.call_args.args[1], result)

        self.auto_applied.assert_has_calls([
            unittest.mock.call(1, "/cache/banner.png", 480, "/cache/icon.png"),
            unittest.mock.call(2, "/cache/banner.png", 480, "/cache/icon.png"),
        ])
        binding.close.assert_called_once()

    @patch("ui.artwork_controller.bind_resource")
    def test_hero_result_is_delivered_to_each_game_sharing_identity(self, bind):
        bind.return_value = Mock()
        self.controller.request_hero(7, "Example", "480", "/game/game.exe", priority=RequestPriority.NORMAL)
        key = self.coordinator._binding_key_for_test if False else next(
            iter(self.coordinator._game_ids["hero"])
        )
        self.controller.request_hero(8, "Duplicate row", "480", "/other/game.exe", priority=RequestPriority.BACKGROUND)
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "hero.png"
            image.write_bytes(b"image")
            self.controller._on_single_state(
                "hero", key, SimpleNamespace(status=ResourceStatus.READY, value=str(image))
            )

        self.hero_applied.assert_has_calls(
            [unittest.mock.call(7, str(image)), unittest.mock.call(8, str(image))],
            any_order=True,
        )

    def test_cached_shared_hero_applies_immediately_to_new_row(self):
        first = self.coordinator.prepare(
            "hero", ArtworkTarget(1, "Example", "480"),
            priority=RequestPriority.NORMAL, mark_attempted=True,
        )
        self.coordinator.attach_binding(first, object())
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "hero.png"
            image.write_bytes(b"image")
            self.manager.states[first.key] = SimpleNamespace(usable=True, value=str(image))

            self.controller.request_hero(
                2, "Example copy", "480", "", priority=RequestPriority.BACKGROUND
            )

            self.hero_applied.assert_called_once_with(2, str(image))

    @patch("ui.artwork_controller.bind_resource")
    def test_shutdown_closes_managed_bindings_and_cancels_compatibility_work(self, bind):
        binding = Mock()
        bind.return_value = binding
        self.controller.request_icon(4, "Example", "480", "", priority=RequestPriority.NORMAL)
        self.controller.cancel_compatibility_fetches = Mock()

        self.controller.shutdown()

        binding.close.assert_called_once()
        binding.deleteLater.assert_called_once()
        self.controller.cancel_compatibility_fetches.assert_called_once()

    @patch("ui.artwork_controller.BannerAutoFetcher", _Fetcher)
    def test_compatibility_fetch_is_deduplicated_and_cancelled_offline(self):
        service = _Service(None)
        coordinator = LibraryArtworkCoordinator(service)
        registered = []
        controller = ArtworkController(
            service,
            coordinator,
            binding_parent=None,
            network_allowed=lambda: True,
            register_worker=registered.append,
            on_auto_artwork=Mock(),
            on_hero_artwork=Mock(),
            on_icon_artwork=Mock(),
        )

        controller.request_auto(3, "Example", "", "")
        controller.request_auto(3, "Example", "", "")

        self.assertEqual(len(registered), 1)
        self.assertEqual(registered[0].started, 1)
        controller.cancel_compatibility_fetches()
        self.assertTrue(registered[0].interrupted)


if __name__ == "__main__":
    unittest.main()
