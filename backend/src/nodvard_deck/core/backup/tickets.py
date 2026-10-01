"""Einmal-Tickets fuer den Download einer Sicherung (Muster: core/terminal_sessions.py).

Der Download ist ein normaler Browser-Download (`GET /system/backups/download/{ticket}`),
ohne Authorization-Header und ohne die Datei im Speicher des Browsers. Das Ticket ersetzt
die Anmeldung fuer genau diese eine Datei:

* zufaellig (256 Bit), nur im Prozessspeicher,
* der Stand wird NICHT ueber das Ticket abgefragt, sondern ueber eine eigene Zufalls-ID
  (`job_id`): die steht in jeder Abfrage-Adresse (jede Sekunde, Zugriffsprotokoll) und taugt
  deshalb nicht zum Herunterladen,
* an den Nutzer gebunden (beim Einloesen muss er noch aktiver Owner sein; den Stand eines
  Tickets darf nur er abfragen),
* gilt 5 Minuten ab dem Moment, in dem die Datei fertig ist,
* wird beim Einloesen entfernt -- auch wenn es abgelaufen war.

Eine fuer den Download gebaute Datei (`delete_after`) wird nach dem Ausliefern bzw. beim
Verfall geloescht; bei einer automatischen Sicherung bleibt die Datei natuerlich liegen.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

TICKET_TTL_S = 300.0


@dataclass
class DownloadTicket:
    ticket: str
    user_id: str
    path: Path
    filename: str
    delete_after: bool
    created_at: float
    job_id: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    """Nur zum Abfragen des Stands, nie zum Herunterladen."""
    status: str = "building"
    """building | ready | failed"""
    ready_at: float | None = None
    size: int | None = None
    error: str | None = None

    def expired(self, ttl_s: float, now: float) -> bool:
        start = self.ready_at if self.ready_at is not None else self.created_at
        # Ein Bau darf laenger dauern als die Gueltigkeit (grosse Sicherungen), nur nicht ewig.
        limit = ttl_s if self.status != "building" else max(ttl_s, 6 * 3600)
        return now - start > limit


class DownloadTicketRegistry:
    def __init__(self) -> None:
        self._tickets: dict[str, DownloadTicket] = {}
        self._jobs: dict[str, str] = {}
        """job_id -> Ticket."""

    def create(self, *, user_id: str, path: Path, filename: str, delete_after: bool, ready: bool) -> DownloadTicket:
        now = time.monotonic()
        ticket = DownloadTicket(
            ticket=secrets.token_urlsafe(32), user_id=user_id, path=path, filename=filename,
            delete_after=delete_after, created_at=now,
        )
        if ready:
            ticket.status, ticket.ready_at = "ready", now
            with contextlib.suppress(OSError):
                ticket.size = path.stat().st_size
        self._tickets[ticket.ticket] = ticket
        self._jobs[ticket.job_id] = ticket.ticket
        return ticket

    def get(self, ticket_id: str, *, ttl_s: float = TICKET_TTL_S) -> DownloadTicket | None:
        self.sweep(ttl_s=ttl_s)
        return self._tickets.get(ticket_id)

    def get_by_job(self, job_id: str, *, ttl_s: float = TICKET_TTL_S) -> DownloadTicket | None:
        self.sweep(ttl_s=ttl_s)
        ticket_id = self._jobs.get(job_id)
        return self._tickets.get(ticket_id) if ticket_id else None

    def _forget(self, ticket: DownloadTicket) -> None:
        self._tickets.pop(ticket.ticket, None)
        self._jobs.pop(ticket.job_id, None)

    def mark_ready(self, ticket_id: str) -> None:
        ticket = self._tickets.get(ticket_id)
        if ticket is not None:
            ticket.status, ticket.ready_at = "ready", time.monotonic()
            with contextlib.suppress(OSError):
                ticket.size = ticket.path.stat().st_size

    def mark_failed(self, ticket_id: str, error: str) -> None:
        ticket = self._tickets.get(ticket_id)
        if ticket is not None:
            ticket.status, ticket.ready_at, ticket.error = "failed", time.monotonic(), error

    def consume(self, ticket_id: str, *, ttl_s: float = TICKET_TTL_S) -> DownloadTicket | None:
        """Gibt das Ticket genau einmal heraus, wenn die Datei fertig und das Ticket noch
        gueltig ist. Ein Ticket im Bau bleibt liegen (der Knopf kam zu frueh), alles andere
        ist danach weg."""
        ticket = self._tickets.get(ticket_id)
        if ticket is None or ticket.status == "building":
            return None
        self._forget(ticket)
        if ticket.status != "ready" or ticket.expired(ttl_s, time.monotonic()):
            _discard(ticket)
            return None
        return ticket

    def sweep(self, *, ttl_s: float = TICKET_TTL_S) -> None:
        now = time.monotonic()
        for ticket_id, ticket in list(self._tickets.items()):
            if ticket.expired(ttl_s, now):
                self._forget(ticket)
                _discard(ticket)

    def live_paths(self) -> set[Path]:
        return {t.path for t in self._tickets.values()}

    def clear(self) -> None:
        for ticket in self._tickets.values():
            _discard(ticket)
        self._tickets.clear()
        self._jobs.clear()


def _discard(ticket: DownloadTicket) -> None:
    if ticket.delete_after:
        with contextlib.suppress(OSError):
            os.unlink(ticket.path)


_registry: DownloadTicketRegistry | None = None


def get_ticket_registry() -> DownloadTicketRegistry:
    global _registry
    if _registry is None:
        _registry = DownloadTicketRegistry()
    return _registry


def reset_ticket_registry() -> None:
    """Nur fuer Tests."""
    global _registry
    if _registry is not None:
        _registry.clear()
    _registry = None
