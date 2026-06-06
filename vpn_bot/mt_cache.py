"""
MikroTik API Cache Layer (Phase 1+2)
Thread-safe in-memory TTL cache for reducing round-trips to MikroTik RouterOS.
Also provides a singleton manager pool to avoid redundant connection setup.
"""

import time
import threading
from vpn_bot.utils import logger

_CACHE_MISS = object()


class TTLCache:
    """Thread-safe in-memory cache with per-key TTL."""

    def __init__(self):
        self._store = {}
        self._lock = threading.Lock()
        self._stats = {'hits': 0, 'misses': 0}

    def get(self, key):
        with self._lock:
            if key in self._store:
                value, expires_at = self._store[key]
                if time.monotonic() < expires_at:
                    self._stats['hits'] += 1
                    return value
                del self._store[key]
            self._stats['misses'] += 1
        return _CACHE_MISS

    def set(self, key, value, ttl):
        with self._lock:
            self._store[key] = (value, time.monotonic() + ttl)

    def invalidate(self, *prefixes):
        """Remove all entries whose keys start with any of the given prefixes."""
        with self._lock:
            to_del = [k for k in self._store if any(k.startswith(p) for p in prefixes)]
            for k in to_del:
                del self._store[k]
            if to_del:
                logger.debug(f"Cache invalidated {len(to_del)} key(s): {to_del[:3]}...")

    def clear(self):
        with self._lock:
            self._store.clear()
            self._stats = {'hits': 0, 'misses': 0}

    @property
    def stats(self):
        with self._lock:
            total = self._stats['hits'] + self._stats['misses']
            hit_rate = (self._stats['hits'] / total * 100) if total > 0 else 0
            return {
                **self._stats,
                'size': len(self._store),
                'hit_rate': f"{hit_rate:.1f}%"
            }


# Singleton cache instance used by mikrotik_manager
mt_cache = TTLCache()

# Singleton MikroTikManager instance pool (Phase 1)
_MANAGER_INSTANCES = {}
_MANAGER_LOCK = threading.Lock()


def get_cached_manager(server=None):
    """
    Return a cached MikroTikManager instance for the given server.
    Avoids creating redundant Python objects and leverages connection pooling.
    """
    from vpn_bot.mikrotik_manager import MikroTikManager
    from vpn_bot.config import config

    if server:
        key = f"{server.host}:{server.port}:{server.username}"
        host, user, pwd, port = server.host, server.username, server.password, server.port
    else:
        key = f"{config.MIKROTIK_HOST}:{config.MIKROTIK_PORT}:{config.MIKROTIK_USERNAME}"
        host, user, pwd, port = config.MIKROTIK_HOST, config.MIKROTIK_USERNAME, config.MIKROTIK_PASSWORD, config.MIKROTIK_PORT

    with _MANAGER_LOCK:
        if key not in _MANAGER_INSTANCES:
            _MANAGER_INSTANCES[key] = MikroTikManager(
                host=host, username=user, password=pwd, port=port
            )
            logger.info(f"Created singleton MikroTikManager for {host}:{port}")
        return _MANAGER_INSTANCES[key]


def evict_manager_for_key(key: str) -> None:
    """Drop cached MikroTikManager after server row is deleted."""
    with _MANAGER_LOCK:
        mgr = _MANAGER_INSTANCES.pop(key, None)
    if mgr:
        try:
            mgr.close()
        except Exception:
            pass
    if key:
        host = key.split(":", 1)[0]
        mt_cache.invalidate(f"{host}:")
