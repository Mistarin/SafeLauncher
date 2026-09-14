# Test Reference: Smoke Tests

[`ci/smoke_phase.py`](../../../ci/smoke_phase.py) isolates core, cloud, achievements, and UI portions of the broad test harness with temporary XDG roots. Run `all` after cross-subsystem changes and the focused phase first when narrowing failures.
