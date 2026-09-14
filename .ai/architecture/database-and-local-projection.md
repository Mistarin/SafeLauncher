# Database and Local Projection

`GameDatabase` opens the XDG data database, performs integrity checking and backup recovery, initializes/migrates the schema, and exposes domain operations. It uses SQLite WAL where available and protects shared schema initialization with a process lock.

Important tables:

- `games`: library records, paths, launch data, Steam IDs, playtime, favorite, collection, archive/install state, build metadata, and environment configuration.
- `collections`: local collection names.
- `achievements`: per-game schema and observed unlock state.
- `achievement_profile`: account-wide append-only achievement projection and provenance.
- `profile_games`: account-wide profile library metadata.
- `playtime_sessions`: session-level playtime reconciliation.

The database can materialize cloud-only records and later update their local installation/path fields when a game is installed. Database methods are the preferred write boundary for local projections.

Sources: [`database.py`](../../database.py), [`core/library_controller.py`](../../core/library_controller.py), [`core/library_state.py`](../../core/library_state.py).
