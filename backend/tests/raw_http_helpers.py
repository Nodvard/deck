"""Ein lokaler TCP-Server, der jede Anfrage mit fest vorgegebenen Bytes beantwortet. Fuer Antworten, die
weder ein echter HTTP-Server (uvicorn) noch `httpx.MockTransport` erzeugen kann: kaputte Kopfzeilen laufen
nur ueber den echten Parser (h11), und genau dessen Fehlertexte zitieren die fremde Antwort."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

# Kaputte Antworten eines fremden Servers. "angreifer" darf in keinem Text und keiner Ursachenkette stehen.
REDIRECT_ANSWERS = {
    # Ungueltiger Punycode-Name: httpx scheitert beim Zusammenbauen der Weiterleitung mit einem
    # `ValueError` (idna), dessen Text Teile des Namens nennt.
    "punycode": b"HTTP/1.1 302 Found\r\nLocation: http://xn--angreifer-ey9f.example/login\r\nContent-Length: 0\r\n\r\n",
    # Ungueltiges Zeichen im Header: h11 meldet "illegal header line: bytearray(b'Location: ...')".
    "nul": b"HTTP/1.1 302 Found\r\nLocation: https://angreifer.example/neu\x00\r\nContent-Length: 0\r\n\r\n",
    "space_before_colon": b"HTTP/1.1 302 Found\r\nLocation : https://angreifer.example/neu\r\nContent-Length: 0\r\n\r\n",
    # UTF-8 im Namen: httpx scheitert an der Umwandlung in Punycode.
    "utf8_name": "HTTP/1.1 302 Found\r\nLocation: http://angreifer\u2980.example/\r\nContent-Length: 0\r\n\r\n".encode(),
}
BROKEN_ANSWERS = {
    "status_line": b"HTTP/1.1 302 https://angreifer.example/neu\x00\r\nContent-Length: 0\r\n\r\n",
    "header_line": b"HTTP/1.1 200 OK\r\nX-Hinweis: neue Adresse https://angreifer.example\x00\r\nContent-Length: 0\r\n\r\n",
    "disconnect": b"",
}


# Antworten auf den WebSocket-Handshake (`websockets` zitiert Kopfzeilen und Statuszeilen).
WEBSOCKET_ANSWERS = {
    "redirect": b"HTTP/1.1 302 Found\r\nLocation: https://angreifer.example/neu\r\nContent-Length: 0\r\n\r\n",
    "bad_accept": (
        b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: angreifer-xyz\r\n\r\n"
    ),
    "forbidden": b"HTTP/1.1 403 Forbidden\r\nContent-Length: 11\r\n\r\nangreifer!!",
    "no_upgrade": b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
    "garbage": b"angreifer\x00 kein HTTP\r\n\r\n",
    "bad_subprotocol": (
        b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Protocol: angreifer\r\nSec-WebSocket-Accept: x\r\n\r\n"
    ),
}


async def _read_request(reader: asyncio.StreamReader) -> None:
    """Liest die Anfrage samt Koerper. Ungelesene Bytes liessen den Kern die Verbindung beim Schliessen
    zuruecksetzen (RST), und der Client saehe dann statt der Antwort nur "Connection reset"."""
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").lower().split("\r\n")
    length = next((int(line.split(":", 1)[1]) for line in lines if line.startswith("content-length:")), 0)
    if length:
        await reader.readexactly(length)
    elif any(line.startswith("transfer-encoding:") and "chunked" in line for line in lines):
        await reader.readuntil(b"0\r\n\r\n")


@contextlib.asynccontextmanager
async def raw_http_server(answer: bytes) -> AsyncIterator[str]:
    """`http://127.0.0.1:<port>`; jede Verbindung bekommt `answer` und wird dann geschlossen."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            with contextlib.suppress(asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
                await _read_request(reader)
            writer.write(answer)
            with contextlib.suppress(ConnectionError):
                await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()


def whole_chain(exc: BaseException) -> str:
    """Alles, was ein Fehlertext aus der Ursachenkette abschreiben koennte (wie `describe_exception`)."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " | ".join(parts)
