#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT_DIR/dist"

# The image build needs outbound access for apt, PyPI, and appimagetool.  The
# final appimagetool step also downloads the AppImage runtime. Some Linux
# kernels (including minimal/locked-down kernels without veth support) cannot
# create Docker's default bridge endpoint, so both phases use the host network
# namespace. The image is only used as a build environment.
DOCKER_BUILD_NETWORK="${DOCKER_BUILD_NETWORK:-host}"
DOCKER_RUN_NETWORK="${DOCKER_RUN_NETWORK:-host}"

docker build \
    --network="$DOCKER_BUILD_NETWORK" \
    -f "$ROOT_DIR/packaging/Dockerfile" \
    -t safelauncher-appimage-builder \
    "$ROOT_DIR"
docker run \
    --rm \
    --network="$DOCKER_RUN_NETWORK" \
    -v "$ROOT_DIR/dist:/out" \
    safelauncher-appimage-builder

echo "Built $ROOT_DIR/dist/SafeLauncher-x86_64.AppImage"
