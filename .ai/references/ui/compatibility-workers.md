# UI Reference: Compatibility Workers

[`ui/threads.py`](../../../ui/threads.py) and feature worker classes remain for standalone/legacy paths. They are not evidence that the shared manager is missing from production paths. Use generated connections and source call sites to determine whether a worker is still reachable before removal.
