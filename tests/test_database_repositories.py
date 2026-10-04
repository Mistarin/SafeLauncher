from concurrent.futures import ThreadPoolExecutor
import sqlite3
import tempfile
from pathlib import Path
from threading import Event, Thread
import unittest
from unittest.mock import patch

from database import GameDatabase, GameRecord, DatabaseRecoveryError
from core.local_database.connection import (
    DatabaseSession, _BACKUP_CREATED, _create_database_backup,
)
from core.local_database.reconciliation import _DUPLICATE_REPAIR_DONE


class DatabaseRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.db = GameDatabase(':memory:')
        self.addCleanup(self.db.close)

    def test_one_connection_for_all_domains(self):
        for repository in (self.db._games, self.db._profiles, self.db._playtime,
                           self.db._achievements, self.db._reconciliation, self.db._schema):
            self.assertIs(repository.conn, self.db.conn)
            self.assertIs(repository.session, self.db._session)

    def test_cross_repository_projection_and_record_compatibility(self):
        game_id = self.db.add_game('Test', '/missing', 'game.exe', 'umu', steam_id='480')
        self.db.merge_profile_game({'identity_key': 'steam:480', 'app_id': '480',
                                   'name': 'Test', 'favorite': True})
        self.db.project_profile_game(game_id, self.db.get_profile_games()[0])
        record = self.db.get_all_games()[0]
        self.assertIsInstance(record, GameRecord)
        self.assertEqual(len(record), 21)
        self.assertEqual(tuple(record)[0], game_id)
        self.assertTrue(record[8])
        self.db.save_achievement_schema(game_id, '480', [{'api_name': 'WIN'}])
        self.assertTrue(self.db.unlock_achievement(game_id, 'WIN'))
        self.assertEqual(self.db.get_achievement_stats(game_id)[:2], (1, 1))
        self.assertIn('WIN', self.db.get_profile_unlocks()['480'])

    def test_concurrent_notification_claim_exactly_once(self):
        game_id = self.db.add_game('Test', '/missing', '', 'umu', steam_id='480')
        self.db.save_achievement_schema(game_id, '480', [{'api_name': 'WIN'}])
        self.db.unlock_achievement(game_id, 'WIN')
        with ThreadPoolExecutor(max_workers=8) as workers:
            claims = list(workers.map(lambda _: self.db.claim_achievement_notification(game_id, 'WIN'), range(40)))
        self.assertEqual(sum(claims), 1)

    def test_facade_serializes_whole_repository_call(self):
        entered, release, second = Event(), Event(), Event()
        def first():
            entered.set()
            self.assertTrue(release.wait(2))
        with patch.object(self.db._games, 'get_all_games', side_effect=first), \
             patch.object(self.db._profiles, 'get_profile_games', side_effect=second.set):
            a = Thread(target=self.db.get_all_games)
            b = Thread(target=self.db.get_profile_games)
            a.start(); self.assertTrue(entered.wait(2)); b.start()
            self.assertFalse(second.wait(.03))
            release.set(); a.join(2); b.join(2)
            self.assertTrue(second.is_set())

    def test_initialization_failure_closes_connection(self):
        closed = []
        original = DatabaseSession.close
        def close(session):
            original(session)
            closed.append(session.conn)
        with patch.object(GameDatabase, '_create_table', side_effect=RuntimeError('migration')), \
             patch.object(DatabaseSession, 'close', close):
            with self.assertRaises(RuntimeError):
                GameDatabase(':memory:')
        self.assertEqual(closed, [None])

    def test_schema_failure_is_not_reported_as_empty_library(self):
        from core.local_database.schema import SchemaMigrator
        fake = type('Session', (), {'db_path': ':memory:', 'conn': None})()
        with self.assertRaises(DatabaseRecoveryError):
            SchemaMigrator(fake).apply()

    def test_legacy_schema_migrates_and_reopens(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'library.db')
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT, path TEXT, executable TEXT, mode TEXT)')
            conn.execute("INSERT INTO games VALUES(1, 'Old', '/missing', '', 'umu')")
            conn.commit(); conn.close()
            db = GameDatabase(path)
            self.assertEqual(db.get_all_games()[0].name, 'Old')
            db.close(); db.close()
            reopened = GameDatabase(path)
            self.addCleanup(reopened.close)
            self.assertEqual(len(reopened.get_all_games()[0]), 21)

    def test_worker_open_uses_read_only_schema_check_during_write_transaction(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'library.db')
            first = GameDatabase(path)
            try:
                game_id = first.add_game('Committed', '/missing', '', 'linux')
                with sqlite3.connect(path) as writer:
                    writer.execute('BEGIN IMMEDIATE')
                    writer.execute("UPDATE games SET name = 'Uncommitted' WHERE id = ?", (game_id,))
                    worker = GameDatabase(path)
                    try:
                        self.assertEqual(worker.get_all_games()[0].name, 'Committed')
                        self.assertFalse(worker.conn.in_transaction)
                    finally:
                        worker.close()
            finally:
                first.close()

    def test_schema_fast_path_detects_database_recreated_at_same_path(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'library.db'
            first = GameDatabase(str(path))
            first.close()
            path.unlink()
            # A legacy replacement at the cached path still needs migration.
            with sqlite3.connect(path) as legacy:
                legacy.execute('CREATE TABLE games(id INTEGER PRIMARY KEY, name TEXT, path TEXT, executable TEXT, mode TEXT)')
                legacy.execute("INSERT INTO games VALUES(1, 'Replacement', '/missing', '', 'linux')")
            reopened = GameDatabase(str(path))
            try:
                self.assertEqual(reopened.get_all_games()[0].name, 'Replacement')
                self.assertEqual(len(reopened.get_all_games()[0]), 21)
                self.assertEqual(reopened.get_profile_games(), [])
            finally:
                reopened.close()

    def test_schema_fast_path_checks_objects_even_with_current_version(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'library.db')
            first = GameDatabase(path)
            first.close()
            with sqlite3.connect(path) as changed:
                changed.execute('DROP TABLE playtime_sessions')
                changed.execute('DROP INDEX idx_achievements_game_unlocked')
            reopened = GameDatabase(path)
            try:
                game_id = reopened.add_game('Test', '/missing', '', 'linux')
                session_id = reopened.create_playtime_session(game_id, 100)
                self.assertEqual(reopened.get_playtime_session_game_id(session_id), game_id)
                self.assertIsNotNone(reopened.conn.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'idx_achievements_game_unlocked'"
                ).fetchone())
            finally:
                reopened.close()

    def test_schema_fast_path_requires_completed_migration_version(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'library.db')
            first = GameDatabase(path)
            first.close()
            with sqlite3.connect(path) as legacy:
                legacy.execute('PRAGMA user_version = 0')
                legacy.execute("INSERT INTO achievement_profile(app_id, api_name, first_seen_at, provenance) VALUES('480', 'WIN', 100, 'unknown')")
            reopened = GameDatabase(path)
            try:
                record = reopened.get_profile_unlock_records()['480']['WIN']
                self.assertEqual(record['provenance'], 'local_emulator')
                self.assertGreater(reopened.conn.execute('PRAGMA user_version').fetchone()[0], 0)
            finally:
                reopened.close()

    def test_cached_path_does_not_hide_incompatible_schema_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'library.db')
            first = GameDatabase(path)
            first.close()
            with sqlite3.connect(path) as broken:
                broken.execute('DROP TABLE games')
                broken.execute('CREATE TABLE games(id INTEGER PRIMARY KEY)')
            with self.assertRaises(DatabaseRecoveryError):
                GameDatabase(path)
            with sqlite3.connect(path) as preserved:
                self.assertEqual([row[1] for row in preserved.execute('PRAGMA table_info(games)')][:1], ['id'])
                self.assertNotIn('name', [row[1] for row in preserved.execute('PRAGMA table_info(games)')])

    def test_alias_only_repair_commits_deletion_to_other_connections(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'library.db')
            db = GameDatabase(path)
            try:
                db.merge_profile_game({'identity_key': 'local:local-test', 'name': 'Test',
                                       'favorite': True, 'playtime_baseline_seconds': 120})
                self.assertEqual(db.consolidate_duplicate_games(force=True), 0)
                self.assertFalse(db.conn.in_transaction)
                with sqlite3.connect(path) as observer:
                    self.assertEqual(observer.execute(
                        'SELECT identity_key, favorite, playtime_baseline_seconds FROM profile_games'
                    ).fetchall(), [('local:test', 1, 120)])
            finally:
                db.close()

    def test_alias_only_startup_commits_before_creating_recovery_backup(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'library.db')
            seed = GameDatabase(path)
            seed.merge_profile_game({'identity_key': 'local:local-test', 'name': 'Test'})
            seed.close()
            _DUPLICATE_REPAIR_DONE.discard(path)
            _BACKUP_CREATED.discard(path)

            def backup_after_commit(db_path, conn):
                # Fail before entering backup on a regression, instead of hanging.
                self.assertFalse(conn.in_transaction)
                _create_database_backup(db_path, conn)

            with patch('database._create_database_backup', side_effect=backup_after_commit) as backup:
                reopened = GameDatabase(path)
                reopened.close()
                backup.assert_called_once()
            with sqlite3.connect(path + '.bak') as recovery:
                self.assertEqual(recovery.execute(
                    'SELECT identity_key FROM profile_games'
                ).fetchall(), [('local:test',)])

    def test_backup_defers_open_transaction_without_committing_and_can_retry(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'library.db')
            db = GameDatabase(path)
            try:
                _BACKUP_CREATED.discard(path)
                db.conn.execute("INSERT INTO collections(name) VALUES('Pending')")
                _create_database_backup(path, db.conn)
                self.assertTrue(db.conn.in_transaction)
                self.assertNotIn(path, _BACKUP_CREATED)
                with sqlite3.connect(path + '.bak') as recovery:
                    self.assertEqual(recovery.execute('SELECT name FROM collections').fetchall(), [])
                db.conn.commit()
                _create_database_backup(path, db.conn)
                self.assertIn(path, _BACKUP_CREATED)
                with sqlite3.connect(path + '.bak') as recovery:
                    self.assertEqual(recovery.execute('SELECT name FROM collections').fetchall(), [('Pending',)])
            finally:
                db.close()

    def test_duplicate_repair_transfers_sessions_and_keeps_checkpoints_idempotent(self):
        canonical = self.db.add_game('Test', '/installed', 'run', 'linux', steam_id='480')
        duplicate = self.db.add_game('Test', '', '', 'linux', steam_id='480')
        self.db.archive_game(duplicate)
        self.db.create_playtime_session(canonical, 100, 'canonical-session')
        self.db.checkpoint_playtime_session('canonical-session', 60, True, 160)
        self.db.create_playtime_session(duplicate, 200, 'duplicate-session')
        self.db.checkpoint_playtime_session('duplicate-session', 120, True, 320)
        before = self.db.get_playtime_sessions(duplicate)[0]

        self.assertEqual(self.db.consolidate_duplicate_games(force=True), 1)
        self.assertEqual(self.db.get_playtime_session_game_id('duplicate-session'), canonical)
        self.assertEqual(self.db.get_playtime_sessions(canonical), [
            {'session_id': 'canonical-session', 'started_at': 100, 'ended_at': 160,
             'duration_seconds': 60, 'finalized': True}, before,
        ])
        self.assertEqual(self.db.get_playtime_sessions(duplicate), [])
        self.assertEqual(self.db.get_playtime(canonical), 180)
        self.db.checkpoint_playtime_session('duplicate-session', 180, True, 380)
        self.db.checkpoint_playtime_session('duplicate-session', 180, True, 380)
        self.assertEqual(self.db.get_playtime(canonical), 240)
        self.assertEqual(self.db.consolidate_duplicate_games(force=True), 0)
        self.assertFalse(self.db.conn.in_transaction)
