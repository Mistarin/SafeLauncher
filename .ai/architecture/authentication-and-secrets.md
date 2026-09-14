# Authentication and Secrets

There are separate credential domains:

- Private cloud credentials/configuration are used by SafeLauncherCloud transport and save encryption.
- Central authentication is used for public profile ownership and social operations.
- Steam Web API credentials, when configured, are optional and feature-specific.

Secrets must remain in the platform/configuration secret store or environment configuration. They must never enter `.ai`, pending sync queue payloads, generated manifests, logs, public profile projections, or cache keys. Cache keys may include an opaque account/backend context fingerprint, never raw tokens.

Sources: [`core/secret_store.py`](../../core/secret_store.py), [`core/central_auth.py`](../../core/central_auth.py), [`core/cloud_backend.py`](../../core/cloud_backend.py), [`core/cloud_sync_queue.py`](../../core/cloud_sync_queue.py), [`ci/security_audit.py`](../../ci/security_audit.py).
