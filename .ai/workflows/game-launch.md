# Workflow: Game Launch

1. Resolve the selected local game record.
2. `PrelaunchController` owns the per-game preflight lock and runs save/cloud checks through the managed cloud operation service. Its progress dialog can cancel cloud work; cancellation aborts launch but keeps the lock until restore rollback/cleanup finishes. Shutdown cancels pending work and suppresses late launch continuations.
3. Prepare prefix, runtime, environment, and launch arguments.
4. Execute through the configured sandbox runner.
5. `LaunchSessionCoordinator` registers the process and durable playtime session; `GameSessionController` activates/checkpoints the tracker.
6. Monitor host process and diagnostics.
7. `GameSessionController` finalizes the session after tracker completion; `SessionFeatureController` applies isolated game-specific hooks and persists/syncs feature state. `AchievementSyncController` reads emulator state immediately and once after exit writes, even offline.
8. Queue private metadata synchronization if configured.
