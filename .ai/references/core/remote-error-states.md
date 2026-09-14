# Core Reference: Remote Error Categories and Resource States

[`core/request_contracts.py`](../../../core/request_contracts.py) is the
transport-neutral contract for remote failures. `classify_remote_error()`
normalizes HTTP statuses and adapter codes into these categories:

- `offline`
- `timeout`
- `rate_limited`
- `transient`
- `authentication_required`
- `permission_denied`
- `conflict`
- `validation`
- `cancelled`
- `unavailable`
- `unexpected`

`ResourceResult.error_category` is the stable value consumed by UI,
diagnostics, and tests. `resource_status_for_error()` maps categories that
need distinct recovery behavior to `ResourceStatus` values:

- `OFFLINE`
- `AUTHENTICATION_REQUIRED`
- `PERMISSION_DENIED`
- `CONFLICT`
- `UNAVAILABLE`
- `CANCELLED`
- otherwise `ERROR` (with the category preserved)

Cloud operation lifecycle records and exit-sync results also normalize legacy
operation labels such as `backend_unavailable` to this vocabulary. They carry
only redacted metadata; save contents and response payloads are never stored.

When stale cached data exists, `RequestManager` keeps it as the current
`STALE` state and attaches the categorized refresh failure. This lets the UI
continue rendering usable data while still distinguishing authentication,
backend availability, conflict, and transient failures.

Transport clients may retain their legacy `code` fields for compatibility,
but should expose `status_code` or `category` so the shared classifier can
produce the common result. Credentials, response bodies, and save contents
must never be included in a category, key, metric, or log payload.
