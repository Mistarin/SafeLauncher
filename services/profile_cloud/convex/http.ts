import { httpRouter } from "convex/server";
import { httpAction } from "./_generated/server";
import { internal } from "./_generated/api";
import {
  ApiError,
  jsonResponse,
  ownerToken,
  readJsonBody,
  sha256,
  validHandle,
  validRequestId,
  validatePublicProfile,
} from "./lib/api";

const http = httpRouter();
const SERVICE_VERSION = "1.1.0";

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
    unauthorized: "The profile owner token is invalid.",
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
