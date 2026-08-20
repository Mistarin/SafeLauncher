"""Small, UI-independent launch diagnostics model and persistence helpers."""

from dataclasses import dataclass, field
import os
import platform
import signal
import time


def architecture() -> str:
    machine = platform.machine().lower()
    return {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "ARM64",
        "arm64": "ARM64",
    }.get(machine, machine or "unknown")


@dataclass
class LaunchDiagnostics:
    game_name: str = ""
    game_path: str = ""
    executable: str = ""
    mode: str = ""
    command: str = ""
    proton_path: str = "system/default"
    prefix_path: str = ""
    dependencies: dict = field(default_factory=dict)
    architecture: str = field(default_factory=architecture)
    output: list[str] = field(default_factory=list)
    return_code: int | None = None
    started_at: float = field(default_factory=time.time)
    log_path: str = ""
    unsafe: bool = False

    @property
    def signal_name(self) -> str:
        if self.return_code is None or self.return_code >= 0:
            return ""
        try:
            return signal.Signals(-self.return_code).name
        except ValueError:
            return f"SIG{-self.return_code}"

    @property
    def output_text(self) -> str:
        return "\n".join(self.output)

    def actionable_explanation(self) -> str:
        text = self.output_text.lower()
        if any(token in text for token in (
            "steam_api64.dll", "steam_api.dll", "steamapi_", "steam api",
            "steamapps_v", "steamapps", "unimplemented function steam",
        )):
            return (
                "Disclaimer: SafeLauncher and the Proton sandbox initialized, "
                "but the game exited because it requires a Steam API/client "
                "that is unavailable in this launch mode. This is a game "
                "compatibility or distribution issue, not a sandbox failure. "
                "Use a Steam-managed launch with Steam active, or a legitimate "
                "standalone build that does not require Steam API services."
            )
        if not self.dependencies.get("umu-run", True) and self.mode.startswith("umu"):
            return "umu-run is not installed. Install the UMU launcher or choose a native Wine/Linux runner."
        if not self.dependencies.get("firejail", True) and not self.unsafe:
            return "Firejail is unavailable. Install Firejail, then retry the sandboxed launch."
        if any(token in text for token in ("proton not found", "protonpath is not set", "error: protonpath", "could not find steamrt", "could not find steamrt4", "umu has not been setup")):
            return "The Proton/Steam Runtime is missing or not initialized. Verify or repair the selected runtime."
        if "baddroutput" in text or "badwindow" in text or "x error of failed request" in text:
            return (
                "The game reached Proton but failed while creating or querying its X11 window/display. "
                "This commonly affects fullscreen or monitor detection under Wayland/XWayland. "
                "Retry in windowed mode, compare a direct launch with SafeLauncher, and test an X11 desktop session. "
                "The graphics-session preflight above shows whether DISPLAY, XRandR, or Vulkan was already failing."
            )
        if "no permissions to create a new namespace" in text or "unprivileged_userns_clone" in text:
            return "The kernel denied user namespaces. Enable the setting for your desktop or use the clearly marked unsafe fallback."
        if "no such file" in text or "cannot open" in text:
            return "A required executable or library is missing. Verify the executable selection and runtime installation."
        if "bad exe format" in text or "exec format error" in text:
            return f"The executable/runtime architecture is incompatible with this host ({self.architecture})."
        if self.return_code == 0:
            if any(marker in text for marker in (
                "presenter:", "actual swap chain", "engaging frame rate limiter",
                "setting display mode", "fsync: up and running", "dxvk:"
            )):
                return "The game session finished normally (exit code 0)."
            return "The runtime exited before the game stayed running. Check the full output and prefix health."
        if self.signal_name:
            return f"The launch process was terminated by {self.signal_name}. Check runtime compatibility and system limits."
        return f"The launch process exited with code {self.return_code}. Review the full output above for the first error."

    def as_text(self) -> str:
        status = f"exit={self.return_code}"
        if self.signal_name:
            status += f" signal={self.signal_name}"
        deps = ", ".join(f"{key}={value}" for key, value in sorted(self.dependencies.items())) or "unknown"
        return (
            f"SafeLauncher launch diagnostics\n"
            f"Game: {self.game_name}\nPath: {self.game_path}\nExecutable: {self.executable}\n"
            f"Mode: {self.mode}\nArchitecture: {self.architecture}\nProton/runtime: {self.proton_path}\n"
            f"Prefix: {self.prefix_path}\nDependencies: {deps}\nStatus: {status}\n"
            f"Command: {self.command}\nLog: {self.log_path}\n\n"
            f"Action: {self.actionable_explanation()}\n\n"
            f"Process output:\n{self.output_text or '(no output)'}\n"
        )


def diagnostics_directory() -> str:
    root = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    path = os.path.join(root, "safelauncher", "diagnostics")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def persist_diagnostics(report: LaunchDiagnostics) -> str:
    filename = f"{int(report.started_at)}-{report.game_name or 'launch'}.txt"
    filename = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in filename)
    path = os.path.join(diagnostics_directory(), filename)
    try:
        report.log_path = path
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(report.as_text())
    except OSError:
        pass
    return path
