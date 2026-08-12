import os
import shlex
import shutil
import subprocess
from core.interfaces import ISandboxRunner
from core.host_process import host_process_env

_VALID_MODES = {"umu", "umu_net", "wine", "linux"}


class FirejailSandboxRunner(ISandboxRunner):
    def __init__(self):
        self.proton_path = ""

    def set_proton_path(self, proton_path: str) -> None:
        """Set an optional local Proton/GE-Proton tool directory for UMU."""
        self.proton_path = os.path.realpath(os.path.expanduser(proton_path.strip())) if proton_path.strip() else ""

    @staticmethod
    def check_dependencies() -> dict:
        """Returns dict of system dependencies status."""
        return {
            "firejail": shutil.which("firejail") is not None,
            "umu-run": shutil.which("umu-run") is not None,
            "wine": shutil.which("wine") is not None,
        }

    @staticmethod
    def firejail_supported() -> bool:
        """Return True only when Firejail can actually create a new namespace."""
        firejail_bin = shutil.which("firejail")
        if not firejail_bin:
            return False

        try:
            result = subprocess.run(
                [firejail_bin, "--noprofile", "--net=none", "--quiet", "true"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False

        if result.returncode == 0:
            return True

        text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        blocked_markers = (
            "no permissions to create a new namespace",
            "user namespace",
            "new namespace",
            "operation not permitted",
        )
        return not any(marker in text for marker in blocked_markers)

    @staticmethod
    def bubblewrap_supported() -> bool:
        """Return True only when bwrap can create a sandbox namespace."""
        bwrap_bin = shutil.which("bwrap")
        if not bwrap_bin:
            return False

        try:
            result = subprocess.run(
                [bwrap_bin, "--unshare-user", "--bind", "/", "/", "/bin/true"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return False

        if result.returncode == 0:
            return True

        text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
        blocked_markers = (
            "no permissions to create a new namespace",
            "user namespace",
            "new namespace",
            "operation not permitted",
            "cannot create new namespace",
            "unshare",
        )
        return not any(marker in text for marker in blocked_markers)

    @staticmethod
    def user_namespace_blocked() -> bool:
        """Return True when the host cannot create unprivileged user namespaces.

        This is the decisive check for UMU. If the kernel or the execution sandbox
        forbids user namespaces, UMU/bwrap will fail regardless of the selected
        launch mode, so SafeLauncher must fall back to Wine.
        """
        proc_path = "/proc/sys/kernel/unprivileged_userns_clone"
        if os.path.exists(proc_path):
            try:
                with open(proc_path, "r", encoding="utf-8", errors="ignore") as fh:
                    value = fh.read().strip().lower()
                if value in {"0", "no", "false"}:
                    return True
            except OSError:
                pass

        return not (FirejailSandboxRunner.firejail_supported() and FirejailSandboxRunner.bubblewrap_supported())

    def build_launch_command(self, game_path: str, executable: str, mode: str,
                            include_net_none: bool = True,
                            include_wine_prefix: bool = True) -> str:
        """Build a safe Firejail/UMU/Wine command without disabling namespace protections.

        Certain kernels reject unprivileged user namespaces; relaxing Firejail with
        --ignore=noroot/--ignore=seccomp/--ignore=restrict-namespaces breaks the
        sandbox even before the game starts. Keep the default sandbox protections in
        place and only whitelist the directories that the runtime genuinely needs.
        """
        if not game_path or not os.path.exists(game_path):
            raise ValueError(f"Game path does not exist: {game_path}")

        if mode not in _VALID_MODES:
            raise ValueError(f"Unknown launch mode: {mode!r}. Must be one of {sorted(_VALID_MODES)}")

        deps = self.check_dependencies()
        has_firejail = deps["firejail"] and self.firejail_supported()
        namespace_blocked = self.user_namespace_blocked()
        umu_works = (deps["umu-run"] and not namespace_blocked) or False

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
        proton_env = f"--env=PROTONPATH={shlex.quote(self.proton_path)} " if self.proton_path else ""
        proton_whitelist = f"--whitelist={shlex.quote(self.proton_path)} " if self.proton_path else ""

        if mode in ("umu", "umu_net"):
            if not umu_works:
                runner_cmd = f"wine {q_exe}" if deps["wine"] else f"umu-run {q_exe}"
                if deps["wine"]:
                    if has_firejail:
                        net_flag = "--net=none --ignore=noroot --ignore=seccomp --ignore=restrict-namespaces" if include_net_none and mode == "umu" else ""
                        cmd = (
                            f"cd {q_path} && exec firejail "
                            f"{net_flag}"
                            f"--whitelist={q_path} "
                            f"--env=WINEPREFIX={prefix_path} {runner_cmd}"
                        )
                    else:
                        cmd = f"cd {q_path} && export WINEPREFIX={prefix_path} && {runner_cmd}"
                else:
                    cmd = f"cd {q_path} && export WINEPREFIX={prefix_path} && {runner_cmd}"
            else:
                runner_cmd = f"umu-run {q_exe}"
                if has_firejail:
                    net_flag = "--net=none --ignore=noroot --ignore=seccomp --ignore=restrict-namespaces" if include_net_none and mode == "umu" else ""
                    cmd = (
                        f"cd {q_path} && exec firejail "
                        f"{net_flag}"
                        f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                        f"{proton_whitelist}{proton_env}"
                        f"--env=WINEPREFIX={prefix_path} {runner_cmd}"
                    )
                else:
                    cmd = f"cd {q_path} && export WINEPREFIX={prefix_path} && {runner_cmd}"
        elif mode == "linux":
            full_exe_path = os.path.join(game_path, executable)
            if os.path.exists(full_exe_path):
                if not os.access(full_exe_path, os.X_OK):
                    try:
                        os.chmod(full_exe_path, os.stat(full_exe_path).st_mode | 0o111)
                    except Exception:
                        pass
            if has_firejail:
                cmd = f"cd {q_path} && exec firejail --whitelist={q_path} ./{q_exe}"
                if include_net_none:
                    cmd = f"cd {q_path} && exec firejail --net=none --ignore=noroot --ignore=seccomp --ignore=restrict-namespaces  --whitelist={q_path} ./{q_exe}"
            else:
                cmd = f"cd {q_path} && ./{q_exe}"
        else:  # "wine"
            runner_cmd = f"wine {q_exe}"
            if has_firejail:
                cmd = f"cd {q_path} && exec firejail --whitelist={q_path} --env=WINEPREFIX={prefix_path} {runner_cmd}"
                if include_net_none and include_wine_prefix:
                    cmd = f"cd {q_path} && exec firejail --net=none --ignore=noroot --ignore=seccomp --ignore=restrict-namespaces  --whitelist={q_path} --env=WINEPREFIX={prefix_path} {runner_cmd}"
            else:
                cmd = f"cd {q_path} && export WINEPREFIX={prefix_path} && {runner_cmd}"

        return cmd

    def launch(self, game_path: str, executable: str, mode: str) -> subprocess.Popen:
        cmd = self.build_launch_command(game_path, executable, mode)

        return subprocess.Popen(
            # Keep the wrapper independent from bash/readline libraries inherited
            # from Proton/Wine environments. POSIX sh is sufficient for the
            # commands above and avoids errors such as bash's
            # "undefined symbol: rl_print_keybinding".
            ["/bin/sh", "-c", cmd],
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=host_process_env(),
        )
