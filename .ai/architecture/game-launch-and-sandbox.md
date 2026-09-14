# Game Launch and Sandbox

Game launch begins with a selected local game record and launch configuration. The runtime selects the configured runner, prepares the prefix/environment, applies network and security policy, starts the host process through the sandbox layer, and tracks the session until exit.

The launch path is separate from remote resource loading. Request/resource work may provide metadata, artwork, build information, or cloud-save status, but it must not silently mutate launch security policy. Launch diagnostics and process ownership remain in runtime modules.

[`LaunchPolicy`](../../core/launch_policy.py) owns the pure entry decision for
archived, running, and configured-mode rows. [`LaunchSessionCoordinator`](../../core/launch_session_coordinator.py) owns the
process-to-session registration boundary. It starts the configured runner,
creates the durable playtime session, creates/attaches the tracker, suppresses
duplicate launches, and finalizes terminal sessions. MainWindow remains the
composition root for cloud preflight choices, Qt signal wiring, achievement
watchers, recorder/Discord UI, and post-exit cloud-sync notifications.

Sources: [`core/game_session.py`](../../core/game_session.py), [`core/firejail_runner.py`](../../core/firejail_runner.py), [`core/host_process.py`](../../core/host_process.py), [`core/network_policy.py`](../../core/network_policy.py), [`core/launch_diagnostics.py`](../../core/launch_diagnostics.py).
