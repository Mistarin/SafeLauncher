# Core Reference: Cloud and Sync

## Files

- [`cloud_client.py`](../../../core/cloud_client.py): explicit private transport type.
- [`cloud_backend.py`](../../../core/cloud_backend.py): Convex HTTP, encryption, generation, health, and compatibility implementation.
- [`cloud_metadata_sync.py`](../../../core/cloud_metadata_sync.py): normalization, merge, local apply, revision-aware profile/game sync.
- [`cloud_sync_queue.py`](../../../core/cloud_sync_queue.py): durable coalesced pending operation queue.
- [`cloud_save_sync.py`](../../../core/cloud_save_sync.py): local/cloud save stats, status, upload, restore, and generations.
- [`cloud_operations.py`](../../../core/cloud_operations.py): serialized save operations, preflight, status, and conflict coordination.

Private library metadata and cloud saves share transport configuration but have distinct domain workflows. Do not treat save archive status as installation status.
