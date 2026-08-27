# SafeLauncher

### Secure, high-performance game sandboxing for Linux.

[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey.svg)](https://www.kernel.org)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

<img width="2560" height="1440" alt="obrazek" src="https://github.com/user-attachments/assets/0c776ac5-fcd6-4ef1-b236-3b81d6d117ff" />

SafeLauncher runs Windows and Linux games in separate Firejail sandboxes while keeping Proton, UMU, Wine, artwork, saves, and desktop integration in one place. Each game gets its own prefix and launch policy, so you can control what it can access and whether it can reach the network.

---

## Overview

Games often run with more access to your home directory and network than they need. SafeLauncher gives each title its own sandbox, prefix, and launch settings, and can run network-free for games that do not need connectivity.

It also handles Proton discovery, SteamGridDB artwork, save snapshots, archive installation, and Discord Rich Presence from the same library.

---

## Key Features

### Kernel-Level Sandbox Isolation
* **Sandboxed launches**: Runs game processes, Wine helpers, and related tools through Firejail.
* **Network isolation**: Native Linux and legacy Wine modes run with `--net=none` (no network). UMU/Proton launches currently have full host network access, so treat them as online.
* **Per-game prefixes**: Creates an isolated Wine or Proton prefix for each game.
* **Launch diagnostics**: Audits sandbox settings and records structured runtime logs.

### Compatibility & Runtime Engine
* **Runner support**: Use UMU, Valve Proton, Proton-GE, system Wine, or custom Wine and Proton paths.
* **Runtime discovery**: Finds installed Steam runtimes, Proton versions, and Wine binaries.
* **Performance tools**: Works with Feral GameMode, Gamescope, and MangoHud.
* **Per-game settings**: Set environment variables, DLL overrides, and extra sandbox arguments.

### Library and Metadata
* **Artwork sync**: Pulls posters, hero banners, logos, and icons from SteamGridDB.
* **Icon extraction**: Extracts icons from Windows `.exe` files.
* **Library organization**: Supports Steam tags, search, sorting, filters, and favorites.

### Archive Installation and Saves
* **Archive installation**: Installs games from `.zip`, `.7z`, `.tar`, `.tar.gz`, and `.tgz` archives, then finds likely executables.
* **Save snapshots**: Exports and imports save data as ZIP archives and detects common Windows and Wine save paths.
* **Snapshot history**: Inspect and restore previous backups.

### Desktop & System Integration
* **Single instance**: Uses a local IPC socket to bring the existing window into focus when launched again.
* **Discord Rich Presence**: Shows the current game and session duration.
* **Capture integration**: Works with screen recording and screenshot tools available on the host.
* **Desktop shortcuts**: Creates `.desktop` entries for launching games from the application menu.

---

## Installation

### Method 1: AppImage (Recommended)

The AppImage bundles SafeLauncher and its Python dependencies. Firejail and your chosen compatibility runner still need to be installed on the host.

1. Download the latest [SafeLauncher-x86_64.AppImage](https://github.com/Mistarin/SafeLauncher/releases/latest).
2. Grant execution permissions:
   ```bash
   chmod +x SafeLauncher-x86_64.AppImage
   ```
3. Run the application:
   ```bash
   ./SafeLauncher-x86_64.AppImage
   ```

#### Host prerequisites
Install Firejail and Wine, or another supported runner such as Proton or UMU:

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

For development or customization:

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

The Docker build script creates a reproducible AppImage:

```bash
./packaging/build-appimage-docker.sh
```

The resulting binary is placed at `dist/SafeLauncher-x86_64.AppImage`.

---

## Getting Started

### Adding a Game

1. Launch SafeLauncher and select **Add Game** in the toolbar.
2. Enter the game title and select the installation directory.
3. Select the target executable (`.exe` or Linux binary).
4. Choose the preferred runner (UMU, Proton, or Wine) and set the network isolation mode.
5. Click **Add**. SafeLauncher prepares the sandbox and fetches artwork when available.

### Installing from an Archive

1. Select **Install from Archive**.
2. Browse to any supported archive file (`.zip`, `.7z`, `.tar.gz`).
3. Choose the installation directory. SafeLauncher extracts the files, finds likely executables, and creates the game entry.

### Managing Save States & Private Cloud Sync

* **Automatic Save Detection**: SafeLauncher automatically discovers save game directories across Wine/UMU prefixes and native Linux folders using Ludusavi heuristics.
* **Local Snapshots**: Export and import save snapshots as compressed, tamper-safe ZIP archives.
* **Private Cloud Saves (Self-Hosted Convex)**: Sync game saves with zero-knowledge AES-256-GCM encryption to your own private Convex backend.
  * Backend repo: [SafeLauncherCloud](https://github.com/Mistarin/SafeLauncherCloud.git)
  * Set up your private cloud in 2 minutes:
    ```bash
    safelauncher --setup-cloud
    ```
  * Free 1 GB cloud storage via Convex free tier with 3-generation version rollback and conflict resolution.

---

## Architecture & Storage

SafeLauncher stores its data in the standard XDG directories:

| Path | Description |
| :--- | :--- |
| `~/.local/share/safelauncher/library.db` | SQLite database with game metadata, settings, and playtime. |
| `~/.local/share/safelauncher/logs/` | Runtime and sandbox diagnostics. |
| `~/.cache/safelauncher/` | Downloaded artwork and extracted icons. |
| `~/.config/safelauncher/` | Global settings and API keys. |

---

## Troubleshooting

#### Firejail SUID Permission
Some distributions require the Firejail SUID bit for namespace creation:
```bash
sudo chmod u+s /usr/bin/firejail
```

Only apply this if your distribution's Firejail setup requires it.

#### Proton / UMU Path Discovery
SafeLauncher checks common Steam locations, including `~/.steam/steam`, `~/.local/share/Steam`, and Flatpak Steam paths. Set a custom Proton or UMU path in Global Settings if automatic discovery does not find it.

#### MangoHud / GameMode Integration
Install `mangohud` and `gamemode` through your distribution's package manager to enable these integrations.

---

## License

Distributed under the MIT License. See `LICENSE` for details.
