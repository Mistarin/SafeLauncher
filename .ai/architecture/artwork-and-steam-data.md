# Artwork and Steam Data

`SteamClient` owns Steam metadata/build transport. `ArtworkClient` provides the explicit artwork transport boundary while retaining `SteamGridDBClient` compatibility behavior and safe local cache handling.

`SteamResourceService` creates cached build/tag specs and deduplicates public
AppID/name lookups. `ArtworkResourceService` creates separate manager-backed
portrait/icon, hero, and icon resources. `LibraryArtworkCoordinator` groups
library targets by those stable keys and keeps per-kind attempt/binding state;
MainWindow subscribes through
`ResourceBinding`; it does not own transport loaders. Manager-less dialogs and
older integrations may still use the compatibility fetchers.

Artwork resource types are independent: covers, heroes, banners, logos, icons, and profile artwork should have distinct stable keys. Visible/selected assets get higher priority; background prefetch must not starve library data. Cached artwork should render immediately, and content-type/size safety checks remain in the artwork transport path.

Library Steam build and store-tag fan-out is coordinated by the Qt-free
[`LibrarySteamMetadataCoordinator`](../../core/library_steam_metadata_coordinator.py).
It groups rows by AppID/name and owns opaque binding bookkeeping; MainWindow
still applies update results to local projections and widgets. Compatibility
`SteamBuildFetcher`/`SteamTagsFetcher` paths remain for hosts without the
managed request manager.

Sources: [`core/steam_client.py`](../../core/steam_client.py), [`core/steam_resource_service.py`](../../core/steam_resource_service.py), [`core/artwork_client.py`](../../core/artwork_client.py), [`core/artwork_resource_service.py`](../../core/artwork_resource_service.py), [`core/library_artwork_coordinator.py`](../../core/library_artwork_coordinator.py), [`core/library_steam_metadata_coordinator.py`](../../core/library_steam_metadata_coordinator.py), [`core/steamgriddb_client.py`](../../core/steamgriddb_client.py), [`ui/main_window.py`](../../ui/main_window.py).
