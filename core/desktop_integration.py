"""SafeLauncher XDG desktop-menu and login-start integration."""

import os
import shutil
import subprocess
import sys
import tempfile

from core.logger import get_logger

logger = get_logger("DesktopIntegration")

_DESKTOP_NAME = "safelauncher.desktop"
_DESKTOP_SHORTCUT_NAME = "SafeLauncher.desktop"
_ICON_NAME = "safelauncher.png"
_MANAGED_MARKER = "X-SafeLauncher-Managed=true"


def _xdg_data_home() -> str:
    return os.path.expanduser(os.environ.get("XDG_DATA_HOME", "~/.local/share"))


def _xdg_config_home() -> str:
    return os.path.expanduser(os.environ.get("XDG_CONFIG_HOME", "~/.config"))


def get_applications_dir() -> str:
    return os.path.join(_xdg_data_home(), "applications")


def get_icons_dir() -> str:
    return os.path.join(_xdg_data_home(), "icons", "hicolor", "256x256", "apps")


def get_desktop_file_path() -> str:
    return os.path.join(get_applications_dir(), _DESKTOP_NAME)


def get_desktop_shortcut_path() -> str:
    return os.path.join(os.path.expanduser("~/Desktop"), _DESKTOP_SHORTCUT_NAME)


def get_autostart_file_path() -> str:
    return os.path.join(_xdg_config_home(), "autostart", _DESKTOP_NAME)


def _is_regular_file(path: str) -> bool:
    return os.path.isfile(path) and not os.path.islink(path)


def _managed_or_known_entry(path: str) -> bool:
    if not _is_regular_file(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read(8192)
        return _MANAGED_MARKER in content or "Name=SafeLauncher" in content
    except OSError:
        return False


def is_desktop_entry_installed() -> bool:
    return _managed_or_known_entry(get_desktop_file_path())


def is_desktop_shortcut_installed() -> bool:
    return _managed_or_known_entry(get_desktop_shortcut_path())


def is_startup_enabled() -> bool:
    return _managed_or_known_entry(get_autostart_file_path())


def _desktop_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _resolve_launch_target() -> tuple[str, str, str]:
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    main_py = os.path.join(project_dir, "main.py")
    appimage_path = os.environ.get("APPIMAGE", "").strip()
    if appimage_path and os.path.isfile(appimage_path):
        return _desktop_quote(appimage_path), os.path.dirname(appimage_path), "safelauncher"

    launch_script = os.path.join(project_dir, "setup", "02-launch.sh")
    if os.path.isfile(launch_script) and os.access(launch_script, os.X_OK):
        return _desktop_quote(launch_script), project_dir, "safelauncher"

    python_bin = sys.executable or "python3"
    for candidate in (
        os.path.join(project_dir, ".venv", "bin", "python"),
        os.path.join(project_dir, "venv", "bin", "python"),
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            python_bin = candidate
            break
    return f"{_desktop_quote(python_bin)} {_desktop_quote(main_py)}", project_dir, "safelauncher"


def _entry_content(*, startup: bool) -> str:
    exec_command, work_dir, icon = _resolve_launch_target()
    args = "" if startup else " %U"
    lines = [
        "[Desktop Entry]", "Version=1.0", "Type=Application", "Name=SafeLauncher",
        "GenericName=Game Sandbox Launcher", "Comment=Secure isolated game launcher and library manager",
        f"Exec={exec_command}{args}", f"Path={_desktop_quote(work_dir)}", f"Icon={icon}",
        "Terminal=false", "Categories=Game;Utility;", "Keywords=game;launcher;sandbox;firejail;steam;proton;wine;",
        _MANAGED_MARKER,
    ]
    if startup:
        lines.extend(("NoDisplay=true", "X-GNOME-Autostart-enabled=true"))
    else:
        lines.extend(("StartupNotify=true", "StartupWMClass=SafeLauncher", "MimeType=x-scheme-handler/safelauncher;"))
    return "\n".join(lines) + "\n"


def _atomic_write(path: str, content: str, mode: int = 0o755) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _refresh_desktop_database() -> None:
    updater = shutil.which("update-desktop-database")
    if not updater:
        return
    try:
        subprocess.run([updater, get_applications_dir()], capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        logger.debug("Could not refresh the desktop database", exc_info=True)


def install_safelauncher_desktop_entry() -> tuple[bool, str]:
    """Install/update the application-menu entry and optional Desktop shortcut."""
    try:
        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logo_path = os.path.join(project_dir, "assets", "logo.png")
        os.makedirs(get_icons_dir(), exist_ok=True)
        if os.path.isfile(logo_path):
            temporary_icon = os.path.join(get_icons_dir(), f".{_ICON_NAME}.tmp")
            shutil.copyfile(logo_path, temporary_icon)
            os.replace(temporary_icon, os.path.join(get_icons_dir(), _ICON_NAME))
        _atomic_write(get_desktop_file_path(), _entry_content(startup=False))
        desktop_folder = os.path.dirname(get_desktop_shortcut_path())
        if os.path.isdir(desktop_folder):
            _atomic_write(get_desktop_shortcut_path(), _entry_content(startup=False))
        _refresh_desktop_database()
        logger.info("SafeLauncher desktop entry installed at %s", get_desktop_file_path())
        return True, "Added to the Applications Menu and Desktop."
    except (OSError, ValueError) as error:
        logger.error("Failed to install desktop shortcut: %s", error)
        return False, f"Failed to install shortcut: {error}"


def remove_safelauncher_desktop_entry() -> tuple[bool, str]:
    """Remove only SafeLauncher-managed menu/desktop files and its copied icon."""
    try:
        removed = []
        for path in (get_desktop_file_path(), get_desktop_shortcut_path()):
            if _managed_or_known_entry(path):
                os.unlink(path)
                removed.append(path)
        icon_path = os.path.join(get_icons_dir(), _ICON_NAME)
        if _is_regular_file(icon_path):
            os.unlink(icon_path)
            removed.append(icon_path)
        _refresh_desktop_database()
        logger.info("SafeLauncher desktop integration removed (%d files)", len(removed))
        return True, "Removed from the Applications Menu and Desktop."
    except OSError as error:
        logger.error("Failed to remove desktop integration: %s", error)
        return False, f"Failed to remove shortcut: {error}"


def set_startup_enabled(enabled: bool) -> tuple[bool, str]:
    """Enable or disable launching SafeLauncher when the desktop session starts."""
    path = get_autostart_file_path()
    try:
        if enabled:
            _atomic_write(path, _entry_content(startup=True))
            return True, "SafeLauncher will start when you sign in."
        if _managed_or_known_entry(path):
            os.unlink(path)
        return True, "SafeLauncher will not start automatically."
    except (OSError, ValueError) as error:
        logger.error("Failed to update startup integration: %s", error)
        return False, f"Failed to update startup setting: {error}"
