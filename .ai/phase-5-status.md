# Phase 5: Achievement, Steam, and artwork resource services

Status: implemented on 2026-09-14.

## Scope completed

- Added `AchievementResourceService` for manager-backed achievement resolution,
  local persistence, worker database lifecycle, batch submission, and explicit
  invalidation.
- Added `SteamResourceService` for cached public build and store-tag resources.
  Build requests deduplicate by AppID; tag requests deduplicate by normalized
  game name.
- Added `ArtworkResourceService` for cached portrait/icon, hero, and icon
  resources with separate stable resource types and priorities.
- MainWindow now obtains achievement, Steam, and artwork loaders from these
  Qt-free services. UI code retains bindings, generation checks, and database
  projection updates only.
- Existing `SafeQThread` fetchers remain compatibility paths for manager-less
  dialogs, tests, and older integrations.
- Cloud status already uses `CloudStatusService`; no public profile or private
  cloud schema was changed in this phase.

## Verification

- Added 3 focused resource-service tests.
- Full unit suite: 125 passed.
- Smoke suite: core, cloud, achievements, and UI passed.
- Python compilation passed.

## Remaining work

Phase 6 should move more widgets from feature-local callback bookkeeping to
shared `ResourceBinding` subscriptions, then Phase 7 can remove compatibility
workers only after manager-less callers are audited.
