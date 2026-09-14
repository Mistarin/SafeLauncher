# Service Reference: Boundaries

```text
SafeLauncher Python client
 ├─ private SafeLauncherCloud / Convex save backend
 └─ public profile gateway / profile cloud

Profile UI transport boundary:

`ProfileResourceService` is the application-facing boundary. It delegates HTTP and response parsing to `ProfileServiceClient`, while keeping request scheduling in `RequestManager` and presentation in `ProfilePageWidget`/`MainWindow`.
```

The private service is account/library/save infrastructure configured per user/deployment. The public service is developer-operated, central-authenticated, and privacy-filtered. The two must not share credentials, cache contexts, or undocumented payload assumptions.
