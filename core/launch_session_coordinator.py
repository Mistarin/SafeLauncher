"""Application service for starting and registering game sessions.

The coordinator deliberately stops at the session boundary.  UI concerns such
as cloud conflict dialogs, achievement watcher presentation, Discord activity,
and recorder notifications remain in the composition root.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable


@dataclass(frozen=True, slots=True)
class LaunchSessionContext:
    game_id: int
    game_name: str
    path: str
    executable: str
    mode: str
    steam_id: str = ""
    proton_path: str = ""
    sandbox: bool = True
    env_vars: dict | None = None


@dataclass(frozen=True, slots=True)
class LaunchSessionResult:
    """Result of one process/session registration attempt."""

    started: bool
    already_running: bool = False
    process: object | None = None
    session: object | None = None
    tracker: object | None = None
    session_id: str = ""


class LaunchSessionCoordinator:
    """Own process-to-session registration without owning UI presentation."""

    def __init__(self, runner, session_store, session_manager, tracker_factory: Callable):
        self.runner = runner
        # ``session_store`` is normally LibraryService. A small database-like
        # object is still accepted for compatibility with focused hosts/tests;
        # both expose the same create_playtime_session contract.
        self.session_store = session_store
        self.session_manager = session_manager
        self.tracker_factory = tracker_factory
        self._trackers: dict[int, object] = {}

    def start(self, context: LaunchSessionContext) -> LaunchSessionResult:
        existing = self.session_manager.get(int(context.game_id))
        if existing is not None and existing.is_live:
            return LaunchSessionResult(started=False, already_running=True)

        if hasattr(self.runner, "set_proton_path"):
            self.runner.set_proton_path(context.proton_path)
        process = self.runner.launch(
            context.path,
            context.executable,
            context.mode,
            context.steam_id,
            sandbox=context.sandbox,
            env_vars=dict(context.env_vars or {}),
        )
        if not process:
            return LaunchSessionResult(started=False)

        session_id = self.session_store.create_playtime_session(
            int(context.game_id), started_at=int(time.time())
        )
        session = self.session_manager.start(
            int(context.game_id), context.game_name, process, session_id=session_id
        )
        tracker = self.tracker_factory(
            int(context.game_id), process, session_id
        )
        self.session_manager.attach_tracker(int(context.game_id), tracker)
        self._trackers[int(context.game_id)] = tracker
        return LaunchSessionResult(
            started=True,
            process=process,
            session=session,
            tracker=tracker,
            session_id=session_id,
        )

    def tracker(self, game_id: int):
        return self._trackers.get(int(game_id))

    def trackers(self) -> tuple[object, ...]:
        return tuple(self._trackers.values())

    def finish_tracker(self, tracker):
        game_id = int(getattr(tracker, "game_id"))
        session = self.session_manager.get(game_id)
        if session is not None:
            process = getattr(tracker, "process", None)
            exit_code = process.poll() if process is not None else None
            self.session_manager.finish(game_id, exit_code=exit_code)
        self._trackers.pop(game_id, None)
        return session


__all__ = [
    "LaunchSessionContext",
    "LaunchSessionCoordinator",
    "LaunchSessionResult",
]
