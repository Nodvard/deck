"""Filter für das Zugriffsprotokoll von uvicorn (`uvicorn.access`): entfernt das Download-Ticket aus der Adresse."""

from __future__ import annotations

import logging
import re

ACCESS_LOGGER = "uvicorn.access"
REDACTED = "…"

# Das Einmal-Ticket steht in der Adresse (`GET .../system/backups/download/{ticket}`, der Browser lädt ohne
# Anmelde-Header); in weitergegebenen Container-Protokollen stünde ein gültiger Download-Link. Der Filter deckt den
# Download-Aufruf und die alte Status-Adresse ab, unabhängig vom Präfix (hinter einem Proxy kann `/api/v1` anders heißen).
_TICKET_PATH = re.compile(r"^(?P<head>[^?#]*/system/backups/download/)(?P<ticket>[^/?#]+)(?P<rest>/[^?#]*)?(?:[?#].*)?$")


def redact_path(path: str) -> str:
    """Ersetzt das Ticket in einer Download-Adresse durch `…`, andere Adressen bleiben gleich."""
    match = _TICKET_PATH.match(path)
    if match is None:
        return path
    return f"{match['head']}{REDACTED}{match['rest'] or ''}"


class TicketRedactFilter(logging.Filter):
    """Kürzt Download-Tickets in Zeilen des Zugriffsprotokolls (Argumente wie bei uvicorn:
    `(client, methode, adresse, http_version, status)`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            redacted = redact_path(args[2])
            if redacted != args[2]:
                record.args = (*args[:2], redacted, *args[3:])
        return True


def install_access_log_filters() -> None:
    """Idempotent; der Filter hängt am Logger selbst und gilt für jeden Handler, auch für eine eigene Logging-Konfiguration.
    Aufruf beim Erzeugen der App und beim Start: uvicorn richtet sein Logging vor dem Import der App ein."""
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, TicketRedactFilter) for f in logger.filters):
        logger.addFilter(TicketRedactFilter())
