"""Registry für Terminal-Tickets im Speicher.

`POST /terminal/sessions` erzeugt ein Einmal-Ticket für den WebSocket; Tokens gehören nicht in die URL (Proxy-Logs)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass


@dataclass
class TerminalSessionTicket:
    session_id: str
    host_id: str
    user_id: str
    cols: int
    rows: int
    created_at: float
    consumed: bool = False
    stamp: str = ""
    """Fingerabdruck des Passworts beim Anlegen (`services.session_guard.credential_stamp`);
    ändert er sich, endet die Sitzung."""
    login_id: str | None = None
    """Anmeldung (`sid` im Zugangs-Token), aus der das Ticket stammt (`services.session_guard`)."""
    scope: str | None = None
    """Shell IN etwas auf dem Host (`ScopedTerminalTarget.scope`, z. B. ein Container), sonst `None`."""
    ref: str | None = None
    """Welches (`ScopedTerminalTarget`, z. B. der Name), nur mit `scope`."""


class TerminalSessionRegistry:
    def __init__(self) -> None:
        self._tickets: dict[str, TerminalSessionTicket] = {}

    def create(
        self, *, host_id: str, user_id: str, cols: int, rows: int, stamp: str = "",
        login_id: str | None = None, scope: str | None = None, ref: str | None = None,
    ) -> TerminalSessionTicket:
        session_id = uuid.uuid4().hex
        ticket = TerminalSessionTicket(
            session_id=session_id, host_id=host_id, user_id=user_id,
            cols=cols, rows=rows, created_at=time.monotonic(), stamp=stamp, login_id=login_id,
            scope=scope, ref=ref,
        )
        self._tickets[session_id] = ticket
        return ticket

    def consume(self, session_id: str, *, ttl_s: float) -> TerminalSessionTicket | None:
        """Entfernt das Ticket unabhängig vom Ergebnis: Ein Ticket ist immer Einmalgebrauch, auch wenn es abgelaufen war."""
        ticket = self._tickets.pop(session_id, None)
        if ticket is None:
            return None
        if time.monotonic() - ticket.created_at > ttl_s:
            return None
        return ticket


_registry: TerminalSessionRegistry | None = None


def get_terminal_session_registry() -> TerminalSessionRegistry:
    global _registry
    if _registry is None:
        _registry = TerminalSessionRegistry()
    return _registry


def reset_terminal_session_registry() -> None:
    """Nur für Tests."""
    global _registry
    _registry = None
