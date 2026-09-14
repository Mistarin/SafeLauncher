# Core Reference: Steam and Artwork

- [`steam_client.py`](../../../core/steam_client.py): managed Steam metadata/build transport.
- [`steam_build_tracker.py`](../../../core/steam_build_tracker.py): build lookup and compatibility worker.
- [`steam_tags.py`](../../../core/steam_tags.py): tags/categories lookup and compatibility worker.
- [`artwork_client.py`](../../../core/artwork_client.py): explicit artwork transport boundary.
- [`steamgriddb_client.py`](../../../core/steamgriddb_client.py): SteamGridDB requests, safe image checks, and local cache.

Use distinct resource keys per artwork type. Preserve existing content-type, image-size, cache, and offline behavior when changing the manager integration.
