# Entrypoints

| Entry | Purpose | Next owner |
|---|---|---|
| `main.py` | CLI dispatch and GUI startup | `core.bootstrap`, `ui.MainWindow` |
| `launcher.sh` | Source/AppImage launch wrapper | `main.py` |
| `run_app.sh` | Developer launch wrapper | `main.py` |
| `setup/01-doctor.sh` | Host dependency diagnostics | `core.system_inspector` |
| `setup/02-launch.sh` | Environment verification and launch | `main.py` |
| `setup/03-setup-cloud.sh` | Private cloud setup | `core.cloud_cli_wizard` |
| `setup/06-run-tests.sh` | Test entrypoint | `test.py` |
| `test.py` | Broad historical test harness | focused tests and smoke phases |
| `ci/smoke_phase.py` | Isolated core/cloud/achievement/UI smoke sections | selected `test.py` sections |
| `ci/security_audit.py` | Static security checks | CI |
| `services/profile_gateway/src/index.ts` | Profile gateway deployment entry | gateway API |
| `services/profile_cloud/convex/http.ts` | Public profile HTTP routes | Convex functions |
