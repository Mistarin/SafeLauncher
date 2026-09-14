# Save Management

Local save detection uses Ludusavi heuristics and known locations. Archives are created/restored through the save and archive layers, with validation against traversal and unsafe archive behavior. Private cloud save transport uses client-side encryption through the cloud backend and keeps active/backup generations plus conflict history.

Cloud-save status is distinct from game installation/update status. Status checks can be batched and cached; restore/upload operations remain explicit user actions with conflict handling and progress UI.

Sources: [`core/cloud_save_sync.py`](../../core/cloud_save_sync.py), [`core/cloud_operations.py`](../../core/cloud_operations.py), [`core/save_crypto.py`](../../core/save_crypto.py), [`core/save_validation.py`](../../core/save_validation.py), [`core/archive_extractor.py`](../../core/archive_extractor.py), [`ui/dialogs/save_conflict_dialog.py`](../../ui/dialogs/save_conflict_dialog.py).
