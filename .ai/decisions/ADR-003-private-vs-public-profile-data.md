# ADR-003: Private Cloud and Public Profile Are Separate

## Decision

Keep SafeLauncherCloud/private library synchronization separate from central-auth/public-profile services.

## Consequence

Private installation state, paths, device details, credentials, and private cloud metadata remain outside the public profile projection. Private sync does not require public-profile backend changes.
