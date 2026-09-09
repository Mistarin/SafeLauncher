import { internalMutation, internalQuery } from "./_generated/server";
import { v } from "convex/values";
import { constantTimeEqual } from "./lib/api";

export const get = internalQuery({
  args: { handle: v.string() },
  handler: async (ctx, args) => {
    return await ctx.db
      .query("publicProfiles")
      .withIndex("by_handle", (q) => q.eq("handle", args.handle))
      .unique();
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
