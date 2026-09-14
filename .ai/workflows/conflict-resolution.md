# Workflow: Conflict Resolution

Metadata conflicts use revision-aware merge/retry logic. Save conflicts are user-visible and require selecting local or cloud content; the displaced generation is retained as a backup where supported. A newer local edit must never be acknowledged by a queue operation representing an older digest.
