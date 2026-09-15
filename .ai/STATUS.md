# AI Cache Status

## Coverage

The cache covers the active desktop runtime, local database, private cloud, public profile services, request/resource manager, UI bindings, launch/session path, achievements, artwork, saves, security boundaries, packaging, operations, tests, and compatibility workers.

The current production-readiness baseline is recorded in [phase-0-baseline.md](phase-0-baseline.md). Cloud, remote-resource, UI-boundary, lifecycle, and reliability work is recorded in [phase-1-status.md](phase-1-status.md), [phase-2-status.md](phase-2-status.md), [phase-3-status.md](phase-3-status.md), [phase-4-status.md](phase-4-status.md), [phase-5-status.md](phase-5-status.md), [phase-5-profile-status.md](phase-5-profile-status.md), [phase-6-status.md](phase-6-status.md), [phase-6-main-window-status.md](phase-6-main-window-status.md), [phase-7-status.md](phase-7-status.md), [phase-7-worker-audit.md](phase-7-worker-audit.md), [point-3-cache-state-audit.md](point-3-cache-state-audit.md), [phase-8-status.md](phase-8-status.md), [phase-9-status.md](phase-9-status.md), and [phase-10-status.md](phase-10-status.md). MainWindow extraction, worker/executor audit, feature-local cache audit, remote error/state normalization, cross-device coverage, and the client-side release gate are implemented. No feature-local request executor remains under `core/` or `ui/`. The only remaining release activities are operator-controlled staging measurements and deliberate retirement of manager-less compatibility APIs when their external callers can be versioned away; these are not correctness gaps in the client architecture.

## Status labels

- `active`: normal production path.
- `compatibility`: intentionally retained for manager-less callers or older integrations.
- `legacy`: historical fallback or migration path.
- `uncertain`: not yet verified against source.

## Maintenance

Run:

```bash
python .ai/tools/build_manifest.py
python .ai/tools/validate_cache.py
```

The generated index includes source hashes and generation metadata. Curated pages should be reviewed when their listed source files or architectural boundaries change.

## Known boundaries

- Local SQLite remains authoritative for the local library projection.
- Private SafeLauncherCloud synchronization is separate from public profile publication.
- Public profiles do not expose installation status, paths, device details, or private cloud state.
- Compatibility workers are not automatically obsolete merely because manager-backed production paths exist; their fallbacks no longer create feature-local executors, and `ci/worker_audit.py` confirms zero executor findings.
- `WorkerSupervisor` is the only MainWindow-owned QThread shutdown registry; feature lists are compatibility indexes only.
- `ResourceBindingRegistry` owns only Qt subscription lifetimes; it is not a
  second request/cache/state manager.
- Remote failures use `classify_remote_error()` and expose a stable
  `ResourceResult.error_category`; auth, permission, conflict, unavailable,
  offline, and cancelled outcomes have explicit resource states.
- `tests/test_cross_device_matrix.py` covers cloud-only materialization,
  installation identity reuse, shared playtime/achievements, and context
  isolation. Installation status remains private and is excluded from public
  profile projection.
- `LaunchPolicy` owns the pure archived/running/configured-mode decision;
  `MainWindow` retains only filesystem checks, dialogs, and dispatch.
- `CloudExitPresentation` is a typed, Qt-free result of exit-sync policy;
  MainWindow only renders its message and performs requested UI refreshes.
- `CompatibilityWorkerIndex` explicitly marks manager-less worker references;
  `PresentationCache` bounds decoded profile artwork/avatar values without
  introducing freshness, retry, or request ownership.
- Grid/list cloud and favorite updates are incremental; they do not rebuild the
  active presentation under the pointer. The inspector splitter has an 8 px
  visible drag handle, and responsive grid column changes apply immediately so
  resizing cannot compete with long-running reflow animations. Card hover
  transitions are debounced across overlay buttons.
- Save history rows display creation/upload device names when the cloud
  generation provides provenance. Legacy generations use an unavailable label
  rather than rendering opaque device IDs; new uploads persist optional device
  provenance in SafeLauncherCloud.
- Per-game `Resolve conflict` actions are disabled until the canonical cached
  status is a real cloud conflict; the same guard is applied to compact and
  full-library cloud menus. Settings forms use a shared aligned label column,
  expanding value column, background-matched editors, and subtle beveled
  surfaces so long names and paths remain readable without layout drift.
- `PopupDialog` owns the shared property-form/grid alignment helpers and the
  muted accessible information-hint row. Cloud Center and Settings expose a
  disabled-until-needed `Review conflicts` entry that delegates to existing
  Save History workflows; it does not introduce a new destructive operation.
- `CloudCenterService` now also composes the cloud operation service. Detailed
  history, upload, restore, and generation-restore callers can delegate
  through the facade while the low-level engine remains a compatibility
  implementation detail. MainWindow invalidates one game's status through
  `CloudStatusService.forget_status()` rather than mutating the compatibility
  mapping directly.
- Game Properties uses managed status and history resources on the normal
  desktop path. Its local coordinator/engine branches are retained only for
  manager-less hosts and are not production schedulers.
- Save history is now normalized once in `core.save_history` and rendered by a
  shared date-grouped timeline in Save Manager, Game Properties, and the
  compatibility Account Manager route. Cloud generations and local safety
  forks are interleaved chronologically, deduplicated, and show safe device
  provenance.
- Cloud operation records and exit-sync results now normalize legacy backend
  categories into `RemoteErrorCategory` values for consistent diagnostics.
- `ci/release_readiness.py` is the canonical local release gate. It creates
  isolated XDG directories, runs compilation, unit tests, smoke phases, the
  full harness, security/worker audits, the offline performance guard, and
  `.ai` manifest/cache validation before running `git diff --check`.

## Confidence

The architecture pages are verified against the repository state at cache creation time. Generated indexes are reproducible from source. Any area not represented in a curated page must be treated as `uncertain` until source inspection confirms it.
