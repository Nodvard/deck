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

UNTOUCHED_KEY_PREFIX = "extension.untouched."
"""Globale Einstellung `extension.untouched.<id>`: die Erweiterung wurde neu entdeckt und seither
von niemandem bewusst ein- oder ausgeschaltet. Nur dann darf der Kern sie bei einem Anlass aus
`enable_on` (Manifest) von selbst einschalten. Erweiterungen, die es schon vor dieser Funktion
gab, haben den Eintrag nie -- sie bleiben so unangetastet, auch wenn jemand sie vor langer Zeit
ausgeschaltet hat. `<id>` ist die Speicher-Kennung (`id` der Registry-Zeile, siehe `get_record`)."""


class ExtensionLoadError(Exception):
    pass


async def get_record(session: AsyncSession, ext_id: str) -> ExtensionRecord | None:
    """Die Registry-Zeile zu einer Kennung (heutige oder alte, siehe `ExtensionManifest.legacy_ids`).

    Gesucht wird ueber die Speicher-Kennung (`ExtensionRuntime.store_id`): nach einer Umbenennung ist das
    die Zeile, in der Einstellungen und Zeitplaene liegen; ein verwaister Zwilling kommt so nie zurueck.
    Ohne Umbenennung ist es die Zeile mit genau dieser Kennung. Alle Zugriffe auf eine einzelne Zeile
    laufen hierueber (ein Test verbietet direkte `session.get(ExtensionRecord, ...)` ausserhalb dieser Datei)."""
    return await session.get(ExtensionRecord, get_extension_runtime().store_id(ext_id))


async def get_records(session: AsyncSession, ext_ids: Iterable[str]) -> dict[str, ExtensionRecord]:
    """Die Registry-Zeilen zu mehreren Kennungen (heutige oder alte) mit EINER Abfrage, wie `get_record` je Kennung.

    Der Schluessel ist die Kennung, wie sie uebergeben wurde: `get_records(s, ["alt", "neu"])` liefert fuer beide
    dieselbe Zeile, wenn `alt` eine alte Kennung von `neu` ist. Kennungen ohne Zeile fehlen im Ergebnis. Fuer Anzeigen
    wie „Vorgeschlagen von“, bei denen die Kennung aus gespeicherten Daten kommt und auch die einer frueheren Version
    sein kann."""
    runtime = get_extension_runtime()
    row_ids = {ext_id: runtime.store_id(ext_id) for ext_id in ext_ids}
    if not row_ids:
        return {}
    rows = (await session.execute(select(ExtensionRecord).where(ExtensionRecord.id.in_(set(row_ids.values()))))).scalars().all()
    by_row = {row.id: row for row in rows}
    return {ext_id: by_row[row_id] for ext_id, row_id in row_ids.items() if row_id in by_row}


async def is_stale_twin_address(session: AsyncSession, ext_id: str) -> bool:
    """`ext_id` ist eine alte Kennung, unter der noch eine eigene Registry-Zeile liegt, die die umbenannte
    Erweiterung nicht nutzt (verwaister Zwilling, `ExtensionRuntime.is_stale_twin`). Die Adresse ist dann
    mehrdeutig: `get_record` liefert zwar die Zeile der Erweiterung, wer aber noch die alte Kennung benutzt,
    kennt sie womoeglich aus der Zeit, in der der Zwilling galt (Rueckweg aufs alte Image). Aendern darf die
    API ueber so eine Adresse deshalb nichts. Ohne `legacy_ids` immer `False`."""
    if not get_extension_runtime().is_stale_twin(ext_id):
        return False
    return await session.get(ExtensionRecord, ext_id) is not None


async def _clear_untouched(session: AsyncSession, ext_id: str) -> None:
    row = await session.get(Setting, (UNTOUCHED_KEY_PREFIX + ext_id, "global", ""))
    if row is not None:
        await session.delete(row)
        await session.flush()


async def record_user_choice(session: AsyncSession, ext_id: str) -> None:
    """Jemand hat die Erweiterung bewusst ein- oder ausgeschaltet: ab jetzt schaltet der Kern sie
    nie mehr von selbst ein."""
    await _clear_untouched(session, get_extension_runtime().store_id(ext_id))


