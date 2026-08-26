#!/bin/bash
# SafeLauncher Wrapper Script with Error Logging

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer the project virtualenv (matches launcher.sh / Dev.txt instructions);
# a bare `python3` from PATH is the fallback of last resort.
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
elif [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/venv/bin/python"
else
    PYTHON="$(command -v python3 || true)"
fi

if [ -z "$PYTHON" ]; then
    echo "run_app.sh: no python3 interpreter found" >&2
    exit 1
fi

# Private debug log location inside XDG state; avoid predictable files in /tmp.
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/safelauncher"
mkdir -p -m 700 "$LOG_DIR"
LOG_FILE="$LOG_DIR/run_app.log"

cd "$PROJECT_DIR"
exec "$PYTHON" "$PROJECT_DIR/main.py" >> "$LOG_FILE" 2>&1
