"""Registry-Sync, Laden/Entladen von Extensions.

Trennung wie ueberall im Projekt: `ext/` sind die reinen Bausteine (Entdeckung,
Kontext-Bau, Laufzeit-Bestand), hier wird das mit der DB und der laufenden FastAPI-App
verdrahtet -- Endpunkte in `api/v1/extensions.py` rufen nur diese Funktionen auf.

**Fehler-Isolation ist das Leitmotiv (docs/02 §1):** jede Phase (Import, `setup()`,
`on_start()`) faengt ihre eigenen Fehler, markiert `state=error` mit `last_error`, und
gibt zurueck -- niemals wirft `enable_extension()` weiter nach oben in eine
Boot-Sequenz, die dadurch fuer ALLE anderen Extensions abbrechen wuerde.
"""

from __future__ import annotations

import importlib
import logging
import sys

from fastapi import FastAPI
from nodvard_sdk import is_compatible
from nodvard_sdk.version import API_VERSION as SDK_API_VERSION
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nodvard_sdk.types import Event

from ..config import Settings
from ..core.events import get_event_bus
from ..db import utcnow
from ..ext.context import build_context
from ..ext.discovery import DiscoveredExtension, discover_all
from ..ext.runtime import LoadedExtension, get_extension_runtime
from ..models import ExtensionRecord, Setting
from . import audit as audit_service
from . import settings as settings_service


log = logging.getLogger(__name__)

UNTOUCHED_KEY_PREFIX = "extension.untouched."
"""Globale Einstellung `extension.untouched.<id>`: die Erweiterung wurde neu entdeckt und seither
von niemandem bewusst ein- oder ausgeschaltet. Nur dann darf der Kern sie bei einem Anlass aus
`enable_on` (Manifest) von selbst einschalten. Erweiterungen, die es schon vor dieser Funktion
gab, haben den Eintrag nie -- sie bleiben so unangetastet, auch wenn jemand sie vor langer Zeit
ausgeschaltet hat."""


class ExtensionLoadError(Exception):
    pass


async def _clear_untouched(session: AsyncSession, ext_id: str) -> None:
    row = await session.get(Setting, (UNTOUCHED_KEY_PREFIX + ext_id, "global", ""))
    if row is not None:
        await session.delete(row)
        await session.flush()


async def record_user_choice(session: AsyncSession, ext_id: str) -> None:
    """Jemand hat die Erweiterung bewusst ein- oder ausgeschaltet: ab jetzt schaltet der Kern sie
    nie mehr von selbst ein."""
    await _clear_untouched(session, ext_id)


async def auto_enable_for(
    app: FastAPI, session: AsyncSession, settings: Settings, trigger: str, *, user_id: str | None = None
) -> list[str]:
    """Schaltet alle Erweiterungen ein, die `trigger` in `enable_on` nennen, noch ausgeschaltet
    und unberuehrt sind (siehe `UNTOUCHED_KEY_PREFIX`). Rueckgabe: die IDs, die danach laufen.
    Der Kern kennt keine Erweiterung beim Namen -- nur die Kennzeichnung im Manifest. Jeder
    Fehler bleibt hier haengen: der Aufrufer (z. B. das Anlegen eines Zugangs) darf daran nie
    scheitern."""
    enabled: list[str] = []
    try:
        records = (await session.execute(select(ExtensionRecord).where(ExtensionRecord.state == "disabled"))).scalars().all()
        for record in list(records):
            if trigger not in ((record.manifest or {}).get("enable_on") or []):
                continue
            if await session.get(Setting, (UNTOUCHED_KEY_PREFIX + record.id, "global", "")) is None:
                continue
            # Die Entscheidung gilt ab jetzt als getroffen -- auch wenn das Einschalten scheitert:
            # ein zweiter Versuch beim naechsten Zugang waere nur Laerm.
            await _clear_untouched(session, record.id)
            try:
                await enable_extension(app, session, settings, record.id)
            except Exception:  # noqa: BLE001 - siehe Docstring
                log.exception("Automatisches Einschalten von '%s' fehlgeschlagen", record.id)
                record.state = "error"
                record.last_error = "Automatisches Einschalten fehlgeschlagen."
                record.last_error_at = utcnow()
            ok = record.state == "enabled"
            if ok:
                enabled.append(record.id)
            await audit_service.log(
                session, actor_type="user" if user_id else "system", actor_id=user_id or "system",
                action="extension.auto_enabled", outcome="success" if ok else "failure",
                target_type="extension", target_id=record.id,
                detail={"trigger": trigger, **({} if ok else {"error": (record.last_error or "")[:300]})},
            )
    except Exception:  # noqa: BLE001
        log.exception("Automatisches Einschalten (%s) fehlgeschlagen", trigger)
    return enabled


