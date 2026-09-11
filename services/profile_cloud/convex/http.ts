import { httpRouter } from "convex/server";
import { httpAction } from "./_generated/server";
import { internal } from "./_generated/api";
import {
  ApiError,
  jsonResponse,
  ownerToken,
  requireCentralIdentity,
  requireGateway,
  readJsonBody,
  sha256,
  validHandle,
  validRequestId,
  validatePublicProfile,
} from "./lib/api";

const http = httpRouter();
const SERVICE_VERSION = "0.2.0";

function generatedHandle(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function profilePayload(profile: any): Record<string, unknown> {
  return {
    profile: JSON.parse(profile.profile),
    revision: profile.revision,
    updatedAt: profile.updatedAt,
  };
}

function throwProfileError(result: any): void {
  const error = result?.error;
  if (!error) return;
  const statuses: Record<string, number> = {
    not_found: 404,
    unauthorized: 401,
    claimed: 409,
    identity_has_profile: 409,
    profile_exists_for_identity: 409,
    exists: 409,
    conflict: 409,
  };
  const messages: Record<string, string> = {
    not_found: "Profile not found.",
    unauthorized: "The central login is not authorized for this profile.",
    claimed: "This profile is already linked to another account.",
    identity_has_profile: "This central account already owns a profile.",
    profile_exists_for_identity: "This central account already owns a profile.",
    exists: "Profile handle is already in use.",
    conflict: "The profile changed on another device.",
  };
  throw new ApiError(
    statuses[error] || 400,
    error,
    messages[error] || "The profile operation could not be completed.",
    error === "conflict" ? { revision: result.revision } : undefined,
  );
}

async function enforceWriteLimit(
  ctx: any,
  ownerIdentityHash: string,
  operation: string,
  limit: number,
  windowSeconds: number,
): Promise<void> {
  const result = await ctx.runMutation(internal.rate_limits.consume, {
    bucketKey: `${ownerIdentityHash}:${operation}`,
    limit,
    windowSeconds,
  });
  if (!result.allowed)
    throw new ApiError(429, "rate_limited", "Too many profile operations; try again later.", {
      retryAfter: result.retryAfter,
    });
}

function throwSocialError(result: any): void {
  const error = result?.error;
  if (!error) return;
  const statuses: Record<string, number> = {
    unauthorized: 401,
    not_found: 404,
    target_not_found: 404,
    request_not_found: 404,
    profile_exists: 409,
    blocked: 409,
    self_request: 400,
    self_block: 400,
    invalid_action: 400,
    request_limit: 429,
    friend_limit: 409,
    block_limit: 409,
  };
  const messages: Record<string, string> = {
    unauthorized: "The central login is not authorized for this profile.",
    not_found: "Profile not found.",
    target_not_found: "The target profile was not found.",
    request_not_found: "The friend request is no longer pending.",
    blocked: "This profile relationship is blocked.",
    self_request: "You cannot send a friend request to yourself.",
    self_block: "You cannot block yourself.",
    invalid_action: "The friend request action is invalid.",
    request_limit: "You have reached the pending friend request limit.",
    friend_limit: "You have reached the friend limit.",
    block_limit: "You have reached the blocked-profile limit.",
  };
  throw new ApiError(
    statuses[error] || 400,
    error,
    messages[error] || "The social operation could not be completed.",
  );
}

async function dispatch(
  method: string,
  ctx: any,
  req: Request,
): Promise<Response> {
  try {
    requireGateway(req);
    const url = new URL(req.url);
    const match = url.pathname.match(
      /^\/api\/profile\/v1\/([^/]+)(\/rotate-token)?$/,
    );
    if (url.pathname === "/api/health" && method === "GET") {
      return jsonResponse({
        ok: true,
        service: "safelauncher-public-profiles",
        version: SERVICE_VERSION,
      });
    }

    const availabilityMatch = url.pathname.match(
      /^\/api\/profile\/v1\/handles\/([^/]+)\/availability$/,
    );
    if (availabilityMatch) {
      if (method !== "GET")
        throw new ApiError(405, "method_not_allowed", "Method not allowed.");
      let handle = "";
      try {
        handle = decodeURIComponent(availabilityMatch[1]).trim().toLowerCase();
      } catch {
        throw new ApiError(400, "invalid_handle", "The profile handle is invalid.");
      }
      if (!validHandle(handle))
        throw new ApiError(400, "invalid_handle", "The profile handle is invalid.");
      const result = await ctx.runQuery(internal.profiles.handleAvailable, { handle });
      // Availability is a uniqueness hint, not cacheable public profile data;
      // the create mutation remains the authoritative race-safe check.
      return jsonResponse(result);
    }

    if (url.pathname === "/api/telemetry/ping" && method === "POST") {
      const body = await readJsonBody(req);
      const clientId = body.clientId;
      const appVersion = body.appVersion;
      const platform = body.platform;
      if (
        typeof clientId !== "string" ||
        !/^[A-Za-z0-9_-]{16,128}$/.test(clientId) ||
        typeof appVersion !== "string" ||
        !/^[A-Za-z0-9._-]{1,32}$/.test(appVersion.trim()) ||
        typeof platform !== "string" ||
        !/^[A-Za-z0-9._-]{1,16}$/.test(platform.trim())
      ) {
        throw new ApiError(400, "invalid_telemetry", "Telemetry payload is invalid.");
      }
      const clientHash = await sha256(`safelauncher-telemetry:${clientId}`);
      await enforceWriteLimit(ctx, clientHash, "telemetry", 24, 86400);
      await ctx.runMutation(internal.telemetry.record, {
        clientHash,
        appVersion: appVersion.trim(),
        platform: platform.trim().toLowerCase(),
      });
      return jsonResponse({ accepted: true });
    }

    const v2SocialMatch = url.pathname.match(
      /^\/api\/profile\/v2\/me\/(friends|friend-requests|blocks)(?:\/([^/]+)(?:\/(accept|decline|cancel))?)?$/,
    );
    if (url.pathname === "/api/profile/v2/me" || v2SocialMatch) {
      const { ownerIdentityHash } = await requireCentralIdentity(ctx, req);
      const owner = await ctx.runQuery(internal.profiles.getByIdentity, {
        ownerIdentityHash,
      });

      if (!v2SocialMatch) {
        if (method === "GET") {
          return jsonResponse(owner ? profilePayload(owner) : { profile: null });
        }
        if (method === "POST") {
          await enforceWriteLimit(ctx, ownerIdentityHash, "profile-write", 30, 600);
          const body = await readJsonBody(req);
          const raw = body.profile;
          if (!raw || typeof raw !== "object" || Array.isArray(raw))
            throw new ApiError(400, "invalid_profile", "Profile must be an object.");
          const candidate = { ...(raw as Record<string, unknown>) };
          const handle = validHandle(candidate.handle)
            ? candidate.handle
            : generatedHandle();
          candidate.handle = handle;
          const serialized = validatePublicProfile(candidate);
          if (owner)
            throw new ApiError(409, "profile_exists_for_identity", "This central account already owns a profile.");
          const result = await ctx.runMutation(internal.profiles.createForIdentity, {
            handle,
            ownerIdentityHash,
            profile: serialized,
          });
          throwProfileError(result);
          return jsonResponse(result);
        }
        if (method === "PUT") {
          await enforceWriteLimit(ctx, ownerIdentityHash, "profile-write", 30, 600);
          if (!owner) throw new ApiError(404, "not_found", "Profile not found.");
          const body = await readJsonBody(req);
          const revision = body.revision;
          if (typeof revision !== "number" || !Number.isSafeInteger(revision))
            throw new ApiError(400, "invalid_revision", "A numeric revision is required.");
          const profile = body.profile as Record<string, unknown>;
          const serialized = validatePublicProfile(profile);
          if (profile.handle !== owner.handle)
            throw new ApiError(400, "handle_mismatch", "The profile handle cannot be changed.");
          const result = await ctx.runMutation(internal.profiles.update, {
            handle: owner.handle,
            ownerTokenHash: "",
            ownerIdentityHash,
            profile: serialized,
            revision,
          });
          throwProfileError(result);
          return jsonResponse(result);
        }
        if (method === "DELETE") {
          await enforceWriteLimit(ctx, ownerIdentityHash, "profile-write", 10, 600);
          if (!owner) return jsonResponse({ deleted: false });
          const result = await ctx.runMutation(internal.profiles.remove, {
            handle: owner.handle,
            ownerTokenHash: "",
            ownerIdentityHash,
          });
          throwProfileError(result);
          return jsonResponse({ deleted: true });
        }
        throw new ApiError(405, "method_not_allowed", "Method not allowed.");
      }

      if (!owner)
        throw new ApiError(404, "profile_required", "Create a public profile first.");
      const resource = v2SocialMatch[1];
      const identifier = v2SocialMatch[2];
      const action = v2SocialMatch[3];
      const handle = owner.handle;
      if (method === "GET" && resource === "friends" && !identifier && !action) {
        const result = await ctx.runQuery(internal.profiles.getSocial, {
          handle,
          ownerTokenHash: "",
          ownerIdentityHash,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }
      if (resource === "friend-requests" && method === "POST" && !identifier) {
        await enforceWriteLimit(ctx, ownerIdentityHash, "friend-request", 20, 3600);
        const body = await readJsonBody(req);
        if (!validHandle(body.targetHandle))
          throw new ApiError(400, "invalid_handle", "A valid target handle is required.");
        const result = await ctx.runMutation(internal.profiles.createFriendRequest, {
          requesterHandle: handle,
          ownerTokenHash: "",
          ownerIdentityHash,
          recipientHandle: body.targetHandle,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }
      if (resource === "friend-requests" && method === "POST" && identifier && action) {
        await enforceWriteLimit(ctx, ownerIdentityHash, "friend-response", 60, 600);
        if (!validRequestId(identifier))
          throw new ApiError(400, "invalid_request", "The friend request identifier is invalid.");
        const result = await ctx.runMutation(internal.profiles.respondFriendRequest, {
          requestId: identifier as any,
          handle,
          ownerTokenHash: "",
          ownerIdentityHash,
          action,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }
      if (resource === "friends" && method === "DELETE" && identifier && !action) {
        await enforceWriteLimit(ctx, ownerIdentityHash, "relationship-write", 60, 600);
        if (!validHandle(identifier))
          throw new ApiError(400, "invalid_handle", "A valid friend handle is required.");
        const result = await ctx.runMutation(internal.profiles.removeFriend, {
          handle,
          ownerTokenHash: "",
          ownerIdentityHash,
          friendHandle: identifier,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }
      if (resource === "blocks" && method === "POST" && !identifier) {
        await enforceWriteLimit(ctx, ownerIdentityHash, "relationship-write", 60, 600);
        const body = await readJsonBody(req);
        if (!validHandle(body.targetHandle))
          throw new ApiError(400, "invalid_handle", "A valid target handle is required.");
        const result = await ctx.runMutation(internal.profiles.blockUser, {
          handle,
          ownerTokenHash: "",
          ownerIdentityHash,
          blockedHandle: body.targetHandle,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }
      if (resource === "blocks" && method === "DELETE" && identifier && !action) {
        await enforceWriteLimit(ctx, ownerIdentityHash, "relationship-write", 60, 600);
        if (!validHandle(identifier))
          throw new ApiError(400, "invalid_handle", "A valid blocked handle is required.");
        const result = await ctx.runMutation(internal.profiles.unblockUser, {
          handle,
          ownerTokenHash: "",
          ownerIdentityHash,
          blockedHandle: identifier,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }
      throw new ApiError(405, "method_not_allowed", "Method not allowed.");
    }

    if (url.pathname === "/api/profile/v2/me/claim" && method === "POST") {
      const { ownerIdentityHash } = await requireCentralIdentity(ctx, req);
      await enforceWriteLimit(ctx, ownerIdentityHash, "profile-claim", 5, 3600);
      const body = await readJsonBody(req);
      if (!validHandle(body.handle))
        throw new ApiError(400, "invalid_handle", "A valid profile handle is required.");
      const token = body.ownerToken;
      if (typeof token !== "string" || token.length < 32 || token.length > 256)
        throw new ApiError(400, "invalid_token", "A valid legacy profile token is required.");
      const result = await ctx.runMutation(internal.profiles.claimLegacy, {
        handle: body.handle,
        ownerTokenHash: await sha256(token),
        ownerIdentityHash,
      });
      throwProfileError(result);
      return jsonResponse(result);
    }

    if (url.pathname === "/api/profile/v1" && method === "POST") {
      const body = await readJsonBody(req);
      const handle = body.handle;
      const token = body.ownerToken;
      if (
        !validHandle(handle) ||
        typeof token !== "string" ||
        token.length < 32 ||
        token.length > 256
      ) {
        throw new ApiError(
          400,
          "invalid_identity",
          "A valid handle and owner token are required.",
        );
      }
      const profile = body.profile as Record<string, unknown>;
      const serialized = validatePublicProfile(profile);
      if (profile.handle !== handle)
        throw new ApiError(
          400,
          "handle_mismatch",
          "Profile handle does not match the route identity.",
        );
      const result = await ctx.runMutation(internal.profiles.create, {
        handle,
        ownerTokenHash: await sha256(token),
        profile: serialized,
      });
      if (result.error === "exists")
        throw new ApiError(
          409,
          "profile_exists",
          "Profile handle is already in use.",
        );
      return jsonResponse(result);
    }

    const socialMatch = url.pathname.match(
      /^\/api\/profile\/v1\/([^/]+)\/(friends|friend-requests|blocks)(?:\/([^/]+)(?:\/(accept|decline|cancel))?)?$/,
    );
    if (socialMatch) {
      const handle = socialMatch[1];
      const resource = socialMatch[2];
      const identifier = socialMatch[3];
      const action = socialMatch[4];
      if (!validHandle(handle))
        throw new ApiError(404, "not_found", "Profile not found.");

      const tokenHash = await sha256(ownerToken(req));
      if (
        method === "GET" &&
        resource === "friends" &&
        !identifier &&
        !action
      ) {
        const result = await ctx.runQuery(internal.profiles.getSocial, {
          handle,
          ownerTokenHash: tokenHash,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }

      if (resource === "friend-requests" && method === "POST" && !identifier) {
        const body = await readJsonBody(req);
        const targetHandle = body.targetHandle;
        if (!validHandle(targetHandle))
          throw new ApiError(
            400,
            "invalid_handle",
            "A valid target handle is required.",
          );
        const result = await ctx.runMutation(
          internal.profiles.createFriendRequest,
          {
            requesterHandle: handle,
            ownerTokenHash: tokenHash,
            recipientHandle: targetHandle,
          },
        );
        throwSocialError(result);
        return jsonResponse(result);
      }

      if (
        resource === "friend-requests" &&
        method === "POST" &&
        Boolean(identifier) &&
        Boolean(action)
      ) {
        if (!validRequestId(identifier))
          throw new ApiError(
            400,
            "invalid_request",
            "The friend request identifier is invalid.",
          );
        const result = await ctx.runMutation(
          internal.profiles.respondFriendRequest,
          {
            requestId: identifier as any,
            handle,
            ownerTokenHash: tokenHash,
            action,
          },
        );
        throwSocialError(result);
        return jsonResponse(result);
      }

      if (
        resource === "friends" &&
        method === "DELETE" &&
        identifier &&
        !action
      ) {
        if (!validHandle(identifier))
          throw new ApiError(
            400,
            "invalid_handle",
            "A valid friend handle is required.",
          );
        const result = await ctx.runMutation(internal.profiles.removeFriend, {
          handle,
          ownerTokenHash: tokenHash,
          friendHandle: identifier,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }

      if (resource === "blocks" && method === "POST" && !identifier) {
        const body = await readJsonBody(req);
        const blockedHandle = body.targetHandle;
        if (!validHandle(blockedHandle))
          throw new ApiError(
            400,
            "invalid_handle",
            "A valid target handle is required.",
          );
        const result = await ctx.runMutation(internal.profiles.blockUser, {
          handle,
          ownerTokenHash: tokenHash,
          blockedHandle,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }

      if (
        resource === "blocks" &&
        method === "DELETE" &&
        identifier &&
        !action
      ) {
        if (!validHandle(identifier))
          throw new ApiError(
            400,
            "invalid_handle",
            "A valid blocked handle is required.",
          );
        const result = await ctx.runMutation(internal.profiles.unblockUser, {
          handle,
          ownerTokenHash: tokenHash,
          blockedHandle: identifier,
        });
        throwSocialError(result);
        return jsonResponse(result);
      }
      throw new ApiError(405, "method_not_allowed", "Method not allowed.");
    }

    if (!match || !validHandle(match[1]))
      throw new ApiError(404, "not_found", "Profile not found.");
    const handle = match[1];
    const rotating = Boolean(match[2]);
    if (rotating && method !== "POST")
      throw new ApiError(405, "method_not_allowed", "Method not allowed.");
    if (method === "GET") {
      const profile = await ctx.runQuery(internal.profiles.get, { handle });
      if (!profile) throw new ApiError(404, "not_found", "Profile not found.");
      return jsonResponse(
        {
          profile: JSON.parse(profile.profile),
          revision: profile.revision,
          updatedAt: profile.updatedAt,
        },
        200,
        true,
      );
    }
    const token = ownerToken(req);
    const tokenHash = await sha256(token);
    if (rotating && method === "POST") {
      const body = await readJsonBody(req);
      const newToken = body.ownerToken;
      if (
        typeof newToken !== "string" ||
        newToken.length < 32 ||
        newToken.length > 256
      )
        throw new ApiError(400, "invalid_token", "New owner token is invalid.");
      const result = await ctx.runMutation(internal.profiles.rotateToken, {
        handle,
        ownerTokenHash: tokenHash,
        newOwnerTokenHash: await sha256(newToken),
      });
      if (result.error === "not_found")
        throw new ApiError(404, "not_found", "Profile not found.");
      if (result.error === "unauthorized")
        throw new ApiError(401, "unauthorized", "Owner token is invalid.");
      return jsonResponse({ rotated: true });
    }
    if (method === "PUT") {
      const body = await readJsonBody(req);
      const revision = body.revision;
      if (typeof revision !== "number" || !Number.isSafeInteger(revision))
        throw new ApiError(
          400,
          "invalid_revision",
          "A numeric revision is required.",
        );
      const profile = body.profile as Record<string, unknown>;
      const serialized = validatePublicProfile(profile);
      if (profile.handle !== handle)
        throw new ApiError(
          400,
          "handle_mismatch",
          "Profile handle does not match the route identity.",
        );
      const result = await ctx.runMutation(internal.profiles.update, {
        handle,
        ownerTokenHash: tokenHash,
        profile: serialized,
        revision,
      });
      if (result.error === "not_found")
        throw new ApiError(404, "not_found", "Profile not found.");
      if (result.error === "unauthorized")
        throw new ApiError(401, "unauthorized", "Owner token is invalid.");
      if (result.error === "conflict")
        throw new ApiError(
          409,
          "revision_conflict",
          "Profile revision is stale.",
          { revision: result.revision },
        );
      return jsonResponse(result);
    }
    if (method === "DELETE") {
      const result = await ctx.runMutation(internal.profiles.remove, {
        handle,
        ownerTokenHash: tokenHash,
      });
      if (result.error === "not_found")
        throw new ApiError(404, "not_found", "Profile not found.");
      if (result.error === "unauthorized")
        throw new ApiError(401, "unauthorized", "Owner token is invalid.");
      return jsonResponse({ deleted: true });
    }
    throw new ApiError(405, "method_not_allowed", "Method not allowed.");
  } catch (error) {
    if (error instanceof ApiError)
      return jsonResponse(
        { error: error.message, code: error.code, ...(error.extra || {}) },
        error.status,
      );
    console.error("Unhandled public profile API error", error);
    return jsonResponse(
      { error: "internal_error", code: "internal_error" },
      500,
    );
  }
}

for (const method of ["GET", "POST", "PUT", "DELETE"] as const) {
  http.route({
    pathPrefix: "/api/",
    method,
    handler: httpAction((ctx, req) => dispatch(method, ctx, req)),
  });
}

export default http;
