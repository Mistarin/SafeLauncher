# Workflow: Offline Mode

Offline mode is set from CLI or network policy. Local SQLite and local files remain available. The request manager short-circuits network loaders, reports offline state, and serves stale cache values where possible. Private cloud changes remain local and enter the pending sync queue when applicable.

The UI must distinguish offline from empty and permanent error states.

When the policy changes to offline while a Cloud Center read is already in
flight, the application cancels the account read/projection without evicting
the reusable cloud cache. Late transport failures are presented as Offline,
and the footer remains visible while the policy blocks automatic networking.

When a connection probe succeeds, Cloud Center refreshes its overview and
signals the shell to recheck library save statuses. The selected-game detail
shows an explicit Checking state during that targeted request.
