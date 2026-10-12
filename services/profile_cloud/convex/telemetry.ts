import { internal } from "./_generated/api";
import { internalMutation } from "./_generated/server";
import { v } from "convex/values";

/** Store only an aggregate heartbeat keyed by a server-derived client hash. */
export const record = internalMutation({
  args: {
    clientHash: v.string(),
    appVersion: v.string(),
    platform: v.string(),
  },
  handler: async (ctx, args) => {
    const now = Date.now();
    const existing = await ctx.db
      .query("telemetryClients")
      .withIndex("by_client_hash", (q) => q.eq("clientHash", args.clientHash))
      .unique();
    if (existing) {
      await ctx.db.patch(existing._id, {
        appVersion: args.appVersion,
        platform: args.platform,
        lastSeenAt: now,
        pingCount: existing.pingCount + 1,
      });
      return { recorded: true };
    }
    await ctx.db.insert("telemetryClients", {
      clientHash: args.clientHash,
      appVersion: args.appVersion,
      platform: args.platform,
      firstSeenAt: now,
      lastSeenAt: now,
      pingCount: 1,
    });
    return { recorded: true };
  },
});

/** Drop installation heartbeats and expired rate-limit buckets so storage stays bounded. */
export const pruneStale = internalMutation({
  args: {},
  handler: async (ctx) => {
    const now = Date.now();
    const staleClients = await ctx.db
      .query("telemetryClients")
      .withIndex("by_last_seen", (q) => q.lt("lastSeenAt", now - 180 * 24 * 60 * 60 * 1000))
      .take(100);
    for (const row of staleClients) await ctx.db.delete(row._id);
    const expiredBuckets = await ctx.db
      .query("profileRateLimits")
      .withIndex("by_expires", (q) => q.lt("expiresAt", now))
      .take(100);
    for (const row of expiredBuckets) await ctx.db.delete(row._id);
    if (staleClients.length === 100 || expiredBuckets.length === 100) {
      await ctx.scheduler.runAfter(0, internal.telemetry.pruneStale, {});
    }
    return { deleted: staleClients.length + expiredBuckets.length };
  },
});
