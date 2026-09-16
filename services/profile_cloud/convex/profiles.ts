import { internalMutation, internalQuery } from "./_generated/server";
import { v } from "convex/values";
import {
  constantTimeEqual,
  normalizeUsernameHandle,
  validHandle,
  validUsernameHandle,
} from "./lib/api";

const PENDING = "pending";
const ACCEPTED = "accepted";
const DECLINED = "declined";
const CANCELED = "canceled";
const BLOCKED = "blocked";
const MAX_FRIENDS = 100;
const MAX_PENDING_REQUESTS = 50;
const MAX_BLOCKS = 100;
const MIGRATION_BATCH_SIZE = 50;

function pairKey(left: string, right: string): string {
  return [left, right].sort().join(":");
}

function profilePairKey(left: any, right: any): string {
  return [String(left), String(right)].sort().join(":");
}

function mergeRows(...groups: any[][]): any[] {
  const rows = new Map<string, any>();
  for (const row of groups.flat()) rows.set(String(row._id), row);
  return [...rows.values()];
}

async function findHandle(ctx: any, handle: string): Promise<any | null> {
  return await ctx.db
    .query("profileHandles")
    .withIndex("by_handle", (q: any) => q.eq("handle", handle))
    .unique();
}

/** Resolve both current usernames and permanent historical aliases. */
async function getProfile(ctx: any, handle: string): Promise<any | null> {
  const normalized = String(handle || "").trim().toLowerCase();
  const alias = await findHandle(ctx, normalized);
  if (alias) return await ctx.db.get(alias.profileId);
  return await ctx.db
    .query("publicProfiles")
    .withIndex("by_handle", (q: any) => q.eq("handle", normalized))
    .unique();
}

async function getProfileById(ctx: any, profileId: any): Promise<any | null> {
  return profileId ? await ctx.db.get(profileId) : null;
}

async function ensureAlias(
  ctx: any,
  handle: string,
  profileId: any,
  canonical: boolean,
): Promise<{ ok: boolean; alias?: any }> {
  const existing = await findHandle(ctx, handle);
  if (existing && existing.profileId !== profileId) return { ok: false };
  if (existing) {
    if (existing.canonical !== canonical) await ctx.db.patch(existing._id, { canonical });
    return { ok: true, alias: existing };
  }
  const id = await ctx.db.insert("profileHandles", {
    handle,
    profileId,
    canonical,
    createdAt: Date.now(),
  });
  return { ok: true, alias: await ctx.db.get(id) };
}

async function syncRelationshipHandles(ctx: any, profile: any, oldHandle: string, newHandle: string): Promise<void> {
  if (oldHandle === newHandle) return;
  const [requestsByRequester, requestsByRecipient, friendshipsA, friendshipsB, blocksByBlocker, blocksByBlocked] = await Promise.all([
    ctx.db.query("friendRequests").withIndex("by_requester_status", (q: any) => q.eq("requesterHandle", oldHandle)).collect(),
    ctx.db.query("friendRequests").withIndex("by_recipient_status", (q: any) => q.eq("recipientHandle", oldHandle)).collect(),
    ctx.db.query("friendships").withIndex("by_member_a", (q: any) => q.eq("memberA", oldHandle)).collect(),
    ctx.db.query("friendships").withIndex("by_member_b", (q: any) => q.eq("memberB", oldHandle)).collect(),
    ctx.db.query("profileBlocks").withIndex("by_blocker", (q: any) => q.eq("blockerHandle", oldHandle)).collect(),
    ctx.db.query("profileBlocks").withIndex("by_blocked", (q: any) => q.eq("blockedHandle", oldHandle)).collect(),
  ]);
  for (const row of mergeRows(requestsByRequester, requestsByRecipient)) {
    await ctx.db.patch(row._id, {
      requesterHandle: row.requesterHandle === oldHandle ? newHandle : row.requesterHandle,
      recipientHandle: row.recipientHandle === oldHandle ? newHandle : row.recipientHandle,
      requesterProfileId: row.requesterHandle === oldHandle ? profile._id : row.requesterProfileId,
      recipientProfileId: row.recipientHandle === oldHandle ? profile._id : row.recipientProfileId,
    });
  }
  for (const row of mergeRows(friendshipsA, friendshipsB)) {
    const memberA = row.memberA === oldHandle ? newHandle : row.memberA;
    const memberB = row.memberB === oldHandle ? newHandle : row.memberB;
    await ctx.db.patch(row._id, { memberA, memberB, pairKey: pairKey(memberA, memberB) });
  }
  for (const row of mergeRows(blocksByBlocker, blocksByBlocked)) {
    await ctx.db.patch(row._id, {
      blockerHandle: row.blockerHandle === oldHandle ? newHandle : row.blockerHandle,
      blockedHandle: row.blockedHandle === oldHandle ? newHandle : row.blockedHandle,
    });
  }
}

