import subprocess
from core.interfaces import ISandboxRunner

class FirejailSandboxRunner(ISandboxRunner):
    def launch(self, game_path: str, executable: str, mode: str) -> None:
        if mode == "umu":
            cmd = (
                f"cd '{game_path}' && firejail --ignore=noroot --ignore=seccomp "
                f"--net=none --whitelist='{game_path}' "
                f"--whitelist='$HOME/.local/share/umu' --whitelist='$HOME/.cache/umu' "
                f"--env=WINEPREFIX='{game_path}/prefix' "
                f"--env=WINEDLLOVERRIDES='winegstreamer=' umu-run '{executable}'"
            )
        else:
            cmd = (
                f"cd '{game_path}' && firejail --net=none "
                f"--whitelist='{game_path}' "
                f"--env=WINEPREFIX='{game_path}/prefix' wine '{executable}'"
            )

        subprocess.Popen(cmd, shell=True)