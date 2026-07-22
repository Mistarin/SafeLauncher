import urllib.request
import json
from PyQt6.QtCore import QThread, pyqtSignal


class SteamBuildFetcher(QThread):
    """Background QThread to query SteamCMD API for latest game buildid and check for updates."""
    update_checked = pyqtSignal(int, str, bool)  # game_id, latest_build_id, is_update_available

    def __init__(self, game_id: int, steam_id: str, local_build_id: str = "", parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.steam_id = str(steam_id).strip()
        self.local_build_id = str(local_build_id).strip()

    def run(self):
        if not self.steam_id or self.steam_id == "0":
            self.update_checked.emit(self.game_id, "", False)
            return

        try:
            url = f"https://api.steamcmd.net/v1/info/{self.steam_id}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "MGLauncher/1.0 (Linux Game Sandbox Manager)"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    app_data = data.get("data", {}).get(self.steam_id, {})
                    depots = app_data.get("depots", {})
                    branches = depots.get("branches", {})
                    public_branch = branches.get("public", {})
                    latest_build_id = str(public_branch.get("buildid", "")).strip()

                    if latest_build_id:
                        is_update = bool(self.local_build_id and latest_build_id != self.local_build_id)
                        self.update_checked.emit(self.game_id, latest_build_id, is_update)
                        return
        except Exception:
            pass

        self.update_checked.emit(self.game_id, "", False)
