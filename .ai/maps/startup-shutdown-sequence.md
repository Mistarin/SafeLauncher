# Startup and Shutdown Sequence

```text
CLI args
 → environment/bootstrap
 → QApplication
 → single-instance check
 → dependency check
 → database/runner/backup
 → MainWindow
 → shared clients/cache/request manager
 → local library render
 → managed optional resources
```

```text
close request
 → stop/close page bindings and transient UI
 → finish/stop game/session and feature workers
 → close profile/cloud/Steam/artwork clients
 → log request/performance metrics
 → RequestManager.shutdown
 → Qt teardown
```

The exact ordering is source-controlled in `MainWindow.closeEvent` and should be rechecked whenever a long-lived client or worker is added.
