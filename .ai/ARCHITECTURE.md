# SafeLauncher Architecture

## System shape

```text
main.py → CLI dispatch / GUI bootstrap / QApplication
  ├─ ApplicationRuntime (shared service graph)
  │   ├─ GameDatabase reference (caller-owned local authority)
  │   ├─ RequestManager ── ResourceCache
  │   ├─ library/state/persistence services
  │   ├─ private cloud services ── SafeLauncherCloud
  │   ├─ Steam/artwork services ── transport clients
  │   └─ WorkerSupervisor / session manager / operation registry
  └─ MainWindow (view composition)
      ├─ library views, detail, navigation, dialogs
      ├─ ManagedTaskController / ShutdownController
      ├─ SteamMetadataController / ArtworkController
      ├─ ProfileController / AppUpdateController
      ├─ CloudStatusController / CloudWorkflowController
      ├─ ManualRestoreController / SettingsAccountController
      ├─ LibraryCardRenderer / LibraryNavigationController / GameInspectorWidget
      ├─ NetworkMonitorController / PrelaunchController
      └─ GameSessionController / SessionFeatureController / AchievementSyncController
```

The authoritative source files are mapped in [MODULES.md](MODULES.md) and [maps/module-map.md](maps/module-map.md).

The private SafeLauncherCloud backend is an external companion component. In
the normal development layout its checkout is the sibling directory
`SafeLauncher/../SafeLauncherDatabase/`, although discovery also
supports a `SafeLauncherCloud` sibling or another configured path. This is a
backend deployment checkout, not the client's local SQLite database. See
[external-components.md](architecture/external-components.md).

## Ownership boundaries

- `GameDatabase` owns the local SQLite projection and local-first mutations.
  Its stable facade delegates to `core/local_database` repositories. A single
  `DatabaseSession` owns connection/recovery/lifetime and serializes facade
  calls; `SchemaMigrator` owns idempotent migration and surfaces failures.
- Library lifecycle actions are explicit: archive preserves the local record;
  remove-from-archive restores it; ordinary removal preserves append-only
  profile history; delete-all-local-data purges the local record, history,
  achievements, and playtime ledger. Remote cloud-save generations require a
  separate explicit cloud operation.
- Steam AppIDs are stable game identity (`steam:<appid>`); game titles are
  display metadata. A generated `Steam App <appid>` title is never treated as
  authoritative when a meaningful local, private-profile, or Steam title is
  available. Local identities are canonicalized (`local:<slug>`), and startup
  repair consolidates legacy duplicate rows while preserving achievements,
  playtime sessions, and profile history. A pathless archived local alias is
  also reconciled into a unique later-known Steam identity by matching the
  meaningful title; installed local games are not merged by title alone. See
  [game-name-resolution.md](architecture/game-name-resolution.md).
- Steam build comparisons use a normalized numeric AppID, prefer an installed
  build reference from the standard Steam library manifest, and keep the
  manually entered display version separate from the actual build comparison.
- Archived library records use a neutral archive icon and do not schedule,
  load, or accept late game-artwork results. Artwork fetching remains an
  active-game concern.
- `RequestManager` owns scheduling, deduplication, cancellation, retry, generation ordering, request state notification, and subscriber-aware cancellation for shared bindings. MainWindow adds a transient connectivity circuit breaker above the manager; connectivity probes are explicitly allowed through that gate so recovery can reopen remote work.
- `ResourceCache` owns reusable resource retention and freshness.
- Transport clients own HTTP sessions, authentication headers, encryption transport, response validation, and parsing.
- UI owns presentation and subscriptions, not remote request lifecycles.
  Settings pages are independent form widgets with explicit inputs/actions.
  `SettingsAccountController` subscribes directly to managed account reads;
  it never waits on a nested request from a scheduler worker.
  `CloudSettingsSession` owns the accepted cloud-preference Cancel baseline.
  Settings drains cancelled work on Save, Cancel and window close before
  rollback or native destruction. Its normal path uses shared managed tasks.
- `GameLifecycleService` stages installation files and coordinates archive,
  removal, and restore transitions through `LibraryService`; the window shell
  applies the resulting UI updates.
- `ApplicationRuntime` owns shared service construction and client disposal.
  MainWindow exposes references to the same graph, not independently owned copies.
- `ProfileController` owns public profile and friends navigation plus
  private profile reconciliation callbacks, keeping those workflows out of
  the library and game-session methods on `MainWindow`.
- `AchievementSyncController` owns achievement request scheduling,
  deduplication, managed resource bindings, batch completion, watcher-event
  reconciliation, and achievement notifications. It receives persistence,
  state, request, settings, and UI callbacks explicitly; `MainWindow` remains
  the view composition root and retains view-update delegates for selection,
  launch, and dialog workflows. SQLite persistence remains authoritative in
  `AchievementPersistenceService`, and the window still owns inspector widgets.
  The controller owns watcher creation/teardown and delayed exit reads, including offline mode.
- `CloudStatusController` owns per-game status request scheduling, managed
  resource bindings, subscriber fan-out, failure mapping, and batch/listing
  result delivery. It receives network/shutdown policy and presentation
  callbacks from `MainWindow`; the window continues to own status rendering
  while CloudWorkflowController owns automatic save mutation policy.
- `NetworkMonitorController` owns the periodic connectivity probe, probe
  deduplication, stale-completion invalidation, and transient-unavailable
  request gate. `MainWindow` owns the offline-mode preference, recovery actions,
  footer presentation, and network-loss dialog.
