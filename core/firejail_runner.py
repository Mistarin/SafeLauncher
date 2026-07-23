import os
import shlex
import shutil
import subprocess
from core.interfaces import ISandboxRunner

_VALID_MODES = {"umu", "umu_net", "wine", "linux"}


class FirejailSandboxRunner(ISandboxRunner):
    @staticmethod
    def check_dependencies() -> dict:
        """Returns dict of system dependencies status."""
        return {
            "firejail": shutil.which("firejail") is not None,
            "umu-run": shutil.which("umu-run") is not None,
            "wine": shutil.which("wine") is not None,
        }

    def launch(self, game_path: str, executable: str, mode: str) -> subprocess.Popen:
        if not game_path or not os.path.exists(game_path):
            raise ValueError(f"Game path does not exist: {game_path}")

        if mode not in _VALID_MODES:
            raise ValueError(f"Unknown launch mode: {mode!r}. Must be one of {sorted(_VALID_MODES)}")

        deps = self.check_dependencies()
        has_firejail = deps["firejail"]

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

        if mode in ("umu", "umu_net"):
            runner_cmd = f"umu-run {q_exe}" if deps["umu-run"] else f"wine {q_exe}"
            if has_firejail:
                net_flag = "--net=none " if mode == "umu" else ""
                cmd = (
                    f"cd {q_path} && exec firejail "
                    f"--ignore=noroot --ignore=seccomp --ignore=restrict-namespaces "
                    f"{net_flag}"
                    f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                    f"--env=WINEPREFIX={prefix_path} {runner_cmd}"
                )
            else:
                # Direct unsandboxed execution fallback
                cmd = f"cd {q_path} && export WINEPREFIX={prefix_path} && {runner_cmd}"
        elif mode == "linux":
            full_exe_path = os.path.join(game_path, executable)
            if os.path.exists(full_exe_path):
                try:
                    os.chmod(full_exe_path, 0o755)
                except Exception:
                    pass
            if has_firejail:
                cmd = f"cd {q_path} && exec firejail --net=none --whitelist={q_path} ./{q_exe}"
            else:
                cmd = f"cd {q_path} && ./{q_exe}"
        else:  # "wine"
            runner_cmd = f"wine {q_exe}"
            if has_firejail:
                cmd = (
                    f"cd {q_path} && exec firejail --net=none "
                    f"--whitelist={q_path} "
                    f"--env=WINEPREFIX={prefix_path} {runner_cmd}"
                )
            else:
                cmd = f"cd {q_path} && export WINEPREFIX={prefix_path} && {runner_cmd}"

        return subprocess.Popen(
            ["/bin/bash", "-c", cmd],
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )