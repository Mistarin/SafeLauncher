# Storage Reference: SQLite

[`database.py`](../../../database.py) owns database path selection, migrations, integrity checks, backup recovery, tables, indexes, and domain operations. Default storage follows XDG data conventions. The database may fall back to memory only after persistent recovery fails; this should be visible through diagnostics.
