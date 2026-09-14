# Workflow: Private Library Sync

1. Read local records and profile metadata from SQLite.
2. Build the normalized private payload.
3. Submit through a manager-backed request when called asynchronously.
4. Fetch/compare remote revision.
5. Merge append-only stats, favorites, playtime, sessions, and library records.
6. Apply the merged projection locally.
7. Write the revision-aware remote update.
8. On failure, preserve SQLite and enqueue a digest-aware pending operation.

Cloud-only records may appear locally as not installed, while an installed device can retain its playable path. This is private library synchronization, not public profile publication.
