import { cronJobs } from "convex/server";
import { internal } from "./_generated/api";

const crons = cronJobs();

crons.interval(
  "expire stale profile presence",
  { hours: 1 },
  internal.profiles.cleanupExpiredPresence,
  {},
);

crons.interval(
  "prune stale telemetry and rate-limit buckets",
  { hours: 24 },
  internal.telemetry.pruneStale,
  {},
);

export default crons;
