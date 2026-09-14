# Cache Map

| Cache | Owner | Contents | Invalidation |
|---|---|---|---|
| `ResourceCache` memory | request manager/application | current managed JSON/bytes resources | TTL, LRU, explicit key invalidation |
| `ResourceCache` disk | XDG cache path | reusable serialized resource values | TTL, size limit, explicit invalidation |
| SteamGridDB local cache | `SteamGridDBClient`/`ArtworkClient` | artwork metadata and files | client cache rules and key invalidation |
| achievement schema cache | achievement schema provider | normalized definitions/icons | AppID/schema refresh |
| cloud listing/status cache | cloud save subsystem | remote save metadata | force refresh/context change |
| SQLite | `GameDatabase` | authoritative local projection | domain writes/migrations |
| pending cloud queue | `PendingCloudSyncQueue` | operation context and local digest | successful matching acknowledgement |

Remote caches must be context-isolated. Local SQLite records must not be confused with ephemeral resource cache values.
