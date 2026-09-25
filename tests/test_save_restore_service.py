import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from core.ludusavi_detector import SaveLocation
from core.save_restore_service import restore_archive_with_safety_backup
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


if __name__ == "__main__":
    unittest.main()
