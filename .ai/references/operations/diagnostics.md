# Operations Reference: Diagnostics

Use `setup/01-doctor.sh`, `core/system_inspector.py`, `ci/security_audit.py`, `ci/smoke_phase.py`, and the full `test.py` harness for diagnosis. Runtime logs and user data remain outside the repository cache.

For request/resource performance support, use the explicit Settings export,
implemented by [`core/runtime_diagnostics.py`](../../../core/runtime_diagnostics.py).
It emits only version/platform/network-policy metadata and allowlisted numeric
counters; launch reports and logs remain separate because they can contain
game-specific paths or process output.

For repeatable offline performance measurements, run:

```bash
python ci/performance_baseline.py --games 600 --requests 100 --repetitions 5 --workers 3
```

Add `--max-render-ms` and/or `--max-workers-peak` only after a measured
baseline has been reviewed.

The reviewed synthetic baseline is recorded in
[`phase-10-performance-baseline.md`](../../phase-10-performance-baseline.md).
It validates local projection/scheduler overhead only; use application
diagnostics for representative startup, artwork, cloud, and shutdown values.
