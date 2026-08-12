import urllib.request
import urllib.parse
import json
from PyQt6.QtCore import pyqtSignal
from core.safe_thread import SafeQThread
from core.logger import get_logger

logger = get_logger("SteamTags")


class SteamTagsFetcher(SafeQThread):
    """Background worker thread to auto-fetch Steam genres and category tags for a game."""
    tags_found = pyqtSignal(int, list, str)  # game_id, tags_list, steam_app_id

    def __init__(self, game_id: int, game_name: str, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.game_name = game_name

    def safe_run(self):
        try:
            if self.isInterruptionRequested():
                return
            query = urllib.parse.quote(self.game_name)
            search_url = f"https://store.steampowered.com/api/storesearch/?term={query}&l=english&cc=US"
            req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                search_data = json.loads(response.read().decode("utf-8"))

            if self.isInterruptionRequested():
                return

            items = search_data.get("items", [])
            if not items:
                self.tags_found.emit(self.game_id, [], "")
                return

            app_id = items[0]["id"]
            detail_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}"
            req_detail = urllib.request.Request(detail_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req_detail, timeout=5) as response:
                detail_data = json.loads(response.read().decode("utf-8"))

            if self.isInterruptionRequested():
                return

            app_data = detail_data.get(str(app_id), {}).get("data", {})
            if not app_data:
                self.tags_found.emit(self.game_id, [], str(app_id))
                return

            genres = [g["description"] for g in app_data.get("genres", [])]
            categories = [c["description"] for c in app_data.get("categories", [])]

            combined = []
            for t in genres + categories:
                if t not in combined and len(combined) < 4:
                    combined.append(t)

            logger.info(f"Fetched Steam tags for '{self.game_name}': {combined}")
            self.tags_found.emit(self.game_id, combined, str(app_id))
        except Exception as e:
            logger.warning(f"Could not fetch Steam tags for '{self.game_name}': {e}")
            self.tags_found.emit(self.game_id, [], "")
