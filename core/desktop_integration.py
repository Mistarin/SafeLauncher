"""Linux Desktop Integration / Start Screen shortcut installer for SafeLauncher."""

import os
import sys
import stat
import shutil
import subprocess
from core.logger import get_logger

logger = get_logger("DesktopIntegration")


def get_desktop_file_path() -> str:
    return os.path.expanduser("~/.local/share/applications/safelauncher.desktop")


def is_desktop_entry_installed() -> bool:
    return os.path.exists(get_desktop_file_path())


def install_safelauncher_desktop_entry() -> tuple[bool, str]:
    """Create a .desktop entry in ~/.local/share/applications so SafeLauncher
    appears in the system Application Menu / Start Screen and app search.
    """
    try:
        apps_dir = os.path.expanduser("~/.local/share/applications")
        os.makedirs(apps_dir, exist_ok=True)

        project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        main_py = os.path.join(project_dir, "main.py")
        logo_path = os.path.join(project_dir, "assets", "logo.png")

        # Resolve best python interpreter (prefer current virtual environment or sys.executable)
        python_bin = sys.executable or "python3"
        for venv_candidate in (
            os.path.join(project_dir, ".venv", "bin", "python"),
            os.path.join(project_dir, "venv", "bin", "python"),
        ):
            if os.path.isfile(venv_candidate) and os.access(venv_candidate, os.X_OK):
                python_bin = venv_candidate
                break

        desktop_path = get_desktop_file_path()
        content = (
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=SafeLauncher\n"
            "GenericName=Game Sandbox Launcher\n"
            "Comment=Secure isolated game launcher and library manager\n"
            f'Exec="{python_bin}" "{main_py}"\n'
            f"Path={project_dir}\n"
            f"Icon={logo_path}\n"
            "Terminal=false\n"
            "Categories=Game;Utility;\n"
            "Keywords=game;launcher;sandbox;firejail;steam;proton;wine;\n"
            "StartupWMClass=SafeLauncher\n"
        )

        with open(desktop_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Make desktop file executable
        current_mode = os.stat(desktop_path).st_mode
        os.chmod(desktop_path, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # Also copy/symlink to ~/Desktop if Desktop directory exists
        desktop_folder = os.path.expanduser("~/Desktop")
        if os.path.isdir(desktop_folder):
            desktop_icon_target = os.path.join(desktop_folder, "SafeLauncher.desktop")
            try:
                shutil.copyfile(desktop_path, desktop_icon_target)
                os.chmod(desktop_icon_target, current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception as e:
                logger.debug(f"Could not copy to Desktop folder: {e}")

        # Update desktop database so launcher appears immediately in GNOME / KDE / XFCE start menu
        if shutil.which("update-desktop-database"):
            try:
                subprocess.run(
                    ["update-desktop-database", apps_dir],
                    capture_output=True,
                    check=False,
                    timeout=5
                )
            except Exception:
                pass

        logger.info(f"SafeLauncher desktop shortcut successfully installed to {desktop_path}")
        return True, "Added to Start Screen & Applications Menu!"
    except Exception as e:
        logger.error(f"Failed to install desktop shortcut: {e}")
        return False, f"Failed to install shortcut: {e}"
