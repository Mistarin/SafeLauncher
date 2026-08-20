# SafeLauncher

### Secure, high-performance game sandboxing for Linux.

SafeLauncher is a purpose-built desktop environment designed to run Windows and Linux games inside strictly isolated sandboxes without sacrificing performance. By fusing the kernel-level isolation of Firejail with modern Proton, UMU, and Wine execution layers, SafeLauncher delivers complete containment, automated artwork curation, and intelligent save management wrapped in a refined interface.

---

## Overview

Traditional gaming setups on Linux grant executables broad access to your user home directory, background processes, and local network. SafeLauncher redefines this paradigm by placing each game inside an isolated sandbox, ensuring personal data, credentials, and system files remain completely untouched.

From dynamic Proton discovery and SteamGridDB artwork syncing to one-click save backup archives and Discord Rich Presence, every subsystem is engineered to deliver a seamless experience.

---

## Key Features

### Kernel-Level Sandbox Isolation
* **Zero-Leak Process Containment**: Enforces strict Firejail sandboxing policies across game processes, background helper daemons, and Wine subprocesses.
* **Network Access Modes**: Toggle between fully network-isolated offline play (blocking telemetry and outbound calls) and bridged networking for multiplayer titles.
* **Prefix Isolation & Hygiene**: Automates isolated per-game Wine and Proton prefixes, preventing cross-contamination and clutter across your system.
* **Security & Launch Diagnostics**: Built-in runtime auditing engine scans sandbox configurations, evaluates access permissions, and generates structured diagnostic logs.

### Compatibility & Runtime Engine
* **Universal Runner Support**: Native compatibility with UMU (Unified Multi-platform Utility), Valve Proton, Proton-GE, and custom Wine distributions.
* **Automated Runtime Inventory**: Automatically discovers installed Steam runtimes, Proton versions, and system Wine binaries.
* **Performance Enhancements**: Integrated support for Feral GameMode, Gamescope micro-compositor, and MangoHud performance overlays.
* **Custom Environment & Launch Flags**: Fine-tune per-game environment variables, DLL overrides, and custom sandbox arguments via an intuitive properties inspector.

### Intelligent Library & Metadata Management
* **Automated Artwork Sync**: Integrates with SteamGridDB to automatically pull high-resolution grid posters, dynamic hero banners, logos, and icon assets.
* **Embedded Icon Extraction**: Native Windows Portable Executable (PE) binary inspection automatically extracts high-resolution icons directly from `.exe` files.
* **Gameplay Insights**: Integrated HowLongToBeat (HLTB) queries provide main story, extra, and completionist playtime estimations directly within the library.
* **Steam Tagging & Organization**: Automated Steam genre and categorization tags with customizable sorting, filtering, search, and favorites.

### Archive Installation & Save State Lifecycle
* **Direct Archive Installer**: Install games directly from compressed archives (`.zip`, `.7z`, `.tar`, `.tar.gz`, `.tgz`) with automatic extraction and structure normalization.
* **Snapshot Save Management**: Export and import entire save states as compressed ZIP archives with automatic save path detection across standard Windows and Wine paths.
* **Safe State Restoration**: Inspect and restore historical backup snapshots without risking save corruption or data loss.

### Desktop & System Integration
* **Single-Instance Architecture**: Instant IPC socket communication ensures quick wake-and-focus behavior when launched repeatedly.
* **Discord Rich Presence**: Real-time Discord status updates reflecting current game titles, session durations, and custom presence assets.
* **GPU Screen Recording & Screenshots**: Integration with modern capture utilities and in-game screenshot galleries.
* **Native Desktop Shortcuts**: Generate standards-compliant `.desktop` application menu entries to launch sandboxed titles directly from your system launcher.

---

## Installation

### Method 1: AppImage (Recommended)

SafeLauncher is distributed as a standalone, zero-dependency AppImage containing Python, PyQt6, and all core dependencies.

1. Download the latest `SafeLauncher-x86_64.AppImage` from the releases page.
2. Grant execution permissions:
   ```bash
   chmod +x SafeLauncher-x86_64.AppImage
   ```
3. Run the application:
   ```bash
   ./SafeLauncher-x86_64.AppImage
   ```

#### Host Prerequisites
SafeLauncher manages sandboxing and compatibility layers using standard Linux host utilities. Install Firejail and your preferred compatibility runner:

* **Ubuntu / Debian**:
  ```bash
  sudo apt install firejail wine
  ```
* **Fedora**:
  ```bash
  sudo dnf install firejail wine
  ```
* **Arch Linux**:
  ```bash
  sudo pacman -S firejail wine
  ```
* **openSUSE**:
  ```bash
  sudo zypper install firejail wine
  ```

---

### Method 2: Running from Source

For development or direct customization:

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Mistarin/SafeLauncher.git
   cd SafeLauncher
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Launch SafeLauncher**:
   ```bash
   python main.py
   ```

5. **Install system desktop shortcut (Optional)**:
   ```bash
   ./install_desktop_entry.sh
   ```

---

### Method 3: Building the AppImage with Docker

You can build a clean, reproducible AppImage using the automated Docker build toolchain:

```bash
./packaging/build-appimage-docker.sh
```

The resulting standalone binary will be placed at `dist/SafeLauncher-x86_64.AppImage`.

---

## Getting Started

### Adding a Game

1. Launch SafeLauncher and select **Add Game** in the toolbar.
2. Enter the game title and select the installation directory.
3. Select the target executable (`.exe` or Linux binary).
4. Choose the preferred runner (UMU, Proton, or Wine) and set the network isolation mode.
5. Click **Add** to finalize. SafeLauncher will automatically fetch artwork and prepare the sandbox environment.

### Installing from an Archive

1. Select **Install from Archive**.
2. Browse to any supported archive file (`.zip`, `.7z`, `.tar.gz`).
3. Designate the target installation directory. SafeLauncher extracts the contents, discovers executable candidates, and populates metadata automatically.

### Managing Save States

* **Export Saves**: Select a game, click **Export Save**, and choose a destination path. SafeLauncher compresses the prefix save directory into a portable `.zip` snapshot.
* **Import Saves**: Select a game, click **Import Save**, and select a previously exported `.zip` file. SafeLauncher validates and extracts the save files into the isolated prefix.

---

## Architecture & Storage

SafeLauncher follows the XDG Base Directory Specification to ensure full separation between configuration, database states, and cached assets:

| Path | Description |
| :--- | :--- |
| `~/.local/share/safelauncher/library.db` | Primary SQLite database storing game metadata, configurations, and playtime metrics. |
| `~/.local/share/safelauncher/logs/` | Structured runtime and sandbox diagnostic logs. |
| `~/.cache/safelauncher/` | Cached SteamGridDB posters, hero banners, logos, and extracted icons. |
| `~/.config/safelauncher/` | Global application preferences and API keys. |

---

## Troubleshooting

#### Firejail SUID Permission
If your distribution requires elevated permissions for Firejail namespace creation:
```bash
sudo chmod u+s /usr/bin/firejail
```

#### Proton / UMU Path Discovery
Ensure Steam or UMU is installed on your host system. SafeLauncher searches standard Steam library locations (`~/.steam/steam`, `~/.local/share/Steam`, and Flatpak Steam paths) to auto-detect Proton versions. Custom Proton paths can be specified in Global Settings.

#### MangoHud / GameMode Integration
To enable performance overlays and priority scheduling, ensure `mangohud` and `gamemode` packages are installed on your host system.

---

## License

Distributed under the MIT License. See `LICENSE` for details.
