# Core Reference: Achievements and Stats

The achievement subsystem is split into models/providers/schema resolution, local file watching, persistence, coordination, and database projection. Start with [`achievement_models.py`](../../../core/achievement_models.py), [`achievement_providers.py`](../../../core/achievement_providers.py), [`achievement_schema.py`](../../../core/achievement_schema.py), [`achievement_watcher.py`](../../../core/achievement_watcher.py), [`achievement_persistence.py`](../../../core/achievement_persistence.py), and [`achievement_coordinator.py`](../../../core/achievement_coordinator.py).

Local observations remain useful when remote schemas or network state are unavailable. Provenance and validation must not be weakened by a later unverified observation.
