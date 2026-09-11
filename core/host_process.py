"""Environment handling for host tools started by the packaged launcher."""

import os
import re
from typing import Mapping, Optional


_PYTHON_OVERRIDES = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONEXECUTABLE",
    "PYTHONUSERBASE",
)

_SENSITIVE_ENV_NAMES = frozenset(
    {
        "AUTH0_CLIENT_SECRET",
        "CONVEX_DEPLOY_KEY",
        "CONVEX_GATEWAY_KEY",
        "RAWG_API_KEY",
        "SAFELAUNCHER_GATEWAY_KEY",
        "SAFELAUNCHER_SECRET_KEY",
        "STEAMGRIDDB_API_KEY",
        "STEAM_WEB_API_KEY",
    }
)
_SENSITIVE_ENV_FRAGMENTS = (
    "API_KEY",
    "ACCESS_TOKEN",
    "CLIENT_SECRET",
    "DEPLOY_KEY",
    "GATEWAY_KEY",
    "PASSWORD",
    "PRIVATE_KEY",
    "REFRESH_TOKEN",
    "SECRET",
    "TOKEN",
)


def is_sensitive_env_name(name: str) -> bool:
    """Return whether an environment variable should not enter host tools.

    SafeLauncher starts third-party tools and game binaries. They must not
    inherit launcher, cloud, provider, or shell credentials merely because
    those credentials happen to be present in the desktop process environment.
    """
    normalized = str(name or "").strip().upper()
    return bool(normalized) and (
        normalized in _SENSITIVE_ENV_NAMES
        or any(fragment in normalized for fragment in _SENSITIVE_ENV_FRAGMENTS)
    )


def host_process_env(base_env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Return an environment safe for system executables.

    PyInstaller one-file applications temporarily prepend their extraction
    directory to ``LD_LIBRARY_PATH``. Passing that environment to host tools
    such as ``/bin/sh`` or ``umu-run`` can make them load incompatible bundled
    readline/OpenSSL libraries. PyInstaller preserves the caller's original
    value in ``LD_LIBRARY_PATH_ORIG``; restore it before starting host tools.
    """
    env = dict(os.environ if base_env is None else base_env)
    original_library_path = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original_library_path is None:
        env.pop("LD_LIBRARY_PATH", None)
    else:
        env["LD_LIBRARY_PATH"] = original_library_path

    # These interpreter/loader overrides are meaningful to the bundled app but
    # must not influence the host Python used by umu-run or other system tools.
    env.pop("LD_PRELOAD", None)
    env.pop("LD_AUDIT", None)
    for variable in _PYTHON_OVERRIDES:
        env.pop(variable, None)
    for variable in list(env):
        if is_sensitive_env_name(variable):
            env.pop(variable, None)
    return env
