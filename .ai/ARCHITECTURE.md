# SafeLauncher Architecture

## System shape

```text
main.py
  └─ bootstrap / QApplication
      └─ MainWindow
          ├─ GameDatabase (local authority)
          ├─ RequestManager ── ResourceCache
          │       ├─ CloudAccountService / CloudClient ── SafeLauncherCloud
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
- `RequestManager` owns scheduling, deduplication, cancellation, retry, generation ordering, and request state notification.
- `ResourceCache` owns reusable resource retention and freshness.
- Transport clients own HTTP sessions, authentication headers, encryption transport, response validation, and parsing.
- UI owns presentation and subscriptions, not remote request lifecycles.
- Compatibility workers remain for manager-less dialogs, plugins, tests, and older integrations.

Details: [ownership-and-boundaries.md](architecture/ownership-and-boundaries.md), [manager-ownership-map.md](maps/manager-ownership-map.md).

Account-level cloud reads, setup probes, device administration, and explicit
generation deletion are documented in
[cloud-account-services.md](architecture/cloud-account-services.md).

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

## Runtime lifecycle

Startup creates the Qt application, initializes environment and database state, constructs the shared managers and clients, then loads the local library before optional remote resources. Shutdown closes page bindings, active feature work, clients, and the request manager in a controlled sequence.

See [runtime-lifecycle.md](architecture/runtime-lifecycle.md) and [startup-shutdown-sequence.md](maps/startup-shutdown-sequence.md).
