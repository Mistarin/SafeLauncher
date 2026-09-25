"""Regression tests for the application-wide offline network gate."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from unittest.mock import patch

import requests

from core.network_policy import (
    OFFLINE_MODE_ENV,
    OFFLINE_MODE_SETTING,
    OFFLINE_TEST_MODE_ENV,
    automatic_network_allowed,
    is_offline_mode,
    set_offline_mode,
)


class _Settings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None, type=None):
        value = self.values.get(key, default)
        if type is bool:
            return bool(value)
        return value

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        pass


class NetworkPolicyTests(unittest.TestCase):
    def test_connectivity_probe_returns_safe_failure_for_transport_error(self):
        from core.network_probe import probe_internet

        with patch(
            "core.network_probe.requests.get",
            side_effect=requests.ConnectionError("offline"),
        ):
            self.assertEqual(
                probe_internet(timeout=0.5),
                (False, "The internet connection could not be reached."),
            )

    def test_persisted_setting_round_trip(self):
        settings = _Settings()
        self.assertFalse(is_offline_mode(settings))
        set_offline_mode(True, settings)
        self.assertTrue(settings.values[OFFLINE_MODE_SETTING])
        self.assertTrue(is_offline_mode(settings))
        self.assertFalse(automatic_network_allowed(settings))

    def test_environment_override_blocks_automatic_work(self):
        settings = _Settings()
        with patch.dict(os.environ, {OFFLINE_MODE_ENV: "1"}, clear=False):
            self.assertTrue(is_offline_mode(settings))
            self.assertFalse(automatic_network_allowed(settings))

    def test_test_mode_blocks_network_without_changing_user_setting(self):
        settings = _Settings()
        with patch.dict(os.environ, {OFFLINE_TEST_MODE_ENV: "1"}, clear=False):
            self.assertFalse(automatic_network_allowed(settings))
        self.assertFalse(is_offline_mode(settings))

    def test_telemetry_does_not_open_http_in_offline_mode(self):
        from core.telemetry import send_central_telemetry

        with patch.dict(os.environ, {OFFLINE_MODE_ENV: "1"}, clear=False), patch(
            "core.telemetry.requests.post"
        ) as post:
            self.assertFalse(send_central_telemetry("test"))
        post.assert_not_called()

    def test_ludusavi_does_not_download_in_offline_mode(self):
        from core import ludusavi_installer

        with patch.dict(
            os.environ,
            {OFFLINE_MODE_ENV: "1", "SAFELAUNCHER_LUDUSAVI": ""},
            clear=False,
        ), patch.object(
            ludusavi_installer, "get_managed_ludusavi_path", return_value="/missing/ludusavi"
        ), patch.object(ludusavi_installer.shutil, "which", return_value=None), patch.object(
            ludusavi_installer, "_download_ludusavi"
        ) as download:
            self.assertIsNone(ludusavi_installer.ensure_ludusavi())
        download.assert_not_called()

    @unittest.skipIf(os.name == "nt", "Unix execute bits do not apply on Windows")
    def test_managed_ludusavi_repairs_missing_execute_bit(self):
        from core import ludusavi_installer

        with tempfile.TemporaryDirectory() as temp_dir:
            managed = os.path.join(temp_dir, "ludusavi")
            with open(managed, "wb") as handle:
                handle.write(b"not a real binary")
            os.chmod(managed, stat.S_IRUSR | stat.S_IWUSR)

            with patch.dict(os.environ, {"SAFELAUNCHER_LUDUSAVI": ""}, clear=False), patch.object(
                ludusavi_installer, "get_managed_ludusavi_path", return_value=managed
            ):
                self.assertEqual(ludusavi_installer.ensure_ludusavi(), managed)

            self.assertTrue(os.stat(managed).st_mode & stat.S_IXUSR)
