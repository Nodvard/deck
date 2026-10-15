"""Schlanker, nur lesender Client für die REST-API von Jellyfin (er kennt nur `GET`)."""

from __future__ import annotations

import json
from typing import Any

from .models import LatestItem, LibraryCounts, Playback
from .parse import (
    ServerInfo,
    parse_counts,
    parse_latest,
    parse_sessions,
    parse_system_info,
)
from .transport import (
    DEFAULT_TIMEOUT_S,
    MAX_LIST_BYTES,
    MAX_SMALL_BYTES,
    TOTAL_TIMEOUT_S,
    AuthFailed,
    HttpLike,
    JellyfinError,
    Reply,
    get,
)

# Neueste Filme und Folgen. Ohne Benutzer geht `/Items/Latest` nicht (404), `/Items` mit API-Schlüssel schon;
# Platzhalter-Folgen (Virtual) und Bilder bleiben draußen, `DateCreated` ist kein Standardfeld.
MAX_LATEST = 10
LATEST_QUERY = (
    "?SortBy=DateCreated&SortOrder=Descending&Recursive=true&IncludeItemTypes=Movie,Episode"
    "&ExcludeLocationTypes=Virtual&Fields=DateCreated&EnableImages=false&EnableUserData=false"
    f"&EnableTotalRecordCount=false&Limit={MAX_LATEST}"
)

_INVALID = object()


def _json(reply: Reply) -> Any:
    try:
        return json.loads(reply.body)
    except (ValueError, RecursionError):
        # Kaputtes, absichtlich tief verschachteltes JSON oder eine HTML-Seite.
        return _INVALID


class JellyfinClient:
    def __init__(
        self,
        http: HttpLike,
        *,
        name: str,
        base_url: str,
        api_key: str,
        insecure_tls: bool = False,
        pin: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        total_timeout_s: float = TOTAL_TIMEOUT_S,
    ) -> None:
        self._http = http
        self._name = name
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._insecure_tls = insecure_tls
        self._pin = pin
        self._timeout_s = timeout_s
        self._total_timeout_s = total_timeout_s

    @property
    def service(self) -> str:
        return f"Jellyfin „{self._name}“"

    async def _get(self, path: str, *, max_bytes: int) -> Reply:
        # Der Schlüssel steht nur im Header, nie in einer Adresse, Meldung oder einem Log. Mehr als `Token`
        # braucht Jellyfin bei einem API-Schlüssel nicht (Client, Gerät und Version setzt es selbst ein).
        return await get(
            self._http, f"{self._base}{path}", service=self.service, insecure_tls=self._insecure_tls, pin=self._pin,
            headers={"Authorization": f'MediaBrowser Token="{self._api_key}"', "Accept": "application/json"},
            timeout_s=self._timeout_s, total_timeout_s=self._total_timeout_s, max_bytes=max_bytes,
        )

    def _status_problem(self, status: int) -> JellyfinError:
        """Fester Satz zu einem Statuscode, der kein Erfolg ist."""
        service = self.service
        if status in (401, 403):
            head = (
                "Der API-Schlüssel wurde abgelehnt (HTTP 401)."
                if status == 401
                else "Der API-Schlüssel wurde abgelehnt, oder er darf das nicht (HTTP 403)."
            )
            return AuthFailed(
                f"{service}: {head} Prüfe in Jellyfin unter Dashboard → API-Schlüssel, ob der Schlüssel noch "
                "vorhanden ist, und trage bei Bedarf einen neuen in den Einstellungen ein. Danach „Verbindung "
                "testen“ drücken, sonst fragt Nodvard Deck erst in fünf Minuten wieder."
            )
        if status == 404:
            return JellyfinError(
                f"{service}: Unter dieser Adresse läuft kein Jellyfin, oder der Pfad stimmt nicht. "
                "Prüfe die Adresse; läuft Jellyfin hinter einem Unterpfad, gehört er mit in die Adresse."
            )
        if status == 429:
            return JellyfinError(f"{service} bremst Anfragen gerade (HTTP 429). Versuche es in einigen Minuten noch einmal.")
        if status in (502, 503, 504):
            return JellyfinError(
                f"{service} ist gerade nicht bereit (HTTP {status}). Das ist beim Start oder Neustart von Jellyfin "
                "normal; sonst kann ein Proxy davor Jellyfin nicht erreichen. Versuche es gleich noch einmal."
            )
        if status >= 500:
            return JellyfinError(f"{service} meldet einen Fehler (HTTP {status}). Schau in das Protokoll von Jellyfin.")
        return JellyfinError(f"{service} hat die Anfrage abgelehnt (HTTP {status}).")

    def _bad_format(self, what: str) -> JellyfinError:
        return JellyfinError(
            f"Die Antwort von {self.service} auf {what} hat nicht das erwartete Format. "
            "Führt die Adresse wirklich zu Jellyfin?"
        )

    async def _json_ok(self, path: str, *, max_bytes: int) -> Any:
        reply = await self._get(path, max_bytes=max_bytes)
        if reply.status_code != 200:
            raise self._status_problem(reply.status_code)
        body = _json(reply)
        if body is _INVALID:
            raise self._bad_format(path.partition("?")[0])
        return body

    async def system_info(self) -> ServerInfo:
        """`GET /System/Info`: Name und Version; wirft, wenn Jellyfin nicht antwortet oder den Schlüssel ablehnt."""
        parsed = parse_system_info(await self._json_ok("/System/Info", max_bytes=MAX_SMALL_BYTES))
        if parsed is None:
            raise self._bad_format("/System/Info")
        return parsed

    async def sessions(self) -> list[Playback]:
        """`GET /Sessions`: die laufenden Wiedergaben aller Benutzer."""
        parsed = parse_sessions(await self._json_ok("/Sessions", max_bytes=MAX_LIST_BYTES))
        if parsed is None:
            raise self._bad_format("/Sessions")
        return parsed

    async def counts(self) -> LibraryCounts:
        """`GET /Items/Counts`: Zahl der Filme, Serien, Folgen und so weiter."""
        parsed = parse_counts(await self._json_ok("/Items/Counts", max_bytes=MAX_SMALL_BYTES))
        if parsed is None:
            raise self._bad_format("/Items/Counts")
        return parsed

    async def latest(self) -> list[LatestItem]:
        """`GET /Items`, neueste zuerst."""
        parsed = parse_latest(await self._json_ok(f"/Items{LATEST_QUERY}", max_bytes=MAX_LIST_BYTES))
        if parsed is None:
            raise self._bad_format("/Items")
        return parsed[:MAX_LATEST]
