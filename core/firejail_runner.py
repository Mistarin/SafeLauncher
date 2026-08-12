import os
import shlex
import shutil
import subprocess
import tempfile
from core.interfaces import ISandboxRunner
from core.host_process import host_process_env
from core.prefix_sanitizer import sanitize_wine_prefix
from core.logger import get_logger

logger = get_logger("FirejailRunner")

_VALID_MODES = {"umu", "umu_net", "wine", "linux"}

# Explicit blacklists to prevent untrusted binaries from reading sensitive user files
_SECURITY_BLACKLISTS = [
    "~/.ssh",
    "~/.gnupg",
    "~/.bashrc",
    "~/.zshrc",
    "~/.mozilla",
    "~/.config/google-chrome",
    "~/.config/BraveSoftware",
    "~/Documents",
    "~/Desktop",
    "~/Downloads",
]


class FirejailSandboxRunner(ISandboxRunner):
    def __init__(self):
        self.proton_path = ""

    def set_proton_path(self, proton_path: str) -> None:
        """Set an optional local Proton/GE-Proton tool directory for UMU."""
        self.proton_path = os.path.realpath(os.path.expanduser(proton_path.strip())) if proton_path.strip() else ""
        logger.info(f"Proton path updated: {self.proton_path}")

    @staticmethod
    def check_dependencies() -> dict:
        """Returns dict of system dependencies status."""
        deps = {
            "firejail": shutil.which("firejail") is not None,
            "umu-run": shutil.which("umu-run") is not None,
            "wine": shutil.which("wine") is not None,
            "gamescope": shutil.which("gamescope") is not None,
        }
        logger.debug(f"Dependency check result: {deps}")
        return deps

    def launch(self, game_path: str, executable: str, mode: str) -> subprocess.Popen:
        if not game_path or not os.path.exists(game_path):
            logger.error(f"Launch failed: Game path does not exist: {game_path}")
            raise ValueError(f"Game path does not exist: {game_path}")

        if mode not in _VALID_MODES:
            logger.error(f"Launch failed: Unknown mode '{mode}'")
            raise ValueError(f"Unknown launch mode: {mode!r}. Must be one of {sorted(_VALID_MODES)}")

        # [SECURITY] Sanitize Wine prefix user folders (Documents, Desktop) to strip host symlinks
        if mode in ("umu", "umu_net", "wine"):
            sanitize_wine_prefix(game_path)

        deps = self.check_dependencies()
        has_firejail = deps["firejail"]

        home_dir = os.path.expanduser('~')
        umu_share = os.path.join(home_dir, '.local', 'share', 'umu')
        umu_cache = os.path.join(home_dir, '.cache', 'umu')

        os.makedirs(umu_share, exist_ok=True)
        os.makedirs(umu_cache, exist_ok=True)

        # Resolve working directory and pure executable filename.
        # This prevents Proton from seeing forward slashes in relative paths
        # (e.g. 'Subdir/game.exe'), which triggers Proton's '/unix' path handler and causes exit.
        full_exe_path = os.path.normpath(os.path.join(game_path, executable))
        if not os.path.isfile(full_exe_path):
            # Case-insensitive & subfolder fallback search for matching .exe on disk
            target_name = os.path.basename(executable).lower()
            found_path = None
            for root, dirs, files in os.walk(game_path):
                if "prefix" in root.split(os.sep):
                    continue
                for f in files:
                    if f.lower() == target_name:
                        found_path = os.path.join(root, f)
                        break
                if found_path:
                    break
            if found_path:
                logger.info(f"Auto-resolved executable on disk: {found_path} (original requested: {executable})")
                full_exe_path = found_path

        if os.path.isfile(full_exe_path):
            working_dir = os.path.dirname(full_exe_path)
            exe_filename = os.path.basename(full_exe_path)

            # Ensure game executable has execute permissions (chmod +x) across all launch modes
            try:
                current_mode = os.stat(full_exe_path).st_mode
                if not (current_mode & 0o111):
                    os.chmod(full_exe_path, current_mode | 0o755)
                    logger.info(f"Granted execute permissions (chmod +x) to {full_exe_path}")
            except Exception as perm_err:
                logger.warning(f"Could not update file permissions for {full_exe_path}: {perm_err}")
        else:
            working_dir = game_path
            exe_filename = executable

        # An empty setting is intentional: let UMU use its system/default
        # Proton resolution. Do not silently replace that choice with the
        # first GE-Proton directory found under ~/.local/share/umu.
        active_proton = self.proton_path
        if active_proton and not os.path.exists(os.path.join(active_proton, "toolmanifest.vdf")):
            logger.warning(f"Configured Proton path '{active_proton}' missing toolmanifest.vdf. Falling back to system/UMU default.")
            active_proton = ""

        q_path = shlex.quote(game_path)
        q_work_dir = shlex.quote(working_dir)
        q_exe = shlex.quote(exe_filename)
        q_umu_share = shlex.quote(umu_share)
        q_umu_cache = shlex.quote(umu_cache)
        prefix_path = shlex.quote(os.path.join(game_path, 'prefix'))
        proton_env = f"--env=PROTONPATH={shlex.quote(active_proton)} " if active_proton else ""
        proton_whitelist = f"--whitelist={shlex.quote(active_proton)} " if active_proton else ""

        # Build Firejail security hardening options
        blacklist_flags = " ".join(f"--blacklist={shlex.quote(os.path.expanduser(p))}" for p in _SECURITY_BLACKLISTS)
        security_flags = f"--private-tmp --nodbus {blacklist_flags}"

        if mode in ("umu", "umu_net"):
            runner_cmd = f"umu-run {q_exe}" if deps["umu-run"] else f"wine {q_exe}"
            if has_firejail:
                # UMU itself creates an AF_INET socket during startup. Both
                # --net=none and --net=lo are incompatible on some hosts:
                # the former blocks UMU's prerequisite check, while the latter
                # can fail when Firejail cannot create a loopback device.
                # Leave the namespace networking unchanged so UMU can start;
                # umu_net remains the explicit network-enabled launch mode.
                net_flag = ""
                if mode == "umu":
                    logger.warning(
                        "UMU launch uses host networking because Firejail cannot "
                        "provide a usable offline/loopback network namespace."
                    )
                cmd = (
                    f"cd {q_work_dir} && exec firejail "
                    f"--ignore=noroot --ignore=seccomp --ignore=restrict-namespaces "
                    f"{net_flag}{security_flags} "
                    f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                    f"{proton_whitelist}{proton_env}--env=WINEPREFIX={prefix_path} {runner_cmd}"
                )
            else:
                cmd = f"cd {q_work_dir} && export WINEPREFIX={prefix_path} && {runner_cmd}"
        elif mode == "linux":
            if has_firejail:
                cmd = f"cd {q_work_dir} && exec firejail --net=none {security_flags} --whitelist={q_path} ./{q_exe}"
            else:
                cmd = f"cd {q_work_dir} && ./{q_exe}"
        else:  # "wine"
            runner_cmd = f"wine {q_exe}"
            if has_firejail:
                cmd = (
                    f"cd {q_work_dir} && exec firejail --net=none {security_flags} "
                    f"--whitelist={q_path} "
                    f"--env=WINEPREFIX={prefix_path} {runner_cmd}"
                )
            else:
                cmd = f"cd {q_work_dir} && export WINEPREFIX={prefix_path} && {runner_cmd}"

        logger.info(f"Spawning process in mode '{mode}' (Firejail: {has_firejail}, Proton: '{active_proton}'): {cmd}")

        process_log_path = None
        log_handle = None
        try:
            # Capture diagnostics to a file rather than a pipe. The launch
            # dialog may close while the game keeps running; a pipe that is
            # no longer drained can block Wine/UMU when it fills.
            log_handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                errors="replace",
                prefix="safelauncher-game-",
                suffix=".log",
                delete=False,
            )
            process_log_path = log_handle.name
            process = subprocess.Popen(
                ["/bin/sh", "-c", cmd],
                shell=False,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=host_process_env(),
            )
            log_handle.close()
            log_handle = None
            process.safelauncher_log_path = process_log_path
            logger.info(f"Process spawned successfully with PID: {process.pid}")
            return process
        except Exception as e:
            if log_handle:
                log_handle.close()
            if process_log_path:
                try:
                    os.unlink(process_log_path)
                except OSError:
                    pass
            logger.error(f"Failed to spawn process for {executable}: {e}")
            raise
