#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "================================================================"
echo "          Building SafeLauncher Standalone AppImage             "
echo "================================================================"

if command -v docker >/dev/null 2>&1; then
    echo "[Build] Docker detected. Building reproducible AppImage via container..."
    bash "$ROOT_DIR/packaging/build-appimage-docker.sh"
else
    echo "[Build] Docker not found. Attempting local AppImage build..."
    PYTHON_BIN="python3"
    if [ -d "$ROOT_DIR/.venv" ]; then
        PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
    fi
    PYTHON_BIN="$PYTHON_BIN" bash "$ROOT_DIR/packaging/build-appimage.sh"
fi

if [ -f "$ROOT_DIR/dist/SafeLauncher-x86_64.AppImage" ]; then
    echo ""
    echo "================================================================"
    echo "✔ Success: AppImage built at dist/SafeLauncher-x86_64.AppImage"
    echo "================================================================"
fi
