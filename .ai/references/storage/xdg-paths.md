# Storage Reference: XDG Paths

The repository uses XDG data/config/cache roots for library data, logs, managed binaries, local cloud saves, configuration, and reusable artwork/resource caches. Tests and smoke scripts override these roots to avoid touching a developer's real data.

Search `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, and `XDG_CACHE_HOME` before changing a persistent path.
