import { internalMutation } from "./_generated/server";
import { v } from "convex/values";

/**
 * Server-side mutation limits complement the gateway's edge limits. Convex
 * mutations are transactional, so concurrent requests cannot increment a
 * bucket past its limit without one request being retried/rejected.
 */
export const consume = internalMutation({
  args: {
    bucketKey: v.string(),
    limit: v.number(),
    windowSeconds: v.number(),
  },
  handler: async (ctx, args) => {
    const now = Date.now();
    const windowMs = Math.max(1, Math.floor(args.windowSeconds)) * 1000;
    const windowStart = Math.floor(now / windowMs) * windowMs;
    const expiresAt = windowStart + windowMs;
    const existing = await ctx.db
      .query("profileRateLimits")
      .withIndex("by_bucket", (q) => q.eq("bucketKey", args.bucketKey))
      .unique();
    if (!existing || existing.windowStart !== windowStart) {
      if (existing) {
        await ctx.db.patch(existing._id, { windowStart, count: 1, expiresAt });
      } else {
        await ctx.db.insert("profileRateLimits", {
          bucketKey: args.bucketKey,
          windowStart,
          count: 1,
          expiresAt,
        });
      }
      return { allowed: true, retryAfter: Math.ceil((expiresAt - now) / 1000) };
    }
    if (existing.count >= Math.max(1, Math.floor(args.limit))) {
      return { allowed: false, retryAfter: Math.ceil((expiresAt - now) / 1000) };
    }
    await ctx.db.patch(existing._id, { count: existing.count + 1 });
    return { allowed: true, retryAfter: Math.ceil((expiresAt - now) / 1000) };
  },
});
