import { AuthConfig } from "convex/server";

/**
 * Auth0 is the single identity provider for the centralized public-profile
 * deployment. Values are configured per Convex deployment, never committed
 * to the repository, so development and production tenants can differ.
 */
export default {
  providers: [
    {
      domain: process.env.AUTH0_ISSUER!,
      applicationID: process.env.AUTH0_AUDIENCE!,
    },
  ],
} satisfies AuthConfig;

