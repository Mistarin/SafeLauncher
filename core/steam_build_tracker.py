import json
import os
import re
import requests
from PyQt6.QtCore import pyqtSignal
from core.safe_thread import SafeQThread
from core.logger import get_logger
from core.network_policy import automatic_network_allowed

logger = get_logger("SteamBuildTracker")


def has_resolved_build_reference(build_id: str, build_date: int) -> bool:
    """Return whether a current build ID has a known matching date.

    Build IDs are only useful as a dated reference once SafeLauncher has a
    trusted date for that same ID (from a manifest, an explicit entry, or a
    successful historical lookup).  Keeping this check in one place prevents
    each UI surface from displaying a different meaning for ``(found)``.
    """
    if not str(build_id or "").strip():
        return False
    try:
        return int(build_date or 0) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def backfill_matching_build_date(
    current_build_id: str,
    current_build_date: int,
    latest_build_id: str,
    latest_build_date: int,
) -> int:
    """Return a current build date only when its ID matches the latest build.

    A build ID does not encode a timestamp.  The latest branch timestamp is
    therefore safe to reuse only when both IDs are exactly the same and the
    local/current date has not already been supplied by a manifest or user.
    """
    try:
        existing_date = int(current_build_date or 0)
    except (TypeError, ValueError, OverflowError):
        existing_date = 0
    if existing_date > 0:
        return existing_date

    current_id = str(current_build_id or "").strip()
    latest_id = str(latest_build_id or "").strip()
    if not current_id or not latest_id or current_id != latest_id:
        return existing_date

    try:
        candidate_date = int(latest_build_date or 0)
    except (TypeError, ValueError, OverflowError):
        candidate_date = 0
    return candidate_date if candidate_date > 0 else existing_date


def _read_steam_manifest(manifest_path: str) -> tuple[str, int]:
    """Read an installed Steam manifest's build ID and LastUpdated value."""
    try:
        with open(manifest_path, "r", encoding="utf-8", errors="replace") as manifest:
            content = manifest.read()
        build = re.search(r'"buildid"\s+"([^"]+)"', content, re.IGNORECASE)
        updated = re.search(r'"LastUpdated"\s+"(\d+)"', content, re.IGNORECASE)
        return (build.group(1) if build else "", int(updated.group(1)) if updated else 0)
    except (OSError, ValueError) as error:
        logger.debug("Could not read Steam manifest %s: %s", manifest_path, error)
        return "", 0


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
        if os.path.isfile(manifest_path):
            build_id, build_date = _read_steam_manifest(manifest_path)
            if build_id or build_date:
                return build_id, build_date
    return "", 0


class SteamBuildFetcher(SafeQThread):
    """Fetch the public Steam branch build and compare it with the local build."""
    update_checked = pyqtSignal(int, str, int, bool)  # game_id, build, updated_at, needs_update
    check_failed = pyqtSignal(int, str)  # game_id, human-readable reason
    offline_detected = pyqtSignal(int)  # game_id — no internet connection

    def __init__(self, game_id: int, steam_id: str, local_build_id: str = "", local_build_date: int = 0, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.steam_id = str(steam_id).strip()
        self.local_build_id = str(local_build_id).strip()
        self.local_build_date = int(local_build_date or 0)

    def safe_run(self):
        if self.isInterruptionRequested() or not automatic_network_allowed():
            return
        if not self.steam_id or self.steam_id == "0":
            self._fail("No Steam AppID is configured")
            return

        resp = None
        try:
            url = f"https://api.steamcmd.net/v1/info/{self.steam_id}"
            headers = {"User-Agent": "SafeLauncher/1.0 (Linux Game Sandbox Manager)"}
            logger.debug(f"Checking Steam build for game {self.game_id}, AppID {self.steam_id}")
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                self._fail(f"Steam metadata returned HTTP {resp.status_code}")
                return
            data = resp.json()
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
                    # A newly added game must not be marked up to date just
                    # because the online lookup succeeded.  Without an
                    # installed build reference there is nothing to compare.
                    self._fail("No installed Steam build reference; enter a current Build ID or date")
                    return
                logger.info(f"Steam Build check for game {self.game_id} (AppID {self.steam_id}): latest={latest_build_id}, update={is_update}")
                if self.isInterruptionRequested():
                    return
                self.update_checked.emit(self.game_id, latest_build_id, latest_build_date, is_update)
                return
            self._fail("Steam returned no public branch build for this AppID")
        except requests.exceptions.RequestException as e:
            # No route / DNS / timeout: an offline machine, not a check that
            # "failed" — the UI must show an offline state, not an error.
            logger.info(f"Offline while checking Steam build for AppID {self.steam_id}: {e}")
            if not self.isInterruptionRequested():
                self.offline_detected.emit(self.game_id)
        except Exception as e:
            logger.warning(f"Failed to check Steam build for AppID {self.steam_id}: {e}")
            self._fail(f"Steam check failed: {e}")
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass

    def _fail(self, reason: str):
        if not self.isInterruptionRequested():
            self.check_failed.emit(self.game_id, reason)
