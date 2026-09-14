"""Regression coverage for portable game identity and title repair."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.cloud_metadata_sync import CloudMetadataSync, _merge_profiles
from core.game_names import (
    fallback_game_name,
    is_placeholder_game_name,
    local_profile_identity,
    meaningful_game_name,
)
from core.request_contracts import ResourceStatus
from core.request_manager import RequestManager
from core.resource_cache import ResourceCache
from core.steam_resource_service import SteamResourceService
from database import GameDatabase


class _SteamClient:
    def __init__(self):
        self.calls = []

    def app_details(self, app_id):
        self.calls.append(str(app_id))
        return {"steam_appid": int(app_id), "name": "A Real Steam Title"}


class GameNameResolutionTests(unittest.TestCase):
    def test_placeholder_rules_are_bounded_and_case_insensitive(self):
        self.assertTrue(is_placeholder_game_name("Steam App 480", "480"))
        self.assertTrue(is_placeholder_game_name(" steam   app   480 ", "480"))
        self.assertFalse(is_placeholder_game_name("Steam App 480 Deluxe", "480"))
        self.assertEqual(meaningful_game_name("Steam App 480", "480"), "")
        self.assertEqual(fallback_game_name("480"), "Steam App 480")

    def test_profile_merge_does_not_let_placeholder_overwrite_title(self):
        merged = _merge_profiles(
            {"games": {"steam:480": {"app_id": "480", "name": "Steam App 480"}}},
            {"games": {"steam:480": {"app_id": "480", "name": "Portal"}}},
        )
        self.assertEqual(merged["games"]["steam:480"]["name"], "Portal")

        preserved = _merge_profiles(
            {"games": {"steam:480": {"app_id": "480", "name": "My Local Title"}}},
            {"games": {"steam:480": {"app_id": "480", "name": "Remote Title"}}},
        )
        self.assertEqual(preserved["games"]["steam:480"]["name"], "My Local Title")

    def test_placeholder_materialization_is_repaired_in_place(self):
        db = GameDatabase(":memory:")
        try:
            CloudMetadataSync._apply_profile(db, _merge_profiles({}, {
                "games": {"steam:480": {"app_id": "480", "name": ""}},
                "achievements": {},
            }))
            first = db.get_all_games()[0]
            self.assertEqual(first.name, "Steam App 480")

            CloudMetadataSync._apply_profile(db, _merge_profiles({}, {
                "games": {"steam:480": {"app_id": "480", "name": "Portal"}},
                "achievements": {},
            }))
            games = db.get_all_games()
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0].id, first.id)
            self.assertEqual(games[0].name, "Portal")
        finally:
            db.close()

    def test_local_identity_normalization_is_idempotent(self):
        self.assertEqual(local_profile_identity("Dub Together"), "local:dub-together")
        self.assertEqual(local_profile_identity("local:dub-together"), "local:dub-together")
        self.assertEqual(local_profile_identity("local:local-dub-together"), "local:dub-together")
        self.assertEqual(fallback_game_name("", "local:local-dub-together"), "Dub Together")
        self.assertEqual(GameDatabase.profile_identity("local:dub-together", ""), "local:dub-together")

    def test_repeated_local_profile_materialization_keeps_one_archive_row(self):
        db = GameDatabase(":memory:")
        try:
            profile = {
                "games": {
                    "local:dub-together": {
                        "app_id": "",
                        "name": "",
                        "display_name": "",
                    }
                },
                "achievements": {},
            }
            for _ in range(5):
                CloudMetadataSync._apply_profile(db, _merge_profiles({}, profile))
            games = db.get_all_games()
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0].name, "Dub Together")
            self.assertTrue(games[0].is_archived)
        finally:
            db.close()

    def test_duplicate_local_rows_are_consolidated_without_losing_active_row(self):
        db = GameDatabase(":memory:")
        try:
            archived_id = db.add_game("local:dub-together", "", "", "linux")
            active_id = db.add_game("Dub Together", "/games/dub-together", "run.sh", "linux")
            db.archive_game(archived_id, True)
            db.archive_game(active_id, False)
            removed = db.consolidate_duplicate_games(force=True)
            self.assertEqual(removed, 1)
            games = db.get_all_games()
            self.assertEqual(len(games), 1)
            self.assertEqual(games[0].id, active_id)
            self.assertEqual(games[0].name, "Dub Together")
            self.assertEqual(games[0].path, "/games/dub-together")
            self.assertFalse(games[0].is_archived)
        finally:
            db.close()

    def test_profile_game_migration_recovers_steam_title(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "library.db")
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    executable TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    banner_url TEXT,
                    steam_id TEXT
                );
                CREATE TABLE profile_games (
                    identity_key TEXT PRIMARY KEY,
                    app_id TEXT DEFAULT '',
                    favorite INTEGER DEFAULT 0,
                    favorite_changed_at REAL DEFAULT 0,
                    favorite_change_id TEXT DEFAULT '',
                    playtime_baseline_seconds INTEGER DEFAULT 0,
                    last_played INTEGER DEFAULT 0,
                    first_seen_at REAL NOT NULL
                );
                INSERT INTO games(name, path, executable, mode, steam_id)
                VALUES ('Known Title', '', '', 'linux', '480');
                INSERT INTO profile_games(identity_key, app_id, first_seen_at)
                VALUES ('steam:480', '480', 1);
                """
            )
            connection.commit()
            connection.close()

            db = GameDatabase(path)
            try:
                self.assertEqual(db.get_profile_games()[0]["display_name"], "Known Title")
            finally:
                db.close()

    def test_steam_app_details_are_cached_and_deduplicated(self):
        manager = RequestManager(max_workers=1, cache=ResourceCache())
        client = _SteamClient()
        try:
            service = SteamResourceService(manager, client=client)
            first = service.request_app_details("480")
            second = service.request_app_details("480")
            self.assertEqual(first.future.result(timeout=2).status, ResourceStatus.READY)
            self.assertEqual(second.future.result(timeout=2).value["name"], "A Real Steam Title")
            self.assertEqual(client.calls, ["480"])
        finally:
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
