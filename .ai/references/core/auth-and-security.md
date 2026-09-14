# Core Reference: Authentication and Security

Relevant modules include [`central_auth.py`](../../../core/central_auth.py), [`secret_store.py`](../../../core/secret_store.py), [`clerk_auth.py`](../../../core/clerk_auth.py), [`save_crypto.py`](../../../core/save_crypto.py), [`save_validation.py`](../../../core/save_validation.py), [`network_policy.py`](../../../core/network_policy.py), [`security_diagnostics.py`](../../../core/security_diagnostics.py), and [`prefix_sanitizer.py`](../../../core/prefix_sanitizer.py).

Keep public profile authentication, private cloud credentials, and optional Steam credentials separate. Security-sensitive changes require `ci/security_audit.py` and focused tests.
