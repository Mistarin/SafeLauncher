from PyQt6.QtCore import pyqtSignal
from core.safe_thread import SafeQThread
from core.logger import get_logger
from core.network_policy import automatic_network_allowed
from core.steam_client import SteamClient

logger = get_logger("SteamTags")


class SteamTagsFetcher(SafeQThread):
    """Background worker thread to auto-fetch Steam genres and category tags for a game."""
    tags_found = pyqtSignal(int, list, str)  # game_id, tags_list, steam_app_id

    def __init__(self, game_id: int, game_name: str, parent=None, request_manager=None, steam_client=None):
        super().__init__(parent)
        self.game_id = game_id
        self.game_name = game_name
        self.request_manager = request_manager
        self.steam_client = steam_client

    def _emit_if_active(self, tags: list, app_id: str = "") -> None:
        """Never deliver a late result after cooperative cancellation."""
        if not self.isInterruptionRequested():
            self.tags_found.emit(self.game_id, tags, app_id)

    def safe_run(self):
        if self.isInterruptionRequested():
            return
        if self.request_manager is not None:
            from core.request_contracts import RequestKey, RequestPriority, ResourceStatus
            client = self.steam_client or SteamClient()
            identity = self.game_name.strip().casefold() or f"game-{self.game_id}"
            key = RequestKey("steam-tags", identity)
            loader = lambda token: (token.raise_if_cancelled(), client.tags_for_game(self.game_name))[1]
            if getattr(self.request_manager, "cache", None) is not None:
                handle = self.request_manager.request_cached(
                    key,
                    loader,
                    max_age_seconds=7 * 24 * 60 * 60,
                    priority=RequestPriority.NORMAL,
                    timeout_seconds=10,
                )
            else:
                handle = self.request_manager.request(
                    key,
                    loader,
                    priority=RequestPriority.NORMAL,
                    timeout_seconds=10,
                )
            result = handle.future.result()
            usable = result
            if result.status != ResourceStatus.READY:
                cached_state = self.request_manager.state(key)
                if cached_state.status == ResourceStatus.STALE:
                    usable = cached_state
            if usable.status in {ResourceStatus.READY, ResourceStatus.STALE} and usable.value and not self.isInterruptionRequested():
                tags, app_id = usable.value
                self._emit_if_active(tags, str(app_id))
            elif result.status == ResourceStatus.ERROR:
                logger.debug("Managed Steam tag request failed for %s: %s", self.game_name, result.error)
                self._emit_if_active([], "")
            if self.steam_client is None:
                client.close()
            return
        if not automatic_network_allowed():
            return
        self._safe_run_direct(None)

    def _safe_run_direct(self, token=None):
        client = self.steam_client or SteamClient()
        try:
            if (token and token.cancelled) or self.isInterruptionRequested():
                return
            combined, app_id = client.tags_for_game(self.game_name)

            if (token and token.cancelled) or self.isInterruptionRequested():
                return
            logger.info(f"Fetched Steam tags for '{self.game_name}': {combined}")
            self._emit_if_active(combined, str(app_id))
        except Exception as e:
            logger.warning(f"Failed to fetch Steam tags for '{self.game_name}': {e}")
            self._emit_if_active([], "")
        finally:
            if self.steam_client is None:
                client.close()