async function setCanonicalHandle(
  ctx: any,
  profile: any,
  requestedHandle: string,
): Promise<{ handle: string } | { error: string }> {
  const handle = requestedHandle.trim().toLowerCase();
  if (!validHandle(handle)) return { error: "invalid_handle" };
  const existing = await findHandle(ctx, handle);
  const existingProfile = await getProfile(ctx, handle);
  if ((existing && existing.profileId !== profile._id) ||
      (existingProfile && existingProfile._id !== profile._id)) return { error: "exists" };
  await ensureAlias(ctx, profile.handle, profile._id, false);
  const aliases = await ctx.db
    .query("profileHandles")
    .withIndex("by_profile", (q: any) => q.eq("profileId", profile._id))
    .collect();
  for (const alias of aliases) {
    if (alias.canonical) await ctx.db.patch(alias._id, { canonical: false });
  }
  const result = await ensureAlias(ctx, handle, profile._id, true);
  if (!result.ok) return { error: "exists" };
  await syncRelationshipHandles(ctx, profile, profile.handle, handle);
  return { handle };
}

function stableHash(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index++) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36).slice(0, 6);
}

function displayNameFromProfile(serialized: string): string {
  try {
    const value = JSON.parse(serialized) as Record<string, unknown>;
    return typeof value.display_name === "string" ? value.display_name : "";
  } catch {
    return "";
  }
}

async function readableHandleFor(
  ctx: any,
  displayName: string,
  seed: string,
): Promise<string> {
  const base = normalizeUsernameHandle(displayName) || "player";
  for (let suffix = 0; suffix < 10_000; suffix++) {
    const candidate = suffix === 0
      ? base
      : normalizeUsernameHandle(`${base}-${suffix + 1}`);
    if (candidate && !(await getProfile(ctx, candidate))) return candidate;
  }
  const fallbackBase = normalizeUsernameHandle(`player-${stableHash(seed)}`) || "player";
  for (let suffix = 0; suffix < 10_000; suffix++) {
    const candidate = suffix === 0
      ? fallbackBase
      : normalizeUsernameHandle(`${fallbackBase}-${suffix + 1}`);
    if (candidate && !(await getProfile(ctx, candidate))) return candidate;
  }
  throw new Error("profile_handle_space_exhausted");
}

async function authorize(
  ctx: any,
  handle: string,
  ownerTokenHash = "",
  ownerIdentityHash = "",
): Promise<any> {
  const profile = await getProfile(ctx, handle);
  if (!profile) return { error: "not_found" };
  const stored = ownerIdentityHash ? profile.ownerIdentityHash : profile.ownerTokenHash;
  const supplied = ownerIdentityHash || ownerTokenHash;
  if (typeof stored !== "string" || !constantTimeEqual(stored, supplied))
    return { error: "unauthorized" };
  return profile;
}

function profileSummary(profile: any): Record<string, unknown> | null {
  try {
    const value = JSON.parse(profile.profile) as Record<string, unknown>;
    return {
      handle: profile.handle,
      display_name: typeof value.display_name === "string" ? value.display_name : "Player",
      updated_at: typeof value.updated_at === "number" && Number.isSafeInteger(value.updated_at)
        ? value.updated_at
        : 0,
    };
  } catch {
    return null;
  }
}

async function getPendingRequest(ctx: any, requester: any, recipient: any): Promise<any | null> {
  const [byId, byHandle] = await Promise.all([
    ctx.db.query("friendRequests")
      .withIndex("by_requester_profile_status", (q: any) =>
        q.eq("requesterProfileId", requester._id).eq("status", PENDING),
      ).collect(),
    ctx.db.query("friendRequests")
      .withIndex("by_requester_status", (q: any) =>
        q.eq("requesterHandle", requester.handle).eq("status", PENDING),
      ).collect(),
  ]);
  return mergeRows(byId, byHandle).find((row: any) =>
    row.recipientProfileId === recipient._id || row.recipientHandle === recipient.handle,
  ) || null;
}

async function pendingRequestsFor(ctx: any, profile: any): Promise<any[]> {
  const [byId, byHandle] = await Promise.all([
    ctx.db.query("friendRequests")
      .withIndex("by_requester_profile_status", (q: any) =>
        q.eq("requesterProfileId", profile._id).eq("status", PENDING),
      ).collect(),
    ctx.db.query("friendRequests")
      .withIndex("by_requester_status", (q: any) =>
        q.eq("requesterHandle", profile.handle).eq("status", PENDING),
      ).collect(),
  ]);
  return mergeRows(byId, byHandle);
}

