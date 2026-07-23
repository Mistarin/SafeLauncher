#!/bin/bash
# MGLauncher - Game Sandbox Launcher

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Find python binary that has required dependencies installed (PyQt6 & requests)
PYTHON_BIN=""

for candidate in "$DIR/.venv/bin/python" "$DIR/venv/bin/python" "$(which python3)" "$(which python)"; do
    if [ -x "$candidate" ]; then
        if "$candidate" -c "import PyQt6, requests" >/dev/null 2>&1; then
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
echo "MGLauncher started in background (PID: $!)."
