from __future__ import annotations

from collections import OrderedDict


class ProcessedSoundCache:
    def __init__(self, max_items: int):
        self.max_items = max(1, int(max_items))
        self._store = OrderedDict()

    def get(self, key):
        item = self._store.get(key)
        if item is None:
            return None
        self._store.move_to_end(key)
        return item

    def put(self, key, sound):
        self._store[key] = sound
        self._store.move_to_end(key)
        while len(self._store) > self.max_items:
            self._store.popitem(last=False)

    def clear(self):
        self._store.clear()

    def set_capacity(self, max_items: int):
        self.max_items = max(1, int(max_items))
        while len(self._store) > self.max_items:
            self._store.popitem(last=False)
