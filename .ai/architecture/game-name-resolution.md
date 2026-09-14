# Portable game identity and display-name resolution

## Decision

Use the Steam AppID as the stable identity and treat the game title as
replaceable presentation metadata.

| Source | Priority | Purpose |
|---|---:|---|
| meaningful local SQLite title | 1 | Preserve a user rename or existing imported title |
| meaningful private cloud profile title | 2 | Materialize the same title on another device |
| managed Steam App Details `name` | 3 | Repair legacy/cloud-only records with no usable title |
| `Steam App <appid>` | 4 | Deterministic offline fallback |

Generated placeholders are filtered by `core.game_names` and cannot overwrite
a meaningful title during private-profile merges or local projection. Name
repair uses `SteamResourceService.request_app_details`, so it is bounded,
cached, deduplicated by AppID, stale-cache compatible, and cooperative with
application shutdown.

Non-Steam identities use `local_profile_identity`, which collapses both title
forms such as `Dub Together` and legacy identity-as-title forms such as
`local:local-dub-together` to `local:dub-together`. `GameDatabase` runs an
idempotent consolidation pass at startup and before profile materialization;
it keeps the best installed/meaningful row, merges achievements, playtime
sessions, and profile history, then removes only redundant game rows.

## Persistence

`profile_games.display_name` stores the last meaningful local title for
append-only history rows, including rows whose `games` record was removed.
Existing databases receive the column through normal SQLite migration and
recover titles for Steam identities from current `games` rows when possible.

Private profile payloads continue to use the existing `games[*].name` field;
no SafeLauncherCloud or public-profile schema change is required. The client
does not publish credentials, installation paths, or device-private state as
part of this naming repair.

## Flow

```text
private profile merge
  -> SQLite identity-preserving materialization
  -> placeholder detection
  -> managed Steam App Details request (if needed)
  -> update only placeholder rows
  -> normal library refresh
```

Cloud save `displayName` is not used as the primary title source because save
archives are keyed by normalized names and may be legacy, renamed, or
ambiguous. The private profile title and Steam AppID lookup are more stable.
