# Game Launch and Sandbox

Game launch begins with a selected local game record and launch configuration. The runtime selects the configured runner, prepares the prefix/environment, applies network and security policy, starts the host process through the sandbox layer, and tracks the session until exit.

The launch path is separate from remote resource loading. Request/resource work may provide metadata, artwork, build information, or cloud-save status, but it must not silently mutate launch security policy. Launch diagnostics and process ownership remain in runtime modules.

Sources: [`core/game_session.py`](../../core/game_session.py), [`core/firejail_runner.py`](../../core/firejail_runner.py), [`core/host_process.py`](../../core/host_process.py), [`core/network_policy.py`](../../core/network_policy.py), [`core/launch_diagnostics.py`](../../core/launch_diagnostics.py).
