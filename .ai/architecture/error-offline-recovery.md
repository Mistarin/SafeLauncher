# Error, Offline, and Recovery Behavior

Optional remote features must distinguish unavailable, offline, empty, and failed states. The request manager avoids retrying permanent errors and applies bounded exponential backoff with jitter to transient failures. Offline mode prevents network work while still allowing local data and stale cache values to render.

Private cloud metadata failures enqueue a coalesced pending operation. The queue is digest-aware so an acknowledgement for an older local payload cannot erase a newer edit. Save conflicts require explicit local/cloud selection; displaced data is retained according to the save generation policy.

Database startup performs integrity checks and can restore a backup before falling back to an in-memory database if recovery fails.

Sources: [`core/network_policy.py`](../../core/network_policy.py), [`core/request_manager.py`](../../core/request_manager.py), [`core/cloud_sync_queue.py`](../../core/cloud_sync_queue.py), [`database.py`](../../database.py).