async function incomingRequestsFor(ctx: any, profile: any): Promise<any[]> {
  const [byId, byHandle] = await Promise.all([
    ctx.db.query("friendRequests")
      .withIndex("by_recipient_profile_status", (q: any) =>
        q.eq("recipientProfileId", profile._id).eq("status", PENDING),
      ).collect(),
    ctx.db.query("friendRequests")
      .withIndex("by_recipient_status", (q: any) =>
        q.eq("recipientHandle", profile.handle).eq("status", PENDING),
      ).collect(),
  ]);
  return mergeRows(byId, byHandle);
}

async function isBlocked(ctx: any, left: any, right: any): Promise<boolean> {
  const [direct, reverse, oldDirect, oldReverse] = await Promise.all([
    ctx.db.query("profileBlocks").withIndex("by_pair_profile", (q: any) =>
      q.eq("blockerProfileId", left._id).eq("blockedProfileId", right._id),
    ).unique(),
    ctx.db.query("profileBlocks").withIndex("by_pair_profile", (q: any) =>
      q.eq("blockerProfileId", right._id).eq("blockedProfileId", left._id),
    ).unique(),
    ctx.db.query("profileBlocks").withIndex("by_pair", (q: any) =>
      q.eq("blockerHandle", left.handle).eq("blockedHandle", right.handle),
    ).unique(),
    ctx.db.query("profileBlocks").withIndex("by_pair", (q: any) =>
      q.eq("blockerHandle", right.handle).eq("blockedHandle", left.handle),
    ).unique(),
  ]);
  return Boolean(direct || reverse || oldDirect || oldReverse);
}

async function directBlock(ctx: any, left: any, right: any): Promise<any | null> {
  const byId = await ctx.db.query("profileBlocks").withIndex("by_pair_profile", (q: any) =>
    q.eq("blockerProfileId", left._id).eq("blockedProfileId", right._id),
  ).unique();
  if (byId) return byId;
  return await ctx.db.query("profileBlocks").withIndex("by_pair", (q: any) =>
    q.eq("blockerHandle", left.handle).eq("blockedHandle", right.handle),
  ).unique();
}

async function findFriendship(ctx: any, left: any, right: any): Promise<any | null> {
  const byId = await ctx.db.query("friendships").withIndex("by_pair_profile", (q: any) =>
    q.eq("pairKeyProfileId", profilePairKey(left._id, right._id)),
  ).unique();
  if (byId) return byId;
  return await ctx.db.query("friendships").withIndex("by_pair", (q: any) =>
    q.eq("pairKey", pairKey(left.handle, right.handle)),
  ).unique();
}

async function friendshipRowsFor(ctx: any, profile: any): Promise<any[]> {
  const [aId, bId, aHandle, bHandle] = await Promise.all([
    ctx.db.query("friendships").withIndex("by_member_a_profile", (q: any) => q.eq("memberAProfileId", profile._id)).collect(),
    ctx.db.query("friendships").withIndex("by_member_b_profile", (q: any) => q.eq("memberBProfileId", profile._id)).collect(),
    ctx.db.query("friendships").withIndex("by_member_a", (q: any) => q.eq("memberA", profile.handle)).collect(),
    ctx.db.query("friendships").withIndex("by_member_b", (q: any) => q.eq("memberB", profile.handle)).collect(),
  ]);
  return mergeRows(aId, bId, aHandle, bHandle);
}

async function friendCount(ctx: any, profile: any): Promise<number> {
  return (await friendshipRowsFor(ctx, profile)).length;
}

function friendshipValue(left: any, right: any, now: number): any {
  const first = String(left._id) < String(right._id) ? left : right;
  const second = first === left ? right : left;
  return {
    pairKey: pairKey(left.handle, right.handle),
    memberA: first.handle,
    memberB: second.handle,
    memberAProfileId: first._id,
    memberBProfileId: second._id,
    pairKeyProfileId: profilePairKey(left._id, right._id),
    createdAt: now,
    updatedAt: now,
  };
}

export const get = internalQuery({
  args: { handle: v.string() },
  handler: async (ctx, args) => await getProfile(ctx, args.handle),
});

export const handleAvailable = internalQuery({
  args: { handle: v.string() },
  handler: async (ctx, args) => ({
    handle: args.handle,
    available: !(await findHandle(ctx, args.handle)) && !(await getProfile(ctx, args.handle)),
  }),
});

export const getByIdentity = internalQuery({
  args: { ownerIdentityHash: v.string() },
  handler: async (ctx, args) => await ctx.db.query("publicProfiles")
    .withIndex("by_owner_identity", (q: any) => q.eq("ownerIdentityHash", args.ownerIdentityHash)).unique(),
});

