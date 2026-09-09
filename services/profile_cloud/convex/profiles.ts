import { internalMutation, internalQuery } from "./_generated/server";
import { v } from "convex/values";
import { constantTimeEqual } from "./lib/api";

const PENDING = "pending";
const ACCEPTED = "accepted";
const DECLINED = "declined";
const CANCELED = "canceled";
const BLOCKED = "blocked";
const MAX_FRIENDS = 100;
const MAX_PENDING_REQUESTS = 50;
const MAX_BLOCKS = 100;

function pairKey(left: string, right: string): string {
  return [left, right].sort().join(":");
}

async function getProfile(ctx: any, handle: string): Promise<any | null> {
  return await ctx.db
    .query("publicProfiles")
    .withIndex("by_handle", (q: any) => q.eq("handle", handle))
    .unique();
}

async function authorize(
  ctx: any,
  handle: string,
  ownerTokenHash: string,
): Promise<any> {
  const profile = await getProfile(ctx, handle);
  if (!profile) return { error: "not_found" };
  if (!constantTimeEqual(profile.ownerTokenHash, ownerTokenHash))
    return { error: "unauthorized" };
  return profile;
}

function profileSummary(profile: any): Record<string, unknown> | null {
  try {
    const value = JSON.parse(profile.profile) as Record<string, unknown>;
    return {
      handle: profile.handle,
      display_name:
        typeof value.display_name === "string" ? value.display_name : "Player",
      updated_at:
        typeof value.updated_at === "number" &&
        Number.isSafeInteger(value.updated_at)
          ? value.updated_at
          : 0,
    };
  } catch {
    return null;
  }
}

async function getPendingRequest(
  ctx: any,
  requesterHandle: string,
  recipientHandle: string,
): Promise<any | null> {
  const rows = await ctx.db
    .query("friendRequests")
    .withIndex("by_requester_status", (q: any) =>
      q.eq("requesterHandle", requesterHandle).eq("status", PENDING),
    )
    .collect();
  return (
    rows.find((row: any) => row.recipientHandle === recipientHandle) || null
  );
}

async function isBlocked(
  ctx: any,
  left: string,
  right: string,
): Promise<boolean> {
  const direct = await ctx.db
    .query("profileBlocks")
    .withIndex("by_pair", (q: any) =>
      q.eq("blockerHandle", left).eq("blockedHandle", right),
    )
    .unique();
  if (direct) return true;
  const reverse = await ctx.db
    .query("profileBlocks")
    .withIndex("by_pair", (q: any) =>
      q.eq("blockerHandle", right).eq("blockedHandle", left),
    )
    .unique();
  return Boolean(reverse);
}

async function findFriendship(
  ctx: any,
  left: string,
  right: string,
): Promise<any | null> {
  return await ctx.db
    .query("friendships")
    .withIndex("by_pair", (q: any) => q.eq("pairKey", pairKey(left, right)))
    .unique();
}

async function friendCount(ctx: any, handle: string): Promise<number> {
  const [left, right] = await Promise.all([
    ctx.db
      .query("friendships")
      .withIndex("by_member_a", (q: any) => q.eq("memberA", handle))
      .collect(),
    ctx.db
      .query("friendships")
      .withIndex("by_member_b", (q: any) => q.eq("memberB", handle))
      .collect(),
  ]);
  return left.length + right.length;
}

export const get = internalQuery({
  args: { handle: v.string() },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("publicProfiles")
      .withIndex("by_handle", (q) => q.eq("handle", args.handle))
      .unique();
  },
});

