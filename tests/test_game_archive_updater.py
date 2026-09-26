import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from core.game_archive_updater import GameArchiveUpdater, recover_incomplete_game_updates
from core.ludusavi_detector import LudusaviDetector, SaveLocation


class GameArchiveUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name
        self.game_path = os.path.join(self.root, "Example Game")
        os.makedirs(os.path.join(self.game_path, "prefix"), exist_ok=True)
        self._write("old-file.dat", "old")
        self._write("stale-file.dat", "remove me")
        self._write("game.exe", "old executable")
        self._write("prefix/runtime.ini", "keep runtime")
        self._write("saves/profile.sav", "keep save")
        self._write(".sandbox-config", 'EXECUTABLE="game.exe"\n')
        self.archive_path = os.path.join(self.root, "update.zip")
        self.backup_root = os.path.join(self.root, "backups")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, relative, content):
        path = os.path.join(self.game_path, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(content)

    def _archive(self, members):
        with zipfile.ZipFile(self.archive_path, "w") as archive:
            for relative, content in members.items():
                archive.writestr(relative, content)

    def _location(self, relative="saves"):
        path = os.path.join(self.game_path, relative)
        return SaveLocation(
            display_name="Save files",
            path=path,
            is_directory=True,
            file_count=1,
            total_size_bytes=os.path.getsize(os.path.join(path, "profile.sav")),
        )

    def _extract_fixture(self, archive_path, destination, **_kwargs):
        os.makedirs(destination, exist_ok=True)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(destination)
        return True

    def test_update_removes_stale_payload_and_preserves_runtime_and_saves(self):
        self._archive({"game.exe": "new executable", "new-file.dat": "new"})
        os.makedirs(os.path.join(self.game_path, "prefix", "drive_c"), exist_ok=True)
        os.makedirs(os.path.join(self.game_path, "prefix", "dosdevices"), exist_ok=True)
        os.symlink(
            "../drive_c",
            os.path.join(self.game_path, "prefix", "dosdevices", "c:"),
        )
        location = self._location()
        with patch.object(LudusaviDetector, "clear_cache"), patch.object(
            LudusaviDetector, "detect_saves", return_value=[location]
        ), patch("core.game_archive_updater.extract_archive_sandboxed", self._extract_fixture):
            result = GameArchiveUpdater(self.backup_root).update(
                self.archive_path,
                self.game_path,
                game_name="Example Game",
                executable="game.exe",
            )

        self.assertTrue(result.success, result.error)
        self.assertEqual(self._read("game.exe"), "new executable")
        self.assertEqual(self._read("new-file.dat"), "new")
        self.assertFalse(os.path.exists(os.path.join(self.game_path, "old-file.dat")))
        self.assertFalse(os.path.exists(os.path.join(self.game_path, "stale-file.dat")))
        self.assertEqual(self._read("prefix/runtime.ini"), "keep runtime")
        self.assertTrue(os.path.islink(os.path.join(self.game_path, "prefix", "dosdevices", "c:")))
        self.assertEqual(self._read("saves/profile.sav"), "keep save")
        self.assertEqual(self._read(".sandbox-config"), 'EXECUTABLE="game.exe"\n')
        self.assertTrue(os.path.isfile(result.payload["save_backup"]))

    def test_archive_cannot_overwrite_protected_save_path(self):
        self._archive({"game.exe": "new executable", "saves/profile.sav": "bad replacement"})
        location = self._location()
        before = self._tree_snapshot()
        with patch.object(LudusaviDetector, "clear_cache"), patch.object(
            LudusaviDetector, "detect_saves", return_value=[location]
        ), patch("core.game_archive_updater.extract_archive_sandboxed", self._extract_fixture):
            result = GameArchiveUpdater(self.backup_root).update(
                self.archive_path,
                self.game_path,
                game_name="Example Game",
                executable="game.exe",
            )

        self.assertFalse(result.success)
        self.assertEqual(result.category, "protected_path_conflict")
        self.assertEqual(self._tree_snapshot(), before)

    def test_existing_install_without_detectable_saves_is_left_untouched(self):
        self._archive({"game.exe": "new executable"})
        before = self._tree_snapshot()
        with patch.object(LudusaviDetector, "clear_cache"), patch.object(
            LudusaviDetector, "detect_saves", return_value=[]
        ), patch("core.game_archive_updater.extract_archive_sandboxed") as extract:
            result = GameArchiveUpdater(self.backup_root).update(
                self.archive_path,
                self.game_path,
                game_name="Example Game",
                executable="game.exe",
            )

        self.assertFalse(result.success)
        self.assertEqual(result.category, "save_detection_failed")
        extract.assert_not_called()
        self.assertEqual(self._tree_snapshot(), before)

    def test_extraction_failure_leaves_live_installation_unchanged(self):
        self._archive({"game.exe": "new executable"})
        location = self._location()
        before = self._tree_snapshot()
        with patch.object(LudusaviDetector, "clear_cache"), patch.object(
            LudusaviDetector, "detect_saves", return_value=[location]
        ), patch("core.game_archive_updater.extract_archive_sandboxed", return_value=False):
            result = GameArchiveUpdater(self.backup_root).update(
                self.archive_path,
                self.game_path,
                game_name="Example Game",
                executable="game.exe",
            )

        self.assertFalse(result.success)
        self.assertEqual(result.category, "archive_invalid")
        self.assertEqual(self._tree_snapshot(), before)

    def test_unsafe_archive_path_is_rejected_before_extraction(self):
        with zipfile.ZipFile(self.archive_path, "w") as archive:
            archive.writestr("../escape.txt", "must not escape")
        location = self._location()
        before = self._tree_snapshot()
        with patch.object(LudusaviDetector, "clear_cache"), patch.object(
            LudusaviDetector, "detect_saves", return_value=[location]
        ), patch("core.game_archive_updater.extract_archive_sandboxed") as extract:
            result = GameArchiveUpdater(self.backup_root).update(
                self.archive_path,
                self.game_path,
                game_name="Example Game",
                executable="game.exe",
            )

        self.assertFalse(result.success)
        self.assertEqual(result.category, "archive_invalid")
        extract.assert_not_called()
        self.assertEqual(self._tree_snapshot(), before)

    def test_interrupted_swap_recovers_original_installation(self):
        transaction = os.path.join(self.root, ".safelauncher-update-interrupted")
        rollback = os.path.join(transaction, "rollback")
        os.makedirs(rollback)
        with open(os.path.join(rollback, "game.exe"), "w", encoding="utf-8") as stream:
            stream.write("restored")
        with open(os.path.join(transaction, "transaction.json"), "w", encoding="utf-8") as stream:
            json.dump({"game_path": self.game_path, "phase": "swapping"}, stream)
        os.rename(self.game_path, self.game_path + ".missing")

        recovered = recover_incomplete_game_updates(self.root)

        self.assertEqual(recovered, [self.game_path])
        self.assertEqual(self._read("game.exe"), "restored")
        self.assertFalse(os.path.exists(transaction))

    def _read(self, relative):
        with open(os.path.join(self.game_path, relative), encoding="utf-8") as stream:
            return stream.read()

    def _tree_snapshot(self):
        snapshot = {}
        for root, dirs, files in os.walk(self.game_path, followlinks=False):
            for name in files:
                path = os.path.join(root, name)
                relative = os.path.relpath(path, self.game_path)
                with open(path, "rb") as stream:
                    snapshot[relative] = stream.read()
        return snapshot


if __name__ == "__main__":
    unittest.main()
