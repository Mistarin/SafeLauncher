"""Central policy for optional network activity.

SafeLauncher can do useful work entirely from the local database, installed
game files, and artwork/metadata caches.  Network access must therefore be a
single, observable policy rather than a collection of unrelated environment
checks spread through the UI.

``SAFELAUNCHER_OFFLINE_MODE=1`` is useful for launchers, CI, and emergency
recovery.  The same policy is persisted from Settings so a user can enable it
before disconnecting.  The test-only offline flag remains an implicit block
for automatic work, but does not change the user's persisted preference.
"""

from __future__ import annotations

import os
from typing import Any

from PyQt6.QtCore import QSettings


OFFLINE_MODE_ENV = "SAFELAUNCHER_OFFLINE_MODE"
OFFLINE_TEST_MODE_ENV = "SAFELAUNCHER_OFFLINE_TEST_MODE"
OFFLINE_MODE_SETTING = "offline_mode"

# Reachability is process-wide because several legacy callers perform their
# policy check outside MainWindow. ``None`` means no monitor has established a
# verdict yet; it preserves normal behavior before an application monitor is
# attached. The monitor marks the network unavailable while its first probe
# is pending, then publishes each verdict here.
_NETWORK_REACHABLE: bool | None = None


def set_network_reachability(reachable: bool | None) -> None:
    """Publish the monitor's reachability verdict for all network guards."""
    global _NETWORK_REACHABLE
    _NETWORK_REACHABLE = None if reachable is None else bool(reachable)


def _env_flag(name: str) -> bool | None:
    """Return an explicitly configured boolean environment value."""
    value = os.environ.get(name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_offline_mode(settings: Any | None = None) -> bool:
    """Return whether the user/application explicitly disabled networking."""
    env_value = _env_flag(OFFLINE_MODE_ENV)
    if env_value is not None:
        return env_value
    if settings is None:
        settings = QSettings("SafeLauncher", "SafeLauncher")
    return bool(settings.value(OFFLINE_MODE_SETTING, False, type=bool))


def offline_mode_forced_by_environment() -> bool:
    """Return whether the environment hard-locks the application offline."""
    return _env_flag(OFFLINE_MODE_ENV) is True


def network_policy_enabled(settings: Any | None = None) -> bool:
    """Return whether user and environment policy permits network activity.

    The smoke-test switch is intentionally included here, so production code
    cannot accidentally reintroduce DNS-dependent work into deterministic UI
    tests while the normal offline setting remains user-visible and durable.
    """
    if is_offline_mode(settings):
        return False
    test_value = _env_flag(OFFLINE_TEST_MODE_ENV)
    return test_value is not True


def automatic_network_allowed(settings: Any | None = None) -> bool:
    """Return whether network work is permitted by policy and reachability."""
    return network_policy_enabled(settings) and _NETWORK_REACHABLE is not False


def set_offline_mode(enabled: bool, settings: Any | None = None) -> None:
    """Persist the user's offline preference."""
    if settings is None:
        settings = QSettings("SafeLauncher", "SafeLauncher")
    settings.setValue(OFFLINE_MODE_SETTING, bool(enabled))
    try:
        settings.sync()
    except Exception:
        # QSettings backends can be read-only in portable/test environments;
        # the in-process value is still useful for the current session.
        pass


def offline_status_text(settings: Any | None = None) -> str:
    """Human-readable status used by settings and non-blocking UI messages."""
    if is_offline_mode(settings):
        return "Offline mode enabled — automatic internet access is disabled."
    if _env_flag(OFFLINE_TEST_MODE_ENV) is True:
        return "Offline test mode — automatic internet access is disabled."
    return "Online mode — automatic internet access is allowed when connected."
