"""Anwendungseinstiegspunkt, nur für einen Worker, weil Registry, Event-Bus und Scheduler Prozesszustand halten."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager
from html.parser import HTMLParser
from pathlib import Path

import asyncssh
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from nodvard_sdk.errors import HostUnreachable, SecretNotOwned
from nodvard_sdk.errors import PermissionDenied as ExtensionPermissionDenied
from nodvard_sdk.types import Event
from sqlalchemy.exc import OperationalError as SqlOperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from .api import api_v1_router
from .api.body_limit import BodyLimitMiddleware
from .api.errors import install_coded_error_handler, install_validation_error_handler
from .api.proxy_headers import ProxyHeadersMiddleware
from .api.security_headers import SecurityHeadersMiddleware
from .config import get_settings
from .core import bootstate, trusted_proxies
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
from .services import update_helper
from .services.auth import ensure_builtin_roles, prepare_setup_code, restore_code_limits
from .services.extensions import discover_and_sync, load_enabled_from_registry
from .services.jobs import mark_interrupted_on_boot
from .services.restore import record_result as record_restore_result
from .version import __version__

_event_logger = logging.getLogger("nodvard_deck.events")
_demo_logger = logging.getLogger("nodvard_deck.demo")
_boot_logger = logging.getLogger("nodvard_deck.boot")

APP_LOCK_WAIT_S = 20.0
"""So lange wartet die Anwendung auf die Sperre des Datenordners (etwa ein laufendes `boot`), bevor sie ohne Sperre
weitermacht."""
STARTED_OK_RETRY_S = 60.0
"""Scheitert das Festhalten von `started_ok`, wird es in diesem Abstand wiederholt, bis es klappt."""


SPA_CSP = (
    "style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'"
)
"""Stile und Schriften nur vom eigenen Server, auch wenn im CSS der Admins eine fremde Adresse stünde; keine
Plugins, kein fremdes `<base>`. Dazu kommt `script-src` aus `spa_script_policy()`."""

SPA_CSP_REPORT_ONLY = "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self'; frame-src blob:"
"""Bilder, Verbindungen und Worker nur vom eigenen Server, Rahmen nur für Vorschauen (`blob:`); nur gemeldet
(Report-Only). Fremde Worker sperrt schon das durchgesetzte `script-src`."""


# Auskommentierte Skripte zählen nicht: HTML-Kommentare überspringt der Parser selbst.
class _InlineScripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self._current: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and all(name != "src" for name, _ in attrs):
            self._current = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._current is not None:
            self.scripts.append("".join(self._current))
            self._current = None


def spa_script_policy(index_html: Path) -> str:
    """`script-src` der App-Seite: nur eigene Dateien und die eingebetteten Skripte der `index.html` über ihre
    Prüfsumme."""
    parser = _InlineScripts()
    try:
        parser.feed(index_html.read_text(encoding="utf-8"))
        parser.close()
    except (OSError, UnicodeDecodeError):
        return "script-src 'self'"
    hashes = {
        f"'sha256-{base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()}'"
        for script in parser.scripts
    }
    return " ".join(["script-src", "'self'", *sorted(hashes)])


# `StaticFiles(html=True)` liefert `index.html` nicht für Tiefen-Links wie `/settings`; darum fällt jede 404 hier auf
# `index.html` zurück, außer unter `/api/` (siehe `_is_api_path()`).
class _SpaStaticFiles(StaticFiles):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._policies: dict[str, tuple[int, int, str]] = {}

    def _policy_for(self, full_path: str, stat_result: os.stat_result) -> str:
        known = self._policies.get(full_path)
        if known is None or known[:2] != (stat_result.st_mtime_ns, stat_result.st_size):
            known = (stat_result.st_mtime_ns, stat_result.st_size, f"{SPA_CSP}; {spa_script_policy(Path(full_path))}")
            self._policies[full_path] = known
        return known[2]

    def file_response(
        self, full_path: str | os.PathLike[str], stat_result: os.stat_result, scope: Scope, status_code: int = 200
    ) -> Response:
        # Auch an die 304: Der Browser übernimmt deren Kopfzeilen in die gespeicherte Seite.
        response = super().file_response(full_path, stat_result, scope, status_code)
        if os.fspath(full_path).endswith(".html"):
            response.headers["Content-Security-Policy"] = self._policy_for(os.fspath(full_path), stat_result)
            response.headers["Content-Security-Policy-Report-Only"] = SPA_CSP_REPORT_ONLY
        return response

    async def get_response(self, path: str, scope: Scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Starlettes StaticFiles.get_response() wirft bei „nicht gefunden“ und gibt keine 404-Response zurück, die
            # sich einfach abfangen ließe.
            if exc.status_code != 404:
                raise
            # Ein unbekannter `/api/...`-Pfad (Tippfehler, Route einer deaktivierten Extension) landet auch hier und
            # darf nicht `index.html` mit 200 bekommen, sonst scheitern API-Aufrufer an „Unexpected token <“ statt an
            # einem ehrlichen 404.
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
"""Adressen der API-Doku von FastAPI; sind sie abgeschaltet, soll hier ein ehrlicher 404 kommen statt der App-Shell
(200, HTML)."""


def _is_api_doc_path(path: str) -> bool:
    return path.replace("\\", "/").lstrip("./").rstrip("/") in _API_DOC_PATHS


def _cache_policy_for(path: str) -> str:
    # Ohne `Cache-Control` cachen Browser heuristisch, ein veraltetes `index.html` verwiese auf gehashte Assets, die
    # es nach dem Rebuild nicht mehr gibt. `assets/` ist dauerhaft cachebar, alles andere nur mit Revalidierung.
    normalized = path.replace("\\", "/").lstrip("./")
    if normalized.startswith("assets/"):
        return "public, max-age=31536000, immutable"
    return "no-cache"


async def _log_event(event: Event) -> None:
    _event_logger.info("event name=%s correlation_id=%s", event.name, event.correlation_id)


async def _take_boot_lock(settings) -> bootstate.Lock:
    # Die laufende Anwendung hält die Sperre des Datenordners, damit `boot` nie unter ihr etwas einspielt; geht sie
    # nicht, wird ohne Sperre weitergemacht.
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
    # Bliebe `started_ok=false` stehen, hielte eine später zurückgestellte ältere Version die neue für nie gestartet.
    while True:
        await asyncio.sleep(STARTED_OK_RETRY_S)
        if await _mark_started_ok(settings, retry=True):
            _boot_logger.info("boot_mark_started_ok_retried")
            return


async def _load_trusted_proxies(settings) -> None:
    try:
        async with session_scope() as session:
            loaded = await trusted_proxies.load(session, settings)
    except Exception as exc:  # noqa: BLE001 - nie ein Grund, nicht zu starten
        trusted_proxies.reset()
        _boot_logger.warning("trusted_proxies_load_failed error=%s", type(exc).__name__)
        return
    if loaded.entries:
        _boot_logger.info("trusted_proxies source=%s entries=%d", loaded.source, len(loaded.entries))


async def _restore_code_limits() -> None:
    # Der Start darf daran nie scheitern (sonst Notseite oder Rückweg), und jedes Konto vorsorglich zu sperren,
    # sperrte den Besitzer bis zu einem Tag von allem aus, was einen Code verlangt. Die Warnung ist eine Zeile ohne
    # Traceback, weil `deploy_pi.sh` jeden Traceback als Fehler wertet.
    try:
        async with session_scope() as session:
            restored = await restore_code_limits(session)
    except Exception as exc:  # noqa: BLE001 - nie ein Grund, nicht zu starten
        first_line = next(iter(str(exc).splitlines()), "")[:200]
        _boot_logger.warning("code_limits_restore_failed error=%s: %s", type(exc).__name__, first_line)
        return
    if restored:
        _boot_logger.info("code_limits_restored entries=%d", restored)


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

    # Wem `X-Forwarded-For`/`-Proto` geglaubt wird (api/proxy_headers.py), steht vor der ersten Anfrage fest.
    await _load_trusted_proxies(settings)

    # Idempotent – sicher bei jedem Start (eingebaute Rollen müssen
    # existieren, bevor irgendjemand eine davon zugewiesen bekommen kann).
    async with session_scope() as session:
        await ensure_builtin_roles(session)

    # Noch kein Konto? Dann den Einrichtungscode ins Protokoll schreiben (bei JEDEM Start,
    # bis die Einrichtung fertig ist). Mit vorhandenem Owner passiert hier nichts.
    async with session_scope() as session:
        await prepare_setup_code(session, settings)

    # Die Grenzen für falsche Zwei-Faktor-Codes gelten über einen Neustart hinweg (vor der ersten Anfrage).
    await _restore_code_limits()

    # Ergebnis einer Wiederherstellung (von `nodvard_deck.boot` vor dem Start geschrieben) ins Audit-Protokoll
    # der jetzt gültigen Datenbank eintragen und Aufgegebenes in restore/ wegräumen.
    await record_restore_result(settings)

    # Ergebnisse des Update-Helfers (ein Update oder ein Rückweg hat gerade diesen Start ausgelöst) ins Audit-Protokoll
    # und als Meldung, genau einmal je Anforderung. Ist der Helfer noch beschäftigt (er wartet z. B. auf das erste
    # "gesund" dieses Starts), schaut ein kleiner Nachlauf alle 30 s nach, höchstens 30 Minuten.
    await update_helper.record_results(settings)
    helper_follow_up = update_helper.start_follow_up(settings)

    # `NODVARD_DECK_DEMO_MODE=1` legt dieselben Beispieldaten an wie der Knopf „Mit Beispieldaten ansehen“, idempotent
    # und nie auf einer Installation, die schon echte Server hat.
    if settings.demo_mode:
        try:
            async with session_scope() as session:
                await seed_demo_data(session)
        except DemoBlockedError:
            _demo_logger.info("demo_mode_skipped reason=hosts_exist")

    # Aktionen, die ein Neustart mitten in der Ausführung abgebrochen hat,
    # als fehlgeschlagen markieren – VOR dem Laden der Extensions, damit keine gerade
    # neu gestartete Ausführung (z. B. aus einem Extension-Hintergrund-Task) mit
    # erwischt wird.
    async with session_scope() as session:
        await fail_interrupted_actions_on_boot(session)

    bus = get_event_bus()
    bus.subscribe("*", _log_event)
    await bus.publish(Event(name="system.started"))

    # Extensions entdecken (Verzeichnis und Entry-Points), Registry-Zeilen für neue Funde anlegen, dann alles laden,
    # was von einem früheren Lauf noch `state=enabled` ist: „aktiviert“ ist eine dauerhafte Admin-Entscheidung, die
    # ein Neustart nicht vergessen darf. Fehler werden pro Extension isoliert (`services/extensions.py`), ein
    # Boot-Fehler einer Extension verhindert den restlichen Start nie.
    async with session_scope() as session:
        await discover_and_sync(session, settings)
        await load_enabled_from_registry(app, session, settings)

    # `interrupted` muss vor jeder neuen Ausführung laufen, sonst markiert ein sofort danach gestarteter echter Lauf
    # sich selbst um (docs/03 §7). Kern-Jobs haben kein `setup()`, das sie erneut anmeldet (anders als Extension-Jobs,
    # siehe `core/scheduler.py`), darum werden sie explizit registriert, dann erst gestartet.
    async with session_scope() as session:
        await mark_interrupted_on_boot(session)
    await register_core_jobs(settings.data_dir / "runs")
    get_scheduler_service().start()
    get_metrics_collector().start()

    retry_mark = None if await _mark_started_ok(settings) else asyncio.create_task(_keep_marking_started_ok(settings))

    yield

    await update_helper.stop_follow_up(helper_follow_up)
    if retry_mark is not None:
        retry_mark.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retry_mark
    await get_metrics_collector().stop()
    await get_scheduler_service().shutdown()
    # Aktionen laufen als Hintergrund-Tasks. Vor dem Schließen der
    # Datenbank abbrechen, damit sie sich noch als abgebrochen vermerken können.
    await shutdown_running_actions()
    engine = get_engine()
    await engine.dispose()
    exit_if_requested(app)  # Neustart auf Wunsch (POST /system/restart): Ende mit Rückgabewert 75


def create_app() -> FastAPI:
    install_access_log_filters()  # Download-Tickets nicht ins Zugriffsprotokoll von uvicorn
    settings = get_settings()
    # API-Doku (Swagger-Oberfläche, ReDoc, `/openapi.json`): FastAPI liefert sie sonst ohne
    # Anmeldung aus und verrät damit jeden Endpunkt samt Feldern. Nur im Entwicklungsmodus
    # (`Settings.api_docs_public`) bleiben die drei Routen; sonst gibt es sie nicht (404).
    # `app.openapi()` läuft unabhängig davon (Vertragstests, `GET /api/v1/system/openapi.json`
    # für angemeldete Admins) – `openapi_url=None` schaltet nur die Route ab.
    docs_public = settings.api_docs_public
    app = FastAPI(
        title="Nodvard Deck",
        # Fester Produktname im API-Dokument (technische Angabe, kein White-Label);
        # das Branding für Menschen kommt aus GET /api/v1/branding.
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_public else None,
        redoc_url="/redoc" if docs_public else None,
        openapi_url="/openapi.json" if docs_public else None,
    )
    app.include_router(api_v1_router)
    install_validation_error_handler(app)
    install_coded_error_handler(app)
    app.add_middleware(BodyLimitMiddleware, max_body_bytes=settings.max_body_bytes)
    # Nach der Größen-Grenze eingehängt, also weiter außen: Auch ihre 413 bekommt die Sicherheits-Kopfzeilen.
    app.add_middleware(SecurityHeadersMiddleware)
    # Echte Client-Adresse und echtes Schema hinter einem vertrauenswürdigen Proxy (api/proxy_headers.py). Zuletzt
    # hinzugefügt = ganz außen: alles danach (auch die Routen) sieht schon die echte Adresse.
    app.add_middleware(ProxyHeadersMiddleware)

    # Schlägt eine Extension eine Aktion vor, für die ihr selbst eine Berechtigung fehlt, soll beim Nutzer nicht nur
    # „HTTP 500“ ankommen: Es bleibt ein Serverfehler (nicht die Schuld des Nutzers), aber mit lesbarem Grund.
    @app.exception_handler(ExtensionPermissionDenied)
    async def _extension_permission_denied(_request: Request, exc: ExtensionPermissionDenied) -> JSONResponse:
        logging.getLogger("nodvard_deck.ext").warning("Extension-Berechtigung fehlt: %s", exc)
        return JSONResponse(status_code=500, content={"detail": f"Einer Erweiterung fehlt eine Berechtigung: {exc}"})

    # Eine Erweiterung wollte ein Geheimnis ersetzen oder löschen, das nicht ihr gehört (ext/context.py,
    # `SecretsHandle`): kein Programmfehler und keine fehlende Berechtigung, sondern ein Zustand im Tresor, den der
    # Nutzer auflösen kann. Das Geheimnis bleibt unverändert.
    @app.exception_handler(SecretNotOwned)
    async def _secret_not_owned(_request: Request, exc: SecretNotOwned) -> JSONResponse:
        logging.getLogger("nodvard_deck.ext").warning(
            "Erweiterung %s: fremdes Geheimnis nicht angefasst (%s)", exc.ext_id, exc.label,
        )
        detail = str(exc) if exc.login_secret else (
            f"{exc} Es bleibt unverändert. Wurde es von Hand angelegt, lösch es selbst unter Einstellungen → Tresor, mit "
            "deinem Passwort und, wenn Zwei-Faktor an ist, dem Code aus der App (API: DELETE /api/v1/secrets/{id} mit "
            "current_password) – oder nimm einen anderen Namen."
        )
        return JSONResponse(status_code=409, content={"detail": detail})

    # Passwort-Prüfungen rechnen in wenigen Threads (`security.run_hashing`); stauen sich dahinter
    # zu viele, kommt ein ehrliches "gleich nochmal" statt einer Warteschlange ohne Ende.
    @app.exception_handler(HashingBusy)
    async def _hashing_busy(_request: Request, _exc: HashingBusy) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "5"},
            content={"detail": "Gerade sind viele Anmeldungen gleichzeitig im Gange. Bitte versuche es in ein paar Sekunden noch einmal."},
        )

    # Eine volle Platte des Hosts (z. B. durch einen abgebrochenen VZDump-Lauf) ließ jeden Schreibzugriff mit SQLites
    # „database or disk is full“ enden und beim Nutzer nur „HTTP 500“ ankommen: 507 Insufficient Storage mit Klartext;
    # jeder andere OperationalError bleibt ein normaler 500 (re-raise).
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

    # Ein Server, der nicht antwortet, ist kein Programmfehler. Fängt eine Anfrage das nirgends selbst ab (z. B. eine
    # Erweiterung, die per SSH misst), gäbe es sonst einen 500 mit Traceback im Protokoll, und `deploy_pi.sh` wertet
    # „Traceback“ in den letzten Minuten als gescheiterten Start: Stattdessen 502 mit verständlichem Grund und einer
    # Zeile im Protokoll.
    @app.exception_handler(SshError)
    @app.exception_handler(asyncssh.Error)
    @app.exception_handler(HostUnreachable)
    async def _server_unreachable(request: Request, exc: Exception) -> JSONResponse:
        reason = describe_connection_error(exc)
        logging.getLogger("nodvard_deck.ssh").info("server_unreachable path=%s reason=%s", request.url.path, reason)
        return JSONResponse(status_code=502, content={"detail": reason})

    # Merkposition für Extension-Routen: Sie müssen vor dem StaticFiles-Catch-all unten stehen, sonst würde „/“ ihn
    # zuerst treffen und jede /api/v1/ext/<id>/...-Anfrage verschlucken, bevor der Router sie je sieht.
    # `ext.runtime.mount_router()` fügt neue Routen immer an dieser Stelle ein, nie ans Ende von app.router.routes.
    app.state.ext_mount_index = len(app.router.routes)

    frontend_dist = settings.data_dir.parent / "frontend" / "dist"
    if frontend_dist.is_dir():
        # Produktionsauslieferung: ein Port, ein Dienst. Im Entwicklungsbetrieb läuft das Frontend über `npm run dev`
        # mit Proxy auf :8080, dann existiert `frontend/dist` noch nicht und das wird übersprungen.
        app.mount("/", _SpaStaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
