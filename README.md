# SafeLauncher

### Secure, high-performance game sandboxing and library manager for Linux.

[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey.svg)](https://www.kernel.org)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

<img width="2560" height="1440" alt="SafeLauncher Library Interface" src="https://github.com/user-attachments/assets/0c776ac5-fcd6-4ef1-b236-3b81d6d117ff" />

SafeLauncher isolates Windows and Linux games inside dedicated Firejail sandboxes. Every title runs in its own prefix with custom launch policies, network isolation, and runtime environment controls.

It combines Proton/UMU/Wine runtime management, SteamGridDB metadata and poster syncing, archive installations (`.zip`, `.7z`, `.tar.gz`), local save backups, and zero-knowledge client-encrypted private cloud save sync on Convex.

---

## Quick Start (Setup Scripts)

The repository provides ordered scripts in `setup/` for running, configuring, testing, and packaging SafeLauncher:

```bash
git clone https://github.com/Mistarin/SafeLauncher.git
cd SafeLauncher
```

| Step | Script | Action |
| :---: | :--- | :--- |
| **`01`** | `./setup/01-doctor.sh` | Audit host distro, GPU drivers (NVIDIA/AMD/Intel), Wayland/X11, and gaming dependencies (`firejail`, `wine`, `gamemode`, `mangohud`, `gamescope`). |
| **`02`** | `./setup/02-launch.sh` | Verify Python requirements, set up `.venv` automatically, and start SafeLauncher. |
| **`03`** | `./setup/03-setup-cloud.sh` | Deploy your personal Convex cloud backend from source or connect an existing deployment. |
| **`04`** | `./setup/04-install-desktop-entry.sh` | Register the application icon and `.desktop` launcher in your application menu. |
| **`05`** | `./setup/05-build-appimage.sh` | Compile a standalone, reproducible Linux AppImage in `dist/SafeLauncher-x86_64.AppImage`. |
| **`06`** | `./setup/06-run-tests.sh` | Execute the component and integration test suite. |

---

## AppImage Installation

If you prefer using prebuilt binaries without managing a Python environment:

1. Download the latest release from [Releases](https://github.com/Mistarin/SafeLauncher/releases/latest).
2. Mark executable and run:
   ```bash
   chmod +x SafeLauncher-x86_64.AppImage
   ./SafeLauncher-x86_64.AppImage
   ```

### Host Requirements

SafeLauncher requires `firejail` and a compatibility runner (`wine`, `proton`, or `umu-run`) on the host system:

* **Arch Linux / CachyOS / Manjaro**:
  ```bash
  sudo pacman -S --needed firejail wine mangohud gamemode gamescope
  ```
* **Ubuntu / Debian / Mint / Pop!_OS**:
  ```bash
  sudo apt install -y firejail wine mangohud gamemode gamescope
  ```
* **Fedora / Nobara**:
  ```bash
  sudo dnf install -y firejail wine mangohud gamemode gamescope
  ```
* **openSUSE**:
  ```bash
  sudo zypper install -y firejail wine mangohud gamemode gamescope
  ```

---

## CLI Commands

SafeLauncher supports direct terminal flags for diagnostics and setup:

```bash
# Run host system and driver diagnostics
python3 main.py --doctor

# Run interactive terminal cloud save setup wizard
python3 main.py --setup-cloud

# Run test suite
python3 test.py
```

---

## Core Features

### 1. Sandbox Isolation & Prefix Sanitization
* **Process Sandboxing**: Wraps games, Wine helpers, and runtime tools inside Firejail namespaces.
* **Network Isolation**: Runs offline games with `--net=none` to prevent unauthorized outbound connections.
* **Prefix Isolation**: Allocates a separate Wine/Proton prefix per game and sanitizes host root directory symlinks (e.g. `dosdevices/z:` mapping).
* **Launch Diagnostics**: Records structured sandbox audit logs per session in `~/.local/share/safelauncher/logs/`.

### 2. Compatibility & Performance Runtimes
* **Runners**: Supports Valve Proton, Proton-GE, UMU Launcher (`umu-run`), system Wine, or custom binary paths.
* **Optimization Overlays**: Toggles Feral GameMode (`gamemoderun`), MangoHud performance monitoring, and Gamescope micro-compositing.
* **Per-Game Variables**: Configure custom environment variables, DLL overrides, and launch arguments.

### 3. Save Management & Private Cloud Sync
* **Automatic Detection**: Finds save directories across Wine prefixes, Proton prefixes, and native folders using Ludusavi heuristics.
* **Local Snapshots**: Export and restore save archives with level-9 DEFLATE compression and Zip-Slip path traversal protection.
* **Private Cloud Backend ([SafeLauncherCloud](https://github.com/Mistarin/SafeLauncherCloud.git))**:
  * Sync game saves to your own private Convex backend with client-side AES-256-GCM encryption.
  * Free 1 GiB storage quota on Convex free tier with 50 MiB max payload support.
  * 3-generation version rollback and conflict resolution.

### 4. Library Management & Archive Installer
* **SteamGridDB Sync**: Fetches high-resolution posters, hero banners, logos, and icons.
* **Hardware-Accelerated UI**: In-memory `QPixmapCache` guarantees 60/120 FPS scrolling on large libraries.
* **Archive Installer**: Installs games directly from `.zip`, `.7z`, `.tar`, `.tar.gz`, and `.tgz` archives and identifies executables automatically.

---

## Data & Storage Locations

SafeLauncher adheres to standard Linux XDG base directory specifications:

| Path | Contents |
| :--- | :--- |
| `~/.local/share/safelauncher/library.db` | SQLite database storing game metadata, launch configurations, and playtime counters. |
| `~/.local/share/safelauncher/logs/` | Runtime logs and sandbox launch diagnostics. |
| `~/.local/share/safelauncher/bin/` | App-managed external binaries (e.g. Ludusavi save detector). |
| `~/.cache/safelauncher/` | Cached SteamGridDB posters, hero banners, and extracted game icons. |
| `~/.config/SafeLauncher/` | Global launcher configuration and local user settings. |

---

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.
