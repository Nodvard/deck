"""Feste Sicherheits-Kopfzeilen für jede HTTP-Antwort (reine ASGI-Middleware); eine Route kann sie nicht lockern.

Eine eigene CSP einer Route bleibt stehen und bekommt nur `frame-ancestors 'none'`. Die Notseite (`rescue.py`) ist ein
eigener Server und setzt dieselben Kopfzeilen selbst."""

from __future__ import annotations

import re
from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Nur Geräte, die Deck nie braucht. Vollbild (Konsole) und Zwischenablage bleiben beim Standard `self`.
PERMISSIONS_POLICY = (
    b"accelerometer=(), bluetooth=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), hid=(), "
    b"magnetometer=(), microphone=(), midi=(), payment=(), serial=(), usb=()"
)
FIXED_HEADERS: dict[bytes, bytes] = {
    # Kein fremder Rahmen um Deck (Klick-Fallen), wie `frame-ancestors 'none'` in der CSP.
    b"x-frame-options": b"DENY",
    b"x-content-type-options": b"nosniff",
    # Nicht `no-referrer`: Browser schicken dann bei einem Formular `Origin: null` (siehe `rescue.py`).
    b"referrer-policy": b"same-origin",
    # Wirkt nur über https oder localhost; Deck öffnet fremde Seiten ohnehin mit `noopener`.
    b"cross-origin-opener-policy": b"same-origin",
    b"permissions-policy": PERMISSIONS_POLICY,
}
CSP_HEADER = b"content-security-policy"
CONTENT_TYPE = b"content-type"
FRAME_ANCESTORS = "frame-ancestors 'none'"
DATA_POLICY = f"default-src 'none'; {FRAME_ANCESTORS}"
"""Für JSON ohne eigene CSP: Direkt im Browser geöffnet, lädt die Antwort nichts nach."""
CORP_HEADER = b"cross-origin-resource-policy"
EMBEDDABLE_PATHS = frozenset({"/api/v1/health", "/api/v1/branding/logo", "/api/v1/branding/favicon"})
APP_ICON = re.compile(r"/api/v1/app/icon-[0-9]+(-maskable)?\.png")
"""Öffentlich ohne Anmeldung: Eine Startseite wie Homarr darf Deck-Symbole zeigen und Deck anpingen."""


def resource_policy(path: str) -> bytes:
    """`Cross-Origin-Resource-Policy` für `path`: `cross-origin` nur für die öffentlichen Symbole und `/health`."""
    if path in EMBEDDABLE_PATHS or APP_ICON.fullmatch(path):
        return b"cross-origin"
    return b"same-origin"


def with_frame_ancestors(policy: str) -> str:
    """`policy` mit `frame-ancestors 'none'` am Ende; eine vorhandene `frame-ancestors`-Angabe fällt weg."""
    directives = [part.strip() for part in policy.split(";") if part.strip()]
    kept = [d for d in directives if d.split(None, 1)[0].lower() != "frame-ancestors"]
    return "; ".join([*kept, FRAME_ANCESTORS])


def secure_headers(headers: Iterable[tuple[bytes, bytes]], path: str = "") -> list[tuple[bytes, bytes]]:
    """Die Kopfzeilen einer Antwort auf `path` samt den festen Sicherheits-Kopfzeilen."""
    result: list[tuple[bytes, bytes]] = []
    has_policy = False
    is_json = False
    for name, value in headers:
        lower = name.lower()
        if lower in FIXED_HEADERS or lower == CORP_HEADER:
            continue  # unten fest gesetzt, genau einmal
        if lower == CSP_HEADER:
            has_policy = True
            value = with_frame_ancestors(value.decode("latin-1")).encode("latin-1")
        elif lower == CONTENT_TYPE:
            is_json = _is_json(value)
        result.append((name, value))
    result.extend(FIXED_HEADERS.items())
    result.append((CORP_HEADER, resource_policy(path)))
    if not has_policy:
        result.append((CSP_HEADER, (DATA_POLICY if is_json else FRAME_ANCESTORS).encode("ascii")))
    return result


def _is_json(content_type: bytes) -> bool:
    media = content_type.split(b";", 1)[0].strip().lower()
    return media == b"application/json" or media.endswith(b"+json")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = secure_headers(message.get("headers", []), scope.get("path", ""))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
