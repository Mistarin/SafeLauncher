import { afterEach, describe, expect, it, vi } from "vitest";
import { checkRateLimit } from "@vercel/firewall";
import gateway from "../api/gateway";

vi.mock("@vercel/firewall", () => ({
  checkRateLimit: vi.fn(async () => ({ rateLimited: false })),
}));

const configuredEnvironment = {
  CONVEX_ORIGIN: "https://redacted.invalid",
  AUTH0_ISSUER: "https://tenant.example.auth0.com/",
  AUTH0_AUDIENCE: "https://profiles.safelauncher.app",
  CONVEX_GATEWAY_KEY: "vercel-test-gateway-key",
  PROFILE_RATE_LIMIT_ID: "safelauncher-profile-api",
};

const environmentKeys = Object.keys(configuredEnvironment) as Array<keyof typeof configuredEnvironment>;
const savedEnvironment = new Map<string, string | undefined>();

function setEnvironment(values: Partial<typeof configuredEnvironment> = configuredEnvironment) {
  for (const key of environmentKeys) {
    if (!savedEnvironment.has(key)) savedEnvironment.set(key, process.env[key]);
    if (values[key] === undefined) delete process.env[key];
    else process.env[key] = values[key];
  }
}

afterEach(() => {
  for (const [key, value] of savedEnvironment) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  savedEnvironment.clear();
  vi.mocked(checkRateLimit).mockReset();
  vi.mocked(checkRateLimit).mockResolvedValue({ rateLimited: false });
  vi.restoreAllMocks();
});

describe("Vercel gateway adapter", () => {
  it("fails closed when the Vercel environment is incomplete", async () => {
    setEnvironment({ ...configuredEnvironment, CONVEX_GATEWAY_KEY: "" });
    const response = await gateway.fetch(new Request("https://profilegateway.vercel.app/api/health"));
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ code: "gateway_not_configured" });
  });

  it("restores a rewritten nested path before applying the route allowlist", async () => {
    setEnvironment();
    const response = await gateway.fetch(
      new Request("https://profilegateway.vercel.app/api/gateway?__safelauncher_path=profile/v2/me"),
    );
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({ code: "unauthorized" });
  });

  it("fails closed when a Vercel rate-limit rule is not configured", async () => {
    setEnvironment({
      ...configuredEnvironment,
      PROFILE_RATE_LIMIT_ID: "",
    });
    const response = await gateway.fetch(
      new Request("https://profilegateway.vercel.app/api/profile/v1/01234567890123456789"),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ code: "rate_limiter_unavailable" });
  });

  it("fails closed when Vercel reports an unknown rate-limit ID", async () => {
    setEnvironment();
    vi.mocked(checkRateLimit).mockResolvedValueOnce({ rateLimited: false, error: "not-found" });
    const response = await gateway.fetch(
      new Request("https://profilegateway.vercel.app/api/profile/v1/01234567890123456789"),
    );
    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ code: "rate_limiter_unavailable" });
  });

  it("proxies a rewritten public path and injects only the server gateway key", async () => {
    setEnvironment();
    const originalFetch = globalThis.fetch;
    const upstream = new Response(JSON.stringify({ profile: { handle: "01234567890123456789" } }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
    globalThis.fetch = vi.fn(async (input, init) => {
      expect(input).toBe("https://redacted.invalid/api/profile/v1/01234567890123456789?source=test");
      const headers = new Headers(init?.headers);
      expect(headers.get("x-safelauncher-gateway-key")).toBe("vercel-test-gateway-key");
      expect(headers.get("authorization")).toBeNull();
      return upstream;
    });
    try {
      const response = await gateway.fetch(
        new Request(
          "https://profilegateway.vercel.app/api/gateway?__safelauncher_path=profile/v1/01234567890123456789&source=test",
          { headers: { "X-SafeLauncher-Gateway-Key": "attacker-value" } },
        ),
      );
      expect(response.status).toBe(200);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
