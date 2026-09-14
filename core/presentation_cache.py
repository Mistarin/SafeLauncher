"""Bounded decoded-value caches used only by UI presentation code."""

from __future__ import annotations

from collections import OrderedDict


class PresentationCache(OrderedDict):
    """LRU cache for already-loaded UI values.

    This is intentionally not a remote resource cache: it has no TTL,
    freshness, disk persistence, request deduplication, or error state. The
    request manager and ``ResourceCache`` remain authoritative for those
    concerns.
    """

    def __init__(self, max_items: int):
        if int(max_items) < 1:
            raise ValueError("Presentation cache size must be positive")
        self.max_items = int(max_items)
        super().__init__()

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.max_items:
            self.popitem(last=False)


__all__ = ["PresentationCache"]
