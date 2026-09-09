#!/bin/bash
# SafeLauncher - Game Sandbox Launcher

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Find a Python binary with the dependencies imported during application
# startup.  Checking only PyQt6/requests lets a system interpreter win even
# though the cloud/crypto modules then fail with ModuleNotFoundError.
PYTHON_BIN=""

for candidate in "$DIR/.venv/bin/python" "$DIR/venv/bin/python" "$(which python3)" "$(which python)"; do
    if [ -x "$candidate" ]; then
        if "$candidate" -c "import PyQt6, requests, cryptography, PIL" >/dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

# Run detached in background without keeping terminal process attached
nohup "$PYTHON_BIN" "$DIR/main.py" >/dev/null 2>&1 &
echo "SafeLauncher started in background (PID: $!)."