export const getSocial = internalQuery({
  args: { handle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.optional(v.string()) },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash, args.ownerIdentityHash);
    if (owner.error) return owner;
    const [friendships, incoming, outgoing, blocked] = await Promise.all([
      friendshipRowsFor(ctx, owner),
      incomingRequestsFor(ctx, owner),
      pendingRequestsFor(ctx, owner),
      (async () => {
        const [byId, byHandle] = await Promise.all([
          ctx.db.query("profileBlocks").withIndex("by_blocker_profile", (q: any) => q.eq("blockerProfileId", owner._id)).collect(),
          ctx.db.query("profileBlocks").withIndex("by_blocker", (q: any) => q.eq("blockerHandle", owner.handle)).collect(),
        ]);
        return mergeRows(byId, byHandle);
      })(),
    ]);
    const friendIds = new Set<string>();
    const friendHandles = new Set<string>();
    for (const friendship of friendships) {
      if (friendship.memberAProfileId === owner._id) friendIds.add(String(friendship.memberBProfileId));
      else if (friendship.memberBProfileId === owner._id) friendIds.add(String(friendship.memberAProfileId));
      else if (friendship.memberA === owner.handle) friendHandles.add(friendship.memberB);
      else if (friendship.memberB === owner.handle) friendHandles.add(friendship.memberA);
    }
    const summaryFor = async (profileId: any, handle: string) => {
      const profile = await getProfileById(ctx, profileId) || await getProfile(ctx, handle);
      return profile ? profileSummary(profile) : null;
    };
    const friends = (await Promise.all([
      ...[...friendIds].map((id) => summaryFor(id, "")),
      ...[...friendHandles].map((handle) => summaryFor(null, handle)),
    ])).filter(Boolean);
    const incomingRequests = (await Promise.all(incoming.map(async (request: any) => {
      const summary = await summaryFor(request.requesterProfileId, request.requesterHandle);
      return summary ? { ...summary, request_id: String(request._id), created_at: request.createdAt } : null;
    }))).filter(Boolean);
    const outgoingRequests = (await Promise.all(outgoing.map(async (request: any) => {
      const summary = await summaryFor(request.recipientProfileId, request.recipientHandle);
      return summary ? { ...summary, request_id: String(request._id), created_at: request.createdAt } : null;
    }))).filter(Boolean);
    const blockedHandles = (await Promise.all(blocked.map(async (item: any) => {
      const profile = await getProfileById(ctx, item.blockedProfileId) || await getProfile(ctx, item.blockedHandle);
      return profile?.handle || item.blockedHandle;
    }))).filter(Boolean);
    return { friends, incoming_requests: incomingRequests, outgoing_requests: outgoingRequests, blocked_handles: blockedHandles };
  },
});

export const create = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string(), profile: v.string() },
  handler: async (ctx, args) => {
    if (await getProfile(ctx, args.handle)) return { error: "exists" };
    const now = Date.now();
    const id = await ctx.db.insert("publicProfiles", {
      handle: args.handle,
      ownerTokenHash: args.ownerTokenHash,
      profile: args.profile,
      revision: 1,
      createdAt: now,
      updatedAt: now,
    });
    const alias = await ensureAlias(ctx, args.handle, id, true);
    if (!alias.ok) return { error: "exists" };
    return { revision: 1, updatedAt: now, handle: args.handle };
  },
});

export const createForIdentity = internalMutation({
  args: { handle: v.optional(v.string()), ownerIdentityHash: v.string(), profile: v.string() },
  handler: async (ctx, args) => {
    const existingOwner = await ctx.db.query("publicProfiles")
      .withIndex("by_owner_identity", (q: any) => q.eq("ownerIdentityHash", args.ownerIdentityHash)).unique();
    if (existingOwner) return { error: "profile_exists_for_identity", profile: existingOwner };
    let value: Record<string, unknown>;
    try { value = JSON.parse(args.profile) as Record<string, unknown>; } catch { return { error: "invalid_profile" }; }
    const requested = String(args.handle || "").trim().toLowerCase();
    if (validUsernameHandle(requested) && await getProfile(ctx, requested)) return { error: "exists" };
    const handle = validUsernameHandle(requested)
      ? requested
      : await readableHandleFor(ctx, displayNameFromProfile(args.profile), requested || args.ownerIdentityHash);
    value.handle = handle;
    const serialized = JSON.stringify(value);
    const now = Date.now();
    const id = await ctx.db.insert("publicProfiles", {
      handle,
      ownerIdentityHash: args.ownerIdentityHash,
      profile: serialized,
      revision: 1,
      createdAt: now,
      updatedAt: now,
    });
    await ensureAlias(ctx, handle, id, true);
    return { revision: 1, updatedAt: now, handle };
  },
});

