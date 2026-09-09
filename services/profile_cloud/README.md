# SafeLauncher public profile service

This is a separate Convex project from the private per-user save backend.
Personal save deployments do not communicate with one another. SafeLauncher
clients publish a privacy-filtered profile projection directly to this service.

```bash
npm install
npx convex dev
# configure the deployment, then:
npx convex deploy --yes
```

The desktop client stores the owner token in its OS credential store. The
service stores only a SHA-256 token hash. Public reads require only the opaque
profile handle; writes require the bearer owner token and an optimistic
revision.

The service deliberately stores only bounded JSON profile documents. Avatars
are already compressed by the desktop client and embedded in the document;
backgrounds are theme data (colors, gradients, and presets), not uploaded
files.

The service also owns friend requests, accepted friendships, and directional
blocks. Relationship writes require the owner bearer token; public profile
reads do not reveal a friend list. A request is addressed by the recipient's
opaque profile handle, and the recipient must accept it before a friendship is
created. The service does not call any user's private SafeLauncherCloud
deployment.

For the official desktop build, deploy this project once as the developer's
central public-profile service and configure its resulting `.convex.site` URL
as `SAFELAUNCHER_PROFILE_SERVICE_URL` (or in the profile page). The client
keeps the URL override so self-hosted deployments can use the same API.
