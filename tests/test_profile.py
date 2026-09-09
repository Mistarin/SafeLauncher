"""Focused regression tests for public-profile data boundaries."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from core.profile_assets import MAX_AVATAR_BYTES, normalize_avatar, validate_avatar_payload
from core.profile_models import (
    DEFAULT_BACKGROUND,
    build_public_projection,
    load_profile_settings,
    normalize_public_document,
    save_profile_settings,
)
from database import GameDatabase
from core.profile_service import ProfileServiceClient
from ui.components.profile_page import ProfilePageWidget


class ProfileModelTests(unittest.TestCase):
    def test_avatar_is_normalized_without_source_metadata(self):
        source = io.BytesIO()
        Image.new("RGBA", (1200, 600), (240, 40, 70, 128)).save(source, format="PNG")
        avatar = normalize_avatar(source.getvalue())
        self.assertEqual(avatar["mime"], "image/jpeg")
        self.assertEqual(avatar["width"], avatar["height"])
        self.assertLessEqual(avatar["bytes"], MAX_AVATAR_BYTES)
        self.assertTrue(validate_avatar_payload(avatar))
        self.assertNotIn("path", avatar)

    def test_settings_round_trip_is_bounded_and_json_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            saved = save_profile_settings(settings, {
                "display_name": "  Martin   Player ",
                "background": {"kind": "gradient", "stops": ["#101010", "#AABBCC"], "angle": 999},
            })
            loaded = load_profile_settings(settings)
            self.assertEqual(loaded["display_name"], "Martin Player")
            self.assertEqual(loaded["background"]["angle"], 360)
            self.assertEqual(saved["avatar"], loaded["avatar"])

    def test_projection_excludes_installation_details(self):
        db = GameDatabase(":memory:")
        try:
            game_id = db.add_game("Public Game", "/private/install/path", "game.exe", "umu", steam_id="12345")
            db.toggle_favorite(game_id)
            db.add_playtime(game_id, 3600)
            document = build_public_projection(db, {
                "display_name": "Player",
                "public_handle": "01234567890123456789",
                "background": DEFAULT_BACKGROUND,
            })
            encoded = json.dumps(document)
            self.assertIn("Public Game", encoded)
            self.assertNotIn("private/install/path", encoded)
            self.assertNotIn("game.exe", encoded)
            self.assertNotIn("owner_token", encoded)
            self.assertEqual(document["stats"]["playtime_seconds"], 3600)
        finally:
            db.close()

    def test_remote_document_rejects_bad_handle_and_keeps_only_public_shape(self):
        bad = {"schema_version": 1, "handle": "short", "display_name": "x"}
        self.assertIsNone(normalize_public_document(bad))
        valid = {
            "schema_version": 1,
            "handle": "01234567890123456789",
            "display_name": "Visible",
            "background": {"kind": "solid", "color": "#123456"},
            "stats": {"games_count": 1},
            "favorite_games": [{"name": "Game", "app_id": "123"}],
            "recent_achievements": [],
        }
        normalized = normalize_public_document(valid)
        self.assertEqual(normalized["display_name"], "Visible")
        self.assertEqual(normalized["stats"]["games_count"], 1)
        self.assertNotIn("path", normalized)

    def test_owner_token_rotation_sends_new_token_without_returning_it(self):
        response = Mock(status_code=200)
        response.json.return_value = {"rotated": True}
        session = Mock()
        session.request.return_value = response
        client = ProfileServiceClient("https://profiles.example", "o" * 43)
        client.session = session
        result = client.rotate_token("01234567890123456789", "n" * 43)
        self.assertEqual(result, {"rotated": True})
        request = session.request.call_args.kwargs
        self.assertEqual(request["json"], {"ownerToken": "n" * 43})
        self.assertEqual(request["headers"]["Authorization"], "Bearer " + "o" * 43)


class ProfilePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_owner_and_public_views_share_one_persistent_page(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            db = GameDatabase(":memory:")
            page = ProfilePageWidget(db, settings)
            try:
                self.assertEqual(page._mode, "owner")
                public = build_public_projection(db, {
                    "display_name": "Public Player",
                    "public_handle": "01234567890123456789",
                    "background": DEFAULT_BACKGROUND,
                })
                self.assertTrue(page.show_public(public))
                self.assertEqual(page._mode, "public")
                self.assertFalse(page.editor.isVisible())
                page.show_owner()
                self.assertEqual(page._mode, "owner")
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
