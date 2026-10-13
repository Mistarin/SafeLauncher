"""Steam build parsing hardening, managed offline handling, and news/store facts."""

from __future__ import annotations

import unittest
from concurrent.futures import Future
from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication

from core.request_contracts import RequestKey, ResourceResult, ResourceStatus
from core.steam_build_tracker import SteamBuildFetcher
from core.steam_client import SteamClient, SteamClientError
from ui.steam_metadata_controller import _plain_text


class _Response:
    def __init__(self, payload=None, status_code=200, raw=None):
        self.payload = payload
        self.raw = raw
        self.status_code = status_code

    def json(self):
        if self.raw is not None:
            raise ValueError("not json")
        return self.payload

    def close(self):
        pass


class _Session:
    def __init__(self, response):
        self.headers = {}
        self.response = response

    def get(self, url, **kwargs):
        return self.response


class BuildPayloadTests(unittest.TestCase):
    def _build(self, payload=None, *, raw=None):
        return SteamClient(_Session(_Response(payload, raw=raw))).public_build("570")

    def test_well_formed_public_branch_is_read(self):
        payload = {"data": {"570": {"depots": {"branches": {"public": {"buildid": "9", "timeupdated": "100"}}}}}}
        self.assertEqual(self._build(payload), ("9", 100))

    def test_missing_app_data_means_no_build_not_an_exception(self):
        self.assertEqual(self._build({"data": None}), ("", 0))
        self.assertEqual(self._build({"data": {"570": []}}), ("", 0))

    def test_unexpected_top_level_shape_is_a_reportable_error(self):
        with self.assertRaises(SteamClientError):
            self._build(["not", "an", "object"])

    def test_html_error_page_is_a_reportable_error(self):
        with self.assertRaises(SteamClientError):
            self._build(raw=b"<html>blocked</html>")


class NewsTests(unittest.TestCase):
    def test_news_is_plain_text_and_links_only_to_the_store_news_page(self):
        payload = {"appnews": {"newsitems": [{
            "gid": "1",
            "title": "Patch &amp; Fixes",
            "contents": "[b]Balance[/b] changes <script>x</script> done",
            "author": "dev",
            "date": 1700000000,
            "url": "https://evil.example/phish",
        }]}}
        items = SteamClient(_Session(_Response(payload))).news("570")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Patch & Fixes")
        self.assertEqual(items[0]["text"], "Balance changes x done")
        self.assertEqual(items[0]["url"], "https://store.steampowered.com/news/app/570")
        self.assertEqual(items[0]["date"], 1700000000)

    def test_malformed_news_is_reported(self):
        with self.assertRaises(SteamClientError):
            SteamClient(_Session(_Response({"appnews": {"newsitems": "oops"}}))).news("570")
        with self.assertRaises(SteamClientError):
            SteamClient(_Session(_Response(raw=b"nope"))).news("570")

    def test_no_news_is_an_empty_list(self):
        self.assertEqual(SteamClient(_Session(_Response({"appnews": {}}))).news("570"), [])

    def test_description_markup_is_stripped_before_display(self):
        self.assertEqual(_plain_text("<b>Fast</b> &amp; fun"), "Fast & fun")


class _ManagerReturningOffline:
    """Stand-in request manager whose only job is to return an OFFLINE result."""

    def __init__(self):
        self.submitted = []

    def submit(self, spec):
        self.submitted.append(spec)
        future = Future()
        future.set_result(ResourceResult(key=spec.key, status=ResourceStatus.OFFLINE))
        return SimpleNamespace(key=spec.key, future=future)

    def state(self, key):
        return SimpleNamespace(status=ResourceStatus.OFFLINE, value=None)


class ManagedBuildOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QCoreApplication.instance() or QCoreApplication([])

    def test_offline_managed_check_reports_offline_not_a_failure(self):
        fetcher = SteamBuildFetcher(1, "570", "", 0, request_manager=_ManagerReturningOffline(),
                                    steam_client=SteamClient(_Session(_Response({}))))
        offline, failures = [], []
        fetcher.offline_detected.connect(offline.append)
        fetcher.check_failed.connect(lambda _gid, reason: failures.append(reason))
        fetcher.safe_run()
        self.assertEqual(offline, [1])
        self.assertEqual(failures, [])


class DetailPageUpdatesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_updates_render_as_plain_text_and_only_store_links_are_offered(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QLabel, QPushButton
        from ui.components.game_detail_page import GameDetailPageWidget

        page = GameDetailPageWidget()
        try:
            page.set_updates([
                {"title": "<b>Hotfix</b>", "text": "Fixed <i>crash</i>", "date": 0,
                 "url": "https://store.steampowered.com/news/app/570"},
                {"title": "Other", "text": "", "date": 0, "url": "https://evil.example/x"},
                {"title": "", "text": "skipped", "date": 0, "url": ""},
            ])
            cards = [page.updates_list.itemAt(i).widget() for i in range(page.updates_list.count())]
            labels = [lbl for card in cards for lbl in card.findChildren(QLabel)]
            titles = [lbl.text() for lbl in labels if lbl.text() in {"<b>Hotfix</b>", "Other"}]
            self.assertEqual(titles, ["<b>Hotfix</b>", "Other"])
            self.assertTrue(all(lbl.textFormat() == Qt.TextFormat.PlainText for lbl in labels))
            self.assertEqual(len(cards), 2)
            buttons = [btn for card in cards for btn in card.findChildren(QPushButton)]
            self.assertEqual(len(buttons), 1)
            self.assertFalse(page.updates_header.isHidden())
            page.set_updates([])
            self.assertEqual(page.updates_list.count(), 0)
            self.assertTrue(page.updates_header.isHidden())
        finally:
            page.close()
            page.deleteLater()

    def test_store_facts_show_release_and_credits(self):
        from ui.components.game_detail_page import GameDetailPageWidget

        page = GameDetailPageWidget()
        try:
            page.set_store_facts({"release_date": "10 Oct, 2007", "developers": ["Valve"], "publishers": ["Valve"]})
            self.assertEqual(page.lbl_store_facts.text(), "Released 10 Oct, 2007  ·  Developer: Valve  ·  Publisher: Valve")
            page.set_store_facts({})
            self.assertTrue(page.lbl_store_facts.isHidden())
        finally:
            page.close()
            page.deleteLater()


if __name__ == "__main__":
    unittest.main()
