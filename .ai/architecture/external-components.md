# External Components

## SafeLauncherCloud backend checkout

SafeLauncher is a client application with a separate private cloud backend. The
backend source is not contained in this repository. The companion GitHub
repository is:

`https://github.com/Mistarin/SafeLauncherCloud.git`

In the normal development layout, the launcher may keep that checkout at:

`SafeLauncher/../SafeLauncherDatabase/`

The directory name is historical and does not mean that this is the launcher's
local SQLite database. It is a local checkout/deployment workspace for the
SafeLauncherCloud Convex backend. The backend may also be found at a sibling
`SafeLauncherCloud` directory or another user-selected path.

## Relationship to the client

```text
SafeLauncher client repository
  core/cloud_client.py
    -> core/cloud_backend.py
      -> deployed SafeLauncherCloud / Convex backend

local SQLite database
  database.py
    -> authoritative local library projection
```

The client owns local library state, request scheduling, cache state, cloud
reconciliation, and save/archive preparation. SafeLauncherCloud owns the
private account-wide cloud records, encrypted save generations, revisions, and
backend-side persistence.

The public profile services under `services/profile_gateway/` and
`services/profile_cloud/` are separate external/deployable services. They must
not be treated as the private SafeLauncherCloud backend, and they must not
receive private installation paths or device installation state.

## Discovery and deployment paths

- [`core/cloud_detector.py`](../../core/cloud_detector.py) discovers local
  `SafeLauncherDatabase` and `SafeLauncherCloud` checkouts.
- [`core/cloud_cli_wizard.py`](../../core/cloud_cli_wizard.py) configures,
  downloads, and deploys the private backend. Its default non-frozen checkout
  target is the sibling `SafeLauncherDatabase` directory.
- The wizard can use an existing checkout, clone the GitHub repository, or
  download an archive into a temporary staging directory for deployment.
- The client talks to the deployed backend through configured Convex/cloud
  URLs; it does not import backend source files at runtime.

## Boundary and security rules

- The external checkout is an operational dependency, not a Python package
  imported by the client.
- Backend deployment configuration and credentials remain in the backend or
  local credential stores; they must not enter `.ai` files, request keys,
  manifests, logs, or cache identities.
- Changes to private library schema/API require coordinated work in both
  repositories when the backend contract changes.
- Client-only request-manager, cache, UI, and local SQLite changes do not
  automatically require edits to the backend repository.
- Public profile schema/API work is a separate change path.

## Verification checklist

When changing private cloud behavior, verify both sides as applicable:

1. Confirm the active backend checkout/path and deployment configuration.
2. Read the client transport and request/resource service contracts.
3. Inspect the corresponding SafeLauncherCloud Convex schema/functions when the
   wire contract changes.
4. Run client tests and private-cloud integration/smoke tests.
5. Never record tokens, URLs containing credentials, private payloads, or save
   contents in the project context cache.

Sources: [`core/cloud_client.py`](../../core/cloud_client.py),
[`core/cloud_backend.py`](../../core/cloud_backend.py),
[`core/cloud_detector.py`](../../core/cloud_detector.py),
[`core/cloud_cli_wizard.py`](../../core/cloud_cli_wizard.py), and the external
SafeLauncherCloud repository.