export const claimLegacy = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.string() },
  handler: async (ctx, args) => {
    const existing = await getProfile(ctx, args.handle);
    if (!existing) return { error: "not_found" };
    if (typeof existing.ownerIdentityHash === "string") {
      return constantTimeEqual(existing.ownerIdentityHash, args.ownerIdentityHash)
        ? { claimed: true, revision: existing.revision, handle: existing.handle }
        : { error: "claimed" };
    }
    if (typeof existing.ownerTokenHash !== "string" || !constantTimeEqual(existing.ownerTokenHash, args.ownerTokenHash))
      return { error: "unauthorized" };
    const identityOwner = await ctx.db.query("publicProfiles")
      .withIndex("by_owner_identity", (q: any) => q.eq("ownerIdentityHash", args.ownerIdentityHash)).unique();
    if (identityOwner && identityOwner._id !== existing._id) return { error: "identity_has_profile" };
    await ctx.db.patch(existing._id, { ownerIdentityHash: args.ownerIdentityHash, ownerTokenHash: undefined, updatedAt: Date.now() });
    return { claimed: true, revision: existing.revision, handle: existing.handle };
  },
});

export const createFriendRequest = internalMutation({
  args: { requesterHandle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.optional(v.string()), recipientHandle: v.string() },
  handler: async (ctx, args) => {
    const requester = await authorize(ctx, args.requesterHandle, args.ownerTokenHash, args.ownerIdentityHash);
    if (requester.error) return requester;
    const recipient = await getProfile(ctx, args.recipientHandle);
    if (!recipient) return { error: "target_not_found" };
    if (requester._id === recipient._id) return { error: "self_request" };
    if (await isBlocked(ctx, requester, recipient)) return { error: "blocked" };
    if (await findFriendship(ctx, requester, recipient)) return { status: ACCEPTED };
    const existing = await getPendingRequest(ctx, requester, recipient);
    if (existing) return { status: PENDING, requestId: String(existing._id) };
    const reverse = await getPendingRequest(ctx, recipient, requester);
    const now = Date.now();
    if (reverse) {
      const [requesterFriendCount, recipientFriendCount] = await Promise.all([friendCount(ctx, requester), friendCount(ctx, recipient)]);
      if (requesterFriendCount >= MAX_FRIENDS || recipientFriendCount >= MAX_FRIENDS) return { error: "friend_limit" };
      await ctx.db.patch(reverse._id, { status: ACCEPTED, updatedAt: now });
      await ctx.db.insert("friendships", friendshipValue(requester, recipient, now));
      return { status: ACCEPTED };
    }
    if ((await pendingRequestsFor(ctx, requester)).length >= MAX_PENDING_REQUESTS) return { error: "request_limit" };
    const requestId = await ctx.db.insert("friendRequests", {
      requesterHandle: requester.handle,
      recipientHandle: recipient.handle,
      requesterProfileId: requester._id,
      recipientProfileId: recipient._id,
      status: PENDING,
      createdAt: now,
      updatedAt: now,
    });
    return { status: PENDING, requestId: String(requestId) };
  },
});

export const respondFriendRequest = internalMutation({
  args: { requestId: v.id("friendRequests"), handle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.optional(v.string()), action: v.string() },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash, args.ownerIdentityHash);
    if (owner.error) return owner;
    if (!["accept", "decline", "cancel"].includes(args.action)) return { error: "invalid_action" };
    const request = await ctx.db.get(args.requestId);
    if (!request || request.status !== PENDING) return { error: "request_not_found" };
    const recipientAction = args.action === "accept" || args.action === "decline";
    const ownsRequest = recipientAction
      ? request.recipientProfileId === owner._id || (!request.recipientProfileId && request.recipientHandle === owner.handle)
      : request.requesterProfileId === owner._id || (!request.requesterProfileId && request.requesterHandle === owner.handle);
    if (!ownsRequest) return { error: "unauthorized" };
    if (args.action === "accept") {
      const requester = await getProfileById(ctx, request.requesterProfileId) || await getProfile(ctx, request.requesterHandle);
      const recipient = await getProfileById(ctx, request.recipientProfileId) || await getProfile(ctx, request.recipientHandle);
      if (!requester || !recipient) return { error: "request_not_found" };
      if (await isBlocked(ctx, requester, recipient)) return { error: "blocked" };
      const existing = await findFriendship(ctx, requester, recipient);
      if (!existing) {
        const [recipientFriendCount, requesterFriendCount] = await Promise.all([friendCount(ctx, recipient), friendCount(ctx, requester)]);
        if (recipientFriendCount >= MAX_FRIENDS || requesterFriendCount >= MAX_FRIENDS) return { error: "friend_limit" };
      }
      const now = Date.now();
      await ctx.db.patch(request._id, {
        status: ACCEPTED,
        requesterHandle: requester.handle,
        recipientHandle: recipient.handle,
        requesterProfileId: requester._id,
        recipientProfileId: recipient._id,
        updatedAt: now,
      });
      if (!existing) await ctx.db.insert("friendships", friendshipValue(requester, recipient, now));
      return { status: ACCEPTED };
    }
    const status = args.action === "decline" ? DECLINED : CANCELED;
    await ctx.db.patch(request._id, { status, updatedAt: Date.now() });
    return { status };
  },
});

