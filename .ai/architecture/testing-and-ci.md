# Testing and CI

The project has a historical broad harness in `test.py`, focused unittest modules under `tests/`, phase smoke execution in `ci/smoke_phase.py`, security checks in `ci/security_audit.py`, and GitHub Actions orchestration in `.github/workflows/ci.yml`.

Tests must isolate XDG data/config/cache roots and use offscreen Qt where UI is involved. Request/cache tests should remain Qt-free. Thread and shutdown changes require both focused tests and a full harness run because teardown failures may only appear after all sections complete.

Sources: [`test.py`](../../test.py), [`ci/smoke_phase.py`](../../ci/smoke_phase.py), [`ci/security_audit.py`](../../ci/security_audit.py), [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
