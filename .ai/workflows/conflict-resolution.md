# Workflow: Conflict Resolution

Metadata conflicts use revision-aware merge/retry logic. Routine save synchronization compares the newest cloud content timestamp across devices with the local snapshot and automatically uploads or restores the newer side; the displaced local state is retained as a safety backup where supported. Manual history selection remains available for deliberate rollback. A newer local edit must never be acknowledged by a queue operation representing an older digest.
