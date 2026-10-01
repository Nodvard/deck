"""Filter fuer das Zugriffsprotokoll von uvicorn (`uvicorn.access`).

uvicorn schreibt zu jeder Anfrage eine Zeile mit der vollen Adresse. Das Einmal-Ticket fuer
den Sicherungs-Download steht in der Adresse (`GET /api/v1/system/backups/download/{ticket}`,
der Browser laedt ohne Anmelde-Header) -- und wer Container-Protokolle sammelt oder
weitergibt, haette damit einen gueltigen Download-Link. Darum wird das Ticket hier durch
`…` ersetzt, bevor die Zeile geschrieben wird. Der Filter haengt am Logger selbst, gilt also
fuer jeden Handler, auch fuer eine eigene Logging-Konfiguration des Betreibers.

Der Stand eines Downloads wird ueber eine eigene ID abgefragt (nicht das Ticket, siehe
`core/backup/tickets.py`); der Filter deckt zusaetzlich den einen echten Download-Aufruf und
die alte Status-Adresse mit dem Ticket ab.
"""

from __future__ import annotations

import logging
import re

ACCESS_LOGGER = "uvicorn.access"
REDACTED = "…"

# Praefix-unabhaengig (`/api/v1` koennte hinter einem Proxy anders heissen). Gekuerzt wird
# nur der Teil nach `download/`; ein folgendes `/status` bleibt stehen, eine Query entfaellt.
_TICKET_PATH = re.compile(r"^(?P<head>[^?#]*/system/backups/download/)(?P<ticket>[^/?#]+)(?P<rest>/[^?#]*)?(?:[?#].*)?$")


def redact_path(path: str) -> str:
    """Ersetzt das Ticket in einer Download-Adresse durch `…`, andere Adressen bleiben gleich."""
    match = _TICKET_PATH.match(path)
    if match is None:
        return path
    return f"{match['head']}{REDACTED}{match['rest'] or ''}"


class TicketRedactFilter(logging.Filter):
    """Kuerzt Download-Tickets in Zeilen des Zugriffsprotokolls (Argumente wie bei uvicorn:
    `(client, methode, adresse, http_version, status)`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            redacted = redact_path(args[2])
            if redacted != args[2]:
                record.args = (*args[:2], redacted, *args[3:])
        return True


def install_access_log_filters() -> None:
    """Idempotent. Aufruf beim Erzeugen der App und beim Start: uvicorn richtet sein Logging
    vor dem Import der App ein, ein spaeter angehaengter Filter bleibt erhalten."""
    logger = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(f, TicketRedactFilter) for f in logger.filters):
        logger.addFilter(TicketRedactFilter())
