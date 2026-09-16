import { action } from "./_generated/server";
import { internal } from "./_generated/api";
import { v } from "convex/values";
import { constantTimeEqual } from "./lib/api";

const DEFAULT_BATCH_SIZE = 50;

async function migrationKeyAllowed(key: string): Promise<boolean> {
  const configured = process.env.SAFELAUNCHER_IDENTIFIER_MIGRATION_KEY || "";
  return Boolean(configured && key && constantTimeEqual(configured, key));
}

/** Developer-only, idempotent migration runner. It never returns an internal ID. */
export const migrateIdentifiers = action({
  args: { migrationKey: v.string(), dryRun: v.boolean(), batchSize: v.optional(v.number()) },
  handler: async (ctx, args): Promise<Record<string, number>> => {
    if (!(await migrationKeyAllowed(args.migrationKey))) throw new Error("identifier_migration_unauthorized");
    if (args.dryRun) return await ctx.runQuery(internal.profiles.identifierMigrationPreview, {});
    const limit = Math.max(1, Math.min(100, args.batchSize || DEFAULT_BATCH_SIZE));
    const report: Record<string, number> = { profiles: 0, profileChanges: 0, collisions: 0, friendRequests: 0, friendships: 0, blocks: 0, unresolved: 0 };
    for (const [name, mutation] of [
      ["profiles", internal.profiles.migrateProfileBatch],
      ["friendRequests", internal.profiles.migrateFriendRequestBatch],
      ["friendships", internal.profiles.migrateFriendshipBatch],
      ["blocks", internal.profiles.migrateBlockBatch],
    ] as const) {
      let cursor: string | undefined;
      do {
        const result: any = cursor
          ? await ctx.runMutation(mutation, { cursor, limit })
          : await ctx.runMutation(mutation, { limit });
        if (name === "profiles") {
          report.profiles += result.processed;
          report.profileChanges += result.changed;
          report.collisions += result.collisions;
        } else {
          report[name] += result.migrated;
          report.unresolved += result.unresolved;
        }
        cursor = result.nextCursor;
      } while (cursor);
    }
    return report;
  },
});
