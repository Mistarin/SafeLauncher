# Achievements and Playtime

Achievement schema resolution combines local files, Lanzador-compatible sources, Steam/community sources, and optional authenticated state. Providers return normalized resolution data with provenance and availability. Local unlock observations are persisted first and can later be reconciled into account-wide profile metadata.

Achievement file watching is local and event-driven. `AchievementResourceService`
owns manager-backed status resolution, worker database lifetimes, batch
submission, and invalidation. `SafeQThread` achievement workers remain
compatibility paths for manager-less callers.

Playtime is recorded locally through game sessions and projected into private account metadata. Session merging is designed to preserve account-wide history across devices.

Achievement request grouping for the library is delegated to the Qt-free
[`LibraryAchievementCoordinator`](../../core/library_achievement_coordinator.py).
It owns stable callback identity, managed binding deduplication, and batch
lifecycle state. `AchievementResourceService` remains responsible for request
specification, local database worker lifetime, provider resolution, and
persistence. MainWindow remains the UI edge for status-cache projection,
watchers, and inspector rendering.

The transient achievement presentation projection is owned by the Qt-free
[`AchievementStateStore`](../../core/achievement_state_store.py). It holds
status tuples, resolution objects, freshness timestamps, and unlock events
waiting for a schema. It is not a second durable authority: SQLite remains the
source of truth and the store is cleared when a game leaves the local library.

Sources: [`core/achievement_resource_service.py`](../../core/achievement_resource_service.py), [`core/library_achievement_coordinator.py`](../../core/library_achievement_coordinator.py), [`core/achievement_providers.py`](../../core/achievement_providers.py), [`core/achievement_coordinator.py`](../../core/achievement_coordinator.py), [`core/achievement_watcher.py`](../../core/achievement_watcher.py), [`core/playtime_tracker.py`](../../core/playtime_tracker.py), [`database.py`](../../database.py).
