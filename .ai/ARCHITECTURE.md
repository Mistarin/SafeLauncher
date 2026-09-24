# SafeLauncher Architecture

## System shape

```text
main.py
  └─ bootstrap / QApplication
      └─ MainWindow
          ├─ GameDatabase (local authority)
          ├─ RequestManager ── ResourceCache
          │       ├─ CloudCenterService ── CloudAccountService / CloudStatusService / CloudMetadataService
          │       │                         └─ CloudClient ── SafeLauncherCloud
          │       ├─ SteamClient ── Steam services
          │       ├─ ArtworkClient ── SteamGridDBClient
          │       └─ Profile/resource adapters
          ├─ library/state/view components
          ├─ launch/session/sandbox services
          └─ compatibility workers and dialogs
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
- Archived library records use a neutral archive icon and do not schedule,
  load, or accept late game-artwork results. Artwork fetching remains an
  active-game concern.
- `RequestManager` owns scheduling, deduplication, cancellation, retry, generation ordering, and request state notification.
- `ResourceCache` owns reusable resource retention and freshness.
- Transport clients own HTTP sessions, authentication headers, encryption transport, response validation, and parsing.
- UI owns presentation and subscriptions, not remote request lifecycles.
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
managed Save Manager or this center. Only redacted overview/health metadata is
cacheable; raw credentials and save contents never cross this boundary.

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

Cached stale values remain usable while refreshes run. Offline mode short-circuits network loaders and permits stale-cache presentation where available. See [request-resource-manager.md](architecture/request-resource-manager.md) and [workflows/offline-mode.md](workflows/offline-mode.md).

Explicit cloud recovery, manual refresh, and selected-game detail checks use
the request manager's forced-network path. That path invalidates the current
resource and active batch before submitting work, so a fresh pre-transition
cache entry cannot satisfy a reconnect check. Cloud status batches retain all
completion consumers when requests are deduplicated.

## Runtime lifecycle

Startup creates the Qt application, initializes environment and database state, constructs the shared managers and clients, then loads the local library before optional remote resources. Shutdown closes page bindings, active feature work, clients, and the request manager in a controlled sequence.

See [runtime-lifecycle.md](architecture/runtime-lifecycle.md) and [startup-shutdown-sequence.md](maps/startup-shutdown-sequence.md).
