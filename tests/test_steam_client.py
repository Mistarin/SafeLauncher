"""Transport-level tests for Steam metadata parsing."""

from __future__ import annotations

import unittest

from core.request_contracts import RetryPolicy
from core.steam_client import SteamClient, SteamClientError


class _Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.closed = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class _Session:
    def __init__(self):
        self.headers = {}
        self.calls = []
        self.responses = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class SteamClientTests(unittest.TestCase):
    def test_tags_and_build_are_parsed_by_transport(self):
        session = _Session()
        session.responses = [
            _Response({"items": [{"id": 123}]}),
            _Response({"123": {"data": {
                "genres": [{"description": "Action"}],
                "categories": [{"description": "Single-player"}, {"description": "Action"}],
            }}}),
            _Response({"data": {"123": {"depots": {"branches": {
                "public": {"buildid": "456", "timeupdated": "789"}
            }}}}}),
        ]
        client = SteamClient(session)
        self.assertEqual(client.tags_for_game("Example"), (["Action", "Single-player"], "123"))
        self.assertEqual(client.public_build("123"), ("456", 789))
        self.assertEqual(len(session.calls), 3)

    def test_http_status_is_available_to_retry_policy(self):
        session = _Session()
        session.responses = [_Response({}, status_code=503)]
        client = SteamClient(session)
        with self.assertRaises(SteamClientError) as raised:
            client.public_build("123")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertTrue(RetryPolicy().should_retry(raised.exception))

        session.responses = [_Response({}, status_code=401)]
        with self.assertRaises(SteamClientError) as raised:
            client.public_build("123")
        self.assertFalse(RetryPolicy().should_retry(raised.exception))


if __name__ == "__main__":
    unittest.main()
