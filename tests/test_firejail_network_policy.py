"""Regression checks for the documented runner network policy."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.firejail_runner import FirejailSandboxRunner, _sandbox_name_for_path


class FirejailNetworkPolicyTests(unittest.TestCase):
    def test_sandbox_name_cannot_end_with_separator_after_truncation(self):
        name = _sandbox_name_for_path(
            "/home/martin/Games/Sandbox/Graveyard-Keeper-2-SteamRIP"
        )
        self.assertEqual(name, "safelauncher-graveyard-ke-d23485")
        self.assertLessEqual(len(name), 32)
        self.assertNotRegex(name, r"[-_]$")

    def test_short_sandbox_name_remains_readable(self):
        self.assertEqual(
            _sandbox_name_for_path("/tmp/Ender-Magnolia"),
            "safelauncher-ender-magnolia",
        )

    def test_umu_standard_keeps_host_networking_for_umu_loopback(self):
        with tempfile.TemporaryDirectory() as game_path:
            executable = os.path.join(game_path, "game.exe")
            with open(executable, "w", encoding="utf-8"):
                pass
            process = SimpleNamespace(pid=1234)
            with patch.object(
                FirejailSandboxRunner,
                "check_dependencies",
                return_value={"firejail": True, "umu-run": True, "wine": True, "gamescope": False},
            ), patch("core.firejail_runner.subprocess.Popen", return_value=process) as popen, patch(
                "core.firejail_runner.sanitize_wine_prefix"
            ), patch("core.firejail_runner.cleanup_prefix_health"):
                FirejailSandboxRunner().launch(game_path, "game.exe", "umu", steam_id="480")

            command = popen.call_args.args[0][2]
            self.assertIn("umu-run", command)
            self.assertNotIn("--net=none", command)


if __name__ == "__main__":
    unittest.main()
