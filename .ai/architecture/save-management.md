# Save Management

Local save detection uses Ludusavi heuristics and known locations. Archives are created/restored through the save and archive layers, with validation against traversal and unsafe archive behavior. Private cloud save transport uses client-side encryption through the cloud backend and keeps active/backup generations plus conflict history.

Cloud-save status is distinct from game installation/update status. Status checks can be batched and cached. The normal pre-launch and post-exit workflow compares the newest retained cloud content timestamp across devices with the local save and automatically uploads or restores the newer side, while preserving safety forks and exposing manual history rollback separately.

History presentation is normalized by `core.save_history.normalize_history_entries()` and rendered by the shared `SaveHistoryTimeline` component. Cloud generations and local safety forks form one newest-first timeline grouped by the local calendar date. Routine sync selection uses `newest_cloud_version()` and compares `sourceMaxMtime` across all devices, with upload/creation timestamps as legacy fallbacks; duplicate cloud generations are suppressed by stable source/version identity, and device provenance is display-safe. Older entries without a device name show an unavailable label rather than an opaque device ID.

Sources: [`core/cloud_models.py`](../../core/cloud_models.py), [`core/cloud_storage.py`](../../core/cloud_storage.py), [`core/cloud_save_sync.py`](../../core/cloud_save_sync.py), [`core/cloud_operations.py`](../../core/cloud_operations.py), [`core/save_crypto.py`](../../core/save_crypto.py), [`core/save_validation.py`](../../core/save_validation.py), [`core/archive_extractor.py`](../../core/archive_extractor.py), [`ui/dialogs/save_conflict_dialog.py`](../../ui/dialogs/save_conflict_dialog.py).

The save engine is an internal domain implementation. UI surfaces use the
managed cloud services for status, history, upload, and restore; cloud-domain
states and local storage configuration are provided by the lightweight model
and storage modules.
