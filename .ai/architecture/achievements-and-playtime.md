# Achievements and Playtime

Achievement schema resolution combines local files, Lanzador-compatible sources, Steam/community sources, and optional authenticated state. Providers return normalized resolution data with provenance and availability. Local unlock observations are persisted first and can later be reconciled into account-wide profile metadata.

Achievement file watching is local and event-driven. Batch status/schema work can be manager-backed, while `SafeQThread` achievement workers remain compatibility paths.

Playtime is recorded locally through game sessions and projected into private account metadata. Session merging is designed to preserve account-wide history across devices.

Sources: [`core/achievement_providers.py`](../../core/achievement_providers.py), [`core/achievement_coordinator.py`](../../core/achievement_coordinator.py), [`core/achievement_watcher.py`](../../core/achievement_watcher.py), [`core/playtime_tracker.py`](../../core/playtime_tracker.py), [`database.py`](../../database.py).
