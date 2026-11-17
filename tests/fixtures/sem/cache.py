"""Memo helpers."""
import time


class TimedCache:
    def __init__(self, ttl_seconds):
        self.ttl_seconds = ttl_seconds
        self.entries = {}

    def get(self, key, make):
        now = time.monotonic()
        hit = self.entries.get(key)
        if hit and now - hit[0] < self.ttl_seconds:
            return hit[1]
        value = make()
        self.entries[key] = (now, value)
        return value
