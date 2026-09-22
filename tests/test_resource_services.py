"""Focused tests for Phase 5 transport/resource service boundaries."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.achievement_providers import AchievementResolution, AchievementAvailability
from core.achievement_resource_service import AchievementResourceService, AchievementTarget
from core.artwork_resource_service import ArtworkResourceService, ArtworkTarget
from core.request_contracts import ResourceStatus
from core.request_manager import RequestManager
from core.resource_cache import ResourceCache
from core.steam_build_tracker import SteamBuildFetcher
from core.steam_resource_service import SteamResourceService
from core.steam_tags import SteamTagsFetcher


class _AchievementDB:
    def __init__(self, _path):
        self.closed = False

    def close(self):
        self.closed = True

    def get_achievement_stats(self, _game_id):
        return 2, 4, 50.0

    def get_recent_unlocked_achievements(self, _game_id, limit=5):
        return [{"api_name": "A", "unlock_time": 1.0}][:limit]


class _SteamClient:
    def __init__(self):
        self.build_calls = []
        self.tag_calls = []

    def public_build(self, app_id):
        self.build_calls.append(app_id)
        return "123", 456

    def tags_for_game(self, name):
        self.tag_calls.append(name)
        return ["Action"], "480"


class _ArtworkClient:
    def __init__(self):
        self.search_calls = 0

    def search_game(self, name):
        self.search_calls += 1
        return {"primary": {"appid": "480", "banner_url": "https://art/banner"}}

    def download_banner(self, _url):
        return "/cache/banner.jpg"

    def fetch_and_cache_game_icon(self, _game_id, _app_id, _name, exe_path=""):
        return "/cache/icon.png"

    def download_hero_banner(self, _steam_id, _game_id, _name, exe_path=""):
        return "/cache/hero.jpg"


class _EmptyArtworkClient:
    def __init__(self):
        self.search_calls = 0

    def search_game(self, _name):
        self.search_calls += 1
        return {"found": False, "results": [], "primary": None}

    def banner_cache_path(self, _url):
        return None

    def get_icon_cached_path(self, **_kwargs):
        return None

    def download_banner(self, _url):
        return ""

    def fetch_and_cache_game_icon(self, _game_id, _app_id, _name, exe_path=""):
        return ""

    def download_hero_banner(self, _steam_id, _game_id, _name, exe_path=""):
        return ""


class ResourceServiceTests(unittest.TestCase):
    def test_achievement_service_owns_worker_db_and_typed_batch(self):
        manager = RequestManager(max_workers=1)
        database = _AchievementDB("library.db")
        resolution = AchievementResolution(
            [], {}, None, "", "", AchievementAvailability.NO_ACHIEVEMENTS
        )
        try:
            service = AchievementResourceService(
                manager, db_factory=lambda path: database
            )
            target = AchievementTarget(7, "480", "/games/example", "/prefix")
            with patch(
                "core.achievement_resource_service.coordinated_resolve",
                return_value=resolution,
            ), patch("core.achievement_resource_service.persist_resolution") as persist:
                result = service.request_status(target).future.result(timeout=2)
            self.assertEqual(result.status, ResourceStatus.READY)
            self.assertEqual(result.value[1:4], (2, 4, 50.0))
            persist.assert_called_once()
            self.assertTrue(database.closed)
            self.assertEqual(service.key_for(target).variant, "v1")
        finally:
            manager.shutdown()

    def test_steam_service_deduplicates_builds_and_caches_tags(self):
        manager = RequestManager(max_workers=2, cache=ResourceCache())
        client = _SteamClient()
        try:
            service = SteamResourceService(manager, client=client)
            builds = service.request_build_many(["480", "480", "730"])
            self.assertEqual(
                [handle.future.result(timeout=2).value for handle in builds],
                [("123", 456), ("123", 456)],
            )
            self.assertEqual(sorted(client.build_calls), ["480", "730"])
            first = service.request_tags("Example Game").future.result(timeout=2)
            second = service.request_tags("example game").future.result(timeout=2)
            self.assertEqual(first.value, (["Action"], "480"))
            self.assertIsNotNone(second.value)
            self.assertEqual(client.tag_calls, ["Example Game"])
        finally:
            manager.shutdown()

    def test_steam_compatibility_workers_use_service_keys_and_cache_policy(self):
        manager = RequestManager(max_workers=2, cache=ResourceCache())
        client = _SteamClient()
        try:
            build_worker = SteamBuildFetcher(
                1,
                "480",
                local_build_id="123",
                request_manager=manager,
                steam_client=client,
            )
            build_worker.safe_run()
            self.assertEqual(
                manager.state(SteamResourceService.build_key("480")).status,
                ResourceStatus.READY,
            )

            tags_worker = SteamTagsFetcher(
                1,
                "Example Game",
                request_manager=manager,
                steam_client=client,
            )
            tags_worker.safe_run()
            self.assertEqual(
                manager.state(SteamResourceService.tags_key("example game")).status,
                ResourceStatus.READY,
            )
        finally:
            manager.shutdown()

    def test_artwork_service_shares_stable_resource_keys(self):
        manager = RequestManager(max_workers=2)
        client = _ArtworkClient()
        try:
            service = ArtworkResourceService(manager, client=client)
            first_target = ArtworkTarget(1, "Example", "480", "/games/example")
            second_target = ArtworkTarget(2, "Example", "480", "/games/other")
            first = service.request_auto(first_target)
            second = service.request_auto(second_target)
            self.assertEqual(first.key, second.key)
            self.assertEqual(first.future.result(timeout=2).value[0], "/cache/banner.jpg")
            self.assertEqual(second.future.result(timeout=2).value[2], "/cache/icon.png")
        finally:
            manager.shutdown()

    def test_automatic_artwork_reuses_shared_search_cache(self):
        manager = RequestManager(max_workers=2, cache=ResourceCache())
        client = _ArtworkClient()
        try:
            service = ArtworkResourceService(manager, client=client)
            target = ArtworkTarget(1, "Example", "480")
            self.assertEqual(service.request_auto(target).future.result(timeout=2).status, ResourceStatus.READY)
            search = service.request_search("Example").future.result(timeout=2)
            self.assertEqual(search.value["primary"]["appid"], "480")
            self.assertEqual(client.search_calls, 1)
        finally:
            manager.shutdown()

    def test_artwork_search_and_banner_keys_do_not_embed_user_or_cdn_text(self):
        manager = RequestManager(max_workers=1)
        client = _ArtworkClient()
        try:
            service = ArtworkResourceService(manager, client=client)
            search = service.search_spec("Example Game")
            banner = service.banner_spec("https://cdn.example/private-looking-banner.jpg?token=x")
            self.assertNotIn("Example Game", search.key.cache_key())
            self.assertNotIn("cdn.example", banner.key.cache_key())
            self.assertEqual(search.key, service.search_spec(" example game ").key)
            self.assertEqual(
                banner.key,
                service.banner_spec("https://cdn.example/private-looking-banner.jpg?token=x").key,
            )
        finally:
            manager.shutdown()

    def test_empty_artwork_results_are_retryable_and_not_cached_as_success(self):
        manager = RequestManager(max_workers=1, cache=ResourceCache())
        client = _EmptyArtworkClient()
        try:
            service = ArtworkResourceService(manager, client=client)
            target = ArtworkTarget(1, "Missing Game", "480")
            first = service.request_auto(target).future.result(timeout=2)
            second = service.request_auto(target).future.result(timeout=2)
            first_search = service.request_search(target.game_name).future.result(timeout=2)
            second_search = service.request_search(target.game_name).future.result(timeout=2)

            self.assertEqual(first.status, ResourceStatus.READY)
            self.assertEqual(second.status, ResourceStatus.READY)
            self.assertEqual(first_search.status, ResourceStatus.READY)
            self.assertEqual(second_search.status, ResourceStatus.READY)
            self.assertEqual(first.value, ("", 480, ""))
            self.assertEqual(second.value, ("", 480, ""))
            self.assertEqual(client.search_calls, 4)
            self.assertIsNone(manager.cache.get(service.search_key(target.game_name)))
            self.assertIsNone(manager.cache.get(service.auto_spec(target).key))
        finally:
            manager.shutdown()


if __name__ == "__main__":
    unittest.main()
