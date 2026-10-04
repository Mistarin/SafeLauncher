"""Explicit save-dialog dependencies, with one legacy parent adapter."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SaveDialogServices:
    request_manager: Any = None
    operation_registry: Any = None
    worker_registry: Any = None
    cloud_center: Any = None
    cloud_operations: Any = None
    cloud_status: Any = None
    save_state: Any = None
    backup: Any = None
    on_changed: Callable[[int], None] | None = None
    open_cloud_center: Callable[[], None] | None = None

    @classmethod
    def from_parent(cls, parent):
        """Compatibility only; production callers pass a composed bundle."""
        def changed(game_id):
            if hasattr(parent, "refresh_cloud_status_for_game"):
                parent.refresh_cloud_status_for_game(game_id)
            elif hasattr(parent, "request_cloud_recheck"):
                parent.request_cloud_recheck([game_id], "save_restored")
            if hasattr(parent, "_load_save_stats_async"):
                parent._load_save_stats_async()
            if hasattr(parent, "_notify_parent_cloud_changed"):
                parent._notify_parent_cloud_changed()
        host = getattr(parent, "parent_window", None) or parent
        return cls(
            request_manager=getattr(parent, "request_manager", None),
            operation_registry=getattr(parent, "operation_registry", None),
            worker_registry=getattr(parent, "worker_supervisor", None),
            cloud_center=getattr(parent, "cloud_center_service", None),
            cloud_operations=getattr(parent, "cloud_operation_service", None),
            cloud_status=getattr(parent, "cloud_status_service", None),
            save_state=getattr(parent, "save_state_store", None),
            on_changed=changed, open_cloud_center=getattr(host, "_open_cloud_center", None),
        )
