# SafeLauncher - Complete Setup Summary

## Project Overview

SafeLauncher is a desktop game sandbox launcher built with PyQt6, SQLite, and Firejail.

---

## Project Structure

```
SafeLauncher/
├── main.py                    # Main entry point - launches PyQt6 GUI
├── database.py                # SQLite game library database
├── launcher.sh                # Bash launcher script (executable)
├── test.py                    # Component verification tests
├── requirements.txt           # Python dependencies
├── README.md                  # Full documentation
├── QUICKSTART.md              # Quick start guide
├── .gitignore                 # Git ignore patterns
├── library.db                 # SQLite database (auto-created)
│
├── core/                      # Core functionality
│   ├── __init__.py
│   ├── interfaces.py          # Abstract base classes
│   ├── firejail_runner.py     # Sandbox execution engine
│   └── zip_backup.py          # Save backup system
│
└── ui/                        # User interface
    ├── __init__.py
    └── main_window.py         # PyQt6 GUI components
```

---

## Quick Start

### 1. Install Python Dependencies
```bash
cd /home/martin/Main/Programming/SafeLauncher
pip install -r requirements.txt
```

### 2. Launch the Application
```bash
python main.py
```

Or use the launcher script:
```bash
bash launcher.sh
```

---

## Features

### PyQt6 GUI
- Game library list view
- Add game dialog with directory browser
- Double-click to launch
- Visual game selection

### Game Management
- Add games (name, path, executable, mode)
- Remove games from library
- Launch in Firejail sandbox
- Support for UMU and Wine modes

### Save Management
- Export game saves to ZIP archives
- Import saves from ZIP files
- Automatic save directory detection

### Database
- SQLite for persistent storage
- Automatic schema creation
- Game metadata management

### Security
- Firejail sandboxing
- Network isolation available
- Separate Wine prefixes per game

---

## Usage

### Adding a Game
1. Click **Add Game**
2. Enter game name
3. Click **Browse...** to select game folder
4. Enter executable filename (e.g., `game.exe`)
5. Choose mode (UMU or Wine)
6. Click **Add**

### Launching a Game
- **Option 1:** Double-click game in list
- **Option 2:** Select game and click **Launch**

### Managing Saves
- **Export:** Select game → **Export Save** → Choose location
- **Import:** Select game → **Import Save** → Choose ZIP file

---

## System Requirements

- Python 3.10+
- PyQt6
- Firejail
- Wine
- UMU (optional, for Windows compatibility)

---

## Verification

Run component tests anytime:
```bash
python test.py
```

---

## Documentation Files

- **README.md** - Complete documentation with features, requirements, and troubleshooting
- **QUICKSTART.md** - Quick start guide with usage examples
- **SETUP_COMPLETE.md** - Project setup summary

---

## Project Files Explained

| File | Purpose |
|------|---------|
| `main.py` | Entry point - initializes and launches PyQt6 app |
| `database.py` | SQLite database for storing game library |
| `core/interfaces.py` | Abstract base classes (ISandboxRunner, IBackupManager) |
| `core/firejail_runner.py` | Firejail sandbox execution implementation |
| `core/zip_backup.py` | Save export/import functionality |
| `ui/main_window.py` | PyQt6 GUI implementation |
| `launcher.sh` | Bash launcher script |
| `test.py` | Component verification tests |

---

## Technologies Used

- **PyQt6** - GUI framework
- **SQLite3** - Local database storage
- **Firejail** - Sandbox security
- **Wine / UMU** - Windows compatibility runtimes
- **Python 3** - Runtime

---

## Tips

- Test with a small game first to verify execution.
- Wine prefixes are created automatically in the game directory.
- Save files are located in `<game_path>/prefix/drive_c/users/`.
- Use the export feature to back up saves before modifying prefixes.
- Check README.md for troubleshooting details.
