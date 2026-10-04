# Game Launch and Sandbox

Game launch begins with a selected local game record and launch configuration. The runtime selects the configured runner, prepares the prefix/environment, applies network and security policy, starts the host process through the sandbox layer, and tracks the session until exit.

The launch path is separate from remote resource loading. Request/resource work may provide metadata, artwork, build information, or cloud-save status, but it must not silently mutate launch security policy. Launch diagnostics and process ownership remain in runtime modules.

[`LaunchPolicy`](../../core/launch_policy.py) owns the pure entry decision for
archived, running, and configured-mode rows. [`PrelaunchController`](../../ui/prelaunch_controller.py)
owns the cloud-safe per-game preflight lock, progress and cooperative
cancellation, conflict/quota choices, and launch handoff. It holds the lock
until canceled restore work has completed rollback, and shutdown cancels
pending work without allowing a late launch. [`LaunchSessionCoordinator`](../../core/launch_session_coordinator.py)
starts the configured runner and creates the durable playtime session;
[`GameSessionController`](../../ui/game_session_controller.py) activates and
finalizes the tracker, owns per-game stopping state, and dispatches process
termination on a managed worker. `SessionFeatureController` owns failure-isolated
recorder/Discord hooks, playtime service calls, and exit-cloud result delivery.
`AchievementSyncController` owns local watcher and delayed-exit read lifetimes.
The window composes these controllers and renders their outcomes.

Sources: [`core/game_session.py`](../../core/game_session.py), [`core/firejail_runner.py`](../../core/firejail_runner.py), [`core/host_process.py`](../../core/host_process.py), [`core/network_policy.py`](../../core/network_policy.py), [`core/launch_diagnostics.py`](../../core/launch_diagnostics.py).
