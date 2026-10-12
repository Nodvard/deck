"""Echte Adresse und echtes Schema hinter einem vertrauenswürdigen Reverse-Proxy (reine ASGI-Middleware).

`X-Forwarded-For` und `X-Forwarded-Proto` gelten nur, wenn die direkte Gegenstelle in `system.trusted_proxies` steht."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send

from ..core import trusted_proxies
from ..core.trusted_proxies import Address, TrustedProxies, parse_address

logger = logging.getLogger("nodvard_deck.trusted_proxies")

# Unter `scope[SCOPE_KEY]` liegt ein `Forwarding` mit der ursprünglichen Gegenstelle, für die Anzeige in den Einstellungen
# (`GET /system/connection`). `X-Forwarded-Host` und `-Port` werden nicht gelesen: Keine Stelle baut Adressen aus dem Host.
SCOPE_KEY = "nodvard_deck.forwarding"
MAX_HOPS = 32
"""So viele Einträge von rechts werden höchstens angesehen; längere Ketten gibt es in einem Heimnetz nicht."""

_HTTP_SCHEMES = {"http": "http", "https": "https", "ws": "http", "wss": "https"}
_WS_SCHEMES = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}


@dataclass(frozen=True)
class Forwarding:
    peer: str | None
    """Die direkte Gegenstelle, wie die Verbindung sie zeigt (`None` ohne IP, z. B. Unix-Socket)."""
    trusted: bool
    """Die Gegenstelle steht in der Liste: ihre Kopfzeilen wurden ausgewertet."""
    forwarded_headers: bool
    """Die Anfrage trug `X-Forwarded-For` oder `X-Forwarded-Proto` (egal, ob sie galten)."""


def _split_values(values: list[bytes]) -> list[str]:
    joined = ",".join(value.decode("latin-1") for value in values)
    return [part.strip() for part in joined.split(",") if part.strip()]


def _parse_hop(entry: str) -> tuple[Address, int] | None:
    """Ein Eintrag aus `X-Forwarded-For`: Adresse mit optionalem Port. `None`, wenn er unlesbar ist."""
    host, port_text = entry, ""
    if entry.startswith("["):
        end = entry.find("]")
        if end == -1:
            return None
        host, rest = entry[1:end], entry[end + 1:]
        if rest:
            if not rest.startswith(":"):
                return None
            port_text = rest[1:]
    elif entry.count(":") == 1:
        host, port_text = entry.split(":", 1)
    port = 0
    if port_text:
        # `isascii`: `isdigit` nimmt auch Ziffern wie „²“, an denen `int()` scheitert.
        if not (port_text.isascii() and port_text.isdigit()) or len(port_text) > 5 or int(port_text) > 65535:
            return None
        port = int(port_text)
    ip = parse_address(host)
    if ip is None:
        return None
    return ip, port


# Von rechts gelesen: Jeder Proxy hängt die Adresse an, von der er die Anfrage bekam. Die erste Adresse, die selbst kein
# vertrauenswürdiger Proxy ist, ist der Client; was links davon steht, kann der Client selbst geschrieben haben. Ein
# unlesbarer Eintrag beendet die Suche, es bleibt die zuletzt geprüfte Adresse. Steht ein ganzes Netz in der Liste, gilt
# jedes Gerät darin als Proxy und wird geglaubt; darum sind Netze begrenzt (`core/trusted_proxies.py`).
def client_from_chain(entries: list[str], trust: TrustedProxies) -> tuple[str, int] | None:
    """Die Client-Adresse aus `X-Forwarded-For` (Einträge schon getrennt, links zuerst), gesehen von einer
    vertrauenswürdigen Gegenstelle. `None`, wenn kein Eintrag etwas taugt (dann bleibt die Gegenstelle)."""
    found: tuple[str, int] | None = None
    for entry in reversed(entries[-MAX_HOPS:]):
        hop = _parse_hop(entry)
        if hop is None:
            break  # Über einen unlesbaren Eintrag hinweg wird nie weiter links gelesen.
        ip, port = hop
        found = (str(ip), port)
        if not trust.contains(ip):
            break
    return found


# `scope["client"]` und `scope["scheme"]` werden im selben Scope ersetzt (wie bei uvicorns ProxyHeadersMiddleware), damit
# auch das Zugriffsprotokoll und jede spätere Middleware die echte Adresse sehen. Die von uvicorn taugt hier nicht: Ihre
# Liste steht beim Start fest, sie nimmt unlesbare Einträge als Client und vergleicht `::ffff:a.b.c.d` nicht mit einem
# IPv4-Netz. Das Image startet uvicorn darum mit `--no-proxy-headers` (sonst vertraute es vorab still `127.0.0.1`);
# es entscheidet nur diese Einstellung.
def apply_forwarding(scope: Scope, trust: TrustedProxies) -> Forwarding:
    """Wertet die Kopfzeilen für diesen Scope aus (ändert ihn ggf.) und legt `Forwarding` unter `SCOPE_KEY` ab."""
    client = scope.get("client")
    peer_host = client[0] if client else None
    forwarded_for: list[bytes] = []
    forwarded_proto: list[bytes] = []
    for name, value in scope.get("headers") or ():
        if name == b"x-forwarded-for":
            forwarded_for.append(value)
        elif name == b"x-forwarded-proto":
            forwarded_proto.append(value)
    peer_ip = parse_address(peer_host)
    trusted = trust.contains(peer_ip)
    info = Forwarding(peer=peer_host if isinstance(peer_host, str) else None, trusted=trusted,
                      forwarded_headers=bool(forwarded_for or forwarded_proto))
    scope[SCOPE_KEY] = info
    if not trusted or peer_ip is None:
        # Kein eingetragener Proxy (auch nicht ohne IP, z. B. Unix-Socket): alles bleibt, wie es ist.
        return info
    try:
        found = client_from_chain(_split_values(forwarded_for), trust) if forwarded_for else None
        values = _split_values(forwarded_proto)
        # Nur der rechteste Wert zählt; Traefik schickt bei WebSockets auch `ws`/`wss`.
        table = _WS_SCHEMES if scope["type"] == "websocket" else _HTTP_SCHEMES
        scheme = table.get(values[-1].lower()) if values else None
    except Exception as exc:  # noqa: BLE001 - lieber wie ohne Proxy als jede Anfrage mit 500
        logger.warning("forwarded_headers_unreadable error=%s", type(exc).__name__)
        return info
    if found is not None:
        scope["client"] = found
    if scheme is not None:
        scope["scheme"] = scheme
    return info


class ProxyHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            apply_forwarding(scope, trusted_proxies.current())
        await self.app(scope, receive, send)