async def discover_and_sync(session: AsyncSession, settings: Settings) -> dict[str, DiscoveredExtension]:
    """Voller Scan (Verzeichnis + Entry-Points), Registry-Zeilen fuer neue Funde
    anlegen (Default `state=disabled` -- ein Admin muss bewusst aktivieren), Ergebnis
    im Runtime-Bestand fuer spaetere `enable()`-Aufrufe zwischenspeichern."""
    discovered = discover_all(settings.extensions_dir)
    by_id: dict[str, DiscoveredExtension] = {}
    for d in discovered:
        if not d.ok:
            continue
        by_id[d.id] = d
        record = await session.get(ExtensionRecord, d.id)
        if record is None:
            session.add(
                ExtensionRecord(
                    id=d.manifest.id,
                    version=d.manifest.version,
                    api_version=d.manifest.api_version,
                    state="disabled",
                    source=d.source,
                    manifest=d.manifest.model_dump(mode="json"),
                )
            )
            # Neu entdeckt und noch nie angefasst (siehe UNTOUCHED_KEY_PREFIX).
            await settings_service.set_global(session, UNTOUCHED_KEY_PREFIX + d.id, True)
        else:
            record.version = d.manifest.version
            record.api_version = d.manifest.api_version
            record.source = d.source
            record.manifest = d.manifest.model_dump(mode="json")
    await session.flush()

    get_extension_runtime().discovered = by_id
    return by_id


async def load_enabled_from_registry(app: FastAPI, session: AsyncSession, settings: Settings) -> None:
    """Boot-Reconciliation: alles, was von einem frueheren Lauf noch `state=enabled`
    ist, jetzt tatsaechlich laden -- "Extension aktivieren" ist eine dauerhafte
    Admin-Entscheidung, kein Prozess-Neustart soll sie stillschweigend vergessen."""
    result = await session.execute(select(ExtensionRecord).where(ExtensionRecord.state == "enabled"))
    for record in result.scalars().all():
        await enable_extension(app, session, settings, record.id)


async def enable_extension(app: FastAPI, session: AsyncSession, settings: Settings, ext_id: str) -> None:
    runtime = get_extension_runtime()
    if ext_id in runtime.loaded:
        existing = await session.get(ExtensionRecord, ext_id)
        if existing is None or existing.state != "error":
            return  # idempotent -- zweimal aktivieren ist kein Fehler
        # on_start() ist beim letzten Mal abgestuerzt: die Extension blieb halb geladen
        # (damit disable() sie aufraeumen kann). Ein erneutes "Einschalten" darf dann
        # nicht stillschweigend nichts tun, sondern entlaedt sie sauber und startet neu --
        # der Zustand spiegelt danach das neue Ergebnis.
        await _unload_extension(app, session, ext_id)

    record = await session.get(ExtensionRecord, ext_id)
    if record is None:
        raise ExtensionLoadError(f"Extension '{ext_id}' ist nicht in der Registry.")

    discovered = runtime.discovered.get(ext_id)
    if discovered is None or not discovered.ok:
        record.state = "error"
        record.last_error = "Nicht (mehr) discoverbar -- Manifest fehlt oder ist ungültig."
        record.last_error_at = utcnow()
        return

    manifest = discovered.manifest
    assert manifest is not None  # discovered.ok garantiert das

    if not is_compatible(manifest.api_version):
        record.state = "incompatible"
        record.last_error = (
            f"api_version {manifest.api_version!r} inkompatibel mit Kern-SDK {SDK_API_VERSION!r}."
        )
        record.last_error_at = utcnow()
        return

    # WP-3 kennt noch keine Einzelauswahl-UI fuer Permissions -- Aktivieren gewaehrt
    # das gesamte im Manifest deklarierte Set. Eine granulare "weniger als beantragt"-
    # Bestaetigung (docs/03 §6 ExtensionRecord.granted_permissions-Kommentar) ist eine
    # spaetere UI-Aufgabe, keine Host-Mechanik.
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
    except Exception as exc:  # noqa: BLE001 - Fehler-Isolation, siehe Modul-Docstring
        record.state = "error"
        record.last_error = f"Import von '{manifest.entrypoint}' fehlgeschlagen: {exc}"
        record.last_error_at = utcnow()
        return

    loaded = LoadedExtension(
        manifest=manifest, instance=instance, ctx=None, granted_permissions=granted
    )
    ctx = build_context(runtime, loaded, manifest, granted, settings.ext_data_dir / manifest.id, settings)
    loaded.ctx = ctx

    # Live gefunden (WP-8, dasselbe Muster wie der Fix in core/gate.py::execute_action()
    # -- siehe dortiger Docstring und docs/00-DECISIONS.md D-14): der Aufrufer (z. B.
    # die Boot-Reconciliation `load_enabled_from_registry()` oder ein API-Request mit
    # `SessionDep`) haelt `session` ueber den gesamten Aufruf offen, mit dem gerade
    # gesetzten `record.granted_permissions` als anstehendem Schreibvorgang.
    # `instance.setup()`/`instance.on_start()` sind Extension-Code -- rufen sie einen
    # Handle auf, der selbst eine unabhaengige `session_scope()` oeffnet und schreibt
    # (z. B. `ctx.scheduler.register_job()` in setup(), `ctx.audit.log()` in on_start(),
    # wie hello-world es tut), blockiert SQLites Ein-Schreiber-Regel die verschachtelte
    # Transaktion -- rueckwirkend die wahrscheinlichste Erklaerung fuer die seit WP-6
    # wiederholt beobachteten, nie sicher diagnostizierten "database is locked"-
    # Vorfaelle beim Neustart. Fix: vor JEDEM Uebergang in Extension-Code committen.
    await session.commit()
    try:
        await instance.setup(ctx)
    except Exception as exc:  # noqa: BLE001
        record.state = "error"
        record.last_error = f"setup() fehlgeschlagen: {exc}"
        record.last_error_at = utcnow()
        # Was setup() vor dem Absturz schon angemeldet hat (Ereignis-Abos, Aufgaben,
        # Seiten, Capabilities ...), gehoert niemandem mehr: `runtime.loaded` kennt die
        # Extension nicht, `_unload_extension` fasst sie nie an. Ohne Aufraeumen liefe
        # nach dem erneuten Einschalten alles doppelt.
        try:
            await _cleanup_loaded(app, loaded)
        except Exception:  # der setup()-Fehler bleibt die eigentliche Meldung
            logging.getLogger(__name__).exception("Aufräumen nach fehlgeschlagenem setup() von '%s'", manifest.id)
        return

    if loaded.router is not None:
        loaded.mounted_routes = runtime.mount_router(app, manifest.id, loaded.router)

    await session.commit()
    try:
        await instance.on_start(ctx)
    except Exception as exc:  # noqa: BLE001
        # setup() ist bereits durchgelaufen (Seiten/Widgets/Router stehen) -- das ist
        # bewusst so belassen (halb registriert ist immer noch nuetzlicher als gar
        # nicht sichtbar), aber der Fehler wird gemeldet und die Extension bleibt
        # trackbar, damit disable() sie sauber wieder entfernen kann.
        runtime.loaded[manifest.id] = loaded
        record.state = "error"
        record.last_error = f"on_start() fehlgeschlagen: {exc}"
        record.last_error_at = utcnow()
        return

    runtime.loaded[manifest.id] = loaded
    record.state = "enabled"
    record.last_error = None
    record.last_error_at = None

    # WP-7: das Dashboard haelt Seiten-/Widget-Katalog per WS aktuell, statt sie zu
    # pollen -- ohne dieses Event wuesste ein offener Browser-Tab erst nach einem
    # manuellen Reload, dass gerade neue Widgets aufgetaucht sind (live im Boot-Test
    # gefunden: der Katalog aenderte sich serverseitig sofort, sichtbar wurde das aber
    # nirgends). `ctx.events`-Konvention wiederverwendet (core/events.py faechert
    # automatisch auf den RBAC-gefilterten "events"-WS-Kanal aus), keine neue Kanal-Art.
    await get_event_bus().publish(Event(name="extension.enabled", payload={"ext_id": ext_id}))