export const getSocial = internalQuery({
  args: { handle: v.string(), ownerTokenHash: v.string() },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash);
    if (owner.error) return owner;

    const [memberA, memberB, incoming, outgoing, blocked] = await Promise.all([
      ctx.db
        .query("friendships")
        .withIndex("by_member_a", (q: any) => q.eq("memberA", args.handle))
        .collect(),
      ctx.db
        .query("friendships")
        .withIndex("by_member_b", (q: any) => q.eq("memberB", args.handle))
        .collect(),
      ctx.db
        .query("friendRequests")
        .withIndex("by_recipient_status", (q: any) =>
          q.eq("recipientHandle", args.handle).eq("status", PENDING),
        )
        .collect(),
      ctx.db
        .query("friendRequests")
        .withIndex("by_requester_status", (q: any) =>
          q.eq("requesterHandle", args.handle).eq("status", PENDING),
        )
        .collect(),
      ctx.db
        .query("profileBlocks")
        .withIndex("by_blocker", (q: any) => q.eq("blockerHandle", args.handle))
        .collect(),
    ]);

    const friendHandles = new Set<string>();
    for (const friendship of [...memberA, ...memberB]) {
      const friend =
        friendship.memberA === args.handle
          ? friendship.memberB
          : friendship.memberA;
      friendHandles.add(friend);
    }

    const summaryFor = async (handle: string) => {
      const profile = await getProfile(ctx, handle);
      return profile ? profileSummary(profile) : null;
    };
    const friends = (
      await Promise.all([...friendHandles].map(summaryFor))
    ).filter(Boolean);
    const incomingRequests = (
      await Promise.all(
        incoming.map(async (request: any) => {
          const summary = await summaryFor(request.requesterHandle);
          return summary
            ? {
                ...summary,
                request_id: String(request._id),
                created_at: request.createdAt,
              }
            : null;
        }),
      )
    ).filter(Boolean);
    const outgoingRequests = (
      await Promise.all(
        outgoing.map(async (request: any) => {
          const summary = await summaryFor(request.recipientHandle);
          return summary
            ? {
                ...summary,
                request_id: String(request._id),
                created_at: request.createdAt,
              }
            : null;
        }),
      )
    ).filter(Boolean);

    return {
      friends,
      incoming_requests: incomingRequests,
      outgoing_requests: outgoingRequests,
      blocked_handles: blocked.map((item: any) => item.blockedHandle),
    };
  },
});

export const create = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string(), profile: v.string() },
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query("publicProfiles")
      .withIndex("by_handle", (q) => q.eq("handle", args.handle))
      .unique();
    if (existing) return { error: "exists" };
    const now = Date.now();
    await ctx.db.insert("publicProfiles", {
      handle: args.handle,
      ownerTokenHash: args.ownerTokenHash,
      profile: args.profile,
      revision: 1,
      createdAt: now,
      updatedAt: now,
    });
    return { revision: 1, updatedAt: now };
  },
});

export const createFriendRequest = internalMutation({
  args: {
    requesterHandle: v.string(),
    ownerTokenHash: v.string(),
    recipientHandle: v.string(),
  },
  handler: async (ctx, args) => {
    const requester = await authorize(
      ctx,
      args.requesterHandle,
      args.ownerTokenHash,
    );
    if (requester.error) return requester;
    if (args.requesterHandle === args.recipientHandle)
      return { error: "self_request" };
    const recipient = await getProfile(ctx, args.recipientHandle);
    if (!recipient) return { error: "target_not_found" };
    if (await isBlocked(ctx, args.requesterHandle, args.recipientHandle))
      return { error: "blocked" };

    const friendship = await findFriendship(
      ctx,
      args.requesterHandle,
      args.recipientHandle,
    );
    if (friendship) return { status: ACCEPTED };
    const existing = await getPendingRequest(
      ctx,
      args.requesterHandle,
      args.recipientHandle,
    );
    if (existing) return { status: PENDING, requestId: String(existing._id) };

    const reverse = await getPendingRequest(
      ctx,
      args.recipientHandle,
      args.requesterHandle,
    );
    const now = Date.now();
    if (reverse) {
      const [requesterFriendCount, recipientFriendCount] = await Promise.all([
        friendCount(ctx, args.requesterHandle),
        friendCount(ctx, args.recipientHandle),
      ]);
      if (
        requesterFriendCount >= MAX_FRIENDS ||
        recipientFriendCount >= MAX_FRIENDS
      )
        return { error: "friend_limit" };
      await ctx.db.patch(reverse._id, { status: ACCEPTED, updatedAt: now });
      await ctx.db.insert("friendships", {
        pairKey: pairKey(args.requesterHandle, args.recipientHandle),
        memberA: [args.requesterHandle, args.recipientHandle].sort()[0],
        memberB: [args.requesterHandle, args.recipientHandle].sort()[1],
        createdAt: now,
        updatedAt: now,
      });
      return { status: ACCEPTED };
    }

    const pending = await ctx.db
      .query("friendRequests")
      .withIndex("by_requester_status", (q: any) =>
        q.eq("requesterHandle", args.requesterHandle).eq("status", PENDING),
      )
      .collect();
    if (pending.length >= MAX_PENDING_REQUESTS)
      return { error: "request_limit" };
    const requestId = await ctx.db.insert("friendRequests", {
      requesterHandle: args.requesterHandle,
      recipientHandle: args.recipientHandle,
      status: PENDING,
      createdAt: now,
      updatedAt: now,
    });
    return { status: PENDING, requestId: String(requestId) };
  },
});

