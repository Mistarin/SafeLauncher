from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from core.launch_session_coordinator import LaunchSessionResult
from ui.game_session_controller import GameSessionController


class _Tracker(QObject):
    playtime_recorded = pyqtSignal(int, int)
    playtime_checkpoint = pyqtSignal(str, int)
    playtime_session_recorded = pyqtSignal(str, int, int, bool)
    finished = pyqtSignal()

    def __init__(self, game_id=7, process=None):
        super().__init__()
        self.game_id = game_id
        self.process = process or SimpleNamespace(poll=lambda: None)
        self.started = False

    def start(self):
        self.started = True


class _DeferredTasks:
    def __init__(self, *_args, **_kwargs):
        self.tasks = []

    def start(self, name, work):
        self.tasks.append((name, work))

    def shutdown(self, **_kwargs):
        pass


class GameSessionControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_controller(self, *, coordinator=None, sessions=None, **callbacks):
        coordinator = coordinator or Mock()
        sessions = sessions or Mock()
        defaults = {
            "on_playtime_recorded": Mock(),
            "on_playtime_checkpoint": Mock(),
            "on_playtime_session_recorded": Mock(),
            "register_worker": Mock(),
            "on_session_finished": Mock(),
            "on_stopping_changed": Mock(),
            "on_stop_requested": Mock(),
            "on_termination_result": Mock(),
            "is_closing": lambda: False,
        }
        defaults.update(callbacks)
        controller = GameSessionController(
            coordinator,
            sessions,
            Mock(),
            parent=self.app.activeWindow(),
            logger=Mock(),
            **defaults,
        )
        self.addCleanup(controller.deleteLater)
        return controller, coordinator, sessions, defaults

    def test_start_activates_and_supervises_tracker_before_return(self):
        tracker = _Tracker()
        result = LaunchSessionResult(started=True, process=tracker.process, tracker=tracker)
        coordinator = Mock(start=Mock(return_value=result))
        controller, _coordinator, _sessions, callbacks = self.make_controller(
            coordinator=coordinator
        )

        self.assertIs(controller.start(object()), result)

        self.assertTrue(tracker.started)
        self.assertEqual(controller.trackers, [tracker])
        callbacks["register_worker"].assert_called_once_with(tracker)
        coordinator.start.assert_called_once()

    def test_tracker_completion_finalizes_once_and_releases_after_ui_callback(self):
        tracker = _Tracker()
        session = SimpleNamespace(game_id=7)
        coordinator = Mock()
        coordinator.start.return_value = LaunchSessionResult(
            started=True, process=tracker.process, tracker=tracker
        )
        coordinator.finish_tracker.return_value = session
        release_order = []
        sessions = Mock()
        sessions.release.side_effect = lambda game_id: release_order.append(("release", game_id))
        on_finished = Mock(side_effect=lambda *_args: release_order.append(("ui", 7)))
        controller, _coordinator, _sessions, _callbacks = self.make_controller(
            coordinator=coordinator,
            sessions=sessions,
            on_session_finished=on_finished,
        )
        controller.start(object())

        tracker.finished.emit()
        self.app.processEvents()
        tracker.finished.emit()
        self.app.processEvents()

        self.assertEqual(release_order, [("ui", 7), ("release", 7)])
        self.assertEqual(controller.trackers, [])
        self.assertEqual(coordinator.finish_tracker.call_count, 1)

    def test_stop_schedules_process_termination_off_the_calling_thread(self):
        tracker = SimpleNamespace(
            game_id=42,
            process=object(),
            sandbox_name="safe-42",
        )
        coordinator = SimpleNamespace(trackers=lambda: [tracker])
        sessions = SimpleNamespace(mark_stopping=Mock(), release=Mock())
        controller, _coordinator, _sessions, callbacks = self.make_controller(
            coordinator=coordinator,
            sessions=sessions,
        )
        with patch("ui.game_session_controller.TaskSupervisor", _DeferredTasks), patch(
            "ui.game_session_controller.terminate_game_process"
        ) as terminate:
            controller.termination_tasks = _DeferredTasks()
            controller.stop(42)

            self.assertEqual(len(controller.termination_tasks.tasks), 1)
            name, work = controller.termination_tasks.tasks[0]
            self.assertEqual(name, "StopGame-42")
            terminate.assert_not_called()
            self.assertIn(42, controller.stopping_game_ids)
            callbacks["on_stop_requested"].assert_called_once_with(42)
            sessions.mark_stopping.assert_called_once_with(42)

            terminate.return_value = True
            work()
            terminate.assert_called_once_with(tracker.process, sandbox_name="safe-42")

    def test_failed_stop_releases_stopping_state_and_reports_error(self):
        tracker = SimpleNamespace(game_id=9, process=object(), sandbox_name="")
        controller, _coordinator, _sessions, callbacks = self.make_controller(
            coordinator=SimpleNamespace(trackers=lambda: [tracker]),
            sessions=SimpleNamespace(mark_stopping=Mock()),
        )
        with patch("ui.game_session_controller.TaskSupervisor", _DeferredTasks), patch(
            "ui.game_session_controller.terminate_game_process",
            side_effect=RuntimeError("permission denied"),
        ):
            controller.termination_tasks = _DeferredTasks()
            controller.stop(9)
            _name, work = controller.termination_tasks.tasks[0]
            work()
            self.app.processEvents()

        self.assertNotIn(9, controller.stopping_game_ids)
        self.assertNotIn(9, controller.termination_in_flight)
        callbacks["on_termination_result"].assert_called_once_with(
            9, False, "permission denied"
        )


if __name__ == "__main__":
    unittest.main()