- `ArtworkController` owns library cover/hero/icon request grouping, Qt
  `ResourceBinding` lifetimes, per-game attempt deduplication, and the bounded
  compatibility-fetch queue. `MainWindow` applies successful results to the
  local projection and active widgets; the artwork service remains the
  transport/cache boundary.
- `AppUpdateController` owns GitHub release-check and AppImage download worker
  lifetimes plus the update-banner action state. `MainWindow` keeps the
  combined startup notice because it joins app-release and backend-health
  results.
- `PrelaunchController` owns the per-game prelaunch lock, cloud-save preflight,
  progress/cancellation lifecycle, conflict and quota decisions, and exactly-once
  launch handoff. Cancelling keeps the lock until the cloud operation reaches a
  terminal result so save-restore rollback cannot race game startup. Shutdown
  cancels pending operations and suppresses any later launch continuation. It
  receives presentation and launch callbacks from the window.
  `ManualRestoreController` owns explicit restore preflight, confirmation,
  selected version/plan, cancellation and cloud-context generation checks.
  A manual restore keeps launch blocked until the operation reaches terminal
  state, including rollback; modal confirmation revalidates game/account state.
- `GameSessionController` composes `LaunchSessionCoordinator` with Qt tracker
  lifetime and asynchronous process termination. It owns tracker registration,
  per-game stopping state, and terminal-session release after feature cleanup;
  `SessionFeatureController` owns playtime service calls and failure-isolated
  achievement, recorder, Discord, and cloud-exit hooks.
- Compatibility workers remain for manager-less dialogs, plugins, tests, and older integrations.

Details: [ownership-and-boundaries.md](architecture/ownership-and-boundaries.md), [manager-ownership-map.md](maps/manager-ownership-map.md).

Account-level cloud reads, setup probes, device administration, and explicit
generation deletion are documented in
[cloud-account-services.md](architecture/cloud-account-services.md).

The private-cloud UI has one user-facing entry point: `CloudCenterDialog`,
opened from the header cloud button or account menu. `CloudCenterService` is
its Qt-free application facade. It composes the existing account, status, and
metadata services; it does not create a second transport or save implementation.
The facade exposes normalized connection, sync, quota, device, and conflict
models plus managed overview, history, and connection-probe handles. The
default surface is a compact overview, while setup, connection settings, and
detailed save history remain expandable/secondary workflows. Per-game grid,
list, and compact views expose one Cloud menu that routes to the existing
managed Save Manager or this center. Newer local saves are uploaded
automatically through the managed cloud operation service when the game is
stopped; newer cloud saves are restored by the same lifecycle. Menu-triggered modal work is deferred one
Qt event-loop turn so native menu teardown completes before a frameless dialog
opens. Only redacted overview/health metadata is cacheable; raw credentials and
save contents never cross this boundary.

## Data boundaries

Local library data, playtime, achievements, installation state, and pending sync metadata live in SQLite or local XDG storage. Private profile/library reconciliation goes through `CloudMetadataSync` and `CloudClient`. Public profile publication goes through `ProfileServiceClient` and the profile gateway/cloud service; it is a curated projection and must not expose private installation state.

Details: [data-authority-model.md](architecture/data-authority-model.md), [cloud-data-flow.md](maps/cloud-data-flow.md), [public-profile-services.md](architecture/public-profile-services.md).

## Request/resource flow

```text
UI or feature coordinator
  → stable RequestKey
  → RequestManager
  → cache lookup / priority queue / retry policy
  → transport loader
  → ResourceResult
  → listener or ResourceBinding
  → Qt UI
```

Cached stale values remain usable while refreshes run. Explicit Offline Mode short-circuits network loaders and permits stale-cache presentation where available. A transient probe failure separately cancels optional remote work and gates new remote requests until the probe succeeds; local managed tasks can opt into the offline-safe path. See [request-resource-manager.md](architecture/request-resource-manager.md) and [workflows/offline-mode.md](workflows/offline-mode.md).

Explicit cloud recovery, manual refresh, and selected-game detail checks use
the request manager's forced-network path. That path invalidates the current
resource and active batch before submitting work, so a fresh pre-transition
cache entry cannot satisfy a reconnect check. Cloud status batches retain all
completion consumers when requests are deduplicated.

MainWindow also owns a lightweight managed overview binding for the compact
header indicator. Returning online forces this binding to refresh even when
Cloud Center is closed; opening Cloud Center continues to use the same account
overview resource.

The MainWindow close path stops new scheduling, requests cooperative
cancellation through `WorkerSupervisor`, re-enters through a short Qt timer
while workers remain, and lets the user abort a stalled shutdown rather than
force-terminating Python/Qt workers. The optional global X11 hotkey listener
owns its Xlib display on its listener thread and is disabled on Qt's
headless/minimal platforms; a window close therefore cannot race native input
cleanup or force the host `QApplication` to quit when SafeLauncher is embedded.

Prelaunch cloud save resolution uses the managed request worker and a modal
progress dialog. Cancelling requests cooperative operation cancellation and
aborts the game launch; the launch lock remains held until the operation has
finished so an in-progress restore can complete rollback before game files
are accessed.

Initial window/dialog sizes are clamped to the available screen geometry, and
the compact hero banner adapts between a readable minimum and its cinematic
maximum. Security probes keep technical subprocess output in a copyable
details field while presenting a stable user-facing result.

## Runtime lifecycle

Startup creates the Qt application, initializes environment and database state, constructs the shared managers and clients, then loads the local library before optional remote resources. Shutdown closes page bindings, active feature work, clients, and the request manager in a controlled sequence.

See [runtime-lifecycle.md](architecture/runtime-lifecycle.md) and [startup-shutdown-sequence.md](maps/startup-shutdown-sequence.md).
