# SafeLauncher public profile service

This is a separate Convex project from the private per-user save backend.
Personal save deployments do not communicate with one another. SafeLauncher
clients publish a privacy-filtered profile projection directly to this service.

```bash
npm install
npx convex dev
# configure the deployment, then deploy:
npx convex deploy --yes
```

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

The service deliberately stores only bounded JSON profile documents. Avatars
are already compressed by the desktop client and embedded in the document;
backgrounds are theme data (colors, gradients, and presets), not uploaded
files.

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
