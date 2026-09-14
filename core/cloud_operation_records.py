"""Redacted lifecycle records for private cloud-save operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.request_contracts import RequestKey


class CloudOperationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CloudOperationRecord:
    """Metadata-only operation state; never contains save payloads."""

    operation_id: str
    key: RequestKey
    game_id: int
    operation: str
    context_fingerprint: str
    generation: int
    state: CloudOperationState
    progress: float | None = None
    started_at: float = 0.0
    finished_at: float = 0.0
    error_category: str = ""
    error: str = ""
    result_status: str = ""


__all__ = ["CloudOperationRecord", "CloudOperationState"]
