"""Steam AppID normalization and installed-manifest discovery tests."""

from __future__ import annotations

import os
import tempfile
import unittest

from core.steam_build_tracker import read_local_steam_build
from core.steam_ids import normalize_steam_app_id


class SteamIdTests(unittest.TestCase):
    def test_normalize_accepts_decimal_ids_and_rejects_placeholders(self):
        self.assertEqual(normalize_steam_app_id(" 000480 "), "480")
        self.assertEqual(normalize_steam_app_id(730), "730")
        for value in ("", "0", None, "None", "Not set", "abc", "480.0", "-480"):
            self.assertEqual(normalize_steam_app_id(value), "")

    def test_manifest_can_be_found_in_standard_steam_library_root(self):
        with tempfile.TemporaryDirectory() as root:
            game_path = os.path.join(root, "steamapps", "common", "Example")
            os.makedirs(game_path)
            manifest_dir = os.path.join(root, "steamapps")
            manifest = os.path.join(manifest_dir, "appmanifest_480.acf")
            with open(manifest, "w", encoding="utf-8") as handle:
                handle.write('"AppState"\n{\n\t"buildid"\t"123456"\n\t"LastUpdated"\t"1700000000"\n}\n')

            self.assertEqual(
                read_local_steam_build(game_path, "480"),
                ("123456", 1700000000),
            )


if __name__ == "__main__":
    unittest.main()