export const respondFriendRequest = internalMutation({
  args: {
    requestId: v.id("friendRequests"),
    handle: v.string(),
    ownerTokenHash: v.string(),
    action: v.string(),
  },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash);
    if (owner.error) return owner;
    if (!["accept", "decline", "cancel"].includes(args.action))
      return { error: "invalid_action" };
    const request = await ctx.db.get(args.requestId);
    if (!request || request.status !== PENDING)
      return { error: "request_not_found" };
    const recipientAction =
      args.action === "accept" || args.action === "decline";
    const ownsRequest = recipientAction
      ? request.recipientHandle === args.handle
      : request.requesterHandle === args.handle;
    if (!ownsRequest) return { error: "unauthorized" };
    const now = Date.now();
    if (args.action === "accept") {
      if (
        await isBlocked(ctx, request.requesterHandle, request.recipientHandle)
      )
        return { error: "blocked" };
      const existing = await findFriendship(
        ctx,
        request.requesterHandle,
        request.recipientHandle,
      );
      if (!existing) {
        const [recipientFriendCount, requesterFriendCount] = await Promise.all([
          friendCount(ctx, request.recipientHandle),
          friendCount(ctx, request.requesterHandle),
        ]);
        if (
          recipientFriendCount >= MAX_FRIENDS ||
          requesterFriendCount >= MAX_FRIENDS
        )
          return { error: "friend_limit" };
      }
      await ctx.db.patch(request._id, { status: ACCEPTED, updatedAt: now });
      if (!existing) {
        await ctx.db.insert("friendships", {
          pairKey: pairKey(request.requesterHandle, request.recipientHandle),
          memberA: [request.requesterHandle, request.recipientHandle].sort()[0],
          memberB: [request.requesterHandle, request.recipientHandle].sort()[1],
          createdAt: now,
          updatedAt: now,
        });
      }
      return { status: ACCEPTED };
    }
    await ctx.db.patch(request._id, {
      status: args.action === "decline" ? DECLINED : CANCELED,
      updatedAt: now,
    });
    return { status: args.action === "decline" ? DECLINED : CANCELED };
  },
});

export const removeFriend = internalMutation({
  args: {
    handle: v.string(),
    ownerTokenHash: v.string(),
    friendHandle: v.string(),
  },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash);
    if (owner.error) return owner;
    const friendship = await findFriendship(
      ctx,
      args.handle,
      args.friendHandle,
    );
    if (!friendship) return { removed: false };
    await ctx.db.delete(friendship._id);
    return { removed: true };
  },
});

export const blockUser = internalMutation({
  args: {
    handle: v.string(),
    ownerTokenHash: v.string(),
    blockedHandle: v.string(),
  },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash);
    if (owner.error) return owner;
    if (args.handle === args.blockedHandle) return { error: "self_block" };
    if (!(await getProfile(ctx, args.blockedHandle)))
      return { error: "target_not_found" };
    const blockCount = await ctx.db
      .query("profileBlocks")
      .withIndex("by_blocker", (q: any) => q.eq("blockerHandle", args.handle))
      .collect();
    const existing = await ctx.db
      .query("profileBlocks")
      .withIndex("by_pair", (q: any) =>
        q
          .eq("blockerHandle", args.handle)
          .eq("blockedHandle", args.blockedHandle),
      )
      .unique();
    if (!existing && blockCount.length >= MAX_BLOCKS)
      return { error: "block_limit" };
    if (!existing)
      await ctx.db.insert("profileBlocks", {
        blockerHandle: args.handle,
        blockedHandle: args.blockedHandle,
        createdAt: Date.now(),
      });
    const friendship = await findFriendship(
      ctx,
      args.handle,
      args.blockedHandle,
    );
    if (friendship) await ctx.db.delete(friendship._id);
    const [outgoing, incoming] = await Promise.all([
      ctx.db
        .query("friendRequests")
        .withIndex("by_requester_status", (q: any) =>
          q.eq("requesterHandle", args.handle).eq("status", PENDING),
        )
        .collect(),
      ctx.db
        .query("friendRequests")
        .withIndex("by_recipient_status", (q: any) =>
          q.eq("recipientHandle", args.handle).eq("status", PENDING),
        )
        .collect(),
    ]);
    for (const request of [...outgoing, ...incoming]) {
      if (
        (request.requesterHandle === args.handle &&
          request.recipientHandle === args.blockedHandle) ||
        (request.recipientHandle === args.handle &&
          request.requesterHandle === args.blockedHandle)
      ) {
        await ctx.db.patch(request._id, {
          status: BLOCKED,
          updatedAt: Date.now(),
        });
      }
    }
    return { blocked: true };
  },
});

