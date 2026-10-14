"""Registry-Sync sowie Laden und Entladen von Extensions, verdrahtet mit der Datenbank und der App.

Jede Phase fängt ihre eigenen Fehler und setzt `state=error`; `enable_extension()` wirft nie in den Start (docs/02 §1).
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import sys
from collections.abc import Iterable
from pathlib import Path

from fastapi import APIRouter, FastAPI
from nodvard_sdk import ExtensionManifest, is_compatible
from nodvard_sdk.version import API_VERSION as SDK_API_VERSION
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import BaseRoute

from nodvard_sdk.types import Event

from ..config import Settings
from ..core.events import get_event_bus
from ..db import utcnow
from ..ext.context import build_context
from ..ext.discovery import DiscoveredExtension, discover_all, resolve_legacy
from ..ext.runtime import ExtensionRuntime, LoadedExtension, get_extension_runtime
from ..models import ExtensionRecord, Setting
from . import audit as audit_service
from . import settings as settings_service


log = logging.getLogger(__name__)

# Globale Einstellung `extension.untouched.<Speicher-Kennung>`: neu entdeckt und seither von niemandem ein- oder
# ausgeschaltet. Nur dann darf der Kern die Erweiterung bei einem Anlass aus `enable_on` selbst einschalten; ältere
# Erweiterungen haben den Eintrag nie und bleiben, wie jemand sie hinterlassen hat.
UNTOUCHED_KEY_PREFIX = "extension.untouched."


class ExtensionLoadError(Exception):
    pass


async def get_record(session: AsyncSession, ext_id: str) -> ExtensionRecord | None:
    """Die Registry-Zeile zu einer Kennung (heutige oder alte), gesucht über `ExtensionRuntime.store_id`.

    Ein verwaister Zwilling kommt so nie zurück. Einzelzugriffe laufen hierüber, ein Test verbietet sie sonst."""
    return await session.get(ExtensionRecord, get_extension_runtime().store_id(ext_id))


async def get_records(session: AsyncSession, ext_ids: Iterable[str]) -> dict[str, ExtensionRecord]:
    """Die Registry-Zeilen zu mehreren Kennungen mit einer Abfrage, wie `get_record` je Kennung.

    Der Schlüssel ist die Kennung, wie sie übergeben wurde; Kennungen ohne Zeile fehlen im Ergebnis."""
    runtime = get_extension_runtime()
    row_ids = {ext_id: runtime.store_id(ext_id) for ext_id in ext_ids}
    if not row_ids:
        return {}
    rows = (await session.execute(select(ExtensionRecord).where(ExtensionRecord.id.in_(set(row_ids.values()))))).scalars().all()
    by_row = {row.id: row for row in rows}
    return {ext_id: by_row[row_id] for ext_id, row_id in row_ids.items() if row_id in by_row}


async def is_stale_twin_address(session: AsyncSession, ext_id: str) -> bool:
    """`ext_id` ist eine alte Kennung mit eigener, ungenutzter Registry-Zeile (verwaister Zwilling).

    Die Adresse ist dann mehrdeutig (Rückweg aufs alte Image), die API darf darüber nichts ändern."""
    if not get_extension_runtime().is_stale_twin(ext_id):
        return False
    return await session.get(ExtensionRecord, ext_id) is not None


async def _clear_untouched(session: AsyncSession, ext_id: str) -> None:
    row = await session.get(Setting, (UNTOUCHED_KEY_PREFIX + ext_id, "global", ""))
    if row is not None:
        await session.delete(row)
        await session.flush()


async def record_user_choice(session: AsyncSession, ext_id: str) -> None:
    """Jemand hat die Erweiterung bewusst ein- oder ausgeschaltet; der Kern schaltet sie nie mehr selbst ein."""
    await _clear_untouched(session, get_extension_runtime().store_id(ext_id))


def public_route_prefixes(ext_id: str) -> list[str]:
    """Adressen (`/api/v1/ext/<id>/...`), die die geladene Erweiterung ohne Anmeldung anbietet, samt alter Kennungen."""
    runtime = get_extension_runtime()
    ext_id = runtime.canonical(ext_id)
    loaded = runtime.loaded.get(ext_id)
    api = getattr(getattr(loaded, "ctx", None), "api", None)
    prefixes = list(getattr(api, "public_prefixes", None) or [])
    own = f"/api/v1/ext/{ext_id}"
    return prefixes + [
        f"/api/v1/ext/{old}{p[len(own):]}"
        for old in runtime.legacy_ids_of(ext_id)
        for p in prefixes
        if p == own or p.startswith(own + "/")
    ]


async def auto_enable_for(
    app: FastAPI, session: AsyncSession, settings: Settings, trigger: str, *, user_id: str | None = None
) -> list[str]:
    """Schaltet ausgeschaltete, unberührte Erweiterungen mit `trigger` in `enable_on` ein; gibt die laufenden zurück.

    Jeder Fehler bleibt hier, der Aufrufer (etwa das Anlegen eines Zugangs) scheitert daran nie."""
    enabled: list[str] = []
    runtime = get_extension_runtime()
    try:
        records = (await session.execute(select(ExtensionRecord).where(ExtensionRecord.state == "disabled"))).scalars().all()
        for record in list(records):
            if runtime.is_stale_twin(record.id):
                continue  # gehört zu einer umbenannten Erweiterung, die eine andere Zeile nutzt
            if trigger not in ((record.manifest or {}).get("enable_on") or []):
                continue
            if await session.get(Setting, (UNTOUCHED_KEY_PREFIX + record.id, "global", "")) is None:
                continue
            ext_id = runtime.canonical(record.id)
            found = runtime.discovered.get(ext_id)
            if found is None or not found.ok:
                # Fehlt gerade (etwa nach Rückweg aufs alte Image): nichts anfassen, der Anlass gilt später wieder.
                continue
            # Gilt auch bei Fehlschlag als entschieden: ein zweiter Versuch beim nächsten Zugang wäre nur Lärm.
            await _clear_untouched(session, record.id)
            try:
                await enable_extension(app, session, settings, ext_id)
            except Exception:  # noqa: BLE001 - der Aufrufer darf daran nie scheitern
                log.exception("Automatisches Einschalten von '%s' fehlgeschlagen", ext_id)
                record.state = "error"
                record.last_error = "Automatisches Einschalten fehlgeschlagen."
                record.last_error_at = utcnow()
            ok = record.state == "enabled"
            if ok:
                enabled.append(ext_id)
            public_routes = public_route_prefixes(ext_id)
            await audit_service.log(
                session, actor_type="user" if user_id else "system", actor_id=user_id or "system",
                action="extension.auto_enabled", outcome="success" if ok else "failure",
                target_type="extension", target_id=ext_id,
                detail={
                    "trigger": trigger,
                    **({} if ok else {"error": (record.last_error or "")[:300]}),
                    **({"public_routes": public_routes} if public_routes else {}),
                },
            )
    except Exception:  # noqa: BLE001
        log.exception("Automatisches Einschalten (%s) fehlgeschlagen", trigger)
    return enabled


def _stored_manifest(manifest: ExtensionManifest) -> dict:
    """Das Manifest, wie es in der Registry-Zeile steht; eine leere `legacy_ids`-Liste bleibt weg."""
    data = manifest.model_dump(mode="json")
    if not data.get("legacy_ids"):
        data.pop("legacy_ids", None)
    return data


async def _find_record(session: AsyncSession, d: DiscoveredExtension) -> ExtensionRecord | None:
    """Die Zeile mit der Kennung des Funds, sonst die erste einer alten Kennung in der Reihenfolge von `legacy_ids`.

    Jede weitere Zeile einer alten Kennung ist ein verwaister Zwilling und bleibt liegen."""
    assert d.manifest is not None  # nur für gültige Funde
    chosen: ExtensionRecord | None = None
    for candidate in (d.id, *d.manifest.legacy_ids):
        record = await session.get(ExtensionRecord, candidate)
        if record is None:
            continue
        if chosen is None:
            chosen = record
            if candidate != d.id:
                log.info("Erweiterung '%s' nutzt den gespeicherten Stand von '%s'.", d.id, candidate)
        else:
            log.info(
                "Erweiterung '%s' nutzt die Zeile '%s'; die Zeile der alten Kennung '%s' bleibt unverändert "
                "liegen und wird nicht geladen.", d.id, chosen.id, candidate,
            )
    return chosen


async def discover_and_sync(session: AsyncSession, settings: Settings) -> dict[str, DiscoveredExtension]:
    """Scannt, legt Zeilen für neue Funde an (`state=disabled`, aktiviert wird bewusst) und merkt sich das Ergebnis.

    Bei Umbenennungen gilt die Zeile der alten Kennung weiter; nichts wird kopiert, ein Rückweg findet sie wieder."""
    resolution = resolve_legacy(discover_all(settings.extensions_dir))
    for refused in resolution.refused:
        log.warning("Erweiterung '%s' wird nicht geladen: %s", refused.id, refused.error)
    by_id: dict[str, DiscoveredExtension] = {}
    store_ids: dict[str, str] = {}
    for d in resolution.found:
        if not d.ok:
            continue
        assert d.manifest is not None  # `d.ok` garantiert das
        by_id[d.id] = d
        record = await _find_record(session, d)
        if record is None:
            session.add(
                ExtensionRecord(
                    id=d.manifest.id,
                    version=d.manifest.version,
                    api_version=d.manifest.api_version,
                    state="disabled",
                    source=d.source,
                    manifest=_stored_manifest(d.manifest),
                )
            )
            # Neu entdeckt und noch nie angefasst (siehe UNTOUCHED_KEY_PREFIX).
            await settings_service.set_global(session, UNTOUCHED_KEY_PREFIX + d.id, True)
            store_ids[d.id] = d.id
        else:
            record.version = d.manifest.version
            record.api_version = d.manifest.api_version
            record.source = d.source
            record.manifest = _stored_manifest(d.manifest)
            store_ids[d.id] = record.id
    await session.flush()

    runtime = get_extension_runtime()
    runtime.discovered = by_id
    runtime.legacy_owner = resolution.owner
    runtime.store_ids = store_ids
    runtime.refused = {d.id: d.error or "" for d in resolution.refused}
    runtime.refused_old = resolution.refused_old
    return by_id


async def load_enabled_from_registry(app: FastAPI, session: AsyncSession, settings: Settings) -> None:
    """Lädt beim Start alles, was noch `state=enabled` ist; ein Neustart vergisst keine Aktivierung.

    Fehlt eine Erweiterung (zurückgerolltes Image), bleibt ihr Zustand stehen; `last_error` nennt den Grund."""
    runtime = get_extension_runtime()
    result = await session.execute(select(ExtensionRecord).where(ExtensionRecord.state == "enabled"))
    records = result.scalars().all()
    for record in records:
        if runtime.is_stale_twin(record.id):
            continue  # Zeile einer alten Kennung, die Erweiterung nutzt eine andere
        await enable_extension(app, session, settings, record.id, at_boot=True)
    _announce_loaded(runtime, records)


# Beginn der Zeile im Container-Protokoll, die `deploy/pi_switch.sh` sucht (die letzte zählt); ein Test prüft beide.
LOADED_LINE_PREFIX = "Erweiterungen geladen: "
# Steht dahinter, wenn nichts läuft; Klammern kommen in Kennungen nie vor.
LOADED_LINE_NONE = "(keine)"


def _announce_loaded(runtime: ExtensionRuntime, records: list[ExtensionRecord]) -> None:
    """Schreibt `Erweiterungen geladen: <Speicher-Kennungen, sortiert, kommagetrennt>` ins Container-Protokoll.

    Gemeint ist, was wirklich läuft: eingeschaltet, geladen und `on_start()` ohne Fehler."""
    # `deploy/pi_switch.sh` vergleicht damit alten und neuen Container und schaltet zurück, wenn eine Kennung fehlt;
    # deshalb schreibt auch jedes Ein- und Ausschalten sie. Die Speicher-Kennung bleibt bei Umbenennung gleich,
    # Kennungen enthalten nie ein Komma. `print` statt `logging`: Der Dienst richtet kein Logging ein, eine
    # `log.info`-Zeile erreichte das Protokoll nie.
    ids = sorted(
        {
            ext.store_id or ext.manifest.id
            for record in records
            if record.state == "enabled"
            and not runtime.is_stale_twin(record.id)
            and (ext := runtime.loaded.get(runtime.canonical(record.id))) is not None
        }
    )
    with contextlib.suppress(OSError, ValueError):
        print(LOADED_LINE_PREFIX + (",".join(ids) or LOADED_LINE_NONE), flush=True)


async def _announce_current(session: AsyncSession) -> None:
    """`_announce_loaded` nach einem Ein- oder Ausschalten im laufenden Betrieb, mit dem Zustand im Speicher."""
    # Ohne Autoflush: Ein Schreibvorgang hielte die Datenbanksperre, während der Aufrufer womöglich noch Code einer
    # Erweiterung ausführt (siehe `enable_extension`).
    with session.no_autoflush:
        records = (await session.execute(select(ExtensionRecord))).scalars().all()
    _announce_loaded(get_extension_runtime(), list(records))


async def enable_extension(
    app: FastAPI, session: AsyncSession, settings: Settings, ext_id: str, *, at_boot: bool = False
) -> None:
    """Lädt die Erweiterung (`ext_id` darf eine alte Kennung sein) und setzt `state` auf das Ergebnis.

    `at_boot=True` nur beim Start: Eine fehlende Erweiterung behält ihren Zustand, sonst `ExtensionLoadError`."""
    await _enable_extension(app, session, settings, ext_id, at_boot=at_boot)
    if not at_boot:
        await _announce_current(session)


async def _enable_extension(
    app: FastAPI, session: AsyncSession, settings: Settings, ext_id: str, *, at_boot: bool
) -> None:
    """Der eigentliche Ablauf von `enable_extension`, ohne die Zeile im Protokoll."""
    runtime = get_extension_runtime()
    ext_id = runtime.canonical(ext_id)
    if ext_id in runtime.loaded:
        existing = await get_record(session, ext_id)
        if existing is None or existing.state != "error":
            return  # idempotent
        # on_start() ist beim letzten Mal abgestürzt, die Extension blieb halb geladen: sauber entladen und neu starten.
        await _unload_extension(app, session, ext_id)

    record = await get_record(session, ext_id)
    if record is None:
        raise ExtensionLoadError(f"Extension '{ext_id}' ist nicht in der Registry.")

    discovered = runtime.discovered.get(ext_id)
    if discovered is None or not discovered.ok:
        # Wegen einer alten Kennung abgelehnt: den eigentlichen Grund nennen, auch für die Zeile der alten Kennung.
        refused = runtime.refused.get(ext_id) or runtime.refused_old.get(ext_id)
        if not at_boot:
            # Ausdrücklich eingeschaltet, aber nicht da: nichts schreiben, auch `state` bleibt.
            if refused:
                raise ExtensionLoadError(f"Extension '{ext_id}' wird nicht geladen: {refused}")
            raise ExtensionLoadError(
                f"Extension '{ext_id}' wurde nicht gefunden (Ordner oder Paket fehlt, oder das Manifest ist ungültig)."
            )
        # Beim Start fehlt sie nur: `state` bleibt, damit ein Rückweg aufs alte Image die Entscheidung "eingeschaltet"
        # nicht löscht; die API meldet solange "error" (siehe `ExtensionOut.from_model`).
        if refused:
            # Die Warnung dazu steht schon im Protokoll (`discover_and_sync`).
            record.last_error = (
                f"Die Erweiterung wird nicht geladen: {refused} "
                "Sie bleibt eingeschaltet und wird beim nächsten Start wieder geladen, sobald das behoben ist."
            )
        else:
            log.warning(
                "Erweiterung '%s' ist eingeschaltet, wurde beim Start aber nicht gefunden: sie bleibt "
                "eingeschaltet und wird nicht geladen.", ext_id,
            )
            record.last_error = (
                "Die Erweiterung wurde nicht gefunden (Ordner oder Paket fehlt, oder das Manifest ist ungültig). "
                "Sie bleibt eingeschaltet und wird beim nächsten Start wieder geladen, sobald sie da ist."
            )
        record.last_error_at = utcnow()
        return

    manifest = discovered.manifest
    assert manifest is not None  # `discovered.ok` garantiert das

    if not is_compatible(manifest.api_version):
        record.state = "incompatible"
        record.last_error = (
            f"api_version {manifest.api_version!r} inkompatibel mit Kern-SDK {SDK_API_VERSION!r}."
        )
        record.last_error_at = utcnow()
        return

    # Aktivieren gewährt das ganze im Manifest deklarierte Set; eine Auswahl gibt es noch nicht.
    granted = list(manifest.permissions)
    record.granted_permissions = granted

    if discovered.source == "bundled":
        src_dir = discovered.source_path / "src"
        if src_dir.is_dir() and str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))

    module_name, _, class_name = manifest.entrypoint.partition(":")
    try:
        module = importlib.import_module(module_name)
        extension_cls = getattr(module, class_name)
        instance = extension_cls()
    except Exception as exc:  # noqa: BLE001 - Fehler-Isolation
        record.state = "error"
        record.last_error = f"Import von '{manifest.entrypoint}' fehlgeschlagen: {exc}"
        record.last_error_at = utcnow()
        return

    loaded = LoadedExtension(
        manifest=manifest, instance=instance, ctx=None, granted_permissions=granted, store_id=record.id
    )
    ctx = build_context(runtime, loaded, manifest, granted, _data_dir(settings, manifest, record.id), settings)
    loaded.ctx = ctx

    # Vor jedem Übergang in Extension-Code committen: setup() und on_start() öffnen über ihre Handles eigene
    # `session_scope()`-Sitzungen, und neben einer offenen Transaktion hier sperrt SQLite ("database is locked").
    await session.commit()
    try:
        await instance.setup(ctx)
        # Auch Routen, die setup() erst nach include_router() angehängt hat.
        ctx.api.check_routes()
    except Exception as exc:  # noqa: BLE001
        record.state = "error"
        record.last_error = f"setup() fehlgeschlagen: {exc}"
        record.last_error_at = utcnow()
        # Was setup() schon angemeldet hat (Abos, Aufgaben, Seiten ...), kennt `runtime.loaded` nicht, und
        # `_unload_extension` fasst es nie an; ohne Aufräumen liefe nach dem erneuten Einschalten alles doppelt.
        try:
            await _cleanup_loaded(app, loaded)
        except Exception:  # der setup()-Fehler bleibt die eigentliche Meldung
            logging.getLogger(__name__).exception("Aufräumen nach fehlgeschlagenem setup() von '%s'", manifest.id)
        return

    if loaded.router is not None:
        # Unter der heutigen Kennung und jeder alten; `mounted_routes` lässt das Ausschalten alle entfernen.
        loaded.mounted_routes = _mount_routes(app, runtime, manifest.id, loaded.router)

    await session.commit()
    start_error: Exception | None = None
    try:
        await instance.on_start(ctx)
    except Exception as exc:  # noqa: BLE001
        start_error = exc

    # Der Router ist seit setup() live: Routen, die on_start() (auch kurz vor einem Absturz) anhängt, bekommen
    # keine Anmeldeprüfung. Die Erweiterung wird dann wie bei einem setup()-Fehler zurückgebaut.
    try:
        ctx.api.check_routes()
    except Exception as exc:  # noqa: BLE001
        record.state = "error"
        record.last_error = f"on_start() hat Routen angehängt, die keine Anmeldung prüfen: {exc}"
        record.last_error_at = utcnow()
        try:
            await instance.on_stop(ctx)
        except Exception:  # Aufräumen darf nicht an einer kaputten Extension scheitern
            log.exception("on_stop() nach abgelehnten Routen von '%s' fehlgeschlagen", manifest.id)
        try:
            await _cleanup_loaded(app, loaded)
        except Exception:  # der eigentliche Fehler bleibt die Meldung
            logging.getLogger(__name__).exception("Aufräumen nach abgelehnten Routen von '%s'", manifest.id)
        return

    if start_error is not None:
        # setup() lief durch, Seiten, Widgets und Router stehen bewusst weiter (halb registriert hilft mehr als
        # unsichtbar); die Extension bleibt in `runtime.loaded`, damit disable() sie entfernen kann.
        runtime.loaded[manifest.id] = loaded
        record.state = "error"
        record.last_error = f"on_start() fehlgeschlagen: {start_error}"
        record.last_error_at = utcnow()
        return

    runtime.loaded[manifest.id] = loaded
    record.state = "enabled"
    record.last_error = None
    record.last_error_at = None

    # Per WebSocket hält der Browser den Katalog aktuell; ohne das Ereignis sähe er neue Widgets erst nach einem Reload.
    await get_event_bus().publish(Event(name="extension.enabled", payload={"ext_id": ext_id}))


def _mount_routes(app: FastAPI, runtime: ExtensionRuntime, ext_id: str, router: APIRouter) -> list[BaseRoute]:
    """Hängt den Router unter der heutigen und jeder alten Kennung ein (alte als veraltet markiert, gleiche Routen).

    Wirft ein Einhängen, werden die schon eingehängten Routen wieder entfernt, sonst blieben sie verwaist stehen."""
    # `mount_router` fügt immer an derselben Stelle ein, Späteres steht also davor: erst die alten Kennungen rückwärts,
    # die heutige zuletzt, damit die heutige vorn steht (`url_for`, OpenAPI) und die alten wie im Manifest folgen.
    legacy_routes: list[BaseRoute] = []
    try:
        for old in reversed(runtime.legacy_ids_of(ext_id)):
            legacy_routes += runtime.mount_router(app, old, router, deprecated=True)
        current = runtime.mount_router(app, ext_id, router)
    except Exception:
        runtime.unmount_router(app, legacy_routes)
        raise
    return current + legacy_routes


def _data_dir(settings: Settings, manifest: ExtensionManifest, store_id: str) -> Path:
    """`data/ext/<Speicher-Kennung>`; fehlt der Ordner, gilt der erste vorhandene der Kennung oder einer alten."""
    # Dort liegen die Dateien zu den Tabellenzeilen, die alle Kennungen teilen. Verschoben wird nie etwas, ein Rückweg
    # aufs alte Image findet auch neue Dateien.
    for name in dict.fromkeys((store_id, manifest.id, *manifest.legacy_ids)):
        if (settings.ext_data_dir / name).is_dir():
            return settings.ext_data_dir / name
    return settings.ext_data_dir / store_id


async def _unload_extension(app: FastAPI, session: AsyncSession, ext_id: str) -> None:
    """Entfernt die geladene Extension (on_stop, Tasks, Routen, Registrierungen); `state` bleibt Sache des Aufrufers."""
    runtime = get_extension_runtime()
    loaded = runtime.loaded.pop(ext_id, None)
    if loaded is None:
        return

    # Wie in `enable_extension`: vor Extension-Code committen, ohne den Aufrufer zu prüfen.
    await session.commit()
    try:
        await loaded.instance.on_stop(loaded.ctx)
    except Exception:  # noqa: BLE001 - Aufräumen darf nicht an einer kaputten Extension scheitern
        pass
    await _cleanup_loaded(app, loaded)


async def _cleanup_loaded(app: FastAPI, loaded: LoadedExtension) -> None:
    """Entfernt alles, was eine Extension angemeldet hat; für `_unload_extension` und den setup()-Fehlerfall."""
    runtime = get_extension_runtime()
    ext_id = loaded.manifest.id
    await runtime.cancel_tasks(loaded)
    if loaded.mounted_routes:
        runtime.unmount_router(app, loaded.mounted_routes)
    runtime.ui.clear_extension(ext_id)
    runtime.capabilities.clear_extension(ext_id)
    runtime.actions.clear_extension(ext_id)
    # Jobs hängen an der Speicher-Kennung (siehe `SchedulerHandle`).
    runtime.scheduler.clear_extension(loaded.store_id or ext_id)
    events_handle = getattr(loaded.ctx, "events", None)
    if events_handle is not None:
        events_handle.unsubscribe_all()

    from ..core.scheduler import get_scheduler_service

    await get_scheduler_service().unschedule_extension(loaded.store_id or ext_id)
    http_handle = getattr(loaded.ctx, "http", None)
    if http_handle is not None:
        await http_handle.aclose()


async def disable_extension(app: FastAPI, session: AsyncSession, ext_id: str) -> None:
    ext_id = get_extension_runtime().canonical(ext_id)
    await _unload_extension(app, session, ext_id)

    record = await get_record(session, ext_id)
    if record is not None:
        record.state = "disabled"
        record.last_error = None
        record.last_error_at = None
        await get_event_bus().publish(Event(name="extension.disabled", payload={"ext_id": ext_id}))
    await _announce_current(session)
