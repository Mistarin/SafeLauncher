// Keep the .js suffix: Vercel runs this project as native ESM in Node, where
// extensionless relative imports are not resolved at runtime. TypeScript's
// bundler resolver still maps this to src/index.ts during the build.
import { handleGatewayRequest, type Env } from "../src/index.js";

function environment(): Env {
  return {
    CONVEX_ORIGIN: process.env.CONVEX_ORIGIN || "",
    AUTH0_ISSUER: process.env.AUTH0_ISSUER || "",
    AUTH0_AUDIENCE: process.env.AUTH0_AUDIENCE || "",
    CONVEX_GATEWAY_KEY: process.env.CONVEX_GATEWAY_KEY || "",
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
    return handleGatewayRequest(originalRequest, environment());
  },
};
