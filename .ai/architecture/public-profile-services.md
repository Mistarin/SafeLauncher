# Public Profile Services

The public profile system is independent of SafeLauncherCloud.

```text
CentralAuthSession
  → ProfileServiceClient
      → profile_gateway
          → services/profile_cloud HTTP routes
              → Convex profiles/social/avatar data
```

The service validates and stores a privacy-filtered public projection. It supports owner profile operations, public reads, social relationships, avatars, and profile appearance. The gateway is the deployment boundary; the Convex service contains validation and persistence.

The public profile does not need changes for private installation-status synchronization. Exposing installation status publicly would be a separate product and privacy decision.

Sources: [`core/central_auth.py`](../../core/central_auth.py), [`core/profile_service.py`](../../core/profile_service.py), [`services/profile_gateway/api/gateway.ts`](../../services/profile_gateway/api/gateway.ts), [`services/profile_cloud/convex/http.ts`](../../services/profile_cloud/convex/http.ts), [`services/profile_cloud/convex/schema.ts`](../../services/profile_cloud/convex/schema.ts).
