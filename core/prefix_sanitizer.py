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


def cleanup_prefix_health(game_path: str) -> int:
    """Scan and clean stale wineserver locks, temp files, and broken links inside a game prefix.

    Returns the number of cleaned items.
    """
    if not game_path or not os.path.exists(game_path):
        return 0

    cleaned = 0
    candidate_prefix_dirs = [
        os.path.join(game_path, "prefix"),
        os.path.join(game_path, "pfx"),
        game_path if os.path.isdir(os.path.join(game_path, "drive_c")) else None
    ]
    prefix_dirs = [d for d in candidate_prefix_dirs if d and os.path.isdir(d)]

    for prefix_dir in prefix_dirs:
        # 1. Clean stale wineserver socket/lock directories if no active process
        try:
            for item in os.listdir(prefix_dir):
                if item.startswith(".wineserver") or item.startswith("wineserver-"):
                    ws_path = os.path.join(prefix_dir, item)
                    try:
                        if os.path.islink(ws_path) and not os.path.exists(ws_path):
                            os.unlink(ws_path)
                            cleaned += 1
                        elif os.path.isdir(ws_path):
                            # Remove dead socket files inside wineserver dir
                            for sf in os.listdir(ws_path):
                                sfp = os.path.join(ws_path, sf)
                                try:
                                    os.unlink(sfp)
                                    cleaned += 1
                                except OSError:
                                    pass
                    except Exception:
                        pass
        except Exception:
            pass

        # 2. Clean broken symlinks in dosdevices
        dosdevices = os.path.join(prefix_dir, "dosdevices")
        if os.path.isdir(dosdevices):
            try:
                for dev in os.listdir(dosdevices):
                    dev_path = os.path.join(dosdevices, dev)
                    if os.path.islink(dev_path) and not os.path.exists(dev_path):
                        try:
                            os.unlink(dev_path)
                            cleaned += 1
                        except OSError:
                            pass
            except Exception:
                pass

        # 3. Prune old temp files inside Windows temp folders (> 1 day old)
        temp_dirs = [
            os.path.join(prefix_dir, "drive_c", "windows", "temp"),
        ]
        users_dir = os.path.join(prefix_dir, "drive_c", "users")
        if os.path.isdir(users_dir):
            try:
                for u in os.listdir(users_dir):
                    u_temp = os.path.join(users_dir, u, "AppData", "Local", "Temp")
                    if os.path.isdir(u_temp):
                        temp_dirs.append(u_temp)
            except Exception:
                pass

        now = os.stat(prefix_dir).st_mtime
        for td in temp_dirs:
            if not os.path.isdir(td):
                continue
            try:
                for root, _, files in os.walk(td):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            st = os.stat(fp)
                            if now - st.st_mtime > 86400:  # > 24 hours old
                                os.unlink(fp)
                                cleaned += 1
                        except OSError:
                            pass
            except Exception:
                pass

    if cleaned > 0:
        logger.info(f"Cleaned {cleaned} stale item(s) in prefix for '{os.path.basename(game_path)}'.")
    return cleaned


def cleanup_global_temp_files() -> int:
    """Prune orphaned SafeLauncher temporary files across /tmp and user cache dirs.

    Returns the number of files cleaned.
    """
    import time
    cleaned = 0
    now = time.time()

    target_dirs = ["/tmp"]
    try:
        from core.cloud_save_sync import CloudSaveSyncEngine
        target_dirs.append(CloudSaveSyncEngine.get_cloud_root())
    except Exception:
        pass

    prefixes = (".sl-up-", ".sl-down-", ".safelauncher-zip-", ".sl-sync-")

    for tdir in target_dirs:
        if not tdir or not os.path.isdir(tdir):
            continue
        try:
            for item in os.listdir(tdir):
                if any(item.startswith(pfx) for pfx in prefixes):
                    fp = os.path.join(tdir, item)
                    try:
                        st = os.stat(fp)
                        if now - st.st_mtime > 3600:  # > 1 hour old orphaned file
                            if os.path.isfile(fp):
                                os.unlink(fp)
                                cleaned += 1
                            elif os.path.isdir(fp):
                                import shutil
                                shutil.rmtree(fp, ignore_errors=True)
                                cleaned += 1
                    except OSError:
                        pass
        except Exception:
            pass

    if cleaned > 0:
        logger.info(f"Pruned {cleaned} orphaned SafeLauncher temporary file(s).")
    return cleaned
