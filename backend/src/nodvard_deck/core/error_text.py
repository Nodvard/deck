"""Verstaendliche Texte fuer Verbindungsfehler.

Manche Ausnahmen haben keinen Text: `str(TimeoutError())` ist leer, und aus einem leeren Text wurde
bei Terminal und Dateien "...Fehler: " ohne jeden Grund. Und die Texte der
anderen sind englisch ("No route to host"). `describe_connection_error()` macht daraus einen
deutschen Satz, den die Oberflaeche unveraendert zeigen kann -- und der nie leer ist.

Kennt keine konkrete Quelle (SSH, WebDAV, ...): dieselbe Funktion dient dem Dateimanager, der
Terminal-Bruecke und der Aktionsausfuehrung. Ausnahmen, deren Text schon ein fertiger deutscher
Satz ist, tragen `readable = True` (z. B. `core.ssh.SshError`) und kommen unveraendert durch.

Nur Netzfehler gelten als "erwartbar". Ein Dateifehler (`PermissionError`, `FileNotFoundError` ...)
ist auch ein `OSError`, hat aber nichts mit dem Netz zu tun: sein Text enthaelt Pfade des Servers
und gehoert nicht in eine Antwort an die Oberflaeche, und er behaelt seinen Traceback im Protokoll.
"""

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
    """Deutscher Satz zu einem Verbindungsfehler; nie leer. `address`/`port` (falls bekannt)
    stehen mit im Text.

    `network=True`: der Aufrufer weiss, dass `exc` beim Verbindungsaufbau entstand (z. B. aus
    `asyncssh.connect`). Dann ist auch ein sonst unbekannter `OSError` ein Netzfehler ("Multiple
    exceptions: ...", wenn ein Name auf mehrere Adressen zeigt), und sein Text bleibt als Zusatz stehen
    -- er nennt nur Adressen, keine Pfade."""
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
        # Ein Dateifehler (PermissionError o. Ae.) hat mit dem Netz nichts zu tun. Sein Text nennt
        # Pfade des Servers ("[Errno 13] Permission denied: /app/data/...") -- die Person bekommt nur
        # den Namen der Ausnahme, der volle Text steht im Protokoll.
        return type(exc).__name__
    # Alles andere: der eigene Text, und wenn es keinen gibt, wenigstens der Name der Ausnahme.
    return text or type(exc).__name__


def is_expected_connection_error(exc: BaseException) -> bool:
    """Server aus oder nicht erreichbar, Zugang abgelehnt, Server-Schluessel unbestaetigt, kein Zugang
    eingerichtet: kein Fehler im Programm. Solche Faelle gehoeren als EINE Zeile ins Protokoll, nicht
    mit Traceback (`deploy_pi.sh` wertet "Traceback" im Protokoll als gescheiterten Start).

    Von den `OSError`s zaehlen nur die Netzfehler (Zeitueberschreitung, abgelehnt, abgebrochen, Name
    unbekannt, kein Weg zum Server). Ein Dateifehler bleibt ein echter Fehler."""
    if getattr(exc, "readable", False):
        return True
    if isinstance(exc, (asyncssh.Error, HostUnreachable, ConnectionError, TimeoutError, socket.gaierror)):
        return True
    return isinstance(exc, OSError) and exc.errno in _NO_ROUTE
