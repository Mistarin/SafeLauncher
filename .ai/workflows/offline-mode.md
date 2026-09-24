# Workflow: Offline Mode

Offline mode is set from CLI or network policy. Local SQLite and local files remain available. The request manager short-circuits network loaders, reports offline state, and serves stale cache values where possible. Private cloud changes remain local and enter the pending sync queue when applicable.

The UI must distinguish offline from empty and permanent error states.

When the policy changes to offline while a Cloud Center read is already in
flight, the application cancels the account read/projection without evicting
the reusable cloud cache. Late transport failures are presented as Offline,
and the footer remains visible while the policy blocks automatic networking.

An online-to-offline connectivity probe transition is handled separately from
the user's persistent Offline Mode preference. MainWindow shows the actionable
retry/offline dialog, marks optional remote requests as transiently blocked,
and cancels queued/running requests that cannot run offline. The connectivity
probe itself remains allowed so Retry can recover the gate. On recovery, the
library, cloud statuses, account indicator, and Steam update checks are
refreshed.

When a connection probe succeeds, Cloud Center refreshes its overview and
signals the shell to recheck library save statuses. The selected-game detail
shows an explicit Checking state during that targeted request.

Reconnect, manual, and detail status checks must bypass fresh per-game cloud
status cache entries. Transport failures must resolve to an explicit Offline,
Unavailable, or Setup required verdict; they must not leave the detail panel
in Checking indefinitely.
