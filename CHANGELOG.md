# Changelog

All notable changes to SafeLauncher since `v0.8.0`.

## [0.9.0] - 2026-10-02

This release is a broad cloud, profile, library, offline-mode, and UI overhaul. It includes 70 commits since `v0.8.0` and covers the full `v0.8.0..HEAD` range.

### Highlights

- Introduced a unified Cloud Center for private-cloud status, sync, storage, devices, conflicts, setup, and connection diagnostics.
- Reworked cloud-save synchronization around explicit status, operation, history, and restore services.
- Added readable public profile usernames, rename-safe profile links, and expanded private friends management.
- Added a dedicated game-detail page with artwork, metadata, playtime, cloud-save state, and achievements.
- Added explicit Offline Mode and transient connection-loss handling with cached-data fallbacks.
- Added safe game-file updates that protect saves and sandbox prefixes during replacement.
- Split uninstall/archive behavior from permanent deletion of local game data.
- Unified remote-resource caching and request scheduling across artwork, Steam metadata, profiles, achievements, and cloud status.

### Added

#### Cloud Center and cloud saves

- Added the Cloud Center overview with:
  - connection state and actionable error messages;
  - account and endpoint information;
  - quota and cloud-game counts;
  - registered-device and online-device summaries;
  - pending changes, conflicts, and last-sync information;
  - explicit setup, connection-settings, probe, sync, history, and conflict actions.
- Added a shared cloud status panel with consistent ready, syncing, stale, offline, setup-required, unavailable, and error states.
- Added a canonical cloud-save repository for game identity resolution, listing reuse, legacy-key compatibility, context isolation, TTLs, and in-flight request coalescing.
- Added managed cloud status resources with cached per-game save comparisons and batched listing-diff checks.
- Added managed cloud operation records for upload, restore, preflight, exit synchronization, cancellation, progress, and operation history.
- Added device-aware save history with cloud generations, local safety backups, date grouping, provenance, conflict markers, and registered-device empty states.
- Added a reusable save-history timeline to Game Properties and Save Manager.
- Added cloud storage and device administration, including device revocation and deletion of individual retained cloud generations.
- Added connection probing and backend health/version reporting.
- Added local cloud-root configuration with private directory permissions.
- Added client-side AES-256-GCM save encryption with streaming file encryption/decryption, bounded memory use, authenticated staging, cancellation, and cleanup of failed temporary files.
- Added safe remote restore preflight and restore planning, including current-save inspection, safety-backup creation, metadata preservation, and restore validation.
- Added automatic newer-save selection during normal launch/exit synchronization while retaining the displaced local state as a safety backup.
- Added explicit manual rollback to retained cloud generations.

#### Library and game management

- Added a dedicated game-detail page with:
  - hero artwork, title, tags, description, collection, and favorite state;
  - launch, edit, properties, local-saves, game-folder, screenshots, and uninstall/delete actions;
  - playtime, last-played time, disk usage, launch mode, executable, and cloud-save metadata;
  - achievement progress and preview badges;
  - separate game-update and cloud-save indicators.
- Added a clear distinction between:
  - uninstalling/removing a game from the active library while retaining its SafeLauncher record and account-wide history; and
  - permanently deleting local files, achievements, sessions, profile records, and other local launcher data.
- Added archive-aware rendering with neutral archive icons and no unnecessary artwork, icon, extraction, or executable work for archived records.
- Added portable game-identity repair for legacy local aliases, duplicate archived rows, pathless placeholders, and later-discovered Steam AppIDs.
- Added meaningful display-name repair for unresolved Steam records without changing their identity or creating duplicate library rows.
- Added a safe archive-based game updater with:
  - archive preflight and member validation;
  - save-location verification;
  - protection for saves, prefixes, and `.sandbox-config` data;
  - disk-space checks;
  - staged replacement and atomic commit;
  - interrupted-update recovery.
- Added normalized Steam AppID handling and broader lookup of standard Steam `appmanifest_*.acf` locations.
- Added a platform-independent sort control and improved list/grid synchronization.
- Added screen-size-aware window sizing and more consistent high-DPI icon rendering.

#### Public profiles and friends

- Replaced newly generated opaque profile handles with readable usernames.
- Added username validation, suggestions, collision handling, and a dedicated username-selection dialog.
- Added permanent historical username aliases so old profile links continue to work after a rename.
- Upgraded the public profile service to release `0.4.0` and public profile schema `4`.
- Added profile-handle migration support with immutable internal profile references and a batched, idempotent identifier migration script.
- Made owner social data authoritative by reading the current owned profile before loading friends state.
- Added accepted-friend removal from both the profile page and Friends popup.
- Improved friend request, accept, decline, block, unblock, and error-state handling.
- Added stronger profile schema compatibility and safe retry behavior for older compatible deployments.
- Added profile avatar, artwork, background, and social-resource caching through the shared resource cache.
- Added Steam hero backgrounds by validated numeric AppID, with bounded local caching.
- Improved profile editing, preview behavior, publishing/unpublishing controls, loading states, and public-profile rendering.

#### Offline mode and connectivity

