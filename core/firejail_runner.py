import os
import shlex
import subprocess
from core.interfaces import ISandboxRunner

# Modes that need --ignore=restrict-namespaces because Proton/UMU uses Bubblewrap
# (bwrap) internally to create user namespaces for the Steam Runtime container.
# Firejail's default.profile includes `restrict-namespaces` which blocks this.
#
# Security trade-off (UMU/Proton modes only):
#   --ignore=noroot            → bwrap can map uid 0 inside its own user namespace
#   --ignore=seccomp           → bwrap's unshare/clone syscalls are not filtered
#   --ignore=restrict-namespaces → bwrap can create its user + mount namespace
#
# ALL OTHER default.profile rules remain fully active:
#   caps.drop all              ✓ All capabilities stripped
#   nonewprivs                 ✓ No new privilege escalation via execve/SUID
#   netfilter                  ✓ Replaced by --net=none
#   private-tmp, private-dev   ✓ Still active
#   landlock-common.inc        ✓ Landlock filesystem restrictions still active
#   disable-common.inc         ✓ Sensitive paths still blacklisted
#   --whitelist                ✓ Home dir locked to only whitelisted paths
#
# Wine and Linux native modes do NOT need these ignores and use the full profile.
_VALID_MODES = {"umu", "umu_net", "wine", "linux"}


class FirejailSandboxRunner(ISandboxRunner):
    def launch(self, game_path: str, executable: str, mode: str) -> None:
        if not game_path or not os.path.exists(game_path):
            raise ValueError(f"Game path does not exist: {game_path}")

        if mode not in _VALID_MODES:
            raise ValueError(f"Unknown launch mode: {mode!r}. Must be one of {sorted(_VALID_MODES)}")

        home_dir = os.path.expanduser('~')
        umu_share = os.path.join(home_dir, '.local', 'share', 'umu')
        umu_cache = os.path.join(home_dir, '.cache', 'umu')

        os.makedirs(umu_share, exist_ok=True)
        os.makedirs(umu_cache, exist_ok=True)

        q_path = shlex.quote(game_path)
        q_exe = shlex.quote(executable)
        q_umu_share = shlex.quote(umu_share)
        q_umu_cache = shlex.quote(umu_cache)
        prefix_path = shlex.quote(os.path.join(game_path, 'prefix'))

        if mode == "umu":
            cmd = (
                f"cd {q_path} && firejail "
                f"--ignore=noroot --ignore=seccomp --ignore=restrict-namespaces "
                f"--net=none "
                f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                f"--env=WINEPREFIX={prefix_path} umu-run {q_exe}"
            )
        elif mode == "umu_net":
            cmd = (
                f"cd {q_path} && firejail "
                f"--ignore=noroot --ignore=seccomp --ignore=restrict-namespaces "
                f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                f"--env=WINEPREFIX={prefix_path} umu-run {q_exe}"
            )
        elif mode == "linux":
            # Native Linux binary - full default.profile, no namespace ignores needed.
            full_exe_path = os.path.join(game_path, executable)
            if os.path.exists(full_exe_path):
                try:
                    os.chmod(full_exe_path, 0o755)
                except Exception:
                    pass
            cmd = (
                f"cd {q_path} && firejail --net=none "
                f"--whitelist={q_path} ./{q_exe}"
            )
        else:  # "wine"
            # Legacy Wine - full default.profile, no namespace ignores needed.
            cmd = (
                f"cd {q_path} && firejail --net=none "
                f"--whitelist={q_path} "
                f"--env=WINEPREFIX={prefix_path} wine {q_exe}"
            )

        # Use `exec` inside the shell so the shell process is *replaced* by firejail.
        # This means process.wait() tracks the Firejail PID directly — the moment
        # Firejail shuts down (which happens right after the game exits and it prints
        # "Parent is shutting down, bye...") our tracker unblocks immediately.
        # Without `exec`, bash wraps firejail and adds an extra layer of latency.
        return subprocess.Popen(["/bin/bash", "-c", f"exec {cmd}"], shell=False)