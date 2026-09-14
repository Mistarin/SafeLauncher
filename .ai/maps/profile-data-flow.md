# Public Profile Data Flow

```text
Local profile editor/stat collector
  → curated public projection
  → CentralAuthSession authorization
  → ProfileServiceClient
  → profile gateway
  → profile cloud validation/storage
  → public handle read
```

The projection boundary excludes installation paths, executable names, device details, private cloud state, owner credentials, and other local-only data. Public profile changes are not required for private library synchronization.
