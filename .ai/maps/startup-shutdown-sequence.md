# Startup and Shutdown Sequence

```text
CLI args
 → environment/bootstrap
 → QApplication
 → single-instance check and listener reservation
 → dependency check
 → database/runner/backup
 → ApplicationRuntime shared clients/cache/request manager
 → MainWindow views and feature controllers
 → local library render
 → managed optional resources
```

```text
close request
 → ShutdownController draining state; stop producers
 → finish/stop game/session and feature workers
 → wait for Qt workers and managed loaders/rollback
 → dispose feature bindings
 → log request/performance metrics
 → RequestManager.shutdown then close transports
 → Qt teardown
```

The ordering is implemented by `ShutdownController`, MainWindow lifecycle hooks, and `ApplicationRuntime.close_resources`. A cancelled/overdue drain resumes the window and invalidates queued close retries instead of destroying active workers.
