# UI Architecture

`MainWindow` is the composition root and long-lived owner of the shared request manager, cache, database, transports, and most feature lifecycles. Library views render snapshots produced by the library controller/state layer. Components and dialogs receive focused data and callbacks.

Remote resources are loaded through stable keys. `ResourceBinding` is the safe Qt bridge: it subscribes to the Qt-free manager, receives results on a queued signal, and closes/unsubscribes when the owner disappears. Direct manager callbacks must not mutate widgets because they execute outside the UI thread.

Compatibility dialogs may still use `SafeQThread` workers when no manager is supplied. New production paths should use the shared manager and keep widget lifetime/account context in the binding owner.

Sources: [`ui/main_window.py`](../../ui/main_window.py), [`ui/resource_binding.py`](../../ui/resource_binding.py), [`core/library_controller.py`](../../core/library_controller.py), [`ui/threads.py`](../../ui/threads.py).
