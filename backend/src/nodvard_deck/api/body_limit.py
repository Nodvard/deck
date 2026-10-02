"""Obergrenze fuer die Groesse einer Anfrage (reine ASGI-Middleware, vor allen Routen).

Ohne diese Grenze liest FastAPI jeden Koerper vollstaendig in den Speicher, bevor ueberhaupt
geprueft wird, wer anfragt oder ob der Inhalt gueltig ist -- auch bei `POST /auth/login` ohne
Anmeldung und auch bei `Content-Type: text/plain`. Eine einzige riesige Anfrage reicht dann,
um einen kleinen Rechner in den Speichermangel zu treiben.

Ablauf:
- Steht eine `Content-Length` ueber der Grenze, antwortet die Middleware mit 413, sobald die
  Anwendung den Koerper anfordert, und liest kein Byte davon. Handler, die ihn gar nicht anfordern
  (zum Beispiel weil die Anmeldung schon scheitert), antworten wie gewohnt.
- Ohne Laenge (`Transfer-Encoding: chunked`) zaehlt sie die gelesenen Bytes mit. Wird die Grenze
  ueberschritten, antwortet sie mit 413 und meldet der Anwendung eine getrennte Verbindung;
  weitere Antworten der Anwendung werden verworfen.
- Die allgemeine Grenze ist `Settings.max_body_bytes`. Routen, die bewusst grosse Uploads annehmen,
  tragen eine eigene, hoehere Grenze (`nodvard_sdk.max_body_bytes`, auch fuer Erweiterungen). Sie
  wird erst herangezogen, wenn eine Anfrage die allgemeine Grenze ueberschreitet; normale Anfragen
  kosten also nur einen Zahlenvergleich. Die Route kennt der Router erst, wenn der Handler liest; daher
  prueft die Middleware beim Lesen und nicht davor.
- WebSocket-Verbindungen laufen unveraendert durch; dort begrenzt uvicorn die Nachrichten (uvicorn-Standard
  16 MiB, das Image startet mit `--ws-max-size 1048576`, also 1 MiB).
"""

from __future__ import annotations

import json
import sys

from nodvard_sdk.http import MAX_BODY_ATTR
from starlette.types import ASGIApp, Message, Receive, Scope, Send


NO_LIMIT = sys.maxsize
"""Fuer `max_body_bytes(...)`: keine eigene Obergrenze (nur fuer Routen, die den Koerper als Strom
weiterreichen und nie ganz in den Speicher lesen)."""


def format_bytes(value: int) -> str:
    """Lesbare Groesse fuer die Fehlermeldung (1 MB, 20 MB, 4 GB)."""
    for unit, size in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if value >= size:
            number = value / size
            return f"{number:.0f} {unit}" if number >= 10 or number == int(number) else f"{number:.1f} {unit}".replace(".", ",")
    return f"{value} Bytes"


def route_limit(scope: Scope) -> int | None:
    """Eigene Grenze der Route, auf die die Anfrage trifft (None: keine erklaert).

    Erst lesbar, nachdem der Router die Anfrage einer Route zugeordnet hat; das ist der Fall,
    sobald ein Handler den Koerper anfordert."""
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
    """Text der 413-Antwort; auch fuer Handler, die eine Laenge selbst vor dem Oeffnen des Ziels pruefen."""
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
                # Laenge laut Kopf zu gross: abweisen, ohne ein Byte des Koerpers zu lesen.
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
