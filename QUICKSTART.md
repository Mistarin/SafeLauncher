# MGLauncher - Python Game Sandbox Launcher

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

✅ **Fully Functional PyQt6 GUI**
- Beautiful, intuitive interface for game management
- Double-click to launch games
- Add/remove games with dialog
- Import/export save files

✅ **Game Sandbox Integration**
- Firejail sandboxing for security
- Wine/UMU compatibility modes
- Automatic Wine prefix management

✅ **SQLite Database**
- Persistent game library storage
- Quick add/remove operations
- Game metadata tracking

✅ **Save Management**
- Export saves as ZIP archives
- Import saves back into games
- Automatic save directory detection

## Features

### 🎮 Game Management
- **Add Games**: Browse for game directory, set executable, choose launch mode
- **Launch Games**: Single click or double-click to launch in sandbox
- **Remove Games**: Delete games from library (game files preserved)

### 🛡️ Security
- Firejail sandboxing for Windows games
- Network isolation option (no network by default for UMU)
- Separate Wine prefixes per game

### 💾 Save Backup
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
MGLauncher/
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
   - **UMU**: Better compatibility for newer Windows games (requires UMU installed)
   - **Wine**: Classic Wine runner (more compatible)
6. Click **Add**

### Launching a Game

- **Option 1**: Double-click the game in the list
- **Option 2**: Select game and click **▶ Launch Selected Game**

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
- **Network isolation**: Default (no network) is safer but some games may need it

## Development

To contribute or modify the launcher:

1. Review the code structure (see File Structure above)
2. Modify `ui/main_window.py` for UI changes
3. Modify `core/firejail_runner.py` for launch behavior
4. Run tests: `python test.py`
5. Test GUI: `python main.py`

## License

Created for personal use. Modify and distribute as needed.

---

Enjoy your sandboxed gaming! 🎮
