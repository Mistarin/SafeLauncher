# Phase 7: Worker and executor audit

Status: complete. The normal desktop path has one remote request scheduler:
`RequestManager`, and no feature-local executor remains under `core/` or
`ui/`. QThreads that remain are compatibility wrappers, local process/file
workers, or explicit control/listener threads.

## Decision rules

- Remote library/resource work must go through `RequestManager`.
- A compatibility worker may remain when it supports a manager-less dialog,
  plugin, embedded host, or older integration. It must switch to the managed
  path when a manager is supplied and must not create a second executor.
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
| `ui/threads.py` | manager-less cloud/achievement batch fallback | owning `SafeQThread` | compatibility | serial fallback; managed callers use the resource services |
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
manager-backed branches. The compatibility workers remain for manager-less
dialogs and older callers and are registered with `WorkerSupervisor` when
owned by MainWindow.

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
- Game/property and Proton dialogs still construct compatibility wrappers for
  manager-less support. Where they receive a manager, the wrappers delegate
  to it rather than creating another executor.
- `TaskSupervisor`/`FunctionWorker` is used for dialog-scoped one-shot work and
  shutdown-safe lifecycle ownership, not as a competing remote batch pool.

## Remaining cleanup

1. Migrate manager-aware dialog wrappers directly to resource-service handles
   when those dialogs are next refactored; this removes one QThread hop but is
   not required for scheduler correctness.
2. Keep the compatibility worker wrappers until manager-less tests/plugins
   are removed or explicitly versioned away.
3. `ci/worker_audit.py` now rejects every feature-local executor under
   `core/` or `ui/`. Extend the architecture review before adding any new
   scheduler.

## Verification

The audit was performed with repository-wide searches for:

- `ThreadPoolExecutor` and `ProcessPoolExecutor`;
- `threading.Thread` and direct `Thread` creation;
- `SafeQThread`, `QThread`, `FunctionWorker`, `TaskSupervisor`, and
  `WorkerSupervisor`;
- every compatibility-worker class and MainWindow/dialog call site.
