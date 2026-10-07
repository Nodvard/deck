"""Sicherheitsluecke (beim Bau der Container-Verwaltung live gefunden): mehrere
Extension-Router hingen ohne `permission=` -- laut `ApiHandle.include_router()`-
Docstring heisst das "oeffentlich". Live auf der lokalen Instanz nachgewiesen:
`GET /ext/gameserver/servers` lieferte ohne Login 200 samt Join-Code, und ueber
denselben Router liessen sich Start/Stop VORSCHLAGEN (bei `autonomy.mode=full` fuehrt
das Gate den risikoarmen Start sofort aus). Dieser Test haelt fest: ohne Login kommt
bei keiner dieser Routen mehr etwas an -- auch kein Vorschlag im Aktions-Journal.
"""

from __future__ import annotations

import re
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from nodvard_deck.models import Action
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


ROUTES = [
    ("GET", "/api/v1/ext/gameserver/servers"),
    ("GET", "/api/v1/ext/gameserver/widgets/servers"),
    ("POST", "/api/v1/ext/gameserver/servers/some-host/start"),
    ("POST", "/api/v1/ext/gameserver/servers/some-host/stop"),
    ("GET", "/api/v1/ext/backups/jobs"),
    ("GET", "/api/v1/ext/backups/jobs/x/history"),
    ("GET", "/api/v1/ext/backups/widgets/summary"),
    ("POST", "/api/v1/ext/backups/jobs/x/retry"),
    ("GET", "/api/v1/ext/proxmox/widgets/overview"),
    ("GET", "/api/v1/ext/proxmox/widgets/node-load"),
    ("GET", "/api/v1/ext/service-matrix/widgets/matrix"),
    ("GET", "/api/v1/ext/service-matrix/containers/h/web/logs"),
    ("GET", "/api/v1/ext/service-matrix/image-updates"),
    ("POST", "/api/v1/ext/service-matrix/image-updates/check"),
    ("GET", "/api/v1/ext/documents/widgets/untagged"),
    ("GET", "/api/v1/ext/inventory/widgets/warranty"),
]


@pytest.mark.asyncio
async def test_extension_routes_reject_anonymous_requests(client, db_session, test_settings):
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for ext_id in ("proxmox", "gameserver", "backups", "service-matrix", "documents", "inventory"):
        enabled = await client.post(f"/api/v1/extensions/{ext_id}/enable", headers=headers)
        assert enabled.status_code == 200, (ext_id, enabled.text)

    results = {f"{m} {p}": (await client.request(m, p)).status_code for m, p in ROUTES}
    assert results == {key: 401 for key in results}

    # Und nichts ist ueber die Hintertuer im Aktions-Journal gelandet.
    assert (await db_session.execute(select(Action))).scalars().all() == []


# Routen, die eine mitgelieferte Erweiterung bewusst OHNE Anmeldung anbietet
# (`include_router(..., public=True)`), als (Erweiterung, Methode, Pfad ab /api/v1/ext/<id>).
# Heute gibt es keine; wer eine ergaenzt, muss sie hier mit Begruendung eintragen.
DECLARED_PUBLIC_ROUTES: set[tuple[str, str, str]] = set()
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


