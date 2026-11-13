"""Entfernt Download- und WebSocket-Tickets aus den Uvicorn-Protokollen."""

from __future__ import annotations

import logging
import re

ACCESS_LOGGER = "uvicorn.access"
REDACTED = "…"

# Das Einmal-Ticket steht in der Adresse (`GET .../system/backups/download/{ticket}`, der Browser lädt ohne
# Anmelde-Header); in weitergegebenen Container-Protokollen stünde ein gültiger Download-Link. Der Filter deckt den
# Download-Aufruf und die alte Status-Adresse ab, unabhängig vom Präfix (hinter einem Proxy kann `/api/v1` anders heißen).
_TICKET_PATH = re.compile(r"^(?P<head>[^?#]*/system/backups/download/)(?P<ticket>[^/?#]+)(?P<rest>/[^?#]*)?(?:[?#].*)?$")
_WS_TICKET_PATH = re.compile(r"^(?P<head>[^?#]*/ws/(?:terminal|console)/)[^/?#]+(?:[?#].*)?$")


def redact_path(path: str) -> str:
    """Ersetzt Einmal-Tickets durch `…`."""
    match = _TICKET_PATH.match(path)
    if match is None:
        match = _WS_TICKET_PATH.match(path)
        return f"{match['head']}{REDACTED}" if match else path
    return f"{match['head']}{REDACTED}{match['rest'] or ''}"


class TicketRedactFilter(logging.Filter):
    """Kürzt Tickets in den Adressargumenten von Uvicorn."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(redact_path(value) if isinstance(value, str) else value for value in args)
        return True


def install_access_log_filters() -> None:
    """Idempotent; der Filter hängt am Logger selbst und gilt für jeden Handler, auch für eine eigene Logging-Konfiguration.
    Aufruf beim Erzeugen der App und beim Start: uvicorn richtet sein Logging vor dem Import der App ein."""
    for name in (ACCESS_LOGGER, "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, TicketRedactFilter) for f in logger.filters):
            logger.addFilter(TicketRedactFilter())
