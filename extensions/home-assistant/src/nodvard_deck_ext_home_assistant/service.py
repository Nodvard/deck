"""Holt den Zustand jeder Home-Assistant-Verbindung und hält ihn kurz im Speicher; `overview()` wirft nie."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .client import HomeAssistantClient
from .config import SETTINGS_HINT, ConnectionConfig, read_connections, token_label
from .models import (
    Connection,
    Counts,
    EntityValue,
    Overview,
    Summary,
    UnavailableItem,
    UpdateItem,
)
from .parse import ParsedStates
from .transport import TOTAL_TIMEOUT_S, HaError

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

logger = logging.getLogger(__name__)

# Seite und Kachel (fragt jede Minute) sollen Home Assistant nicht doppelt belasten.
CACHE_TTL_S = 20.0
# Home Assistant zählt abgelehnte Anmeldungen und sperrt nach einigen die Adresse, bis jemand die Sperre von Hand
# aufhebt: nach `auth_failed` wird lange nicht neu gefragt, auch nicht auf „Aktualisieren“.
AUTH_FAILED_TTL_S = 1800.0
TEST_TIMEOUT_S = 20.0  # für „Verbindung testen“ (der Kern bricht nach 25 Sekunden ab)
CONNECTION_TIMEOUT_S = 50.0  # alle Abfragen einer Verbindung zusammen
# So viele Einträge stehen höchstens in einer Liste der Antwort; die Gesamtzahl steht in `counts`.
MAX_UPDATES = 50
MAX_UNAVAILABLE = 50

# Ein Long-Lived Access Token ist ein JWT.
_TOKEN_CHARS = re.compile(r"[A-Za-z0-9._~+/=-]+")


def _clean(problem: Exception, token: str) -> str:
    """Der Satz einer Meldung, ohne das Token, falls eine Ausnahme es zitiert."""
    text = str(problem)
    return text.replace(token, "…") if token else text


@dataclass
class _Entry:
    fingerprint: Any
    expires: float
    connection: Connection


class HomeAssistantService:
    def __init__(self, ctx: ExtensionContext, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._ctx = ctx
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def clear(self) -> None:
        """Verwirft alle Zwischenergebnisse."""
        self._cache.clear()

    async def read_token(self, name: str) -> str:
        label = token_label(name)
        try:
            handle = await self._ctx.secrets.get_handle(label)
            async with self._ctx.vault_use(handle) as value:
                token = str(value or "").strip()
        except Exception as exc:  # noqa: BLE001 - nie den Text der Ausnahme weitergeben
            logger.warning("Home Assistant %s: Token nicht lesbar (%s)", name, type(exc).__name__)
            raise HaError(
                f"Das Zugangs-Token für {name} ließ sich nicht aus dem Tresor lesen. Bitte unter Einstellungen → "
                "Erweiterungen → Home Assistant neu eintragen."
            ) from None
        if not token:
            raise HaError(f"Das Zugangs-Token für {name} ist leer. Bitte unter Einstellungen → Erweiterungen → Home Assistant neu eintragen.")
        if not _TOKEN_CHARS.fullmatch(token):
            raise HaError(
                f"Das Zugangs-Token für {name} enthält Zeichen, die in einem Token nicht vorkommen (Leerzeichen, "
                "Zeilenumbrüche oder Sonderzeichen). Bitte neu eintragen: kopiere es ganz und ohne etwas davor oder danach."
            )
        return token

    def client(self, cfg: ConnectionConfig, token: str, *, total_timeout_s: float = TOTAL_TIMEOUT_S) -> HomeAssistantClient:
        assert cfg.base_url is not None
        return HomeAssistantClient(
            self._ctx.http, name=cfg.name, base_url=cfg.base_url, token=token, insecure_tls=cfg.insecure_tls, pin=cfg.pin,
            total_timeout_s=total_timeout_s,
        )

    @staticmethod
    def _failed(
        cfg: ConnectionConfig, state: str, message: str, *, version: str | None = None, location_name: str | None = None
    ) -> Connection:
        return Connection(
            name=cfg.name, url=cfg.base_url, state=state, message=message, version=version, location_name=location_name,
            counts=None, updates=[], unavailable=[], entities=[],
        )

    @staticmethod
    def _entities(cfg: ConnectionConfig, parsed: ParsedStates) -> list[EntityValue]:
        out: list[EntityValue] = []
        for entity_id in cfg.entities:
            found = parsed.selected.get(entity_id)
            if found is None:
                out.append(EntityValue(entity_id=entity_id, name=entity_id, status="missing", state=None, unit=None))
            else:
                out.append(EntityValue(entity_id=entity_id, name=found.name, status=found.status, state=found.state, unit=found.unit))
        return out

    async def _load(self, cfg: ConnectionConfig, has_token: bool) -> Connection:
        if cfg.problem or cfg.base_url is None:
            return self._failed(cfg, "error", cfg.problem or f"Die Adresse von {cfg.name} fehlt.")
        if not has_token:
            return self._failed(cfg, "not_configured", f"Für {cfg.name} fehlt das Zugangs-Token. {SETTINGS_HINT}")
        token = ""
        version: str | None = None
        location_name: str | None = None
        try:
            token = await self.read_token(cfg.name)
            client = self.client(cfg, token)
            info = await client.config()
            version, location_name = info.version, info.location_name
            # Nacheinander: bei einem abgelehnten Token zählt Home Assistant jeden Versuch.
            parsed = await client.states(cfg.entities)
        except HaError as exc:
            return self._failed(cfg, exc.state, _clean(exc, token), version=version, location_name=location_name)
        return Connection(
            name=cfg.name,
            url=cfg.base_url,
            state="ok",
            message=None,
            version=version,
            location_name=location_name,
            counts=Counts(
                total=parsed.total, updates=parsed.update_count, unavailable=parsed.unavailable_count, unknown=parsed.unknown_count
            ),
            updates=[
                UpdateItem(entity_id=u.entity_id, name=u.name, installed_version=u.installed_version, latest_version=u.latest_version)
                for u in parsed.updates[:MAX_UPDATES]
            ],
            unavailable=[UnavailableItem(entity_id=u.entity_id, name=u.name) for u in parsed.unavailable[:MAX_UNAVAILABLE]],
            entities=self._entities(cfg, parsed),
        )

    async def _load_bounded(self, cfg: ConnectionConfig, has_token: bool) -> Connection:
        try:
            async with asyncio.timeout(CONNECTION_TIMEOUT_S):
                return await self._load(cfg, has_token)
        except TimeoutError:
            return self._failed(cfg, "unreachable", f"Home Assistant „{cfg.name}“ antwortet nicht (Zeitüberschreitung).")

    async def _connection(self, cfg: ConnectionConfig, *, refresh: bool) -> Connection:
        try:
            has_token = False if cfg.problem else await self._ctx.secrets.exists(token_label(cfg.name))
            lock = self._locks.setdefault(cfg.name, asyncio.Lock())
            async with lock:
                now = self._clock()
                fingerprint = (cfg, has_token)
                entry = self._cache.get(cfg.name)
                fresh = entry is not None and entry.fingerprint == fingerprint and entry.expires > now
                # "Aktualisieren" umgeht den Zwischenspeicher, aber nicht die Pause nach einem abgelehnten Token.
                if entry is not None and fresh and (not refresh or entry.connection.state == "auth_failed"):
                    return entry.connection
                connection = await self._load_bounded(cfg, has_token)
                ttl = AUTH_FAILED_TTL_S if connection.state == "auth_failed" else CACHE_TTL_S
                self._cache[cfg.name] = _Entry(fingerprint, self._clock() + ttl, connection)
                return connection
        except Exception:  # eine Verbindung darf nie die ganze Antwort kippen
            logger.exception("Home Assistant %s: unerwarteter Fehler", cfg.name)
            return self._failed(cfg, "error", f"Bei der Abfrage von {cfg.name} ist ein unerwarteter Fehler aufgetreten.")

    async def _test_one(self, cfg: ConnectionConfig) -> dict[str, Any]:
        """Prüft eine Verbindung mit `GET /api/`; das Ergebnis ist ein Eintrag für `HealthReport.details`."""
        def bad(text: str) -> dict[str, Any]:
            # `verbatim`: der Kern übersetzt den Satz nicht.
            return {"healthy": False, "error": text, "verbatim": True}

        if cfg.problem or cfg.base_url is None:
            return bad(cfg.problem or f"Die Adresse von {cfg.name} fehlt.")
        token = ""
        try:
            if not await self._ctx.secrets.exists(token_label(cfg.name)):
                return bad(f"Für {cfg.name} fehlt das Zugangs-Token. {SETTINGS_HINT}")
            token = await self.read_token(cfg.name)
            await self.client(cfg, token, total_timeout_s=TEST_TIMEOUT_S).ping()
        except HaError as exc:
            return bad(_clean(exc, token))
        except Exception:  # "Verbindung testen" darf nie als 500 enden
            logger.exception("Home Assistant %s: unerwarteter Fehler beim Testen", cfg.name)
            return bad(f"Beim Testen von {cfg.name} ist ein unerwarteter Fehler aufgetreten.")
        return {"healthy": True}

    async def test_connections(self) -> dict[str, dict[str, Any]]:
        """Leert den Zwischenspeicher (auch die Pause nach einem abgelehnten Token) und prüft jede Verbindung gleichzeitig."""
        self.clear()
        configs = await self.configs()
        results = await asyncio.gather(*(self._test_one(c) for c in configs))
        out: dict[str, dict[str, Any]] = {}
        for cfg, result in zip(configs, results, strict=True):
            key, number = cfg.name, 1
            while key in out:  # ein doppelter Kurzname überschreibt den ersten nicht
                number += 1
                key = f"{cfg.name} ({number})"
            out[key] = result
        return out

    async def configs(self) -> list[ConnectionConfig]:
        return read_connections(await self._ctx.settings.get())

    async def overview(self, *, refresh: bool = False) -> Overview:
        configs = await self.configs()
        names = {c.name for c in configs}
        for gone in [n for n in self._cache if n not in names]:
            del self._cache[gone]
        connections = list(await asyncio.gather(*(self._connection(c, refresh=refresh) for c in configs)))
        summary = Summary(connections=len(connections), updates=0, unavailable=0, unknown=0)
        for connection in connections:
            if connection.counts is not None:
                summary.updates += connection.counts.updates
                summary.unavailable += connection.counts.unavailable
                summary.unknown += connection.counts.unknown
        return Overview(connections=connections, summary=summary)
