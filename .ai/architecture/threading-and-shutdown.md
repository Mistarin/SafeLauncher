# Threading and Shutdown

Qt UI objects live on the Qt thread. `RequestManager` workers are Qt-free daemon workers and communicate through listeners; `ResourceBinding` queues results back to Qt. `SafeQThread` and `TaskSupervisor` support feature workers that cannot yet be migrated or are intentionally standalone.

`WorkerSupervisor` is the single MainWindow-owned registry for QThread shutdown.
Feature-level lists such as `metadata_fetchers` and `auto_fetchers` are only
compatibility indexes and must not become a second lifetime registry.

Compatibility workers that receive a `RequestManager` delegate their
manager-backed branches to the shared resource services (`CloudStatusService`
and `AchievementResourceService`). Their local executors remain only for
manager-less embedding and legacy callers.

Cancellation is cooperative: queued tasks can be removed or marked cancelled, and loaders should check tokens. Late results are rejected by cancellation/generation checks. Shutdown must stop accepting new work, close bindings, stop feature workers, close clients, and wait within the application shutdown policy.

The strong ownership of thread wrappers until supervisor shutdown is intentional. Removing that retention can reintroduce late `deleteLater`/signal races during Qt teardown.

MainWindow's `closeEvent()` uses the same cooperative boundary in the live UI:
it stops recurring work, requests cancellation, waits in short bounded slices,
and re-enters through the event loop while a worker remains. A visible progress
dialog lets the user keep the launcher open if the safe deadline is exceeded;
unsafe `QThread.terminate()` is deliberately avoided. Regression coverage
includes active/queued request cancellation and a cooperative slow-worker reap.

Sources: [`core/request_manager.py`](../../core/request_manager.py), [`core/safe_thread.py`](../../core/safe_thread.py), [`ui/resource_binding.py`](../../ui/resource_binding.py), [`ui/main_window.py`](../../ui/main_window.py).
