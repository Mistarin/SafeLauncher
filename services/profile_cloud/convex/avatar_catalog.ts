import { action, internalMutation, internalQuery } from "./_generated/server";
import { internal } from "./_generated/api";
import { v } from "convex/values";
import { constantTimeEqual } from "./lib/api";

const MAX_ASSET_BYTES = 512 * 1024;
const MAX_ASSET_PIXELS = 4_000_000;
const AVATAR_ID_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

function validAvatarId(value: string): boolean {
  return AVATAR_ID_RE.test(value);
}

function validCatalogText(value: string, maxLength: number): boolean {
  const normalized = value.trim();
  return normalized.length > 0 && normalized.length <= maxLength;
}

function validCatalogNumber(value: number, minimum: number, maximum: number): boolean {
  return Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}

function hexDigest(digest: ArrayBuffer): string {
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

async function importKeyAllowed(key: string): Promise<boolean> {
  const configured = process.env.SAFELAUNCHER_AVATAR_IMPORT_KEY || "";
  return Boolean(configured && key && constantTimeEqual(configured, key));
}

export const list = internalQuery({
  args: {},
  handler: async (ctx) => {
    const rows = await ctx.db.query("profileAvatars").collect();
    return rows
      .filter((row) => row.enabled)
      .sort((left, right) => left.order - right.order || left.avatarId.localeCompare(right.avatarId))
      .map((row) => ({
        id: row.avatarId,
        label: row.label,
        category: row.category,
        order: row.order,
        sha256: row.sha256,
        width: row.width,
        height: row.height,
        bytes: row.bytes,
      }));
  },
});

export const get = internalQuery({
  args: { avatarId: v.string() },
  handler: async (ctx, args) => {
    if (!validAvatarId(args.avatarId)) return null;
    const row = await ctx.db
      .query("profileAvatars")
      .withIndex("by_avatar_id", (query) => query.eq("avatarId", args.avatarId))
      .unique();
    return row && row.enabled ? row : null;
  },
});

export const upsert = internalMutation({
  args: {
    avatarId: v.string(),
    label: v.string(),
    category: v.string(),
    order: v.number(),
    storageId: v.id("_storage"),
    sha256: v.string(),
    width: v.number(),
    height: v.number(),
    bytes: v.number(),
  },
  handler: async (ctx, args) => {
    if (
      !validAvatarId(args.avatarId) ||
      !validCatalogText(args.label, 80) ||
      !validCatalogText(args.category, 32) ||
      !validCatalogNumber(args.order, 0, 10_000) ||
      !/^[a-f0-9]{64}$/.test(args.sha256) ||
      !validCatalogNumber(args.width, 1, 2_048) ||
      !validCatalogNumber(args.height, 1, 2_048) ||
      args.width * args.height > MAX_ASSET_PIXELS ||
      !validCatalogNumber(args.bytes, 1, MAX_ASSET_BYTES)
    )
      return { error: "invalid_avatar" };
    const existing = await ctx.db
      .query("profileAvatars")
      .withIndex("by_avatar_id", (query) => query.eq("avatarId", args.avatarId))
      .unique();
    const value = {
      avatarId: args.avatarId,
      label: args.label,
      category: args.category,
      order: args.order,
      storageId: args.storageId,
      sha256: args.sha256,
      width: args.width,
      height: args.height,
      bytes: args.bytes,
      enabled: true,
      updatedAt: Date.now(),
    };
    if (existing) {
      await ctx.db.patch(existing._id, value);
      return { updated: true, id: String(existing._id) };
    }
    const id = await ctx.db.insert("profileAvatars", value);
    return { updated: false, id: String(id) };
  },
});

/** Developer-only import entrypoint. It is never used by the desktop app. */
export const importAsset = action({
  args: {
    importKey: v.string(),
    avatarId: v.string(),
    label: v.string(),
    category: v.string(),
    order: v.number(),
    dataBase64: v.string(),
    sha256: v.string(),
    width: v.number(),
    height: v.number(),
  },
  handler: async (ctx, args): Promise<{ updated: boolean; id: string }> => {
    if (!(await importKeyAllowed(args.importKey))) throw new Error("avatar_import_unauthorized");
    if (!validAvatarId(args.avatarId) || args.dataBase64.length > 700_000 ||
        !/^[A-Za-z0-9+/]+={0,2}$/.test(args.dataBase64) || args.dataBase64.length % 4 !== 0 ||
        !/^[a-f0-9]{64}$/.test(args.sha256))
      throw new Error("avatar_import_invalid");
    if (!validCatalogNumber(args.width, 1, 2_048) ||
        !validCatalogNumber(args.height, 1, 2_048) ||
        args.width * args.height > MAX_ASSET_PIXELS)
      throw new Error("avatar_import_invalid");
    let binary: string;
    try {
      binary = atob(args.dataBase64);
    } catch {
      throw new Error("avatar_import_invalid");
    }
    if (!binary || binary.length > MAX_ASSET_BYTES) throw new Error("avatar_import_invalid");
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    if (bytes.length < 24 ||
        bytes[0] !== 0x89 || bytes[1] !== 0x50 || bytes[2] !== 0x4e || bytes[3] !== 0x47 ||
        bytes[4] !== 0x0d || bytes[5] !== 0x0a || bytes[6] !== 0x1a || bytes[7] !== 0x0a ||
        String.fromCharCode(bytes[12], bytes[13], bytes[14], bytes[15]) !== "IHDR")
      throw new Error("avatar_import_invalid");
    const encodedWidth = (bytes[16] * 0x1000000) + (bytes[17] * 0x10000) + (bytes[18] * 0x100) + bytes[19];
    const encodedHeight = (bytes[20] * 0x1000000) + (bytes[21] * 0x10000) + (bytes[22] * 0x100) + bytes[23];
    if (encodedWidth !== args.width || encodedHeight !== args.height)
      throw new Error("avatar_import_dimensions_mismatch");
    const digest = hexDigest(await crypto.subtle.digest("SHA-256", bytes));
    if (digest !== args.sha256) throw new Error("avatar_import_hash_mismatch");
    const existing = await ctx.runQuery(internal.avatar_catalog.get, { avatarId: args.avatarId });
    if (existing && existing.sha256 === digest) {
      return { updated: true, id: String(existing._id) };
    }
    const storageId = await ctx.storage.store(new Blob([bytes], { type: "image/png" }));
    const result = await ctx.runMutation(internal.avatar_catalog.upsert, {
      avatarId: args.avatarId,
      label: args.label.slice(0, 80),
      category: args.category.slice(0, 32),
      order: Math.max(0, Math.min(10_000, Math.trunc(args.order))),
      storageId,
      sha256: digest,
      width: Math.min(2048, Math.trunc(args.width)),
      height: Math.min(2048, Math.trunc(args.height)),
      bytes: bytes.byteLength,
    });
    if (existing && existing.storageId !== storageId) {
      await ctx.storage.delete(existing.storageId);
    }
    return { updated: Boolean(result.updated), id: String(result.id) };
  },
});

export const migrateLegacyProfiles = internalMutation({
  args: {},
  handler: async (ctx) => {
    const profiles = await ctx.db.query("publicProfiles").collect();
    let migrated = 0;
    for (const row of profiles) {
      let profile: Record<string, unknown>;
      try {
        profile = JSON.parse(row.profile) as Record<string, unknown>;
      } catch {
        continue;
      }
      if (!profile || typeof profile !== "object" ||
          (profile.schema_version === 2 && !Object.prototype.hasOwnProperty.call(profile, "avatar")))
        continue;
      delete profile.avatar;
      profile.avatar_id = null;
      profile.schema_version = 2;
      await ctx.db.patch(row._id, { profile: JSON.stringify(profile), updatedAt: Date.now() });
      migrated += 1;
    }
    return { migrated };
  },
});

export const runLegacyMigration = action({
  args: { importKey: v.string() },
  handler: async (ctx, args): Promise<{ migrated: number }> => {
    if (!(await importKeyAllowed(args.importKey))) throw new Error("avatar_import_unauthorized");
    const result = await ctx.runMutation(internal.avatar_catalog.migrateLegacyProfiles, {});
    return { migrated: Number(result.migrated || 0) };
  },
});
