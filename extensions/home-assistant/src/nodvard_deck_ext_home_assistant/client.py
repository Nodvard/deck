"""Schlanker, nur lesender Client für die REST-API von Home Assistant (er kennt nur `GET`)."""

from __future__ import annotations

import json
from typing import Any

from .parse import HaConfig, ParsedStates, is_status_ok, parse_config, parse_states
from .transport import (
    DEFAULT_TIMEOUT_S,
    MAX_SMALL_BYTES,
    MAX_STATES_BYTES,
    TOTAL_TIMEOUT_S,
    AuthFailed,
    HaError,
    HttpLike,
    Reply,
    get,
)

_INVALID = object()


def _json(reply: Reply) -> Any:
    try:
        return json.loads(reply.body)
    except (ValueError, RecursionError):
        # Kaputtes, absichtlich tief verschachteltes JSON oder eine HTML-Seite.
        return _INVALID


class HomeAssistantClient:
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
        return f"Home Assistant „{self._name}“"

    async def _get(self, path: str, *, max_bytes: int) -> Reply:
        # Das Token steht nur im Header, nie in einer Adresse, Meldung oder einem Log.
        return await get(
            self._http, f"{self._base}{path}", service=self.service, insecure_tls=self._insecure_tls, pin=self._pin,
            headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
            timeout_s=self._timeout_s, total_timeout_s=self._total_timeout_s, max_bytes=max_bytes,
        )

    def _status_problem(self, status: int) -> HaError:
        """Fester Satz zu einem Statuscode, der kein Erfolg ist."""
        service = self.service
        if status in (401, 403):
            head = (
                "Das Zugangs-Token wurde abgelehnt (HTTP 401)."
                if status == 401
                else "Das Zugangs-Token wurde abgelehnt, oder Home Assistant hat die Adresse dieses Rechners gesperrt (HTTP 403)."
            )
            return AuthFailed(
                f"{service}: {head} Prüfe in Home Assistant unter Profil → Sicherheit → „Langlebige Zugangs-Token“, ob es "
                "noch gültig ist, und trage ein neues in den Einstellungen ein. Danach „Verbindung testen“ drücken, "
                "sonst fragt Nodvard Deck erst in einer halben Stunde wieder. Nach mehreren abgelehnten Anmeldungen "
                "sperrt Home Assistant die Adresse des Rechners (Datei ip_bans.yaml); darum wird nicht öfter gefragt."
            )
        if status == 404:
            return HaError(
                f"{service}: Unter dieser Adresse läuft kein Home Assistant, oder die API ist ausgeschaltet. "
                "Prüfe die Adresse und ob in Home Assistant die Integration „API“ (Teil der Standard-Konfiguration) aktiv ist."
            )
        if status == 429:
            return HaError(f"{service} bremst Anfragen gerade (HTTP 429). Versuche es in einigen Minuten noch einmal.")
        if status in (502, 503, 504):
            return HaError(
                f"{service} ist gerade nicht bereit (HTTP {status}). Das ist beim Start oder Neustart von Home Assistant "
                "normal; sonst kann ein Proxy davor Home Assistant nicht erreichen. Versuche es gleich noch einmal."
            )
        if status >= 500:
            return HaError(f"{service} meldet einen Fehler (HTTP {status}). Schau in das Protokoll von Home Assistant.")
        return HaError(f"{service} hat die Anfrage abgelehnt (HTTP {status}).")

    def _bad_format(self, what: str) -> HaError:
        return HaError(
            f"Die Antwort von {self.service} auf {what} hat nicht das erwartete Format. "
            "Führt die Adresse wirklich zu Home Assistant?"
        )

    async def _json_ok(self, path: str, *, max_bytes: int) -> Any:
        reply = await self._get(path, max_bytes=max_bytes)
        if reply.status_code != 200:
            raise self._status_problem(reply.status_code)
        body = _json(reply)
        if body is _INVALID:
            raise self._bad_format(path)
        return body

    async def ping(self) -> None:
        """`GET /api/`. Wirft, wenn die API nicht antwortet oder das Token nicht angenommen wird."""
        if not is_status_ok(await self._json_ok("/api/", max_bytes=MAX_SMALL_BYTES)):
            raise self._bad_format("/api/")

    async def config(self) -> HaConfig:
        """`GET /api/config`: Version und Standortname."""
        parsed = parse_config(await self._json_ok("/api/config", max_bytes=MAX_SMALL_BYTES))
        if parsed is None:
            raise self._bad_format("/api/config")
        return parsed

    async def states(self, wanted: tuple[str, ...] = ()) -> ParsedStates:
        """`GET /api/states`; `wanted` sind die ausgewählten Entitäten."""
        parsed = parse_states(await self._json_ok("/api/states", max_bytes=MAX_STATES_BYTES), wanted)
        if parsed is None:
            raise self._bad_format("/api/states")
        return parsed