- Added an explicit Offline Mode indicator and policy boundary.
- Offline Mode now stops automatic artwork, Steam, cloud, profile, telemetry, and update requests while preserving local and cached data.
- Added bounded internet connectivity probing independent of the configured cloud backend.
- Added an actionable connection-loss dialog with Retry and Go to Offline Mode actions.
- Added automatic recovery behavior when connectivity returns, including cloud rechecks, Steam update checks, library refreshes, and profile refreshes.
- Added persisted offline-safe game-update verdicts and clearer unavailable, stale, empty, and failed states.
- Hardened Steam online-status checks and prevented malformed AppIDs from reaching remote services.

#### Remote resources and performance

- Added one persistent shared resource cache for reusable remote-derived values.
- Migrated or unified caching for:
  - profile catalogs, avatars, artwork, and backgrounds;
  - Steam metadata and build information;
  - SteamGridDB search and artwork paths;
  - achievement schemas and achievement icons;
  - cloud-status, cloud-listing, and account snapshots.
- Added cache validators so corrupt, incomplete, or unusable cached values are discarded and reloaded.
- Added stale-while-refresh behavior with usable stale data when refreshes fail or the application goes offline.
- Added request deduplication, subscriber-aware cancellation, priority scheduling, retry gating, and deferred timeout clocks that start when work begins executing.
- Expanded request metrics with queue wait, execution duration, cache-hit, stale, cancellation, retry, offline, and invalidation data.
- Added bounded presentation caching for decoded UI values and improved artwork/icon materialization behavior.

#### Desktop integration and settings

- Added managed XDG desktop-menu and Desktop shortcut installation/removal.
- Added optional XDG autostart integration.
- Made desktop-entry writes atomic and limited removal to SafeLauncher-managed entries.
- Reorganized Settings and moved cloud management toward Cloud Center, with technical details kept behind expandable controls.
- Added clearer runtime, save, network, cloud, update, and maintenance dialogs.
- Added a standalone network-unavailable dialog and improved recovery actions.

#### Utilities

- Added the bundled `install-sonarr.sh` Debian/Ubuntu installation helper for Sonarr service setup.

#### Security and lifecycle reliability

- Hardened save restore against archive traversal, destination symlink escapes, unsupported archive members, duplicate destinations, and partially committed restores.
- Hardened archive updates against unsafe links, missing save verification, insufficient disk space, and interrupted swaps.
- Improved cloud error classification, authentication handling, transient-failure reporting, and safe retry behavior.
- Fixed X11 global-hotkey shutdown races by keeping display ownership and cleanup on the listener thread.
- Disabled OS-wide hotkey startup on headless/offscreen Qt platforms.
- Improved cooperative worker shutdown and prevented embedded-window close from forcing an application-wide quit.
- Improved cloud, profile, and UI lifecycle cleanup when dialogs close or network policy changes.
- Added safer handling for cached paths, stale asynchronous results, invalid resource values, and late artwork updates.

### Changed

- Cloud-save status is now presented separately from game-update status throughout the library, game-detail page, and tooltips.
- Game Properties is now the per-game cloud-save hub; Save Manager focuses on detected local saves, import/export, backups, and history.
- Cloud controls use a shared service boundary instead of each dialog maintaining its own transport and cache lifecycle.
- Profile and cloud UI states now distinguish setup required, authentication required, offline, unavailable, stale, empty, cancelled, and failed results.
- Achievement schema and icon loading now prefer local authoritative data, shared cache data, and offline-safe fallbacks before attempting remote sources.
- Artwork and profile resources now use validated shared cache entries while retaining materialized files as derived outputs.
- Main-window library refreshes are more incremental, with shared state for list view, grid view, compact pages, and the selected game.
- Settings and popup styling were consolidated across account, cloud, profile, game, save, achievement, maintenance, and runtime dialogs.
- Accessibility metadata, tooltips, keyboard behavior, and screen-reader labels were added or corrected across interactive controls and delegate-painted badges.
- The application version is now `0.9.0`; the minimum supported private Convex backend remains `1.7.0`.
- Documentation was updated for cloud-save semantics, public usernames, friend management, archive/delete behavior, Offline Mode, and the request/cache architecture.

### Tests and engineering changes

- Added or expanded coverage for cloud account health, Cloud Center, cloud operations, cloud status, cloud dialogs, cloud-save flows, save restore, save history, and cache policy.
- Added coverage for archive updates, game-name and Steam-ID repair, game status, offline update behavior, network policy, desktop integration, icon rendering, UI consistency, profile resources, and global hotkeys.
- Added request-manager, request-contract, resource-cache, resource-binding, and resource-service regression tests.
- Expanded security and thread-lifecycle audits.
- Updated the top-level smoke harness and version assertions for `0.9.0`.
- Added repository architecture and workflow documentation for cloud boundaries, offline behavior, request/resource management, save management, threading, and public-profile services.

### Upgrade notes

- Deploy the public profile service at release `0.4.0` / public profile schema `4` before relying on readable usernames and historical aliases.
- Existing public profile deployments require the identifier migration described in `services/profile_cloud/README.md`. Keep the migration key private and remove or rotate it after verification.
- Do not point the desktop application directly at raw Convex profile endpoints; public profile traffic continues to use the configured gateway.
- Existing local resource-cache data is eligible for migration into the shared resource cache.
- Existing private cloud save generations remain separate from permanent local game deletion and require explicit cloud-management actions for removal.

[0.9.0]: https://github.com/Mistarin/SafeLauncher/compare/v0.8.0...master
