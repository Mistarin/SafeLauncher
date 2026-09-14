"""Explicit indexes for manager-less compatibility workers.

These indexes deliberately do not start, stop, schedule, or retry workers.
They only retain references long enough for the owning UI to route completion
and for ``WorkerSupervisor`` to own actual QThread shutdown.
"""

from __future__ import annotations

from collections.abc import Iterator


class CompatibilityWorkerIndex:
    """Small list-like reference index with an explicit compatibility role."""

    def __init__(self, name: str):
        self.name = str(name)
        self._workers: list[object] = []

    def append(self, worker: object) -> None:
        self._workers.append(worker)

    def remove(self, worker: object) -> None:
        self._workers.remove(worker)

    def pop(self, index: int = -1):
        return self._workers.pop(index)

    def clear(self) -> None:
        self._workers.clear()

    def __iter__(self) -> Iterator[object]:
        return iter(self._workers)

    def __len__(self) -> int:
        return len(self._workers)

    def __contains__(self, worker: object) -> bool:
        return worker in self._workers

    def __getitem__(self, index: int):
        return self._workers[index]

    def snapshot(self) -> tuple[object, ...]:
        """Return an immutable view for lifecycle scans."""
        return tuple(self._workers)


__all__ = ["CompatibilityWorkerIndex"]
