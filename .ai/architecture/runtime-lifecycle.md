# Runtime Lifecycle

## Startup

`main.py` applies CLI offline mode, handles doctor/setup/desktop commands, initializes the environment, creates `QApplication`, checks single-instance state, verifies dependencies, creates the database and runner, and opens `MainWindow`.

`MainWindow` creates shared clients and services before presenting the library. Local database data is available before optional Steam, artwork, achievement, and cloud refreshes.

## Active runtime

Views query a local `LibrarySnapshot`, then request optional resources through the application-scoped manager. Bindings marshal manager results onto the Qt thread and are closed when their page or resource context is no longer valid.

## Shutdown

The window closes page bindings and active feature work, finalizes relevant session/playtime state, closes transport clients, logs request/performance metrics, shuts down the request manager, and lets Qt finish destruction. `SafeQThread`/`TaskSupervisor` retain worker wrappers long enough to avoid late signal/destruction races.

Sources: [`main.py`](../../main.py), [`ui/main_window.py`](../../ui/main_window.py), [`core/safe_thread.py`](../../core/safe_thread.py).
