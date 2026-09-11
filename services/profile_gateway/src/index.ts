import { createRemoteJWKSet, jwtVerify, type JWTPayload } from "jose";

export interface GatewayRateLimiter {
  limit(input: { key?: string }): Promise<{ success: boolean }>;
}

export interface Env {
  CONVEX_ORIGIN: string;
  AUTH0_ISSUER: string;
  AUTH0_AUDIENCE: string;
  CONVEX_GATEWAY_KEY: string;
}

export interface GatewayRequestOptions {
  rateLimiter: GatewayRateLimiter;
}

const MAX_BODY_BYTES = 512 * 1024;
const HANDLE_PATTERN = "[a-f0-9]{20,40}";
const REQUEST_ID_PATTERN = "[A-Za-z0-9_-]{8,128}";
const PUBLIC_PROFILE_RE = new RegExp(`^/api/profile/v1/(${HANDLE_PATTERN})$`);
const V2_PROFILE_RE = /^\/api\/profile\/v2\/me$/;
const V2_CLAIM_RE = /^\/api\/profile\/v2\/me\/claim$/;
const V2_SOCIAL_RE = new RegExp(
  `^/api/profile/v2/me/(?:friends(?:/${HANDLE_PATTERN})?|blocks(?:/${HANDLE_PATTERN})?|friend-requests(?:/${REQUEST_ID_PATTERN}/(?:accept|decline|cancel))?)$`,
);
const hopByHopHeaders = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
]);

type AuthenticatedRequest = { subject: string; payload: JWTPayload };

let jwksCache: { issuer: string; keys: ReturnType<typeof createRemoteJWKSet> } | null = null;

function jsonError(status: number, code: string, message: string, extra: Record<string, unknown> = {}): Response {
  const headers = securityHeaders();
  headers.set("Content-Type", "application/json; charset=utf-8");
  headers.set("Cache-Control", "no-store");
  return new Response(JSON.stringify({ error: message, code, ...extra }), { status, headers });
}

function securityHeaders(): Headers {
  return new Headers({
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'",
  });
}

function isSecureUrl(value: string | undefined): boolean {
  if (!value) return false;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && Boolean(parsed.hostname) && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

function validConfiguration(env: Env): boolean {
  return Boolean(
    isSecureUrl(env.CONVEX_ORIGIN) &&
      isSecureUrl(env.AUTH0_ISSUER) &&
      env.AUTH0_AUDIENCE?.trim() &&
      env.CONVEX_GATEWAY_KEY?.trim()
  );
}

function getJwks(env: Env): ReturnType<typeof createRemoteJWKSet> {
  const issuer = env.AUTH0_ISSUER.replace(/\/+$/, "");
  if (!jwksCache || jwksCache.issuer !== issuer) {
    jwksCache = {
      issuer,
      keys: createRemoteJWKSet(new URL(`${issuer}/.well-known/jwks.json`)),
    };
  }
  return jwksCache.keys;
}

async function authenticate(request: Request, env: Env): Promise<AuthenticatedRequest> {
  const header = request.headers.get("authorization") || "";
  const match = header.match(/^Bearer\s+([^\s]+)$/i);
  if (!match) throw new Error("missing_token");
  const issuer = env.AUTH0_ISSUER.endsWith("/") ? env.AUTH0_ISSUER : `${env.AUTH0_ISSUER}/`;
  const { payload } = await jwtVerify(match[1], getJwks(env), {
    issuer,
    audience: env.AUTH0_AUDIENCE,
    algorithms: ["RS256"],
  });
  if (typeof payload.sub !== "string" || payload.sub.length < 1 || payload.sub.length > 256)
    throw new Error("invalid_subject");
  return { subject: payload.sub, payload };
}

export function classify(pathname: string, method: string): { kind: "health" | "public" | "auth" | "invalid" } {
  if (pathname === "/api/health" && method === "GET") return { kind: "health" };
  if (method === "GET" && PUBLIC_PROFILE_RE.test(pathname)) return { kind: "public" };
  const v2Path = V2_PROFILE_RE.test(pathname) || V2_CLAIM_RE.test(pathname) || V2_SOCIAL_RE.test(pathname);
  if (v2Path && ["GET", "POST", "PUT", "DELETE"].includes(method))
    return { kind: "auth" };
  return { kind: "invalid" };
}

async function checkRateLimit(limiter: GatewayRateLimiter | undefined, key?: string): Promise<Response | null> {
  if (!limiter) return jsonError(503, "rate_limiter_unavailable", "The profile service is temporarily unavailable.");
  try {
    const result = await limiter.limit({ key });
    if (result.success) return null;
    return jsonError(429, "rate_limited", "Too many requests; try again later.");
  } catch {
    return jsonError(503, "rate_limiter_unavailable", "The profile service is temporarily unavailable.");
  }
}

function forwardedHeaders(request: Request, gatewayKey: string): Headers {
  const headers = new Headers();
  for (const [name, value] of request.headers) {
    const lower = name.toLowerCase();
    if (hopByHopHeaders.has(lower) || lower === "x-safelauncher-gateway-key") continue;
    if (["accept", "authorization", "content-type", "if-none-match", "if-modified-since"].includes(lower))
      headers.set(name, value);
  }
  headers.set("X-SafeLauncher-Gateway-Key", gatewayKey);
  return headers;
}

async function proxy(request: Request, env: Env): Promise<Response> {
  const incoming = new URL(request.url);
  const origin = env.CONVEX_ORIGIN.replace(/\/+$/, "");
  const target = `${origin}${incoming.pathname}${incoming.search}`;
  const headers = forwardedHeaders(request, env.CONVEX_GATEWAY_KEY);
  let body: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const declaredLength = Number(request.headers.get("content-length") || "");
    if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES)
      return jsonError(413, "body_too_large", "The request body is too large.");
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES)
      return jsonError(413, "body_too_large", "The request body is too large.");
  }
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
    });
  } catch {
    return jsonError(502, "upstream_unavailable", "The profile service is temporarily unavailable.");
  }
  const responseHeaders = securityHeaders();
  for (const name of ["content-type", "cache-control", "etag", "retry-after"]) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (!responseHeaders.has("content-type")) responseHeaders.set("Content-Type", "application/json; charset=utf-8");
  responseHeaders.set("X-SafeLauncher-Gateway", "1");
  responseHeaders.set("X-Request-Id", crypto.randomUUID());
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export async function handleGatewayRequest(
  request: Request,
  env: Env,
  options: GatewayRequestOptions,
): Promise<Response> {
  if (!validConfiguration(env)) return jsonError(503, "gateway_not_configured", "The profile gateway is not configured.");
  const url = new URL(request.url);
  const route = classify(url.pathname, request.method);
  if (route.kind === "invalid") return jsonError(404, "not_found", "Not found.");
  if (route.kind === "health") return proxy(request, env);
  if (route.kind === "public") {
    const limited = await checkRateLimit(options.rateLimiter);
    return limited || proxy(request, env);
  }
  let identity: AuthenticatedRequest;
  try {
    identity = await authenticate(request, env);
  } catch {
    return jsonError(401, "unauthorized", "A valid central login is required.");
  }
  const limited = await checkRateLimit(options.rateLimiter, `subject:${identity.subject}`);
  return limited || proxy(request, env);
}
