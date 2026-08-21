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

## What's Included

- **PyQt6 GUI**: Desktop interface with game list, add/remove dialogs, and save backup tools.
- **Game Sandbox Integration**: Firejail sandboxing, Wine/UMU compatibility modes, and automatic prefix management.
- **SQLite Database**: Persistent game library storage and metadata tracking.
- **Save Management**: Export and import saves as ZIP archives with automatic directory detection.

## Features

### Game Management
- **Add Games**: Browse for a game directory, set the executable, and choose a runner mode
- **Install from Archive**: Install a game from a ZIP, 7z, TAR, TAR.GZ, or TGZ archive
- **Launch Games**: Select a game and click **Launch Game**, or double-click it
- **Remove Games**: Delete games from library (game files preserved)

### Security
- Firejail sandboxing for Windows games
- Offline mode with network access disabled
- Network-enabled mode for games that need online features
- Separate Wine prefixes per game

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
   - **UMU – Offline**: Recommended for Windows games that do not need internet access
   - **UMU – Network Enabled**: For games that need online features
   - **Wine – Legacy**: Runs directly with system Wine
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
- **Network access**: Offline mode is safer; use Network Enabled mode when a game needs internet access

## Development

To contribute or modify the launcher:

1. Review the code structure (see File Structure above)
2. Modify `ui/main_window.py` for UI changes
3. Modify `core/firejail_runner.py` for launch behavior
4. Run tests: `python test.py`
5. Test GUI: `python main.py`

## License

Created for personal use. Modify and distribute as needed.
