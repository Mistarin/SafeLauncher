import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  publicProfiles: defineTable({
    handle: v.string(),
    /** SHA-256 of the legacy bearer token; removed after identity claim. */
    ownerTokenHash: v.optional(v.string()),
    /** SHA-256 of Auth0 issuer/subject; the raw identity is never persisted. */
    ownerIdentityHash: v.optional(v.string()),
    /** Sanitized JSON projection; private launcher metadata is never accepted. */
    profile: v.string(),
    revision: v.number(),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_handle", ["handle"])
    .index("by_owner_identity", ["ownerIdentityHash"]),
  profileAvatars: defineTable({
    avatarId: v.string(),
    label: v.string(),
    category: v.string(),
    order: v.number(),
    storageId: v.id("_storage"),
    sha256: v.string(),
    width: v.number(),
    height: v.number(),
    bytes: v.number(),
    enabled: v.boolean(),
    updatedAt: v.number(),
  }).index("by_avatar_id", ["avatarId"]),
  friendRequests: defineTable({
    requesterHandle: v.string(),
    recipientHandle: v.string(),
    status: v.string(),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_requester_status", ["requesterHandle", "status"])
    .index("by_recipient_status", ["recipientHandle", "status"]),
  friendships: defineTable({
    pairKey: v.string(),
    memberA: v.string(),
    memberB: v.string(),
    createdAt: v.number(),
    updatedAt: v.number(),
  })
    .index("by_pair", ["pairKey"])
    .index("by_member_a", ["memberA"])
    .index("by_member_b", ["memberB"]),
  profileBlocks: defineTable({
    blockerHandle: v.string(),
    blockedHandle: v.string(),
    createdAt: v.number(),
  })
    .index("by_blocker", ["blockerHandle"])
    .index("by_blocked", ["blockedHandle"])
    .index("by_pair", ["blockerHandle", "blockedHandle"]),
  profileRateLimits: defineTable({
    bucketKey: v.string(),
    windowStart: v.number(),
    count: v.number(),
    expiresAt: v.number(),
  }).index("by_bucket", ["bucketKey"]),
  /** Anonymous installation heartbeat; raw client identifiers are never stored. */
  telemetryClients: defineTable({
    clientHash: v.string(),
    appVersion: v.string(),
    platform: v.string(),
    firstSeenAt: v.number(),
    lastSeenAt: v.number(),
    pingCount: v.number(),
  }).index("by_client_hash", ["clientHash"]),
});
