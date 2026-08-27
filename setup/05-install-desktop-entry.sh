#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"

mkdir -p "$APPS_DIR" "$ICONS_DIR"

echo "[Desktop] Installing SafeLauncher icon..."
cp "$ROOT_DIR/assets/logo.png" "$ICONS_DIR/safelauncher.png"

echo "[Desktop] Generating desktop entry..."
cat << DESKTOP > "$APPS_DIR/safelauncher.desktop"
[Desktop Entry]
Name=SafeLauncher
Comment=Secure, high-performance game sandboxing for Linux
Exec=$ROOT_DIR/setup/02-launch.sh %U
Icon=safelauncher
Terminal=false
Type=Application
Categories=Game;Utility;
StartupWMClass=SafeLauncher
MimeType=x-scheme-handler/safelauncher;
DESKTOP

chmod +x "$APPS_DIR/safelauncher.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" || true
fi

echo "[Desktop] Installed desktop entry to $APPS_DIR/safelauncher.desktop"
