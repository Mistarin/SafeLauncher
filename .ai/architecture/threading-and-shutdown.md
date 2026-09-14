# Threading and Shutdown

Qt UI objects live on the Qt thread. `RequestManager` workers are Qt-free daemon workers and communicate through listeners; `ResourceBinding` queues results back to Qt. `SafeQThread` and `TaskSupervisor` support feature workers that cannot yet be migrated or are intentionally standalone.

Cancellation is cooperative: queued tasks can be removed or marked cancelled, and loaders should check tokens. Late results are rejected by cancellation/generation checks. Shutdown must stop accepting new work, close bindings, stop feature workers, close clients, and wait within the application shutdown policy.

The strong ownership of thread wrappers until supervisor shutdown is intentional. Removing that retention can reintroduce late `deleteLater`/signal races during Qt teardown.

Sources: [`core/request_manager.py`](../../core/request_manager.py), [`core/safe_thread.py`](../../core/safe_thread.py), [`ui/resource_binding.py`](../../ui/resource_binding.py), [`ui/main_window.py`](../../ui/main_window.py).
