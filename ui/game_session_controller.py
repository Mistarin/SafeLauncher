"""UI-owned lifecycle adapter for game processes and playtime trackers."""

from __future__ import annotations

import logging
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from core.safe_thread import TaskSupervisor
from core.playtime_tracker import terminate_game_process


class GameSessionController(QObject):
    """Own tracker activation/finalization and cooperative process stop work.

    The core ``LaunchSessionCoordinator`` still owns runner invocation and the
    durable session registration. This UI-side controller owns Qt tracker
    lifetimes and stop-task state, while callbacks keep game-specific UI
    effects (recording, achievements, cloud sync, dialogs) in the window.
    """

    _termination_finished = pyqtSignal(object)

    def __init__(
        self,
        launch_session_coordinator,
        session_manager,
        worker_supervisor,
        *,
        parent,
        on_playtime_recorded: Callable,
        on_playtime_checkpoint: Callable,
        on_playtime_session_recorded: Callable,
        register_worker: Callable,
        on_session_finished: Callable,
        on_stopping_changed: Callable,
        on_stop_requested: Callable,
        on_termination_result: Callable,
        is_closing: Callable[[], bool],
        logger: logging.Logger | None = None,
    ):
        super().__init__(parent)
        self.launch_session_coordinator = launch_session_coordinator
        self.session_manager = session_manager
        self.worker_supervisor = worker_supervisor
        self.on_playtime_recorded = on_playtime_recorded
        self.on_playtime_checkpoint = on_playtime_checkpoint
        self.on_playtime_session_recorded = on_playtime_session_recorded
        self.register_worker = register_worker
        self.on_session_finished = on_session_finished
        self.on_stopping_changed = on_stopping_changed
        self.on_stop_requested = on_stop_requested
        self.on_termination_result = on_termination_result
        self.is_closing = is_closing
        self.logger = logger or logging.getLogger(__name__)
        self.trackers: list[object] = []
        self.stopping_game_ids: set[int] = set()
        self.termination_in_flight: set[int] = set()
        self._finishing_trackers: set[int] = set()
        self.termination_tasks = TaskSupervisor(
            self,
            self.logger,
            worker_registry=worker_supervisor,
        )
        self._termination_finished.connect(self._on_termination_finished)

    def start(self, context):
        """Launch through the core coordinator and activate the tracker."""
        result = self.launch_session_coordinator.start(context)
        if not result.started or result.tracker is None:
            return result

        tracker = result.tracker
        tracker.playtime_recorded.connect(self.on_playtime_recorded)
        tracker.playtime_checkpoint.connect(self.on_playtime_checkpoint)
        tracker.playtime_session_recorded.connect(self.on_playtime_session_recorded)
        tracker.finished.connect(self._on_tracker_finished)
        self.trackers.append(tracker)
        self.register_worker(tracker)
        tracker.start()
        return result

    def _on_tracker_finished(self, *_args) -> None:
        tracker = self.sender()
        if tracker is not None:
            self.finish_tracker(tracker)

    def finish_tracker(self, tracker):
        """Finalize session registration once, then let the window run effects."""
        tracker_key = id(tracker)
        if tracker_key in self._finishing_trackers or tracker not in self.trackers:
            return None
        self._finishing_trackers.add(tracker_key)
        game_id = int(tracker.game_id)
        session = None
        try:
            session = self.launch_session_coordinator.finish_tracker(tracker)
            self.stopping_game_ids.discard(game_id)
            self.trackers.remove(tracker)
            try:
                self.on_stopping_changed(game_id, False)
            except Exception:
                self.logger.exception(
                    "Could not refresh stopped-game presentation for game %s",
                    game_id,
                )
            try:
                self.on_session_finished(tracker, session)
            except Exception:
                self.logger.exception(
                    "Game-specific session cleanup failed for game %s", game_id
                )
        finally:
            # Keep terminal sessions available to UI/diagnostic consumers for
            # the duration of their callback, then release them exactly once.
            self.session_manager.release(game_id)
            self._finishing_trackers.discard(tracker_key)
        return session

    def stop(self, game_id: int) -> None:
        """Ask the sandbox process to stop on a managed worker thread."""
        game_id = int(game_id)
        if game_id in self.termination_in_flight:
            return
        self.stopping_game_ids.add(game_id)
        try:
            self.on_stopping_changed(game_id, True)
        except Exception:
            self.logger.exception(
                "Could not update stopping presentation for game %s", game_id
            )
        self.session_manager.mark_stopping(game_id)
        trackers = tuple(
            tracker
            for tracker in self.launch_session_coordinator.trackers()
            if int(getattr(tracker, "game_id", -1)) == game_id
            and getattr(tracker, "process", None) is not None
        )
        if not trackers:
            self.stopping_game_ids.discard(game_id)
            try:
                self.on_stopping_changed(game_id, False)
            except Exception:
                self.logger.exception(
                    "Could not restore game presentation for game %s", game_id
                )
            return

        self.termination_in_flight.add(game_id)
        if not self.is_closing():
            try:
                self.on_stop_requested(game_id)
            except Exception:
                self.logger.exception(
                    "Could not show stop-request feedback for game %s", game_id
                )

        def terminate_trackers():
            stopped = False
            errors = []
            for tracker in trackers:
                try:
                    stopped = terminate_game_process(
                        tracker.process,
                        sandbox_name=getattr(tracker, "sandbox_name", None),
                    ) or stopped
                except Exception as exc:
                    errors.append(str(exc))
            self._termination_finished.emit((game_id, stopped, "; ".join(errors)))

        try:
            self.termination_tasks.start(f"StopGame-{game_id}", terminate_trackers)
        except Exception as exc:
            self.termination_in_flight.discard(game_id)
            self.stopping_game_ids.discard(game_id)
            try:
                self.on_stopping_changed(game_id, False)
            except Exception:
                self.logger.exception(
                    "Could not restore game presentation for game %s", game_id
                )
            self._report_termination_result(game_id, False, str(exc))

    def _on_termination_finished(self, payload: object) -> None:
        game_id, stopped, error = payload
        game_id = int(game_id)
        self.termination_in_flight.discard(game_id)
        if not stopped:
            self.stopping_game_ids.discard(game_id)
            try:
                self.on_stopping_changed(game_id, False)
            except Exception:
                self.logger.exception(
                    "Could not restore game presentation for game %s", game_id
                )
        self._report_termination_result(game_id, bool(stopped), str(error or ""))

    def _report_termination_result(self, game_id: int, stopped: bool, error: str) -> None:
        try:
            self.on_termination_result(game_id, stopped, error)
        except Exception:
            self.logger.exception(
                "Could not present process-stop result for game %s", game_id
            )

    def shutdown(self, wait_ms: int = 0) -> None:
        self.termination_tasks.shutdown(wait_ms=wait_ms)


__all__ = ["GameSessionController"]
