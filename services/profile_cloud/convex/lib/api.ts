export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public extra?: Record<string, unknown>,
  ) {
    super(message);
  }
}

export function jsonResponse(
  body: unknown,
  status = 200,
  isPublic = false,
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": isPublic ? "public, max-age=60" : "no-store",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "Content-Security-Policy": "default-src 'none'",
    },
  });
}

export function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return result === 0;
}

/** Only the official gateway may reach the central deployment. */
export function requireGateway(req: Request): void {
  const configured = process.env.SAFELAUNCHER_GATEWAY_KEY;
  const supplied = req.headers.get("x-safelauncher-gateway-key") || "";
  if (!configured)
    throw new ApiError(503, "backend_not_configured", "The profile gateway is not configured.");
  if (!supplied || !constantTimeEqual(supplied, configured))
    throw new ApiError(403, "gateway_required", "This profile service is available through the official gateway only.");
}

/**
 * Authenticate a central API request with Convex's configured OIDC provider.
 * The derived hash is used as the database ownership key so Auth0 subjects
 * never become directly queryable profile data.
 */
export async function requireCentralIdentity(ctx: any, req: Request): Promise<{
  identity: any;
  ownerIdentityHash: string;
}> {
  requireGateway(req);
  let identity: any;
  try {
    identity = await ctx.auth.getUserIdentity();
  } catch {
    throw new ApiError(401, "unauthorized", "A valid central login is required.");
  }
  if (!identity) throw new ApiError(401, "unauthorized", "A valid central login is required.");
  const tokenIdentifier = String(
    identity.tokenIdentifier ||
      `${identity.issuer || ""}|${identity.subject || ""}`,
  ).trim();
  if (!tokenIdentifier || tokenIdentifier === "|")
    throw new ApiError(401, "unauthorized", "The central login identity is invalid.");
  return {
    identity,
    ownerIdentityHash: await sha256(`safelauncher-profile:${tokenIdentifier}`),
  };
}

export async function readJsonBody(
  req: Request,
): Promise<Record<string, unknown>> {
  let value: unknown;
  try {
    value = await req.json();
  } catch {
    throw new ApiError(400, "bad_json", "Request body must be valid JSON.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError(400, "bad_json", "Request body must be an object.");
  }
  return value as Record<string, unknown>;
}

export function ownerToken(req: Request): string {
  const value = req.headers.get("authorization") || "";
  const token = value.replace(/^Bearer\s+/i, "").trim();
  if (!token || token.length < 32 || token.length > 256) {
    throw new ApiError(
      401,
      "owner_token_required",
      "A valid profile owner token is required.",
    );
  }
  return token;
}

export async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export function validHandle(value: unknown): value is string {
  return (
    typeof value === "string" &&
    (/^[a-f0-9]{20,40}$/.test(value) || /^[a-z0-9](?:[a-z0-9._-]{1,30}[a-z0-9])?$/.test(value))
  );
}

export function validRequestId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{8,128}$/.test(value);
}

function isColor(value: unknown): value is string {
  return typeof value === "string" && /^#[0-9A-Fa-f]{6}$/.test(value);
}

function boundedString(
  value: unknown,
  maxLength: number,
  fallback = "",
): string {
  return typeof value === "string"
    ? value.trim().slice(0, maxLength)
    : fallback;
}

function validSteamAppId(value: unknown): value is string {
  return typeof value === "string" && /^[1-9][0-9]{0,15}$/.test(value);
}

function steamBannerUrl(appId: string): string {
  return `https://cdn.akamai.steamstatic.com/steam/apps/${appId}/header.jpg`;
}

function validSteamBanner(value: unknown, appId: string): boolean {
  return (
    typeof value === "string" &&
    new RegExp(`^https://(?:cdn\\.akamai\\.steamstatic\\.com|shared\\.akamai\\.steamstatic\\.com|steamcdn-a\\.akamaihd\\.net)/steam/apps/${appId}/header\\.jpg$`).test(value)
  );
}

