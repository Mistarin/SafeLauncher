#!/usr/bin/env node

/* Developer-only identifier migration. This file is never called by SafeLauncher. */
import { ConvexHttpClient } from "convex/browser";
import { api } from "../convex/_generated/api.js";

const dryRun = process.argv.includes("--dry-run");
const convexUrl = process.env.SAFELAUNCHER_CONVEX_URL || "";
const migrationKey = process.env.SAFELAUNCHER_IDENTIFIER_MIGRATION_KEY || "";
const batchSize = Number(process.env.SAFELAUNCHER_IDENTIFIER_BATCH_SIZE || 50);

if (!convexUrl || !migrationKey) {
  console.error(
    "Usage: SAFELAUNCHER_CONVEX_URL=... SAFELAUNCHER_IDENTIFIER_MIGRATION_KEY=... " +
    "npm run migrate-identifiers [-- --dry-run]",
  );
  process.exitCode = 1;
} else {
  const client = new ConvexHttpClient(convexUrl);
  client.action(api.identifier_migration.migrateIdentifiers, {
    migrationKey,
    dryRun,
    batchSize: Number.isSafeInteger(batchSize) ? Math.max(1, Math.min(100, batchSize)) : 50,
  }).then((report) => {
    console.log(JSON.stringify(report, null, 2));
  }).catch((error) => {
    console.error(error.message || error);
    process.exitCode = 1;
  });
}
