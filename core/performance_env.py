"""Validated per-game performance environment settings.

The UI stores the two SafeLauncher-managed settings in the existing per-game
environment JSON.  This module translates them into real process variables at
launch time, keeping that policy out of the widgets and the shell builder.
"""

from __future__ import annotations

import ctypes.util
import re
import shutil
from typing import Mapping


ENABLE_GAMEMODE = "SAFELAUNCHER_ENABLE_GAMEMODE"
GAMEMODE_MODE = "SAFELAUNCHER_GAMEMODE_MODE"
DXVK_MAX_DEVICE_MEMORY_MB = "SAFELAUNCHER_DXVK_MAX_DEVICE_MEMORY_MB"

# These are owned by the named controls rather than the free-form table.
MANAGED_ENV_KEYS = {
    ENABLE_GAMEMODE,
    GAMEMODE_MODE,
    DXVK_MAX_DEVICE_MEMORY_MB,
    "LD_PRELOAD",
    "DXVK_CONFIG",
    "DXVK_CONFIG_FILE",
}

MIN_VRAM_MB = 256
MAX_VRAM_MB = 131072


def parse_vram_mb(value: object) -> int | None:
    """Return a validated VRAM value, or ``None`` when it is not usable."""
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if MIN_VRAM_MB <= parsed <= MAX_VRAM_MB:
        return parsed
    return None


def gamemode_library() -> str | None:
    """Resolve the GameMode preload library without requiring a shell lookup."""
    return ctypes.util.find_library("gamemodeauto") or None


def gamemode_wrapper() -> str | None:
    """Resolve the standard Steam-launch-option-style GameMode wrapper."""
    return shutil.which("gamemoderun")


def _with_dxvk_memory(config: str, value: int) -> str:
    """Make the managed maxDeviceMemory option authoritative in DXVK_CONFIG."""
    option = f"dxgi.maxDeviceMemory = {value}"
    # DXVK_CONFIG uses semicolons as separators. Remove an older value before
    # appending ours so the result is deterministic even for legacy configs.
    cleaned = re.sub(
        r"(?:^|;)\s*dxgi\.maxDeviceMemory\s*=\s*[^;]*",
        "",
        str(config or ""),
        flags=re.IGNORECASE,
    ).strip(" ;")
    return f"{cleaned}; {option}" if cleaned else option


def build_launch_env(raw_env: Mapping[str, object] | None) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(launch_env, status)`` for a per-game environment mapping.

    ``launch_env`` contains only actual variables passed to the game.  The
    SafeLauncher control keys are never leaked to Wine/Proton. ``status`` is
    suitable for diagnostics and contains no secrets.
    """
    source = raw_env if isinstance(raw_env, Mapping) else {}
    launch_env: dict[str, str] = {}
    for key, value in source.items():
        key = str(key).strip()
        if key in (ENABLE_GAMEMODE, GAMEMODE_MODE, DXVK_MAX_DEVICE_MEMORY_MB):
            continue
        if not key or value is None or str(value).strip() == "":
            continue
        clean_key = "".join(ch for ch in key if ch.isalnum() or ch == "_")
        if clean_key:
            launch_env[clean_key] = str(value)

    status = {
        "gamemode": "disabled",
        "gamemode_mode": "",
        "vram_override": "disabled",
        "enabled": "none",
    }

    if str(source.get(ENABLE_GAMEMODE, "0")).strip().lower() in {"1", "true", "yes", "on"}:
        mode = str(source.get(GAMEMODE_MODE, "feral")).strip().lower()
        if mode not in {"feral", "steam"}:
            mode = "feral"
        status["gamemode_mode"] = mode
        if mode == "steam":
            wrapper = gamemode_wrapper()
            if wrapper:
                status["gamemode"] = f"enabled via wrapper ({wrapper})"
                status["gamemode_wrapper"] = wrapper
            else:
                status["gamemode"] = "requested standard wrapper but gamemoderun was not found"
        else:
            library = gamemode_library()
            if library:
                existing = launch_env.get("LD_PRELOAD", "").strip()
                launch_env["LD_PRELOAD"] = f"{library}:{existing}" if existing else library
                status["gamemode"] = f"enabled via Feral injection ({library})"
            else:
                status["gamemode"] = "requested Feral injection but libgamemodeauto.so.0 was not found"

    vram = parse_vram_mb(source.get(DXVK_MAX_DEVICE_MEMORY_MB))
    if vram is not None:
        launch_env["DXVK_CONFIG"] = _with_dxvk_memory(launch_env.get("DXVK_CONFIG", ""), vram)
        status["vram_override"] = f"{vram} MiB reported to DXVK"

    enabled: list[str] = []
    if launch_env.get("WINE_FULLSCREEN_FSR") == "1":
        strength = launch_env.get("WINE_FULLSCREEN_FSR_STRENGTH", "default")
        enabled.append(f"FSR (sharpness {strength})")
    if launch_env.get("DXVK_ASYNC") == "1":
        enabled.append("DXVK Async")
    if "gpl" in launch_env.get("RADV_PERFTEST", "").lower():
        enabled.append("Mesa GPL")
    if launch_env.get("DRI_PRIME") == "1" or launch_env.get("MESA_VK_DEVICE_SELECT") == "1":
        enabled.append("Dedicated GPU")
    if launch_env.get("DXVK_FRAME_RATE"):
        enabled.append(f"FPS cap {launch_env['DXVK_FRAME_RATE']}")
    if status["gamemode"] not in {"disabled"} and status["gamemode"].startswith("enabled"):
        enabled.append(f"GameMode ({status['gamemode_mode']})")
    if vram is not None:
        enabled.append(f"DXVK VRAM {vram} MiB")
    status["enabled"] = ", ".join(enabled) if enabled else "none"

    return launch_env, status
