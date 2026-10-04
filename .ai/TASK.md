# Current AI Cache Task

The repository is establishing a durable `.ai` architecture cache. This file is reserved for temporary implementation notes and should remain short.

Stable architecture belongs in `CONTEXT.md`, `ARCHITECTURE.md`, `MODULES.md`, or the relevant reference/map/workflow page. Regenerate `generated/` after source structure changes.

## Desktop architecture refactor in progress

ApplicationRuntime now owns shared services; CLI/GUI bootstrap is separate.
ManagedTaskController and ShutdownController own task delivery and nonblocking
drain/re-entry. Steam, automatic save mutations, public/private profile workflows,
and session effects have explicit controllers. AchievementSyncController owns
local watchers and delayed exit reads, including offline mode. Save Manager and
Game Properties receive explicit dependency bundles. CI now guards migrated
boundaries and isolates XDG_STATE_HOME as well as data/config/cache.

The next boundary pass is implemented: manual restore has a context-aware,
cancellable owner; library card construction and layout navigation are separate;
the inspector and five Settings pages have explicit actions/inputs. Settings
uses managed tasks and direct account subscriptions, and drains work on every
exit path before rollback/destruction. Media/storage/install dialogs are separate
modules with compatible exports. SQLite recovery, migrations, records and domain
repositories are separated behind the serialized GameDatabase facade.
Existing backend protocols and database schema remain unchanged.

Residual shell work is still visible: query/selection presentation, top-level
chrome, Settings-result application, collection/edit/archive action orchestration
and some compatibility worker paths remain in MainWindow. Large individual
dialogs also retain their own editing/presentation methods. Do not describe the
entire application as fully decoupled solely because these boundaries moved.

The complete isolated release-readiness gate passed after these migrations,
including localhost health fixtures, smoke/full harness, boundary audits, and
the offline performance guard (527 discovered unit tests). Regression tests now
cover manual-restore cancellation/context, one-worker account subscriptions,
Settings drain/rollback, repository serialization and navigation roundtrips.
Physical desktop and live production behavior
remain unverified.

## Deployment verification detail

Convex deployment output may identify a `.convex.cloud` client host while
SafeLauncher HTTP routes live on the paired `.convex.site` host. Managed
redeploy verification now extracts the just-deployed Convex host from CLI
output, normalizes it, and only then falls back to saved/check-out URLs. No
secret values are parsed, logged, or stored by this path.

The global hotkey listener also consumes the initial dirty-bind marker before
its first bind, preventing duplicate registration messages during startup.

## Portable archived-game naming

The private profile path now preserves meaningful game titles in SQLite
history, ignores generated `Steam App <appid>` placeholders during merges, and
repairs unresolved Steam records through cached `SteamResourceService` App
Details requests. AppID identity remains unchanged, so repair is in-place and
cannot create a second library row. `CloudStatusService.record_status` accepts
both its original `generation` spelling and MainWindow's compatibility spelling
`context_generation`.

Legacy non-Steam identities are now canonicalized to one `local:<slug>` form.
`GameDatabase` repairs rows created by the old `local:local-<slug>` derivation
at startup and during profile application, merging dependent achievements,
playtime sessions, and profile history before removing redundant archived rows.
It also merges a pathless archived local placeholder into a unique matching
Steam identity once an AppID is known, while leaving ambiguous or installed
same-title games separate. Profile payload normalization removes the obsolete
alias before materialization or upload.

Archived rows render a neutral archive icon and are excluded from icon/banner
fetching, cache reads, executable extraction, and late artwork result updates.

Game lifecycle actions now distinguish restoring an archived record from
deleting all local data. The destructive action requires confirmation, removes
registered files with the existing symlink/protected-path guard, purges local
history and ledgers, and intentionally leaves remote cloud-save generations
for separate explicit management.

## Reliability follow-up verified

The request/resource hardening pass now includes subscriber-aware binding
cancellation, transient connectivity gating with an allowed recovery probe,
offline-safe local sandbox verification, independent recorder capture/replay
hotkey loading, and delegate-grid cloud tooltip/accessibility metadata. The
MainWindow shutdown path has a cooperative slow-worker regression test. The
X11 global-hotkey listener now owns its display cleanup on its own thread, and
headless Qt platforms do not start the OS-wide listener; this avoids native
teardown races in embedded/offscreen launches. MainWindow close no longer
forces an application-wide quit, so embedded windows can close without
invalidating the host event loop.

The discovered unit suite, compile check, diff check, security audit, and
worker audit pass. The top-level `test.py` smoke harness also passes in an
isolated offscreen environment. Physical Wayland/X11 input, high-DPI layout,
and screen-reader behavior still require desktop verification.
