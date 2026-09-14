# SafeLauncher Context

SafeLauncher is a PyQt6 desktop launcher for Linux games. It manages a local SQLite library, sandboxed game sessions, save archives, Steam/SteamGridDB metadata, achievements, and private SafeLauncherCloud synchronization.

## Read order

1. [INDEX.md](INDEX.md) for concept-first navigation.
2. [ARCHITECTURE.md](ARCHITECTURE.md) for boundaries and data flow.
3. [MODULES.md](MODULES.md) for source locations.
4. The relevant [workflow](workflows/) and [map](maps/) before editing a subsystem.

## Source-of-truth rules

- Source code is authoritative when this cache disagrees with it.
- SQLite is the authoritative local projection for library, playtime, and achievement state.
- SafeLauncherCloud is a private synchronization backend, not the public profile backend.
- The private backend source is an external companion repository. In the normal local layout it is checked out as the sibling directory `SafeLauncher/../SafeLauncherDatabase/`; this directory name is historical and refers to the SafeLauncherCloud deployment checkout, not the client's SQLite database. See [external-components.md](architecture/external-components.md).
- The public profile service receives a curated projection and does not receive installation paths, device state, or private cloud state.
- `.ai/generated/` is disposable output from `.ai/tools/build_manifest.py`.

## Primary entrypoints

- GUI: [`main.py`](../main.py)
- Environment/bootstrap: [`core/bootstrap.py`](../core/bootstrap.py)
- Main application owner: [`ui/main_window.py`](../ui/main_window.py)
- Local database: [`database.py`](../database.py)
- Test harness: [`test.py`](../test.py)
- Phase smoke tests: [`ci/smoke_phase.py`](../ci/smoke_phase.py)

## Current architectural center

The application-scoped [`RequestManager`](../core/request_manager.py) coordinates remote resource loading. [`ResourceCache`](../core/resource_cache.py) provides bounded memory/disk caching, and [`ResourceBinding`](../ui/resource_binding.py) safely bridges worker-thread results to Qt. Transport clients remain responsible for HTTP and parsing.

See [STATUS.md](STATUS.md) for coverage and known uncertainty.