def public_route_prefixes(ext_id: str) -> list[str]:
    """Adressen (`/api/v1/ext/<id>/...`), die die geladene Erweiterung mit `public=True`
    ohne Anmeldung anbietet. Fuer das Protokoll beim Einschalten; leer, wenn es keine gibt
    oder die Erweiterung nicht geladen ist. Eine umbenannte Erweiterung ist zusaetzlich unter
    ihren alten Kennungen erreichbar (`enable_extension`); diese Adressen stehen mit darin."""
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
    """Schaltet alle Erweiterungen ein, die `trigger` in `enable_on` nennen, noch ausgeschaltet
    und unberuehrt sind (siehe `UNTOUCHED_KEY_PREFIX`). Rueckgabe: die IDs, die danach laufen.
    Der Kern kennt keine Erweiterung beim Namen -- nur die Kennzeichnung im Manifest. Jeder
    Fehler bleibt hier haengen: der Aufrufer (z. B. das Anlegen eines Zugangs) darf daran nie
    scheitern."""
    enabled: list[str] = []
    runtime = get_extension_runtime()
    try:
        records = (await session.execute(select(ExtensionRecord).where(ExtensionRecord.state == "disabled"))).scalars().all()
        for record in list(records):
            if runtime.is_stale_twin(record.id):
                continue  # gehoert zu einer umbenannten Erweiterung, die eine andere Zeile nutzt
            if trigger not in ((record.manifest or {}).get("enable_on") or []):
                continue
            if await session.get(Setting, (UNTOUCHED_KEY_PREFIX + record.id, "global", "")) is None:
                continue
            ext_id = runtime.canonical(record.id)
            found = runtime.discovered.get(ext_id)
            if found is None or not found.ok:
                # Fehlt gerade (z. B. nach einem Rueckweg aufs alte Image): nichts anfassen. Weder
                # `state` noch die Markierung "unberuehrt" aendern sich -- der Anlass gilt spaeter wieder.
                continue
            # Die Entscheidung gilt ab jetzt als getroffen -- auch wenn das Einschalten scheitert:
            # ein zweiter Versuch beim naechsten Zugang waere nur Laerm.
            await _clear_untouched(session, record.id)
            try:
                await enable_extension(app, session, settings, ext_id)
            except Exception:  # noqa: BLE001 - siehe Docstring
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
    """Das Manifest, wie es in der Registry-Zeile steht. Eine leere `legacy_ids`-Liste bleibt weg:
    die Zeilen von Erweiterungen, die nie umbenannt wurden, bleiben genau wie vorher."""
    data = manifest.model_dump(mode="json")
    if not data.get("legacy_ids"):
        data.pop("legacy_ids", None)
    return data


