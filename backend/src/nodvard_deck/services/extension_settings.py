"""Gemeinsame Sperre für die Einstellungen einer Erweiterung."""

from __future__ import annotations

import asyncio
from weakref import WeakValueDictionary

_locks: WeakValueDictionary[tuple[asyncio.AbstractEventLoop, str], asyncio.Lock] = WeakValueDictionary()


def lock_for(store_id: str) -> asyncio.Lock:
    key = (asyncio.get_running_loop(), store_id)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock
