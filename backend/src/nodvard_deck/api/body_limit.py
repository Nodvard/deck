"""Obergrenze für die Größe einer Anfrage (reine ASGI-Middleware, vor allen Routen).

Ohne sie liest FastAPI jeden Körper ganz in den Speicher, bevor geprüft wird, wer anfragt (auch bei `POST /auth/login`)."""

from __future__ import annotations

import json
import sys

from nodvard_sdk.http import MAX_BODY_ATTR
from starlette.types import ASGIApp, Message, Receive, Scope, Send


NO_LIMIT = sys.maxsize
"""Für `max_body_bytes(...)`: keine eigene Obergrenze (nur für Routen, die den Körper als Strom weiterreichen und nie
ganz in den Speicher lesen)."""


def format_bytes(value: int) -> str:
    """Lesbare Größe für die Fehlermeldung (1 MB, 20 MB, 4 GB)."""
    for unit, size in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if value >= size:
            number = value / size
            return f"{number:.0f} {unit}" if number >= 10 or number == int(number) else f"{number:.1f} {unit}".replace(".", ",")
    return f"{value} Bytes"


def route_limit(scope: Scope) -> int | None:
    """Eigene Grenze der Route, auf die die Anfrage trifft (None: keine erklärt).

    Erst lesbar, nachdem der Router die Anfrage einer Route zugeordnet hat, also sobald ein Handler den Körper anfordert."""
    route = scope.get("route")
    endpoint = getattr(route, "endpoint", None) or scope.get("endpoint")
    declared = getattr(endpoint, MAX_BODY_ATTR, None)
    if callable(declared):
        try:
            declared = declared()
        except Exception:  # noqa: BLE001 - eine kaputte Grenze darf Anfragen nicht umwerfen
            return None
    return declared if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0 else None


def too_large_message(limit: int) -> str:
    """Text der 413-Antwort; auch für Handler, die eine Länge selbst vor dem Öffnen des Ziels prüfen."""
    return f"Die Anfrage ist zu groß (erlaubt sind höchstens {format_bytes(limit)})."


async def _send_413(send: Send, limit: int) -> None:
    body = json.dumps({"detail": too_large_message(limit)}, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# Eine zu große `Content-Length` wird mit 413 beantwortet, sobald die Anwendung den Körper anfordert (ohne ein Byte zu
# lesen); ohne Länge (chunked) wird mitgezählt und beim Überschreiten mit 413 und getrennter Verbindung abgebrochen.
# Die höhere Grenze einer Route (`nodvard_sdk.max_body_bytes`) kennt der Router erst beim Lesen, daher prüft die
# Middleware dort. WebSockets begrenzt uvicorn (`--ws-max-size` im Image).
class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.default_limit = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        default = self.default_limit
        limit: int | None = None  # erst beim ersten Lesen bekannt (dann ist die Route zugeordnet)

        def effective_limit() -> int:
            nonlocal limit
            if limit is None:
                limit = max(default, route_limit(scope) or 0)
            return limit

        declared_length: int | None = None
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared_length = int(value)
                except ValueError:
                    declared_length = None
                break

        seen = 0
        rejected = False
        started = False
        length_checked = False

        async def reject() -> Message:
            nonlocal rejected
            rejected = True
            if not started:
                await _send_413(send, effective_limit())
            return {"type": "http.disconnect"}

        async def guarded_receive() -> Message:
            nonlocal seen, length_checked
            if rejected:
                return {"type": "http.disconnect"}
            if not length_checked:
                length_checked = True
                # Länge laut Kopf zu groß: abweisen, ohne ein Byte des Körpers zu lesen.
                if declared_length is not None and declared_length > default and declared_length > effective_limit():
                    return await reject()
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > default and seen > effective_limit():
                    return await reject()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if rejected:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, guarded_receive, guarded_send)
        except Exception:
            if not rejected:
                raise
            # Die Anwendung hat auf die gemeldete Trennung mit einem Fehler reagiert; geantwortet ist bereits.
