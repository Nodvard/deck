"""Anwendungseinstiegspunkt.

    uvicorn nodvard_deck.main:app --reload --port 8080

Startet mit **einem** Worker (docs/00-DECISIONS.md D-01) — die Extension-Registry, der
Event-Bus und der Scheduler halten Prozesszustand, der nicht dupliziert werden darf.
Das ist in `--reload`-Entwicklung ohnehin der Fall; die Warnung gilt fuer den
Produktivstart in deploy/.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager

import asyncssh
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from nodvard_sdk.errors import HostUnreachable
from nodvard_sdk.errors import PermissionDenied as ExtensionPermissionDenied
from nodvard_sdk.types import Event
from sqlalchemy.exc import OperationalError as SqlOperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from .api import api_v1_router
from .api.body_limit import BodyLimitMiddleware
from .api.errors import install_validation_error_handler
from .config import get_settings
from .core import bootstate
from .core.demo_seed import DemoBlockedError, seed_demo_data
from .core.error_text import describe_connection_error
from .core.events import get_event_bus
from .core.gate import fail_interrupted_on_boot as fail_interrupted_actions_on_boot
from .core.gate import shutdown_running as shutdown_running_actions
from .core.log_filters import install_access_log_filters
from .core.metrics_history import get_metrics_collector
from .core.restart import exit_if_requested
from .core.scheduler import get_scheduler_service, register_core_jobs
from .core.security import HashingBusy
from .core.ssh import SshError
from .db.session import get_engine, session_scope
from .services.auth import ensure_builtin_roles, prepare_setup_code
from .services.extensions import discover_and_sync, load_enabled_from_registry
from .services.jobs import mark_interrupted_on_boot
from .services.restore import record_result as record_restore_result
from .version import __version__

_event_logger = logging.getLogger("nodvard_deck.events")
_demo_logger = logging.getLogger("nodvard_deck.demo")
_boot_logger = logging.getLogger("nodvard_deck.boot")

APP_LOCK_WAIT_S = 20.0
"""So lange wartet die Anwendung auf die Sperre des Datenordners (z. B. ein `compose run`, dessen `boot` gerade
laeuft), bevor sie ohne Sperre weitermacht."""
STARTED_OK_RETRY_S = 60.0
"""Scheitert das Festhalten von `started_ok`, wird es in diesem Abstand wiederholt, bis es klappt."""


class _SpaStaticFiles(StaticFiles):
    """Nachtrag (beim Testen der
    neuen /settings-Route gefunden): `StaticFiles(html=True)` liefert `index.html`
    fuer `/`, aber NICHT fuer einen Tiefen-Link wie `/settings` oder `/files/foo` --
    ein direkter Aufruf, ein Lesezeichen oder ein Browser-Reload auf JEDER
    React-Router-Route ausser `/` endete deshalb schon immer in einem rohen
    `{"detail":"Not Found"}` statt der App. Betraf nicht nur die neue Settings-Seite,
    sondern grundsaetzlich jede Client-Route -- nur nie aufgefallen, weil man sie
    bisher immer nur per Klick (clientseitige Navigation) erreicht hat.

    Jede echte 404 innerhalb dieses Mounts faellt jetzt auf `index.html` zurueck --
    AUSSER unter `/api/`: dorthin gelangt nur, was kein API-Router kennt (Tippfehler,
    Route einer deaktivierten Extension), und das bleibt ein ehrlicher 404 (siehe
    `_is_api_path()`). Alles andere ist weder API noch vorhandene Datei im Build;
    React Router uebernimmt danach clientseitig."""

    async def get_response(self, path: str, scope: Scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Starlettes StaticFiles.get_response() RAIST bei "nicht gefunden" (siehe
            # dortige Quelle) -- gibt KEINE 404-Response zurueck, die sich hier
            # einfach abfangen liesse.
            if exc.status_code != 404:
                raise
            # Live gefunden: die Annahme im Docstring
            # ("steht fest, dass es kein API-Pfad ist") stimmte nicht -- ein UNBEKANNTER
            # `/api/...`-Pfad (Tippfehler, Route einer deaktivierten Extension) landete
            # hier und bekam `index.html` mit 200. API-Aufrufer (apiFetch) scheiterten
            # dann an "Unexpected token <" statt an einem ehrlichen 404.
            if _is_api_path(path) or _is_api_doc_path(path):
                raise
            response = await super().get_response("index.html", scope)
            path = "index.html"
        response.headers["Cache-Control"] = _cache_policy_for(path)
        return response


def _is_api_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    return normalized == "api" or normalized.startswith("api/")


_API_DOC_PATHS = frozenset({"docs", "docs/oauth2-redirect", "redoc", "openapi.json"})
"""Adressen der API-Doku von FastAPI. Sind sie abgeschaltet (Standard ausserhalb des
Entwicklungsmodus, siehe `create_app`), kennt sie kein Router -- sie wuerden dann hier mit der
App-Shell (200, HTML) beantwortet, und ein Werkzeug, das `/openapi.json` erwartet, bekaeme
HTML statt JSON. Sie bleiben deshalb ein ehrlicher 404. Wo sie eingeschaltet sind, erreichen
sie diesen Mount nie (ihre Routen stehen davor)."""


def _is_api_doc_path(path: str) -> bool:
    return path.replace("\\", "/").lstrip("./").rstrip("/") in _API_DOC_PATHS


def _cache_policy_for(path: str) -> str:
    """Live gefunden (nach einem Redeploy standen weiter alte UI-Texte da): ohne
    `Cache-Control` cachen Browser heuristisch anhand von `Last-Modified` -- bei
    einem Tage alten Build fuer Stunden, ohne nachzufragen. Fuer `index.html` ist das
    besonders heikel: ein veraltetes `index.html` referenziert gehashte
    `assets/index-<alt>.js`-Dateien, die es nach dem Rebuild nicht mehr gibt.

    `assets/` traegt Vite-Inhaltshashes im Dateinamen -- eine neue Version ist immer
    ein neuer Dateiname, deshalb dort dauerhaft cachebar. Alles andere (vor allem
    `index.html`) nur mit Revalidierung (`no-cache` = ETag/Last-Modified-Abgleich,
    ein billiges 304, kein erneuter Download)."""
    normalized = path.replace("\\", "/").lstrip("./")
    if normalized.startswith("assets/"):
        return "public, max-age=31536000, immutable"
    return "no-cache"


async def _log_event(event: Event) -> None:
    """Der eine eingebaute Abonnent des Event-Bus: bis der
    Extension-Host (WP-3) echte Konsumenten mitbringt, ist strukturiertes Loggen
    jedes Events die einzige Weise, den Bus im laufenden Betrieb zu beobachten
    (D-11)."""
    _event_logger.info("event name=%s correlation_id=%s", event.name, event.correlation_id)


async def _take_boot_lock(settings) -> bootstate.Lock:
    """Die laufende Anwendung haelt die Sperre des Datenordners (`<Datenordner>/.boot/app.lock`): `nodvard_deck.boot`
    spielt dann nie unter ihr etwas ein, und `nodvard_deck.admin restore-backup` raeumt keinen laufenden Upload weg.
    Nie ein Grund, nicht zu starten: Geht die Sperre nicht (anderer Halter, Dateisystem ohne Sperren), wird das
    gemeldet und ohne Sperre weitergemacht."""
    try:
        lock = await asyncio.to_thread(bootstate.acquire_lock, settings.data_dir, purpose="app", wait_s=APP_LOCK_WAIT_S)
    except bootstate.LockHeld:
        _boot_logger.error(
            "Die Sperre des Datenordners gehört einem anderen Prozess (läuft Nodvard Deck zweimal mit demselben Datenordner?). "
            "Ich starte trotzdem, aber ohne Sperre."
        )
        return bootstate.Lock(None, False)
    except Exception:  # noqa: BLE001
        _boot_logger.exception("boot_lock_failed")
        return bootstate.Lock(None, False)
    if not lock.held:
        _boot_logger.warning("Der Datenordner ließ sich nicht sperren (Dateisystem ohne Sperren?).")
    return lock


async def _mark_started_ok(settings, *, retry: bool = False) -> bool:
    """Der Start ist gelungen: `started_ok=true` in `<Datenordner>/.boot/state.json`. Damit weiss `nodvard_deck.boot`
    einer spaeter zurueckgestellten aelteren Version, dass in der neuen schon gearbeitet wurde (siehe boot.py).
    `False`, wenn es nicht geklappt hat (nie ein Grund, nicht zu starten)."""
    try:
        await asyncio.to_thread(bootstate.mark_started_ok, settings.data_dir)
    except Exception as exc:  # noqa: BLE001 - nie ein Grund, nicht zu starten
        if retry:  # beim Wiederholen nur eine Zeile, kein Traceback je Minute
            _boot_logger.warning("boot_mark_started_retry_failed error=%s", type(exc).__name__)
        else:
            _boot_logger.exception("boot_mark_started_failed")
        return False
    return True


async def _keep_marking_started_ok(settings) -> None:
    """Wiederholt `_mark_started_ok`, bis es klappt (z. B. war der Speicher beim Start kurz voll). Bliebe `started_ok=false`
    stehen, hielte eine spaeter zurueckgestellte aeltere Version die neue fuer nie gestartet."""
    while True:
        await asyncio.sleep(STARTED_OK_RETRY_S)
        if await _mark_started_ok(settings, retry=True):
            _boot_logger.info("boot_mark_started_ok_retried")
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.ensure_data_dir()
    lock = await _take_boot_lock(settings)
    app.state.boot_lock = lock
    try:
        async with _lifespan_body(app):
            yield
    finally:
        lock.release()


@asynccontextmanager
async def _lifespan_body(app: FastAPI):
    settings = get_settings()
    install_access_log_filters()  # falls das Logging nach dem Import neu eingerichtet wurde
    app.state.started_at = time.monotonic()

    # Idempotent -- sicher bei jedem Start (eingebaute Rollen muessen
    # existieren, bevor irgendjemand eine davon zugewiesen bekommen kann).
    async with session_scope() as session:
        await ensure_builtin_roles(session)

    # Noch kein Konto? Dann den Einrichtungscode ins Protokoll schreiben (bei JEDEM Start,
    # bis die Einrichtung fertig ist). Mit vorhandenem Owner passiert hier nichts.
    async with session_scope() as session:
        await prepare_setup_code(session, settings)

    # Ergebnis einer Wiederherstellung (von `nodvard_deck.boot` vor dem Start geschrieben) ins Audit-Protokoll
    # der jetzt gueltigen Datenbank eintragen und Aufgegebenes in restore/ wegraeumen.
    await record_restore_result(settings)

    # D-10 ("Demo-Image zum Selbst-Antesten"): NODVARD_DECK_DEMO_MODE=1 legt dieselben Beispieldaten
    # an wie der Knopf "Mit Beispieldaten ansehen" -- idempotent, und nie auf einer Installation, die
    # schon echte Server hat (dann bleibt alles, wie es ist).
    if settings.demo_mode:
        try:
            async with session_scope() as session:
                await seed_demo_data(session)
        except DemoBlockedError:
            _demo_logger.info("demo_mode_skipped reason=hosts_exist")

    # Aktionen, die ein Neustart mitten in der Ausfuehrung abgebrochen hat,
    # als fehlgeschlagen markieren -- VOR dem Laden der Extensions, damit keine gerade
    # neu gestartete Ausfuehrung (z. B. aus einem Extension-Hintergrund-Task) mit
    # erwischt wird.
    async with session_scope() as session:
        await fail_interrupted_actions_on_boot(session)

    bus = get_event_bus()
    bus.subscribe("*", _log_event)
    await bus.publish(Event(name="system.started"))

    # WP-3: Extensions entdecken (Verzeichnis + Entry-Points), Registry-Zeilen fuer
    # neue Funde anlegen, dann alles laden, was von einem frueheren Lauf noch
    # state=enabled ist -- "aktiviert" ist eine dauerhafte Admin-Entscheidung, kein
    # Prozess-Neustart soll sie stillschweigend vergessen. Fehler-isoliert pro
    # Extension (services/extensions.py); ein Boot-Fehler einer Extension darf den
    # restlichen Start nie verhindern.
    async with session_scope() as session:
        await discover_and_sync(session, settings)
        await load_enabled_from_registry(app, session, settings)

    # WP-6: `interrupted` MUSS vor jeder neuen Ausfuehrung laufen, sonst markiert ein
    # sofort danach gestarteter echter Lauf sich selbst um (docs/03 §7). Kern-Jobs
    # (kein setup(), das sie erneut anmeldet, anders als Extension-Jobs -- siehe
    # core/scheduler.py Modul-Docstring) explizit registrieren, dann erst starten.
    async with session_scope() as session:
        await mark_interrupted_on_boot(session)
    await register_core_jobs(settings.data_dir / "runs")
    get_scheduler_service().start()
    get_metrics_collector().start()

    retry_mark = None if await _mark_started_ok(settings) else asyncio.create_task(_keep_marking_started_ok(settings))

    yield

    if retry_mark is not None:
        retry_mark.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retry_mark
    await get_metrics_collector().stop()
    await get_scheduler_service().shutdown()
    # Aktionen laufen als Hintergrund-Tasks. Vor dem Schliessen der
    # Datenbank abbrechen, damit sie sich noch als abgebrochen vermerken koennen.
    await shutdown_running_actions()
    engine = get_engine()
    await engine.dispose()
    exit_if_requested(app)  # Neustart auf Wunsch (POST /system/restart): Ende mit Rueckgabewert 75


def create_app() -> FastAPI:
    install_access_log_filters()  # Download-Tickets nicht ins Zugriffsprotokoll von uvicorn
    settings = get_settings()
    # API-Doku (Swagger-Oberflaeche, ReDoc, `/openapi.json`): FastAPI liefert sie sonst ohne
    # Anmeldung aus und verraet damit jeden Endpunkt samt Feldern. Nur im Entwicklungsmodus
    # (`Settings.api_docs_public`) bleiben die drei Routen; sonst gibt es sie nicht (404).
    # `app.openapi()` laeuft unabhaengig davon (Vertragstests, `GET /api/v1/system/openapi.json`
    # fuer angemeldete Admins) -- `openapi_url=None` schaltet nur die Route ab.
    docs_public = settings.api_docs_public
    app = FastAPI(
        title="Nodvard Deck",
        # Fester Produktname im API-Dokument (technische Angabe, kein White-Label);
        # das Branding fuer Menschen kommt aus GET /api/v1/branding.
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_public else None,
        redoc_url="/redoc" if docs_public else None,
        openapi_url="/openapi.json" if docs_public else None,
    )
    app.include_router(api_v1_router)
    install_validation_error_handler(app)
    # Obergrenze fuer die Groesse jeder Anfrage, bevor irgendein Handler sie liest (api/body_limit.py).
    app.add_middleware(BodyLimitMiddleware, max_body_bytes=settings.max_body_bytes)

    # Live gefunden (Backup-Job-Editor): schlaegt eine Extension eine Aktion vor, fuer
    # die ihr selbst eine Berechtigung fehlt, kam beim Nutzer nur "HTTP 500" an. Das
    # bleibt ein Serverfehler (nicht die Schuld des Nutzers), aber mit lesbarem Grund.
    @app.exception_handler(ExtensionPermissionDenied)
    async def _extension_permission_denied(_request: Request, exc: ExtensionPermissionDenied) -> JSONResponse:
        logging.getLogger("nodvard_deck.ext").warning("Extension-Berechtigung fehlt: %s", exc)
        return JSONResponse(status_code=500, content={"detail": f"Einer Erweiterung fehlt eine Berechtigung: {exc}"})

    # Passwort-Pruefungen rechnen in wenigen Threads (`security.run_hashing`); stauen sich dahinter
    # zu viele, kommt ein ehrliches "gleich nochmal" statt einer Warteschlange ohne Ende.
    @app.exception_handler(HashingBusy)
    async def _hashing_busy(_request: Request, _exc: HashingBusy) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "5"},
            content={"detail": "Gerade sind viele Anmeldungen gleichzeitig im Gange. Bitte versuche es in ein paar Sekunden noch einmal."},
        )

    # Live gefunden: ein abgebrochener VZDump-Lauf hatte die Platte des Hosts von
    # Nodvard Deck gefuellt -- jeder Schreibzugriff (z. B. "Bestaetigen" auf der
    # Aktionen-Seite) endete in SQLites "database or disk is full", beim Nutzer kam nur
    # "HTTP 500" an und niemand dachte an die Platte. 507 Insufficient Storage mit
    # Klartext; jeder andere OperationalError bleibt ein normaler 500 (re-raise).
    @app.exception_handler(SqlOperationalError)
    async def _sql_operational_error(_request: Request, exc: SqlOperationalError) -> JSONResponse:
        if "disk is full" not in str(exc.orig if exc.orig is not None else exc):
            raise exc
        logging.getLogger("nodvard_deck.db").error("Speicher voll, Schreibzugriff gescheitert: %s", exc.orig)
        return JSONResponse(
            status_code=507,
            content={
                "detail": "Der Speicherplatz auf dem Server ist voll -- Änderungen können nicht "
                "gespeichert werden. Bitte Platz freigeben (z. B. alte Backups löschen)."
            },
        )

    # Ein Server, der nicht antwortet, ist kein Programmfehler. Faengt eine Anfrage das nirgends selbst
    # ab (z. B. eine Erweiterung, die per SSH misst), kam bisher ein 500 mit Traceback im Protokoll --
    # und `deploy_pi.sh` wertet "Traceback" in den letzten Minuten als gescheiterten Start. Jetzt:
    # 502 mit verstaendlichem Grund, im Protokoll eine Zeile.
    @app.exception_handler(SshError)
    @app.exception_handler(asyncssh.Error)
    @app.exception_handler(HostUnreachable)
    async def _server_unreachable(request: Request, exc: Exception) -> JSONResponse:
        reason = describe_connection_error(exc)
        logging.getLogger("nodvard_deck.ssh").info("server_unreachable path=%s reason=%s", request.url.path, reason)
        return JSONResponse(status_code=502, content={"detail": reason})

    # Merkposition FUER Extension-Routen: sie muessen vor dem StaticFiles-Catch-all
    # unten stehen, sonst wuerde "/" ihn zuerst treffen und jede /api/v1/ext/<id>/...
    # -Anfrage verschlucken, bevor der Router sie je sieht. `ext.runtime.mount_router()`
    # fuegt neue Routen IMMER an dieser Stelle ein, nie ans Ende von app.router.routes.
    app.state.ext_mount_index = len(app.router.routes)

    frontend_dist = settings.data_dir.parent / "frontend" / "dist"
    if frontend_dist.is_dir():
        # Produktionsauslieferung (D-03): ein Port, ein Dienst. Im Entwicklungsbetrieb
        # laeuft das Frontend stattdessen ueber `npm run dev` mit Proxy auf :8080 —
        # dann existiert frontend/dist schlicht noch nicht und wir ueberspringen das.
        app.mount("/", _SpaStaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
