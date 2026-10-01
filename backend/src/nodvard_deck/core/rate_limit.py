"""Einfache Mengenbegrenzung: hoechstens `limit` Aufrufe je Schluessel in einem gleitenden
Zeitfenster (Idee wie `core/login_limit.py`, aber ohne Sperrlogik fuer Anmeldungen).

Gedacht fuer teure oder sensible Aktionen einzelner Nutzer (z. B. SSH-Schluessel erzeugen).
Der Zustand liegt nur im Prozessspeicher und ist nach einem Neustart weg; die Funktionen
sind synchron und enthalten kein `await`."""

from __future__ import annotations

import math
import threading
import time
import weakref
from collections import deque

_SWEEP_THRESHOLD = 1024
_windows: weakref.WeakSet[SlidingWindow] = weakref.WeakSet()


def _now() -> float:
    return time.monotonic()


class SlidingWindow:
    def __init__(self, limit: int, window_s: int) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        _windows.add(self)

    def hit(self, key: str) -> int:
        """Zaehlt einen Aufruf. `0` = erlaubt (und gezaehlt); sonst die Sekunden, bis wieder
        ein Aufruf frei ist -- ein abgelehnter Aufruf zaehlt nicht mit."""
        with self._lock:
            now = _now()
            cutoff = now - self.window_s
            hits = self._hits.get(key)
            if hits is not None:
                while hits and hits[0] <= cutoff:
                    hits.popleft()
                if not hits:
                    del self._hits[key]
                    hits = None
            if hits is not None and len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window_s - now))
            if hits is None:
                hits = self._hits[key] = deque()
            hits.append(now)
            if len(self._hits) > _SWEEP_THRESHOLD:
                self._sweep(cutoff)
            return 0

    def _sweep(self, cutoff: float) -> None:
        for key, hits in list(self._hits.items()):
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def reset_all() -> None:
    """Vergisst alle Zaehler aller Fenster (fuer Tests)."""
    for window in list(_windows):
        window.reset()


def wait_text(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    return f"{minutes} {'Minute' if minutes == 1 else 'Minuten'}"
