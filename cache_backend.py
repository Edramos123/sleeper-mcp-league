#!/usr/bin/env python3
"""Cache backend selection: real Redis, with an in-process fallback.

Render's free tier does not always have a Redis instance attached. If
REDIS_URL is unset or the configured Redis is unreachable, fall back to a
process-local in-memory store so the player cache still works on a single
instance without Redis. The fallback does not survive a process restart and
is not shared across instances, but that matches the single-instance
free-tier deployment this project targets.
"""

import os
import time
import logging
import threading
from typing import Any, Optional

import redis

logger = logging.getLogger(__name__)

# Shared across InMemoryCache instances so every caller sees the same data
# for the lifetime of the process.
_memory_store: dict[str, tuple[Any, Optional[float]]] = {}

_selected_client = None
_selection_lock = threading.Lock()


class InMemoryCache:
    """Minimal subset of the redis.Redis interface, backed by a process-local dict."""

    def __init__(self) -> None:
        self._store = _memory_store

    def _is_expired(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return True
        _, expires_at = entry
        if expires_at is not None and time.time() > expires_at:
            del self._store[key]
            return True
        return False

    def get(self, key: str):
        if self._is_expired(key):
            return None
        return self._store[key][0]

    def set(self, key: str, value, ex: Optional[int] = None):
        expires_at = time.time() + ex if ex else None
        self._store[key] = (value, expires_at)
        return True

    def exists(self, key: str) -> int:
        return 0 if self._is_expired(key) else 1

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def lrange(self, key: str, start: int, end: int) -> list:
        # Only used for refresh-history display; the in-memory backend
        # doesn't track that history, so return empty rather than erroring.
        return []


def _redis_reachable(client: "redis.Redis") -> bool:
    try:
        client.ping()
        return True
    except Exception as e:
        logger.warning(f"Redis unreachable ({e}); falling back to in-memory cache")
        return False


def get_cache_client():
    """Return a cache client: real Redis if reachable, else an in-memory fallback.

    The choice is made once per process and cached, so we don't re-probe
    Redis (and pay its connection timeout) on every cache access. Callers
    may hit this concurrently from a thread pool (e.g. parallel projections
    fetches), so the first-time selection is guarded by a lock.
    """
    global _selected_client
    if _selected_client is not None:
        return _selected_client

    with _selection_lock:
        if _selected_client is not None:
            return _selected_client

        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        client = redis.from_url(
            redis_url, decode_responses=False, socket_connect_timeout=2
        )

        if _redis_reachable(client):
            _selected_client = client
        else:
            _selected_client = InMemoryCache()

    return _selected_client
