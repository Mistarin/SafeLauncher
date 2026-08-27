"""Advanced Linux host environment, distribution, driver, and shell inspector for SafeLauncher."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class DistroInfo:
    id: str
    id_like: List[str]
    pretty_name: str
    version_id: str
    package_manager: str
    install_command: str


@dataclass
class GPUInfo:
    vendor: str
    name: str
    driver_version: str
    is_hybrid: bool = False
    vulkan_supported: bool = False
    notes: List[str] = field(default_factory=list)


@dataclass
class ShellInfo:
    name: str
    path: str
    env_export_syntax: str  # e.g. "export VAR=val" or "set -gx VAR val"


@dataclass
class HostSystemAudit:
    distro: DistroInfo
    shell: ShellInfo
    gpus: List[GPUInfo]
    display_server: str
    desktop_environment: str
    kernel_version: str
    architecture: str
    tools_installed: Dict[str, bool]
    missing_packages: List[str]
    install_command_for_missing: str


# --------------------------------------------------------------------------- #
# Distro detection and package manager mapping                                #
# --------------------------------------------------------------------------- #

_DISTRO_PKGMGR_MAP = {
    "arch": ("pacman", "sudo pacman -S --needed"),
    "cachyos": ("pacman", "sudo pacman -S --needed"),
    "manjaro": ("pacman", "sudo pacman -S --needed"),
    "endeavouros": ("pacman", "sudo pacman -S --needed"),
    "garuda": ("pacman", "sudo pacman -S --needed"),
    "artix": ("pacman", "sudo pacman -S --needed"),
    "debian": ("apt", "sudo apt install -y"),
    "ubuntu": ("apt", "sudo apt install -y"),
    "linuxmint": ("apt", "sudo apt install -y"),
    "pop": ("apt", "sudo apt install -y"),
    "elementary": ("apt", "sudo apt install -y"),
    "zorin": ("apt", "sudo apt install -y"),
    "kali": ("apt", "sudo apt install -y"),
    "neon": ("apt", "sudo apt install -y"),
    "fedora": ("dnf", "sudo dnf install -y"),
    "nobara": ("dnf", "sudo dnf install -y"),
    "rhel": ("dnf", "sudo dnf install -y"),
    "centos": ("dnf", "sudo dnf install -y"),
    "almalinux": ("dnf", "sudo dnf install -y"),
    "rocky": ("dnf", "sudo dnf install -y"),
    "opensuse": ("zypper", "sudo zypper install -y"),
    "opensuse-tumbleweed": ("zypper", "sudo zypper install -y"),
    "opensuse-leap": ("zypper", "sudo zypper install -y"),
    "alpine": ("apk", "sudo apk add"),
    "void": ("xbps", "sudo xbps-install -S"),
    "gentoo": ("emerge", "sudo emerge -av"),
    "nixos": ("nix", "nix-env -iA nixos."),
}

_PACKAGE_NAME_ALIASES: Dict[str, Dict[str, str]] = {
    "firejail": {"apt": "firejail", "pacman": "firejail", "dnf": "firejail", "zypper": "firejail"},
    "wine": {"apt": "wine", "pacman": "wine", "dnf": "wine", "zypper": "wine"},
    "gamemode": {"apt": "gamemode", "pacman": "gamemode", "dnf": "gamemode", "zypper": "gamemode"},
    "mangohud": {"apt": "mangohud", "pacman": "mangohud", "dnf": "mangohud", "zypper": "mangohud"},
    "gamescope": {"apt": "gamescope", "pacman": "gamescope", "dnf": "gamescope", "zypper": "gamescope"},
    "ffmpeg": {"apt": "ffmpeg", "pacman": "ffmpeg", "dnf": "ffmpeg", "zypper": "ffmpeg"},
}


def detect_distribution() -> DistroInfo:
    """Parse /etc/os-release and identify the Linux distribution family and package manager."""
    data: Dict[str, str] = {}
    for p in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        data[k.strip()] = v.strip().strip('"').strip("'")
                break
            except Exception:
                pass

    distro_id = data.get("ID", "linux").lower()
    id_like = [x.strip().lower() for x in data.get("ID_LIKE", "").split() if x.strip()]
    pretty = data.get("PRETTY_NAME", data.get("NAME", "Linux"))
    version = data.get("VERSION_ID", "")

    # Resolve package manager from direct ID or ID_LIKE fallback
    pkg_mgr, cmd = _DISTRO_PKGMGR_MAP.get(distro_id, (None, None))
    if not pkg_mgr:
        for parent in id_like:
            if parent in _DISTRO_PKGMGR_MAP:
                pkg_mgr, cmd = _DISTRO_PKGMGR_MAP[parent]
                break
    if not pkg_mgr:
        pkg_mgr = "apt" if shutil.which("apt") else ("pacman" if shutil.which("pacman") else ("dnf" if shutil.which("dnf") else "unknown"))
        cmd = f"sudo {pkg_mgr} install -y" if pkg_mgr != "unknown" else "package-manager-install"

    return DistroInfo(
        id=distro_id,
        id_like=id_like,
        pretty_name=pretty,
        version_id=version,
        package_manager=pkg_mgr,
        install_command=cmd,
    )


# --------------------------------------------------------------------------- #
# Shell detection and syntax adaptation                                       #
# --------------------------------------------------------------------------- #

def detect_shell() -> ShellInfo:
    """Identify the active user shell and provide adapted syntax."""
    raw_shell = os.environ.get("SHELL", "/bin/bash").strip()
    name = Path(raw_shell).name.lower()

    if "fish" in name:
        syntax = "set -gx %s %s"
    elif "nu" in name:
        syntax = "$env.%s = '%s'"
    elif "csh" in name or "tcsh" in name:
        syntax = "setenv %s %s"
    else:
        syntax = "export %s=%s"

    return ShellInfo(name=name, path=raw_shell, env_export_syntax=syntax)


# --------------------------------------------------------------------------- #
# GPU and Driver inspection                                                   #
# --------------------------------------------------------------------------- #

def detect_gpus() -> List[GPUInfo]:
    """Inspect installed GPUs, proprietary NVIDIA drivers, Mesa, and Vulkan state."""
    gpus: List[GPUInfo] = []

    # 1. Probe NVIDIA via nvidia-smi
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            for line in out.strip().splitlines():
                if "," in line:
                    gpu_name, drv_ver = line.split(",", 1)
                    notes = ["Proprietary NVIDIA driver active"]
                    if os.path.exists("/sys/module/nvidia_drm/parameters/modeset"):
                        try:
                            with open("/sys/module/nvidia_drm/parameters/modeset") as f:
                                if f.read().strip() == "Y":
                                    notes.append("DRM modeset enabled (Wayland GBM ready)")
                        except Exception:
                            pass

                    gpus.append(GPUInfo(
                        vendor="NVIDIA",
                        name=gpu_name.strip(),
                        driver_version=drv_ver.strip(),
                        vulkan_supported=True,
                        notes=notes,
                    ))
        except Exception:
            pass

    # 2. Probe via lspci for AMD / Intel / secondary GPUs
    if shutil.which("lspci"):
        try:
            out = subprocess.check_output(["lspci"], stderr=subprocess.DEVNULL, text=True, timeout=2)
            for line in out.splitlines():
                line_lower = line.lower()
                if "vga compatible controller:" in line_lower or "3d controller:" in line_lower:
                    if "nvidia" in line_lower and any(g.vendor == "NVIDIA" for g in gpus):
                        continue  # Already captured via nvidia-smi

                    vendor = "AMD" if ("amd" in line_lower or "advanced micro devices" in line_lower or "radeon" in line_lower) else ("Intel" if "intel" in line_lower else "Generic")
                    name_part = line.split(":", 2)[-1].strip()

                    # Check Mesa version for AMD/Intel
                    mesa_ver = "Mesa Open-Source"
                    if shutil.which("glxinfo"):
                        try:
                            gl_out = subprocess.check_output(["glxinfo", "-B"], stderr=subprocess.DEVNULL, text=True, timeout=2)
                            m = re.search(r"OpenGL version string:.*Mesa\s+([0-9.]+)", gl_out)
                            if m:
                                mesa_ver = f"Mesa {m.group(1)}"
                        except Exception:
                            pass

                    gpus.append(GPUInfo(
                        vendor=vendor,
                        name=name_part,
                        driver_version=mesa_ver,
                        vulkan_supported=bool(shutil.which("vulkaninfo")),
                        notes=[f"Renderer: {vendor}"],
                    ))
        except Exception:
            pass

    # Mark hybrid GPU state if multiple GPUs detected (e.g. Intel + NVIDIA or AMD + NVIDIA)
    if len(gpus) > 1:
        for g in gpus:
            g.is_hybrid = True
            if g.vendor == "NVIDIA":
                g.notes.append("Hybrid Optimus setup: prime-run or __NV_PRIME_RENDER_OFFLOAD=1 available")

    return gpus


# --------------------------------------------------------------------------- #
# Display Server and Desktop Environment                                      #
# --------------------------------------------------------------------------- #

def detect_display_server() -> str:
    """Identify Wayland vs X11."""
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if session:
        return session.capitalize()
    if os.environ.get("WAYLAND_DISPLAY"):
        return "Wayland"
    if os.environ.get("DISPLAY"):
        return "X11"
    return "Headless / Unknown"


def detect_desktop_environment() -> str:
    """Identify KDE, GNOME, Hyprland, XFCE, etc."""
    de = os.environ.get("XDG_CURRENT_DESKTOP", "") or os.environ.get("DESKTOP_SESSION", "")
    return de.strip() or "Standard Linux Desktop"


# --------------------------------------------------------------------------- #
# Full System Audit & Diagnostic Report                                       #
# --------------------------------------------------------------------------- #

def audit_host_system() -> HostSystemAudit:
    """Run full system diagnostics and build actionable recommendations."""
    distro = detect_distribution()
    shell = detect_shell()
    gpus = detect_gpus()
    display = detect_display_server()
    desktop = detect_desktop_environment()

    # Kernel & Arch
    import platform
    kernel = platform.release()
    arch = platform.machine()

    # Tools check
    tools_to_check = ["firejail", "wine", "gamemode", "mangohud", "gamescope", "ffmpeg", "umu-run", "ludusavi"]
    installed: Dict[str, bool] = {}
    missing_keys: List[str] = []

    for t in tools_to_check:
        bin_name = "gamemoderun" if t == "gamemode" else t
        is_inst = bool(shutil.which(bin_name) or shutil.which(t))
        if not is_inst:
            # Check user-level and app-managed paths (e.g. ~/.local/share/safelauncher/bin/ludusavi)
            home = os.path.expanduser("~")
            custom_locations = [
                os.path.join(home, ".local", "share", "safelauncher", "bin", t),
                os.path.join(home, ".local", "bin", t),
                os.path.join(home, ".cargo", "bin", t),
                os.path.join(home, ".local", "share", "flatpak", "exports", "bin", t),
                f"/var/lib/flatpak/exports/bin/{t}",
            ]
            for loc in custom_locations:
                if os.path.isfile(loc) and os.access(loc, os.X_OK):
                    is_inst = True
                    break

        installed[t] = is_inst
        if not is_inst and t in _PACKAGE_NAME_ALIASES:
            missing_keys.append(t)

    # Generate tailored installation command for detected distro
    pkg_mgr = distro.package_manager
    missing_pkg_names: List[str] = []
    for k in missing_keys:
        alias_map = _PACKAGE_NAME_ALIASES.get(k, {})
        pkg_name = alias_map.get(pkg_mgr, k)
        missing_pkg_names.append(pkg_name)

    if missing_pkg_names:
        install_cmd = f"{distro.install_command} {' '.join(missing_pkg_names)}"
    else:
        install_cmd = ""

    return HostSystemAudit(
        distro=distro,
        shell=shell,
        gpus=gpus,
        display_server=display,
        desktop_environment=desktop,
        kernel_version=kernel,
        architecture=arch,
        tools_installed=installed,
        missing_packages=missing_pkg_names,
        install_command_for_missing=install_cmd,
    )


def print_system_report() -> None:
    """Print beautifully styled terminal diagnostics report."""
    audit = audit_host_system()

    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    print("\n" + f"{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}{BOLD}║               SafeLauncher Host System Diagnostics               ║{RESET}")
    print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════════╝{RESET}")

    # OS & Kernel
    print(f"\n{BOLD}Operating System & Kernel:{RESET}")
    print(f"  • Distro:       {GREEN}{audit.distro.pretty_name}{RESET} (ID: {audit.distro.id})")
    print(f"  • Pkg Manager:  {CYAN}{audit.distro.package_manager}{RESET}")
    print(f"  • Kernel:       {audit.kernel_version} ({audit.architecture})")
    print(f"  • Display:      {audit.display_server} ({audit.desktop_environment})")

    # Shell
    print(f"\n{BOLD}Shell Environment:{RESET}")
    print(f"  • User Shell:   {GREEN}{audit.shell.name}{RESET} ({audit.shell.path})")
    example_env = audit.shell.env_export_syntax % ("SAFELAUNCHER_CLOUD_MODE", "convex")
    print(f"  • Syntax Hint:  {DIM}{example_env}{RESET}")

    # GPU
    print(f"\n{BOLD}Graphics Hardware & Drivers:{RESET}")
    if audit.gpus:
        for i, g in enumerate(audit.gpus, 1):
            print(f"  • GPU {i}:        {GREEN}{g.name}{RESET} ({g.vendor})")
            print(f"    Driver:       {g.driver_version}")
            for note in g.notes:
                print(f"    {DIM}↳ {note}{RESET}")
    else:
        print(f"  {YELLOW}• No dedicated GPU driver probes responded. Standard Mesa fallback active.{RESET}")

    # Gaming & Sandbox Tools
    print(f"\n{BOLD}Gaming & Sandboxing Dependencies:{RESET}")
    for tool, is_installed in audit.tools_installed.items():
        if is_installed:
            print(f"  {GREEN}✔ {tool:<12}{RESET} Installed")
        else:
            print(f"  {RED}✖ {tool:<12}{RESET} Not found")

    # Actionable suggestions
    if audit.missing_packages:
        print(f"\n{YELLOW}{BOLD}Recommended Action for {audit.distro.pretty_name}:{RESET}")
        print("  Install missing gaming and sandboxing packages with:")
        print(f"  {CYAN}{BOLD}{audit.install_command_for_missing}{RESET}")
    else:
        print(f"\n{GREEN}{BOLD}✔ All essential gaming and sandbox dependencies are installed!{RESET}")

    print("")


if __name__ == "__main__":
    print_system_report()