export const unblockUser = internalMutation({
  args: {
    handle: v.string(),
    ownerTokenHash: v.string(),
    blockedHandle: v.string(),
  },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash);
    if (owner.error) return owner;
    const existing = await ctx.db
      .query("profileBlocks")
      .withIndex("by_pair", (q: any) =>
        q
          .eq("blockerHandle", args.handle)
          .eq("blockedHandle", args.blockedHandle),
      )
      .unique();
    if (existing) await ctx.db.delete(existing._id);
    return { unblocked: true };
  },
});

export const update = internalMutation({
  args: {
    handle: v.string(),
    ownerTokenHash: v.string(),
    profile: v.string(),
    revision: v.number(),
  },
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query("publicProfiles")
      .withIndex("by_handle", (q) => q.eq("handle", args.handle))
      .unique();
    if (!existing) return { error: "not_found" };
    if (!constantTimeEqual(existing.ownerTokenHash, args.ownerTokenHash))
      return { error: "unauthorized" };
    if (existing.revision !== args.revision)
      return { error: "conflict", revision: existing.revision };
    const revision = existing.revision + 1;
    const updatedAt = Date.now();
    await ctx.db.patch(existing._id, {
      profile: args.profile,
      revision,
      updatedAt,
    });
    return { revision, updatedAt };
  },
});

export const remove = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string() },
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query("publicProfiles")
      .withIndex("by_handle", (q) => q.eq("handle", args.handle))
      .unique();
    if (!existing) return { error: "not_found" };
    if (!constantTimeEqual(existing.ownerTokenHash, args.ownerTokenHash))
      return { error: "unauthorized" };

    const [outgoing, incoming, memberA, memberB, blocked, blockedBy] =
      await Promise.all([
        ctx.db
          .query("friendRequests")
          .withIndex("by_requester_status", (q: any) =>
            q.eq("requesterHandle", args.handle),
          )
          .collect(),
        ctx.db
          .query("friendRequests")
          .withIndex("by_recipient_status", (q: any) =>
            q.eq("recipientHandle", args.handle),
          )
          .collect(),
        ctx.db
          .query("friendships")
          .withIndex("by_member_a", (q: any) => q.eq("memberA", args.handle))
          .collect(),
        ctx.db
          .query("friendships")
          .withIndex("by_member_b", (q: any) => q.eq("memberB", args.handle))
          .collect(),
        ctx.db
          .query("profileBlocks")
          .withIndex("by_blocker", (q: any) =>
            q.eq("blockerHandle", args.handle),
          )
          .collect(),
        ctx.db
          .query("profileBlocks")
          .withIndex("by_blocked", (q: any) =>
            q.eq("blockedHandle", args.handle),
          )
          .collect(),
      ]);
    const rows = new Map<string, any>();
    for (const row of [
      ...outgoing,
      ...incoming,
      ...memberA,
      ...memberB,
      ...blocked,
      ...blockedBy,
    ])
      rows.set(String(row._id), row);
    for (const row of rows.values()) await ctx.db.delete(row._id);
    await ctx.db.delete(existing._id);
    return { deleted: true };
  },
});

export const rotateToken = internalMutation({
  args: {
    handle: v.string(),
    ownerTokenHash: v.string(),
    newOwnerTokenHash: v.string(),
  },
  handler: async (ctx, args) => {
    const existing = await ctx.db
      .query("publicProfiles")
      .withIndex("by_handle", (q) => q.eq("handle", args.handle))
      .unique();
    if (!existing) return { error: "not_found" };
    if (!constantTimeEqual(existing.ownerTokenHash, args.ownerTokenHash))
      return { error: "unauthorized" };
    await ctx.db.patch(existing._id, {
      ownerTokenHash: args.newOwnerTokenHash,
      updatedAt: Date.now(),
    });
    return { rotated: true };
  },
});
