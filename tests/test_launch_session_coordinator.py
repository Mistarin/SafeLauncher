"""Unit tests for process/session registration outside MainWindow."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from core.launch_session_coordinator import (
    LaunchSessionContext,
    LaunchSessionCoordinator,
)


class _Process:
    pid = 1234

    def __init__(self, return_code=None):
        self.return_code = return_code

    def poll(self):
        return self.return_code


class _Runner:
    def __init__(self):
        self.proton = None
        self.calls = []
        self.process = _Process()

    def set_proton_path(self, value):
        self.proton = value

    def launch(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.process


class _Database:
    def __init__(self):
        self.calls = []

    def create_playtime_session(self, game_id, started_at):
        self.calls.append((game_id, started_at))
        return "session-1"


class _SessionManager:
    def __init__(self):
        self.sessions = {}
        self.calls = []

    def get(self, game_id):
        return self.sessions.get(game_id)

    def start(self, game_id, game_name, process, session_id=""):
        session = SimpleNamespace(
            game_id=game_id,
            game_name=game_name,
            process=process,
            session_id=session_id,
            is_live=True,
        )
        self.sessions[game_id] = session
        self.calls.append(("start", game_id, session_id))
        return session

    def attach_tracker(self, game_id, tracker):
        self.sessions[game_id].tracker = tracker

    def finish(self, game_id, exit_code=None):
        self.calls.append(("finish", game_id, exit_code))


class LaunchSessionCoordinatorTests(unittest.TestCase):
    def _coordinator(self):
        runner = _Runner()
        database = _Database()
        sessions = _SessionManager()
        trackers = []

        def tracker_factory(game_id, process, session_id):
            tracker = SimpleNamespace(game_id=game_id, process=process, session_id=session_id)
            trackers.append(tracker)
            return tracker

        coordinator = LaunchSessionCoordinator(runner, database, sessions, tracker_factory)
        return coordinator, runner, database, sessions, trackers

    def test_start_registers_process_session_and_tracker(self):
        coordinator, runner, database, sessions, trackers = self._coordinator()
        result = coordinator.start(
            LaunchSessionContext(
                7, "Example", "/games/example", "run.sh", "umu",
                steam_id="480", proton_path="/proton", env_vars={"A": "B"},
            )
        )

        self.assertTrue(result.started)
        self.assertFalse(result.already_running)
        self.assertEqual(result.session_id, "session-1")
        self.assertIs(result.tracker, trackers[0])
        self.assertEqual(runner.proton, "/proton")
        self.assertEqual(runner.calls[0][1]["env_vars"], {"A": "B"})
        self.assertEqual(sessions.calls, [("start", 7, "session-1")])
        self.assertIs(coordinator.tracker(7), trackers[0])

    def test_running_game_is_not_launched_twice(self):
        coordinator, runner, _database, _sessions, _trackers = self._coordinator()
        context = LaunchSessionContext(8, "Example", "/games/example", "run.sh", "umu")
        first = coordinator.start(context)
        second = coordinator.start(context)

        self.assertTrue(first.started)
        self.assertFalse(second.started)
        self.assertTrue(second.already_running)
        self.assertEqual(len(runner.calls), 1)

    def test_finish_tracker_closes_session_registration(self):
        coordinator, _runner, _database, sessions, _trackers = self._coordinator()
        result = coordinator.start(
            LaunchSessionContext(9, "Example", "/games/example", "run.sh", "umu")
        )
        result.process.return_code = 17
        session = coordinator.finish_tracker(result.tracker)

        self.assertIsNotNone(session)
        self.assertIsNone(coordinator.tracker(9))
        self.assertEqual(sessions.calls[-1], ("finish", 9, 17))


if __name__ == "__main__":
    unittest.main()
