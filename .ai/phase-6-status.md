# Phase 6: UI resource-state subscriptions

Status: implemented on 2026-09-14.

## Scope completed

- Extended `ResourceBinding` with request-id and generation filters.
- Added `bind_request()` for short-lived dialog operations that already have
  a `RequestHandle`; it delivers only that request's terminal result on the
  owning Qt thread.
- Migrated manager-backed achievement resolution in `AchievementsDialog` to
  `AchievementResourceService` and request-scoped binding.
- Migrated cloud operation delivery in Save Manager and Game Properties from
  raw Future callbacks to request-scoped bindings.
- Migrated profile page, Friends, Account, and Settings manager-backed remote
  work to the same binding path.
- Closing or hiding these surfaces detaches bindings and cooperatively cancels
  owned requests; compatibility `TaskSupervisor` workers remain for callers
  without a shared RequestManager.
- MainWindow library, cloud status, artwork, Steam metadata, and public profile
  paths already use resource bindings and retain their existing generation and
  context checks.

## Verification

- Added request-binding generation filtering coverage.
- Full unit suite: 126 passed.
- Smoke suite: core, cloud, achievements, and UI passed.
- Python compilation passed.

## Remaining work

Phase 7 can now audit and remove redundant compatibility workers and feature
local in-flight maps one production path at a time. Keep compatibility wrappers
until manager-less integrations and plugin callers are explicitly verified.
