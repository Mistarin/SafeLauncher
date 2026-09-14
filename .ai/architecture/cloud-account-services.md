# Private Cloud Account Services

`CloudAccountService` is the application boundary for account-level
SafeLauncherCloud work that is not a per-game save mutation.

## Ownership

```text
settings/account/setup UI
  -> CloudAccountService
  -> CloudClient / CloudBackend transport
  -> deployed SafeLauncherCloud
```

`CloudAccountService` owns:

- combined account overview and save-generation listing reads;
- quota/account reads;
- device revocation;
- explicit cloud-generation deletion;
- backend health probes;
- setup-time authenticated quota verification.

Device revocation and generation deletion are explicit manager-backed mutations
when the application-scoped `RequestManager` is available. Their keys contain
only the cloud context and opaque digests of device/game identifiers, and the
request handle supplies cancellation and a request/operation identity. The
underlying transport client remains short-lived and is always closed. These
administrative mutations are not cached as reusable resources.

`MainWindow._refresh_cloud_after_config_change()` invalidates this service
alongside `CloudStatusService` and `CloudOperationService`, so account-admin
results from an older endpoint, account, or credential context cannot be
applied after a configuration switch.

`CloudOperationService` remains the owner of save restore/upload/preflight
operation lifecycle. `CloudStatusService` remains the owner of per-game save
status and polling. `CloudAccountService` does not duplicate either state
store.

## UI integration

Account, Settings, Cloud Wizard, and MainWindow use the service while retaining
their existing Qt worker/binding bridges. Manager-backed dialogs schedule the
service loaders and account mutations through `RequestManager`; manager-less
construction remains a compatibility path for older embedders and tests.

Setup probes may receive an explicit site URL and secret in memory. The secret
is never included in request keys, cache identities, diagnostics, operation
records, or logs. Explicit probe keys use an opaque endpoint/credential
fingerprint.

Convex CLI commonly reports the paired `*.convex.cloud` client URL after a
deployment. SafeLauncher normalizes that host to `*.convex.site` before HTTP
function probes and runtime cloud-save requests; `/api/health` and the
SafeLauncher API are served from the site host, not the client host.

After a managed deploy, verification first uses the Convex hostname captured
from that deploy command's output. The saved settings URL and the backend
checkout's dotenv URL are fallbacks only; this prevents an older deployment
URL from making a successful push appear to have failed.

## Backend boundary

This is client-side orchestration only. The SafeLauncherCloud API contract and
backend schema are unchanged. Backend edits are required only when account,
device, save-generation, or health wire behavior itself changes.