export const removeFriend = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.optional(v.string()), friendHandle: v.string() },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash, args.ownerIdentityHash);
    if (owner.error) return owner;
    const friend = await getProfile(ctx, args.friendHandle);
    if (!friend) return { removed: false };
    const friendship = await findFriendship(ctx, owner, friend);
    if (!friendship) return { removed: false };
    await ctx.db.delete(friendship._id);
    return { removed: true };
  },
});

export const blockUser = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.optional(v.string()), blockedHandle: v.string() },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash, args.ownerIdentityHash);
    if (owner.error) return owner;
    const blocked = await getProfile(ctx, args.blockedHandle);
    if (!blocked) return { error: "target_not_found" };
    if (owner._id === blocked._id) return { error: "self_block" };
    const [blocksById, blocksByHandle, existing] = await Promise.all([
      ctx.db.query("profileBlocks").withIndex("by_blocker_profile", (q: any) => q.eq("blockerProfileId", owner._id)).collect(),
      ctx.db.query("profileBlocks").withIndex("by_blocker", (q: any) => q.eq("blockerHandle", owner.handle)).collect(),
      directBlock(ctx, owner, blocked),
    ]);
    if (!existing && mergeRows(blocksById, blocksByHandle).length >= MAX_BLOCKS) return { error: "block_limit" };
    if (!existing) await ctx.db.insert("profileBlocks", {
      blockerHandle: owner.handle,
      blockedHandle: blocked.handle,
      blockerProfileId: owner._id,
      blockedProfileId: blocked._id,
      createdAt: Date.now(),
    });
    const friendship = await findFriendship(ctx, owner, blocked);
    if (friendship) await ctx.db.delete(friendship._id);
    const requests = mergeRows(await pendingRequestsFor(ctx, owner), await incomingRequestsFor(ctx, owner));
    for (const request of requests) {
      const otherId = request.requesterProfileId === owner._id ? request.recipientProfileId : request.recipientProfileId === owner._id ? request.requesterProfileId : null;
      const otherHandle = request.requesterHandle === owner.handle ? request.recipientHandle : request.recipientHandle === owner.handle ? request.requesterHandle : "";
      if (otherId === blocked._id || otherHandle === blocked.handle) await ctx.db.patch(request._id, { status: BLOCKED, updatedAt: Date.now() });
    }
    return { blocked: true };
  },
});

export const unblockUser = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.optional(v.string()), blockedHandle: v.string() },
  handler: async (ctx, args) => {
    const owner = await authorize(ctx, args.handle, args.ownerTokenHash, args.ownerIdentityHash);
    if (owner.error) return owner;
    const blocked = await getProfile(ctx, args.blockedHandle);
    if (!blocked) return { unblocked: true };
    const existing = await directBlock(ctx, owner, blocked);
    if (existing) await ctx.db.delete(existing._id);
    return { unblocked: true };
  },
});

export const update = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.optional(v.string()), profile: v.string(), revision: v.number() },
  handler: async (ctx, args) => {
    const existing = await getProfile(ctx, args.handle);
    if (!existing) return { error: "not_found" };
    const stored = args.ownerIdentityHash ? existing.ownerIdentityHash : existing.ownerTokenHash;
    const supplied = args.ownerIdentityHash || args.ownerTokenHash;
    if (typeof stored !== "string" || !constantTimeEqual(stored, supplied)) return { error: "unauthorized" };
    if (existing.revision !== args.revision) return { error: "conflict", revision: existing.revision };
    let value: Record<string, unknown>;
    try { value = JSON.parse(args.profile) as Record<string, unknown>; } catch { return { error: "invalid_profile" }; }
    const requested = typeof value.handle === "string" ? value.handle.trim().toLowerCase() : existing.handle;
    const requestedAlias = validHandle(requested) ? await findHandle(ctx, requested) : null;
    let canonicalHandle = existing.handle;
    // A stale client may still submit a historical alias. It should resolve
    // to the current canonical name, not roll a rename back.
    if (validUsernameHandle(requested) && (!requestedAlias || requestedAlias.profileId !== existing._id)) {
      const result = await setCanonicalHandle(ctx, existing, requested);
      if ("error" in result) return result;
      canonicalHandle = result.handle;
    } else if (!requestedAlias && requested === existing.handle) {
      const result = await setCanonicalHandle(ctx, existing, existing.handle);
      if ("error" in result) return result;
      canonicalHandle = result.handle;
    }
    value.handle = canonicalHandle;
    const revision = existing.revision + 1;
    const updatedAt = Date.now();
    await ctx.db.patch(existing._id, { handle: canonicalHandle, profile: JSON.stringify(value), revision, updatedAt });
    return { revision, updatedAt, handle: canonicalHandle };
  },
});

