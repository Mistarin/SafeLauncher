#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PARENT_DIR="$(cd "$ROOT_DIR/.." && pwd)"

echo "================================================================"
echo "         Deploying SafeLauncher Private Convex Backend          "
echo "================================================================"

# Locate server repository
SERVER_DIR=""
for candidate in \
    "$PARENT_DIR/SafeLauncherDatabase" \
    "$PARENT_DIR/SafeLauncherCloud" \
    "$HOME/Main/Programming/SafeLauncherDatabase" \
    "$HOME/Main/Programming/SafeLauncherCloud" \
    "$HOME/SafeLauncherCloud"; do
    if [ -d "$candidate" ] && { [ -f "$candidate/ImHereJustToExist.txt" ] || [ -f "$candidate/convex/schema.ts" ]; }; then
        SERVER_DIR="$candidate"
        break
    fi
done

if [ -z "$SERVER_DIR" ]; then
    echo "[Info] No local server folder found. Cloning SafeLauncherCloud..."
    SERVER_DIR="$PARENT_DIR/SafeLauncherCloud"
    git clone https://github.com/Mistarin/SafeLauncherCloud.git "$SERVER_DIR"
fi

echo "[Info] Server folder: $SERVER_DIR"
cd "$SERVER_DIR"

if ! command -v npm >/dev/null 2>&1; then
    echo "[Error] Node.js and npm are required to deploy Convex. Please install Node.js (e.g. sudo apt install nodejs npm)."
    exit 1
fi

echo "[Deploy] Installing server dependencies..."
npm install

echo "[Deploy] Deploying Convex backend functions..."
npx convex deploy

echo ""
echo "[Deploy] Backend deployed successfully."
echo "[Deploy] Running SafeLauncher cloud setup wizard..."
cd "$ROOT_DIR"
bash "$ROOT_DIR/setup/03-setup-cloud.sh"
