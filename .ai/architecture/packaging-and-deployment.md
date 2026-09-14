# Packaging and Deployment

Source launch is supported through setup and launcher scripts. AppImage packaging is defined under `packaging/`. The profile gateway and profile cloud are separate deployable TypeScript services. Cloud setup can discover/configure/deploy a private Convex backend through the CLI wizard.

Changes to Python runtime modules should be checked against source launch, tests, and AppImage packaging assumptions. Changes to profile service routes require gateway and Convex validation separately.

Sources: [`setup/README.md`](../../setup/README.md), [`setup/02-launch.sh`](../../setup/02-launch.sh), [`packaging/safelauncher.spec`](../../packaging/safelauncher.spec), [`core/cloud_cli_wizard.py`](../../core/cloud_cli_wizard.py), [`services/profile_gateway/README.md`](../../services/profile_gateway/README.md), [`services/profile_cloud/README.md`](../../services/profile_cloud/README.md).
