# Workflow: Offline Mode

Offline mode is set from CLI or network policy. Local SQLite and local files remain available. The request manager short-circuits network loaders, reports offline state, and serves stale cache values where possible. Private cloud changes remain local and enter the pending sync queue when applicable.

The UI must distinguish offline from empty and permanent error states.
