"""SafeLauncherCloud transport boundary.

ConvexSaveBackend is retained as the compatibility name used by older
features. CloudClient gives manager-backed code an explicit transport type
without moving HTTP or encryption responsibilities into the request manager.
"""

from core.cloud_backend import ConvexSaveBackend


class CloudClient(ConvexSaveBackend):
    """SafeLauncherCloud HTTP/encrypted-save transport."""


__all__ = ["CloudClient"]
