# Artwork and Steam Data

`SteamClient` owns Steam metadata/build transport. `ArtworkClient` provides the explicit artwork transport boundary while retaining `SteamGridDBClient` compatibility behavior and safe local cache handling.

Artwork resource types are independent: covers, heroes, banners, logos, icons, and profile artwork should have distinct stable keys. Visible/selected assets get higher priority; background prefetch must not starve library data. Cached artwork should render immediately, and content-type/size safety checks remain in the artwork transport path.

Sources: [`core/steam_client.py`](../../core/steam_client.py), [`core/artwork_client.py`](../../core/artwork_client.py), [`core/steamgriddb_client.py`](../../core/steamgriddb_client.py), [`ui/main_window.py`](../../ui/main_window.py).
