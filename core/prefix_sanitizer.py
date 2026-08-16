"""Wine prefix security sanitizer for SafeLauncher.

Scans the WINEPREFIX user directories (drive_c/users/*/) and removes host symlinks
(such as Documents -> /home/user/Documents, Desktop -> /home/user/Desktop) that Wine
creates by default. Replaces them with real isolated folders inside the prefix to prevent
untrusted game binaries / ransomware from accessing personal files on the host OS.
"""

import os
from core.logger import get_logger

logger = get_logger("PrefixSanitizer")

_SENSITIVE_FOLDERS = (
    "Documents",
    "My Documents",
    "Desktop",
    "Pictures",
    "My Pictures",
    "Videos",
    "My Videos",
    "Music",
    "My Music",
    "Downloads",
)


def sanitize_wine_prefix(game_path: str) -> bool:
    """Scan and sanitize all user directories inside the Wine prefix.

    Returns True if prefix was inspected and sanitized, False if no prefix exists yet.
    """
    if not game_path or not os.path.exists(game_path):
        return False

    prefix_dir = os.path.join(game_path, "prefix")
    users_dir = os.path.join(prefix_dir, "drive_c", "users")

    if not os.path.isdir(users_dir):
        logger.debug(f"No Wine users directory found at {users_dir} (prefix not initialized yet).")
        return False

    sanitized_count = 0
    try:
        # Sanitize dosdevices/z: (symlink to / root filesystem in Wine)
        dosdevices_dir = os.path.join(prefix_dir, "dosdevices")
        if os.path.isdir(dosdevices_dir):
            z_drive = os.path.join(dosdevices_dir, "z:")
            if os.path.islink(z_drive):
                try:
                    os.unlink(z_drive)
                    logger.info(f"[SECURITY] Removed host root mapping dosdevices/z: in {prefix_dir}")
                    sanitized_count += 1
                except Exception as e:
                    logger.debug(f"Could not remove dosdevices/z: symlink: {e}")

        user_entries = os.listdir(users_dir)
        for user_folder in user_entries:
            user_path = os.path.join(users_dir, user_folder)
            if not os.path.isdir(user_path):
                continue

            for target_name in _SENSITIVE_FOLDERS:
                target_path = os.path.join(user_path, target_name)
                if os.path.islink(target_path):
                    link_dest = os.readlink(target_path)
                    logger.warning(
                        f"[SECURITY] Removing host symlink in Wine prefix: {target_path} → {link_dest}"
                    )
                    try:
                        os.unlink(target_path)
                        os.makedirs(target_path, exist_ok=True)
                        sanitized_count += 1
                    except Exception as e:
                        logger.error(f"Failed to replace symlink {target_path}: {e}")
                elif not os.path.exists(target_path):
                    try:
                        os.makedirs(target_path, exist_ok=True)
                    except Exception:
                        pass

        if sanitized_count > 0:
            logger.info(f"Successfully sanitized {sanitized_count} host symlink(s) in Wine prefix {prefix_dir}.")
        return True
    except Exception as e:
        logger.error(f"Error while sanitizing Wine prefix at {prefix_dir}: {e}")
        return False
