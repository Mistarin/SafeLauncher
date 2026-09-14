# Phase 3: Cloud mutation and conflict operation service

Status: implemented on 2026-09-14.

## Scope completed

- Added the Qt-free `CloudOperationService` and `CloudOperationTarget` boundary.
- Upload, restore, restore-with-preflight, generation restore, history loading, pre-launch resolution, and game-exit sync now have manager-backed request paths.
- Operation keys are scoped by opaque cloud context, game identity, and operation type.
- RequestManager owns deduplication, priority, cooperative cancellation, retries, timeout state, and generation ordering.
- Existing `CloudSyncCoordinator` remains the domain owner for serialization, save validation, archive packaging, encryption, transport, and conflict-safe algorithms.
- MainWindow no longer directly invokes coordinator mutations. It retains Qt dialogs, launch decisions, progress surfaces, and notifications.
- Save Manager and Game Properties use the shared operation service when opened from the main application.
- Manager-less dialog construction retains compatibility fallbacks for older integrations and tests.
- Cloud operation failures remain typed `SaveOperationResult` values so recovery guidance and retry UI are preserved.
- `CloudOperationService` now records metadata-only lifecycle state for every managed operation: operation/request ID, game/context identity, generation, timestamps, terminal state, error category, and bounded progress. Save payloads and archive contents are never stored in records.
- Upload packaging, local/cloud restore stages, preflight, exit sync, and history now report bounded stage progress into those records. Restore requests pass cooperative cancellation through the coordinator and stop at safe archive boundaries before/after download, staging, and verification.
- Operation records classify domain-level mutation failures as `failed`, not merely successful request delivery; cancellation remains a distinct terminal state.
- Account-level device revocation and cloud-generation deletion now use
  `CloudAccountService` request handles in the production Account dialog, with
  opaque keys, cooperative cancellation, and explicit UI operation tracking.

## Safety rules

Operation request keys contain no endpoint URLs, access tokens, deployment keys, or raw secret material. Generation invalidation is shared with the status service so a configuration change retires both reads and mutations. Upload loaders pass cooperative cancellation into save packaging; restore and preflight check cancellation before and after domain work.

## Verification

- Cloud operation/status/request focused tests passed, including progress and cooperative cancellation lifecycle coverage.
- Full unit suite: 159 passed.
- Smoke suite: core, cloud, achievements, and UI passed.
- Python compilation, cache/index validation, and `git diff --check` passed.

## Remaining work

The compatibility worker classes and their manager-less fallbacks remain intentionally available. Backend blob streaming and encryption/decryption remain coarse-grained progress stages because their transport/crypto APIs do not yet expose byte-level callbacks; continue the compatibility-worker audit and private library/profile reconciliation hardening.
