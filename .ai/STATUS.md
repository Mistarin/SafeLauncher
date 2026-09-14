# AI Cache Status

## Coverage

The cache covers the active desktop runtime, local database, private cloud, public profile services, request/resource manager, UI bindings, launch/session path, achievements, artwork, saves, security boundaries, packaging, operations, tests, and compatibility workers.

## Status labels

- `active`: normal production path.
- `compatibility`: intentionally retained for manager-less callers or older integrations.
- `legacy`: historical fallback or migration path.
- `uncertain`: not yet verified against source.

## Maintenance

Run:

```bash
python .ai/tools/build_manifest.py
python .ai/tools/validate_cache.py
```

The generated index includes source hashes and generation metadata. Curated pages should be reviewed when their listed source files or architectural boundaries change.

## Known boundaries

- Local SQLite remains authoritative for the local library projection.
- Private SafeLauncherCloud synchronization is separate from public profile publication.
- Public profiles do not expose installation status, paths, device details, or private cloud state.
- Compatibility workers are not automatically obsolete merely because manager-backed production paths exist.

## Confidence

The architecture pages are verified against the repository state at cache creation time. Generated indexes are reproducible from source. Any area not represented in a curated page must be treated as `uncertain` until source inspection confirms it.
