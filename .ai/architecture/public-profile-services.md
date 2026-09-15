# Public Profile Services

The public profile system is independent of SafeLauncherCloud.

```text
CentralAuthSession
  → ProfileResourceService
      → ProfileServiceClient
      → profile_gateway
          → services/profile_cloud HTTP routes
              → Convex profiles/social/avatar data
```

The service validates and stores a privacy-filtered public projection. It supports owner profile operations, public reads, social relationships, avatars, and profile appearance. The gateway is the deployment boundary; the Convex service contains validation and persistence.

Owner publication treats a create-time uniqueness response as a recoverable
`handle_taken` result when the authenticated identity has no existing profile.
The UI remains signed in, marks the local handle unavailable, and directs the
user to choose another handle. An identity-owned profile conflict remains a
separate condition and is not silently converted into a handle change.

The public profile does not need changes for private installation-status synchronization. Exposing installation status publicly would be a separate product and privacy decision.

`ProfileResourceService` is the application boundary used by `ProfilePageWidget` and MainWindow. It owns profile workflow composition, bounded image transport, context-isolated resource-key/spec construction, social operation routing, avatar batching, and revision/conflict recovery. `ProfileServiceClient` remains the only profile HTTP/parser boundary. RequestManager owns scheduling, deduplication, generation filtering, cancellation, and cache delivery; UI code owns only presentation state and compatibility fallback workers.

Sources: [`core/central_auth.py`](../../core/central_auth.py), [`core/profile_service.py`](../../core/profile_service.py), [`services/profile_gateway/api/gateway.ts`](../../services/profile_gateway/api/gateway.ts), [`services/profile_cloud/convex/http.ts`](../../services/profile_cloud/convex/http.ts), [`services/profile_cloud/convex/schema.ts`](../../services/profile_cloud/convex/schema.ts).
