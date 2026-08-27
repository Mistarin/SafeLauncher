#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="python3"
if [ -d "$ROOT_DIR/venv" ]; then
    PYTHON_BIN="$ROOT_DIR/venv/bin/python"
elif [ -d "$ROOT_DIR/.venv" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

exec "$PYTHON_BIN" "$ROOT_DIR/main.py" --setup-cloud "$@"
