"""Tests for the request/resource contracts used by the request manager."""

from __future__ import annotations

import unittest

from core.request_contracts import (
    CancellationToken,
    RequestKey,
    RequestCancelled,
    RequestPriority,
    RequestSpec,
    ResourceResult,
    ResourceState,
    ResourceStatus,
    RetryPolicy,
    RetryableRequestError,
    is_transient_error,
)


class RequestContractTests(unittest.TestCase):
    def test_request_key_is_stable_and_validated(self):
        key = RequestKey("artwork", "steam:123", "hero:wide")
        self.assertEqual(key.cache_key(), "artwork:steam%3A123:hero%3Awide")
        with self.assertRaises(ValueError):
            RequestKey("", "123")
        with self.assertRaises(ValueError):
            RequestKey("artwork", "")

    def test_request_spec_has_safe_defaults(self):
        spec = RequestSpec(RequestKey("library", "account"), lambda token: "ok")
        self.assertEqual(spec.priority, RequestPriority.NORMAL)
        self.assertEqual(spec.retry_policy.max_attempts, 3)
        self.assertEqual(spec.generation, 0)

    def test_cancellation_token_is_cooperative(self):
        token = CancellationToken()
        self.assertFalse(token.cancelled)
        token.raise_if_cancelled()
        token.cancel()
        self.assertTrue(token.cancelled)
        with self.assertRaises(RequestCancelled):
            token.raise_if_cancelled()

    def test_resource_result_reports_usable_cached_states(self):
        key = RequestKey("library", "account")
        ready = ResourceResult(key, ResourceStatus.READY, value={"games": []})
        stale = ResourceResult(key, ResourceStatus.STALE, value={"games": []}, from_cache=True)
        loading = ResourceResult(key, ResourceStatus.LOADING)
        self.assertTrue(ready.usable)
        self.assertTrue(stale.usable)
        self.assertFalse(loading.usable)

    def test_retry_policy_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            RetryPolicy(max_attempts=0)
        with self.assertRaises(ValueError):
            RetryPolicy(base_delay_seconds=2, max_delay_seconds=1)
        with self.assertRaises(ValueError):
            RetryPolicy(jitter_ratio=2)

    def test_retry_policy_classifies_transient_failures(self):
        self.assertTrue(is_transient_error(TimeoutError("timed out")))
        self.assertTrue(is_transient_error(RetryableRequestError("try again")))
        self.assertFalse(is_transient_error(ValueError("bad payload")))

        class HttpFailure(RuntimeError):
            status_code = 503

        class AuthFailure(RuntimeError):
            status_code = 401

        self.assertTrue(is_transient_error(HttpFailure("busy")))
        self.assertFalse(is_transient_error(AuthFailure("unauthorized")))
        self.assertTrue(RetryPolicy().should_retry(HttpFailure("busy")))
        self.assertFalse(RetryPolicy().should_retry(AuthFailure("unauthorized")))

    def test_request_timeout_must_be_positive(self):
        with self.assertRaises(ValueError):
            RequestSpec(RequestKey("data", "timeout"), lambda _token: None, timeout_seconds=0)

    def test_resource_state_is_the_public_alias(self):
        self.assertIs(ResourceState, ResourceStatus)


if __name__ == "__main__":
    unittest.main()
