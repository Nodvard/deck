"""Schlanker, nur lesender Client für die HTTP-Schnittstelle von Plex Media Server (er kennt nur `GET`)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from .models import LatestItem, Library, Playback
from .parse import (
    ServerInfo,
    library_of,
    parse_latest,
    parse_sections,
    parse_server_info,
    parse_sessions,
    parse_total,
)
from .transport import (
    DEFAULT_TIMEOUT_S,
    MAX_LIST_BYTES,
    MAX_SMALL_BYTES,
    TOTAL_TIMEOUT_S,
    AuthFailed,
    HttpLike,
    PlexError,
    Reply,
    get,
)

MAX_LATEST = 10
MAX_LIBRARIES = 30  # mehr Bibliotheken werden weder gezählt noch gezeigt
COUNT_PARALLEL = 4  # so viele Zähler gleichzeitig, damit Plex nicht auf einmal dreißig Anfragen bekommt
# Größe 0: Plex liefert nur `totalSize`, nicht die Einträge. Ohne Sammlungen, wie bei python-plexapi.
COUNT_QUERY = "?includeCollections=0&X-Plex-Container-Start=0&X-Plex-Container-Size=0"

_INVALID = object()


def _json(reply: Reply) -> Any:
    try:
        return json.loads(reply.body)
    except (ValueError, RecursionError):
        # Kaputtes, absichtlich tief verschachteltes JSON oder eine HTML-Seite.
        return _INVALID


@dataclass(frozen=True)
class LibraryReading:
    libraries: list[Library]
    count_error: PlexError | None  # erster Fehler eines Zählers; die Bibliothek bleibt dann ohne Zahl


class PlexClient:
    def __init__(
        self,
        http: HttpLike,
        *,
        name: str,
        base_url: str,
        token: str,
        insecure_tls: bool = False,
        pin: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        total_timeout_s: float = TOTAL_TIMEOUT_S,
    ) -> None:
        self._http = http
        self._name = name
        self._base = base_url.rstrip("/")
        self._token = token
        self._insecure_tls = insecure_tls
        self._pin = pin
        self._timeout_s = timeout_s
        self._total_timeout_s = total_timeout_s

    @property
    def service(self) -> str:
        return f"Plex „{self._name}“"

    async def _get(self, path: str, *, max_bytes: int, extra_headers: dict[str, str] | None = None) -> Reply:
        # Der Token steht nur im Header, nie in einer Adresse, Meldung oder einem Log; ohne `Accept` antwortet Plex mit XML.
        headers = {"X-Plex-Token": self._token, "Accept": "application/json", **(extra_headers or {})}
        return await get(
            self._http, f"{self._base}{path}", service=self.service, insecure_tls=self._insecure_tls, pin=self._pin,
            headers=headers, timeout_s=self._timeout_s, total_timeout_s=self._total_timeout_s, max_bytes=max_bytes,
        )

    def _status_problem(self, status: int) -> PlexError:
        """Fester Satz zu einem Statuscode, der kein Erfolg ist."""
        service = self.service
        if status in (401, 403):
            head = (
                "Der Token wurde abgelehnt (HTTP 401)."
                if status == 401
                else "Der Token wurde abgelehnt, oder er darf das nicht (HTTP 403)."
            )
            return AuthFailed(
                f"{service}: {head} Trage in den Einstellungen einen gültigen Token ein (die Beschreibung dort sagt, "
                "wo du ihn findest). Danach „Verbindung testen“ drücken, sonst fragt Nodvard Deck erst in fünf Minuten wieder."
            )
        if status == 404:
            return PlexError(
                f"{service}: Unter dieser Adresse läuft kein Plex Media Server, oder der Pfad stimmt nicht. "
                "Prüfe die Adresse; läuft Plex hinter einem Unterpfad, gehört er mit in die Adresse."
            )
        if status == 429:
            return PlexError(f"{service} bremst Anfragen gerade (HTTP 429). Versuche es in einigen Minuten noch einmal.")
        if status in (502, 503, 504):
            return PlexError(
                f"{service} ist gerade nicht bereit (HTTP {status}). Das ist beim Start oder Neustart von Plex "
                "normal; sonst kann ein Proxy davor Plex nicht erreichen. Versuche es gleich noch einmal."
            )
        if status >= 500:
            return PlexError(f"{service} meldet einen Fehler (HTTP {status}). Schau in das Protokoll von Plex.")
        return PlexError(f"{service} hat die Anfrage abgelehnt (HTTP {status}).")

    def _bad_format(self, what: str) -> PlexError:
        return PlexError(
            f"Die Antwort von {self.service} auf {what} hat nicht das erwartete Format. "
            "Führt die Adresse wirklich zu einem Plex Media Server?"
        )

    async def _json_ok(self, path: str, *, max_bytes: int, extra_headers: dict[str, str] | None = None) -> Any:
        reply = await self._get(path, max_bytes=max_bytes, extra_headers=extra_headers)
        if reply.status_code != 200:
            raise self._status_problem(reply.status_code)
        body = _json(reply)
        if body is _INVALID:
            raise self._bad_format(path.partition("?")[0])
        return body

    async def server_info(self) -> ServerInfo:
        """`GET /`: Name und Version; wirft, wenn Plex nicht antwortet oder den Token ablehnt."""
        # `/identity` nennt nur Version und Kennung (python-plexapi: Identity), nicht den Namen des Servers.
        parsed = parse_server_info(await self._json_ok("/", max_bytes=MAX_SMALL_BYTES))
        if parsed is None:
            raise self._bad_format("/")
        return parsed

    async def sessions(self) -> list[Playback]:
        """`GET /status/sessions`: die laufenden Wiedergaben aller Benutzer."""
        parsed = parse_sessions(await self._json_ok("/status/sessions", max_bytes=MAX_LIST_BYTES))
        if parsed is None:
            raise self._bad_format("/status/sessions")
        return parsed

    async def _count(self, key: str) -> int | None:
        body = await self._json_ok(f"/library/sections/{key}/all{COUNT_QUERY}", max_bytes=MAX_SMALL_BYTES)
        return parse_total(body)

    async def libraries(self) -> LibraryReading:
        """`GET /library/sections` und je Bibliothek nur der Zähler (Größe 0), nie die Einträge selbst."""
        parsed = parse_sections(await self._json_ok("/library/sections", max_bytes=MAX_SMALL_BYTES))
        if parsed is None:
            raise self._bad_format("/library/sections")
        sections = parsed[:MAX_LIBRARIES]
        gate = asyncio.Semaphore(COUNT_PARALLEL)

        async def count(key: str | None) -> tuple[int | None, PlexError | None]:
            if key is None:
                return None, None
            async with gate:
                try:
                    return await self._count(key), None
                except PlexError as exc:
                    return None, exc

        counted = await asyncio.gather(*(count(s.key) for s in sections))
        errors = [e for _, e in counted if e is not None]
        rejected = next((e for e in errors if isinstance(e, AuthFailed)), None)
        if rejected is not None:
            raise rejected
        libraries = [library_of(s, n) for s, (n, _) in zip(sections, counted, strict=True)]
        return LibraryReading(libraries, errors[0] if errors else None)

    async def latest(self) -> list[LatestItem]:
        """`GET /library/recentlyAdded`, neueste zuerst."""
        # Wie python-plexapi: Anfang und Größe als Kopfzeilen.
        window = {"X-Plex-Container-Start": "0", "X-Plex-Container-Size": str(MAX_LATEST)}
        body = await self._json_ok("/library/recentlyAdded", max_bytes=MAX_LIST_BYTES, extra_headers=window)
        parsed = parse_latest(body)
        if parsed is None:
            raise self._bad_format("/library/recentlyAdded")
        return parsed[:MAX_LATEST]
