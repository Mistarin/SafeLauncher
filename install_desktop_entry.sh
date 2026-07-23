#!/bin/bash
# Install MGLauncher Desktop Shortcut for Linux

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_FILE="$HOME/.local/share/applications/mglauncher.desktop"
ICON_PATH="$PROJECT_DIR/assets/logo.png"

# Find python binary that has required dependencies installed (PyQt6 & requests)
PYTHON_BIN=""

for candidate in "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/venv/bin/python" "$(which python3)" "$(which python)"; do
    if [ -x "$candidate" ]; then
        if "$candidate" -c "import PyQt6, requests" >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(which python3)"
fi

mkdir -p "$HOME/.local/share/applications"

cat <<EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=MGLauncher
Comment=Game Sandbox Launcher
Exec="$PYTHON_BIN" "$PROJECT_DIR/main.py"
Path=$PROJECT_DIR
Icon=$ICON_PATH
Terminal=false
Categories=Game;
Keywords=game;launcher;sandbox;firejail;steam;
StartupWMClass=MGLauncher
EOF

chmod +x "$DESKTOP_FILE"

# Re-index desktop entries so application menu updates immediately
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1
fi

echo "✓ MGLauncher desktop shortcut installed to $DESKTOP_FILE"
echo "  Using Python binary: $PYTHON_BIN"
