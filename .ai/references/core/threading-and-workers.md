# Core Reference: Threading and Workers

[`safe_thread.py`](../../../core/safe_thread.py) contains reusable Qt worker/supervisor lifecycle primitives. [`ui/threads.py`](../../../ui/threads.py) contains feature-specific compatibility workers. The request manager is deliberately Qt-free and owns its own bounded worker pool.

When changing thread lifetime, test normal completion, cancellation, exceptions, repeated runs, application shutdown, and late signal delivery. Strong ownership through shutdown is a known safety invariant.
