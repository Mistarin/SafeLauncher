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
