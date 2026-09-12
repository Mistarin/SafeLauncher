#!/usr/bin/env node

/* Developer-only catalog importer. This file is never called by SafeLauncher. */
import { createHash } from "node:crypto";
import { readFile, readdir, stat } from "node:fs/promises";
import { basename, extname, join } from "node:path";
import { ConvexHttpClient } from "convex/browser";
import { api } from "../convex/_generated/api.js";

const MAX_BYTES = 512 * 1024;
const MAX_PIXELS = 4_000_000;

function argument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : "";
}

function avatarId(filename) {
  return basename(filename, extname(filename))
    .normalize("NFKD")
    .replace(/[^\x00-\x7F]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function dimensions(data) {
  if (data.length < 24 || data.subarray(0, 8).toString("hex") !== "89504e470d0a1a0a")
    throw new Error("not a PNG");
  if (data.subarray(12, 16).toString("ascii") !== "IHDR") throw new Error("invalid PNG header");
  const width = data.readUInt32BE(16);
  const height = data.readUInt32BE(20);
  if (!width || !height || width * height > MAX_PIXELS) throw new Error("unsafe dimensions");
  return { width, height };
}

function sortNaturally(left, right) {
  return left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" });
}

async function importCatalog() {
  const sourceDir = argument("--source-dir");
  const dryRun = process.argv.includes("--dry-run");
  const convexUrl = process.env.SAFELAUNCHER_CONVEX_URL || "";
  const importKey = process.env.SAFELAUNCHER_AVATAR_IMPORT_KEY || "";
  if (!sourceDir || (!dryRun && (!convexUrl || !importKey))) {
    throw new Error("Usage: [SAFELAUNCHER_CONVEX_URL=... SAFELAUNCHER_AVATAR_IMPORT_KEY=...] node scripts/import_profile_avatars.mjs --source-dir /path [--dry-run]");
  }
  const entries = (await readdir(sourceDir))
    .filter((name) => extname(name).toLowerCase() === ".png")
    .sort(sortNaturally);
  if (!entries.length) throw new Error("No PNG avatars found.");
  const client = dryRun ? null : new ConvexHttpClient(convexUrl);
  const seenIds = new Set();
  for (const [order, name] of entries.entries()) {
    const path = join(sourceDir, name);
    const info = await stat(path);
    if (!info.isFile() || info.size <= 0 || info.size > MAX_BYTES) throw new Error(`${name}: invalid file size`);
    const data = await readFile(path);
    const { width, height } = dimensions(data);
    const id = avatarId(name);
    if (!id) throw new Error(`${name}: invalid avatar ID`);
    if (seenIds.has(id)) throw new Error(`${name}: duplicate avatar ID ${id}`);
    seenIds.add(id);
    const digest = createHash("sha256").update(data).digest("hex");
    const category = id.startsWith("r-") ? "R" : "Standard";
    if (dryRun) {
      console.log(`${name} -> ${id} (valid)`);
      continue;
    }
    const result = await client.action(api.avatar_catalog.importAsset, {
      importKey,
      avatarId: id,
      label: basename(name, extname(name)),
      category,
      order,
      dataBase64: data.toString("base64"),
      sha256: digest,
      width,
      height,
    });
    console.log(`${name} -> ${id} (${result.updated ? "updated" : "imported"})`);
  }
}

async function migrateAppearance() {
  const convexUrl = process.env.SAFELAUNCHER_CONVEX_URL || "";
  const importKey = process.env.SAFELAUNCHER_AVATAR_IMPORT_KEY || "";
  if (!convexUrl || !importKey) throw new Error("SAFELAUNCHER_CONVEX_URL and SAFELAUNCHER_AVATAR_IMPORT_KEY are required");
  const client = new ConvexHttpClient(convexUrl);
  console.log(await client.action(api.avatar_catalog.runAppearanceMigration, { importKey }));
}

if (process.argv.includes("--migrate-appearance") || process.argv.includes("--migrate-legacy")) migrateAppearance().catch((error) => { console.error(error.message); process.exitCode = 1; });
else importCatalog().catch((error) => { console.error(error.message); process.exitCode = 1; });