@pytest.mark.asyncio
async def test_no_bundled_extension_route_is_reachable_without_login(client, db_session, test_settings):
    """Schaltet JEDE mitgelieferte Erweiterung ein und ruft jede ihrer HTTP-Routen ohne
    Token auf: ueberall muss 401 kommen, ausser bei den ausdruecklich als oeffentlich
    erklaerten (`DECLARED_PUBLIC_ROUTES`). Faengt vergessene `permission=` bei neuen und
    bestehenden Erweiterungen ab."""
    from nodvard_deck.main import app

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    discovered = await extensions_service.discover_and_sync(db_session, test_settings)
    assert len(discovered) >= 10, "es muessen alle mitgelieferten Erweiterungen entdeckt werden"
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    for ext_id in sorted(discovered):
        enabled = await client.post(f"/api/v1/extensions/{ext_id}/enable", headers=headers)
        assert enabled.status_code == 200, (ext_id, enabled.text)

    # OpenAPI statt der internen Routenliste: das ist die oeffentliche Sicht auf alle
    # eingehaengten Routen. Der Zwischenspeicher wird davor und danach geleert, damit
    # weder alte Routen auftauchen noch spaetere Tests (Vertrags-Schnappschuss) den
    # Stand mit eingeschalteten Erweiterungen sehen.
    app.openapi_schema = None
    try:
        paths = app.openapi()["paths"]
    finally:
        app.openapi_schema = None

    checked: list[tuple[str, str, str]] = []
    for path, operations in paths.items():
        if not path.startswith("/api/v1/ext/"):
            continue
        ext_id, _, rest = path.removeprefix("/api/v1/ext/").partition("/")
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in sorted(m.upper() for m in operations if m.upper() in _HTTP_METHODS):
            checked.append((ext_id, method, "/" + rest))
            if (ext_id, method, "/" + rest) in DECLARED_PUBLIC_ROUTES:
                continue
            response = await client.request(method, url)
            assert response.status_code == 401, (method, path, response.status_code)

    assert {ext for ext, _, _ in checked} >= {"hello-world", "proxmox", "backups", "shield", "ntfy"}
    assert not DECLARED_PUBLIC_ROUTES - set(checked), "eine als oeffentlich erklaerte Route gibt es nicht mehr"

    # OpenAPI zeigt nur APIRoutes mit include_in_schema=True. WebSockets, add_route(),
    # mount() und versteckte Routen fehlen dort und waeren oben nie aufgerufen worden.
    # Deshalb die echten Routenobjekte durchgehen: es darf keine davon geben.
    from nodvard_deck.ext.runtime import get_extension_runtime

    invisible: list[tuple[str, str]] = []
    seen = 0
    for ext_id, loaded in get_extension_runtime().loaded.items():
        for route in _all_routes(loaded.router):
            seen += 1
            if not isinstance(route, APIRoute) or not route.include_in_schema:
                invisible.append((ext_id, f"{type(route).__name__} {getattr(route, 'path', '?')}"))
    # Eine umbenannte Erweiterung (`legacy_ids`) ist zusaetzlich unter ihren alten Adressen eingehaengt: dieselben Routen
    # als veraltete Pfade im Schema. Sie stehen oben in `checked` (und antworten dort mit 401), die Routenobjekte der
    # Erweiterung gibt es aber nur einmal.
    old_addresses = get_extension_runtime().legacy_owner
    assert {ext for ext, _, _ in checked} & set(old_addresses), "die alten Adressen von Shield sind mit geprueft"
    assert seen >= len([entry for entry in checked if entry[0] not in old_addresses])
    assert invisible == []


def _all_routes(router):
    """Alle Routenobjekte eines Routers samt eingebundener Unter-Router."""
    for route in getattr(router, "routes", []) if router is not None else []:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _all_routes(nested)
        else:
            yield route


