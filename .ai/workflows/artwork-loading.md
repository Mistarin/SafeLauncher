# Workflow: Artwork Loading

1. Build a stable type-specific key for cover, hero, banner, logo, icon, or profile asset.
2. Check memory/disk/client cache.
3. Render fresh or stale cached bytes immediately.
4. Schedule visible/selected artwork ahead of background prefetch.
5. Validate response size/content type in the artwork client.
6. Store reusable bytes and notify all subscribers sharing the key.
7. Close bindings when a view disappears or changes generation.

`ArtworkController` owns the library's `ResourceBinding` lifecycle and
compatibility-fetch queue. `MainWindow` owns only the presentation callbacks
that apply completed paths to the library projection and visible widgets.
