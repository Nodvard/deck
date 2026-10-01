"""In-Memory-Registry fuer Terminal-Session-Tickets (docs/04-API.md §4).

`POST /terminal/sessions` mintet ein Ticket, das GENAU EINMAL beim WS-Verbindungsaufbau
eingeloest wird. Kein Access-Token in der URL (docs/04 §4: "Keine Tokens in der URL,
sie landen in Proxy-Logs") -- die Session-ID selbst ist zwingend Teil des WS-Pfads, um
den Kanal zu adressieren, ist aber ein kurzlebiges, einmaliges Ticket und kein
wiederverwendbares Credential."""

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


class TerminalSessionRegistry:
    def __init__(self) -> None:
        self._tickets: dict[str, TerminalSessionTicket] = {}

    def create(self, *, host_id: str, user_id: str, cols: int, rows: int) -> TerminalSessionTicket:
        session_id = uuid.uuid4().hex
        ticket = TerminalSessionTicket(
            session_id=session_id, host_id=host_id, user_id=user_id,
            cols=cols, rows=rows, created_at=time.monotonic(),
        )
        self._tickets[session_id] = ticket
        return ticket

    def consume(self, session_id: str, *, ttl_s: float) -> TerminalSessionTicket | None:
        """Entfernt das Ticket unabhaengig vom Ergebnis -- ein Ticket ist IMMER
        Einmalgebrauch, auch wenn es abgelaufen war."""
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
    """Nur fuer Tests."""
    global _registry
    _registry = None
