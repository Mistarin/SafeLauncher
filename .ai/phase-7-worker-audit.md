# Phase 7: Worker and executor audit

Status: complete. The normal desktop path has one remote request scheduler:
`RequestManager`, and no feature-local executor remains under `core/` or
`ui/`. Cloud UI call sites are service-only. QThreads that remain are
compatibility wrappers, local process/file workers, or explicit control/listener
threads.

## Decision rules

- Remote library/resource work must go through `RequestManager`.
- A compatibility worker may remain for a plugin, embedded host, or older
  integration, but cloud-save bridges require a manager and refuse manager-less
  execution. It must switch to the managed path when a manager is supplied and
  must not create a second executor.
- Explicit mutations and downloads (application update, Proton bootstrap,
  archive extraction, save restore) are operations, not cacheable resources;
  their workers may remain bounded and independently cancellable.
- Local file/process helpers must not become hidden network schedulers.
- Every QThread remains owned by `WorkerSupervisor` when created by
  `MainWindow`; semantic lists are indexes only.

## Executor inventory

| Location | Mechanism | Current call path | Classification | Decision |
|---|---|---|---|---|
| `core/request_manager.py` | bounded `threading.Thread` workers | all managed remote resources | active production | canonical remote scheduler |
| `core/cloud_status_polling_service.py` | one daemon polling thread | periodic cloud listing diff | active production | retained as a control loop; each request is submitted to `RequestManager` |
| `ui/threads.py` | manager-required cloud/achievement compatibility bridges | owning `SafeQThread` | compatibility | cloud bridges refuse manager-less execution; managed callers use resource services |
| `core/achievement_schema.py` | manager-less icon fallback | synchronous caller | compatibility/local assets | serial fallback; managed callers submit through `RequestManager` |

There are no `ThreadPoolExecutor` or `ProcessPoolExecutor` uses under
`core/` or `ui/`. Compatibility fallback work is still explicit, but it no
longer creates a feature-local scheduler.

## QThread inventory

### Managed resource compatibility wrappers

These classes in `ui/threads.py` switch to a `RequestManager` request when one
is supplied. Their direct transport branch is compatibility-only:

- `BannerFetcher`, `BannerDownloader`, and `BannerAutoFetcher` — artwork.
- `HeroFetcherThread` and `IconAutoFetcherThread` — artwork assets.
- `CloudSaveStatusFetcherThread` and `CloudSaveBatchQueueWorker` — cloud save
  status.
- `AchievementStatusFetcherThread` and `AchievementBatchQueueWorker` —
  achievement resolution/status.
- `SteamTagsFetcher` — Steam tags.
- `SteamBuildFetcher` — Steam build metadata.

MainWindow uses `ArtworkResourceService`, `CloudStatusService`,
`AchievementResourceService`, and `SteamResourceService` for its normal
manager-backed branches. Cloud status compatibility workers only operate when
an application manager is supplied; dialogs and MainWindow do not use them.
Other compatibility workers are registered with `WorkerSupervisor` when owned
by MainWindow.

### Explicit local/process operations

These are not remote resource schedulers and remain valid independent worker
boundaries:

- `ArchiveExtractorThread` — sandboxed archive extraction with cancellation
  and progress.
- `DiskSizeFetcherThread` — local filesystem size calculation.
- `SafeLaunchLogReader` — drains a child-process stream.
- `UmuBootstrapWorker` — launches and supervises the UMU bootstrap process.
- `GEProtonDownloader` — explicit Proton archive download/extraction operation.
- `UpdateCheckWorker` and `UpdateDownloadWorker` — explicit application update
  lifecycle, including atomic replacement.
- `PlaytimeTrackerThread` — process/session observation and local checkpoints.

### Explicit control/listener threads

- `core/global_hotkeys.py` owns one OS hotkey listener thread. It does not
  perform application resource loading.
- `core/archive_extractor.py` owns one stderr-drain thread for a sandboxed
  subprocess. It is local process I/O only.
- `core/request_manager.py` owns its bounded scheduler workers.
- `core/cloud_status_polling_service.py` owns its single periodic control
  loop; network work is still manager-backed.

## Call-site findings

MainWindow's compatibility references are held by named
`CompatibilityWorkerIndex` instances. This makes their role explicit and
prevents a future caller from treating them as a second scheduler or
shutdown owner. `WorkerSupervisor` remains the only QThread lifetime owner.

- MainWindow's managed cloud status path calls `_spawn_status_fetchers` with
  request-manager specs and does not create `CloudSaveBatchQueueWorker`.
- MainWindow's managed achievement path calls
  `AchievementResourceService.request_many` and does not create
  `AchievementBatchQueueWorker`.
- MainWindow's managed Steam and artwork paths use their resource services;
  direct `SteamBuildFetcher`/artwork workers are fallback branches.
- Game Properties and Save Manager route cloud work directly to managed
  services. If they are constructed without an application host, cloud actions
  return an explicit unavailable result rather than creating a coordinator.
- `TaskSupervisor`/`FunctionWorker` is used for dialog-scoped one-shot work and
  shutdown-safe lifecycle ownership, not as a competing remote batch pool.

## Remaining intentional compatibility

1. Keep non-cloud compatibility workers for older integrations that cannot yet
   inject the application services. They are not used by MainWindow's cloud
   path and remain under explicit lifecycle ownership.
2. Cloud status compatibility bridges require `RequestManager`; manager-less
   cloud execution has been removed.
3. `ci/worker_audit.py` rejects every feature-local executor under
   `core/` or `ui/`. Extend the architecture review before adding any new
   scheduler.

## Verification

The audit was performed with repository-wide searches for:

- `ThreadPoolExecutor` and `ProcessPoolExecutor`;
- `threading.Thread` and direct `Thread` creation;
- `SafeQThread`, `QThread`, `FunctionWorker`, `TaskSupervisor`, and
  `WorkerSupervisor`;
- every compatibility-worker class and MainWindow/dialog call site.
