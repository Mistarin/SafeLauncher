"""Regression coverage for credential boundaries and Qt worker teardown."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from core.cloud_cli_wizard import _convex_cli_env, _redact_deploy_output
from core.disk_utils import get_dir_size, peek_dir_size
from core.host_process import host_process_env
from core.logger import redact_sensitive_text
from core.performance_env import build_launch_env
from ui.threads import DiskSizeFetcherThread


class SecurityBoundaryTests(unittest.TestCase):
    def test_dotenv_credentials_are_ignored_and_store_key_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            backend = Path(temp_dir)
            (backend / ".env.local").write_text(
                "CONVEX_DEPLOYMENT=prod:central\n"
                "CONVEX_DEPLOY_KEY=dotenv-deploy-key\n"
                "CONVEX_GATEWAY_KEY=dotenv-gateway-key\n"
                "SAFELAUNCHER_GATEWAY_KEY=dotenv-central-key\n"
                "SAFELAUNCHER_SECRET_KEY=dotenv-api-secret\n"
                "AUTH0_CLIENT_SECRET=dotenv-auth-secret\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "CONVEX_DEPLOY_KEY": "inherited-deploy-key",
                    "CONVEX_GATEWAY_KEY": "inherited-gateway-key",
                    "SAFELAUNCHER_GATEWAY_KEY": "inherited-central-key",
                    "SAFELAUNCHER_SECRET_KEY": "inherited-api-secret",
                    "AUTH0_CLIENT_SECRET": "inherited-auth-secret",
                },
                clear=False,
            ), patch(
                "core.cloud_cli_wizard.get_secret",
                return_value="os-store-deploy-key",
            ):
                env = _convex_cli_env(backend)

            self.assertEqual(env["CONVEX_DEPLOYMENT"], "prod:central")
            self.assertEqual(env["CONVEX_DEPLOY_KEY"], "os-store-deploy-key")
            self.assertNotIn("CONVEX_GATEWAY_KEY", env)
            self.assertNotIn("SAFELAUNCHER_GATEWAY_KEY", env)
            self.assertNotIn("SAFELAUNCHER_SECRET_KEY", env)
            self.assertNotIn("AUTH0_CLIENT_SECRET", env)

    def test_deploy_diagnostics_redact_credentials_and_token_shaped_values(self):
        diagnostic = _redact_deploy_output(
            "CONVEX_DEPLOY_KEY=team:project|deploy-secret\n"
            "SAFELAUNCHER_SECRET_KEY=api-secret",
            ("api-secret",),
        )
        self.assertNotIn("team:project|deploy-secret", diagnostic)
        self.assertNotIn("api-secret", diagnostic)
        self.assertIn("[redacted]", diagnostic)

    def test_host_and_game_process_environments_drop_credentials(self):
        with patch.dict(
            os.environ,
            {
                "CONVEX_DEPLOY_KEY": "deploy-secret",
                "SAFELAUNCHER_SECRET_KEY": "cloud-secret",
                "STEAM_WEB_API_KEY": "steam-secret",
                "SAFE_NORMAL_FLAG": "kept",
            },
            clear=False,
        ):
            clean_host = host_process_env()
        self.assertNotIn("CONVEX_DEPLOY_KEY", clean_host)
        self.assertNotIn("SAFELAUNCHER_SECRET_KEY", clean_host)
        self.assertNotIn("STEAM_WEB_API_KEY", clean_host)
        self.assertEqual(clean_host["SAFE_NORMAL_FLAG"], "kept")

        launch_env, _status = build_launch_env(
            {
                "RAWG_API_KEY": "rawg-secret",
                "GAME_TOKEN": "game-secret",
                "CUSTOM_TEST_FLAG": "enabled",
            }
        )
        self.assertNotIn("RAWG_API_KEY", launch_env)
        self.assertNotIn("GAME_TOKEN", launch_env)
        self.assertEqual(launch_env["CUSTOM_TEST_FLAG"], "enabled")

    def test_logger_redacts_headers_assignments_and_query_credentials(self):
        message = redact_sensitive_text(
            "Authorization: Bearer bearer-secret "
            "CONVEX_DEPLOY_KEY=team:prod|deploy-secret "
            "https://example.test/?api_key=query-secret&ok=1"
        )
        self.assertNotIn("bearer-secret", message)
        self.assertNotIn("deploy-secret", message)
        self.assertNotIn("query-secret", message)
        self.assertIn("ok=1", message)

    def test_cancelled_directory_size_does_not_publish_partial_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(20):
                (root / f"file-{index}").write_bytes(b"x")
            calls = [0]

            def cancel_after_first_poll() -> bool:
                calls[0] += 1
                return calls[0] > 1

            self.assertEqual(get_dir_size(str(root), use_cache=False, cancel_callback=cancel_after_first_poll), 0)
            self.assertIsNone(peek_dir_size(str(root)))


class QtWorkerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Use QApplication because the same process may also run widget tests.
        # A QCoreApplication cannot later be upgraded to QApplication.
        cls.app = QApplication.instance() or QApplication([])

    def test_disk_size_worker_stops_after_interruption(self):
        with patch(
            "ui.threads.get_dir_size",
            side_effect=lambda path, use_cache=False, cancel_callback=None: 0
            if cancel_callback and cancel_callback()
            else 0,
        ):
            worker = DiskSizeFetcherThread(1, tempfile.gettempdir())
            worker.start()
            worker.requestInterruption()
            self.assertTrue(worker.wait(2000))
            self.assertFalse(worker.isRunning())


if __name__ == "__main__":
    unittest.main()
