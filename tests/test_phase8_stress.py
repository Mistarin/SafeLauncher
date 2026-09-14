"""Deterministic stress coverage for account/context and library navigation."""

from __future__ import annotations

import threading
import unittest

from core.cloud_context import CloudContext
from core.cloud_operations import CloudStatusResult
from core.cloud_save_sync import SyncStatus
from core.cloud_status_service import CloudStatusService, CloudStatusTarget
from core.library_controller import LibraryController, LibraryQuery
from core.library_state import LibraryStateStore
from core.request_manager import RequestManager


class _Coordinator:
    def __init__(self):
        self._generation = 1
        self.started = threading.Event()
        self.release = threading.Event()

    @property
    def generation(self):
        return self._generation

    def invalidate_context(self):
        self._generation += 1
        return self._generation

    def check_status(self, game_id, game_name, game_path, steam_id):
        if game_name == "Old Account Game":
            self.started.set()
            self.release.wait(2)
        return CloudStatusResult(game_name, SyncStatus.IN_SYNC)


class Phase8StressTests(unittest.TestCase):
    @staticmethod
    def _games(count=600):
        games = []
        for game_id in range(count):
            row = [
                game_id,
                f"Game {game_id}",
                "",
                "",
                "linux",
                "",
                str(1000 + game_id),
                0,
                1 if game_id % 3 == 0 else 0,
                0,
                "tag",
                "",
                "",
                "Collection A" if game_id % 2 == 0 else "Collection B",
                game_id,
                "",
                "",
                1 if game_id % 11 == 0 else 0,
            ]
            games.append(tuple(row))
        return games

    def test_rapid_filtering_keeps_snapshot_and_selection_consistent(self):
        games = self._games()
        store = LibraryStateStore(LibraryController())
        store.set_inputs(games, LibraryQuery())
        store.selection.replace({0, 1, 11, 12})
        queries = [
            LibraryQuery(search="game 1", filter_mode="all"),
            LibraryQuery(filter_mode="favorites"),
            LibraryQuery(filter_mode="archived"),
            LibraryQuery(filter_mode="installed", collection="Collection A"),
            LibraryQuery(search="tag", collection="Collection B"),
        ]

        for index in range(120):
            snapshot = store.set_inputs(games, queries[index % len(queries)])
            self.assertTrue(store.selected_ids.issubset(snapshot.visible_ids))
            for item in snapshot.items:
                self.assertIn(item.game_id, snapshot.visible_ids)
                if snapshot.query.filter_mode == "archived":
                    self.assertTrue(item.is_archived)
                elif snapshot.query.filter_mode == "favorites":
                    self.assertTrue(item.is_favorite)
                if snapshot.query.collection:
                    self.assertEqual(item.game[13], snapshot.query.collection)

    def test_rapid_account_switch_isolates_old_cloud_context(self):
        coordinator = _Coordinator()
        def provider(generation):
            return CloudContext(
                mode="convex",
                endpoint="https://private.example",
                fingerprint="account-a" if generation <= 1 else "account-b",
                generation=generation,
                network_allowed=True,
                backend_active=True,
                authentication_configured=True,
            )

        manager = RequestManager(max_workers=2)
        try:
            service = CloudStatusService(
                manager,
                coordinator=coordinator,
                context_provider=provider,
            )
            old = service.request_status(CloudStatusTarget(1, "Old Account Game"))
            self.assertTrue(coordinator.started.wait(2))
            service.invalidate_context()
            new = service.request_status(CloudStatusTarget(1, "New Account Game"))
            coordinator.release.set()
            self.assertNotEqual(old.key, new.key)
            self.assertEqual(new.future.result(timeout=2).value.game_name, "New Account Game")
            self.assertEqual(old.future.result(timeout=2).value.game_name, "Old Account Game")
            self.assertNotEqual(manager.state(old.key).generation, manager.state(new.key).generation)
        finally:
            coordinator.release.set()
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