async def _find_record(session: AsyncSession, d: DiscoveredExtension) -> ExtensionRecord | None:
    """Die Zeile, die fuer einen Fund gilt: die mit seiner Kennung, sonst die erste vorhandene einer alten
    Kennung in der Reihenfolge von `legacy_ids` (umbenannte Erweiterung auf einer bestehenden Installation).
    Jede weitere vorhandene Zeile einer alten Kennung ist ein verwaister Zwilling und bleibt liegen."""
    assert d.manifest is not None  # nur fuer gueltige Funde
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
    """Voller Scan (Verzeichnis + Entry-Points), Registry-Zeilen fuer neue Funde
    anlegen (Default `state=disabled` -- ein Admin muss bewusst aktivieren), Ergebnis
    im Runtime-Bestand fuer spaetere `enable()`-Aufrufe zwischenspeichern.

    Umbenannte Erweiterungen (`legacy_ids`): gibt es keine Zeile mit der Kennung, gilt die einer alten
    Kennung weiter (Speicher-Kennung, `ExtensionRuntime.store_ids`) -- nichts wird kopiert oder
    umbenannt, das alte Image findet nach einem Rueckweg genau seine Zeile wieder."""
    resolution = resolve_legacy(discover_all(settings.extensions_dir))
    for refused in resolution.refused:
        log.warning("Erweiterung '%s' wird nicht geladen: %s", refused.id, refused.error)
    by_id: dict[str, DiscoveredExtension] = {}
    store_ids: dict[str, str] = {}
    for d in resolution.found:
        if not d.ok:
            continue
        assert d.manifest is not None  # d.ok garantiert das
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
    """Boot-Reconciliation: alles, was von einem frueheren Lauf noch `state=enabled`
    ist, jetzt tatsaechlich laden -- "Extension aktivieren" ist eine dauerhafte
    Admin-Entscheidung, kein Prozess-Neustart soll sie stillschweigend vergessen.

    Das gilt auch, wenn eine Erweiterung gerade fehlt (zurueckgerolltes Image, noch nicht
    kopierter Ordner): `at_boot=True` laesst ihren Zustand dann stehen und merkt sich nur
    den Grund in `last_error`. Kommt sie zurueck, laedt der naechste Start sie wieder."""
    runtime = get_extension_runtime()
    result = await session.execute(select(ExtensionRecord).where(ExtensionRecord.state == "enabled"))
    records = result.scalars().all()
    for record in records:
        if runtime.is_stale_twin(record.id):
            continue  # Zeile einer alten Kennung, die Erweiterung nutzt eine andere (siehe get_record)
        await enable_extension(app, session, settings, record.id, at_boot=True)
    _announce_loaded(runtime, records)


LOADED_LINE_PREFIX = "Erweiterungen geladen: "
"""Beginn der Zeile, die jeder Start (genau eine) und jedes Ein- oder Ausschalten danach ins Container-Protokoll
schreibt (`_announce_loaded`). `deploy/pi_switch.sh` sucht genau diesen Text, die letzte Zeile zaehlt; ein Test haelt
beide gleich."""
LOADED_LINE_NONE = "(keine)"
"""Steht hinter `LOADED_LINE_PREFIX`, wenn keine Erweiterung laeuft. Kein Zeichen
einer Kennung (Klammern), also nie mit einer verwechselbar."""


def _announce_loaded(runtime: ExtensionRuntime, records: list[ExtensionRecord]) -> None:
    """Schreibt eine Zeile `Erweiterungen geladen: <Speicher-Kennungen, sortiert, durch Komma getrennt>`.

    Gemeint ist, was gerade wirklich laeuft: eingeschaltet, geladen UND `on_start()` ohne Fehler
    (`state == "enabled"` der Zeile, die die Erweiterung nutzt; ein verwaister Zwilling zaehlt nie). Eine
    eingeschaltete, aber fehlende oder abgestuerzte Erweiterung steht nicht darin. Mit der letzten dieser
    Zeilen vergleicht `deploy/pi_switch.sh` beim Ausliefern den alten mit dem neuen Container: Fehlt danach
    eine Kennung, wird zurueckgeschaltet. Deshalb schreibt nicht nur der Start sie, sondern auch jedes Ein-
    und Ausschalten danach (`_announce_current`): Eine seit dem Start ausgeschaltete Erweiterung galte sonst
    beim naechsten Ausliefern als verloren. Es zaehlt die Speicher-Kennung (`id` der Registry-Zeile), nicht
    die heutige Kennung: sie bleibt auch bei einer Umbenennung gleich, der Vergleich zwischen altem und neuem
    Image also eindeutig. Kennungen bestehen nur aus `a-z`, `0-9` und `-` (`nodvard_sdk.manifest.ID_RE`),
    ein Komma kommt darin nie vor.

    Mit `print` statt `logging`: Der Dienst richtet kein Logging ein, ausser fuer uvicorn selbst. Eine
    `log.info`-Zeile erreichte das Container-Protokoll deshalb nie (die Stufe des Wurzel-Loggers ist
    `WARNING`). Wie bei der Einrichtungs-Zeile in `services/auth.py` und den `[boot]`-Zeilen. Ein
    Schreibfehler (Ausgabe geschlossen) darf den Start nie aufhalten."""
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
    """`_announce_loaded` nach einem Ein- oder Ausschalten im laufenden Betrieb, mit dem Zustand im Speicher.

    Liest alle Zeilen ohne vorher zu schreiben (`no_autoflush`): Der Aufrufer schreibt erst spaeter, und ein
    Schreibvorgang hier hielte die Sperre der Datenbank, waehrend er womoeglich noch Code einer Erweiterung
    aufruft (siehe `enable_extension`). Zeilen, die die Sitzung schon kennt, behalten dabei ihren noch nicht
    geschriebenen Zustand; gefiltert wird darauf erst in `_announce_loaded`."""
    with session.no_autoflush:
        records = (await session.execute(select(ExtensionRecord))).scalars().all()
    _announce_loaded(get_extension_runtime(), list(records))


