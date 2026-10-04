"""UI-thread coordinator for cloud-safe game preflight and launch handoff."""

from __future__ import annotations

import logging
import uuid
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QMessageBox, QProgressDialog

from core.cloud_operation_service import CloudOperationTarget
from core.cloud_models import SyncStatus
from core.request_contracts import RequestPriority, ResourceStatus
from core.disk_utils import format_size
from ui.components.cloud_ui import confirm_restore
from ui.dialogs.save_conflict_dialog import SaveConflictDialog


class PrelaunchController(QObject):
    """Own cloud preflight state, progress, cancellation, and launch handoff.

    It intentionally waits for a cancelled restore worker to reach its terminal
    result before releasing the per-game lock. The game therefore cannot race
    staged restore writes or rollback cleanup.
    """

    _preflight_result = pyqtSignal(object)
    _operation_result = pyqtSignal(object)

    def __init__(
        self,
        cloud_operation_service,
        settings,
        *,
        parent,
        network_allowed: Callable[[], bool],
        continue_launch: Callable[[dict], None],
        show_toast: Callable[..., None],
        update_launch_button: Callable[[int], None],
        open_cloud_center: Callable[[], None],
        refresh_cloud_status: Callable[[int], None],
        is_closing: Callable[[], bool] = lambda: False,
        logger: logging.Logger | None = None,
    ):
        super().__init__(parent)
        self.cloud_operation_service = cloud_operation_service
        self.settings = settings
        self.parent_window = parent
        self.network_allowed = network_allowed
        self.continue_launch = continue_launch
        self.show_toast = show_toast
        self.update_launch_button = update_launch_button
        self.open_cloud_center = open_cloud_center
        self.refresh_cloud_status = refresh_cloud_status
        self.is_closing = is_closing
        self.logger = logger or logging.getLogger(__name__)
        self._pending: dict[int, dict] = {}
        self._preflight_result.connect(self._on_preflight_result)
        self._operation_result.connect(self._on_operation_result)

    def is_pending(self, game_id: int) -> bool:
        return int(game_id) in self._pending

    def cancel_pending_for_shutdown(self) -> None:
        """Cancel cloud work but retain locks until each worker is terminal."""
        for game_id, state in tuple(self._pending.items()):
            token = state.get("token")
            self.cancel(game_id, token)
            ctx = state.get("ctx")
            if ctx:
                self._close_progress(ctx)

    def start(self, context: dict) -> bool:
        """Begin preflight once; offline policy proceeds with local saves."""
        ctx = dict(context)
        game_id = int(ctx["game_id"])
        if self.is_closing():
            return False
        if game_id in self._pending:
            self.show_toast("Launch is already preparing this game's cloud save.")
            return False
        if not self.network_allowed():
            self.show_toast(
                f"Offline mode — launching '{ctx['game_name']}' with local saves."
            )
            self.continue_launch(ctx)
            return True

        token = uuid.uuid4().hex
        ctx["prelaunch_token"] = token
        state = {
            "token": token,
            "ctx": ctx,
            "handle": None,
            "cancel_requested": False,
        }
        self._pending[game_id] = state
        self._show_progress(ctx, f"Checking cloud saves for '{ctx['game_name']}'…")
        target = CloudOperationTarget(
            game_id, ctx["game_name"], ctx["path"], ctx.get("steam_id", "")
        )
        try:
            handle = self.cloud_operation_service.request_prelaunch_resolution(
                target,
                auto_prefer_newer=self.settings.value(
                    "auto_prefer_newer_saves", False, type=bool
                ),
                auto_prefer_local=self.settings.value(
                    "auto_prefer_local_saves", False, type=bool
                ),
                priority=RequestPriority.CRITICAL,
                tag="prelaunch",
            )
        except Exception as exc:
            self.logger.warning(
                "Could not queue prelaunch cloud resolution for game %s: %s",
                game_id,
                exc,
            )
            cancelled = self._state(game_id, token)
            self._finish(ctx)
            if self.is_closing() or (cancelled and cancelled.get("cancel_requested")):
                self.show_toast("Launch cancelled.")
            else:
                self.show_toast(
                    f"Cloud check failed — launching '{ctx['game_name']}' with local saves.",
                    is_error=True,
                )
                self.continue_launch(ctx)
            return True

        state["handle"] = handle
        if state.get("cancel_requested"):
            self._cancel_operation(handle)
        self._refresh_progress(game_id, token)
        handle.future.add_done_callback(
            lambda future, launch_ctx=ctx: self._preflight_result.emit(
                self._build_preflight_payload(launch_ctx, future)
            )
        )
        return True

    def cancel(self, game_id: int, token: str) -> None:
        """Request cooperative cancellation without releasing the launch lock."""
        state = self._state(game_id, token)
        if not state or state.get("cancel_requested"):
            return
        state["cancel_requested"] = True
        progress = state.get("progress")
        if progress is not None:
            progress.setLabelText("Cancelling cloud sync…")
            progress.setCancelButton(None)
            progress.setRange(0, 0)
        handle = state.get("handle")
        if handle is not None:
            self._cancel_operation(handle)

    def _cancel_operation(self, handle) -> None:
        try:
            self.cloud_operation_service.cancel(handle.request_id)
        except Exception:
            self.logger.exception(
                "Could not request cancellation for prelaunch cloud operation %s",
                getattr(handle, "request_id", "unknown"),
            )

    def _state(self, game_id: int, token: str):
        state = self._pending.get(int(game_id))
        if state and state.get("token") == token:
            return state
        return None

    def _show_progress(self, ctx: dict, label: str, handle=None) -> None:
        game_id = int(ctx["game_id"])
        state = self._state(game_id, ctx.get("prelaunch_token"))
        if state is None:
            return
        self._close_progress(ctx)
        state["handle"] = handle
        progress = QProgressDialog(label, None, 0, 100, self.parent_window)
        progress.setWindowTitle("Preparing game launch")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButtonText("Cancel")
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        progress.canceled.connect(
            lambda gid=game_id, token=state["token"]: self.cancel(gid, token)
        )
        if handle is None:
            progress.setRange(0, 0)
        progress.show()
        timer = QTimer(progress)
        timer.setInterval(150)
        timer.timeout.connect(
            lambda gid=game_id, token=state["token"]: self._refresh_progress(gid, token)
        )
        state["progress"] = progress
        state["timer"] = timer
        timer.start()
        self._refresh_progress(game_id, state["token"])
        self.update_launch_button(game_id)

    def _refresh_progress(self, game_id: int, token: str) -> None:
        state = self._state(game_id, token)
        if not state:
            return
        progress = state.get("progress")
        handle = state.get("handle")
        if progress is None or handle is None:
            return
        try:
            record = self.cloud_operation_service.operation(handle.request_id)
            value = getattr(record, "progress", None)
        except Exception:
            value = None
        if value is None:
            if progress.minimum() != 0 or progress.maximum() != 0:
                progress.setRange(0, 0)
        else:
            if progress.minimum() == progress.maximum():
                progress.setRange(0, 100)
            progress.setValue(int(max(0.0, min(1.0, float(value))) * 100))

    def _close_progress(self, ctx: dict) -> None:
        state = self._state(
            int(ctx.get("game_id", -1)), ctx.get("prelaunch_token")
        )
        if not state:
            return
        timer = state.pop("timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        progress = state.pop("progress", None)
        if progress is not None:
            # QProgressDialog.close() emits canceled even for an owned,
            # programmatic teardown. Only a user action may cancel the work.
            progress.blockSignals(True)
            progress.close()
            progress.deleteLater()

    def _finish(self, ctx: dict) -> bool:
        """Retire the matching launch exactly once."""
        game_id = int(ctx.get("game_id", -1))
        state = self._state(game_id, ctx.get("prelaunch_token"))
        if state is None:
            return False
        self._close_progress(ctx)
        self._pending.pop(game_id, None)
        if game_id >= 0:
            self.update_launch_button(game_id)
        return True

    def _proceed(self, ctx: dict) -> None:
        if not self._can_continue(ctx):
            self._finish(ctx)
            return
        if self._finish(ctx):
            self.continue_launch(ctx)

    def _can_continue(self, ctx: dict) -> bool:
        state = self._state(int(ctx.get("game_id", -1)), ctx.get("prelaunch_token"))
        return bool(state and not state.get("cancel_requested") and not self.is_closing())

    @staticmethod
    def _build_preflight_payload(ctx: dict, future) -> dict:
        payload = {"proceed": True, "needs_conflict": False, "toast": "", "ctx": ctx}
        try:
            resource = future.result()
            value = resource.value if resource.status == ResourceStatus.READY else None
            payload["cancelled"] = resource.status == ResourceStatus.CANCELLED
            if resource.status == ResourceStatus.CANCELLED:
                payload["error"] = str(resource.error or "Cloud operation cancelled")
            elif value is None:
                payload["error"] = str(getattr(resource, "error", "") or "Cloud check failed")
        except Exception as exc:
            value = None
            payload["error"] = str(exc)
        if value is None:
            payload["toast"] = (
                f"Cloud check failed — launching '{ctx['game_name']}' with local saves."
            )
            return payload

        status = value.get("status")
        payload.update({
            "local_stats": value.get("local_stats"),
            "cloud_stats": value.get("cloud_stats"),
        })
        if value.get("cloud_error") is not None:
            error = value["cloud_error"]
            payload.update({
                "cloud_error": error,
                "error": error.error,
                "guidance": error.guidance,
                "toast": f"Cloud check failed — launching '{ctx['game_name']}' with local saves.",
            })
        elif status == SyncStatus.CLOUD_AUTH_REQUIRED:
            payload["toast"] = f"Cloud setup required — launching '{ctx['game_name']}' with local saves."
        elif status in (SyncStatus.CLOUD_OFFLINE, SyncStatus.CLOUD_UNAVAILABLE):
            payload["toast"] = f"Cloud not reachable — launching '{ctx['game_name']}' with local saves."
        elif value.get("cloud_result") is not None:
            result = value["cloud_result"]
            payload["cloud_result"] = result
            payload["toast"] = (
                f"Restored cloud save for '{ctx['game_name']}'."
                if result.success
                else f"Cloud restore failed — launching '{ctx['game_name']}' with local saves."
            )
            if not result.success:
                payload.update({"error": result.error, "guidance": result.guidance})
                if result.category == "quota":
                    payload["quota_blocked"] = True
                    payload["quota_details"] = result.payload or {}
        elif value.get("needs_cloud_only_prompt"):
            payload["needs_cloud_only_prompt"] = True
        elif value.get("needs_conflict"):
            payload["needs_conflict"] = True
        elif value.get("local_ready"):
            payload["toast"] = f"Local saves ready for '{ctx['game_name']}'."
        elif value.get("local_preferred"):
            payload["toast"] = f"Local saves preferred for '{ctx['game_name']}'."
        return payload

    @staticmethod
    def _build_operation_payload(ctx: dict, future, success_toast: str, failure_toast: str) -> dict:
        try:
            resource = future.result()
            result = resource.value if resource.status == ResourceStatus.READY else None
            cancelled = resource.status == ResourceStatus.CANCELLED
            error = str(getattr(resource, "error", "") or "")
        except Exception as exc:
            result = None
            error = str(exc)
            cancelled = False
        payload = {
            "ctx": ctx,
            "ok": bool(result is not None and result.success),
            "cloud_result": result,
            "cancelled": cancelled,
            "toast": success_toast if result is not None and result.success else failure_toast,
        }
        if result is None:
            payload.update({
                "error": error or "Cloud operation failed.",
                "guidance": "Check the cloud connection and try again.",
            })
        elif not result.success:
            payload.update({"error": result.error, "guidance": result.guidance})
        return payload

    def _queue_cloud_operation(
        self,
        ctx: dict,
        operation: str,
        success_toast: str,
        failure_toast: str,
        target_version=None,
    ) -> None:
        if not self._can_continue(ctx):
            self._finish(ctx)
            return
        target = CloudOperationTarget(
            ctx["game_id"], ctx["game_name"], ctx["path"], ctx.get("steam_id", "")
        )
        try:
            if operation == "restore":
                handle = self.cloud_operation_service.request_restore(
                    target,
                    priority=RequestPriority.CRITICAL,
                    tag="prelaunch_conflict",
                    target_version=target_version,
                )
            elif operation == "upload":
                handle = self.cloud_operation_service.request_upload(
                    target,
                    priority=RequestPriority.CRITICAL,
                    tag="prelaunch_conflict",
                )
            else:
                raise ValueError(f"Unsupported prelaunch cloud operation: {operation}")
        except Exception as exc:
            self.logger.warning(
                "Could not queue prelaunch %s for game %s: %s",
                operation,
                ctx["game_id"],
                exc,
            )
            self.show_toast(f"{failure_toast} ({exc})", is_error=True)
            self._proceed(ctx)
            return

        label = "Restoring cloud save…" if operation == "restore" else "Uploading local save…"
        self._show_progress(ctx, label, handle)
        handle.future.add_done_callback(
            lambda future, launch_ctx=ctx, success=success_toast, failure=failure_toast:
            self._operation_result.emit(
                self._build_operation_payload(launch_ctx, future, success, failure)
            )
        )

    def _on_preflight_result(self, payload: dict) -> None:
        ctx = payload.get("ctx", {})
        state = self._state(int(ctx.get("game_id", -1)), ctx.get("prelaunch_token"))
        if state is None:
            self.logger.debug("Ignoring stale prelaunch result for game %s", ctx.get("game_id"))
            return
        self._close_progress(ctx)
        if self.is_closing():
            self._finish(ctx)
            return
        if state.get("cancel_requested") or payload.get("cancelled"):
            self._finish(ctx)
            self.show_toast(
                f"Launch cancelled — cloud sync for '{ctx.get('game_name', 'game')}' was stopped."
            )
            return

        game_name = ctx.get("game_name", "")
        if payload.get("quota_blocked"):
            details = payload.get("quota_details") or {}
            requested = format_size(int(details.get("requested_bytes", 0) or 0))
            available = format_size(int(details.get("available_bytes", 0) or 0))
            quota = format_size(int(details.get("quota_bytes", 0) or 0))
            dialog = QMessageBox(self.parent_window)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle("Cloud storage limit reached")
            dialog.setText(f"The save for '{game_name}' cannot be uploaded right now.")
            dialog.setInformativeText(
                f"Save size: {requested}\nAvailable cloud space: {available}\n"
                f"Account quota: {quota}\n\nYour local save was not changed."
            )
            launch_button = dialog.addButton(
                "Launch with local saves", QMessageBox.ButtonRole.AcceptRole
            )
            dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            cloud_button = dialog.addButton(
                "Open Cloud Center", QMessageBox.ButtonRole.ActionRole
            )
            dialog.exec()
            if not self._can_continue(ctx):
                self._finish(ctx)
                return
            clicked = dialog.clickedButton()
            if clicked is launch_button:
                self._proceed(ctx)
            elif clicked is cloud_button:
                self._finish(ctx)
                self.open_cloud_center()
            else:
                self._finish(ctx)
                self.show_toast(
                    f"Launch cancelled — cloud storage is insufficient for '{game_name}'."
                )
            return

        if payload.get("needs_conflict"):
            dialog = SaveConflictDialog(
                game_name, payload["local_stats"], payload["cloud_stats"],
                parent=self.parent_window,
            )
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            if not self._can_continue(ctx):
                self._finish(ctx)
                return
            if not accepted:
                self.show_toast(
                    f"Launch cancelled — resolve the save conflict for '{game_name}' first."
                )
                self._finish(ctx)
                return
            if dialog.always_newer:
                key = (
                    "auto_prefer_newer_saves"
                    if dialog.choice == "cloud"
                    else "auto_prefer_local_saves"
                )
                self.settings.setValue(key, True)
            if dialog.choice == "cloud":
                self._queue_cloud_operation(
                    ctx,
                    "restore",
                    f"Restored cloud save for '{game_name}' — your previous save was kept as a local backup.",
                    f"Could not restore the cloud save for '{game_name}' — launched with local saves.",
                    target_version=getattr(payload.get("cloud_stats"), "cloud_version", None),
                )
                return
            self._queue_cloud_operation(
                ctx,
                "upload",
                "Overwrote cloud save with local version.",
                "Could not upload the local save — launching with local state.",
            )
            return

        if payload.get("needs_cloud_only_prompt"):
            cloud_stats = payload.get("cloud_stats")
            should_restore = confirm_restore(
                self.parent_window,
                game_name=game_name,
                target_path=ctx.get("path", ""),
                technical_details=(
                    f"Cloud save: {getattr(cloud_stats, 'display_path', 'Latest cloud save version')}"
                ),
                title="Restore latest cloud save",
            )
            if not self._can_continue(ctx):
                self._finish(ctx)
                return
            if should_restore:
                self._queue_cloud_operation(
                    ctx,
                    "restore",
                    f"Restored cloud save for '{game_name}'.",
                    f"Failed to restore cloud save for '{game_name}'.",
                    target_version=getattr(cloud_stats, "cloud_version", None),
                )
                return
            self.show_toast(f"Launching '{game_name}' without restoring cloud save.")
        elif payload.get("toast"):
            toast = payload["toast"]
            if payload.get("guidance"):
                toast = f"{toast} {payload['guidance']}"
            self.show_toast(toast, is_error=bool(payload.get("error")))
        self._proceed(ctx)

    def _on_operation_result(self, result: dict) -> None:
        ctx = result.get("ctx", {})
        state = self._state(int(ctx.get("game_id", -1)), ctx.get("prelaunch_token"))
        if state is None:
            self.logger.debug(
                "Ignoring stale prelaunch cloud-operation result for game %s",
                ctx.get("game_id"),
            )
            return
        self._close_progress(ctx)
        if self.is_closing():
            self._finish(ctx)
            return
        if state.get("cancel_requested") or result.get("cancelled"):
            self._finish(ctx)
            self.show_toast(
                f"Launch cancelled — cloud sync for '{ctx.get('game_name', 'game')}' was stopped."
            )
            return
        toast = result.get("toast", "")
        if result.get("guidance"):
            toast = f"{toast} {result['guidance']}".strip()
        if toast:
            self.show_toast(toast, is_error=bool(result.get("error")))
        if result.get("ok"):
            self.refresh_cloud_status(int(ctx["game_id"]))
        self._proceed(ctx)


__all__ = ["PrelaunchController"]
