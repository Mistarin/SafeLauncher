# MGLauncher - UI Components & Workflow

## Main Window UI

```
┌─────────────────────────────────────────────────────┐
│  Game Sandbox Launcher                          [_□×] │
├─────────────────────────────────────────────────────┤
│                                                       │
│  Game Library                                         │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │ 🎮 Portal 2 (WINE)                              │ │
│  │ 🎮 Starfield (UMU)                              │ │
│  │ 🎮 Baldur's Gate 3 (UMU)                        │ │
│  │                                                  │ │
│  │                                                  │ │
│  │                                                  │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │ ▶ Launch Selected Game │ ➕ Add Game │ 🗑️ Remove  │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │ 💾 Export Save │ 📂 Import Save                  │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
└─────────────────────────────────────────────────────┘
```

## Add Game Dialog

```
┌──────────────────────────────────────────┐
│  Add Game                            [×] │
├──────────────────────────────────────────┤
│                                          │
│  Game Name:        [Portal 2________]   │
│                                          │
│  Game Path:        [/home/user/Portal2] │
│                    [Browse...]           │
│                                          │
│  Executable:       [portal2.exe____]    │
│                                          │
│  Launch Mode:      [▼ wine            ] │
│                    ├─ umu               │
│                    └─ wine              │
│                                          │
│  ┌──────────────────────────────────┐  │
│  │     [Add]        [Cancel]         │  │
│  └──────────────────────────────────┘  │
│                                          │
└──────────────────────────────────────────┘
```

---

## User Workflows

### Workflow 1: Adding a Game

```
User Clicks "Add Game"
         ↓
AddGameDialog Opens
         ↓
User Enters Game Name
         ↓
User Clicks "Browse..." → Selects Game Folder
         ↓
User Enters Executable Name
         ↓
User Selects Launch Mode
         ↓
User Clicks "Add"
         ↓
Database Saves Game
         ↓
Game List Refreshed
         ↓
Success Message Shown
```

### Workflow 2: Launching a Game

```
User Selects Game from List
         ↓
User Double-Clicks OR Clicks "Launch"
         ↓
Game Object Retrieved from Database
         ↓
FirejailSandboxRunner.launch() Called
         ↓
Firejail Command Constructed
         ↓
Game Subprocess Started
         ↓
User Sees "Launching..." Message
         ↓
Game Runs in Sandbox
         ↓
User Plays Game!
```

### Workflow 3: Exporting Save

```
User Selects Game from List
         ↓
User Clicks "Export Save"
         ↓
Save Directory Located
         ↓
File Save Dialog Shown
         ↓
User Chooses Location & Filename
         ↓
ZipBackupManager.export_save() Called
         ↓
Game Save Compressed to ZIP
         ↓
ZIP File Saved to Disk
         ↓
Success Message Shown
```

### Workflow 4: Importing Save

```
User Selects Game from List
         ↓
User Clicks "Import Save"
         ↓
File Open Dialog Shown
         ↓
User Selects ZIP File
         ↓
ZipBackupManager.import_save() Called
         ↓
ZIP File Extracted to Save Directory
         ↓
Success Message Shown
         ↓
Game Files Updated
```

---

## Component Interactions

```
┌─────────────┐
│   PyQt6     │  Main window, dialogs, buttons
│   (UI)      │
└──────┬──────┘
       │ Uses
       ↓
┌──────────────────┐
│  GameDatabase    │  SQLite game library
│  (database.py)   │
└──────────────────┘
       ↑
       │ Manages

┌──────────────────────────────────┐
│  MainWindow (main_window.py)     │
│  - Displays game list             │
│  - Manages user interactions      │
│  - Coordinates with backend       │
└──────────────────┬───────────────┘
                   │
        ┌──────────┼──────────┐
        ↓          ↓          ↓
┌──────────────┐  ┌──────────────────┐  ┌──────────────┐
│ FirejailSand│  │ ZipBackupManager  │  │GameDatabase  │
│   boxRunner │  │ (zip_backup.py)   │  │(database.py) │
│(firejail_   │  │                   │  │              │
│ runner.py)  │  │- Export saves     │  │- Add game    │
│             │  │- Import saves     │  │- Remove game │
│- Launch in  │  │- ZIP compression  │  │- Get all     │
│  sandbox    │  │- ZIP extraction   │  │              │
│- Firejail   │  └──────────────────┘  └──────────────┘
│  commands   │
│- Wine/UMU   │
│  support    │
└──────────────┘
```

