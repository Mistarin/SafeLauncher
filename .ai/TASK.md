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
