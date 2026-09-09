import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  publicProfiles: defineTable({
    handle: v.string(),
    /** SHA-256 of the bearer owner token. The raw token is never persisted. */
    ownerTokenHash: v.string(),
    /** Sanitized JSON projection; private launcher metadata is never accepted. */
    profile: v.string(),
    revision: v.number(),
    createdAt: v.number(),
    updatedAt: v.number(),
  }).index("by_handle", ["handle"]),
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
});
