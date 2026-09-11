import { describe, expect, it } from "vitest";
import { classify, handleGatewayRequest, type Env } from "../src/index";

const env: Env = {
  CONVEX_ORIGIN: "https://central-profile.convex.site",
  AUTH0_ISSUER: "https://tenant.example.auth0.com/",
  AUTH0_AUDIENCE: "https://profiles.safelauncher.app",
  CONVEX_GATEWAY_KEY: "test-gateway-key",
};

const rateLimiter = {
  async limit() {
    return { success: true };
  },
};

function fetchThroughGateway(request: Request, requestEnv: Env = env) {
  return handleGatewayRequest(request, requestEnv, { rateLimiter });
}

describe("profile gateway", () => {
  it("allows only the public profile read and exact authenticated routes", () => {
    expect(classify("/api/profile/v1/01234567890123456789", "GET").kind).toBe("public");
    expect(classify("/api/telemetry/ping", "POST").kind).toBe("public");
    expect(classify("/api/telemetry/ping", "GET").kind).toBe("invalid");
    expect(classify("/api/profile/v1/01234567890123456789", "POST").kind).toBe("invalid");
    expect(classify("/api/profile/v2/me", "PUT").kind).toBe("auth");
    expect(classify("/api/profile/v2/me/claim", "POST").kind).toBe("auth");
    expect(classify("/api/profile/v2/me/friends/01234567890123456789", "DELETE").kind).toBe("auth");
    expect(classify("/api/profile/v2/me/unknown", "GET").kind).toBe("invalid");
    expect(classify("/api/profile/v2/me/friend-requests/bad/accept", "POST").kind).toBe("invalid");
  });

  it("fails closed when deployment secrets are missing", async () => {
    const response = await fetchThroughGateway(
      new Request("https://profiles.safelauncher.app/api/health"),
      { ...env, CONVEX_GATEWAY_KEY: "" },
    );
    expect(response.status).toBe(503);
    expect((await response.json() as { code?: string }).code).toBe("gateway_not_configured");
  });

  it("rejects unauthenticated owner requests before contacting Convex", async () => {
    const response = await fetchThroughGateway(
      new Request("https://profiles.safelauncher.app/api/profile/v2/me"),
      env,
    );
    expect(response.status).toBe(401);
    expect((await response.json() as { code?: string }).code).toBe("unauthorized");
  });

  it("fails closed when a rate limiter is not supplied", async () => {
    const response = await handleGatewayRequest(
      new Request("https://profiles.safelauncher.app/api/profile/v1/01234567890123456789"),
      env,
      {} as { rateLimiter: typeof rateLimiter },
    );
    expect(response.status).toBe(503);
    expect((await response.json() as { code?: string }).code).toBe("rate_limiter_unavailable");
  });

  it("does not forward the client-supplied gateway header", async () => {
    const upstream = new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (_input, init) => {
      const headers = new Headers(init?.headers);
      expect(headers.get("x-safelauncher-gateway-key")).toBe("test-gateway-key");
      expect(headers.get("x-forwarded-for")).toBeNull();
      expect(headers.get("authorization")).toBeNull();
      return upstream;
    };
    try {
      const response = await fetchThroughGateway(
        new Request("https://profiles.safelauncher.app/api/profile/v1/01234567890123456789", {
          headers: { "X-SafeLauncher-Gateway-Key": "attacker-value" },
        }),
        env,
      );
      expect(response.status).toBe(200);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
