# Phase 9: Production reliability and observability

Status: implemented for the client architecture on 2026-09-14.

## Verified

- Remote failures use `RemoteErrorCategory` and are mapped to explicit
  resource states, including offline, timeout, unavailable, authentication,
  permission, conflict, cancellation, and unexpected failure.
- Managed request metadata carries request ID, opaque context identity,
  generation, operation tag, and retry information without credentials or
  private payloads.
- Cloud operation records and exit-sync presentation results are metadata-only
  and preserve local data on failed remote work.
- Runtime diagnostics use an explicit allowlist and atomic, owner-readable
  export files. They contain metrics and redacted identities only.
- Cloud Center and existing remote UI surfaces render usable cached, stale,
  offline, unavailable, authentication-required, conflict, and error states.

## Verification

- Full unit suite passed.
- Full offline smoke suite passed.
- Security boundary audit passed, including reachable Git history.
- Worker audit passed with no feature-local executor findings.
- Runtime diagnostics and redaction tests passed.

## External release check

Live backend latency, rate limits, and production outage behavior still require
an operator-controlled staging deployment. The local gate intentionally does
not read credentials or contact production services.
