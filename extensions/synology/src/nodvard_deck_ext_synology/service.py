"""Holt den Zustand der Synology und hält ihn kurz im Speicher; `overview()` wirft nie."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .backup import UNKNOWN_CLOCK_SLACK, build_task
from .client import AUTH_PAUSE_S, SessionBox, SynologyClient, logout
from .config import (
    PASSWORD_LABEL,
    SETTINGS_HINT,
    AlertConfig,
    SynologyConfig,
    read_alerts,
    read_config,
)
from .models import Load, Note, Overview, SystemInfo, Update
from .parse import BackupStatus, Storage, TaskEntry
from .problems import collect
from .transport import SERVICE, TOTAL_TIMEOUT_S, AuthFailed, DsmError, Unavailable

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

logger = logging.getLogger(__name__)

# Seite und Kacheln (fragen jede Minute) sollen DSM nicht mehrfach belasten.
CACHE_TTL_S = 20.0
# Nach einer abgelehnten Anmeldung wird eine Weile nicht neu gefragt, auch nicht auf „Aktualisieren“;
# „Verbindung testen“ und geänderte Einstellungen heben die Pause auf.
AUTH_FAILED_TTL_S = AUTH_PAUSE_S
TEST_TIMEOUT_S = 20.0  # für „Verbindung testen“ (der Kern bricht nach 25 Sekunden ab)
CONNECTION_TIMEOUT_S = 50.0  # alle Abfragen zusammen

NOT_CONFIGURED = f"{SERVICE} ist noch nicht eingerichtet. {SETTINGS_HINT}"
MORE_TASKS = (
    "Hyper Backup hat {total} Aufgaben, Nodvard Deck zeigt und prüft davon die ersten {shown}. Für die übrigen kommen "
    "keine Meldungen; sieh in Hyper Backup selbst nach."
)
NO_HYPER_BACKUP = "Hyper Backup ist auf diesem Gerät nicht installiert, oder DSM bietet die Schnittstelle dafür nicht an."

Tasks = list[tuple[TaskEntry, BackupStatus | None]]


def _clean(problem: Exception, secret: str) -> str:
    """Der Satz einer Meldung, ohne das Passwort, falls eine Ausnahme es zitiert."""
    text = str(problem)
    return text.replace(secret, "…") if secret else text


async def _section[T](call: Awaitable[T]) -> tuple[T | None, DsmError | None]:
    """Ein Teil darf scheitern, ohne die anderen mitzureißen."""
    try:
        return await call, None
    except DsmError as exc:
        return None, exc


def _gap(cfg: SynologyConfig, has_password: bool) -> str | None:
    """Was zu den Zugangsdaten fehlt, als Satz; `None`, wenn Benutzername und Passwort da sind."""
    if cfg.username and has_password:
        return None
    if not cfg.username and not has_password:
        what = "fehlen Benutzername und Passwort"
    elif not cfg.username:
        what = "fehlt der Benutzername"
    else:
        what = "fehlt das Passwort"
    return f"Für {SERVICE} {what}. {SETTINGS_HINT}"


@dataclass
class _Entry:
    fingerprint: Any
    expires: float
    overview: Overview


@dataclass
class _Box:
    fingerprint: Any
    session: SessionBox


def _nas_now(system: SystemInfo | None, wall: Callable[[], datetime]) -> tuple[datetime, timedelta]:
    """Die Uhr der Synology, gegen die Zeiten von Hyper Backup zählen, und der Puffer dazu; ohne sie die Ortszeit hier
    mit einem Puffer für eine andere Zeitzone."""
    if system is not None and system.clock is not None:
        try:
            return datetime.fromisoformat(system.clock), timedelta(0)
        except ValueError:
            pass
    return wall(), UNKNOWN_CLOCK_SLACK


class SynologyService:
    def __init__(
        self, ctx: ExtensionContext, *, clock: Callable[[], float] = time.monotonic, wall: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._ctx = ctx
        self._clock = clock
        self._wall = wall
        self._cache: _Entry | None = None
        self._box: _Box | None = None
        self._lock = asyncio.Lock()

    async def clear(self) -> None:
        """Meldet die Sitzung ab und verwirft Zwischenergebnis, Pause und Sitzung; gibt das Abmelden nach fünf Sekunden auf."""
        box, self._box = self._box, None
        self._cache = None
        if box is None:
            return
        try:
            async with asyncio.timeout(5.0):
                await logout(self._ctx.http, box.session)
        except Exception:  # noqa: BLE001 - Abmelden ist nur Höflichkeit und darf nichts aufhalten
            logger.debug("Synology: Abmelden unvollständig")

    async def shutdown(self) -> None:
        await self.clear()

    async def config(self) -> SynologyConfig:
        return read_config(await self._ctx.settings.get())

    async def alerts(self) -> AlertConfig:
        return read_alerts(await self._ctx.settings.get())

    async def has_password(self) -> bool:
        return await self._ctx.secrets.exists(PASSWORD_LABEL)

    async def read_password(self) -> str:
        try:
            handle = await self._ctx.secrets.get_handle(PASSWORD_LABEL)
            async with self._ctx.vault_use(handle) as value:
                # Leerzeichen können zum Passwort gehören, ein Zeilenumbruch vom Einfügen nicht.
                password = str(value or "").rstrip("\r\n")
        except Exception as exc:  # noqa: BLE001 - nie den Text der Ausnahme weitergeben
            logger.warning("Synology: Passwort nicht lesbar (%s)", type(exc).__name__)
            raise DsmError(
                "Das Passwort ließ sich nicht aus dem Tresor lesen. Bitte unter Einstellungen → Erweiterungen → "
                "Synology DSM neu eintragen."
            ) from None
        if not password:
            raise DsmError("Das Passwort ist leer. Bitte unter Einstellungen → Erweiterungen → Synology DSM neu eintragen.")
        return password

    def _session_box(self, cfg: SynologyConfig, has_password: bool) -> SessionBox:
        fingerprint = (cfg, has_password)
        if self._box is None or self._box.fingerprint != fingerprint:
            self._box = _Box(fingerprint, SessionBox())
        return self._box.session

    async def client(self, cfg: SynologyConfig, *, total_timeout_s: float = TOTAL_TIMEOUT_S) -> tuple[SynologyClient, str]:
        """Der Client samt Passwort; wirft `DsmError`, wenn Adresse, Benutzername oder Passwort nicht reichen."""
        assert cfg.base_url is not None
        password = await self.read_password()
        client = SynologyClient(
            self._ctx.http, base_url=cfg.base_url, username=cfg.username, password=password,
            box=self._session_box(cfg, True), insecure_tls=cfg.insecure_tls, pin=cfg.pin, total_timeout_s=total_timeout_s,
        )
        return client, password

    @staticmethod
    def _failed(cfg: SynologyConfig, state: str, message: str) -> Overview:
        return Overview(
            state=state, message=message, url=cfg.base_url, system=None, load=None, volumes=None, disks=None, update=None,
            system_note=None, load_note=None, storage_note=None, update_note=None,
        )

    async def _load(self, cfg: SynologyConfig, has_password: bool, alerts: AlertConfig) -> Overview:
        if cfg.problem:
            return self._failed(cfg, "error", cfg.problem)
        if cfg.base_url is None:
            return self._failed(cfg, "not_configured", NOT_CONFIGURED)
        gap = _gap(cfg, has_password)
        if gap:
            return self._failed(cfg, "not_configured", gap)
        password = ""
        try:
            client, password = await self.client(cfg)
            # Zuerst allein: Hier findet der Client die Schnittstellen und meldet sich an. Wird die Anmeldung abgelehnt
            # oder ist der Server weg, hilft keine zweite Anfrage, und parallele Teile würden mehrfach falsch anmelden.
            await client.connect()
        except DsmError as exc:
            return self._failed(cfg, exc.state, _clean(exc, password))
        (system, system_error), (load, load_error), (storage, storage_error), (update, update_error), (listed, backup_error) = (
            await asyncio.gather(
                _section(client.system_info()), _section(client.load()), _section(client.storage()), _section(client.update()),
                _section(client.backup_tasks()),
            )
        )
        errors = [system_error, load_error, storage_error, update_error]
        # Ohne Hyper Backup fehlt nur ein Teil; ob DSM selbst antwortet, entscheiden die vier anderen.
        if all(error is not None for error in errors):
            broken = next((e for e in errors if isinstance(e, AuthFailed)), None) or next(
                (e for e in errors if e is not None and e.state == "unreachable"), None
            ) or errors[0]
            assert broken is not None
            return self._failed(cfg, broken.state, _clean(broken, password))
        tasks, total = listed if listed is not None else (None, 0)
        return self._overview(
            cfg, password, alerts, system=system, load=load, storage=storage, update=update, tasks=tasks,
            errors=[*errors, backup_error], tasks_total=total,
        )

    def _overview(
        self, cfg: SynologyConfig, password: str, alerts: AlertConfig, *, system: SystemInfo | None, load: Load | None,
        storage: Storage | None, update: Update | None, tasks: Tasks | None, errors: list[DsmError | None],
        tasks_total: int = 0,
    ) -> Overview:
        def note(error: DsmError | None) -> Note | None:
            return None if error is None else Note(kind=error.kind, message=_clean(error, password))

        system_error, load_error, storage_error, update_error, backup_error = errors
        backup_note = note(backup_error)
        if isinstance(backup_error, Unavailable):
            backup_note = Note(kind="unavailable", message=NO_HYPER_BACKUP)
        elif tasks is not None and tasks_total > len(tasks):
            backup_note = Note(kind="partial", message=MORE_TASKS.format(total=tasks_total, shown=len(tasks)))
        now, slack = _nas_now(system, self._wall)
        disks = None
        if storage is not None:
            disks = [
                d.model_copy(update={"temp_warn": d.temp is not None and d.temp >= alerts.disk_limit(d.kind)}) for d in storage.disks
            ]
        return Overview(
            state="ok", message=None, url=cfg.base_url, system=system, load=load,
            volumes=None if storage is None else storage.volumes, disks=disks, pools=None if storage is None else storage.pools,
            update=update, backups=None if tasks is None else [build_task(entry, status, now, slack) for entry, status in tasks],
            system_note=note(system_error), load_note=note(load_error), storage_note=note(storage_error),
            update_note=note(update_error), backup_note=backup_note,
            problems=collect(system=system, storage=storage, update=update, tasks=tasks, alerts=alerts, now=now, slack=slack),
        )

    async def _load_bounded(self, cfg: SynologyConfig, has_password: bool, alerts: AlertConfig) -> Overview:
        try:
            async with asyncio.timeout(CONNECTION_TIMEOUT_S):
                return await self._load(cfg, has_password, alerts)
        except TimeoutError:
            return self._failed(cfg, "unreachable", f"{SERVICE} antwortet nicht (Zeitüberschreitung).")

    async def overview(self, *, refresh: bool = False) -> Overview:
        settings = await self._ctx.settings.get()
        cfg, alerts = read_config(settings), read_alerts(settings)
        try:
            has_password = False if cfg.problem else await self.has_password()
            async with self._lock:
                fingerprint = (cfg, has_password, alerts)
                entry = self._cache
                fresh = entry is not None and entry.fingerprint == fingerprint and entry.expires > self._clock()
                # „Aktualisieren“ umgeht den Zwischenspeicher, aber nicht die Pause nach einer abgelehnten Anmeldung.
                if entry is not None and fresh and (not refresh or entry.overview.state == "auth_failed"):
                    return entry.overview
                overview = await self._load_bounded(cfg, has_password, alerts)
                ttl = AUTH_FAILED_TTL_S if overview.state == "auth_failed" else CACHE_TTL_S
                self._cache = _Entry(fingerprint, self._clock() + ttl, overview)
                return overview
        except Exception:  # eine Abfrage darf nie die ganze Antwort kippen
            logger.exception("Synology: unerwarteter Fehler")
            return self._failed(cfg, "error", f"Bei der Abfrage von {SERVICE} ist ein unerwarteter Fehler aufgetreten.")

    async def test_connection(self) -> dict[str, Any]:
        """Leert Zwischenspeicher, Pause und Sitzung und prüft Anmeldung und Geräteinfo; das Ergebnis ist ein Eintrag für
        `HealthReport.details`."""

        def bad(text: str) -> dict[str, Any]:
            # `verbatim`: der Kern übersetzt den Satz nicht.
            return {"healthy": False, "error": text, "verbatim": True}

        await self.clear()
        cfg = await self.config()
        if cfg.problem:
            return bad(cfg.problem)
        if cfg.base_url is None:
            return bad(NOT_CONFIGURED)
        password = ""
        try:
            gap = _gap(cfg, await self.has_password())
            if gap:
                return bad(gap)
            client, password = await self.client(cfg, total_timeout_s=TEST_TIMEOUT_S)
            await client.connect()
            try:
                await client.system_info()
            except DsmError as exc:
                # Die Anmeldung hat geklappt; fehlende Rechte oder eine fehlende Schnittstelle sind kein Verbindungsfehler.
                if exc.kind == "error":
                    raise
        except DsmError as exc:
            return bad(_clean(exc, password))
        except Exception:  # "Verbindung testen" darf nie als 500 enden
            logger.exception("Synology: unerwarteter Fehler beim Testen")
            return bad(f"Beim Testen von {SERVICE} ist ein unerwarteter Fehler aufgetreten.")
        return {"healthy": True}
