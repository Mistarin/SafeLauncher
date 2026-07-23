#!/bin/bash
# MGLauncher Wrapper Script with Error Logging

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/tmp/mglauncher_debug.log"

cd "$PROJECT_DIR"
exec /usr/bin/python3 "$PROJECT_DIR/main.py" > "$LOG_FILE" 2>&1
