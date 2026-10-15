"""Holt den Zustand jeder Jellyfin-Verbindung und hält ihn kurz im Speicher; `overview()` wirft nie."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .client import JellyfinClient
from .config import SETTINGS_HINT, ConnectionConfig, key_label, read_connections
from .models import Connection, Overview, Summary
from .transport import TOTAL_TIMEOUT_S, AuthFailed, JellyfinError

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

logger = logging.getLogger(__name__)

# Seite und Kachel (fragt jede Minute) sollen Jellyfin nicht doppelt belasten.
CACHE_TTL_S = 20.0
# Ein abgelehnter Schlüssel wird nicht besser: Nach `auth_failed` wird eine Weile nicht neu gefragt, auch nicht
# auf „Aktualisieren“; „Verbindung testen“ und geänderte Einstellungen heben die Pause auf.
AUTH_FAILED_TTL_S = 300.0
TEST_TIMEOUT_S = 20.0  # für „Verbindung testen“ (der Kern bricht nach 25 Sekunden ab)
CONNECTION_TIMEOUT_S = 50.0  # alle Abfragen einer Verbindung zusammen
MAX_PLAYBACKS = 50  # mehr Wiedergaben stehen nicht in der Liste, die Gesamtzahl steht in `playback_count`

# API-Schlüssel sind 32 Zeichen aus 0-9 und a-f; Jellyfin entschlüsselt den Header-Wert noch einmal,
# darum kommen nur Buchstaben und Zahlen in Frage.
_KEY_CHARS = re.compile(r"[A-Za-z0-9]{1,128}")


def _clean(problem: Exception, key: str) -> str:
    """Der Satz einer Meldung, ohne den Schlüssel, falls eine Ausnahme ihn zitiert."""
    text = str(problem)
    return text.replace(key, "…") if key else text


async def _section[T](call: Awaitable[T]) -> tuple[T | None, JellyfinError | None]:
    """Ein Abschnitt darf scheitern, ohne die anderen mitzureißen."""
    try:
        return await call, None
    except JellyfinError as exc:
        return None, exc


@dataclass
class _Entry:
    fingerprint: Any
    expires: float
    connection: Connection


class JellyfinService:
    def __init__(self, ctx: ExtensionContext, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._ctx = ctx
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def clear(self) -> None:
        """Verwirft alle Zwischenergebnisse."""
        self._cache.clear()

    async def read_key(self, name: str) -> str:
        label = key_label(name)
        try:
            handle = await self._ctx.secrets.get_handle(label)
            async with self._ctx.vault_use(handle) as value:
                key = str(value or "").strip()
        except Exception as exc:  # noqa: BLE001 - nie den Text der Ausnahme weitergeben
            logger.warning("Jellyfin %s: Schlüssel nicht lesbar (%s)", name, type(exc).__name__)
            raise JellyfinError(
                f"Der API-Schlüssel für {name} ließ sich nicht aus dem Tresor lesen. Bitte unter Einstellungen → "
                "Erweiterungen → Jellyfin neu eintragen."
            ) from None
        if not key:
            raise JellyfinError(f"Der API-Schlüssel für {name} ist leer. Bitte unter Einstellungen → Erweiterungen → Jellyfin neu eintragen.")
        if not _KEY_CHARS.fullmatch(key):
            raise JellyfinError(
                f"Der API-Schlüssel für {name} enthält Zeichen, die in einem Schlüssel nicht vorkommen (Leerzeichen, "
                "Zeilenumbrüche oder Sonderzeichen). Bitte neu eintragen: kopiere ihn ganz und ohne etwas davor oder danach."
            )
        return key

    def client(self, cfg: ConnectionConfig, key: str, *, total_timeout_s: float = TOTAL_TIMEOUT_S) -> JellyfinClient:
        assert cfg.base_url is not None
        return JellyfinClient(
            self._ctx.http, name=cfg.name, base_url=cfg.base_url, api_key=key, insecure_tls=cfg.insecure_tls, pin=cfg.pin,
            total_timeout_s=total_timeout_s,
        )

    @staticmethod
    def _failed(
        cfg: ConnectionConfig, state: str, message: str, *, server_name: str | None = None, version: str | None = None
    ) -> Connection:
        return Connection(
            name=cfg.name, url=cfg.base_url, state=state, message=message, server_name=server_name, version=version,
            playback_count=None, playbacks=None, counts=None, latest=None, problems=[],
        )

    async def _load(self, cfg: ConnectionConfig, has_key: bool) -> Connection:
        if cfg.problem or cfg.base_url is None:
            return self._failed(cfg, "error", cfg.problem or f"Die Adresse von {cfg.name} fehlt.")
        if not has_key:
            return self._failed(cfg, "not_configured", f"Für {cfg.name} fehlt der API-Schlüssel. {SETTINGS_HINT}")
        key = ""
        try:
            key = await self.read_key(cfg.name)
            client = self.client(cfg, key)
            # Zuerst allein: Wird der Schlüssel abgelehnt oder ist der Server weg, hilft keine zweite Anfrage.
            info = await client.system_info()
        except JellyfinError as exc:
            return self._failed(cfg, exc.state, _clean(exc, key))
        (sessions, sessions_error), (counts, counts_error), (latest, latest_error) = await asyncio.gather(
            _section(client.sessions()), _section(client.counts()), _section(client.latest())
        )
        labelled = (("Laufende Wiedergaben", sessions_error), ("Bibliotheken", counts_error), ("Neu hinzugefügt", latest_error))
        errors = [error for _, error in labelled if error is not None]
        broken = next((e for e in errors if isinstance(e, AuthFailed)), None) or (errors[0] if len(errors) == len(labelled) else None)
        if broken is not None:
            return self._failed(cfg, broken.state, _clean(broken, key), server_name=info.name, version=info.version)
        return Connection(
            name=cfg.name,
            url=cfg.base_url,
            state="ok",
            message=None,
            server_name=info.name,
            version=info.version,
            playback_count=None if sessions is None else len(sessions),
            playbacks=None if sessions is None else sessions[:MAX_PLAYBACKS],
            counts=counts,
            latest=latest,
            problems=[f"{label}: {_clean(error, key)}" for label, error in labelled if error is not None],
        )

    async def _load_bounded(self, cfg: ConnectionConfig, has_key: bool) -> Connection:
        try:
            async with asyncio.timeout(CONNECTION_TIMEOUT_S):
                return await self._load(cfg, has_key)
        except TimeoutError:
            return self._failed(cfg, "unreachable", f"Jellyfin „{cfg.name}“ antwortet nicht (Zeitüberschreitung).")

    async def _connection(self, cfg: ConnectionConfig, *, refresh: bool) -> Connection:
        try:
            has_key = False if cfg.problem else await self._ctx.secrets.exists(key_label(cfg.name))
            lock = self._locks.setdefault(cfg.name, asyncio.Lock())
            async with lock:
                fingerprint = (cfg, has_key)
                entry = self._cache.get(cfg.name)
                fresh = entry is not None and entry.fingerprint == fingerprint and entry.expires > self._clock()
                # „Aktualisieren“ umgeht den Zwischenspeicher, aber nicht die Pause nach einem abgelehnten Schlüssel.
                if entry is not None and fresh and (not refresh or entry.connection.state == "auth_failed"):
                    return entry.connection
                connection = await self._load_bounded(cfg, has_key)
                ttl = AUTH_FAILED_TTL_S if connection.state == "auth_failed" else CACHE_TTL_S
                self._cache[cfg.name] = _Entry(fingerprint, self._clock() + ttl, connection)
                return connection
        except Exception:  # eine Verbindung darf nie die ganze Antwort kippen
            logger.exception("Jellyfin %s: unerwarteter Fehler", cfg.name)
            return self._failed(cfg, "error", f"Bei der Abfrage von {cfg.name} ist ein unerwarteter Fehler aufgetreten.")

    async def _test_one(self, cfg: ConnectionConfig) -> dict[str, Any]:
        """Prüft eine Verbindung mit `GET /System/Info`; das Ergebnis ist ein Eintrag für `HealthReport.details`."""
        def bad(text: str) -> dict[str, Any]:
            # `verbatim`: der Kern übersetzt den Satz nicht.
            return {"healthy": False, "error": text, "verbatim": True}

        if cfg.problem or cfg.base_url is None:
            return bad(cfg.problem or f"Die Adresse von {cfg.name} fehlt.")
        key = ""
        try:
            if not await self._ctx.secrets.exists(key_label(cfg.name)):
                return bad(f"Für {cfg.name} fehlt der API-Schlüssel. {SETTINGS_HINT}")
            key = await self.read_key(cfg.name)
            await self.client(cfg, key, total_timeout_s=TEST_TIMEOUT_S).system_info()
        except JellyfinError as exc:
            return bad(_clean(exc, key))
        except Exception:  # "Verbindung testen" darf nie als 500 enden
            logger.exception("Jellyfin %s: unerwarteter Fehler beim Testen", cfg.name)
            return bad(f"Beim Testen von {cfg.name} ist ein unerwarteter Fehler aufgetreten.")
        return {"healthy": True}

    async def test_connections(self) -> dict[str, dict[str, Any]]:
        """Leert den Zwischenspeicher samt Pause und prüft jede Verbindung gleichzeitig."""
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
        summary = Summary(connections=len(connections), playing=0, transcoding=0)
        for connection in connections:
            summary.playing += connection.playback_count or 0
            summary.transcoding += sum(1 for p in connection.playbacks or [] if p.transcoding)
        return Overview(connections=connections, summary=summary)