def _write_probe_extension(root: Path, *, permissions: str = '["api.public"]') -> None:
    pkg = root / "probe" / "src" / "nodvard_deck_ext_probe"
    pkg.mkdir(parents=True)
    (root / "probe" / "extension.toml").write_text(
        textwrap.dedent(
            f"""
            [extension]
            id = "probe"
            name = "Probe"
            version = "0.1.0"
            api_version = "0.1.0"
            entrypoint = "nodvard_deck_ext_probe:Extension"
            permissions = {permissions}
            """
        ),
        encoding="utf-8",
    )
    (pkg / "__init__.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter
            from nodvard_sdk import NodvardExtension


            class Extension(NodvardExtension):
                async def setup(self, ctx):
                    default_router = APIRouter()
                    default_router.add_api_route("/default", lambda: {"ok": "default"})
                    public_router = APIRouter()
                    public_router.add_api_route("/open", lambda: {"ok": "public"})
                    guarded_router = APIRouter()
                    guarded_router.add_api_route("/guarded", lambda: {"ok": "guarded"})
                    ctx.api.include_router(default_router)
                    ctx.api.include_router(public_router, prefix="/hooks", public=True)
                    ctx.api.include_router(guarded_router, permission="extensions.manage")
            """
        ),
        encoding="utf-8",
    )


async def _enable_probe(client, db_session, test_settings, tmp_path, **kwargs):
    root = tmp_path / "ext"
    _write_probe_extension(root, **kwargs)
    test_settings.extensions_dir = root
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    enabled = await client.post("/api/v1/extensions/probe/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "enabled", enabled.text
    return headers


@pytest.mark.asyncio
async def test_route_without_permission_needs_login_by_default(client, db_session, test_settings, tmp_path):
    headers = await _enable_probe(client, db_session, test_settings, tmp_path)

    assert (await client.get("/api/v1/ext/probe/default")).status_code == 401
    ok = await client.get("/api/v1/ext/probe/default", headers=headers)
    assert ok.status_code == 200
    assert ok.json() == {"ok": "default"}


@pytest.mark.asyncio
async def test_public_route_is_reachable_without_login(client, db_session, test_settings, tmp_path):
    await _enable_probe(client, db_session, test_settings, tmp_path)

    r = await client.get("/api/v1/ext/probe/hooks/open")
    assert r.status_code == 200
    assert r.json() == {"ok": "public"}
    # Die anderen Routen derselben Erweiterung bleiben geschuetzt.
    assert (await client.get("/api/v1/ext/probe/default")).status_code == 401
    assert (await client.get("/api/v1/ext/probe/guarded")).status_code == 401


@pytest.mark.asyncio
async def test_permission_route_still_checks_the_permission(client, db_session, test_settings, tmp_path):
    headers = await _enable_probe(client, db_session, test_settings, tmp_path)

    assert (await client.get("/api/v1/ext/probe/guarded")).status_code == 401
    assert (await client.get("/api/v1/ext/probe/guarded", headers=headers)).status_code == 200


def test_public_together_with_permission_is_rejected(tmp_path):
    from fastapi import APIRouter
    from nodvard_sdk.errors import InvalidRegistration
    from test_ext_context import _build

    _, _, ctx = _build(tmp_path)
    with pytest.raises(InvalidRegistration):
        ctx.api.include_router(APIRouter(), permission="hosts.read", public=True)


def test_public_needs_the_api_public_permission(tmp_path):
    from fastapi import APIRouter
    from nodvard_sdk.errors import PermissionDenied
    from test_ext_context import _build

    _, _, ctx = _build(tmp_path)
    with pytest.raises(PermissionDenied) as denied:
        ctx.api.include_router(APIRouter(), prefix="/hooks", public=True)
    assert denied.value.permission == "api.public"
    assert ctx.api.public_prefixes == []

    _, _, allowed_ctx = _build(tmp_path, permissions=["api.public"])
    allowed_ctx.api.include_router(APIRouter(), prefix="/hooks", public=True)
    assert allowed_ctx.api.public_prefixes == ["/api/v1/ext/test-ext/hooks"]


@pytest.mark.asyncio
async def test_extension_without_api_public_cannot_open_routes(client, db_session, test_settings, tmp_path):
    """Ohne die Berechtigung im Manifest scheitert schon setup(): die Erweiterung steht auf
    Fehler, keine ihrer Routen ist eingehaengt (auch nicht die geschuetzten)."""
    root = tmp_path / "ext"
    _write_probe_extension(root, permissions="[]")
    test_settings.extensions_dir = root
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    enabled = await client.post("/api/v1/extensions/probe/enable", headers=headers)

    assert enabled.status_code == 200, enabled.text
    body = enabled.json()
    assert body["state"] == "error"
    assert "api.public" in body["last_error"]
    assert (await client.get("/api/v1/ext/probe/hooks/open")).status_code == 404
    assert (await client.get("/api/v1/ext/probe/default", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_enabling_an_extension_with_public_routes_is_audited(client, db_session, test_settings, tmp_path):
    headers = await _enable_probe(client, db_session, test_settings, tmp_path)

    audit = await client.get("/api/v1/audit", params={"action": "extension.enabled"}, headers=headers)

    assert audit.status_code == 200, audit.text
    entries = [e for e in audit.json() if e["target_id"] == "probe"]
    assert len(entries) == 1
    assert entries[0]["detail"]["public_routes"] == ["/api/v1/ext/probe/hooks"]
    # Die Berechtigung steht auch in der Erweiterungsliste.
    listed = await client.get("/api/v1/extensions/probe", headers=headers)
    assert "api.public" in listed.json()["granted_permissions"]


def _write_raw_routes_extension(root: Path, *, public: bool, late: bool = False) -> None:
    """Erweiterung mit Routen, an denen eine Router-Abhaengigkeit nicht greift:
    WebSocket, Starlette-Route (add_route) und mount(), in einem Unter-Router.
    `late`: die Starlette-Route kommt erst NACH include_router() dazu."""
    pkg = root / "raw" / "src" / "nodvard_deck_ext_raw"
    pkg.mkdir(parents=True)
    (root / "raw" / "extension.toml").write_text(
        textwrap.dedent(
            f"""
            [extension]
            id = "raw"
            name = "Raw"
            version = "0.1.0"
            api_version = "0.1.0"
            entrypoint = "nodvard_deck_ext_raw:Extension"
            permissions = {'["api.public"]' if public else '[]'}
            """
        ),
        encoding="utf-8",
    )
    (pkg / "__init__.py").write_text(
        textwrap.dedent(
            f"""
            from fastapi import APIRouter, WebSocket
            from starlette.responses import JSONResponse
            from nodvard_sdk import NodvardExtension

            HITS = []


            async def raw(request):
                HITS.append("raw")
                return JSONResponse({{"ok": "raw"}})


            async def mounted(scope, receive, send):
                HITS.append("mount")
                await JSONResponse({{"ok": "mount"}})(scope, receive, send)


            class Extension(NodvardExtension):
                async def setup(self, ctx):
                    router = APIRouter()
                    inner = APIRouter()
                    if not {late}:
                        @inner.websocket("/ws")
                        async def ws(websocket: WebSocket):
                            HITS.append("ws")
                            await websocket.accept()
                            await websocket.close()

                        inner.add_route("/raw", raw, methods=["POST"])
                        inner.mount("/files", mounted)
                    router.include_router(inner)
                    ctx.api.include_router(router, public={public})
                    if {late}:
                        router.add_route("/raw", raw, methods=["POST"])
            """
        ),
        encoding="utf-8",
    )


async def _enable_raw(client, db_session, test_settings, tmp_path, **kwargs):
    root = tmp_path / "ext"
    _write_raw_routes_extension(root, **kwargs)
    test_settings.extensions_dir = root
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    enabled = await client.post("/api/v1/extensions/raw/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    return enabled.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("late", [False, True])
async def test_routes_that_cannot_check_login_are_refused(client, db_session, test_settings, tmp_path, late):
    """add_route()/mount() uebernehmen keine Router-Abhaengigkeit: ohne diese Pruefung
    waeren sie trotz des sicheren Standards ohne Anmeldung erreichbar. Auch eine Route,
    die erst nach include_router() dazukommt, faellt auf."""
    body = await _enable_raw(client, db_session, test_settings, tmp_path, public=False, late=late)

    assert body["state"] == "error"
    assert "Route /raw" in body["last_error"]
    if not late:
        assert "WebSocketRoute /ws" in body["last_error"] and "Mount /files" in body["last_error"]
    assert (await client.post("/api/v1/ext/raw/raw")).status_code == 404
    assert (await client.get("/api/v1/ext/raw/files/x")).status_code == 404
    assert sys.modules["nodvard_deck_ext_raw"].HITS == []


@pytest.mark.asyncio
async def test_raw_routes_are_allowed_with_public(client, db_session, test_settings, tmp_path):
    body = await _enable_raw(client, db_session, test_settings, tmp_path, public=True)

    assert body["state"] == "enabled", body
    assert (await client.post("/api/v1/ext/raw/raw")).status_code == 200
    assert (await client.get("/api/v1/ext/raw/files/x")).status_code == 200


def _write_on_start_routes_extension(root: Path, *, crash: bool) -> None:
    """Erweiterung, die erst in on_start() (nach dem Einhaengen) eine Route an ihren
    Router haengt, die keine Anmeldung pruefen kann. `crash`: on_start() stuerzt danach ab."""
    pkg = root / "starter" / "src" / "nodvard_deck_ext_starter"
    pkg.mkdir(parents=True)
    (root / "starter" / "extension.toml").write_text(
        textwrap.dedent(
            """
            [extension]
            id = "starter"
            name = "Starter"
            version = "0.1.0"
            api_version = "0.1.0"
            entrypoint = "nodvard_deck_ext_starter:Extension"
            permissions = []
            """
        ),
        encoding="utf-8",
    )
    (pkg / "__init__.py").write_text(
        textwrap.dedent(
            f"""
            from fastapi import APIRouter
            from starlette.responses import JSONResponse
            from nodvard_sdk import NodvardExtension

            EVENTS = []


            async def raw(request):
                EVENTS.append("raw")
                return JSONResponse({{"ok": "raw"}})


            class Extension(NodvardExtension):
                async def setup(self, ctx):
                    self.router = APIRouter()
                    self.router.add_api_route("/fine", lambda: {{"ok": "fine"}})
                    ctx.api.include_router(self.router)

                async def on_start(self, ctx):
                    self.router.add_route("/late", raw, methods=["POST"])
                    EVENTS.append("start")
                    if {crash}:
                        raise RuntimeError("on_start kaputt")

                async def on_stop(self, ctx):
                    EVENTS.append("stop")
            """
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("crash", [False, True])
async def test_route_added_in_on_start_without_login_check_is_refused(client, db_session, test_settings, tmp_path, crash):
    """check_routes() lief bisher nur nach setup(): eine Route, die on_start() an den schon
    eingehaengten Router haengt, ging ungeprueft live. Jetzt wird die Erweiterung
    zurueckgebaut und steht auf Fehler (auch wenn on_start() danach abstuerzt)."""
    root = tmp_path / "ext"
    _write_on_start_routes_extension(root, crash=crash)
    test_settings.extensions_dir = root
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    enabled = await client.post("/api/v1/extensions/starter/enable", headers=headers)

    assert enabled.status_code == 200, enabled.text
    body = enabled.json()
    assert body["state"] == "error"
    assert "Route /late" in body["last_error"]
    # Nichts von der Erweiterung bleibt eingehaengt, die ungeschuetzte Route ist nie angekommen ...
    assert (await client.post("/api/v1/ext/starter/late")).status_code == 404
    assert (await client.get("/api/v1/ext/starter/fine", headers=headers)).status_code == 404
    # ... und die Erweiterung wurde ordentlich beendet.
    events = sys.modules["nodvard_deck_ext_starter"].EVENTS
    assert events == ["start", "stop"]
    # Erneutes Ausschalten ist harmlos (die Erweiterung ist nicht mehr geladen).
    off = await client.post("/api/v1/extensions/starter/disable", headers=headers)
    assert off.status_code == 200, off.text
