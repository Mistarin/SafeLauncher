# Current AI Cache Task

The repository is establishing a durable `.ai` architecture cache. This file is reserved for temporary implementation notes and should remain short.

Stable architecture belongs in `CONTEXT.md`, `ARCHITECTURE.md`, `MODULES.md`, or the relevant reference/map/workflow page. Regenerate `generated/` after source structure changes.

## Current deployment verification note

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
MainWindow shutdown path has a cooperative slow-worker regression test.

The discovered unit suite, compile check, diff check, security audit, and
worker audit pass. Physical Wayland/X11 input, high-DPI layout, and screen
reader behavior still require desktop verification. The historical `test.py`
harness has a stale compact-settings assertion for `combo_card_size`; that
test mismatch is separate from the passing discovered suite.
