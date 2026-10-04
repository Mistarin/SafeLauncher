"""Explicit Settings dependencies; parent lookup is legacy compatibility only."""
from dataclasses import dataclass


@dataclass(frozen=True)
class SettingsDialogServices:
    request_manager: object = None
    operation_registry: object = None
    worker_registry: object = None
    cloud_account: object = None
    cloud_center: object = None
    open_cloud_center: object = None

    @classmethod
    def from_parent(cls, parent, request_manager=None):
        return cls(request_manager=request_manager or getattr(parent, "request_manager", None),
                   operation_registry=getattr(parent, "operation_registry", None),
                   worker_registry=getattr(parent, "worker_supervisor", None),
                   cloud_account=getattr(parent, "cloud_account_service", None),
                   cloud_center=getattr(parent, "cloud_center_service", None),
                   open_cloud_center=getattr(parent, "_open_cloud_center", None))
