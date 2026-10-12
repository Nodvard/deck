"""Verständliche, nie leere deutsche Texte für Verbindungsfehler, die die Oberfläche unverändert zeigen kann.
Ausnahmen mit fertigem deutschem Text tragen `readable = True` (z. B. `core.ssh.SshError`) und kommen unverändert durch."""

from __future__ import annotations

import errno
import socket

import asyncssh
from nodvard_sdk.errors import HostUnreachable

_NO_ROUTE = frozenset({errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EHOSTDOWN})


def _where(address: str | None, port: int | None) -> str:
    """` (adresse:port)` oder leer."""
    if not address:
        return ""
    return f" ({address}:{port})" if port else f" ({address})"


def describe_connection_error(
    exc: BaseException, *, address: str | None = None, port: int | None = None, network: bool = False
) -> str:
    """Deutscher Satz zu einem Verbindungsfehler, nie leer (`str(TimeoutError())` ist es, andere Texte sind englisch).
    `network=True`: `exc` entstand beim Verbindungsaufbau, dann ist auch ein sonst unbekannter `OSError` ein Netzfehler
    ("Multiple exceptions: ..." bei mehreren Adressen) und sein Text bleibt als Zusatz stehen, er nennt nur Adressen."""
    text = str(exc).strip()
    if getattr(exc, "readable", False) and text:
        return text
    if isinstance(exc, TimeoutError):  # asyncio.TimeoutError ist seit Python 3.11 dasselbe
        where = f" bei {address}:{port}" if address and port else (f" bei {address}" if address else "")
        return f"Server antwortet nicht (Zeitüberschreitung{where}). Ist er eingeschaltet und im Netz?"
    if isinstance(exc, ConnectionRefusedError):
        return f"Der Server lehnt die Verbindung ab{_where(address, port)}. Läuft dort der Dienst?"
    if isinstance(exc, socket.gaierror):
        return f"Den Namen {address} kennt das Netz nicht." if address else "Der Servername ist dem Netz unbekannt."
    if isinstance(exc, (asyncssh.ConnectionLost, asyncssh.DisconnectError, ConnectionError)):
        # Der Originaltext (oft englisch, bei einer eigenen Quelle auch mal deutsch) bleibt als Zusatz stehen.
        return f"Die Verbindung zum Server ist abgebrochen ({text})." if text else "Die Verbindung zum Server ist abgebrochen."
    if isinstance(exc, OSError) and exc.errno in _NO_ROUTE:
        return f"Kein Weg zum Server{_where(address, port)}. Stimmt die Adresse, und ist er im selben Netz?"
    if isinstance(exc, OSError) and network:
        where = _where(address, port)
        return f"Keine Verbindung zum Server{where}: {text}" if text else f"Keine Verbindung zum Server{where}."
    if isinstance(exc, OSError):
        # Ein Dateifehler (PermissionError o. Ä.) hat mit dem Netz nichts zu tun, sein Text nennt Pfade des Servers
        # ("[Errno 13] Permission denied: /app/data/..."): Die Person bekommt nur den Namen der Ausnahme, der volle Text
        # steht im Protokoll.
        return type(exc).__name__
    # Alles andere: der eigene Text, sonst der Name der Ausnahme.
    return text or type(exc).__name__


def is_expected_connection_error(exc: BaseException) -> bool:
    """Erwartbare Fehler: Server aus oder nicht erreichbar, Zugang abgelehnt, Schlüssel unbestätigt, kein Zugang
    eingerichtet. Sie gehören als eine Zeile ins Protokoll, ohne Traceback (`deploy_pi.sh` wertet "Traceback" als
    gescheiterten Start). Von den `OSError`s zählen nur Netzfehler; ein Dateifehler bleibt ein echter Fehler."""
    if getattr(exc, "readable", False):
        return True
    if isinstance(exc, (asyncssh.Error, HostUnreachable, ConnectionError, TimeoutError, socket.gaierror)):
        return True
    return isinstance(exc, OSError) and exc.errno in _NO_ROUTE