async def enable_extension(
    app: FastAPI, session: AsyncSession, settings: Settings, ext_id: str, *, at_boot: bool = False
) -> None:
    """Erweiterung laden und `state` auf das Ergebnis setzen.

    `at_boot=True` nur aus `load_enabled_from_registry()`: Die Entscheidung "eingeschaltet" stammt
    dann aus einem frueheren Lauf und gilt weiter, auch wenn die Erweiterung diesmal nicht
    auffindbar ist. Ein ausdrueckliches Einschalten (Standard) einer nicht auffindbaren
    Erweiterung wirft dagegen `ExtensionLoadError` (die API antwortet 404) und aendert nichts.
    Ausser beim Start (der schreibt sie einmal am Ende) steht danach die Zeile `Erweiterungen geladen` neu im
    Protokoll (`_announce_loaded`), auch wenn die Erweiterung dabei abgestuerzt ist; nur nach `ExtensionLoadError`
    nicht, dann hat sich nichts geaendert.

    `ext_id` darf auch eine alte Kennung sein (`legacy_ids`); geladen wird immer unter der heutigen."""
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
            return  # idempotent -- zweimal aktivieren ist kein Fehler
        # on_start() ist beim letzten Mal abgestuerzt: die Extension blieb halb geladen
        # (damit disable() sie aufraeumen kann). Ein erneutes "Einschalten" darf dann
        # nicht stillschweigend nichts tun, sondern entlaedt sie sauber und startet neu --
        # der Zustand spiegelt danach das neue Ergebnis.
        await _unload_extension(app, session, ext_id)

    record = await get_record(session, ext_id)
    if record is None:
        raise ExtensionLoadError(f"Extension '{ext_id}' ist nicht in der Registry.")

    discovered = runtime.discovered.get(ext_id)
    if discovered is None or not discovered.ok:
        # Abgelehnt wegen einer alten Kennung (`legacy_ids`): dann den eigentlichen Grund nennen. Auch fuer die
        # Zeile einer alten Kennung, deren neue Version abgelehnt wurde (Update einer bestehenden Installation).
        refused = runtime.refused.get(ext_id) or runtime.refused_old.get(ext_id)
        if not at_boot:
            # Ausdruecklich eingeschaltet, aber nicht da: nichts schreiben (auch `state` bleibt).
            if refused:
                raise ExtensionLoadError(f"Extension '{ext_id}' wird nicht geladen: {refused}")
            raise ExtensionLoadError(
                f"Extension '{ext_id}' wurde nicht gefunden (Ordner oder Paket fehlt, oder das Manifest ist ungültig)."
            )
        # Beim Start fehlt die Erweiterung nur: `state` bleibt wie er war (ein Rueckweg aufs alte
        # Image oder ein erneutes Update soll die Entscheidung "eingeschaltet" nicht loeschen), die
        # API meldet solange "error" (siehe `ExtensionOut.from_model`).
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
        manifest=manifest, instance=instance, ctx=None, granted_permissions=granted, store_id=record.id
    )
    ctx = build_context(runtime, loaded, manifest, granted, _data_dir(settings, manifest, record.id), settings)
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
        # Auch Routen, die setup() erst nach include_router() angehaengt hat.
        ctx.api.check_routes()
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
        # Unter der heutigen Kennung und (umbenannt, `legacy_ids`) unter jeder alten. Sie stehen in
        # `mounted_routes`, damit das Ausschalten sie mit entfernt.
        loaded.mounted_routes = _mount_routes(app, runtime, manifest.id, loaded.router)

    await session.commit()
    start_error: Exception | None = None
    try:
        await instance.on_start(ctx)
    except Exception as exc:  # noqa: BLE001
        start_error = exc

    # Der Router steht seit setup() und ist live: Routen, die on_start() (auch kurz vor
    # einem Absturz) angehaengt hat, bekommen die Anmeldepruefung nicht. Sie fliegen raus,
    # und die Erweiterung wird wie bei einem setup()-Fehler zurueckgebaut.
    try:
        ctx.api.check_routes()
    except Exception as exc:  # noqa: BLE001
        record.state = "error"
        record.last_error = f"on_start() hat Routen angehängt, die keine Anmeldung prüfen: {exc}"
        record.last_error_at = utcnow()
        try:
            await instance.on_stop(ctx)
        except Exception:  # Aufraeumen darf nicht an einer kaputten Extension scheitern
            log.exception("on_stop() nach abgelehnten Routen von '%s' fehlgeschlagen", manifest.id)
        try:
            await _cleanup_loaded(app, loaded)
        except Exception:  # der eigentliche Fehler bleibt die Meldung
            logging.getLogger(__name__).exception("Aufräumen nach abgelehnten Routen von '%s'", manifest.id)
        return

    if start_error is not None:
        # setup() ist bereits durchgelaufen (Seiten/Widgets/Router stehen) -- das ist
        # bewusst so belassen (halb registriert ist immer noch nuetzlicher als gar
        # nicht sichtbar), aber der Fehler wird gemeldet und die Extension bleibt
        # trackbar, damit disable() sie sauber wieder entfernen kann.
        runtime.loaded[manifest.id] = loaded
        record.state = "error"
        record.last_error = f"on_start() fehlgeschlagen: {start_error}"
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


