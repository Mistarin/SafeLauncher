# Phase 10: Performance baseline

Measured 2026-09-14 with:

```text
python ci/performance_baseline.py --games 600 --requests 100 --repetitions 5 --workers 3
```

The run is deterministic and offline. It measures library projection and
request-manager overhead only; it is not a substitute for a real Steam,
profile-service, or SafeLauncherCloud network run.

| Metric | Result |
|---|---:|
| Synthetic library records | 600 |
| Library snapshot median | 1.138 ms |
| Library snapshot min/max | 0.641 / 2.248 ms |
| Managed batch median (100 requests) | 2.246 ms |
| Managed batch min/max | 2.113 / 3.165 ms |
| Request errors | 0 |
| Requests submitted | 100 |
| Request workers configured/peak | 3 / 1 |
| Duplicate requests | 0 |

## Interpretation

- Local projection and scheduler overhead are comfortably bounded for a
  600-game library.
- The synthetic loaders complete too quickly to exercise all configured
  workers; the peak of one is expected and is not a concurrency ceiling.
- No claim is made here about first visible artwork, cloud refresh latency,
  backend outage recovery, or real startup time. Those require an instrumented
  integration fixture or a controlled staging backend.

## Local release gate

The complete local verification sequence is now available through:

```text
python ci/release_readiness.py
```

It runs with fresh XDG directories and offline test flags, then performs
compilation, the full unit suite, all smoke phases, the historical harness,
security and worker audits, this deterministic performance guard, AI manifest
generation/cache validation, and `git diff --check`. The default synthetic
render guard is 250 ms for a 600-game projection; this is a regression limit,
not a claim about live network performance.

## Release-gate follow-up

Before release, capture the same metrics from an isolated representative
profile using the application diagnostics export, then compare:

- first usable library render;
- first visible artwork;
- cloud status batch duration;
- cache hit/stale/error ratios;
- peak active requests and shutdown duration.

Do not put account identifiers, URLs containing secrets, request payloads, save
contents, or game paths into the baseline artifact.