export const remove = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.optional(v.string()) },
  handler: async (ctx, args) => {
    const existing = await authorize(ctx, args.handle, args.ownerTokenHash, args.ownerIdentityHash);
    if (existing.error) return existing;
    const [outgoing, incoming, relationships, blocked, blockedBy, aliases] = await Promise.all([
      ctx.db.query("friendRequests").withIndex("by_requester_profile_status", (q: any) => q.eq("requesterProfileId", existing._id)).collect(),
      ctx.db.query("friendRequests").withIndex("by_recipient_profile_status", (q: any) => q.eq("recipientProfileId", existing._id)).collect(),
      friendshipRowsFor(ctx, existing),
      ctx.db.query("profileBlocks").withIndex("by_blocker_profile", (q: any) => q.eq("blockerProfileId", existing._id)).collect(),
      ctx.db.query("profileBlocks").withIndex("by_blocked_profile", (q: any) => q.eq("blockedProfileId", existing._id)).collect(),
      ctx.db.query("profileHandles").withIndex("by_profile", (q: any) => q.eq("profileId", existing._id)).collect(),
    ]);
    const rows = new Map<string, any>();
    for (const row of [...outgoing, ...incoming, ...relationships, ...blocked, ...blockedBy]) rows.set(String(row._id), row);
    // Include old rows that predate the ID backfill.
    for (const [table, index, field] of [
      ["friendRequests", "by_requester_status", "requesterHandle"],
      ["friendRequests", "by_recipient_status", "recipientHandle"],
      ["profileBlocks", "by_blocker", "blockerHandle"],
      ["profileBlocks", "by_blocked", "blockedHandle"],
    ] as const) {
      const oldRows = await ctx.db.query(table as any).withIndex(index, (q: any) => q.eq(field, existing.handle)).collect();
      for (const row of oldRows) rows.set(String(row._id), row);
    }
    for (const row of rows.values()) await ctx.db.delete(row._id);
    for (const alias of aliases) await ctx.db.delete(alias._id);
    await ctx.db.delete(existing._id);
    return { deleted: true };
  },
});

export const rotateToken = internalMutation({
  args: { handle: v.string(), ownerTokenHash: v.string(), ownerIdentityHash: v.optional(v.string()), newOwnerTokenHash: v.string() },
  handler: async (ctx, args) => {
    const existing = await getProfile(ctx, args.handle);
    if (!existing) return { error: "not_found" };
    if (typeof existing.ownerTokenHash !== "string" || !constantTimeEqual(existing.ownerTokenHash, args.ownerTokenHash)) return { error: "unauthorized" };
    await ctx.db.patch(existing._id, { ownerTokenHash: args.newOwnerTokenHash, updatedAt: Date.now() });
    return { rotated: true };
  },
});

async function migrationProfile(ctx: any, profile: any): Promise<{ changed: boolean; collision: boolean }> {
  const oldHandle = profile.handle;
  const aliases = await ctx.db.query("profileHandles").withIndex("by_profile", (q: any) => q.eq("profileId", profile._id)).collect();
  const canonicalAlias = aliases.find((alias: any) => alias.canonical);
  let handle = validUsernameHandle(oldHandle)
    ? oldHandle
    : canonicalAlias?.handle || await readableHandleFor(ctx, displayNameFromProfile(profile.profile), oldHandle || String(profile._id));
  const existingProfile = await getProfile(ctx, handle);
  if (existingProfile && existingProfile._id !== profile._id) {
    handle = await readableHandleFor(ctx, displayNameFromProfile(profile.profile), oldHandle || String(profile._id));
  }
  const alias = await ensureAlias(ctx, oldHandle, profile._id, false);
  if (!alias.ok) return { changed: false, collision: true };
  await ensureAlias(ctx, handle, profile._id, true);
  let serialized = profile.profile;
  try {
    const value = JSON.parse(profile.profile) as Record<string, unknown>;
    value.handle = handle;
    serialized = JSON.stringify(value);
  } catch { /* The regular profile API will report malformed stored data. */ }
  await syncRelationshipHandles(ctx, profile, oldHandle, handle);
  const changed = profile.handle !== handle || profile.profile !== serialized;
  if (changed) await ctx.db.patch(profile._id, { handle, profile: serialized, updatedAt: Date.now() });
  return { changed, collision: false };
}

export const migrateProfileBatch = internalMutation({
  args: { cursor: v.optional(v.string()), limit: v.number() },
  handler: async (ctx, args) => {
    const page = await ctx.db.query("publicProfiles").order("asc").paginate({ cursor: args.cursor ?? null, numItems: Math.max(1, Math.min(100, args.limit)) });
    let changed = 0;
    let collisions = 0;
    for (const profile of page.page) {
      const result = await migrationProfile(ctx, profile);
      if (result.changed) changed++;
      if (result.collision) collisions++;
    }
    return { processed: page.page.length, changed, collisions, nextCursor: page.isDone ? undefined : page.continueCursor };
  },
});

