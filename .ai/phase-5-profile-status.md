# Phase 5: Public profile resource unification

Status: active production integration, with compatibility fallbacks retained.

## Implemented

- Added [`core/profile_resource_service.py`](../core/profile_resource_service.py) as the Qt-independent profile application/transport boundary.
- Public profile reads, owner profile reads, handle availability, social reads, and social mutations now run through `ProfileResourceService`.
- Avatar catalog and bounded avatar batches run through the same service.
- Owner reconciliation, publish, unpublish, and revision-conflict recovery are service-owned; legacy-token deletion remains in the UI boundary and only occurs after a confirmed server-side claim.
- Steam hero/background and profile-card artwork fallback downloads are service-owned and retain bounded response validation.
- MainWindow public-profile request keys now use an endpoint fingerprint instead of embedding the endpoint text.
- Profile resource keys and generation-bound `RequestSpec` construction are now centralized in `ProfileResourceService`; profile artwork identities use bounded hashes rather than raw CDN URLs.
- Managed Steam profile backgrounds, artwork, avatar catalog data, and avatar bytes use context-isolated `ResourceCache` keys. Managed background loads no longer create a second legacy disk cache; legacy files remain read-compatible for older/manager-less paths.
- RequestManager remains responsible for scheduling, deduplication, generation filtering, cancellation, and cache delivery.
- ProfilePage and FriendsDialog keep only presentation state and compatibility `TaskSupervisor` paths for manager-less embedders.

## Remaining in this phase

- Migrate remaining compatibility-only profile workers after independent construction paths are measured.
- Add full manager-backed profile-resource integration tests with fake RequestManager state transitions.
- Keep cloud setup/health and account-management transport separate; those are SafeLauncherCloud configuration flows, not public-profile resources.

## Security boundary

No raw access tokens, owner tokens, save contents, or full private profile payloads enter request keys or cache identities. Public profile projection remains separate from SafeLauncherCloud and does not expose installation status.

## Verification

- `tests/test_profile_resource_service.py` covers endpoint identity and transport delegation.
- `tests/test_profile_resource_service.py` also covers context-isolated keys, generation-bound specs, and shared-cache deduplication.
- Profile avatar catalog/bytes use shared `ResourceCache` on the managed path; legacy avatar files remain only as a manager-less compatibility fallback.
- FriendsDialog uses named `ProfileResourceService` social operations and context-isolated managed social reads; it no longer constructs `ProfileServiceClient` directly.
- Profile view/account/backend changes advance a profile context generation and cancel page-scoped bindings.
- Existing profile tests continue to pass.
