"""Confirmed cloud restore ownership, independent of library selection."""
from dataclasses import dataclass
from uuid import uuid4

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from core.cloud_operation_service import CloudOperationTarget
from core.request_contracts import RequestPriority, ResourceStatus


@dataclass
class _Restore:
    token: str
    target: CloudOperationTarget
    generation: int
    handle: object = None
    cancelled: bool = False


class ManualRestoreController(QObject):
    completed = pyqtSignal(object)

    def __init__(self, *, service, generation, accepts_work, can_restore,
                 progress, close_progress, confirm, notify, no_save,
                 state_changed, recheck, parent=None):
        super().__init__(parent)
        self.service, self.generation = service, generation
        self.accepts_work, self.can_restore = accepts_work, can_restore
        self.progress, self.close_progress, self.confirm = progress, close_progress, confirm
        self.notify, self.no_save = notify, no_save
        self.state_changed, self.recheck = state_changed, recheck
        self._active = None
        self._disposed = False
        self.completed.connect(self.deliver)

    def is_pending(self, game_id):
        return self._active is not None and self._active.target.game_id == int(game_id)

    def start(self, target):
        if self._disposed or not self.accepts_work() or self._active is not None:
            return False
        if not self.can_restore(target):
            self.notify("Stop the game and wait for its save operation before restoring.", is_error=True)
            return False
        state = _Restore(uuid4().hex, target, self.generation())
        self._active = state
        self.state_changed(target.game_id, True)
        self.progress(f"Checking cloud save for '{target.game_name}'…", self.cancel)
        self._submit(state, "preflight", lambda: self.service.request_restore_preflight(
            target, priority=RequestPriority.CRITICAL, tag="manual_restore"))
        return True

    def _submit(self, state, phase, submit):
        try:
            state.handle = submit()
        except Exception as exc:
            self.deliver((state.token, phase, None, str(exc)))
            return

        def done(future):
            try:
                result = future.result()
                value = result.value if result.status == ResourceStatus.READY else None
                error = str(result.error or result.status.value) if value is None else ""
            except Exception as exc:
                value, error = None, str(exc)
            try:
                self.completed.emit((state.token, phase, value, error))
            except RuntimeError:
                pass  # Native QObject was destroyed after shutdown drained work.

        state.handle.future.add_done_callback(done)

    def cancel(self):
        state = self._active
        if state is None:
            return
        state.cancelled = True
        if state.handle is not None:
            state.handle.cancel()
        # Keep the game locked until the future completes and rollback finishes.

    def dispose(self):
        self._disposed = True
        self.cancel()
        self.close_progress()

    def _finish(self, state):
        self._active = None
        self.close_progress()
        self.state_changed(state.target.game_id, False)

    @pyqtSlot(object)
    def deliver(self, payload):
        token, phase, value, error = payload
        state = self._active
        if state is None or state.token != token:
            return
        if (state.cancelled or self._disposed or not self.accepts_work()
                or state.generation != self.generation()):
            self._finish(state)
            return
        target = state.target
        self.close_progress()
        if phase == "preflight":
            if not isinstance(value, dict) or value.get("kind") == "error":
                detail = value.get("error") if isinstance(value, dict) else error
                self._finish(state)
                self.notify(f"{getattr(detail, 'error', None) or detail or 'Could not check the cloud save.'} "
                            f"{getattr(detail, 'guidance', '')}".strip(), is_error=True)
                return
            if value.get("kind") not in {"ready", "history"} or not value.get("cloud_exists", True):
                self._finish(state)
                self.no_save(target.game_name)
                return
            if not self.confirm(target, value):
                self._finish(state)
                return
            # Modal confirmation can process events: revalidate after it returns.
            if (state.cancelled or self._disposed or not self.accepts_work()
                    or state.generation != self.generation() or not self.can_restore(target)):
                self._finish(state)
                return
            entry = value.get("history_entry") or {}
            try:
                version = int(entry["version"]) if entry.get("version") is not None else None
            except (ValueError, TypeError):
                version = None
            self.progress(f"Restoring latest cloud save for '{target.game_name}'…", self.cancel)
            self._submit(state, "restore", lambda: self.service.request_restore(
                target, priority=RequestPriority.CRITICAL, tag="manual_restore",
                target_version=version, restore_plan=value.get("restore_plan")))
            return
        self._finish(state)
        if getattr(value, "category", "") == "cloud_missing":
            self.no_save(target.game_name)
        elif getattr(value, "success", False):
            self.notify(f"Successfully restored cloud save for '{target.game_name}'.")
            self.recheck([target.game_id], "manual_restore")
        else:
            self.notify(f"{getattr(value, 'error', '') or error or 'Cloud restore failed.'} "
                        f"{getattr(value, 'guidance', '')}".strip(), is_error=True)