async function relationProfiles(ctx: any, leftHandle: string, rightHandle: string, leftId: any, rightId: any): Promise<[any | null, any | null]> {
  return [await getProfileById(ctx, leftId) || await getProfile(ctx, leftHandle), await getProfileById(ctx, rightId) || await getProfile(ctx, rightHandle)];
}

export const migrateFriendRequestBatch = internalMutation({
  args: { cursor: v.optional(v.string()), limit: v.number() },
  handler: async (ctx, args) => {
    const page = await ctx.db.query("friendRequests").order("asc").paginate({ cursor: args.cursor ?? null, numItems: Math.max(1, Math.min(100, args.limit)) });
    let migrated = 0;
    let unresolved = 0;
    for (const row of page.page) {
      const [requester, recipient] = await relationProfiles(ctx, row.requesterHandle, row.recipientHandle, row.requesterProfileId, row.recipientProfileId);
      if (!requester || !recipient) { unresolved++; continue; }
      await ctx.db.patch(row._id, { requesterHandle: requester.handle, recipientHandle: recipient.handle, requesterProfileId: requester._id, recipientProfileId: recipient._id, updatedAt: row.updatedAt });
      migrated++;
    }
    return { processed: page.page.length, migrated, unresolved, nextCursor: page.isDone ? undefined : page.continueCursor };
  },
});

export const migrateFriendshipBatch = internalMutation({
  args: { cursor: v.optional(v.string()), limit: v.number() },
  handler: async (ctx, args) => {
    const page = await ctx.db.query("friendships").order("asc").paginate({ cursor: args.cursor ?? null, numItems: Math.max(1, Math.min(100, args.limit)) });
    let migrated = 0;
    let unresolved = 0;
    for (const row of page.page) {
      const [left, right] = await relationProfiles(ctx, row.memberA, row.memberB, row.memberAProfileId, row.memberBProfileId);
      if (!left || !right) { unresolved++; continue; }
      const first = String(left._id) < String(right._id) ? left : right;
      const second = first === left ? right : left;
      await ctx.db.patch(row._id, { pairKey: pairKey(left.handle, right.handle), memberA: first.handle, memberB: second.handle, memberAProfileId: first._id, memberBProfileId: second._id, pairKeyProfileId: profilePairKey(left._id, right._id), updatedAt: row.updatedAt });
      migrated++;
    }
    return { processed: page.page.length, migrated, unresolved, nextCursor: page.isDone ? undefined : page.continueCursor };
  },
});

export const migrateBlockBatch = internalMutation({
  args: { cursor: v.optional(v.string()), limit: v.number() },
  handler: async (ctx, args) => {
    const page = await ctx.db.query("profileBlocks").order("asc").paginate({ cursor: args.cursor ?? null, numItems: Math.max(1, Math.min(100, args.limit)) });
    let migrated = 0;
    let unresolved = 0;
    for (const row of page.page) {
      const [blocker, blocked] = await relationProfiles(ctx, row.blockerHandle, row.blockedHandle, row.blockerProfileId, row.blockedProfileId);
      if (!blocker || !blocked) { unresolved++; continue; }
      await ctx.db.patch(row._id, { blockerHandle: blocker.handle, blockedHandle: blocked.handle, blockerProfileId: blocker._id, blockedProfileId: blocked._id });
      migrated++;
    }
    return { processed: page.page.length, migrated, unresolved, nextCursor: page.isDone ? undefined : page.continueCursor };
  },
});

export const identifierMigrationPreview = internalQuery({
  args: {},
  handler: async (ctx) => {
    const [profiles, aliases, requests, friendships, blocks] = await Promise.all([
      ctx.db.query("publicProfiles").collect(),
      ctx.db.query("profileHandles").collect(),
      ctx.db.query("friendRequests").collect(),
      ctx.db.query("friendships").collect(),
      ctx.db.query("profileBlocks").collect(),
    ]);
    return {
      profiles: profiles.length,
      legacyProfiles: profiles.filter((profile: any) => !validUsernameHandle(profile.handle)).length,
      aliases: aliases.length,
      profilesMissingAliases: profiles.filter((profile: any) => !aliases.some((alias: any) => alias.profileId === profile._id)).length,
      friendRequestsMissingIds: requests.filter((row: any) => !row.requesterProfileId || !row.recipientProfileId).length,
      friendshipsMissingIds: friendships.filter((row: any) => !row.memberAProfileId || !row.memberBProfileId).length,
      blocksMissingIds: blocks.filter((row: any) => !row.blockerProfileId || !row.blockedProfileId).length,
    };
  },
});
