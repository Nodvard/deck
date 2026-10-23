"""Holt den Zustand jedes Synology-Geräts und hält ihn kurz im Speicher; ein Gerät, das sich nicht abfragen lässt, wird ein
Zustand mit Grund, nie eine Ausnahme."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .backup import UNKNOWN_CLOCK_SLACK, build_task
from .client import AUTH_PAUSE_S, SessionBox, SynologyClient, backup_deadline, logout
from .config import (
    SETTINGS_HINT,
    AlertConfig,
    DeviceConfig,
    password_label,
    read_alerts,
    read_devices,
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
# Nach einer abgelehnten Anmeldung wird eine Weile nicht neu gefragt, auch nicht auf „Aktualisieren“; die Pause gilt
# je Gerät. „Verbindung testen“ hebt alle auf, geänderte Angaben eines Geräts (Adresse, Konto, Zertifikat) seine eigene.
AUTH_FAILED_TTL_S = AUTH_PAUSE_S
# „Verbindung testen“: Der Kern bricht nach 25 Sekunden ab und verliert dann auch die Ergebnisse der gesunden Geräte.
# Darum bekommt jedes Gerät eine Gesamtfrist, und das Abmelden vorher bleibt kurz.
TEST_TIMEOUT_S = 20.0  # je Anfrage
TEST_DEVICE_TIMEOUT_S = 17.0  # alle Anfragen eines Geräts zusammen
TEST_LOGOUT_TIMEOUT_S = 2.0
CONNECTION_TIMEOUT_S = 50.0  # alle Abfragen eines Geräts zusammen
LOGOUT_TIMEOUT_S = 5.0

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


def _gap(cfg: DeviceConfig, has_password: bool) -> str | None:
    """Was zu den Zugangsdaten fehlt, als Satz; `None`, wenn Benutzername und Passwort da sind."""
    if cfg.username and has_password:
        return None
    if not cfg.username and not has_password:
        what = "fehlen Benutzername und Passwort"
    elif not cfg.username:
        what = "fehlt der Benutzername"
    else:
        what = "fehlt das Passwort"
    return f"Für {cfg.name} {what}. {SETTINGS_HINT}"


def _bad(text: str) -> dict[str, Any]:
    # `verbatim`: der Kern übersetzt den Satz nicht.
    return {"healthy": False, "error": text, "verbatim": True}


class UnknownDevice(LookupError):
    """Unter diesem Kurznamen ist kein Gerät eingetragen."""


@dataclass
class _Entry:
    fingerprint: Any
    expires: float
    overview: Overview


@dataclass
class _Box:
    fingerprint: Any
    session: SessionBox


@dataclass
class _Pause:
    identity: Any
    until: float
    overview: Overview


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
        # Alles Folgende gilt je Gerät (Kurzname). Die Pause hängt nicht am Zwischenergebnis: Sie gilt, solange sich die
        # Angaben des Geräts nicht ändern, auch wenn sich gemeinsame Einstellungen oder die Zahl der Geräte ändern.
        self._cache: dict[str, _Entry] = {}
        self._paused: dict[str, _Pause] = {}
        self._boxes: dict[str, _Box] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _log_out(self, boxes: list[_Box], limit_s: float = LOGOUT_TIMEOUT_S) -> None:
        """Meldet die Sitzungen ab; gibt nach `limit_s` Sekunden auf."""
        if not boxes:
            return
        try:
            async with asyncio.timeout(limit_s):
                await asyncio.gather(*(logout(self._ctx.http, box.session) for box in boxes))
        except Exception:  # noqa: BLE001 - Abmelden ist nur Höflichkeit und darf nichts aufhalten
            logger.debug("Synology: Abmelden unvollständig")

    async def clear(self, *, logout_limit_s: float = LOGOUT_TIMEOUT_S) -> None:
        """Meldet alle Sitzungen ab und verwirft Zwischenergebnisse, Pausen und Sitzungen."""
        boxes, self._boxes = list(self._boxes.values()), {}
        self._cache.clear()
        self._paused.clear()
        await self._log_out(boxes, logout_limit_s)

    async def settings_changed(self) -> None:
        """Verwirft Sitzung, Zwischenergebnis und Pause der Geräte, deren Angaben sich geändert haben oder die weg sind."""
        current = {c.name: c for c in await self.configs() if c.named}
        stale = [name for name, box in self._boxes.items() if current.get(name) != box.fingerprint[0]]
        boxes = [self._boxes.pop(name) for name in stale]
        for name in stale:
            self._cache.pop(name, None)
            self._paused.pop(name, None)
        await self._log_out(boxes)

    async def shutdown(self) -> None:
        await self.clear()

    async def configs(self) -> list[DeviceConfig]:
        return read_devices(await self._ctx.settings.get())

    async def alerts(self) -> AlertConfig:
        return read_alerts(await self._ctx.settings.get())

    async def many(self) -> bool:
        """Ob mehr als ein Gerät eingetragen ist; dann nennen Titel und Kacheln den Kurznamen."""
        return len(await self.configs()) > 1

    async def has_password(self, cfg: DeviceConfig) -> bool:
        return await self._ctx.secrets.exists(password_label(cfg.name))

    async def read_password(self, cfg: DeviceConfig) -> str:
        try:
            handle = await self._ctx.secrets.get_handle(password_label(cfg.name))
            async with self._ctx.vault_use(handle) as value:
                # Leerzeichen können zum Passwort gehören, ein Zeilenumbruch vom Einfügen nicht.
                password = str(value or "").rstrip("\r\n")
        except Exception as exc:  # noqa: BLE001 - nie den Text der Ausnahme weitergeben
            logger.warning("Synology %s: Passwort nicht lesbar (%s)", cfg.name, type(exc).__name__)
            raise DsmError(
                f"Das Passwort für {cfg.name} ließ sich nicht aus dem Tresor lesen. Bitte unter Einstellungen → "
                "Erweiterungen → Synology DSM neu eintragen."
            ) from None
        if not password:
            raise DsmError(
                f"Das Passwort für {cfg.name} ist leer. Bitte unter Einstellungen → Erweiterungen → Synology DSM neu eintragen."
            )
        return password

    def _session_box(self, cfg: DeviceConfig, has_password: bool) -> SessionBox:
        fingerprint = (cfg, has_password)
        box = self._boxes.get(cfg.name)
        if box is None or box.fingerprint != fingerprint:
            box = self._boxes[cfg.name] = _Box(fingerprint, SessionBox())
        return box.session

    async def client(self, cfg: DeviceConfig, *, total_timeout_s: float = TOTAL_TIMEOUT_S) -> tuple[SynologyClient, str]:
        """Der Client samt Passwort; wirft `DsmError`, wenn Adresse, Benutzername oder Passwort nicht reichen."""
        assert cfg.base_url is not None
        password = await self.read_password(cfg)
        client = SynologyClient(
            self._ctx.http, base_url=cfg.base_url, username=cfg.username, password=password,
            box=self._session_box(cfg, True), insecure_tls=cfg.insecure_tls, pin=cfg.pin, total_timeout_s=total_timeout_s,
        )
        return client, password

    @staticmethod
    def _failed(cfg: DeviceConfig | None, state: str, message: str, *, config_problem: bool = False) -> Overview:
        return Overview(
            device=cfg.name if cfg is not None else None, state=state, message=message, url=cfg.base_url if cfg is not None else None,
            system=None, load=None, volumes=None, disks=None, update=None,
            system_note=None, load_note=None, storage_note=None, update_note=None, config_problem=config_problem,
        )

    async def _load(self, cfg: DeviceConfig, has_password: bool, alerts: AlertConfig, many: bool) -> Overview:
        if cfg.problem:
            return self._failed(cfg, "error", cfg.problem, config_problem=True)
        if cfg.base_url is None:
            return self._failed(cfg, "not_configured", f"Für {cfg.name} fehlt die Adresse. {SETTINGS_HINT}")
        gap = _gap(cfg, has_password)
        if gap:
            return self._failed(cfg, "not_configured", gap)
        password = ""
        # Die Frist für Hyper Backup läuft schon während der Anmeldung: Sonst reicht die Gesamtfrist nicht für beides.
        backup_by = backup_deadline()
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
                _section(client.backup_tasks(backup_by)),
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
            cfg, password, alerts, many, system=system, load=load, storage=storage, update=update, tasks=tasks,
            errors=[*errors, backup_error], tasks_total=total,
        )

    def _overview(
        self, cfg: DeviceConfig, password: str, alerts: AlertConfig, many: bool, *, system: SystemInfo | None,
        load: Load | None, storage: Storage | None, update: Update | None, tasks: Tasks | None,
        errors: list[DsmError | None], tasks_total: int = 0,
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
            device=cfg.name, state="ok", message=None, url=cfg.base_url, system=system, load=load,
            volumes=None if storage is None else storage.volumes, disks=disks, pools=None if storage is None else storage.pools,
            update=update, backups=None if tasks is None else [build_task(entry, status, now, slack) for entry, status in tasks],
            system_note=note(system_error), load_note=note(load_error), storage_note=note(storage_error),
            update_note=note(update_error), backup_note=backup_note,
            problems=collect(
                system=system, storage=storage, update=update, tasks=tasks, alerts=alerts, now=now, slack=slack,
                label=cfg.name if many else None,
            ),
        )

    async def _load_bounded(self, cfg: DeviceConfig, has_password: bool, alerts: AlertConfig, many: bool) -> Overview:
        try:
            async with asyncio.timeout(CONNECTION_TIMEOUT_S):
                return await self._load(cfg, has_password, alerts, many)
        except TimeoutError:
            return self._failed(cfg, "unreachable", f"{cfg.name} antwortet nicht (Zeitüberschreitung).")

    async def _device(self, cfg: DeviceConfig, alerts: AlertConfig, many: bool, *, refresh: bool) -> Overview:
        if not cfg.named:
            return self._failed(cfg, "error", cfg.problem or "", config_problem=True)
        try:
            has_password = False if cfg.problem else await self.has_password(cfg)
            lock = self._locks.setdefault(cfg.name, asyncio.Lock())
            async with lock:
                now = self._clock()
                identity = (cfg, has_password)
                pause = self._paused.get(cfg.name)
                if pause is not None and (pause.identity != identity or pause.until <= now):
                    del self._paused[cfg.name]
                    pause = None
                # „Aktualisieren“ umgeht den Zwischenspeicher, aber nicht die Pause nach einer abgelehnten Anmeldung.
                if pause is not None:
                    return pause.overview
                fingerprint = (cfg, has_password, alerts, many)
                entry = self._cache.get(cfg.name)
                if entry is not None and entry.fingerprint == fingerprint and entry.expires > now and not refresh:
                    return entry.overview
                overview = await self._load_bounded(cfg, has_password, alerts, many)
                if overview.state == "auth_failed":
                    self._paused[cfg.name] = _Pause(identity, self._clock() + AUTH_FAILED_TTL_S, overview)
                    self._cache.pop(cfg.name, None)
                else:
                    self._cache[cfg.name] = _Entry(fingerprint, self._clock() + CACHE_TTL_S, overview)
                return overview
        except Exception:  # ein Gerät darf nie die ganze Antwort kippen
            logger.exception("Synology %s: unerwarteter Fehler", cfg.name)
            return self._failed(cfg, "error", f"Bei der Abfrage von {cfg.name} ist ein unerwarteter Fehler aufgetreten.")

    def _forget_gone(self, configs: list[DeviceConfig]) -> None:
        names = {c.name for c in configs}
        for table in (self._cache, self._paused, self._boxes, self._locks):
            for gone in [name for name in table if name not in names]:
                del table[gone]

    async def overview(self, device: str | None = None, *, refresh: bool = False) -> Overview:
        """Der Zustand des Geräts mit diesem Kurznamen, ohne Namen des ersten; wirft `UnknownDevice`, wenn es keins gibt."""
        settings = await self._ctx.settings.get()
        configs, alerts = read_devices(settings), read_alerts(settings)
        self._forget_gone(configs)
        if device is None:
            if not configs:
                return self._failed(None, "not_configured", NOT_CONFIGURED)
            cfg = configs[0]
        else:
            found = next((c for c in configs if c.named and c.name == device), None)
            if found is None:
                raise UnknownDevice(device)
            cfg = found
        return await self._device(cfg, alerts, len(configs) > 1, refresh=refresh)

    async def overviews(self, *, refresh: bool = False) -> list[Overview]:
        """Der Zustand aller Geräte in der Reihenfolge der Einstellungen, gleichzeitig abgefragt."""
        settings = await self._ctx.settings.get()
        configs, alerts = read_devices(settings), read_alerts(settings)
        self._forget_gone(configs)
        many = len(configs) > 1
        return list(await asyncio.gather(*(self._device(c, alerts, many, refresh=refresh) for c in configs)))

    async def _test_one(self, cfg: DeviceConfig) -> dict[str, Any]:
        """Prüft Anmeldung und Geräteinfo eines Geräts; das Ergebnis ist ein Eintrag für `HealthReport.details`."""
        try:
            async with asyncio.timeout(TEST_DEVICE_TIMEOUT_S):
                return await self._probe(cfg)
        except TimeoutError:
            return _bad(
                f"{cfg.name} antwortet nicht rechtzeitig. Läuft die Synology gerade hoch oder ist sie sehr ausgelastet, "
                "versuche es in ein paar Minuten noch einmal."
            )

    async def _probe(self, cfg: DeviceConfig) -> dict[str, Any]:
        if cfg.problem or not cfg.named:
            return _bad(cfg.problem or "")
        if cfg.base_url is None:
            return _bad(f"Für {cfg.name} fehlt die Adresse. {SETTINGS_HINT}")
        password = ""
        try:
            gap = _gap(cfg, await self.has_password(cfg))
            if gap:
                return _bad(gap)
            client, password = await self.client(cfg, total_timeout_s=TEST_TIMEOUT_S)
            await client.connect()
            try:
                await client.system_info()
            except DsmError as exc:
                # Die Anmeldung hat geklappt; fehlende Rechte oder eine fehlende Schnittstelle sind kein Verbindungsfehler.
                if exc.kind == "error":
                    raise
        except DsmError as exc:
            return _bad(_clean(exc, password))
        except Exception:  # "Verbindung testen" darf nie als 500 enden
            logger.exception("Synology %s: unerwarteter Fehler beim Testen", cfg.name)
            return _bad(f"Beim Testen von {cfg.name} ist ein unerwarteter Fehler aufgetreten.")
        return {"healthy": True}

    async def test_connections(self) -> dict[str, dict[str, Any]]:
        """Leert Zwischenspeicher, Pausen und Sitzungen und prüft jedes Gerät gleichzeitig; leer, wenn keins eingetragen ist."""
        await self.clear(logout_limit_s=TEST_LOGOUT_TIMEOUT_S)
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
