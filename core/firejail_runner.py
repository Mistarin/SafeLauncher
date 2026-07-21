import os
import shlex
import subprocess
from core.interfaces import ISandboxRunner

# Modes that need --noprofile because Proton/UMU uses bwrap (Bubblewrap) to create
# user namespaces internally. Firejail's Landlock rules in default.profile block bwrap
# namespace creation, so we must disable the profile for these modes only.
# We keep --ignore=noroot --ignore=seccomp to specifically permit bwrap namespaces.
# Wine and Linux native modes retain the full default.profile for maximum security.
_UMU_MODES = {"umu", "umu_net"}
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
            # --noprofile: required for Proton/UMU (bwrap needs user namespaces).
            # --ignore=noroot --ignore=seccomp: permit bwrap namespace creation.
            # --net=none: block all network access inside the sandbox.
            cmd = (
                f"cd {q_path} && firejail --noprofile --ignore=noroot --ignore=seccomp --net=none "
                f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                f"--env=WINEPREFIX={prefix_path} umu-run {q_exe}"
            )
        elif mode == "umu_net":
            # Same as umu but with network access enabled for online/multiplayer games.
            cmd = (
                f"cd {q_path} && firejail --noprofile --ignore=noroot --ignore=seccomp "
                f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                f"--env=WINEPREFIX={prefix_path} umu-run {q_exe}"
            )
        elif mode == "linux":
            # Native Linux binary / shell script - full default.profile, no network.
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
            # Legacy Wine - full default.profile, no network.
            cmd = (
                f"cd {q_path} && firejail --net=none "
                f"--whitelist={q_path} "
                f"--env=WINEPREFIX={prefix_path} wine {q_exe}"
            )

        subprocess.Popen(cmd, shell=True)