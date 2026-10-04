"""A small bounded TTL + LRU cache for deterministic lookups. Single event loop, so no locking."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable


class TTLCache[K: Hashable, V]:
    """Least-recently-used cache whose entries also expire after `ttl` seconds.

    Bounded by `maxsize` so keys derived from client input (such as caller hints) can't grow memory.
    """

    def __init__(self, maxsize: int, ttl: float, clock: Callable[[], float] = time.monotonic) -> None:
        if maxsize < 1 or ttl <= 0:
            raise ValueError("maxsize must be >= 1 and ttl > 0")
        self._maxsize = maxsize
        self._ttl = ttl
        self._clock = clock
        self._items: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: K) -> bool:
        item = self._items.get(key)
        return item is not None and item[0] > self._clock()

    def get(self, key: K) -> V | None:
        item = self._items.get(key)
        if item is None or item[0] <= self._clock():
            if item is not None:
                del self._items[key]
            self.misses += 1
            return None
        self._items.move_to_end(key)
        self.hits += 1
        return item[1]

    def set(self, key: K, value: V) -> None:
        self._items[key] = (self._clock() + self._ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._maxsize:
            self._items.popitem(last=False)
