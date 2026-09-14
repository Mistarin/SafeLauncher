# Database Schema Map

The authoritative schema is created and migrated in [`database.py`](../../database.py). Current conceptual tables:

```text
games
 ├─ local launch/install metadata
 ├─ favorite/archive/build/collection state
 └─ playtime summary
collections
achievements
achievement_profile
profile_games
playtime_sessions
```

Use [`generated/database-schema.json`](../generated/database-schema.json) for extracted columns, indexes, and schema statements. Use `GameDatabase` methods for reads/writes and preserve migration compatibility.
