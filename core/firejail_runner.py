import os
import shlex
import subprocess
from core.interfaces import ISandboxRunner

class FirejailSandboxRunner(ISandboxRunner):
    def launch(self, game_path: str, executable: str, mode: str) -> None:
        if not game_path or not os.path.exists(game_path):
            raise ValueError(f"Game path does not exist: {game_path}")
        
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
                f"cd {q_path} && firejail --noprofile --ignore=noroot --ignore=seccomp --net=none "
                f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                f"--env=WINEPREFIX={prefix_path} umu-run {q_exe}"
            )
        elif mode == "umu_net":
            cmd = (
                f"cd {q_path} && firejail --noprofile --ignore=noroot --ignore=seccomp "
                f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                f"--env=WINEPREFIX={prefix_path} umu-run {q_exe}"
            )
        elif mode == "linux":
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
            cmd = (
                f"cd {q_path} && firejail --net=none "
                f"--whitelist={q_path} "
                f"--env=WINEPREFIX={prefix_path} wine {q_exe}"
            )

        subprocess.Popen(cmd, shell=True)