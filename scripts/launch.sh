#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 1. Virtual environment selection
PYTHON_BIN="python3"
if [ -d "$ROOT_DIR/venv" ]; then
    PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif [ -d "$ROOT_DIR/.venv" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

# 2. Dependency verification
echo "[SafeLauncher] Checking environment and requirements..."
if ! "$PYTHON_BIN" -c "import PyQt6, requests, cryptography, PIL" >/dev/null 2>&1; then
    echo "[SafeLauncher] Missing dependencies detected. Installing requirements..."
    if [ ! -d "$ROOT_DIR/.venv" ] && [ "$PYTHON_BIN" = "python3" ]; then
        echo "[SafeLauncher] Creating isolated virtual environment in .venv..."
        python3 -m venv "$ROOT_DIR/.venv"
        PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
    fi
    "$PYTHON_BIN" -m pip install --upgrade pip
    "$PYTHON_BIN" -m pip install -r "$ROOT_DIR/requirements.txt"
    echo "[SafeLauncher] Requirements installed successfully."
fi

# 3. Launch application
echo "[SafeLauncher] Starting SafeLauncher..."
exec "$PYTHON_BIN" "$ROOT_DIR/main.py" "$@"
