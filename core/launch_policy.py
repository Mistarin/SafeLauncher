"""Pure launch-entry policy used by the MainWindow composition root.

This module deliberately contains no Qt, process, database, or cloud code.
It answers only the first decision made when the user activates a library
row.  Dialog presentation and the actual launch remain owned by the UI and
``LaunchSessionCoordinator`` respectively.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LaunchAction(StrEnum):
    """Action the composition root should take for a selected game."""

    RESTORE = "restore"
    STOP = "stop"
    LAUNCH = "launch"
    CHOOSE_MODE = "choose_mode"


@dataclass(frozen=True, slots=True)
class LaunchDecision:
    """Stable result of evaluating one selected game launch request."""

    action: LaunchAction
    mode: str = ""


class LaunchPolicy:
    """Evaluate launch/stop/restore/mode-selection policy without side effects."""

    @staticmethod
    def decide(game, *, is_running: bool, shift_pressed: bool) -> LaunchDecision:
        """Return the next UI/application action for one library row.

        The library tuple intentionally remains the boundary input here: the
        policy needs only the archived flag (column 17) and configured launch
        mode (column 4).  Missing columns are treated as unset for compatibility
        with lightweight test fixtures and older database rows.
        """
        if bool(game[17]) if len(game) > 17 else False:
            return LaunchDecision(LaunchAction.RESTORE)
        if is_running:
            return LaunchDecision(LaunchAction.STOP)

        configured_mode = str(game[4] or "").strip() if len(game) > 4 else ""
        if configured_mode and not shift_pressed:
            return LaunchDecision(LaunchAction.LAUNCH, configured_mode)
        return LaunchDecision(LaunchAction.CHOOSE_MODE)


__all__ = ["LaunchAction", "LaunchDecision", "LaunchPolicy"]
