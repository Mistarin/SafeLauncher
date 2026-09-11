"""Focused regression tests for public-profile data boundaries."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from unittest.mock import patch
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
    normalize_public_document, normalize_social_snapshot,
    save_profile_settings,
)
from database import GameDatabase
from core.profile_service import ProfileServiceClient, ProfileServiceError
from core.central_auth import (
    CentralAuthConfig,
    CentralAuthSession,
    OFFICIAL_AUTH0_AUDIENCE,
    OFFICIAL_AUTH0_CLIENT_ID,
    OFFICIAL_AUTH0_ISSUER,
    get_central_auth_config,
)
from ui.components.profile_page import ProfilePageWidget


class ProfileModelTests(unittest.TestCase):
    def test_official_central_auth_defaults_are_configured(self):
        with patch.dict("os.environ", {
            "SAFELAUNCHER_AUTH0_ISSUER": "",
            "SAFELAUNCHER_AUTH0_CLIENT_ID": "",
            "SAFELAUNCHER_AUTH0_AUDIENCE": "",
        }, clear=False):
            config = get_central_auth_config()
        self.assertEqual(config.issuer, OFFICIAL_AUTH0_ISSUER)
        self.assertEqual(config.client_id, OFFICIAL_AUTH0_CLIENT_ID)
        self.assertEqual(config.audience, OFFICIAL_AUTH0_AUDIENCE)
        self.assertTrue(config.configured)

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

    def test_social_snapshot_accepts_only_bounded_public_summaries(self):
        snapshot = normalize_social_snapshot({
            "friends": [{
                "handle": "01234567890123456789",
                "display_name": "  Friend   One ",
                "updated_at": "not-a-timestamp",
                "path": "/private/path",
            }],
            "incoming_requests": [{
                "request_id": "request-1",
                "handle": "abcdefabcdefabcdefabcd",
                "display_name": "Incoming",
                "created_at": 12,
            }],
            "outgoing_requests": [],
            "blocked_handles": ["short", "FEDCFEDCFEDCFEDCFEDC"],
        })
        self.assertEqual(snapshot["friends"][0]["display_name"], "Friend One")
        self.assertEqual(snapshot["friends"][0]["updated_at"], 0)
        self.assertEqual(snapshot["incoming_requests"][0]["request_id"], "request-1")
        self.assertEqual(snapshot["blocked_handles"], ["fedcfedcfedcfedcfedc"])

    def test_social_client_uses_owner_authentication_and_routes(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "friends": [],
            "incoming_requests": [],
            "outgoing_requests": [],
            "blocked_handles": [],
        }
        session = Mock()
        session.request.return_value = response
        owner = "o" * 43
        handle = "01234567890123456789"
        target = "abcdefabcdefabcdefabcd"
        client = ProfileServiceClient("https://profiles.example", owner)
        client.session = session

        self.assertEqual(client.get_social(handle)["friends"], [])
        self.assertEqual(session.request.call_args.args[:2], ("GET", "https://profiles.example/api/profile/v1/01234567890123456789/friends"))
        self.assertEqual(session.request.call_args.kwargs["headers"]["Authorization"], "Bearer " + owner)

        client.send_friend_request(handle, target)
        self.assertEqual(session.request.call_args.args[:2], ("POST", "https://profiles.example/api/profile/v1/01234567890123456789/friend-requests"))
        self.assertEqual(session.request.call_args.kwargs["json"], {"targetHandle": target})

        client.remove_friend(handle, target)
        self.assertEqual(session.request.call_args.args[:2], ("DELETE", "https://profiles.example/api/profile/v1/01234567890123456789/friends/abcdefabcdefabcdefabcd"))

    def test_v2_client_uses_central_bearer_and_never_sends_legacy_token(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "profile": {
                "schema_version": 1,
                "handle": "01234567890123456789",
                "display_name": "Central Player",
                "avatar": None,
                "background": DEFAULT_BACKGROUND,
                "stats": {},
                "favorite_games": [],
                "recent_achievements": [],
            },
            "revision": 3,
        }
        http = Mock()
        http.request.return_value = response
        auth = Mock()
        auth.authorization_header.return_value = "Bearer central-access-token"
        client = ProfileServiceClient("https://profiles.example", "l" * 43, auth_session=auth)
        client.session = http

        profile = client.current_profile()

        self.assertEqual(profile["handle"], "01234567890123456789")
        request = http.request.call_args
        self.assertEqual(request.args[:2], ("GET", "https://profiles.example/api/profile/v2/me"))
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer central-access-token")
        auth.authorization_header.assert_called_once_with()

    def test_client_rejects_mixing_legacy_routes_with_central_auth(self):
        auth = Mock()
        client = ProfileServiceClient("https://profiles.example", "l" * 43, auth_session=auth)
        with self.assertRaisesRegex(ProfileServiceError, "Legacy owner-token"):
            client.delete("01234567890123456789")

    def test_v2_client_refreshes_once_after_expired_access_token(self):
        unauthorized = Mock(status_code=401)
        unauthorized.json.return_value = {"code": "unauthorized", "error": "expired"}
        accepted = Mock(status_code=200)
        accepted.json.return_value = {"profile": None}
        http = Mock()
        http.request.side_effect = [unauthorized, accepted]
        auth = Mock()
        auth.authorization_header.side_effect = ["Bearer expired", "Bearer refreshed"]
        client = ProfileServiceClient("https://profiles.example", auth_session=auth)
        client.session = http

        self.assertIsNone(client.current_profile())
        self.assertEqual(http.request.call_count, 2)
        self.assertEqual(http.request.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer refreshed")
        auth.authorization_header.assert_any_call(force_refresh=True)

    def test_device_login_persists_refresh_token_and_requests_expected_audience(self):
        device = Mock(status_code=200)
        device.json.return_value = {
            "device_code": "device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://login.example/activate",
            "expires_in": 600,
            "interval": 5,
        }
        token = Mock(status_code=200)
        token.json.return_value = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
        }
        http = Mock()
        http.post.side_effect = [device, token]
        session = CentralAuthSession(
            CentralAuthConfig("https://login.example", "client-id", "https://profiles.example"),
            session=http,
        )
        with patch("core.central_auth.set_secret", return_value=True) as save_secret, \
             patch("core.central_auth.webbrowser.open") as open_browser, \
             patch("core.central_auth.time.sleep"), \
             patch("core.central_auth.time.monotonic", side_effect=[0, 0, 0]):
            self.assertEqual(session.device_login(open_browser=True), "access-token")

        self.assertEqual(save_secret.call_args.args, ("central_profile_refresh_token", "refresh-token"))
        open_browser.assert_called_once_with("https://login.example/activate", new=2)
        self.assertEqual(http.post.call_args_list[0].kwargs["data"]["audience"], "https://profiles.example")


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
                self.assertFalse(page.friends_section.isHidden())
                page.show_owner()
                self.assertEqual(page._mode, "owner")
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
