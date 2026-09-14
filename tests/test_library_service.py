"""Focused tests for the MainWindow library application boundary."""

from __future__ import annotations

import unittest

from core.library_controller import LibraryController, LibraryQuery
from core.library_service import LibraryService
from core.library_state import LibraryStateStore
from core.game_status import GameStatusState
from database import GameDatabase


class LibraryServiceTests(unittest.TestCase):
    def test_refresh_reads_projection_and_builds_shared_snapshot(self):
        db = GameDatabase(":memory:")
        try:
            game_id = db.add_game("Service Game", "/missing", "game.exe", "umu", "", "123")
            db.add_collection("Favorites")
            db.update_game_collection(game_id, "Favorites")
            service = LibraryService(db, LibraryStateStore(LibraryController()))

            projection = service.refresh(LibraryQuery(collection="Favorites"))

            self.assertEqual(projection.games_by_id[game_id][1], "Service Game")
            self.assertEqual(projection.snapshot.visible_ids, {game_id})
            self.assertEqual(projection.collection_counts, (("Favorites", 1),))
        finally:
            db.close()

    def test_archive_and_restore_preserve_the_same_database_identity(self):
        db = GameDatabase(":memory:")
        try:
            game_id = db.add_game("Archived Service Game", "/missing", "game.exe", "umu", "")
            service = LibraryService(db, LibraryStateStore())
            self.assertTrue(service.archive_game(game_id))
            archived = service.refresh(LibraryQuery(filter_mode="archived"))
            self.assertEqual(archived.snapshot.visible_ids, {game_id})
            self.assertTrue(service.restore_game(game_id))
            active = service.refresh(LibraryQuery(filter_mode="all"))
            self.assertEqual(active.snapshot.visible_ids, {game_id})
        finally:
            db.close()

    def test_selection_api_is_owned_by_the_service(self):
        db = GameDatabase(":memory:")
        try:
            service = LibraryService(db, LibraryStateStore())
            self.assertEqual(service.replace_selection({1, 2}), {1, 2})
            self.assertEqual(service.toggle_selection(3, additive=True), {1, 2, 3})
            self.assertEqual(service.remove_from_selection(2), {1, 3})
            service.clear_selection()
            self.assertEqual(service.selected_ids, set())
        finally:
            db.close()

    def test_status_reconciliation_is_deterministic_before_rendering(self):
        db = GameDatabase(":memory:")
        try:
            game_id = db.add_game("Status Game", "/missing", "game.exe", "umu", "")
            service = LibraryService(db, LibraryStateStore())
            status = service.reconcile_statuses(
                [(game_id, "Status Game", "/missing", "game.exe", "umu", "", "", 0, 0)],
                current={game_id: GameStatusState()},
                update_status={game_id: True},
                cloud_status={game_id: ("local_newer", {"local": 1}, {"cloud": 2})},
                build_results={game_id: ("build-7", 123, "", "failed")},
            )[game_id]
            self.assertTrue(status.update_available)
            self.assertEqual(status.update_build_id, "build-7")
            self.assertEqual(status.cloud_status, "local_newer")
            self.assertEqual(status.local_stats, {"local": 1})
        finally:
            db.close()

    def test_launch_adjacent_mutations_stay_inside_library_service(self):
        db = GameDatabase(":memory:")
        try:
            service = LibraryService(db, LibraryStateStore())
            game_id = service.upsert_game(
                "Launch Service Game",
                "/games/service",
                "run.sh",
                "linux",
                steam_id="480",
            )
            self.assertIsNotNone(game_id)
            service.update_runtime_settings(game_id, proton_path="/umu", mode="umu")
            service.set_build_reference(game_id, "build-1", 123)
            service.set_artwork_identity(game_id, icon_url="/tmp/icon.png")
            service.set_tags(game_id, "strategy, sandbox")
            self.assertTrue(service.toggle_favorite(game_id))
            self.assertFalse(service.toggle_favorite(game_id))
            total = service.record_playtime_finished(game_id, timestamp=456)
            session_id = db.create_playtime_session(game_id, started_at=400)
            self.assertEqual(
                service.checkpoint_playtime_session(session_id, 12, finalized=True, ended_at=412),
                game_id,
            )
            row = db.get_all_games()[0]
            self.assertEqual(row[4], "umu")
            self.assertEqual(row[6], "480")
            self.assertEqual(row[11], "build-1")
            self.assertEqual(row[18], "/tmp/icon.png")
            self.assertEqual(row[10], "strategy, sandbox")
            self.assertEqual(total, 0)
            self.assertTrue(service.remove_game(game_id))
        finally:
            db.close()

    def test_verified_metadata_name_update_stays_inside_library_service(self):
        db = GameDatabase(":memory:")
        try:
            game_id = db.add_game("Steam App 480", "", "", "steam", "", "480")
            service = LibraryService(db, LibraryStateStore())
            self.assertFalse(service.apply_metadata_name(game_id, ""))
            self.assertTrue(service.apply_metadata_name(game_id, "Spacewar"))
            self.assertEqual(db.get_all_games()[0][1], "Spacewar")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