export function validatePublicProfile(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError(400, "invalid_profile", "Profile must be an object.");
  }
  const profile = value as Record<string, unknown>;
  if (
    profile.schema_version !== 1 ||
    typeof profile.handle !== "string" ||
    !validHandle(profile.handle)
  ) {
    throw new ApiError(
      400,
      "invalid_profile",
      "Profile schema or handle is invalid.",
    );
  }
  const displayName = boundedString(profile.display_name, 64);
  if (!displayName) {
    throw new ApiError(
      400,
      "invalid_profile",
      "Profile display name is invalid.",
    );
  }
  const avatar = profile.avatar;
  let cleanAvatar: Record<string, unknown> | null = null;
  if (avatar !== null && avatar !== undefined) {
    if (!avatar || typeof avatar !== "object" || Array.isArray(avatar)) {
      throw new ApiError(400, "invalid_avatar", "Avatar payload is invalid.");
    }
    const input = avatar as Record<string, unknown>;
    const data = boundedString(input.data_b64, 360_000);
    if (
      input.mime !== "image/jpeg" ||
      !data ||
      data.length > 350_000 ||
      !/^[A-Za-z0-9+/=]+$/.test(data)
    ) {
      throw new ApiError(400, "invalid_avatar", "Avatar payload is invalid.");
    }
    cleanAvatar = {
      mime: "image/jpeg",
      data_b64: data,
      sha256: boundedString(input.sha256, 64),
      width: Number.isSafeInteger(input.width) ? input.width : 0,
      height: Number.isSafeInteger(input.height) ? input.height : 0,
      bytes: Number.isSafeInteger(input.bytes) ? input.bytes : 0,
    };
  }
  const inputBackground = profile.background;
  let background: Record<string, unknown>;
  if (
    inputBackground &&
    typeof inputBackground === "object" &&
    !Array.isArray(inputBackground)
  ) {
    const candidate = inputBackground as Record<string, unknown>;
    if (candidate.kind === "solid" && isColor(candidate.color)) {
      background = { kind: "solid", color: candidate.color.toUpperCase() };
    } else if (
      candidate.kind === "gradient" &&
      Array.isArray(candidate.stops) &&
      candidate.stops.length >= 2 &&
      candidate.stops.length <= 3 &&
      candidate.stops.every(isColor)
    ) {
      background = {
        kind: "gradient",
        stops: candidate.stops.map((color) => color.toUpperCase()),
        angle: Number.isSafeInteger(candidate.angle)
          ? Math.max(0, Math.min(360, candidate.angle as number))
          : 135,
      };
    } else {
      throw new ApiError(
        400,
        "invalid_background",
        "Background theme is invalid.",
      );
    }
  } else {
    throw new ApiError(
      400,
      "invalid_background",
      "Background theme is invalid.",
    );
  }
  const statsInput = profile.stats;
  const stats =
    statsInput && typeof statsInput === "object" && !Array.isArray(statsInput)
      ? (statsInput as Record<string, unknown>)
      : {};
  const stat = (key: string): number =>
    Number.isSafeInteger(stats[key])
      ? Math.max(0, Math.min(10_000_000, stats[key] as number))
      : 0;
  const cleanGames: Array<Record<string, string>> = [];
  if (
    !Array.isArray(profile.favorite_games) ||
    profile.favorite_games.length > 24
  ) {
    throw new ApiError(400, "invalid_games", "Favorite game list is invalid.");
  }
  for (const raw of profile.favorite_games) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw))
      throw new ApiError(
        400,
        "invalid_games",
        "Favorite game list is invalid.",
      );
    const item = raw as Record<string, unknown>;
    const name = boundedString(item.name, 120);
    if (!name)
      throw new ApiError(
        400,
        "invalid_games",
        "Favorite game name is invalid.",
      );
    const appId = boundedString(item.app_id, 16);
    if (appId && !validSteamAppId(appId)) {
      throw new ApiError(400, "invalid_games", "Favorite game AppID is invalid.");
    }
    cleanGames.push({ name, app_id: appId });
  }

  const cleanLibrary: Array<Record<string, unknown>> = [];
  const rawLibrary = profile.games === undefined ? [] : profile.games;
  if (!Array.isArray(rawLibrary) || rawLibrary.length > 60) {
    throw new ApiError(400, "invalid_games", "Game library is invalid.");
  }
  const seenApps = new Set<string>();
  for (const raw of rawLibrary) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new ApiError(400, "invalid_games", "Game library is invalid.");
    }
    const item = raw as Record<string, unknown>;
    const appId = boundedString(item.app_id, 16);
    const name = boundedString(item.name, 120);
    if (!validSteamAppId(appId) || !name || seenApps.has(appId)) {
      throw new ApiError(400, "invalid_games", "Game library entry is invalid.");
    }
    seenApps.add(appId);
    const banner = boundedString(item.banner_url, 300);
    if (banner && !validSteamBanner(banner, appId)) {
      throw new ApiError(400, "invalid_games", "Game banner URL is invalid.");
    }
    const achievementInput =
      item.achievements && typeof item.achievements === "object" && !Array.isArray(item.achievements)
        ? (item.achievements as Record<string, unknown>)
        : {};
    const count = (key: string): number =>
      Number.isSafeInteger(achievementInput[key])
        ? Math.max(0, Math.min(10_000_000, achievementInput[key] as number))
        : 0;
    const unlockedCount = count("unlocked_count");
    const totalCount = Math.max(unlockedCount, count("total_count"));
    const rawRecent = achievementInput.recent;
    if (!Array.isArray(rawRecent) || rawRecent.length > 20) {
      throw new ApiError(400, "invalid_achievements", "Per-game achievement list is invalid.");
    }
    const cleanRecent: Array<Record<string, string | number>> = [];
    for (const rawAchievement of rawRecent) {
      if (!rawAchievement || typeof rawAchievement !== "object" || Array.isArray(rawAchievement)) {
        throw new ApiError(400, "invalid_achievements", "Per-game achievement is invalid.");
      }
      const achievement = rawAchievement as Record<string, unknown>;
      const achievementAppId = boundedString(achievement.app_id, 16);
      const apiName = boundedString(achievement.api_name, 128);
      const achievementName = boundedString(achievement.name, 120);
      const gameName = boundedString(achievement.game, 120);
      if (achievementAppId !== appId || !apiName || !achievementName || !gameName) {
        throw new ApiError(400, "invalid_achievements", "Per-game achievement is invalid.");
      }
      cleanRecent.push({
        app_id: appId,
        api_name: apiName,
        name: achievementName,
        game: gameName,
        unlocked_at: Number.isSafeInteger(achievement.unlocked_at)
          ? Math.max(0, Math.min(4_000_000_000, achievement.unlocked_at as number))
          : 0,
      });
    }
    cleanLibrary.push({
      name,
      app_id: appId,
      banner_url: banner || steamBannerUrl(appId),
      playtime_seconds: Number.isSafeInteger(item.playtime_seconds)
        ? Math.max(0, Math.min(3_200_000_000, item.playtime_seconds as number))
        : 0,
      last_played: Number.isSafeInteger(item.last_played)
        ? Math.max(0, Math.min(4_000_000_000, item.last_played as number))
        : 0,
      favorite: item.favorite === true,
      achievements: {
        unlocked_count: unlockedCount,
        total_count: totalCount,
        percentage: totalCount ? Math.round((unlockedCount / totalCount) * 1000) / 10 : 0,
        recent: cleanRecent,
      },
    });
  }
  const cleanAchievements: Array<Record<string, string | number>> = [];
  if (
    !Array.isArray(profile.recent_achievements) ||
    profile.recent_achievements.length > 20
  ) {
    throw new ApiError(
      400,
      "invalid_achievements",
      "Recent achievement list is invalid.",
    );
  }
  for (const raw of profile.recent_achievements) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw))
      throw new ApiError(
        400,
        "invalid_achievements",
        "Recent achievement list is invalid.",
      );
    const item = raw as Record<string, unknown>;
    const name = boundedString(item.name, 120);
    const game = boundedString(item.game, 120);
    if (!name || !game)
      throw new ApiError(
        400,
        "invalid_achievements",
        "Recent achievement is invalid.",
      );
    cleanAchievements.push({
      app_id: boundedString(item.app_id, 16),
      api_name: boundedString(item.api_name, 128),
      name,
      game,
      unlocked_at: Number.isSafeInteger(item.unlocked_at)
        ? Math.max(0, item.unlocked_at as number)
        : 0,
    });
  }
  const clean = {
    schema_version: 1,
    handle: profile.handle,
    display_name: displayName,
    avatar: cleanAvatar,
    background,
    stats: {
      games_count: stat("games_count"),
      favorite_count: stat("favorite_count"),
      playtime_seconds: stat("playtime_seconds"),
      achievements_unlocked: stat("achievements_unlocked"),
      achievements_known: Math.max(
        stat("achievements_known"),
        stat("achievements_unlocked"),
      ),
    },
    games: cleanLibrary,
    favorite_games: cleanGames,
    recent_achievements: cleanAchievements,
    updated_at: Number.isSafeInteger(profile.updated_at)
      ? Math.max(0, profile.updated_at as number)
      : 0,
  };
  const serialized = JSON.stringify(clean);
  if (!serialized || serialized.length > 768 * 1024) {
    throw new ApiError(
      413,
      "profile_too_large",
      "Public profile exceeds the size limit.",
    );
  }
  return serialized;
}
