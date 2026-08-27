# SafeLauncher Setup & Operations Scripts

Ordered scripts for configuring, launching, building, and deploying SafeLauncher:

| Script | Purpose |
| :--- | :--- |
| `01-doctor.sh` | Run host system diagnostics (distro, GPU driver, Wayland/X11, gaming dependencies). |
| `02-launch.sh` | Start SafeLauncher with automatic environment and dependency checks. |
| `03-setup-cloud.sh` | All-in-one Cloud Save Setup (deploy Convex backend from source + configure connection). |
| `04-install-desktop-entry.sh` | Install the desktop application menu icon and launcher entry. |
| `05-build-appimage.sh` | Build the standalone Linux AppImage package (`dist/SafeLauncher-x86_64.AppImage`). |
| `06-run-tests.sh` | Run the complete automated test suite. |
