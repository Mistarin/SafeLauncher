# SafeLauncher profile gateway

This gateway is the only public client endpoint for the centralized profile
service. The production deployment runs as a Vercel Function at
`https://profilegateway.vercel.app`; it proxies a fixed set of routes to the
Convex deployment and adds a gateway-only secret header. The Convex HTTP
actions verify that header and independently validate Auth0 JWTs.

## Production setup

1. Create an Auth0 Native Application and API.
2. Enable Device Code, Refresh Token, offline access, and refresh-token rotation.
3. Set the Auth0 API audience to `https://profiles.safelauncher.app`.
4. Configure the Vercel project `profile_gateway` with these **Production** variables:

   ```text
   CONVEX_ORIGIN=https://redacted.invalid
   AUTH0_ISSUER=https://dev-712dm7e8q0c2tie3.us.auth0.com/
   AUTH0_AUDIENCE=https://profiles.safelauncher.app
   CONVEX_GATEWAY_KEY=<same random value configured in Convex>
   PROFILE_RATE_LIMIT_ID=safelauncher-profile-api
   ```

   Keep the issuer's trailing slash; it must match the JWT `iss` claim exactly.
   Mark `CONVEX_GATEWAY_KEY` as sensitive. Vercel environment changes require
   a new deployment before the function sees them.
5. Set the same random gateway secret in Convex and Vercel:

   ```bash
   npx convex env set SAFELAUNCHER_GATEWAY_KEY '<same random value>'
   # In the linked Vercel project, add CONVEX_GATEWAY_KEY for Production
   npx vercel env add CONVEX_GATEWAY_KEY production --sensitive
   ```

6. Deploy the central Convex service to the `redacted-central-deployment` production
   deployment, then deploy this Vercel project with `npx vercel --prod`.

The Convex origin and gateway secret are never included in SafeLauncher. The
desktop application uses only `https://profilegateway.vercel.app` and Auth0's
public issuer/client configuration. Vercel is the only supported production
gateway for the centralized profile service.

## Desktop build configuration

The native client needs the public Auth0 values at runtime (or they can be
baked into the release environment):

```bash
export SAFELAUNCHER_AUTH0_ISSUER='https://dev-712dm7e8q0c2tie3.us.auth0.com/'
export SAFELAUNCHER_AUTH0_CLIENT_ID='wja9mk07Ykw3xvey2V47QGO0olzwRdl6'
export SAFELAUNCHER_AUTH0_AUDIENCE='https://profiles.safelauncher.app'
```

The client ID, issuer, and audience are not secrets. Never place
`CONVEX_GATEWAY_KEY`, an Auth0 client secret, or any other private credential
in the desktop build. The client uses Auth0 Device Authorization Flow, so the
native application must allow that grant and offline access.

## Vercel rate limiting

The Vercel adapter calls `@vercel/firewall` for every public profile read and
authenticated profile/social request. Configure this Firewall custom rule in
the Vercel dashboard before deploying:

1. Open the project, select **Firewall → Configure → New Rule**.
2. Set the condition to **@vercel/firewall** and use the rate-limit ID
   `safelauncher-profile-api`. Select **Rate Limit**, **Fixed Window**, a
   60-second window, and 120 requests. Keep the default 429 response.
3. Review and **Publish** the Firewall change.

Public requests use Vercel's source-IP key. Authenticated requests use the
verified Auth0 subject as their rate-limit key, so one account cannot exhaust
another account's authenticated quota. The ID is a configuration value, not a
secret. If the ID is missing or the Vercel rule is unavailable, the gateway
fails closed with `503` instead of forwarding an unprotected request.

## Local verification

```bash
npm install
npx tsc --noEmit
npm test
```
