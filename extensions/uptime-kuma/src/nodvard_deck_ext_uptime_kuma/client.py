"""Schlanker, nur lesender Client für Uptime Kuma: `/metrics` und die öffentliche Status-Seite."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote

from .metrics import MonitorData, collect_monitors, parse_prometheus
from .statuspage import StatusPage, parse_heartbeats, parse_monitors
from .transport import DEFAULT_TIMEOUT_S, AuthFailed, HttpLike, KumaError, Reply, get


def basic_header(key: str) -> str:
    """Kuma nimmt den API-Schlüssel als Passwort der Basic-Anmeldung, der Benutzername bleibt leer."""
    return "Basic " + base64.b64encode(f":{key}".encode()).decode("ascii")


def _json(reply: Reply) -> Any:
    try:
        return json.loads(reply.body)
    except (ValueError, RecursionError):
        # Kaputtes oder absichtlich tief verschachteltes JSON zählt als "kein JSON".
        return None


class KumaClient:
    def __init__(
        self, http: HttpLike, *, name: str, base_url: str, insecure_tls: bool = False, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        self._http = http
        self._name = name
        self._base = base_url.rstrip("/")
        self._insecure_tls = insecure_tls
        self._timeout_s = timeout_s

    @property
    def service(self) -> str:
        return f"Uptime Kuma „{self._name}“"

    async def _get(self, path: str, headers: dict[str, str]) -> Reply:
        # Der Schlüssel steht nur im Header, nie in einer Adresse, Meldung oder einem Log.
        return await get(
            self._http, f"{self._base}{path}", service=self.service, insecure_tls=self._insecure_tls,
            headers=headers, timeout_s=self._timeout_s,
        )

    def _http_problem(self, status: int) -> KumaError:
        return KumaError(f"{self.service} meldet einen Fehler (HTTP {status}). Schau in Uptime Kuma nach, ob alles läuft.")

    async def metrics(self, key: str) -> list[MonitorData]:
        reply = await self._get("/metrics", {"Authorization": basic_header(key), "Accept": "text/plain"})
        if reply.status_code in (401, 403):
            raise AuthFailed(
                f"{self.service} hat den API-Schlüssel nicht akzeptiert. Prüfe in Uptime Kuma unter "
                "Einstellungen → API-Schlüssel, ob er noch gültig und eingeschaltet ist, und trage ihn hier neu ein."
            )
        if reply.status_code == 404:
            raise KumaError("Unter dieser Adresse antwortet kein Uptime Kuma oder /metrics ist abgeschaltet.")
        if reply.status_code != 200:
            raise self._http_problem(reply.status_code)
        samples, total = parse_prometheus(reply.body.decode("utf-8", errors="replace"))
        if total == 0:
            # Uptime Kuma schreibt immer auch allgemeine Messwerte; ohne eine einzige Reihe ist das keine Messwert-Seite.
            raise KumaError(
                f"{self.service} liefert unter /metrics keine Messwerte. "
                "Ist unter dieser Adresse wirklich Uptime Kuma erreichbar?"
            )
        return collect_monitors(samples)

    def _missing_page(self, slug: str) -> KumaError:
        return KumaError(
            f"Die Status-Seite „{slug}“ gibt es nicht. Den Kurznamen findest du in Uptime Kuma in der Adresse der "
            "Seite, direkt nach /status/."
        )

    async def _page_json(self, path: str, slug: str) -> Any:
        reply = await self._get(path, {"Accept": "application/json"})
        if reply.status_code == 404:
            raise self._missing_page(slug)
        if reply.status_code in (401, 403):
            raise KumaError(f"Die Status-Seite „{slug}“ von {self.service} verlangt eine Anmeldung und lässt sich so nicht lesen.")
        if reply.status_code != 200:
            raise self._http_problem(reply.status_code)
        body = _json(reply)
        if body is None:
            raise KumaError(f"Die Antwort der Status-Seite „{slug}“ von {self.service} war kein gültiges JSON.")
        return body

    async def status_page(self, slug: str) -> StatusPage:
        quoted = quote(slug, safe="")
        monitors = parse_monitors(await self._page_json(f"/api/status-page/{quoted}", slug))
        if monitors is None:
            raise KumaError(f"Die Antwort der Status-Seite „{slug}“ von {self.service} hatte nicht das erwartete Format.")
        page = parse_heartbeats(await self._page_json(f"/api/status-page/heartbeat/{quoted}", slug), monitors)
        if page is None:
            raise KumaError(f"Der Verlauf der Status-Seite „{slug}“ von {self.service} hatte nicht das erwartete Format.")
        return page
