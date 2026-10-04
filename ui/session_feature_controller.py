"""Session feature hooks with failure-isolated optional integrations."""

import time

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from core.cloud_metadata_service import CloudMetadataTarget
from core.cloud_operation_service import CloudOperationTarget
from core.logger import get_logger
from core.request_contracts import RequestPriority


logger = get_logger("SessionFeatures")


class SessionFeatureController(QObject):
    """Session/process state remains exclusively in GameSessionController."""

    _exit_ready = pyqtSignal(object)

    def __init__(self, *, achievements, library_service, metadata_service,
                 exit_sync_service, games_provider, db_path, running_games,
                 rpc_provider, recorder_config, recorder_provider, notify,
                 network_allowed, on_playtime_changed, on_exit_result, parent=None):
        super().__init__(parent)
        self.achievements = achievements
        self.library_service, self.metadata_service = library_service, metadata_service
        self.exit_sync_service, self.games_provider = exit_sync_service, games_provider
        self.db_path, self.running_games = db_path, running_games
        self.rpc_provider, self.recorder_config = rpc_provider, recorder_config
        self.recorder_provider, self.notify = recorder_provider, notify
        self.network_allowed = network_allowed
        self.on_playtime_changed, self.on_exit_result = on_playtime_changed, on_exit_result
        self._closed = False
        self._exit_ready.connect(self._deliver_exit)

    @staticmethod
    def _optional(name, work):
        try:
            work()
        except Exception:
            logger.exception("Optional session integration failed: %s", name)

    def started(self, context):
        if self._closed:
            return
        self._optional("Discord presence", lambda: self._start_presence(context["game_name"]))
        self._optional("GPU recorder", lambda: self._start_recording(context["game_name"]))
        self._optional("achievement watcher", lambda: self.achievements.start_watching(
            context["game_id"], str(context.get("steam_id") or "").strip(),
            context.get("selected_proton") or "", context.get("path") or "",
        ))

    def _start_presence(self, game_name):
        rpc = self.rpc_provider()
        if rpc is not None:
            rpc.set_activity(game_name, start_timestamp=int(time.time()), details="Playing in Sandbox")

    def _start_recording(self, game_name):
        config = self.recorder_config()
        if config is None or not config.enabled or config.mode not in ("replay_buffer", "auto_game"):
            return
        recorder = self.recorder_provider()
        if not recorder.is_running():
            replay = config.mode == "replay_buffer"
            recorder.start_recording(game_name, is_replay=replay)
            if replay:
                self.notify("Replay Buffer Active", f"{config.capture_hotkey} → save clip",
                            icon_type="replay", enabled=config.in_game_overlay, target_screen=config.target_screen)

    def finished(self, game_id):
        if self._closed:
            return
        self._optional("achievement exit reconciliation", lambda: self.achievements.finish_session(game_id))
        if not self.running_games():
            self._optional("Discord cleanup", self._clear_presence)
            self._optional("GPU recorder standby", self._stop_recording)
        if self.network_allowed():
            self._optional("exit cloud synchronization", lambda: self._sync_exit(game_id))

    def _clear_presence(self):
        rpc = self.rpc_provider()
        if rpc is not None:
            rpc.clear_activity()

    def _stop_recording(self):
        config = self.recorder_config()
        if config is not None and config.enabled and config.mode in ("replay_buffer", "auto_game"):
            recorder = self.recorder_provider()
            if recorder.is_running():
                recorder.stop_recording()

    def _sync_exit(self, game_id):
        game = self.games_provider().get(game_id)
        if game is None:
            return
        target = CloudOperationTarget(game_id, game[1], game[2], str(game[6] or "").strip())
        handle = self.exit_sync_service.request(
            target, settle_seconds=0.5, priority=RequestPriority.BACKGROUND, tag="game_exit",
        )
        handle.future.add_done_callback(lambda future: self._emit_exit(target, future))

    def _emit_exit(self, target, future):
        try:
            self._exit_ready.emit(self.exit_sync_service.resolve(target, future))
        except RuntimeError:
            pass

    @pyqtSlot(object)
    def _deliver_exit(self, result):
        if not self._closed:
            self.on_exit_result(result)

    def playtime_recorded(self, game_id):
        total = self.library_service.record_playtime_finished(game_id)
        self.on_playtime_changed(game_id, total)

    def checkpoint(self, session_id, elapsed_seconds):
        self.library_service.checkpoint_playtime_session(session_id, elapsed_seconds)

    def session_recorded(self, session_id, elapsed_seconds, ended_at, finalized):
        game_id = self.library_service.checkpoint_playtime_session(
            session_id, elapsed_seconds, finalized=finalized, ended_at=ended_at,
        )
        if game_id is not None:
            self.sync_metadata(game_id)

    def sync_metadata(self, game_id):
        if self._closed:
            return None
        game = self.games_provider().get(game_id)
        if game is None:
            return None
        target = CloudMetadataTarget(game_id, game[1], str(game[6] or "").strip())
        return self.metadata_service.request_latest_game(
            target, self.db_path, priority=RequestPriority.BACKGROUND, tag="game_metadata",
        )

    def dispose(self):
        self._closed = True
        self._optional("Discord cleanup", self._clear_presence)
        self._optional("GPU recorder cleanup", lambda: self.recorder_provider().stop_recording())
