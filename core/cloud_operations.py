"""Shared application boundary for cloud-save operations.

UI surfaces should use this module instead of translating CloudSaveSyncEngine
return values independently. The low-level engine remains responsible for
packaging, transport, encryption, and restore validation.
"""

from dataclasses import dataclass, replace
from typing import Optional

from core.cloud_backend import describe_cloud_error
from core.cloud_save_sync import CloudSaveSyncEngine, SyncStatus
from core.save_models import SaveOperationResult


# Compatibility name retained for callers that use the cloud-specific type.
# Save workflows now share one result model across cloud, local, and UI layers.
CloudOperationResult = SaveOperationResult


@dataclass
class CloudPreflightResult:
    """Read-only sync decision input for launch and library surfaces."""
    status: Optional[SyncStatus] = None
    local_stats: object = None
    cloud_stats: object = None
    error: Optional[CloudOperationResult] = None


def classify_cloud_error(error: str, status: int = 0) -> tuple[str, str]:
    text = (error or "").lower()
    if status == 404 or "404" in text or "endpoint not found" in text:
        return "backend_unavailable", "The deployment is reachable but the SafeLauncher API endpoint is missing. Verify the .convex.site URL and redeploy the current backend."
    if status in (401, 403) or "authentication" in text or "secret key" in text:
        return "authentication", "Open Settings → Cloud and verify the Site URL and Secret Access Key."
    if status in (413, 507) or "quota" in text or "too large" in text:
        return "quota", "Check cloud usage and deploy the current backend if an older size limit is still active."
    if "not found" in text or "no local save" in text or "no longer exists" in text or "no readable files" in text:
        return "local_save_missing", "Rescan local saves and verify that the game has a readable save location."
    if "permission" in text or "read" in text:
        return "local_save_unreadable", "Check file permissions, close the game, rescan saves, and retry."
    if "timeout" in text or "connection" in text or "network" in text:
        return "backend_unavailable", "Check the network connection and cloud configuration, then retry."
    return "unknown", "Review the SafeLauncher logs for the exact cause, then retry after correcting the reported problem."


def _failure(operation: str, game_name: str, error: str = "") -> CloudOperationResult:
    error = error or "Cloud operation failed."
    category, guidance = classify_cloud_error(error)
    return CloudOperationResult(
        False, operation, game_name, error=error,
        category=category, guidance=guidance,
    )


def _normalize_engine_result(result: CloudOperationResult) -> CloudOperationResult:
    """Add user-facing classification without consulting a global engine error."""
    if result.success:
        return result
    category, guidance = classify_cloud_error(result.error)
    return replace(
        result,
        category=category if result.category in {"unknown", ""} else result.category,
        guidance=result.guidance or guidance,
        retry_safe=result.retry_safe and category not in {"local_save_missing", "local_save_unreadable"},
    )


class CloudOperationCoordinator:
    """Stable, UI-neutral facade for cloud save workflows."""

    @staticmethod
    def upload_local_save(
        game_name: str,
        game_path: str,
        steam_id: str = "",
        locations=None,
        snapshot=None,
        cancel_check=None,
    ) -> CloudOperationResult:
        try:
            result = CloudSaveSyncEngine.sync_local_to_cloud(
                game_name, game_path, steam_id=steam_id, locations=locations,
                snapshot=snapshot,
                cancel_check=cancel_check,
            )
        except Exception as exc:
            return _failure("Cloud upload", game_name, describe_cloud_error(exc))
        return _normalize_engine_result(result)

    @staticmethod
    def restore_cloud_save(game_name: str, game_path: str, steam_id: str = "", target_version: Optional[int] = None) -> CloudOperationResult:
        try:
            result = CloudSaveSyncEngine.sync_cloud_to_local(
                game_name, game_path, steam_id=steam_id,
                preserve_local_fork=True, target_version=target_version,
            )
        except Exception as exc:
            return _failure("Cloud restore", game_name, describe_cloud_error(exc))
        return _normalize_engine_result(result)

    @staticmethod
    def restore_generation(game_name: str, game_path: str, steam_id: str = "", version: Optional[int] = None) -> CloudOperationResult:
        result = CloudOperationCoordinator.restore_cloud_save(
            game_name, game_path, steam_id=steam_id, target_version=version
        )
        result.operation = "Generation restore"
        return result

    @staticmethod
    def check_status(game_name: str, game_path: str, steam_id: str = ""):
        try:
            status, local_stats, cloud_stats = CloudSaveSyncEngine.check_sync_status(
                game_name, game_path, steam_id
            )
            return status, local_stats, cloud_stats, None
        except Exception as exc:
            return None, None, None, _failure("Cloud status check", game_name, describe_cloud_error(exc))

    @staticmethod
    def preflight(game_name: str, game_path: str, steam_id: str = "") -> CloudPreflightResult:
        status, local_stats, cloud_stats, error = CloudOperationCoordinator.check_status(
            game_name, game_path, steam_id
        )
        return CloudPreflightResult(status, local_stats, cloud_stats, error)

    @staticmethod
    def load_history(game_name: str, game_path: str, steam_id: str = "") -> tuple[list, Optional[CloudOperationResult]]:
        try:
            return CloudSaveSyncEngine.get_available_versions(game_name, game_path, steam_id), None
        except Exception as exc:
            return [], _failure("History load", game_name, describe_cloud_error(exc))


__all__ = ["CloudOperationCoordinator", "CloudOperationResult", "CloudPreflightResult", "classify_cloud_error"]
