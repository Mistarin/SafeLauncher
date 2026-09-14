# Private Cloud Library Sync

`CloudClient` is the explicit private SafeLauncherCloud transport boundary and subclasses the compatibility `ConvexSaveBackend`. `CloudMetadataSync` owns the domain merge/apply logic. `CloudContext`, `CloudStatusService`, `CloudStatusPollingService`, `CloudOperationService`, and `CloudMetadataService` provide the Qt-free application-service boundaries around context-safe cloud reads, polling, mutations, status projection persistence, changed-listing polling, and private library/profile reconciliation. The request manager schedules those resources; it does not decide merge precedence or save merge precedence.

Managed cloud mutations expose metadata-only lifecycle records through
[`CloudOperationService`](../../core/cloud_operation_service.py) and
[`CloudOperationRecord`](../../core/cloud_operation_records.py). Records carry
operation identity, context generation, state, timestamps, error category, and
optional bounded stage progress. Upload packaging and restore archive stages
feed this progress through cooperative callbacks; backend blob transfer remains
coarse-grained until the transport exposes byte-level callbacks. They
deliberately exclude credentials, save
archives, payloads, and full private responses.

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

Status/polling/operation/metadata-service sources: [`core/cloud_context.py`](../../core/cloud_context.py), [`core/cloud_status_service.py`](../../core/cloud_status_service.py), [`core/cloud_status_polling_service.py`](../../core/cloud_status_polling_service.py), [`core/cloud_operation_service.py`](../../core/cloud_operation_service.py), [`core/cloud_metadata_service.py`](../../core/cloud_metadata_service.py), [`core/cloud_operations.py`](../../core/cloud_operations.py).
