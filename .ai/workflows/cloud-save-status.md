# Workflow: Cloud Save Status

1. Determine cloud context and local save locations.
2. Reuse cached account listing when valid.
3. Batch or deduplicate per-game status requests.
4. Compare local and cloud save metadata.
5. Display clean, changed, missing, conflict, unavailable, or offline state.
6. Automatically resolve routine upload/restore decisions by timestamp; require explicit user action only for manual history rollback.
7. Keep active and backup generations according to the cloud save policy.
