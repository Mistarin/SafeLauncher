# Service Reference: Boundaries

```text
SafeLauncher Python client
 ├─ private SafeLauncherCloud / Convex save backend
 └─ public profile gateway / profile cloud
```

The private service is account/library/save infrastructure configured per user/deployment. The public service is developer-operated, central-authenticated, and privacy-filtered. The two must not share credentials, cache contexts, or undocumented payload assumptions.
