# Packaging and Deployment

Source launch is supported through setup and launcher scripts. AppImage packaging is defined under `packaging/`. The profile gateway and profile cloud are separate deployable TypeScript services. Cloud setup can discover/configure/deploy the external private SafeLauncherCloud Convex backend through the CLI wizard. In the normal development layout, that backend checkout is the sibling `SafeLauncher/../SafeLauncherDatabase/` directory; it is not the client's SQLite database and is not bundled into the launcher runtime.

Changes to Python runtime modules should be checked against source launch, tests, and AppImage packaging assumptions. Changes to profile service routes require gateway and Convex validation separately.

Sources: [`setup/README.md`](../../setup/README.md), [`setup/02-launch.sh`](../../setup/02-launch.sh), [`packaging/safelauncher.spec`](../../packaging/safelauncher.spec), [`core/cloud_cli_wizard.py`](../../core/cloud_cli_wizard.py), [`services/profile_gateway/README.md`](../../services/profile_gateway/README.md), [`services/profile_cloud/README.md`](../../services/profile_cloud/README.md).
