# Database and Local Projection

`GameDatabase` remains the public compatibility facade. `DatabaseSession` in
`core/local_database/connection.py` owns integrity checks, WAL-safe backup
recovery, the native connection and idempotent close. `SchemaMigrator` owns
schema initialization/migration and fails visibly rather than presenting an
empty library when migration fails. Constructor failure closes the connection.

Game, profile, playtime and achievement repositories share that session.
`IdentityReconciler` receives game/profile repositories explicitly; it retains
the existing duplicate-repair rules and dependent ledgers. Facade methods keep
their signatures and serialize whole repository calls under one reentrant
session lock, including read/modify/write sequences. Cross-connection schema
and achievement operations still use the existing process-wide lock.
The legacy raw `conn` property is available for compatibility/diagnostics;
direct external SQL does not receive facade serialization guarantees.

Important tables:

- `games`: library records, paths, launch data, Steam IDs, playtime, favorite, collection, archive/install state, build metadata, and environment configuration.
- `collections`: local collection names.
- `achievements`: per-game schema and observed unlock state.
- `achievement_profile`: account-wide append-only achievement projection and provenance.
- `profile_games`: account-wide profile library metadata.
- `playtime_sessions`: session-level playtime reconciliation.

The database can materialize cloud-only records and later update their local installation/path fields when a game is installed. Database methods are the preferred write boundary for local projections.

Sources: [`database.py`](../../database.py), [`core/library_controller.py`](../../core/library_controller.py), [`core/library_state.py`](../../core/library_state.py).
