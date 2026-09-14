# SafeLauncher AI Index

Start here when navigating the repository.

## By task

| If you need to... | Read first | Then inspect |
|---|---|---|
| Understand the whole application | [ARCHITECTURE.md](ARCHITECTURE.md) | [system-overview.md](architecture/system-overview.md) |
| Work on the private backend/deployment | [external-components.md](architecture/external-components.md) | `core/cloud_detector.py`, `core/cloud_cli_wizard.py`, `core/cloud_client.py` |
| Change request scheduling | [request-resource-manager.md](architecture/request-resource-manager.md) | `core/request_contracts.py`, `core/request_manager.py`, `tests/test_request_manager.py` |
| Add a remote resource | [ownership-and-boundaries.md](architecture/ownership-and-boundaries.md) | [request-key-map.md](maps/request-key-map.md), transport client, cache tests |
| Change private cloud sync | [private-cloud-library-sync.md](architecture/private-cloud-library-sync.md) | [cloud-data-flow.md](maps/cloud-data-flow.md), [private-library-sync.md](workflows/private-library-sync.md), `core/cloud_operation_records.py` |
| Change public profiles | [public-profile-services.md](architecture/public-profile-services.md) | [profile-data-flow.md](maps/profile-data-flow.md), `core/profile_service.py`, `services/profile_cloud/` |
| Change install/not-installed behavior | [data-authority-model.md](architecture/data-authority-model.md) | [archived-game-restoration.md](workflows/archived-game-restoration.md), [install-after-cloud-sync.md](workflows/install-after-cloud-sync.md) |
| Change UI loading | [ui-architecture.md](architecture/ui-architecture.md) | [resource-bindings.md](references/ui/resource-bindings.md), `ui/resource_binding.py` |
| Change achievements | [achievements-and-playtime.md](architecture/achievements-and-playtime.md) | `core/achievement_*`, `database.py`, achievement dialogs |
| Change artwork/Steam metadata | [artwork-and-steam-data.md](architecture/artwork-and-steam-data.md) | `core/artwork_client.py`, `core/steam_client.py`, main-window bindings |
| Change launch/security | [game-launch-and-sandbox.md](architecture/game-launch-and-sandbox.md) | [authentication-and-secrets.md](architecture/authentication-and-secrets.md), runtime modules |
| Change shutdown/threading | [threading-and-shutdown.md](architecture/threading-and-shutdown.md) | `core/safe_thread.py`, `ui/main_window.py`, thread tests |
| Run or extend tests | [testing-and-ci.md](architecture/testing-and-ci.md) | [test-suite-map.md](references/tests/test-suite-map.md) |

## Production-readiness planning

- Current baseline and component ratings: [phase-0-baseline.md](phase-0-baseline.md)
- Readiness dimensions and priority order: [production-readiness.md](architecture/production-readiness.md)
- Cloud, resource, UI boundary, lifecycle, and reliability implementation: [phase-1-status.md](phase-1-status.md), [phase-2-status.md](phase-2-status.md), [phase-3-status.md](phase-3-status.md), [phase-4-status.md](phase-4-status.md), [phase-5-status.md](phase-5-status.md), [phase-6-status.md](phase-6-status.md), [phase-7-status.md](phase-7-status.md), [phase-8-status.md](phase-8-status.md), [private-cloud-library-sync.md](architecture/private-cloud-library-sync.md)
- Canonical isolated release verification: [`ci/release_readiness.py`](../ci/release_readiness.py), [phase-10-performance-baseline.md](phase-10-performance-baseline.md)
- Public profile resource unification: [phase-5-profile-status.md](phase-5-profile-status.md)
- MainWindow extraction: [phase-6-main-window-status.md](phase-6-main-window-status.md)
- Library artwork coordination: [`core/library_artwork_coordinator.py`](../core/library_artwork_coordinator.py), [artwork-and-steam-data.md](architecture/artwork-and-steam-data.md)
- Library achievement coordination: [`core/library_achievement_coordinator.py`](../core/library_achievement_coordinator.py), [achievements-and-playtime.md](architecture/achievements-and-playtime.md)
- Library Steam metadata coordination: [`core/library_steam_metadata_coordinator.py`](../core/library_steam_metadata_coordinator.py), [artwork-and-steam-data.md](architecture/artwork-and-steam-data.md)
- Launch/session lifecycle: [`core/launch_session_coordinator.py`](../core/launch_session_coordinator.py), [game-launch-and-sandbox.md](architecture/game-launch-and-sandbox.md)
- Achievement presentation state: [`core/achievement_state_store.py`](../core/achievement_state_store.py), [achievements-and-playtime.md](architecture/achievements-and-playtime.md)

## By source area

- Core runtime: [references/core/game-runtime.md](references/core/game-runtime.md)
- Core cloud and sync: [references/core/cloud-and-sync.md](references/core/cloud-and-sync.md)
- Core request/resource: [references/core/request-and-resource.md](references/core/request-and-resource.md)
- UI: [references/ui/main-window.md](references/ui/main-window.md)
- Services: [references/services/service-boundaries.md](references/services/service-boundaries.md)
- Storage: [references/storage/sqlite.md](references/storage/sqlite.md)
- Operations: [references/operations/setup-scripts.md](references/operations/setup-scripts.md)
- Tests: [references/tests/test-suite-map.md](references/tests/test-suite-map.md)

## Machine indexes

- [File inventory](generated/files.json)
- [Symbols](generated/symbols.json)
- [Imports](generated/imports.json)
- [Manager/service connections](generated/connections.json)
- [Request keys](generated/request-keys.json)
- [Database schema](generated/database-schema.json)
- [Tests](generated/tests.json)

## Navigation rules

1. Read the smallest relevant architecture page before opening many source files.
2. Use generated indexes to find symbols and consumers.
3. Confirm behavior in source before making a change.
4. Label new notes as active, compatibility, legacy, or uncertain.
5. Regenerate and validate the cache after structural or boundary changes.
