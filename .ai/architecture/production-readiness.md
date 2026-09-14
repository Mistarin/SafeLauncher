# Production-Readiness Model

SafeLauncher production readiness is evaluated across five independent dimensions:

1. **Correctness:** local/cloud merge, installation identity, save conflicts, and state transitions are deterministic.
2. **Lifecycle safety:** requests, workers, Qt objects, external processes, and shutdown have explicit owners.
3. **Consistency:** equivalent remote work uses the shared request/cache/state path rather than feature-local implementations.
4. **Resilience:** offline, stale, timeout, authentication, rate-limit, conflict, and backend failure states are explicit.
5. **Operability:** metrics, logs, diagnostics, smoke tests, and recovery procedures make failures diagnosable.

## Current assessment

The request manager satisfies much of the lifecycle and consistency foundation. Cloud saves remain the highest-risk domain because they combine user data, cross-device behavior, conflicts, encryption, filesystem operations, and remote state; their status, mutation, exit-sync, and launch-entry boundaries now have dedicated services. Remaining production work is compatibility-path retirement, real-library performance measurement, and release hardening.

## Priority order

1. Cloud status/application service — Phase 1 boundary and Phase 2 cache/polling ownership are complete.
2. Cloud mutation/conflict application service — Phase 3 operation boundary is complete; compatibility fallbacks remain.
3. Private library sync application service — Phase 4 metadata reconciliation boundary is complete.
4. Achievement, Steam, and artwork resource services — Phase 5 transport/resource boundaries are complete; compatibility workers remain.
5. UI resource subscriptions — Phase 6 request-scoped bindings and generation filtering are complete across manager-backed dialogs/pages.
6. Public profile transport/application separation.
7. MainWindow extraction and shared UI state subscriptions.
8. Compatibility worker consolidation — first lifecycle slice complete; worker and cache audit remains.
9. Cache-policy consolidation.
10. Reliability/performance/release gates — diagnostics, bounded-batch, context/filter stress, configurable gate evaluation, the first synthetic 600-game/100-request baseline, and the isolated `ci/release_readiness.py` gate are complete; real representative network timings remain.

## Release verification command

Run the canonical local gate from the repository root:

```bash
python ci/release_readiness.py
```

It runs with fresh XDG data/config/cache directories and offline test flags.
The performance portion is a deterministic local regression guard. A real
backend/artwork performance baseline still requires a controlled staging
environment and must not be collected into repository artifacts with private
account or game data.

## Do not optimize prematurely

Do not rewrite the database, sandbox runner, or all Qt dialogs before cloud status and operation ownership are clear. Those systems have different authority and lifecycle requirements. The first refactor should reduce distributed cloud state while preserving the existing `CloudSaveSyncEngine`, `CloudClient`, and local database behavior.
