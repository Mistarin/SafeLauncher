# Core Reference: Saves and Archives

Archive inspection/installation is implemented by [`archive_installer.py`](../../../core/archive_installer.py) and [`archive_extractor.py`](../../../core/archive_extractor.py). Save location discovery and cloud operations are handled by [`ludusavi_detector.py`](../../../core/ludusavi_detector.py), [`cloud_save_sync.py`](../../../core/cloud_save_sync.py), and the validation/crypto modules.

The archive path must remain staged, bounded, traversal-safe, and recoverable. Cloud restore is explicit and conflict-aware.
