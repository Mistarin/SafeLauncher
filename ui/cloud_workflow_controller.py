"""Automatic save mutation policy and terminal-result delivery."""

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from core.cloud_models import SyncStatus
from core.cloud_operation_service import CloudOperationTarget
from core.logger import get_logger
from core.request_contracts import RequestPriority, ResourceStatus

logger = get_logger("UI.CloudWorkflow")


class CloudWorkflowController(QObject):
    _upload_done = pyqtSignal(object)
    _restore_done = pyqtSignal(object)

    def __init__(self, *, operation_service, coordinator, games_provider, running_games,
                 accepts_work, network_allowed, is_closing, set_syncing,
                 update_launch_button, show_toast, recheck, parent=None):
        super().__init__(parent)
        self.cloud_operation_service, self.cloud_sync_coordinator = operation_service, coordinator
        self.games_provider, self.running_games = games_provider, running_games
        self.accepts_work, self.network_allowed, self.is_closing = accepts_work, network_allowed, is_closing
        self.set_syncing, self.update_launch_button = set_syncing, update_launch_button
        self.show_toast, self.recheck = show_toast, recheck
        self._cloud_auto_restore_in_flight = {}
        self._cloud_auto_upload_in_flight = {}
        self._cloud_auto_restore_attempts = {}
        self._cloud_auto_upload_attempts = {}
        self._disposed = False
        self._upload_done.connect(self.handle_upload_done)
        self._restore_done.connect(self.handle_restore_done)

    def in_flight(self, game_id):
        return int(game_id) in self._cloud_auto_restore_in_flight or int(game_id) in self._cloud_auto_upload_in_flight

    @staticmethod
    def restore_signature(cloud_stats):
        if cloud_stats is None:
            return (None, 0.0, 0, 0, "")
        return (getattr(cloud_stats, "cloud_version", None),
                round(float(getattr(cloud_stats, "last_modified", 0.0) or 0.0), 3),
                int(getattr(cloud_stats, "size_bytes", 0) or 0),
                int(getattr(cloud_stats, "file_count", 0) or 0),
                str(getattr(cloud_stats, "device_name", "") or ""))

    def _emit_upload(self, payload):
        try:
            self._upload_done.emit(payload)
        except RuntimeError:
            pass

    def _emit_restore(self, payload):
        try:
            self._restore_done.emit(payload)
        except RuntimeError:
            pass

    def dispose(self):
        self._disposed = True

    def maybe_upload(
        self,
        game_id: int,
        status,
        local_stats=None,
        cloud_stats=None,
    ) -> None:
        """Upload a newer local save automatically once the game is stopped."""
        if self._disposed or not self.accepts_work():
            return
        if status != SyncStatus.LOCAL_NEWER:
            return
        if game_id in self.running_games():
            return
        if not self.network_allowed():
            return
        if self.in_flight(game_id):
            return
        game = self.games_provider().get(game_id)
        if not game:
            return

        active_operations = self.cloud_operation_service.active_operations()
        if any(
            record.game_id == int(game_id)
            and (
                str(record.operation).startswith("restore")
                or record.operation in {"exit-sync", "prelaunch", "upload"}
            )
            for record in active_operations
        ):
            return

        game_name = str(game[1] if len(game) > 1 else "")
        game_path = str(game[2] if len(game) > 2 else "")
        steam_id = str(game[6] if len(game) > 6 and game[6] else "")
        signature = (
            self.cloud_sync_coordinator.generation,
            round(float(getattr(local_stats, "last_modified", 0.0) or 0.0), 3),
            int(getattr(local_stats, "size_bytes", 0) or 0),
            int(getattr(local_stats, "file_count", 0) or 0),
            str(getattr(local_stats, "device_name", "") or ""),
        )
        attempts = getattr(self, "_cloud_auto_upload_attempts", {})
        previous = attempts.get(int(game_id))
        attempt_count = previous[1] if previous and previous[0] == signature else 0
        if attempt_count >= 2:
            logger.warning(
                "Automatic cloud upload stopped for game %s after %d attempts "
                "for unchanged local snapshot %s",
                game_id,
                attempt_count,
                signature,
            )
            return
        generation = self.cloud_sync_coordinator.generation
        marker = (generation, "upload")
        attempts[int(game_id)] = (signature, attempt_count + 1)
        setattr(self, "_cloud_auto_upload_attempts", attempts)
        self._cloud_auto_upload_in_flight[int(game_id)] = marker
        self.set_syncing(game_id, local_stats, cloud_stats)
        target = CloudOperationTarget(int(game_id), game_name, game_path, steam_id)
        try:
            handle = self.cloud_operation_service.request_upload(
                target,
                priority=RequestPriority.BACKGROUND,
                generation=generation,
                tag="automatic-cloud-upload",
            )
        except Exception as exc:
            logger.exception("Unable to queue automatic cloud upload for game %s", game_id)
            self._emit_upload({
                "game_id": int(game_id),
                "game_name": game_name,
                "generation": generation,
                "success": False,
                "error": str(exc),
                "guidance": "Check the cloud connection and try again.",
            })
            return

        def _deliver(future):
            from core.cloud_operations import CloudOperationResult
            try:
                resource = future.result()
                result = resource.value if resource.status == ResourceStatus.READY else None
                error = str(resource.error or "") if result is None else ""
            except Exception as exc:
                result = None
                error = str(exc)
            if not isinstance(result, CloudOperationResult):
                self._emit_upload({
                    "game_id": int(game_id),
                    "game_name": game_name,
                    "generation": generation,
                    "success": False,
                    "error": error or "Cloud upload failed.",
                    "guidance": "Check the cloud connection and try again.",
                })
                return
            self._emit_upload({
                "game_id": int(game_id),
                "game_name": game_name,
                "generation": generation,
                "success": bool(result.success),
                "error": str(result.error or ""),
                "guidance": str(result.guidance or ""),
            })

        handle.future.add_done_callback(_deliver)


    @pyqtSlot(object)
    def handle_upload_done(self, payload: object) -> None:
        """Finish an automatic upload and re-derive the authoritative status."""
        if not isinstance(payload, dict):
            return
        game_id = int(payload.get("game_id", 0) or 0)
        if self.is_closing() or self._disposed:
            self._cloud_auto_upload_in_flight.pop(game_id, None)
            return
        marker = self._cloud_auto_upload_in_flight.get(game_id)
        expected = (int(payload.get("generation", -1)), "upload")
        if marker != expected:
            return
        self._cloud_auto_upload_in_flight.pop(game_id, None)
        self.update_launch_button(game_id)
        if not self.cloud_sync_coordinator.accepts(expected[0]):
            return

        game_name = str(payload.get("game_name", "this game"))
        if payload.get("success"):
            self.show_toast(f"Local save synced to cloud for '{game_name}'.")
            self.recheck(
                [game_id],
                "automatic-upload-complete",
                auto_sync=False,
            )
            return

        message = str(payload.get("error") or "Cloud upload failed.")
        guidance = str(payload.get("guidance") or "")
        self.show_toast(
            f"{message} Local save preserved. {guidance}".strip(),
            is_error=True,
        )
        self.recheck([game_id], "automatic-upload-failed")


    def maybe_restore(
        self,
        game_id: int,
        status,
        local_stats=None,
        cloud_stats=None,
    ) -> None:
        """Restore a newly detected cloud save when it is safe to mutate disk."""
        if self._disposed or not self.accepts_work():
            return
        if status not in (SyncStatus.CLOUD_NEWER, SyncStatus.CLOUD_ONLY):
            return
        if game_id in self.running_games():
            return
        if not self.network_allowed():
            return
        if self.in_flight(game_id):
            return
        game = self.games_provider().get(game_id)
        if not game:
            return

        active_operations = self.cloud_operation_service.active_operations()
        if any(
            record.game_id == int(game_id)
            and (
                str(record.operation).startswith("restore")
                or record.operation in {"exit-sync", "prelaunch", "upload"}
            )
            for record in active_operations
        ):
            return

        game_name = str(game[1] if len(game) > 1 else "")
        game_path = str(game[2] if len(game) > 2 else "")
        steam_id = str(game[6] if len(game) > 6 and game[6] else "")
        target_version = getattr(cloud_stats, "cloud_version", None)
        signature = (
            self.cloud_sync_coordinator.generation,
            *self.restore_signature(cloud_stats),
        )
        attempts = getattr(self, "_cloud_auto_restore_attempts", {})
        previous = attempts.get(int(game_id))
        attempt_count = previous[1] if previous and previous[0] == signature else 0
        # Two attempts cover a transient transfer failure.  A successful
        # restore that still reports the same cloud snapshot must never turn
        # into a periodic destructive restore loop.
        if attempt_count >= 2:
            logger.warning(
                "Automatic cloud restore stopped for game %s after %d attempts "
                "for unchanged snapshot %s",
                game_id,
                attempt_count,
                signature,
            )
            return
        generation = self.cloud_sync_coordinator.generation
        marker = (generation, target_version)
        attempts[int(game_id)] = (signature, attempt_count + 1)
        setattr(self, "_cloud_auto_restore_attempts", attempts)
        self._cloud_auto_restore_in_flight[int(game_id)] = marker
        self.set_syncing(game_id, local_stats, cloud_stats)

        target = CloudOperationTarget(int(game_id), game_name, game_path, steam_id)
        try:
            handle = self.cloud_operation_service.request_restore(
                target,
                priority=RequestPriority.BACKGROUND,
                generation=generation,
                tag="automatic-cloud-restore",
                target_version=target_version,
            )
        except Exception as exc:
            logger.exception("Unable to queue automatic cloud restore for game %s", game_id)
            self._emit_restore({
                "game_id": int(game_id),
                "game_name": game_name,
                "generation": generation,
                "target_version": target_version,
                "success": False,
                "error": str(exc),
                "guidance": "Check the cloud connection and try again.",
            })
            return

        def _deliver(future):
            from core.cloud_operations import CloudOperationResult
            try:
                resource = future.result()
                result = resource.value if resource.status == ResourceStatus.READY else None
                error = str(resource.error or "") if result is None else ""
            except Exception as exc:
                result = None
                error = str(exc)
            if not isinstance(result, CloudOperationResult):
                self._emit_restore({
                    "game_id": int(game_id),
                    "game_name": game_name,
                    "generation": generation,
                    "target_version": target_version,
                    "success": False,
                    "error": error or "Cloud restore failed.",
                    "guidance": "Check the cloud connection and try again.",
                })
                return
            self._emit_restore({
                "game_id": int(game_id),
                "game_name": game_name,
                "generation": generation,
                "target_version": target_version,
                "success": bool(result.success),
                "error": str(result.error or ""),
                "guidance": str(result.guidance or ""),
            })

        handle.future.add_done_callback(_deliver)


    @pyqtSlot(object)
    def handle_restore_done(self, payload: object) -> None:
        """Finish an automatic restore on the GUI thread and re-derive status."""
        if not isinstance(payload, dict):
            return
        game_id = int(payload.get("game_id", 0) or 0)
        if self.is_closing() or self._disposed:
            self._cloud_auto_restore_in_flight.pop(game_id, None)
            return
        marker = self._cloud_auto_restore_in_flight.get(game_id)
        expected = (
            int(payload.get("generation", -1)),
            payload.get("target_version"),
        )
        if marker != expected:
            return
        self._cloud_auto_restore_in_flight.pop(game_id, None)
        self.update_launch_button(game_id)
        if not self.cloud_sync_coordinator.accepts(expected[0]):
            return

        game_name = str(payload.get("game_name", "this game"))
        if payload.get("success"):
            self.show_toast(f"Newest cloud save restored for '{game_name}'.")
            self.recheck(
                [game_id],
                "automatic-restore-complete",
                # Re-read and render the authoritative status, but do not
                # immediately feed a non-converged result back into restore.
                # The normal poll path can handle a genuinely new snapshot.
                auto_sync=False,
            )
            return

        message = str(payload.get("error") or "Cloud restore failed.")
        guidance = str(payload.get("guidance") or "")
        self.show_toast(
            f"{message} Local save preserved. {guidance}".strip(),
            is_error=True,
        )
        # Recheck without launching another restore loop after a failure.
        self.recheck([game_id], "automatic-restore-failed")
