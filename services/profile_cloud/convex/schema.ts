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
});