---

## Error Handling & User Feedback

All operations provide user feedback:

```
Success Scenarios:
  ✓ Game added successfully
  ✓ Game launched (with message)
  ✓ Save exported successfully
  ✓ Save imported successfully
  ✓ Game removed from library

Error Scenarios:
  ✗ All fields required (Add Game)
  ✗ Invalid game path (Add Game)
  ✗ No game selected (Launch/Remove/Export/Import)
  ✗ Save directory not found (Export)
  ✗ Failed to export save (Export)
  ✗ Failed to import save (Import)
  ✗ Failed to launch game (Launch)

Confirmation Dialogs:
  ? Remove game from library?
    - Yes (Removes entry, keeps game files)
    - No (Cancels operation)
```

---

## Database Schema

```sql
CREATE TABLE games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,          -- Game display name
    path TEXT NOT NULL,           -- Game directory path
    executable TEXT NOT NULL,     -- Executable filename
    mode TEXT NOT NULL            -- Launch mode: "umu" or "wine"
)
```

**Example Record:**
```
id: 1
name: Portal 2
path: /home/user/Games/Portal2
executable: portal2.exe
mode: wine
```

---

## Launch Modes Explained

### UMU (Unified Multi-platform Utility)
```bash
cd '<game_path>' && firejail --ignore=noroot --ignore=seccomp \
  --net=none --whitelist='<game_path>' \
  --whitelist='$HOME/.local/share/umu' \
  --whitelist='$HOME/.cache/umu' \
  --env=WINEPREFIX='<game_path>/prefix' \
  --env=WINEDLLOVERRIDES='winegstreamer=' umu-run '<executable>'
```
- Better compatibility with newer games
- Requires UMU installed
- No network by default
- Modern approach

### Wine
```bash
cd '<game_path>' && firejail --net=none \
  --whitelist='<game_path>' \
  --env=WINEPREFIX='<game_path>/prefix' wine '<executable>'
```
- Broader game compatibility
- No dependencies beyond Wine
- No network by default
- Classic approach

---

## PyQt6 Components Used

| Component | Purpose |
|-----------|---------|
| `QMainWindow` | Main application window |
| `QWidget` | Central widget container |
| `QVBoxLayout` / `QHBoxLayout` | Layout management |
| `QPushButton` | Clickable buttons |
| `QListWidget` | Game list display |
| `QListWidgetItem` | Individual list items |
| `QFileDialog` | Browse folders/files |
| `QMessageBox` | Notifications & confirmations |
| `QDialog` | Add game dialog |
| `QLabel` | Text labels |
| `QLineEdit` | Text input fields |
| `QComboBox` | Dropdown selection |
| `QFormLayout` | Form structure |

---

## File Permissions

The application needs:
- Read/write access to `library.db`
- Execute permission on `launcher.sh`
- Read access to game directories
- Write access to game `prefix/` directories

If Firejail shows permission errors:
```bash
sudo chmod u+s /usr/bin/firejail
```

---

## Next Steps for Enhancement

Potential future improvements:
- Game cover images
- Play time tracking
- Installation wizard
- Cloud sync for saves
- Custom launch parameters per game
- Game rating/notes
- Recent games list
- Search/filter functionality
- Pro controller support
- Achievement tracking

---

Created with PyQt6 for seamless game management! 🎮✨