def _mount_routes(app: FastAPI, runtime: ExtensionRuntime, ext_id: str, router: APIRouter) -> list[BaseRoute]:
    """Router unter der heutigen Kennung einhaengen und, nach einer Umbenennung (`legacy_ids`), unter jeder
    alten. Gleiche Route-Objekte des Routers, also auch dieselbe Anmeldepruefung; die alten sind im
    OpenAPI-Schema als veraltet markiert.

    `mount_router` fuegt immer an derselben Stelle ein, Spaeteres steht also davor: erst die alten Kennungen
    rueckwaerts, die heutige zuletzt. Dann steht die heutige vorne (`url_path_for` bzw. `request.url_for`
    bauen ihre Adresse, im Schema kommt sie zuerst), die alten folgen wie im Manifest.

    Wirft ein Einhaengen, werden die in diesem Aufruf schon eingehaengten Routen wieder entfernt (wie beim
    Ausschalten) und der Fehler geht weiter nach oben: sonst blieben sie verwaist in der App stehen."""
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
    """`data/ext/<Speicher-Kennung>`, ohne Umbenennung also `data/ext/<Kennung>`.

    Nach einer Umbenennung (`legacy_ids`): Fehlt der Ordner der Speicher-Kennung, gilt der erste vorhandene
    Ordner der Kennung oder einer alten Kennung -- dort liegen die Dateien zu den Zeilen in den Tabellen der
    Erweiterung, die alle Kennungen teilen. Gibt es keinen, wird der Ordner der Speicher-Kennung angelegt;
    so findet ein Rueckweg aufs alte Image auch neue Dateien. Verschoben wird nie etwas."""
    for name in dict.fromkeys((store_id, manifest.id, *manifest.legacy_ids)):
        if (settings.ext_data_dir / name).is_dir():
            return settings.ext_data_dir / name
    return settings.ext_data_dir / store_id


async def _unload_extension(app: FastAPI, session: AsyncSession, ext_id: str) -> None:
    """Geladene Extension sauber entfernen (on_stop, Tasks, Routen, Registrierungen).
    Der Registry-Zustand bleibt Sache des Aufrufers. `ext_id` ist die heutige Kennung."""
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
    # Jobs haengen an der Speicher-Kennung (siehe `SchedulerHandle`), nicht an der Kennung.
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
