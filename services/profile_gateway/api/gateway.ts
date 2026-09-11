// Keep the .js suffix: Vercel runs this project as native ESM in Node, where
// extensionless relative imports are not resolved at runtime. TypeScript's
// bundler resolver still maps this to src/index.ts during the build.
import { checkRateLimit } from "@vercel/firewall";
import {
  handleGatewayRequest,
  type Env,
  type GatewayRateLimiter,
} from "../src/index.js";

function environment(): Env {
  return {
    CONVEX_ORIGIN: process.env.CONVEX_ORIGIN || "",
    AUTH0_ISSUER: process.env.AUTH0_ISSUER || "",
    AUTH0_AUDIENCE: process.env.AUTH0_AUDIENCE || "",
    CONVEX_GATEWAY_KEY: process.env.CONVEX_GATEWAY_KEY || "",
  };
}

function vercelRateLimiter(
  request: Request,
  rateLimitId: string | undefined,
): GatewayRateLimiter {
  return {
    async limit({ key }) {
      const id = rateLimitId?.trim();
      if (!id) throw new Error("rate_limiter_not_configured");
      const options: { request: Request; rateLimitKey?: string } = { request };
      if (key) options.rateLimitKey = key;
      const result = await checkRateLimit(id, options);
      if (result.error === "not-found") throw new Error("rate_limiter_not_configured");
      return { success: !result.rateLimited };
    },
  };
}

function restoreOriginalRequest(request: Request): Request {
  const routed = new URL(request.url);
  const path = routed.searchParams.get("__safelauncher_path");
  if (!path) return request;

  routed.searchParams.delete("__safelauncher_path");
  const originalPath = path.startsWith("/api/") ? path : `/api/${path.replace(/^\/+/, "")}`;
  const original = new URL(originalPath, routed.origin);
  original.search = routed.search;
  return new Request(original, request);
}

export default {
  async fetch(request: Request): Promise<Response> {
    const originalRequest = restoreOriginalRequest(request);
    const env = environment();
    return handleGatewayRequest(originalRequest, env, {
      rateLimiter: vercelRateLimiter(originalRequest, process.env.PROFILE_RATE_LIMIT_ID),
    });
  },
};
