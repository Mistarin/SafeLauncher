# MGLauncher - Game Sandbox Launcher

A PyQt6-based GUI launcher for sandboxed games using Firejail. Manage your game library and launch games in isolated sandboxes with Wine/UMU support.

## Features

✨ **Game Library Management**
- Add games with custom paths and executables
- Remove games from library
- Launch games with a double-click or button

🎮 **Sandbox Support**
- UMU (Unified Multi-platform Utility) with Firejail
- Wine with Firejail
- No network isolation option available

💾 **Save Management**
- Export game saves as ZIP archives
- Import saves from ZIP archives
- Automatic save directory detection

🗄️ **Database**
- SQLite database for persistent game library
- Game metadata: name, path, executable, launch mode

## Requirements

- Python 3.9+
- PyQt6
- Firejail (for sandboxing)
- Wine or UMU (for running Windows games)

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Install System Requirements

**Ubuntu/Debian:**
```bash
sudo apt install firejail wine
```

**Fedora:**
```bash
sudo dnf install firejail wine
```

**Arch:**
```bash
sudo pacman -S firejail wine
```

## Usage

### Launch the Application

```bash
python main.py
```

### Add a Game

1. Click **Add Game** button
2. Enter game name
3. Click **Browse...** and select the game directory
4. Enter the executable filename (e.g., `game.exe`)
5. Select launch mode (UMU or Wine)
6. Click **Add**

### Launch a Game

- Double-click a game in the library, OR
- Select a game and click **▶ Launch Selected Game**

### Export Saves

1. Select a game
2. Click **💾 Export Save**
3. Choose location and filename
4. Save is packaged as ZIP

### Import Saves

1. Select a game
2. Click **📂 Import Save**
3. Select a ZIP file with save data
4. Save is extracted to game directory

## Project Structure

```
MGLauncher/
├── main.py                 # Entry point - launches PyQt6 app
├── database.py             # SQLite database management
├── requirements.txt        # Python dependencies
├── core/
│   ├── __init__.py
│   ├── interfaces.py       # Abstract base classes
│   ├── firejail_runner.py  # Sandbox execution
│   └── zip_backup.py       # Save import/export
└── ui/
    ├── __init__.py
    └── main_window.py      # PyQt6 UI components
```

## Configuration

Games are stored in an SQLite database (`library.db`) in the project directory.

## Troubleshooting

**Firejail Permission Denied:**
```bash
sudo chmod u+s /usr/bin/firejail
```

**Wine Prefix Issues:**
Games automatically create Wine prefix in `<game_path>/prefix`

**UMU Not Found:**
Install UMU from the official repository or use Wine mode instead

## License

Created for personal use
