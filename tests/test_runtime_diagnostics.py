"""Security and contract tests for support-safe runtime diagnostics."""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path

from core.runtime_diagnostics import build_runtime_diagnostics, export_runtime_diagnostics


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_report_allowlists_numeric_metrics_and_omits_sensitive_values(self):
        report = build_runtime_diagnostics(
            app_version="9.9.9",
            request_metrics={
                "submitted": 4,
                "workers_peak": 3,
                "secret": "do-not-export",
                "resource_value": "/home/user/private/game",
            },
            performance_metrics={
                "requests_cache_hits": 2,
                "requests_errors": 1,
                "game_path": "/private/path",
            },
            offline=True,
            generated_at=123.0,
        )
        encoded = json.dumps(report)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["generated_at_unix"], 123.0)
        self.assertEqual(report["network"]["offline"], True)
        self.assertEqual(report["requests"]["submitted"], 4)
        self.assertEqual(report["requests"]["workers_peak"], 3)
        self.assertEqual(report["requests"]["cache_hits"], 2)
        self.assertEqual(report["requests"]["errors"], 1)
        self.assertNotIn("do-not-export", encoded)
        self.assertNotIn("/home/user/private/game", encoded)
        self.assertNotIn("/private/path", encoded)

    def test_export_is_atomic_json_and_user_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.json"
            exported = export_runtime_diagnostics(
                path,
                build_runtime_diagnostics(generated_at=456.0),
            )
            self.assertEqual(exported, str(path))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["generated_at_unix"], 456.0)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
