# Storage Reference: Cache Storage

There are multiple intentional caches: shared `ResourceCache`, SteamGridDB artwork cache, achievement schema/icon cache, cloud listing/status state, and local database state. They have different authority and invalidation rules. Do not merge them merely because they all use disk.
