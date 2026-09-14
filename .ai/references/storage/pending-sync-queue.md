# Storage Reference: Pending Sync Queue

[`core/cloud_sync_queue.py`](../../../core/cloud_sync_queue.py) stores durable, coalesced pending private-cloud operations. It keeps operation/context/digest/timestamps, not credentials or full sensitive payloads. Acknowledge only the digest that was successfully synchronized.
