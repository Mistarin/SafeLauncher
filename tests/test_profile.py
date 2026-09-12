"""Focused regression tests for public-profile data boundaries."""

from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from unittest.mock import Mock

from PyQt6.QtCore import QSettings, QTimer, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QMainWindow

from core.profile_avatar_catalog import (
    normalize_avatar_catalog,
    read_cached_avatar,
    save_cached_avatar,
)
from core.profile_models import (
    BACKGROUND_PRESETS, DEFAULT_BACKGROUND,
    build_public_projection,
    load_profile_settings,
    normalize_public_document, normalize_social_snapshot, normalize_username_handle,
    profile_username_suggestion, steam_hero_url, normalize_background, normalize_avatar_id,
    normalize_avatar_asset_id, normalize_panel_theme_id, normalize_background_preset_id,
    save_profile_settings,
)
from database import GameDatabase
from core.library_controller import LibraryController, LibraryQuery
from core.profile_service import ProfileServiceClient, ProfileServiceError
from core.central_auth import (
    CentralAuthConfig,
    CentralAuthError,
    CentralAuthSession,
    OFFICIAL_AUTH0_AUDIENCE,
    OFFICIAL_AUTH0_CLIENT_ID,
    OFFICIAL_AUTH0_ISSUER,
    get_central_auth_config,
)
from ui.components.profile_page import ProfilePageWidget
from ui.dialogs.profile_avatar_dialog import ProfileAvatarCatalogDialog
from ui.dialogs.friends_dialog import FriendsDialog
from ui.dialogs.game_dialogs import CustomRemoveDialog, EditGameDialog
from ui.dialogs.settings_dialog import UserSettingsDialog
from ui.components.sidebar import HeaderBar
from ui.profile_theme import get_profile_theme, normalize_profile_theme, profile_theme_choices


