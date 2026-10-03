import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.ludusavi_detector import SaveLocation
from core.save_restore_service import create_restore_plan, restore_archive_with_safety_backup
from core.zip_backup import ZipBackupManager


class SaveRestoreSafetyTests(unittest.TestCase):
    def _location(self, path):
        return SaveLocation("Saves", path, True)

    def test_restore_creates_backup_before_replacing_local_files(self):
        with tempfile.TemporaryDirectory() as root:
            game = os.path.join(root, "game")
            os.makedirs(game)
            current = game
            with open(os.path.join(game, "slot.dat"), "w") as handle:
                handle.write("before")

            source = os.path.join(root, "source")
            os.makedirs(source)
            with open(os.path.join(source, "slot.dat"), "w") as handle:
                handle.write("after")
            archive = os.path.join(root, "cloud.zip")
            self.assertTrue(ZipBackupManager().export_save_locations(
                [self._location(source)], archive, game_name="Example", game_path=game
            ))

            backup = os.path.join(root, "before.zip")
            result = restore_archive_with_safety_backup(
                archive,
                game,
                game_name="Example",
                game_path=game,
                current_locations=[self._location(current)],
                backup_zip_path=backup,
            )

            self.assertTrue(result.success)
            self.assertTrue(os.path.isfile(backup))
            with open(os.path.join(game, "slot.dat")) as handle:
                self.assertEqual(handle.read(), "after")

    def test_backup_failure_does_not_replace_local_files(self):
        with tempfile.TemporaryDirectory() as root:
            game = os.path.join(root, "game")
            os.makedirs(game)
            current = game
            current_file = os.path.join(current, "slot.dat")
            with open(current_file, "w") as handle:
                handle.write("before")
            archive = os.path.join(root, "archive.zip")
            source = os.path.join(root, "source")
            os.makedirs(source)
            with open(os.path.join(source, "slot.dat"), "w") as handle:
                handle.write("after")
            self.assertTrue(ZipBackupManager().export_save_locations(
                [self._location(source)], archive, game_name="Example", game_path=game
            ))

            with patch.object(ZipBackupManager, "export_save_locations", return_value=False):
                result = restore_archive_with_safety_backup(
                    archive,
                    game,
                    game_name="Example",
                    game_path=game,
                    current_locations=[self._location(current)],
                    backup_zip_path=os.path.join(root, "before.zip"),
                )

            self.assertFalse(result.success)
            with open(current_file) as handle:
                self.assertEqual(handle.read(), "before")

    def test_import_rejects_duplicate_destination_members(self):
        with tempfile.TemporaryDirectory() as root:
            archive = os.path.join(root, "duplicate.zip")
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("slot.dat", b"one")
                output.writestr("./slot.dat", b"two")
            destination = os.path.join(root, "destination")
            self.assertFalse(ZipBackupManager().import_save(archive, destination))

    def test_cancelled_restore_rolls_back_files_already_committed(self):
        with tempfile.TemporaryDirectory() as root:
            destination = os.path.join(root, "saves")
            os.makedirs(destination)
            for name, contents in (("one.dat", "old-one"), ("two.dat", "old-two")):
                with open(os.path.join(destination, name), "w", encoding="utf-8") as handle:
                    handle.write(contents)
            archive = os.path.join(root, "cloud.zip")
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("one.dat", b"new-one")
                output.writestr("two.dat", b"new-two")

            checks = 0

            def cancel_during_commit():
                nonlocal checks
                checks += 1
                # Initial check, two staging checks, and the first commit
                # check pass; cancel before the second commit.
                return checks >= 5

            self.assertFalse(ZipBackupManager().import_save(
                archive, destination, cancel_check=cancel_during_commit
            ))
            for name, expected in (("one.dat", "old-one"), ("two.dat", "old-two")):
                with open(os.path.join(destination, name), encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), expected)

    def test_failed_rollback_preserves_displaced_save_for_manual_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            destination = os.path.join(root, "saves")
            os.makedirs(destination)
            for name, contents in (("one.dat", "old-one"), ("two.dat", "old-two")):
                with open(os.path.join(destination, name), "w", encoding="utf-8") as handle:
                    handle.write(contents)
            archive = os.path.join(root, "cloud.zip")
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("one.dat", b"new-one")
                output.writestr("two.dat", b"new-two")

            real_move = shutil.move
            calls = 0

            def fail_commit_and_one_rollback(source, target, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls in (4, 5):
                    raise OSError("simulated filesystem failure")
                return real_move(source, target, *args, **kwargs)

            with patch("core.zip_backup.shutil.move", side_effect=fail_commit_and_one_rollback):
                self.assertFalse(ZipBackupManager().import_save(archive, destination))

            recovery_dirs = [
                path for path in os.listdir(root)
                if path.startswith(".safelauncher-import-")
            ]
            self.assertTrue(recovery_dirs, "rollback source should not be deleted after a failed rollback")
            recovery_files = []
            for recovery_dir in recovery_dirs:
                for current, _dirs, files in os.walk(os.path.join(root, recovery_dir)):
                    recovery_files.extend(os.path.join(current, name) for name in files)
            self.assertTrue(recovery_files)
            self.assertTrue(any(
                Path(path).read_text(encoding="utf-8") == "old-two"
                for path in recovery_files
            ))

    def test_manifest_restore_accepts_identical_overlapping_locations(self):
        with tempfile.TemporaryDirectory() as root:
            game = os.path.join(root, "game")
            source = os.path.join(game, "prefix", "userdata")
            os.makedirs(source)
            with open(os.path.join(source, "achievements.ini"), "w") as handle:
                handle.write("[achievements]\n")
            archive = os.path.join(root, "overlapping.zip")
            locations = [
                self._location(source),
                SaveLocation(
                    "Achievement state",
                    os.path.join(source, "achievements.ini"),
                    False,
                ),
            ]
            manager = ZipBackupManager()
            self.assertTrue(manager.export_save_locations(
                locations, archive, game_name="Example", game_path=game
            ))
            self.assertTrue(manager.validate_archive(
                archive,
                os.path.join(game, "prefix"),
                game_path=game,
            ))

    def test_restore_plan_rejects_changed_archive_before_backup(self):
        with tempfile.TemporaryDirectory() as root:
            game = os.path.join(root, "game")
            source = os.path.join(root, "source")
            os.makedirs(game)
            os.makedirs(source)
            with open(os.path.join(game, "slot.dat"), "w") as handle:
                handle.write("before")
            with open(os.path.join(source, "slot.dat"), "w") as handle:
                handle.write("after")
            archive = os.path.join(root, "cloud.zip")
            location = self._location(source)
            self.assertTrue(ZipBackupManager().export_save_locations([location], archive, game_name="Example", game_path=game))
            plan = create_restore_plan(
                archive,
                game,
                game_name="Example",
                game_path=game,
                current_locations=[self._location(game)],
            )
            stat = os.stat(archive)
            os.utime(archive, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            result = restore_archive_with_safety_backup(
                archive,
                game,
                game_name="Example",
                game_path=game,
                current_locations=[self._location(game)],
                backup_zip_path=os.path.join(root, "backup.zip"),
                plan=plan,
            )
            self.assertFalse(result.success)
            self.assertEqual(result.category, "stale_selection")
            with open(os.path.join(game, "slot.dat")) as handle:
                self.assertEqual(handle.read(), "before")


if __name__ == "__main__":
    unittest.main()
