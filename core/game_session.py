"""Authoritative lifecycle model for launched game processes."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal


@dataclass
class GameSession:
    game_id: int
    game_name: str
    process: object
    session_id: str = ""
    sandbox_name: str = ""
    tracker: object = None
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    exit_code: Optional[int] = None
    state: str = "running"
    failure_reason: str = ""

    @property
    def is_live(self) -> bool:
        return bool(self.process and self.process.poll() is None)


class GameSessionManager(QObject):
    """Own one authoritative session per running game."""

    session_started = pyqtSignal(object)
    session_state_changed = pyqtSignal(object)
    session_finished = pyqtSignal(object)
    session_failed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sessions: dict[int, GameSession] = {}

    def start(self, game_id: int, game_name: str, process, session_id: str = "") -> GameSession:
        existing = self._sessions.get(game_id)
        if existing and existing.is_live:
            raise RuntimeError(f"Game '{game_name}' is already running")
        session = GameSession(
            game_id=game_id,
            game_name=game_name,
            process=process,
            session_id=session_id,
            sandbox_name=getattr(process, "safelauncher_sandbox_name", "") or "",
        )
        self._sessions[game_id] = session
        self.session_started.emit(session)
        return session

    def get(self, game_id: int) -> Optional[GameSession]:
        return self._sessions.get(game_id)

    def active(self) -> list[GameSession]:
        return [session for session in self._sessions.values() if session.state in {"starting", "running", "stopping"}]

    def active_game_ids(self) -> set[int]:
        return {session.game_id for session in self.active() if session.is_live or session.state == "stopping"}

    def attach_tracker(self, game_id: int, tracker) -> Optional[GameSession]:
        session = self._sessions.get(game_id)
        if session is None:
            return None
        session.tracker = tracker
        return session

    def mark_stopping(self, game_id: int) -> Optional[GameSession]:
        session = self._sessions.get(game_id)
        if session is None or session.state in {"exited", "failed"}:
            return session
        session.state = "stopping"
        self.session_state_changed.emit(session)
        return session

    def finish(self, game_id: int, exit_code: Optional[int] = None, reason: str = "") -> Optional[GameSession]:
        session = self._sessions.get(game_id)
        if session is None or session.state in {"exited", "failed"}:
            return session
        session.exit_code = exit_code if exit_code is not None else session.process.poll()
        session.finished_at = time.time()
        session.failure_reason = reason
        session.state = "failed" if reason else "exited"
        self.session_state_changed.emit(session)
        if reason:
            self.session_failed.emit(session)
        else:
            self.session_finished.emit(session)
        return session

    def remove(self, game_id: int) -> Optional[GameSession]:
        return self._sessions.pop(game_id, None)

    def clear_finished(self) -> None:
        for game_id, session in list(self._sessions.items()):
            if session.state in {"exited", "failed"}:
                self._sessions.pop(game_id, None)
