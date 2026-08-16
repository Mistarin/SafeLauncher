import urllib.request
import json
import os
import re
from PyQt6.QtCore import pyqtSignal
from core.safe_thread import SafeQThread
from core.logger import get_logger

logger = get_logger("SteamBuildTracker")


def read_local_steam_build(game_path: str, steam_id: str) -> tuple[str, int]:
    """Read buildid/LastUpdated from a copied Steam appmanifest, if present."""
    if not game_path or not steam_id:
        return "", 0
    manifest_name = f"appmanifest_{steam_id}.acf"
    candidates = (
        os.path.join(game_path, "_Manifests", manifest_name),
        os.path.join(game_path, "steamapps", manifest_name),
        os.path.join(game_path, manifest_name),
    )
    for manifest_path in candidates:
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8", errors="replace") as manifest:
                content = manifest.read()
            build = re.search(r'"buildid"\s+"([^"]+)"', content, re.IGNORECASE)
            updated = re.search(r'"LastUpdated"\s+"(\d+)"', content, re.IGNORECASE)
            return (build.group(1) if build else "", int(updated.group(1)) if updated else 0)
        except (OSError, ValueError) as error:
            logger.warning(f"Could not read local Steam manifest {manifest_path}: {error}")
            return "", 0
    return "", 0


class SteamBuildFetcher(SafeQThread):
    """Fetch the public Steam branch build and compare it with the local build."""
    update_checked = pyqtSignal(int, str, int, bool)  # game_id, build, updated_at, needs_update
    check_failed = pyqtSignal(int, str)  # game_id, human-readable reason

    def __init__(self, game_id: int, steam_id: str, local_build_id: str = "", local_build_date: int = 0, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.steam_id = str(steam_id).strip()
        self.local_build_id = str(local_build_id).strip()
        self.local_build_date = int(local_build_date or 0)

    def safe_run(self):
        if self.isInterruptionRequested():
            return
        if not self.steam_id or self.steam_id == "0":
            self._fail("No Steam AppID is configured")
            return

        try:
            url = f"https://api.steamcmd.net/v1/info/{self.steam_id}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "SafeLauncher/1.0 (Linux Game Sandbox Manager)"}
            )
            logger.debug(f"Checking Steam build for game {self.game_id}, AppID {self.steam_id}")
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    self._fail(f"Steam metadata returned HTTP {resp.status}")
                    return
                data = json.loads(resp.read().decode("utf-8"))
                if not isinstance(data, dict):
                    self._fail("Steam metadata returned an invalid response")
                    return
                if self.isInterruptionRequested():
                    return
                app_data = data.get("data", {}).get(self.steam_id, {})
                depots = app_data.get("depots", {})
                branches = depots.get("branches", {})
                public_branch = branches.get("public", {})
                latest_build_id = str(public_branch.get("buildid", "")).strip()
                latest_build_date = public_branch.get("timeupdated", 0)
                try:
                    latest_build_date = int(latest_build_date or 0)
                except (TypeError, ValueError):
                    latest_build_date = 0

                if latest_build_id:
                    if self.local_build_id:
                        is_update = (latest_build_id != self.local_build_id)
                    elif self.local_build_date > 0:
                        is_update = (latest_build_date > self.local_build_date)
                    else:
                        is_update = False
                    logger.info(f"Steam Build check for game {self.game_id} (AppID {self.steam_id}): latest={latest_build_id}, update={is_update}")
                    if self.isInterruptionRequested():
                        return
                    self.update_checked.emit(self.game_id, latest_build_id, latest_build_date, is_update)
                    return
                self._fail("Steam returned no public branch build for this AppID")
        except Exception as e:
            logger.warning(f"Failed to check Steam build for AppID {self.steam_id}: {e}")
            self._fail(f"Steam check failed: {e}")

    def _fail(self, reason: str):
        if not self.isInterruptionRequested():
            self.check_failed.emit(self.game_id, reason)
