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
const UPSTREAM_TIMEOUT_MS = 15_000;
const HANDLE_PATTERN = "(?:[a-f0-9]{20,40}|[a-z0-9](?:[a-z0-9._-]{2,30}[a-z0-9])?)";
const REQUEST_ID_PATTERN = "[A-Za-z0-9_-]{8,128}";
const PUBLIC_PROFILE_RE = new RegExp(`^/api/profile/v1/(${HANDLE_PATTERN})$`);
const HANDLE_AVAILABILITY_RE = new RegExp(`^/api/profile/v1/handles/(${HANDLE_PATTERN})/availability$`);
const PUBLIC_AVATAR_CATALOG_RE = /^\/api\/profile\/v2\/avatars$/;
const PUBLIC_AVATAR_IMAGE_RE = /^\/api\/profile\/v2\/avatars\/[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;
const TELEMETRY_RE = /^\/api\/telemetry\/ping$/;
const V2_PROFILE_RE = /^\/api\/profile\/v2\/me$/;
const V2_CLAIM_RE = /^\/api\/profile\/v2\/me\/claim$/;
const V2_PRESENCE_HEARTBEAT_RE = /^\/api\/profile\/v2\/me\/presence$/;
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
      (env.CONVEX_GATEWAY_KEY?.trim().length ?? 0) >= 32
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
  const token = header.match(/^Bearer\s+([^\s]+)$/i)?.[1];
  if (!token) throw new Error("missing_token");
  const issuer = env.AUTH0_ISSUER.endsWith("/") ? env.AUTH0_ISSUER : `${env.AUTH0_ISSUER}/`;
  const { payload } = await jwtVerify(token, getJwks(env), {
    issuer,
    audience: env.AUTH0_AUDIENCE,
    algorithms: ["RS256"],
    requiredClaims: ["exp", "sub"],
  });
  if (typeof payload.sub !== "string" || payload.sub.length < 1 || payload.sub.length > 256)
    throw new Error("invalid_subject");
  return { subject: payload.sub, payload };
}

export function classify(pathname: string, method: string): { kind: "health" | "public" | "auth" | "invalid" } {
  if (pathname === "/api/health" && method === "GET") return { kind: "health" };
  if (method === "GET" && PUBLIC_PROFILE_RE.test(pathname)) return { kind: "public" };
  if (method === "GET" && HANDLE_AVAILABILITY_RE.test(pathname)) return { kind: "public" };
  if (method === "GET" && (PUBLIC_AVATAR_CATALOG_RE.test(pathname) || PUBLIC_AVATAR_IMAGE_RE.test(pathname))) return { kind: "public" };
  if (method === "POST" && TELEMETRY_RE.test(pathname)) return { kind: "public" };
  const presenceHeartbeat = V2_PRESENCE_HEARTBEAT_RE.test(pathname) && method === "POST";
  const friendPresencePath = pathname === "/api/profile/v2/me/friends/presence";
  const friendPresence = friendPresencePath && method === "GET";
  const v2Path = V2_PROFILE_RE.test(pathname) || V2_CLAIM_RE.test(pathname) ||
    (V2_SOCIAL_RE.test(pathname) && !friendPresencePath) || presenceHeartbeat || friendPresence;
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

function forwardedHeaders(request: Request, gatewayKey: string, forwardAuthorization: boolean): Headers {
  const headers = new Headers();
  for (const [name, value] of request.headers) {
    const lower = name.toLowerCase();
    if (hopByHopHeaders.has(lower) || lower === "x-safelauncher-gateway-key") continue;
    // The client's central login is only meaningful on authenticated routes.
    if (lower === "authorization" && !forwardAuthorization) continue;
    if (["accept", "authorization", "content-type", "if-none-match", "if-modified-since"].includes(lower))
      headers.set(name, value);
  }
  headers.set("X-SafeLauncher-Gateway-Key", gatewayKey);
  return headers;
}

/** Read the request body while enforcing the byte limit as chunks arrive. */
async function readLimitedBody(request: Request): Promise<ArrayBuffer | null> {
  if (!request.body) return new ArrayBuffer(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_BODY_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes.buffer;
}

async function proxy(request: Request, env: Env, kind: "health" | "public" | "auth"): Promise<Response> {
  const incoming = new URL(request.url);
  const origin = env.CONVEX_ORIGIN.replace(/\/+$/, "");
  const target = `${origin}${incoming.pathname}${incoming.search}`;
  const headers = forwardedHeaders(request, env.CONVEX_GATEWAY_KEY, kind === "auth");
  let body: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const declaredLength = Number(request.headers.get("content-length") || "");
    if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES)
      return jsonError(413, "body_too_large", "The request body is too large.");
    const read = await readLimitedBody(request);
    if (!read) return jsonError(413, "body_too_large", "The request body is too large.");
    body = read;
  }
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return jsonError(502, "upstream_unavailable", "The profile service is temporarily unavailable.");
  }
  const responseHeaders = securityHeaders();
  for (const name of ["content-type", "cache-control", "etag", "retry-after"]) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if (kind === "auth") {
    // Per-user responses must never be stored by a shared cache.
    responseHeaders.set("Cache-Control", "private, no-store");
    responseHeaders.set("Vary", "Authorization");
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
  if (route.kind === "health" || route.kind === "public") {
    const limited = await checkRateLimit(options.rateLimiter);
    return limited || proxy(request, env, route.kind);
  }
  let identity: AuthenticatedRequest;
  try {
    identity = await authenticate(request, env);
  } catch {
    // Failed authentication still costs a signature check, so it is limited per client.
    const limited = await checkRateLimit(options.rateLimiter);
    return limited || jsonError(401, "unauthorized", "A valid central login is required.");
  }
  const limited = await checkRateLimit(options.rateLimiter, `subject:${identity.subject}`);
  return limited || proxy(request, env, "auth");
}
