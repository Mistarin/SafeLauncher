"""Application boundary for automatic cloud-save synchronization after exit.

The service owns exit-sync request construction and result normalization. It
does not decide what widgets should display; MainWindow remains responsible
for toasts and detail refreshes after the automatic newest-save decision.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.cloud_operation_service import CloudOperationService, CloudOperationTarget
from core.request_contracts import (
    RequestPriority,
    ResourceStatus,
    classify_remote_error,
)


@dataclass(frozen=True, slots=True)
class CloudExitPresentation:
    """UI-neutral instructions for presenting one terminal exit-sync result."""

    kind: str
    message: str = ""
    refresh_status: bool = False
    refresh_detail: bool = False


@dataclass(frozen=True, slots=True)
class CloudExitSyncResult:
    """Redacted, presentation-neutral outcome of one exit synchronization."""

    game_id: int
    game_name: str
    outcome: str
    reason: str = ""
    error: str = ""
    guidance: str = ""
    error_category: str = ""

    def as_payload(self) -> dict:
        """Return the legacy signal payload shape during UI migration."""
        return {
            "game": self.game_name,
            "game_id": self.game_id,
            "outcome": self.outcome,
            "reason": self.reason,
            "error": self.error,
            "guidance": self.guidance,
            "error_category": self.error_category,
        }

    def presentation(self) -> CloudExitPresentation:
        """Return UI-neutral presentation instructions for the terminal result.

        The service owns wording and outcome classification; the Qt layer only
        decides which widgets to refresh and how to display the instruction.
        """
        if self.outcome in {"uploaded", "restored"}:
            return CloudExitPresentation(
                kind="success",
                message=(
                    f"Cloud save synced for '{self.game_name}'."
                    if self.outcome == "uploaded"
                    else f"Newest cloud save restored for '{self.game_name}'."
                ),
                refresh_status=True,
                refresh_detail=True,
            )
        if self.outcome == "failed":
            message = self.error or "Cloud sync failed."
            if self.guidance:
                message = f"{message} Local save preserved. {self.guidance}".strip()
            return CloudExitPresentation(kind="error", message=message)
        return CloudExitPresentation(kind="silent")


class CloudExitSyncService:
    """Coordinate exit-save requests without owning Qt or presentation."""

    def __init__(self, operation_service: CloudOperationService):
        self.operation_service = operation_service

    def request(
        self,
        target: CloudOperationTarget,
        *,
        settle_seconds: float = 0.5,
        priority: RequestPriority = RequestPriority.BACKGROUND,
        tag: str = "game_exit",
    ):
        return self.operation_service.request_exit_sync(
            target,
            settle_seconds=settle_seconds,
            priority=priority,
            tag=tag,
        )

    @staticmethod
    def resolve(target: CloudOperationTarget, future) -> CloudExitSyncResult:
        """Normalize a manager result or exception into one stable outcome."""
        try:
            resource = future.result()
            value = resource.value if resource.status in {
                ResourceStatus.READY,
                ResourceStatus.STALE,
            } else None
            if isinstance(value, dict):
                return CloudExitSyncResult(
                    game_id=int(value.get("game_id", target.game_id)),
                    game_name=str(value.get("game", target.game_name)),
                    outcome=str(value.get("outcome", "failed")),
                    reason=str(value.get("reason", "")),
                    error=str(value.get("error", "")),
                    guidance=str(value.get("guidance", "")),
                    error_category=str(value.get("error_category", "")),
                )
            error = str(resource.error or "Cloud exit sync failed.")
            category = getattr(resource, "error_category", "") or classify_remote_error(error).value
        except Exception as exc:
            error = str(exc) or "Cloud exit sync failed."
            category = classify_remote_error(exc).value
        return CloudExitSyncResult(
            game_id=int(target.game_id),
            game_name=str(target.game_name),
            outcome="failed",
            reason=error,
            error=error,
            error_category=category,
        )


__all__ = ["CloudExitPresentation", "CloudExitSyncResult", "CloudExitSyncService"]
