# SafeLauncher public profile service

This is a separate Convex project from the private per-user save backend.
Personal save deployments do not communicate with one another. SafeLauncher
clients publish a privacy-filtered profile projection directly to this service.

```bash
npm install
# This is a central production deployment. Replace the value with the
# deployment name shown in the Convex dashboard (for example,
# prod:joyful-capybara-123):
export CONVEX_DEPLOYMENT='prod:<central-deployment-name>'
npx convex deploy --yes
# Bash/Zsh: unset CONVEX_DEPLOYMENT
# Fish: set -e CONVEX_DEPLOYMENT
```

Do not run `npx convex dev` for the production import. It selects or creates a
development deployment and would leave the central production database
unchanged.

## Central authentication and gateway

Production desktop clients use `https://profilegateway.vercel.app`, not the
raw Convex HTTP-action URL. The Vercel gateway in `../profile_gateway`
validates Auth0 access tokens and adds a private gateway header. This Convex
deployment rejects requests without that header, then independently validates
the Auth0 JWT with `ctx.auth.getUserIdentity()`.

Configure these values as production Convex environment variables before
deploying:

```bash
npx convex env set AUTH0_ISSUER 'https://YOUR_AUTH0_DOMAIN/'
npx convex env set AUTH0_AUDIENCE 'https://profiles.safelauncher.app'
npx convex env set SAFELAUNCHER_GATEWAY_KEY 'a-long-random-value'
```

`AUTH0_AUDIENCE` must be the same audience requested by the native Auth0
application and configured as `applicationID` in `auth.config.ts`. The gateway
uses the same issuer/audience and the same random gateway key. Never commit the
key or put it in the desktop application.

The one-time `/api/profile/v2/me/claim` route migrates a legacy owner-token
profile to the signed-in Auth0 identity. The old token hash is removed only
after the claim succeeds. New profiles have no bearer owner token at all.

The service deliberately stores only bounded JSON profile documents. Profile
pictures are not user uploads: the developer imports the approved PNG catalog
into Convex File Storage, and profiles store only a validated `avatar_id`.
Public images are served through the gateway-backed HTTP action, never through
raw Convex storage URLs. Backgrounds remain theme data (colors, gradients,
presets, or a Steam AppID), not uploaded files.

### Importing the approved avatar catalog

Run this only from the developer environment after setting the deployment URL
and the private import key as shell environment variables. Neither value is
part of a desktop build or committed configuration:

```bash
export SAFELAUNCHER_CONVEX_URL='https://your-central-deployment.convex.cloud'
export SAFELAUNCHER_AVATAR_IMPORT_KEY='<private-random-value>'
npx convex env set SAFELAUNCHER_AVATAR_IMPORT_KEY "$SAFELAUNCHER_AVATAR_IMPORT_KEY"
npx convex deploy --yes
npm run import-avatars -- --source-dir '/home/martin/Stažené/ProfilePictures/FINAL'
npm run import-avatars -- --migrate-legacy
```

The importer validates the PNG signature, dimensions, size, and SHA-256 hash;
re-running it is safe. The migration removes legacy embedded avatar payloads
from existing profiles and sets them to the default avatar. The Convex
deployment must have `SAFELAUNCHER_AVATAR_IMPORT_KEY` set to the same private
value. The normal gateway key remains separate.

The public projection includes a bounded profile identity (`display_name`, an
optional 160-character `bio`, and a stable `handle`) plus a bounded `games`
library. Each entry contains only the Steam AppID, display name, a derived
Steam CDN 16:9 hero artwork URL, playtime, favorite state, and an achievement summary
(`unlocked_count`, `total_count`, percentage, and a short list of unlocked
achievements). Handles are immutable after publication; users can change their
visible display name and bio. A profile background may optionally reference a
validated Steam AppID; the client derives the fixed hero URL locally and the
server stores only that AppID plus its derived URL. Installation paths,
executables, save locations, cloud keys, email addresses, and OIDC subjects
are never part of this document. Older profiles without `games` remain
readable and are upgraded when their owner publishes again.

The service also owns friend requests, accepted friendships, and directional
blocks. Relationship writes require the authenticated central identity; public
profile reads do not reveal a friend list. A request is addressed by the recipient's
opaque profile handle, and the recipient must accept it before a friendship is
created. The service does not call any user's private SafeLauncherCloud
deployment.

Anonymous startup telemetry is accepted through the gateway at
`/api/telemetry/ping`. The server hashes the client identifier before storage
and keeps only bounded version/platform and heartbeat counters; the desktop
client never contacts the Convex origin directly.

For the official desktop build, deploy this project once as the developer's
central public-profile service and set the fixed regional `CONVEX_ORIGIN` in
the gateway to its `.convex.site` URL. The desktop client only knows the Vercel
gateway; an environment override remains available for local development.
