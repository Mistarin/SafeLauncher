# SafeLauncher agent instructions

## Environment

Load the local environment before running commands that need deployment or
service configuration:

```bash
set -a
source .env.local
set +a
```

`.env.local` is intentionally ignored by Git. Never copy its values into
tracked files, commit it, or print credentials. The public profile service
uses `SAFELAUNCHER_PROFILE_SERVICE_URL`; normal desktop traffic must go
through that gateway and must not call the raw Convex endpoints directly.

## Project boundaries

- This repository is the SafeLauncher desktop application.
- `/home/martin/Main/Programming/SafeLauncherDatabase/` is the working area
  for private cloud/save-backend edits.
- `/home/martin/Main/Programming/SafeLauncher/services/` contains the global
  public-profile service, including the `dashing-pigeon` Convex project and
  its gateway.
- Do not mix private save-cloud changes into the global profile service, or
  global profile changes into the private cloud project.

## Global profile deployment

The production Convex deployment is selected through `CONVEX_DEPLOYMENT` from
`.env.local`. Deploy only from the global service directory and verify the
target before pushing:

```bash
cd /home/martin/Main/Programming/SafeLauncher/services/profile_cloud
set -a
source /home/martin/Main/Programming/SafeLauncher/.env.local
set +a
npx convex deploy --yes
```

The raw Convex site/cloud URLs are deployment references, not public desktop
API endpoints. Public profile requests must remain behind the configured
gateway. Never expose gateway keys, Auth0 secrets, Convex deploy keys, or
private migration keys in Git, logs, screenshots, or chat.

## Verification

After a global profile deployment:

1. Check the gateway health endpoint through the configured public service
   URL and confirm the expected service version.
2. Run the SafeLauncher Python test suite from the repository root.
3. For private cloud changes, run the relevant tests from
   `SafeLauncherDatabase` separately.