async def _unload_extension(app: FastAPI, session: AsyncSession, ext_id: str) -> None:
    """Geladene Extension sauber entfernen (on_stop, Tasks, Routen, Registrierungen).
    Der Registry-Zustand bleibt Sache des Aufrufers."""
    runtime = get_extension_runtime()
    loaded = runtime.loaded.pop(ext_id, None)
    if loaded is None:
        return

    # Siehe D-14/enable_extension() oben: derselbe Schutz, auch wenn `session` hier
    # beim Aufruf typischerweise noch keinen anstehenden Schreibvorgang hat --
    # struktureller Schutz statt einer Annahme ueber den jeweils aktuellen Aufrufer.
    await session.commit()
    try:
        await loaded.instance.on_stop(loaded.ctx)
    except Exception:  # noqa: BLE001 - Aufraeumen darf nicht an einer kaputten Extension scheitern
        pass
    await _cleanup_loaded(app, loaded)


async def _cleanup_loaded(app: FastAPI, loaded: LoadedExtension) -> None:
    """Alles entfernen, was eine Extension angemeldet hat (Tasks, Routen, Registrierungen,
    Ereignis-Abos, Zeitplan, HTTP-Client) -- fuer `_unload_extension` und den setup()-Fehlerfall."""
    runtime = get_extension_runtime()
    ext_id = loaded.manifest.id
    await runtime.cancel_tasks(loaded)
    if loaded.mounted_routes:
        runtime.unmount_router(app, loaded.mounted_routes)
    runtime.ui.clear_extension(ext_id)
    runtime.capabilities.clear_extension(ext_id)
    runtime.actions.clear_extension(ext_id)
    runtime.scheduler.clear_extension(ext_id)
    events_handle = getattr(loaded.ctx, "events", None)
    if events_handle is not None:
        events_handle.unsubscribe_all()

    from ..core.scheduler import get_scheduler_service

    await get_scheduler_service().unschedule_extension(ext_id)
    http_handle = getattr(loaded.ctx, "http", None)
    if http_handle is not None:
        await http_handle.aclose()


async def disable_extension(app: FastAPI, session: AsyncSession, ext_id: str) -> None:
    await _unload_extension(app, session, ext_id)

    record = await session.get(ExtensionRecord, ext_id)
    if record is not None:
        record.state = "disabled"
        record.last_error = None
        record.last_error_at = None
        await get_event_bus().publish(Event(name="extension.disabled", payload={"ext_id": ext_id}))
