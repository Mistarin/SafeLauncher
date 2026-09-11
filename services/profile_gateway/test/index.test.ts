import { describe, expect, it } from "vitest";
import worker, { classify, type Env } from "../src/index";

const env: Env = {
  CONVEX_ORIGIN: "https://redacted.invalid",
  AUTH0_ISSUER: "https://tenant.example.auth0.com/",
  AUTH0_AUDIENCE: "https://profiles.safelauncher.app",
  CONVEX_GATEWAY_KEY: "test-gateway-key",
};

describe("profile gateway", () => {
  it("allows only the public profile read and exact authenticated routes", () => {
    expect(classify("/api/profile/v1/01234567890123456789", "GET").kind).toBe("public");
    expect(classify("/api/profile/v1/01234567890123456789", "POST").kind).toBe("invalid");
    expect(classify("/api/profile/v2/me", "PUT").kind).toBe("auth");
    expect(classify("/api/profile/v2/me/claim", "POST").kind).toBe("auth");
    expect(classify("/api/profile/v2/me/friends/01234567890123456789", "DELETE").kind).toBe("auth");
    expect(classify("/api/profile/v2/me/unknown", "GET").kind).toBe("invalid");
    expect(classify("/api/profile/v2/me/friend-requests/bad/accept", "POST").kind).toBe("invalid");
  });

  it("fails closed when deployment secrets are missing", async () => {
    const response = await worker.fetch(
      new Request("https://profiles.safelauncher.app/api/health"),
      { ...env, CONVEX_GATEWAY_KEY: "" },
    );
    expect(response.status).toBe(503);
    expect((await response.json() as { code?: string }).code).toBe("gateway_not_configured");
  });

  it("rejects unauthenticated owner requests before contacting Convex", async () => {
    const response = await worker.fetch(
      new Request("https://profiles.safelauncher.app/api/profile/v2/me"),
      env,
    );
    expect(response.status).toBe(401);
    expect((await response.json() as { code?: string }).code).toBe("unauthorized");
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
      const response = await worker.fetch(
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
