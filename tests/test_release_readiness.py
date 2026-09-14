"""Tests for the isolated release-readiness command composition."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ci.release_readiness import gate_steps, isolated_environment


class ReleaseReadinessTests(unittest.TestCase):
    def test_gate_has_ordered_checks_and_explicit_offline_performance_guard(self):
        steps = gate_steps(python_executable="python-test", smoke_timeout_seconds=17)
        self.assertEqual(
            [step.name for step in steps],
            [
                "compile",
                "unit tests",
                "smoke phases",
                "full harness",
                "security audit",
                "worker audit",
                "offline performance guard",
                "build AI manifest",
                "validate AI cache",
                "diff check",
            ],
        )
        self.assertIn("--max-render-ms", steps[6].command)
        self.assertIn("--max-workers-peak", steps[6].command)
        self.assertIn("17", steps[2].command)

    def test_environment_isolated_from_existing_xdg_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = isolated_environment(
                root,
                {
                    "XDG_DATA_HOME": "/developer/data",
                    "XDG_CONFIG_HOME": "/developer/config",
                    "XDG_CACHE_HOME": "/developer/cache",
                    "SAFELAUNCHER_GATE_TEST_SECRET": "must-remain-unlogged",
                },
            )
            self.assertNotEqual(environment["XDG_DATA_HOME"], "/developer/data")
            self.assertNotEqual(environment["XDG_CONFIG_HOME"], "/developer/config")
            self.assertNotEqual(environment["XDG_CACHE_HOME"], "/developer/cache")
            self.assertEqual(environment["QT_QPA_PLATFORM"], "offscreen")
            self.assertEqual(environment["SAFELAUNCHER_OFFLINE_TEST_MODE"], "1")
            self.assertNotIn("SAFELAUNCHER_GATE_TEST_SECRET", environment)
            self.assertTrue(Path(environment["XDG_DATA_HOME"]).is_dir())


if __name__ == "__main__":
    unittest.main()
