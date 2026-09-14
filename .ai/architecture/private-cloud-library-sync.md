# Private Cloud Library Sync

`CloudClient` is the explicit private SafeLauncherCloud transport boundary and subclasses the compatibility `ConvexSaveBackend`. `CloudMetadataSync` owns the domain merge/apply logic. The request manager schedules asynchronous entrypoints from the UI and startup paths; it does not decide merge precedence.

## Sync model

1. Read local profile/library metadata from SQLite.
2. Fetch remote profile or game metadata through `CloudClient`.
3. Normalize both sides.
4. Merge append-only achievements, playtime/session information, favorites, last-played metadata, device metadata, and library records according to domain rules.
5. Apply the merged projection locally.
6. Write with revision awareness where the backend supports revisions.
7. On offline/auth/backend failure, enqueue a digest-aware pending operation.

Cloud-only records are materialized locally so a device without the game can still show the account-wide library history. Installation status remains private and is not published to the public profile service.

Sources: [`core/cloud_client.py`](../../core/cloud_client.py), [`core/cloud_backend.py`](../../core/cloud_backend.py), [`core/cloud_metadata_sync.py`](../../core/cloud_metadata_sync.py), [`core/cloud_sync_queue.py`](../../core/cloud_sync_queue.py).
