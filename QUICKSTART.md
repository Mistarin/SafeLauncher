# SafeLauncher - Python Game Sandbox Launcher

## Quick Start

### 1. Install Requirements
```bash
pip install -r requirements.txt
```

### 2. Run the Launcher
```bash
python main.py
```

Or use the included launcher script:
```bash
bash launcher.sh
```

## Private Cloud Saves (optional)

Saves sync to a local folder by default. To store them encrypted on your own private Convex backend:

1. Run the interactive setup wizard:
   ```bash
   ./SafeLauncher-x86_64.AppImage --setup-cloud
   ```
   or in SafeLauncher: **Settings → Cloud → Setup Wizard…**
2. Deploy or connect your private Convex instance (1 GB free storage on Convex without monthly fees).
3. Optionally set a secret key (`SAFELAUNCHER_SECRET_KEY`) for secure single-tenant access.

Each save upload is AES-256-GCM encrypted on your PC (up to the 1 GB free storage tier, with referral-based expansion available, and two generations retained per game: the active save plus one backup).

## Public profiles (optional)

The profile page is available from `View → Profile` or the user icon in the title bar. Sign in with the central Auth0 account, choose an avatar/background, and publish. SafeLauncher generates a public handle and stores only the rotating Auth0 refresh token in the OS credential store. Other users can open the handle from `Open Public Profile…`; they receive a read-only view.

The public profile is derived from the local account profile and never includes game paths, executable names, devices, cloud credentials, or private save state. The developer-operated Convex service in `services/profile_cloud` is reached through the production Vercel gateway at `https://profilegateway.vercel.app`; the raw Convex URL and gateway secret stay server-side. Do not deploy the profile service into a personal save backend.

Friends use the same public-profile service: share the generated handle, open the other profile, and choose `Add friend`. Requests require acceptance and friend lists remain private to each owner. SafeLauncher clients do not connect Convex deployments directly to one another; authenticated social writes use the central Auth0 session.


## What's Included

- **PyQt6 GUI**: Desktop interface with game list, add/remove dialogs, and save backup tools.
- **Game Sandbox Integration**: Firejail sandboxing, Wine/UMU compatibility modes, and automatic prefix management.
- **SQLite Database**: Persistent game library storage and metadata tracking.
- **Save Management**: Export and import saves as ZIP archives with automatic directory detection.

## Features

### Game Management
- **Add Games**: Browse for a game directory, set the executable, and choose a runner mode
- **Install from Archive**: Install a game from a ZIP, RAR, 7z, TAR, TAR.GZ, or TGZ archive
- **Launch Games**: Select a game and click **Launch Game**, or double-click it
- **Archive Games**: Remove games from the active library while preserving files and history
- **Permanently Delete**: Delete game files and launcher records after confirmation

### Security
- Firejail sandboxing for Windows games
- Native Linux and legacy Wine modes run without network access (`--net=none`)
- UMU/Proton launches currently have full host network access — treat them as online
- Separate Wine prefixes per game

“Sandboxed” describes process and filesystem isolation. It does not mean network isolation for UMU/Proton launches.

### Save Backup
- Export game saves to ZIP files
- Import saves from ZIP archives
- Backup and restore across systems

## System Requirements

- Python 3.9+
- PyQt6
- Firejail
- Wine or UMU runtime

### Installation on Linux

**Ubuntu/Debian:**
```bash
sudo apt install python3-pip firejail wine
```

**Fedora:**
```bash
sudo dnf install python3-pip firejail wine
```

**Arch:**
```bash
sudo pacman -S python firejail wine
```

## File Structure

```
SafeLauncher/
├── main.py                 # Entry point
├── database.py             # Game library database
├── launcher.sh             # Bash launcher script
├── test.py                 # Component tests
├── requirements.txt        # Python dependencies
├── README.md               # Full documentation
├── QUICKSTART.md           # This file
├── library.db              # SQLite database (created on first run)
├── core/
│   ├── interfaces.py       # Abstract interfaces
│   ├── firejail_runner.py  # Sandbox runner
│   └── zip_backup.py       # Save backup system
└── ui/
    └── main_window.py      # PyQt6 UI components
```

## Usage Guide

### Adding a Game

1. Click **➕ Add Game** button
2. Enter game name (e.g., "Portal 2")
3. Click **Browse...** and select the game directory
4. Enter the executable name (e.g., "portal2.exe")
5. Select launch mode:
   - **UMU – Standard (networked)**: Recommended path for Windows games via Proton. Note: currently has full host network access.
   - **UMU – Network Enabled (alias)**: Identical to UMU Standard; kept for compatibility.
   - **Wine – Legacy (offline)**: Runs directly with system Wine, sandboxed without network.
6. Click **Add**

### Launching a Game

- **Option 1**: Double-click the game in the list
- **Option 2**: Select a game and click **Launch Game**

The game will launch in a Firejail sandbox with:
- Limited filesystem access (only the game directory)
- Optional network isolation
- Isolated Wine prefix (saves don't affect other games)

### Managing Saves

#### Export (Backup)
1. Select a game
2. Click **💾 Export Save**
3. Choose filename and location
4. Save is packaged as ZIP

#### Import (Restore)
1. Select a game
2. Click **📂 Import Save**
3. Select a ZIP file
4. Save is restored to game directory

## Troubleshooting

### Firejail: "Operation not permitted"
```bash
sudo chmod u+s /usr/bin/firejail
```

### Wine: WINEPREFIX errors
- Ensure you have write permissions to the game directory
- First launch may take longer while Wine initializes

### UMU Not Found
- Install UMU: https://github.com/Open-Wine-Components/umu-launcher
- Or use Wine mode instead

### Game Won't Launch
- Verify the executable path is correct
- Try Wine mode instead of UMU
- Check game directory permissions
- Ensure game files aren't corrupted

## Tips & Tricks

- **Backup saves regularly**: Use the export feature to create backups
- **Test launch mode**: UMU and Wine have different compatibility levels
- **Check game logs**: Wine logs are in `<game_path>/prefix/drive_c/windows/temp`
- **Network access**: UMU launches are online; use Wine/Native Linux modes for network isolation

## Development

To contribute or modify the launcher:

1. Review the code structure (see File Structure above)
2. Modify `ui/main_window.py` for UI changes
3. Modify `core/firejail_runner.py` for launch behavior
4. Run tests: `python test.py`
5. Test GUI: `python main.py`

When playing without a network connection, enable **Settings → Cloud → Offline
mode** before disconnecting. SafeLauncher then uses local/cache data and does
not start automatic artwork, Steam, cloud, profile, telemetry, or update
requests. The same switch is available for scripts as
`python main.py --offline` (also supported by `./run_app.sh --offline` and
`./launcher.sh --offline`).

## License

Created for personal use. Modify and distribute as needed.
