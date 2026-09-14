# Data Authority Model

## Local authority

SQLite is authoritative for the local library projection. The `games` table contains both installed records and records materialized from private cloud metadata, including archived/not-installed state. Local playtime, achievement observations, and play sessions are persisted locally.

## Private cloud

SafeLauncherCloud stores private account-wide launcher metadata and encrypted save generations. `CloudMetadataSync` normalizes, merges, and applies cloud metadata to SQLite. Local edits are queued when offline or unavailable; the pending queue stores context, operation, digest, and timing rather than credentials or sensitive payloads.

## Public profile

The public profile is a separate curated projection. `ProfileServiceClient`, the gateway, and `services/profile_cloud` handle its authentication and publication. It can expose curated stats and achievements, but private installation state, device information, paths, executable names, private cloud state, and credentials remain outside that projection.

## Installation semantics

Installed/playable state is device-local. Not-installed or archived records can be synchronized as private library metadata so another device can materialize the game and its account-wide stats. Installing the game later must reconcile the existing local record rather than create a duplicate identity.

Sources: [`database.py`](../../database.py), [`core/cloud_metadata_sync.py`](../../core/cloud_metadata_sync.py), [`core/cloud_sync_queue.py`](../../core/cloud_sync_queue.py), [`core/profile_service.py`](../../core/profile_service.py).
