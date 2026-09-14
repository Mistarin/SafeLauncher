# System Overview

SafeLauncher is a single-process PyQt6 application with a local-first library model. `main.py` handles CLI modes and creates the application; `MainWindow` composes the database, transport clients, request/resource services, UI views, and feature coordinators.

## Major planes

1. **Presentation:** `ui/main_window.py`, components, dialogs, Qt signals.
2. **Application coordination:** library state, request/resource manager, cloud/achievement/save coordinators.
3. **Domain/runtime:** game records, sessions, playtime, archives, sandbox execution.
4. **Transport:** private cloud, public profile, Steam, SteamGridDB, authentication.
5. **Persistence:** SQLite, XDG cache/data/config files, encrypted save archives.

The application intentionally does not make the remote services the immediate UI source of truth. Remote data is reconciled into local state or exposed as managed resources depending on its nature.

Sources: [`main.py`](../../main.py), [`ui/main_window.py`](../../ui/main_window.py), [`database.py`](../../database.py).
