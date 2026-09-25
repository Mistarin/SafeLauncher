import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import requests

from core.cloud_backend import check_backend_health
from core.version import MIN_CONVEX_BACKEND_VERSION


class _HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        self.server.seen.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "safe_launcher_key": self.headers.get("X-SafeLauncher-Key"),
            }
        )
        status, body = self.server.routes.get(self.path, (404, {}))
        if isinstance(body, bytes):
            payload = body
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        else:
            payload = json.dumps(body).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


class _HealthServer(ThreadingHTTPServer):
    allow_reuse_address = True


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class CloudBackendHealthTests(unittest.TestCase):
    def setUp(self):
        self.server = _HealthServer(("127.0.0.1", 0), _HealthHandler)
        self.server.routes = {}
        self.server.seen = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_connected_probe_uses_auth_headers_and_reports_latency(self):
        self.server.routes["/api/health"] = (200, {"version": MIN_CONVEX_BACKEND_VERSION})

        result = check_backend_health(self.endpoint + "/", "test-secret", timeout=1.5)

        self.assertTrue(result["healthy"])
        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["version"], MIN_CONVEX_BACKEND_VERSION)
        self.assertFalse(result["is_outdated"])
        self.assertGreaterEqual(result["latency_ms"], 1)
        self.assertIsNone(result["error"])
        self.assertEqual(result["min_version"], MIN_CONVEX_BACKEND_VERSION)
        self.assertEqual(
            self.server.seen,
            [
                {
                    "path": "/api/health",
                    "authorization": "Bearer test-secret",
                    "safe_launcher_key": "test-secret",
                }
            ],
        )

    def test_probe_normalizes_convex_cloud_to_convex_site(self):
        response = _Response(200, {"version": MIN_CONVEX_BACKEND_VERSION})
        with patch("core.cloud_backend.requests.get", return_value=response) as get:
            result = check_backend_health(
                "https://deployment.eu-west-1.convex.cloud/",
                "test-secret",
            )

        self.assertEqual(result["status"], "connected")
        get.assert_called_once_with(
            "https://deployment.eu-west-1.convex.site/api/health",
            headers={
                "Authorization": "Bearer test-secret",
                "X-SafeLauncher-Key": "test-secret",
            },
            timeout=5.0,
        )
        self.assertTrue(response.closed)

    def test_missing_health_version_falls_back_to_version_endpoint(self):
        self.server.routes["/api/health"] = (200, {"healthy": True})
        self.server.routes["/api/version"] = (200, {"version": "1.8.0"})

        result = check_backend_health(self.endpoint, "test-secret")

        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["version"], "1.8.0")
        self.assertFalse(result["is_outdated"])
        self.assertEqual(
            [request["path"] for request in self.server.seen],
            ["/api/health", "/api/version"],
        )
        for request in self.server.seen:
            self.assertEqual(request["authorization"], "Bearer test-secret")
            self.assertEqual(request["safe_launcher_key"], "test-secret")

    def test_malformed_health_and_version_responses_use_safe_fallback(self):
        self.server.routes["/api/health"] = (200, b"not-json")
        self.server.routes["/api/version"] = (200, b"also-not-json")

        result = check_backend_health(self.endpoint, "test-secret")

        self.assertTrue(result["healthy"])
        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["version"], "1.0.0")
        self.assertTrue(result["is_outdated"])
        self.assertEqual(
            [request["path"] for request in self.server.seen],
            ["/api/health", "/api/version"],
        )

    def test_legacy_backend_is_healthy_but_identified_as_legacy(self):
        self.server.routes["/api/health"] = (404, {"error": "not found"})

        result = check_backend_health(self.endpoint, "test-secret")

        self.assertTrue(result["healthy"])
        self.assertEqual(result["status"], "legacy")
        self.assertEqual(result["version"], "1.0.0")
        self.assertIn("not implemented", result["error"])
        self.assertEqual([request["path"] for request in self.server.seen], ["/api/health"])

    def test_auth_and_server_failures_have_distinct_statuses(self):
        for status_code, expected_status in ((401, "unauthorized"), (403, "unauthorized"), (503, "error")):
            with self.subTest(status_code=status_code):
                self.server.seen.clear()
                self.server.routes["/api/health"] = (status_code, {})

                result = check_backend_health(self.endpoint, "test-secret")

                self.assertFalse(result["healthy"])
                self.assertEqual(result["status"], expected_status)
                self.assertIn(str(status_code), result["error"])
                self.assertEqual([request["path"] for request in self.server.seen], ["/api/health"])

    def test_empty_endpoint_is_unconfigured_without_transport_call(self):
        with patch("core.cloud_backend.requests.get") as get:
            result = check_backend_health("")

        self.assertFalse(result["healthy"])
        self.assertEqual(result["status"], "unconfigured")
        self.assertEqual(result["latency_ms"], -1)
        self.assertEqual(result["version"], "unknown")
        self.assertIn("No Convex site URL", result["error"])
        get.assert_not_called()

    def test_transport_exception_is_reported_as_unreachable(self):
        with patch(
            "core.cloud_backend.requests.get",
            side_effect=requests.Timeout("timed out"),
        ) as get:
            result = check_backend_health(self.endpoint, "test-secret", timeout=0.25)

        self.assertFalse(result["healthy"])
        self.assertEqual(result["status"], "unreachable")
        self.assertEqual(result["latency_ms"], -1)
        self.assertIn("timed out", result["error"])
        get.assert_called_once_with(
            f"{self.endpoint}/api/health",
            headers={
                "Authorization": "Bearer test-secret",
                "X-SafeLauncher-Key": "test-secret",
            },
            timeout=0.25,
        )

    def test_health_and_version_response_objects_are_always_closed(self):
        health = _Response(200, {})
        version = _Response(200, {"version": "1.8.0"})
        with patch(
            "core.cloud_backend.requests.get",
            side_effect=(health, version),
        ) as get:
            result = check_backend_health("https://backend.invalid", "test-secret")

        self.assertEqual(result["version"], "1.8.0")
        self.assertTrue(health.closed)
        self.assertTrue(version.closed)
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[1].kwargs["timeout"], 2.0)


if __name__ == "__main__":
    unittest.main()
