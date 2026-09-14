# Manager Ownership Map

| Manager/coordinator | Created/owned by | Coordinates | Persistence/transport it uses |
|---|---|---|---|
| `RequestManager` | `MainWindow` | all managed remote resource work | loaders and `ResourceCache` |
| `ResourceCache` | `MainWindow` | reusable resource values | XDG cache directory |
| `LibraryController`/`LibraryStateStore` | `MainWindow`/library UI | local filtering and snapshots | `GameDatabase` |
| `CloudMetadataSync` | sync call sites | private profile/library merge | `GameDatabase`, `CloudClient` |
| `CloudSaveSyncEngine` | cloud save coordinators | save status/upload/restore | local saves, `CloudClient` |
| `PerformanceMetrics` | `MainWindow` | resource/render timing | logs/diagnostics |
| `TaskSupervisor` | runtime/UI owners | compatibility thread lifetime | `SafeQThread` workers |

Do not introduce a feature-local executor for a resource that can use the shared manager.
