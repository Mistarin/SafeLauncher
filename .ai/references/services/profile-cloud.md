# Service Reference: Profile Cloud

`services/profile_cloud/convex/http.ts` dispatches public profile, owner, social, and avatar routes. `convex/profiles.ts`, `schema.ts`, `auth.config.ts`, and `lib/api.ts` contain persistence, validation, authentication, and response helpers.

This service is public-profile infrastructure. It is not SafeLauncherCloud and does not need private library installation-state changes.
