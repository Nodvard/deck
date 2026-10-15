"""Holt den Zustand jeder qBittorrent-Verbindung und hält ihn kurz im Speicher; `overview()` wirft nie."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .client import AUTH_PAUSE_S, QbittorrentClient, SessionBox, logout
from .config import SETTINGS_HINT, ConnectionConfig, password_label, read_connections
from .models import Connection, Overview, Summary, Torrent, Transfer
from .parse import MainData
from .transport import (
    TOTAL_TIMEOUT_S,
    AuthFailed,
    LoginRequired,
    QbtError,
    ResponseTooLarge,
)

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

logger = logging.getLogger(__name__)

# Seite und Kachel (fragt jede Minute) sollen qBittorrent nicht doppelt belasten.
CACHE_TTL_S = 20.0
# Nach einer abgelehnten Anmeldung wird eine Weile nicht neu gefragt, auch nicht auf „Aktualisieren“;
# „Verbindung testen“ und geänderte Einstellungen heben die Pause auf.
AUTH_FAILED_TTL_S = AUTH_PAUSE_S
# Die Liste aller Torrents ist die schwerste Abfrage; war sie zu groß, wird sie so lange nicht erneut geholt.
TOO_LARGE_PAUSE_S = 600.0
TEST_TIMEOUT_S = 20.0  # für „Verbindung testen“ (der Kern bricht nach 25 Sekunden ab)
CONNECTION_TIMEOUT_S = 50.0  # alle Abfragen einer Verbindung zusammen

TOO_LARGE_NOTE = (
    "Anzahl je Zustand und freier Platz sind unbekannt: Die Liste aller Torrents ist größer als 8 MB und wird "
    "nicht gelesen."
)


def _clean(problem: Exception, secret: str) -> str:
    """Der Satz einer Meldung, ohne das Passwort, falls eine Ausnahme es zitiert."""
    text = str(problem)
    return text.replace(secret, "…") if secret else text


async def _section[T](call: Awaitable[T]) -> tuple[T | None, QbtError | None]:
    """Ein Abschnitt darf scheitern, ohne die anderen mitzureißen."""
    try:
        return await call, None
    except QbtError as exc:
        return None, exc


async def _skip() -> None:
    return None


@dataclass
class _Entry:
    fingerprint: Any
    expires: float
    connection: Connection


@dataclass
class _Box:
    fingerprint: Any
    session: SessionBox


class QbittorrentService:
    def __init__(self, ctx: ExtensionContext, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._ctx = ctx
        self._clock = clock
        self._cache: dict[str, _Entry] = {}
        self._boxes: dict[str, _Box] = {}
        self._too_large: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def clear(self) -> None:
        """Verwirft alle Zwischenergebnisse, Pausen und Sitzungen."""
        self._cache.clear()
        self._boxes.clear()
        self._too_large.clear()

    async def shutdown(self) -> None:
        """Meldet alle Sitzungen ab; gibt nach fünf Sekunden auf."""
        boxes = [b.session for b in self._boxes.values()]
        self._boxes.clear()
        try:
            async with asyncio.timeout(5.0):
                await asyncio.gather(*(logout(self._ctx.http, box) for box in boxes), return_exceptions=True)
        except Exception:  # noqa: BLE001 - Abmelden ist nur Höflichkeit und darf das Beenden nie aufhalten
            logger.debug("qBittorrent: Abmelden beim Beenden unvollständig")

    async def read_password(self, name: str) -> str:
        label = password_label(name)
        try:
            handle = await self._ctx.secrets.get_handle(label)
            async with self._ctx.vault_use(handle) as value:
                # Leerzeichen können zum Passwort gehören, ein Zeilenumbruch vom Einfügen nicht.
                password = str(value or "").rstrip("\r\n")
        except Exception as exc:  # noqa: BLE001 - nie den Text der Ausnahme weitergeben
            logger.warning("qBittorrent %s: Passwort nicht lesbar (%s)", name, type(exc).__name__)
            raise QbtError(
                f"Das Passwort für {name} ließ sich nicht aus dem Tresor lesen. Bitte unter Einstellungen → "
                "Erweiterungen → qBittorrent neu eintragen."
            ) from None
        if not password:
            raise QbtError(f"Das Passwort für {name} ist leer. Bitte unter Einstellungen → Erweiterungen → qBittorrent neu eintragen.")
        return password

    def _session_box(self, cfg: ConnectionConfig, has_password: bool) -> SessionBox:
        fingerprint = (cfg, has_password)
        box = self._boxes.get(cfg.name)
        if box is None or box.fingerprint != fingerprint:
            box = self._boxes[cfg.name] = _Box(fingerprint, SessionBox())
        return box.session

    async def client(
        self, cfg: ConnectionConfig, has_password: bool, *, total_timeout_s: float = TOTAL_TIMEOUT_S
    ) -> tuple[QbittorrentClient, str]:
        """Der Client samt Passwort (leer, wenn es keines gibt); wirft `QbtError`, wenn die Zugangsdaten nicht reichen."""
        assert cfg.base_url is not None
        password = ""
        if has_password:
            if not cfg.username:
                raise LoginRequired(f"Für {cfg.name} fehlt der Benutzername. {SETTINGS_HINT}")
            password = await self.read_password(cfg.name)
        client = QbittorrentClient(
            self._ctx.http, name=cfg.name, base_url=cfg.base_url, username=cfg.username, password=password or None,
            box=self._session_box(cfg, has_password), insecure_tls=cfg.insecure_tls, pin=cfg.pin, total_timeout_s=total_timeout_s,
        )
        return client, password

    @staticmethod
    def _failed(cfg: ConnectionConfig, state: str, message: str, *, version: str | None = None) -> Connection:
        return Connection(
            name=cfg.name, url=cfg.base_url, state=state, message=message, version=version, api_version=None, transfer=None,
            active=None, active_more=False, counts=None, free_space=None, notes=[], problems=[],
        )

    async def _load(self, cfg: ConnectionConfig, has_password: bool) -> Connection:
        if cfg.problem or cfg.base_url is None:
            return self._failed(cfg, "error", cfg.problem or f"Die Adresse von {cfg.name} fehlt.")
        password = ""
        try:
            client, password = await self.client(cfg, has_password)
            # Zuerst allein: Hier meldet sich der Client an. Wird die Anmeldung abgelehnt oder ist der Server weg,
            # hilft keine zweite Anfrage, und parallele Abschnitte würden mehrfach falsch anmelden.
            version = await client.version()
        except QbtError as exc:
            return self._failed(cfg, exc.state, _clean(exc, password))
        skip_totals = self._too_large.get(cfg.name, 0.0) > self._clock()
        (api_version, _), (transfer, transfer_error), (active, active_error), (main, main_error) = await asyncio.gather(
            _section(client.api_version()), _section(client.transfer()), _section(client.active()),
            _section(_skip() if skip_totals else client.maindata()),
        )
        notes: list[str] = []
        too_large = isinstance(main_error, ResponseTooLarge)
        if too_large:
            self._too_large[cfg.name] = self._clock() + TOO_LARGE_PAUSE_S
        if too_large or skip_totals:
            notes.append(TOO_LARGE_NOTE)
            main_error = None
        labelled = (("Geschwindigkeit", transfer_error), ("Aktive Torrents", active_error), ("Anzahl und freier Platz", main_error))
        asked = 2 if (too_large or skip_totals) else 3
        errors = [error for _, error in labelled if error is not None]
        broken = next((e for e in errors if isinstance(e, AuthFailed)), None) or (errors[0] if len(errors) == asked else None)
        if broken is not None:
            return self._failed(cfg, broken.state, _clean(broken, password), version=version)
        return self._connection(cfg, version, api_version, transfer, active, main, notes, [
            f"{label}: {_clean(error, password)}" for label, error in labelled if error is not None
        ])

    @staticmethod
    def _connection(
        cfg: ConnectionConfig, version: str, api_version: str | None, transfer: Transfer | None,
        active: tuple[list[Torrent], bool] | None, main: MainData | None, notes: list[str], problems: list[str],
    ) -> Connection:
        if transfer is not None and main is not None and main.alt_limits is not None:
            transfer = transfer.model_copy(update={"alt_limits": main.alt_limits})
        return Connection(
            name=cfg.name,
            url=cfg.base_url,
            state="ok",
            message=None,
            version=version,
            api_version=api_version,
            transfer=transfer,
            active=None if active is None else active[0],
            active_more=False if active is None else active[1],
            counts=None if main is None else main.counts,
            free_space=None if main is None else main.free_space,
            notes=notes,
            problems=problems,
        )

    async def _load_bounded(self, cfg: ConnectionConfig, has_password: bool) -> Connection:
        try:
            async with asyncio.timeout(CONNECTION_TIMEOUT_S):
                return await self._load(cfg, has_password)
        except TimeoutError:
            return self._failed(cfg, "unreachable", f"qBittorrent „{cfg.name}“ antwortet nicht (Zeitüberschreitung).")

    async def _connection_for(self, cfg: ConnectionConfig, *, refresh: bool) -> Connection:
        try:
            has_password = False if cfg.problem else await self._ctx.secrets.exists(password_label(cfg.name))
            lock = self._locks.setdefault(cfg.name, asyncio.Lock())
            async with lock:
                fingerprint = (cfg, has_password)
                entry = self._cache.get(cfg.name)
                fresh = entry is not None and entry.fingerprint == fingerprint and entry.expires > self._clock()
                # „Aktualisieren“ umgeht den Zwischenspeicher, aber nicht die Pause nach einer abgelehnten Anmeldung.
                if entry is not None and fresh and (not refresh or entry.connection.state == "auth_failed"):
                    return entry.connection
                connection = await self._load_bounded(cfg, has_password)
                ttl = AUTH_FAILED_TTL_S if connection.state == "auth_failed" else CACHE_TTL_S
                self._cache[cfg.name] = _Entry(fingerprint, self._clock() + ttl, connection)
                return connection
        except Exception:  # eine Verbindung darf nie die ganze Antwort kippen
            logger.exception("qBittorrent %s: unerwarteter Fehler", cfg.name)
            return self._failed(cfg, "error", f"Bei der Abfrage von {cfg.name} ist ein unerwarteter Fehler aufgetreten.")

    async def _test_one(self, cfg: ConnectionConfig) -> dict[str, Any]:
        """Prüft eine Verbindung mit Anmeldung und `GET /api/v2/app/version`; das Ergebnis ist ein Eintrag für `HealthReport.details`."""
        def bad(text: str) -> dict[str, Any]:
            # `verbatim`: der Kern übersetzt den Satz nicht.
            return {"healthy": False, "error": text, "verbatim": True}

        if cfg.problem or cfg.base_url is None:
            return bad(cfg.problem or f"Die Adresse von {cfg.name} fehlt.")
        password = ""
        try:
            has_password = await self._ctx.secrets.exists(password_label(cfg.name))
            client, password = await self.client(cfg, has_password, total_timeout_s=TEST_TIMEOUT_S)
            await client.version()
        except QbtError as exc:
            return bad(_clean(exc, password))
        except Exception:  # "Verbindung testen" darf nie als 500 enden
            logger.exception("qBittorrent %s: unerwarteter Fehler beim Testen", cfg.name)
            return bad(f"Beim Testen von {cfg.name} ist ein unerwarteter Fehler aufgetreten.")
        return {"healthy": True}

    async def test_connections(self) -> dict[str, dict[str, Any]]:
        """Leert Zwischenspeicher, Pausen und Sitzungen und prüft jede Verbindung gleichzeitig."""
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
        for gone in [n for n in self._boxes if n not in names]:
            del self._boxes[gone]
        connections = list(await asyncio.gather(*(self._connection_for(c, refresh=refresh) for c in configs)))
        summary = Summary(connections=len(connections), download_speed=0, upload_speed=0, active=0)
        for connection in connections:
            if connection.transfer is not None:
                summary.download_speed += connection.transfer.download_speed
                summary.upload_speed += connection.transfer.upload_speed
            summary.active += len(connection.active or [])
        return Overview(connections=connections, summary=summary)
