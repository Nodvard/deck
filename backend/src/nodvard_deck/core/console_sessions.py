"""Zwischenablage für bereits geöffnete grafische Konsolen-Sitzungen (VM-/Container-Bildschirm, `api/v1/console.py`)."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class PendingConsole:
    session_id: str
    host_id: str
    user_id: str
    session: Any
    """Erfüllt `nodvard_sdk.capabilities.ConsoleSession`."""
    created_at: float
    stamp: str = ""
    """Fingerabdruck des Passworts beim Anlegen (`services.session_guard.credential_stamp`)."""
    login_id: str | None = None
    """Anmeldung (`sid` im Zugangs-Token), aus der der Eintrag stammt (`services.session_guard`)."""


async def _close_quietly(session: Any) -> None:
    try:
        await asyncio.wait_for(session.close(), timeout=5.0)
    except Exception:  # noqa: BLE001 - Aufräumen darf nie selbst scheitern
        pass


# Anders als beim Terminal (`core/terminal_sessions.py`: Ticket jetzt, Verbindung erst beim WebSocket-Aufbau) öffnet
# `POST /console/sessions` die Konsole sofort: Das Einmal-Kennwort für die RFB-Authentifizierung entsteht erst beim Öffnen,
# noVNC braucht es aber, bevor es den WebSocket aufbaut. Käme es erst als Nachricht über den WebSocket, könnte die
# Server-Begrüßung verloren gehen, weil noVNC sich asynchron an den Socket hängt. Die Session-ID ist wie beim Terminal
# ein kurzlebiges Einmal-Ticket; nicht abgeholte Sitzungen werden nach `ttl_s` geschlossen, sonst bliebe nach einem
# abgebrochenen Seitenaufruf eine offene Verbindung zum Hypervisor hängen.
class ConsoleSessionStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingConsole] = {}
        self._cleanup_tasks: set[asyncio.Task[None]] = set()

    def add(
        self, *, host_id: str, user_id: str, session: Any, ttl_s: float, stamp: str = "",
        login_id: str | None = None,
    ) -> PendingConsole:
        session_id = uuid.uuid4().hex
        entry = PendingConsole(
            session_id=session_id, host_id=host_id, user_id=user_id,
            session=session, created_at=time.monotonic(), stamp=stamp, login_id=login_id,
        )
        self._pending[session_id] = entry
        asyncio.get_running_loop().call_later(ttl_s, self._expire, session_id)
        return entry

    def take(self, session_id: str, *, ttl_s: float) -> PendingConsole | None:
        """Einmalgebrauch: entfernt den Eintrag unabhängig vom Ergebnis; ein abgelaufener wird geschlossen, nicht ausgeliefert."""
        entry = self._pending.pop(session_id, None)
        if entry is None:
            return None
        if time.monotonic() - entry.created_at > ttl_s:
            self._schedule_close(entry.session)
            return None
        return entry

    def pending_count(self) -> int:
        return len(self._pending)

    def _expire(self, session_id: str) -> None:
        entry = self._pending.pop(session_id, None)
        if entry is not None:
            self._schedule_close(entry.session)

    def _schedule_close(self, session: Any) -> None:
        task = asyncio.ensure_future(_close_quietly(session))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def close_all(self) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        await asyncio.gather(*(_close_quietly(p.session) for p in pending))


_store: ConsoleSessionStore | None = None


def get_console_session_store() -> ConsoleSessionStore:
    global _store
    if _store is None:
        _store = ConsoleSessionStore()
    return _store


def reset_console_session_store() -> None:
    """Nur für Tests."""
    global _store
    _store = None
