"""Sicherheits-Kopfzeilen für jede HTTP-Antwort (reine ASGI-Middleware).

Keine fremde Seite darf Deck in einen Rahmen laden (`X-Frame-Options: DENY` und `frame-ancestors 'none'`, Schutz vor
Klick-Fallen), der Browser rät keinen Dateityp (`nosniff`), und fremde Seiten erfahren beim Klick auf einen Link nicht,
welche Deck-Seite offen war (`Referrer-Policy: same-origin`).

Setzt eine Route selbst eine `Content-Security-Policy` (Dateien von Nutzern mit `sandbox`, die App-Seite mit ihren Regeln
für Stile), bleiben ihre Regeln stehen; die Middleware ersetzt darin nur eine eigene `frame-ancestors`-Angabe durch
`'none'`. Die drei festen Kopfzeilen setzt sie immer, eine Route kann sie nicht lockern. WebSockets laufen unverändert
durch. Die Notseite (`rescue.py`) ist ein eigener Server und setzt dieselben Kopfzeilen selbst."""

from __future__ import annotations

from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

FIXED_HEADERS: dict[bytes, bytes] = {
    b"x-frame-options": b"DENY",
    b"x-content-type-options": b"nosniff",
    # Nicht `no-referrer`: Browser schicken dann bei einem Formular `Origin: null` (siehe `rescue.py`).
    b"referrer-policy": b"same-origin",
}
CSP_HEADER = b"content-security-policy"
FRAME_ANCESTORS = "frame-ancestors 'none'"


def with_frame_ancestors(policy: str) -> str:
    """`policy` mit `frame-ancestors 'none'` am Ende; eine vorhandene `frame-ancestors`-Angabe fällt weg."""
    directives = [part.strip() for part in policy.split(";") if part.strip()]
    kept = [d for d in directives if d.split(None, 1)[0].lower() != "frame-ancestors"]
    return "; ".join([*kept, FRAME_ANCESTORS])


def secure_headers(headers: Iterable[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """Die Kopfzeilen einer Antwort mit den festen Sicherheits-Kopfzeilen und `frame-ancestors 'none'` in jeder CSP."""
    result: list[tuple[bytes, bytes]] = []
    has_policy = False
    for name, value in headers:
        lower = name.lower()
        if lower in FIXED_HEADERS:
            continue  # unten fest gesetzt, genau einmal
        if lower == CSP_HEADER:
            has_policy = True
            value = with_frame_ancestors(value.decode("latin-1")).encode("latin-1")
        result.append((name, value))
    result.extend(FIXED_HEADERS.items())
    if not has_policy:
        result.append((CSP_HEADER, FRAME_ANCESTORS.encode("ascii")))
    return result


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": secure_headers(message.get("headers", []))}
            await send(message)

        await self.app(scope, receive, send_with_headers)
