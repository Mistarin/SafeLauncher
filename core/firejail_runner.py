import os
import shlex
import shutil
import subprocess
import tempfile
from core.launch_diagnostics import LaunchDiagnostics
from core.interfaces import ISandboxRunner
from core.host_process import host_process_env
from core.prefix_sanitizer import sanitize_wine_prefix, cleanup_prefix_health
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

_GPU_CACHE_PATHS = [
    "~/.nv",
    "~/.cache/nvidia",
    "~/.cache/mesa_shader_cache",
    "~/.cache/vkd3d_shader_cache",
    "~/.cache/dxvk-cache",
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

    def launch(self, game_path: str, executable: str, mode: str, steam_id: str = "", sandbox: bool = True, env_vars: dict = None) -> subprocess.Popen:
        if not game_path or not os.path.exists(game_path):
            logger.error(f"Launch failed: Game path does not exist: {game_path}")
            raise ValueError(f"Game path does not exist: {game_path}")

        if mode not in _VALID_MODES:
            logger.error(f"Launch failed: Unknown mode '{mode}'")
            raise ValueError(f"Unknown launch mode: {mode!r}. Must be one of {sorted(_VALID_MODES)}")

        # [SECURITY] Sanitize Wine prefix user folders (Documents, Desktop) and clean stale locks
        if mode in ("umu", "umu_net", "wine"):
            sanitize_wine_prefix(game_path)
            cleanup_prefix_health(game_path)

        deps = self.check_dependencies()
        has_firejail = deps["firejail"] and sandbox

        home_dir = os.path.expanduser('~')
        umu_share = os.path.join(home_dir, '.local', 'share', 'umu')
        umu_cache = os.path.join(home_dir, '.cache', 'umu')
        # UMU currently installs downloaded Proton builds in Steam's
        # compatibilitytools.d directory, not only under ~/.local/share/umu.
        # If this directory is hidden by Firejail, UMU believes Proton is
        # missing and downloads/extracts it on every launch.
        steam_compat_dir = os.path.join(
            home_dir, '.local', 'share', 'Steam', 'compatibilitytools.d'
        )

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

        # If the user has not selected a Proton tool explicitly, reuse UMU's
        # installed runtime when available. Leaving this implicit makes UMU
        # download and unpack Proton again inside every sandbox launch.
        active_proton = self.proton_path
        if not active_proton and mode in ("umu", "umu_net"):
            cached_umu = os.path.join(umu_share, "compatibilitytools", "UMU-Latest")
            if os.path.isfile(os.path.join(cached_umu, "toolmanifest.vdf")):
                active_proton = cached_umu
                logger.info(f"Reusing cached UMU Proton runtime: {active_proton}")
        os.makedirs(steam_compat_dir, exist_ok=True)

        q_path = shlex.quote(game_path)
        q_work_dir = shlex.quote(working_dir)
        q_exe = shlex.quote(exe_filename)

        q_umu_share = shlex.quote(umu_share)
        q_umu_cache = shlex.quote(umu_cache)
        q_steam_compat = shlex.quote(steam_compat_dir)
        prefix_path = shlex.quote(os.path.join(game_path, 'prefix'))
        proton_env = f"--env=PROTONPATH={shlex.quote(active_proton)} " if active_proton else ""
        proton_whitelist = f"--whitelist={shlex.quote(active_proton)} " if active_proton else ""
        # Auto-detect Steam AppID from steam_appid.txt if not specified
        if not steam_id or str(steam_id).strip() in ("", "0", "None"):
            for cand_dir in (working_dir, game_path):
                appid_file = os.path.join(cand_dir, "steam_appid.txt")
                if os.path.isfile(appid_file):
                    try:
                        with open(appid_file, "r") as af:
                            val = af.read().strip()
                            if val.isdigit() and int(val) > 0:
                                steam_id = val
                                break
                    except Exception:
                        pass

        game_id_env = ""
        if steam_id and str(steam_id).isdigit() and int(steam_id) > 0:
            s_id = int(steam_id)
            game_id_env = f"--env=GAMEID=umu-{s_id} --env=SteamAppId={s_id} --env=SteamGameId={s_id} --env=STEAM_COMPAT_APP_ID={s_id} "
        game_id_export = ""
        if steam_id and str(steam_id).isdigit() and int(steam_id) > 0:
            s_id = int(steam_id)
            game_id_export = f"export GAMEID=umu-{s_id} SteamAppId={s_id} SteamGameId={s_id} STEAM_COMPAT_APP_ID={s_id} && "

        # GPU Driver Shader Cache Whitelist
        gpu_whitelist_flags = ""
        for p in _GPU_CACHE_PATHS:
            expanded = os.path.expanduser(p)
            try:
                os.makedirs(expanded, exist_ok=True)
            except OSError:
                pass
            if os.path.exists(expanded):
                gpu_whitelist_flags += f"--whitelist={shlex.quote(expanded)} "

        # Keep the complete runtime state in the same persistent launch log.
        # Only create the per-launch diagnostics directory when verbose logging
        # is requested; unconditionally it littered every game folder with
        # stale empty dirs and (via the env dump below) persisted the launcher
        # environment — potentially including API tokens — on every launch.
        enable_verbose = os.environ.get("SAFELAUNCHER_DEBUG", "0").strip() == "1" or \
            os.environ.get("SAFELAUNCHER_PROTON_LOG", "0") == "1"
        if enable_verbose:
            proton_log_dir_path = tempfile.mkdtemp(
                prefix=".safelauncher-proton-",
                dir=os.path.realpath(game_path),
            )
        else:
            proton_log_dir_path = ""
        proton_log_dir = shlex.quote(proton_log_dir_path)

        proton_log_flag = "1" if enable_verbose else "0"
        vkd3d_debug = os.environ.get("SAFELAUNCHER_VKD3D_DEBUG", "warn" if enable_verbose else "none").strip()
        wine_debug = os.environ.get("SAFELAUNCHER_WINEDEBUG", "-all").strip() or "-all"

        debug_exports = (
            f"export PROTON_LOG={proton_log_flag} VKD3D_DEBUG={shlex.quote(vkd3d_debug)} "
            f"WINEDEBUG={shlex.quote(wine_debug)} PROTON_LOG_DIR={proton_log_dir} && "
        )
        # Opt-in: `env | sort` dumps every ambient token into a persistent file.
        diagnostic_header = (
            f"echo '--- runtime={shlex.quote(active_proton or 'system/default')} "
            f"prefix={prefix_path} game={q_path} ---'; "
        )
        if enable_verbose:
            diagnostic_header = (
                "echo '===== SAFELAUNCHER DIAGNOSTICS ====='; "
                "echo '--- complete launch environment ---'; env | sort; "
                "echo '--- graphics session preflight ---'; "
                "printf 'DISPLAY=%s\\nWAYLAND_DISPLAY=%s\\nXDG_SESSION_TYPE=%s\\n' "
                "\"$DISPLAY\" \"$WAYLAND_DISPLAY\" \"$XDG_SESSION_TYPE\"; "
                "if command -v xrandr >/dev/null 2>&1; then "
                "echo '--- xrandr --query ---'; xrandr --query 2>&1 | sed -n '1,120p'; "
                "else echo 'xrandr: not installed'; fi; "
                "if command -v vulkaninfo >/dev/null 2>&1; then "
                "echo '--- vulkaninfo --summary ---'; vulkaninfo --summary 2>&1 | sed -n '1,80p'; "
                "else echo 'vulkaninfo: not installed'; fi; "
                "echo '--- initial process tree ---'; ps -ef --forest; "
                + diagnostic_header
            )
        enable_strace = os.environ.get("SAFELAUNCHER_STRACE", "0").strip() == "1"
        strace_path = shutil.which("strace") if enable_strace else None
        if strace_path:
            trace_prefix = (
                f"{shlex.quote(strace_path)} -f -tt -yy -s 256 "
                "-o /proc/self/fd/1 "
            )
            logger.info("strace enabled via SAFELAUNCHER_STRACE=1; recursive syscall tracing enabled for game launch")
        else:
            trace_prefix = ""
            if enable_strace:
                logger.warning("SAFELAUNCHER_STRACE=1 requested but strace is not installed.")
        firejail_prefix = "" if trace_prefix else "exec "
        firejail_audit = ""

        # Sandbox name for reliable container discovery & termination
        sandbox_id = f"safelauncher-{os.path.basename(os.path.normpath(game_path)).lower()}"
        sandbox_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in sandbox_id)[:32]
        sandbox_name_flag = f"--name={sandbox_id} " if has_firejail else ""

        # Build Firejail security hardening options
        blacklist_flags = " ".join(f"--blacklist={shlex.quote(os.path.expanduser(p))}" for p in _SECURITY_BLACKLISTS)
        common_security_flags = f"--private-tmp {blacklist_flags}"
        security_flags = f"{common_security_flags} --nodbus"
        game_compat_flags = "--ignore=noinput --ignore=novideo"

        # Per-game custom environment variables and presets (FSR, DXVK, etc.)
        custom_env_flags = ""
        custom_env_exports = ""
        if env_vars and isinstance(env_vars, dict):
            for k, v in env_vars.items():
                clean_k = "".join(c for c in str(k) if c.isalnum() or c == "_")
                if clean_k and v is not None and str(v).strip() != "":
                    q_v = shlex.quote(str(v))
                    custom_env_flags += f"--env={clean_k}={q_v} "
                    custom_env_exports += f"export {clean_k}={q_v} && "

        if mode in ("umu", "umu_net"):
            runner_cmd = f"umu-run {q_exe}" if deps["umu-run"] else f"wine {q_exe}"
            if has_firejail:
                net_flag = ""
                if mode == "umu":
                    logger.warning(
                        "UMU launch uses host networking because Firejail cannot "
                        "provide a usable offline/loopback network namespace."
                    )
                cmd = (
                    f"cd {q_work_dir} && {debug_exports}{diagnostic_header}{trace_prefix}{firejail_prefix}firejail {sandbox_name_flag}"
                    f"--noprofile --ignore=noroot --ignore=seccomp --ignore=restrict-namespaces "
                    f"{net_flag}{firejail_audit}{common_security_flags} {game_compat_flags} "
                    f"--whitelist={q_path} --whitelist={q_umu_share} --whitelist={q_umu_cache} "
                    f"--whitelist={q_steam_compat} {gpu_whitelist_flags}"
                    f"{proton_whitelist}{proton_env}{game_id_env}{custom_env_flags}--env=WINEPREFIX={prefix_path} {runner_cmd}"
                )
            else:
                cmd = f"cd {q_work_dir} && {debug_exports}{diagnostic_header}{game_id_export}{custom_env_exports}export WINEPREFIX={prefix_path} && {trace_prefix}{runner_cmd}"
        elif mode == "linux":
            if has_firejail:
                cmd = f"cd {q_work_dir} && {diagnostic_header}{trace_prefix}{firejail_prefix}firejail {sandbox_name_flag}--noprofile --net=none {security_flags} --whitelist={q_path} {gpu_whitelist_flags}{custom_env_flags}./{q_exe}"
            else:
                cmd = f"cd {q_work_dir} && {diagnostic_header}{custom_env_exports}./{q_exe}"
        else:  # "wine"
            runner_cmd = f"wine {q_exe}"
            if has_firejail:
                cmd = (
                    f"cd {q_work_dir} && {diagnostic_header}{trace_prefix}{firejail_prefix}firejail {sandbox_name_flag}--noprofile --net=none {security_flags} "
                    f"--whitelist={q_path} {gpu_whitelist_flags}"
                    f"{custom_env_flags}--env=WINEPREFIX={prefix_path} {runner_cmd}"
                )
            else:
                cmd = f"cd {q_work_dir} && {diagnostic_header}{debug_exports}{custom_env_exports}export WINEPREFIX={prefix_path} && {trace_prefix}{runner_cmd}"

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
            process.safelauncher_sandbox_name = sandbox_id if has_firejail else None
            if steam_id and str(steam_id).isdigit() and int(steam_id) > 0:
                process.safelauncher_extra_log_paths = [
                    os.path.join(proton_log_dir_path, f"steam-{int(steam_id)}.log")
                ]
            process.safelauncher_diagnostics = LaunchDiagnostics(
                game_path=game_path,
                executable=executable,
                mode=mode,
                command=cmd,
                proton_path=active_proton or "system/default",
                prefix_path=os.path.join(game_path, "prefix"),
                dependencies=deps,
                unsafe=not sandbox,
            )
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
