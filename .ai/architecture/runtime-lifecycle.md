# Runtime Lifecycle

## Startup

`main.py` applies CLI offline mode and dispatches terminal commands through `core.cli` before importing GUI bootstrap. `ui.application_entrypoint` creates Qt, reserves the single-instance listener before starting application work, verifies dependencies, and constructs the database/runner/backup and shared `ApplicationRuntime` before opening `MainWindow`.

`ApplicationRuntime` owns the shared clients, services, cache, state stores, request manager, worker supervisor, and operation registry. MainWindow accepts an injected runtime or creates a compatibility runtime for its historical three-argument constructor. Caller-supplied databases are not closed by the runtime; GUI bootstrap closes its own database. Partial runtime construction unwinds created resources. Local database data precedes optional remote refreshes.

## Active runtime

Views query a local `LibrarySnapshot`, then request optional resources through the application-scoped manager. Bindings marshal manager results onto the Qt thread and are closed when their page or resource context is no longer valid.

## Shutdown

`ShutdownController` owns running/draining/cancelled/closed transitions, deadlines, and generation-safe Qt re-entry. Window hooks stop producers and request cancellation. The drain barrier includes supervised QThreads and RequestManager queued/running work, including invalidated loaders still unwinding. Timeout or user cancellation resumes observation without closing services. After draining, feature bindings are disposed, requests are shut down, and only then are transports closed. `ManagedTaskController` suppresses late callbacks and retains cancelled handles while local save commits/rollbacks finish.

Sources: [`main.py`](../../main.py), [`ui/application_entrypoint.py`](../../ui/application_entrypoint.py), [`ui/application_runtime.py`](../../ui/application_runtime.py), [`ui/shutdown_controller.py`](../../ui/shutdown_controller.py), [`ui/managed_task_controller.py`](../../ui/managed_task_controller.py), [`core/safe_thread.py`](../../core/safe_thread.py).