class ProfileModelTests(unittest.TestCase):
    def test_profile_panel_themes_are_bounded(self):
        self.assertEqual(normalize_profile_theme("sunset"), "sunset")
        self.assertEqual(normalize_profile_theme("unsupported"), "grey")
        self.assertEqual(
            [key for _label, key in profile_theme_choices()],
            ["grey", "aurora", "sunset", "bubble", "glassmorphism"],
        )
        self.assertEqual(normalize_panel_theme_id("sunset"), 3)
        self.assertEqual(normalize_panel_theme_id("glassmorphism"), 5)
        self.assertEqual(normalize_panel_theme_id(999), 1)
        self.assertEqual(normalize_background_preset_id("ember"), 2)
        self.assertFalse(get_profile_theme("grey").is_glass)
        self.assertTrue(get_profile_theme("glassmorphism").is_glass)

    def test_numeric_appearance_references_round_trip(self):
        self.assertEqual(normalize_avatar_asset_id("0042"), 42)
        self.assertIsNone(normalize_avatar_asset_id(0))
        self.assertIsNone(normalize_avatar_asset_id(True))
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            saved = save_profile_settings(settings, {
                "avatar_asset_id": 42,
                "panel_theme_id": 3,
                "background": {"kind": "gradient", "stops": ["#5C2630", "#171417"], "angle": 135},
            })
            loaded = load_profile_settings(settings)
            self.assertEqual(saved["avatar_asset_id"], 42)
            self.assertEqual(loaded["avatar_asset_id"], 42)
            self.assertEqual(loaded["panel_theme_id"], 3)
            db = GameDatabase(":memory:")
            try:
                self.assertEqual(build_public_projection(db, loaded)["panel_theme_id"], 3)
            finally:
                db.close()

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

    def test_avatar_references_are_ids_and_catalog_data_is_bounded(self):
        self.assertEqual(normalize_avatar_id("R 1-1"), "")
        self.assertEqual(normalize_avatar_id("r-1-1"), "r-1-1")
        catalog = normalize_avatar_catalog({"avatars": []})
        self.assertIsNone(catalog)
        catalog = normalize_avatar_catalog([{
            "id": "r-1-1",
            "label": "R 1-1",
            "category": "R",
            "order": 1,
            "sha256": "a" * 64,
            "width": 512,
            "height": 512,
            "bytes": 1024,
        }])
        self.assertEqual(catalog[0]["id"], "r-1-1")

    def test_avatar_cache_is_hash_bound_and_rejects_path_traversal(self):
        data = b"avatar-cache-fixture"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            with patch("core.profile_avatar_catalog.avatar_cache_directory", return_value=Path(directory)):
                save_cached_avatar("r-1-1", digest, data)
                self.assertEqual(read_cached_avatar("r-1-1", digest), data)
                self.assertEqual(read_cached_avatar("r-1-1", "0" * 64), b"")
                self.assertEqual(read_cached_avatar("../outside", digest), b"")

    def test_settings_round_trip_is_bounded_and_json_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            saved = save_profile_settings(settings, {
                "display_name": "  Martin   Player ",
                "bio": "bio " * 100,
                "background": {"kind": "gradient", "stops": ["#101010", "#AABBCC"], "angle": 999},
            })
            loaded = load_profile_settings(settings)
            self.assertEqual(loaded["display_name"], "Martin Player")
            self.assertLessEqual(len(loaded["bio"]), 160)
            self.assertEqual(loaded["background"]["angle"], 360)
            self.assertEqual(saved["avatar_id"], loaded["avatar_id"])

    def test_legacy_embedded_avatar_is_removed_from_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            settings.setValue("profile_avatar", '{"mime":"image/jpeg","data_b64":"secret"}')
            settings.sync()
            loaded = load_profile_settings(settings)
            self.assertEqual(loaded["avatar_id"], "")
            self.assertEqual(settings.value("profile_avatar", "", type=str), "")

    def test_projection_excludes_installation_details(self):
        db = GameDatabase(":memory:")
        try:
            game_id = db.add_game("Public Game", "/private/install/path", "game.exe", "umu", steam_id="12345")
            db.toggle_favorite(game_id)
            db.add_playtime(game_id, 3600)
            document = build_public_projection(db, {
                "display_name": "Player",
                "public_handle": "01234567890123456789",
                "bio": "  A   public   bio. ",
                "background": DEFAULT_BACKGROUND,
            })
            encoded = json.dumps(document)
            self.assertIn("Public Game", encoded)
            self.assertNotIn("private/install/path", encoded)
            self.assertNotIn("game.exe", encoded)
            self.assertNotIn("owner_token", encoded)
            self.assertEqual(document["stats"]["playtime_seconds"], 3600)
            self.assertEqual(document["games"][0]["app_id"], "12345")
            self.assertEqual(document["bio"], "A public bio.")
            self.assertEqual(document["games"][0]["artwork_url"], steam_hero_url("12345"))
            self.assertEqual(document["games"][0]["achievements"]["unlocked_count"], 0)
        finally:
            db.close()

    def test_archived_games_remain_in_library_archive_and_public_projection(self):
        db = GameDatabase(":memory:")
        try:
            game_id = db.add_game(
                "Archived Game",
                "/private/archived/path",
                "game.exe",
                "umu",
                steam_id="45678",
            )
            db.toggle_favorite(game_id)
            db.add_playtime(game_id, 7200)
            db.save_achievement_schema(game_id, "45678", [
                {"api_name": "ARCHIVE_ONE", "display_name": "Archive complete"},
                {"api_name": "ARCHIVE_TWO", "display_name": "Another milestone"},
            ])
            db.record_achievement_state(game_id, "45678", {"ARCHIVE_ONE": 1_700_000_000})
            self.assertTrue(db.archive_game(game_id))

            all_games = db.get_all_games()
            active = LibraryController().build_snapshot(
                all_games,
                LibraryQuery(filter_mode="all"),
            )
            archived = LibraryController().build_snapshot(
                all_games,
                LibraryQuery(filter_mode="archived"),
            )
            self.assertEqual(active.items, ())
            self.assertEqual([item.game_id for item in archived.items], [game_id])
            self.assertTrue(archived.items[0].is_archived)

            document = build_public_projection(db, {
                "display_name": "Player",
                "public_handle": "archive-player",
                "background": DEFAULT_BACKGROUND,
            })
            self.assertEqual(document["stats"]["games_count"], 1)
            self.assertEqual(document["stats"]["favorite_count"], 1)
            self.assertEqual(document["stats"]["playtime_seconds"], 7200)
            self.assertNotIn("is_archived", json.dumps(document))
            public_game = document["games"][0]
            self.assertEqual(public_game["app_id"], "45678")
            self.assertEqual(public_game["playtime_seconds"], 7200)
            self.assertTrue(public_game["favorite"])
            self.assertEqual(public_game["achievements"]["unlocked_count"], 1)
            self.assertEqual(public_game["achievements"]["total_count"], 2)
            self.assertEqual(public_game["achievements"]["recent"][0]["name"], "Archive complete")
        finally:
            db.close()

    def test_remove_game_keeps_append_only_profile_history(self):
        db = GameDatabase(":memory:")
        try:
            game_id = db.add_game("Removed Game", "/private/removed/path", "game.exe", "umu", steam_id="56789")
            db.add_playtime(game_id, 1800)
            db.toggle_favorite(game_id)
            db.save_achievement_schema(game_id, "56789", [{"api_name": "KEEP_ME", "display_name": "Keep me"}])
            db.record_achievement_state(game_id, "56789", {"KEEP_ME": 1_700_000_001})
            self.assertTrue(db.remove_game(game_id))
            self.assertEqual(db.get_all_games(), [])
            records = db.get_profile_unlock_records(include_pending=False)
            self.assertIn("KEEP_ME", records["56789"])
            historical = db.get_profile_games()
            self.assertEqual(len(historical), 1)
            self.assertEqual(historical[0]["app_id"], "56789")
        finally:
            db.close()

    def test_disk_removal_guard_rejects_root_symlink_and_deletes_only_target(self):
        from ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game_path = root / "game"
            game_path.mkdir()
            (game_path / "save.dat").write_text("save", encoding="utf-8")
            removed, error = MainWindow._remove_game_files_from_disk(str(game_path))
            self.assertTrue(removed, error)
            self.assertFalse(game_path.exists())

            protected_target = root / "protected"
            protected_target.mkdir()
            link_path = root / "game-link"
            link_path.symlink_to(protected_target, target_is_directory=True)
            removed, error = MainWindow._remove_game_files_from_disk(str(link_path))
            self.assertFalse(removed)
            self.assertIn("symlink", error.lower())
            self.assertTrue(protected_target.exists())

    def test_projection_groups_account_achievements_by_steam_app(self):
        db = GameDatabase(":memory:")
        try:
            game_id = db.add_game("Achievement Game", "/private/path", "game.exe", "umu", steam_id="98765")
            db.save_achievement_schema(game_id, "98765", [
                {"api_name": "ACH_ONE", "display_name": "First"},
                {"api_name": "ACH_TWO", "display_name": "Second"},
            ])
            db.record_achievement_state(game_id, "98765", {"ACH_ONE": 1_700_000_000})
            document = build_public_projection(db, {
                "display_name": "Player",
                "public_handle": "player",
                "background": DEFAULT_BACKGROUND,
            })
            game = next(item for item in document["games"] if item["app_id"] == "98765")
            self.assertEqual(game["achievements"]["unlocked_count"], 1)
            self.assertEqual(game["achievements"]["total_count"], 2)
            self.assertEqual(game["achievements"]["recent"][0]["name"], "First")
        finally:
            db.close()

    def test_remote_document_rejects_bad_handle_and_keeps_only_public_shape(self):
        bad = {"schema_version": 1, "handle": "ab", "display_name": "x"}
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
        self.assertIsNone(normalized["avatar_id"])
        self.assertNotIn("path", normalized)

        legacy_avatar = dict(valid)
        legacy_avatar["avatar"] = {"mime": "image/jpeg", "data_b64": "not-public"}
        self.assertNotIn("avatar", normalize_public_document(legacy_avatar))

        unsafe = dict(valid)
        unsafe["games"] = [{
            "name": "Game",
            "app_id": "123",
            "artwork_url": "https://attacker.example/image.jpg",
            "achievements": {"recent": []},
        }]
        normalized_unsafe = normalize_public_document(unsafe)
        self.assertEqual(normalized_unsafe["games"][0]["artwork_url"], steam_hero_url("123"))

    def test_public_bio_and_artwork_are_bounded_and_legacy_artwork_is_replaced(self):
        document = normalize_public_document({
            "schema_version": 1,
            "handle": "01234567890123456789",
            "display_name": "Visible",
            "bio": "x" * 500,
            "background": DEFAULT_BACKGROUND,
            "games": [{
                "name": "Game",
                "app_id": "123",
                "banner_url": "https://cdn.akamai.steamstatic.com/steam/apps/123/header.jpg",
                "artwork_url": "https://cdn.akamai.steamstatic.com/steam/apps/456/capsule_616x353.jpg",
                "achievements": {"recent": [{"app_id": "456", "api_name": "wrong"}]},
            }],
            "favorite_games": [],
            "recent_achievements": [],
        })
        self.assertEqual(document["bio"], "x" * 160)
        self.assertEqual(document["games"][0]["artwork_url"], steam_hero_url("123"))
        self.assertEqual(document["games"][0]["achievements"]["recent"], [])

    def test_steam_hero_background_is_derived_from_app_id(self):
        normalized = normalize_background({
            "kind": "steam_hero",
            "app_id": "1321440",
            "url": "https://attacker.example/background.jpg",
        })
        self.assertEqual(normalized["kind"], "steam_hero")
        self.assertEqual(normalized["app_id"], "1321440")
        self.assertEqual(normalized["url"], steam_hero_url("1321440"))
        self.assertEqual(normalize_background({"kind": "steam_hero", "app_id": "0"}), DEFAULT_BACKGROUND)

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
                "blocked_handles": ["ab", "FEDCFEDCFEDCFEDCFEDC"],
        })
        self.assertEqual(snapshot["friends"][0]["display_name"], "Friend One")
        self.assertEqual(snapshot["friends"][0]["updated_at"], 0)
        self.assertEqual(snapshot["incoming_requests"][0]["request_id"], "request-1")
        self.assertEqual(snapshot["blocked_handles"], ["fedcfedcfedcfedcfedc"])

    def test_username_handles_are_ascii_and_suggested_from_oidc_claims(self):
        self.assertEqual(normalize_username_handle("Ž Martin 42"), "z-martin-42")
        self.assertEqual(profile_username_suggestion({"preferred_username": "Martin_42"}), "martin_42")
        self.assertEqual(profile_username_suggestion({"email": "martin@example.test"}), "player")

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

    def test_handle_availability_is_public_and_validates_response_shape(self):
        response = Mock(status_code=200)
        response.json.return_value = {"handle": "new-player", "available": True}
        session = Mock()
        session.request.return_value = response
        client = ProfileServiceClient("https://profiles.example")
        client.session = session

        self.assertTrue(client.check_handle_availability("@New Player"))
        request = session.request.call_args
        self.assertEqual(request.args[:2], ("GET", "https://profiles.example/api/profile/v1/handles/new-player/availability"))
        self.assertNotIn("Authorization", request.kwargs["headers"])

        response.json.return_value = {"handle": "other", "available": "yes"}
        with self.assertRaisesRegex(ProfileServiceError, "invalid handle availability"):
            client.check_handle_availability("other")

    def test_avatar_catalog_client_validates_catalog_and_uses_gateway_path(self):
        response = Mock(status_code=200)
        response.json.return_value = {"avatars": [{
            "id": "1-1",
            "label": "1-1",
            "category": "Standard",
            "order": 0,
            "sha256": "a" * 64,
            "width": 512,
            "height": 512,
            "bytes": 1024,
        }]}
        session = Mock()
        session.request.return_value = response
        client = ProfileServiceClient("https://profiles.example")
        client.session = session

        self.assertEqual(client.list_avatar_catalog()[0]["id"], "1-1")
        self.assertEqual(client.avatar_url("1-1"), "https://profiles.example/api/profile/v2/avatars/1-1")
        self.assertNotIn("convex", client.avatar_url("1-1"))
        with self.assertRaises(ProfileServiceError):
            client.avatar_url("../storage-id")

    def test_avatar_catalog_maps_legacy_slug_to_numeric_asset(self):
        catalog = normalize_avatar_catalog([{
            "id": "r-1-1",
            "asset_id": 7,
            "label": "R 1-1",
            "category": "R",
            "order": 0,
            "sha256": "b" * 64,
            "width": 512,
            "height": 512,
            "bytes": 1024,
        }])
        self.assertEqual(catalog[0]["id"], "7")
        self.assertEqual(catalog[0]["legacy_id"], "r-1-1")

    def test_avatar_download_rejects_non_images_and_oversized_responses(self):
        response = Mock(status_code=200)
        response.headers = {"Content-Type": "application/json", "Content-Length": "10"}
        session = Mock()
        session.get.return_value = response
        client = ProfileServiceClient("https://profiles.example")
        client.session = session
        with self.assertRaisesRegex(ProfileServiceError, "non-image"):
            client.fetch_avatar_bytes("1-1")

    def test_avatar_batch_reuses_one_client_and_skips_missing_entries(self):
        good = Mock(status_code=200)
        good.headers = {"Content-Type": "image/png", "Content-Length": "3"}
        good.iter_content.return_value = [b"PNG"]
        missing = Mock(status_code=404)
        missing.headers = {"Content-Type": "application/json"}
        session = Mock()
        session.get.side_effect = [good, missing]
        client = ProfileServiceClient("https://profiles.example")
        client.session = session

        self.assertEqual(client.fetch_avatar_batch(["1-1", "r-1-1"]), {"1-1": b"PNG"})
        self.assertEqual(session.get.call_count, 2)
        good.close.assert_called_once()
        missing.close.assert_called_once()

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

    def test_forced_refresh_after_device_login_does_not_reuse_initial_token(self):
        http = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {
            "access_token": "refreshed-access-token",
            "refresh_token": "rotated-refresh-token",
            "expires_in": 3600,
        }
        http.post.return_value = response
        session = CentralAuthSession(
            CentralAuthConfig("https://login.example", "client-id", "https://profiles.example"),
            session=http,
        )

        with patch("core.central_auth.set_secret", return_value=True), \
             patch("core.central_auth.get_secret", return_value="refresh-token"):
            session._set_tokens({
                "access_token": "device-access-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
            })
            self.assertEqual(session.authorization_header(force_refresh=True), "Bearer refreshed-access-token")

        self.assertEqual(http.post.call_count, 1)
        self.assertEqual(http.post.call_args.kwargs["data"]["grant_type"], "refresh_token")

    def test_userinfo_uses_access_token_and_returns_oidc_claims(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "sub": "auth0|opaque-subject",
            "preferred_username": "martin_42",
            "name": "Martin",
            "email": "private@example.test",
        }
        http = Mock()
        http.get.return_value = response
        session = CentralAuthSession(
            CentralAuthConfig("https://login.example", "client-id", "https://profiles.example"),
            session=http,
        )
        with patch("core.central_auth.set_secret", return_value=True):
            session._set_tokens({"access_token": "access-token", "expires_in": 3600})
        self.assertEqual(session.userinfo()["preferred_username"], "martin_42")
        self.assertEqual(http.get.call_args.args[0], "https://login.example/userinfo")
        self.assertEqual(http.get.call_args.kwargs["headers"]["Authorization"], "Bearer access-token")

    def test_resource_server_denial_has_actionable_auth0_guidance(self):
        response = Mock(status_code=403)
        response.json.return_value = {
            "error": "unauthorized_client",
            "error_description": (
                'Client "client-id" is not authorized to access resource server '
                '"https://profiles.example".'
            ),
        }
        http = Mock()
        http.post.return_value = response
        session = CentralAuthSession(
            CentralAuthConfig("https://login.example", "client-id", "https://profiles.example"),
            session=http,
        )

        with self.assertRaises(CentralAuthError) as raised:
            session._post_form("https://login.example/oauth/device/code", {"client_id": "client-id"})

        self.assertEqual(raised.exception.code, "api_not_authorized")
        self.assertIn("Applications → APIs", str(raised.exception))
        self.assertIn("https://profiles.example", str(raised.exception))


class ProfilePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_avatar_catalog_dialog_is_selectable_and_searchable(self):
        catalog = [{
            "id": "1-1",
            "label": "1-1",
            "category": "Standard",
            "order": 0,
            "sha256": "a" * 64,
            "width": 512,
            "height": 512,
            "bytes": 1024,
        }, {
            "id": "r-1-1",
            "label": "R 1-1",
            "category": "R",
            "order": 1,
            "sha256": "b" * 64,
            "width": 512,
            "height": 512,
            "bytes": 1024,
        }]
        dialog = ProfileAvatarCatalogDialog(catalog, "r-1-1")
        try:
            self.assertEqual(dialog.objectName(), "safeLauncherPopup")
            self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertEqual(dialog.windowTitle(), "Choose profile picture")
            self.assertEqual(dialog.btn_select.text(), "Select")
            self.assertEqual(dialog.table.columnCount(), 6)
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertEqual(dialog.selected_avatar_id, "r-1-1")
            dialog.search.setText("standard")
            self.assertFalse(dialog.table.isRowHidden(0))
            self.assertFalse(dialog._tiles_by_id["1-1"].isHidden())
            self.assertTrue(dialog._tiles_by_id["r-1-1"].isHidden())
            QTimer.singleShot(0, dialog.accept)
            self.assertEqual(dialog.exec(), dialog.DialogCode.Accepted)
            self.assertEqual(dialog.selected_avatar_id, "r-1-1")
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_avatar_decode_removes_only_bad_iccp_chunk(self):
        signature = b"\x89PNG\r\n\x1a\n"

        def chunk(kind, payload=b""):
            return len(payload).to_bytes(4, "big") + kind + payload + b"crc!"

        encoded = signature + chunk(b"iCCP", b"broken-profile") + chunk(b"IEND")
        decoded = ProfilePageWidget._png_without_iccp(encoded)
        self.assertNotIn(b"iCCP", decoded)
        self.assertIn(b"IEND", decoded)
        self.assertEqual(ProfilePageWidget._png_without_iccp(b"not-a-png"), b"not-a-png")

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

    def test_profile_owner_actions_are_unified_and_public_view_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            db = GameDatabase(":memory:")
            auth = Mock()
            auth.signed_in = False
            page = ProfilePageWidget(db, settings, auth_session=auth)
            try:
                self.assertIs(page.btn_banner_edit, page.btn_edit)
                self.assertFalse(hasattr(page, "btn_open_public"))
                self.assertFalse(hasattr(page, "btn_settings"))
                self.assertFalse(page.btn_banner_edit.isHidden())
                self.assertEqual(page.status_label.text(), "Unpublished · Private")
                self.assertTrue(hasattr(page, "panel_theme_combo"))

                auth.signed_in = True
                page._set_admin_controls(True)
                self.assertEqual(page.profile_action_state(), (True, True, False, False))

                page._start_edit()
                self.assertTrue(page.btn_banner_edit.isHidden())
                page.panel_theme_combo.setCurrentIndex(page.panel_theme_combo.findData("sunset"))
                self.assertEqual(page.profile_theme(), "sunset")
                page._cancel_edit()
                self.assertFalse(page.btn_banner_edit.isHidden())
                self.assertEqual(page.profile_theme(), "grey")

                public = build_public_projection(db, {
                    "display_name": "Public Player",
                    "public_handle": "01234567890123456789",
                    "background": DEFAULT_BACKGROUND,
                })
                self.assertTrue(page.show_public(public))
                self.assertTrue(page.btn_banner_edit.isHidden())
                self.assertIn("Published · Public · @", page.status_label.text())
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()

    def test_profile_editor_bio_and_sixth_game_navigation(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            db = GameDatabase(":memory:")
            page = ProfilePageWidget(db, settings)
            try:
                games = [{
                    "name": f"Game {index}",
                    "app_id": str(1000 + index),
                    "artwork_url": f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{1000 + index}/capsule_616x353.jpg",
                    "playtime_seconds": index * 3600,
                    "achievements": {"unlocked_count": index, "total_count": 10, "recent": []},
                } for index in range(6)]
                public = normalize_public_document({
                    "schema_version": 1,
                    "handle": "01234567890123456789",
                    "display_name": "Public Player",
                    "bio": "A short public bio",
                    "background": DEFAULT_BACKGROUND,
                    "stats": {},
                    "games": games,
                    "favorite_games": [],
                    "recent_achievements": [],
                })
                self.assertTrue(page.show_public(public))
                self.assertFalse(page.bio_label.isHidden())
                self.assertEqual(page.games_grid.count(), 6)  # five games plus See more
                card_heights = {
                    page.games_grid.itemAt(index).widget().height()
                    for index in range(page.games_grid.count())
                }
                self.assertEqual(len(card_heights), 1)
                self.assertEqual(page.games_all_grid.count(), 0)  # populated on demand
                page.games_grid.itemAt(5).widget().clicked.emit()
                self.assertEqual(page.games_stack.currentIndex(), 1)
                self.assertEqual(page.games_all_grid.count(), 6)
                page.games_all_grid.itemAt(0).widget().clicked.emit(games[0])
                self.assertEqual(page.games_stack.currentIndex(), 2)
                page.btn_games_back.click()
                self.assertEqual(page.games_stack.currentIndex(), 1)
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()

    def test_header_uses_one_combined_identity_control(self):
        class TestWindow(QMainWindow):
            def _toggle_maximize(self):
                pass

        window = TestWindow()
        header = HeaderBar(window)
        try:
            header.set_profile_identity("Martin Player", "martin-player")
            self.assertEqual(header.btn_profile.text(), "Martin Player")
            self.assertIn("@martin-player", header.btn_profile.toolTip())
            self.assertFalse(hasattr(header, "profile_identity_label"))
            self.assertIsNotNone(header.btn_profile.menu())
            self.assertEqual(header.btn_friends.text(), "Friends")
            actions = [action.text() for action in header.profile_menu.actions() if not action.isSeparator()]
            self.assertIn("Find Friends…", actions)
        finally:
            header.deleteLater()
            window.deleteLater()
            self.app.processEvents()

    def test_profile_settings_theme_preview_and_friends_popup_are_custom(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            auth = Mock()
            auth.signed_in = False
            settings_dialog = UserSettingsDialog("Player", parent=None, profile_theme="grey")
            friends_dialog = FriendsDialog(settings, auth)
            try:
                settings_dialog.combo_profile_theme.setCurrentIndex(
                    settings_dialog.combo_profile_theme.findData("aurora")
                )
                self.assertEqual(settings_dialog.get_profile_theme(), "aurora")
                self.assertTrue(settings_dialog.profile_theme_preview.styleSheet())
                settings_dialog.set_profile_action_state(True, True, False, False)
                self.assertEqual(settings_dialog.btn_profile_auth.text(), "Sign out")
                self.assertEqual(settings_dialog.btn_profile_publish.text(), "Publish profile")
                self.assertTrue(settings_dialog.btn_profile_publish.isEnabled())
                settings_dialog.set_profile_action_state(True, True, True, True)
                self.assertEqual(settings_dialog.btn_profile_publish.text(), "Unpublish profile")
                self.assertFalse(settings_dialog.btn_profile_publish.isEnabled())
                self.assertTrue(friends_dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
                self.assertEqual(friends_dialog.tabs.count(), 3)
                self.assertEqual(friends_dialog.tabs.tabText(2), "Find Friends")
                friends_dialog.find_input.setText("not valid!")
                self.assertEqual(friends_dialog._target(), "")
            finally:
                settings_dialog.close()
                friends_dialog.close()
                settings_dialog.deleteLater()
                friends_dialog.deleteLater()
                self.app.processEvents()

    def test_edit_game_exposes_safe_lifecycle_action_without_form_save(self):
        with tempfile.TemporaryDirectory() as directory:
            db = GameDatabase(":memory:")
            game_path = Path(directory) / "game"
            game_path.mkdir()
            game_id = db.add_game("Lifecycle Game", str(game_path), "game.exe", "umu", steam_id="67890")
            game = db.get_all_games()[0]
            edit_dialog = EditGameDialog(game, parent=None)
            chooser = CustomRemoveDialog("Lifecycle Game", parent=None)
            try:
                self.assertEqual(edit_dialog.game_id, game_id)
                self.assertEqual(edit_dialog.lifecycle_action, "")
                self.assertFalse(edit_dialog.btn_lifecycle.isHidden())
                self.assertEqual(edit_dialog.LIFECYCLE_RESULT, 2)
                chooser._select_remove_library()
                self.assertEqual(chooser.choice, "remove_library")
            finally:
                edit_dialog.close()
                chooser.close()
                edit_dialog.deleteLater()
                chooser.deleteLater()
                db.close()
                self.app.processEvents()

    def test_profile_editor_shows_username_but_hides_service_details(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            save_profile_settings(settings, {
                "display_name": "Player",
                "public_handle": "taken-name",
                "published": False,
                "bio": "old bio",
                "background": DEFAULT_BACKGROUND,
            })
            db = GameDatabase(":memory:")
            page = ProfilePageWidget(db, settings)
            try:
                page._start_edit()
                page.name_edit.setText("New Name")
                page.bio_edit.setPlainText("A visible bio")
                page._save_edit()
                self.assertTrue(hasattr(page, "handle_edit"))
                self.assertFalse(hasattr(page, "service_url_edit"))
                self.assertEqual(page.handle_edit.text(), "taken-name")
                self.assertEqual(page.handle_label.text(), "@taken-name")
                loaded = load_profile_settings(settings)
                self.assertEqual(loaded["display_name"], "New Name")
                self.assertEqual(loaded["bio"], "A visible bio")
                self.assertEqual(loaded["public_handle"], "taken-name")
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()

    def test_profile_editor_saves_steam_hero_background_from_appid(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            save_profile_settings(settings, {
                "display_name": "Player",
                "public_handle": "profile-player",
                "published": False,
                "background": DEFAULT_BACKGROUND,
            })
            db = GameDatabase(":memory:")
            page = ProfilePageWidget(db, settings)
            try:
                page._start_edit()
                page.background_combo.setCurrentIndex(page.background_combo.findData("steam_hero"))
                page.background_app_id_edit.setText("1321440")
                with patch("ui.components.profile_page.automatic_network_allowed", return_value=False):
                    page._save_edit()
                saved = load_profile_settings(settings)
                self.assertEqual(saved["background"]["kind"], "steam_hero")
                self.assertEqual(saved["background"]["app_id"], "1321440")
                self.assertEqual(saved["background"]["url"], steam_hero_url("1321440"))
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()

    def test_profile_editor_previews_backgrounds_without_mutating_saved_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            save_profile_settings(settings, {
                "display_name": "Player",
                "public_handle": "profile-player",
                "published": False,
                "background": DEFAULT_BACKGROUND,
            })
            db = GameDatabase(":memory:")
            page = ProfilePageWidget(db, settings)
            try:
                page._start_edit()
                page.background_combo.setCurrentIndex(page.background_combo.findData("ember"))
                self.assertEqual(page._draft_background, normalize_background(BACKGROUND_PRESETS["ember"]))
                self.assertEqual(page._document["background"], normalize_background(BACKGROUND_PRESETS["ember"]))
                self.assertEqual(load_profile_settings(settings)["background"], normalize_background(DEFAULT_BACKGROUND))

                page.panel_theme_combo.setCurrentIndex(page.panel_theme_combo.findData("aurora"))
                self.assertEqual(page.profile_theme(), "aurora")
                self.assertEqual(page._document["background"], normalize_background(BACKGROUND_PRESETS["ember"]))

                page._cancel_edit()
                self.assertEqual(page._document["background"], normalize_background(DEFAULT_BACKGROUND))
                self.assertEqual(page.profile_theme(), "grey")
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()

    def test_profile_editor_previews_steam_hero_behind_panels(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            save_profile_settings(settings, {
                "display_name": "Player",
                "public_handle": "profile-player",
                "published": False,
                "background": DEFAULT_BACKGROUND,
            })
            db = GameDatabase(":memory:")
            page = ProfilePageWidget(db, settings)
            try:
                page._start_edit()
                page.background_combo.setCurrentIndex(page.background_combo.findData("steam_hero"))
                page.background_app_id_edit.setText("1321440")
                with patch("ui.components.profile_page.automatic_network_allowed", return_value=False):
                    page._use_steam_hero_background()
                self.assertEqual(page._document["background"]["kind"], "steam_hero")
                self.assertEqual(page._document["background"]["app_id"], "1321440")
                self.assertEqual(load_profile_settings(settings)["background"], normalize_background(DEFAULT_BACKGROUND))
                self.assertIn("background: transparent", page._background_style(page._document["background"]))
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()

    def test_nested_profile_panels_apply_opaque_and_glass_surface_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            db = GameDatabase(":memory:")
            page = ProfilePageWidget(db, settings)
            try:
                page.show_public(build_public_projection(db, {
                    "display_name": "Player",
                    "public_handle": "profile-player",
                    "background": {"kind": "solid", "color": "#FF0000"},
                }))
                page.resize(1000, 800)
                page.show()
                self.app.processEvents()
                for key in ("grey", "aurora", "sunset", "bubble"):
                    page.set_profile_theme(key)
                    self.app.processEvents()
                    color = page.stats_section.grab().toImage().pixelColor(10, 10)
                    self.assertEqual(color.alpha(), 255)
                    self.assertNotEqual(color.getRgb()[:3], (255, 0, 0))

                page.set_profile_theme("glassmorphism")
                self.app.processEvents()
                glass_color = page.stats_section.grab().toImage().pixelColor(10, 10)
                self.assertLess(glass_color.red(), 255)
                self.assertNotEqual(glass_color.getRgb()[:3], (255, 0, 0))
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()

    def test_steam_hero_is_painted_as_background_independent_of_panel_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "profile.ini"), QSettings.Format.IniFormat)
            db = GameDatabase(":memory:")
            page = ProfilePageWidget(db, settings)
            try:
                document = build_public_projection(db, {
                    "display_name": "Player",
                    "public_handle": "profile-player",
                    "background": {"kind": "steam_hero", "app_id": "1321440"},
                })
                page._document = document
                page._profile_background_pixmap = QPixmap(32, 32)
                page._profile_background_pixmap.fill(QColor("#D83B3B"))
                page._profile_background_blur_size = (0, 0)
                page.resize(500, 400)

                first = QImage(page.size(), QImage.Format.Format_ARGB32)
                first.fill(QColor(0, 0, 0, 0))
                page.render(first)
                page.set_profile_theme("sunset")
                second = QImage(page.size(), QImage.Format.Format_ARGB32)
                second.fill(QColor(0, 0, 0, 0))
                page.render(second)

                first_color = first.pixelColor(5, 5)
                second_color = second.pixelColor(5, 5)
                self.assertGreater(first_color.red(), first_color.green() + 20)
                self.assertEqual(first_color, second_color)
            finally:
                page.close()
                page.deleteLater()
                db.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
