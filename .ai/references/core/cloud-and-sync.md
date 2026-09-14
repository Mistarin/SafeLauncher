# Core Reference: Cloud and Sync

## Files

- [`cloud_client.py`](../../../core/cloud_client.py): explicit private transport type.
- [`cloud_backend.py`](../../../core/cloud_backend.py): Convex HTTP, encryption, generation, health, and compatibility implementation.
- [`cloud_metadata_sync.py`](../../../core/cloud_metadata_sync.py): normalization, merge, local apply, revision-aware profile/game sync.
- [`cloud_sync_queue.py`](../../../core/cloud_sync_queue.py): durable coalesced pending operation queue.
- [`cloud_save_sync.py`](../../../core/cloud_save_sync.py): local/cloud save stats, status, upload, restore, and generations.
- [`cloud_operations.py`](../../../core/cloud_operations.py): serialized save operations, preflight, status, and conflict coordination.
- [`cloud_context.py`](../../../core/cloud_context.py): opaque cloud identity, availability, and generation context.
- [`cloud_status_service.py`](../../../core/cloud_status_service.py): status projection cache, persistence, recheck planning, and manager-backed listing/status requests.
- [`cloud_operation_service.py`](../../../core/cloud_operation_service.py): manager-backed upload, restore, preflight, history, pre-launch, and exit-sync operation resources.
- [`cloud_metadata_service.py`](../../../core/cloud_metadata_service.py): manager-backed private profile and per-game metadata reconciliation.

Achievement, Steam, and artwork resources are separate from private cloud
state; their manager-backed services are documented in
[`request-and-resource.md`](request-and-resource.md).

Private library metadata and cloud saves share transport configuration but have distinct domain workflows. Do not treat save archive status as installation status.
