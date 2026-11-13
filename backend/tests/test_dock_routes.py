from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute, APIWebSocketRoute, iter_route_contexts

from nodvard_deck.api.edition import (
    DOCK_ROUTES,
    dock_route_allowed,
    require_edition_route,
)

DOCK_MODULE = SimpleNamespace(manifest=SimpleNamespace(editions=["dock"]))
"""Fund eines Moduls, das laut Manifest (`editions`) fest zu Nodvard Dock gehört."""

DECK_ROUTES = {
    ("GET", "/api/v1/apps"),
    ("GET", "/api/v1/apps/suggestions"),
    ("POST", "/api/v1/apps"),
    ("PUT", "/api/v1/apps/order"),
    ("PATCH", "/api/v1/apps/{app_id}"),
    ("DELETE", "/api/v1/apps/{app_id}"),
    ("GET", "/api/v1/widgets"),
    ("GET", "/api/v1/console/hosts"),
    ("POST", "/api/v1/console/sessions"),
    ("WEBSOCKET", "/api/v1/ws/console/{session_id}"),
    ("GET", "/api/v1/dashboard/layouts"),
    ("GET", "/api/v1/dashboard/layouts/{layout_id}"),
    ("PUT", "/api/v1/dashboard/layouts/{layout_id}"),
    ("GET", "/api/v1/demo"),
    ("POST", "/api/v1/demo/seed"),
    ("DELETE", "/api/v1/demo"),
    ("POST", "/api/v1/extensions/{ext_id}/takeover"),
    ("POST", "/api/v1/extensions/{ext_id}/disable"),
    ("GET", "/api/v1/files/sources"),
    ("GET", "/api/v1/files/places"),
    ("GET", "/api/v1/files/{source_id}/info"),
    ("GET", "/api/v1/files/{source_id}/list"),
    ("GET", "/api/v1/files/{source_id}/stat"),
    ("GET", "/api/v1/files/{source_id}/download"),
    ("POST", "/api/v1/files/{source_id}/upload"),
    ("POST", "/api/v1/files/{source_id}/mkdir"),
    ("POST", "/api/v1/files/{source_id}/rename"),
    ("POST", "/api/v1/files/{source_id}/remove"),
    ("GET", "/api/v1/files/search"),
    ("POST", "/api/v1/files/transfer"),
    ("GET", "/api/v1/hosts/{host_id}/status"),
    ("GET", "/api/v1/hosts/{host_id}/metrics"),
    ("GET", "/api/v1/hosts/metrics/latest"),
    ("GET", "/api/v1/hosts/{host_id}/metrics/history"),
    ("GET", "/api/v1/hosts/{host_id}/actions"),
    ("GET", "/api/v1/hosts/{host_id}/tools"),
    ("GET", "/api/v1/host-groups"),
    ("POST", "/api/v1/host-groups"),
    ("PATCH", "/api/v1/host-groups/{group_id}"),
    ("DELETE", "/api/v1/host-groups/{group_id}"),
    ("POST", "/api/v1/host-groups/{group_id}/members/{host_id}"),
    ("DELETE", "/api/v1/host-groups/{group_id}/members/{host_id}"),
    ("POST", "/api/v1/hosts/{host_id}/helper-hint/dismiss"),
    ("GET", "/api/v1/jobs"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("PATCH", "/api/v1/jobs/{job_id}"),
    ("DELETE", "/api/v1/jobs/{job_id}"),
    ("POST", "/api/v1/jobs/{job_id}/run"),
    ("GET", "/api/v1/jobs/{job_id}/runs"),
    ("GET", "/api/v1/runs/{run_id}"),
    ("GET", "/api/v1/runs/{run_id}/output"),
    ("POST", "/api/v1/runs/{run_id}/cancel"),
    ("GET", "/api/v1/overview"),
    ("GET", "/api/v1/system/openapi.json"),
    ("GET", "/api/v1/system/updates/helper"),
    ("POST", "/api/v1/system/updates/apply"),
    ("POST", "/api/v1/system/updates/rollback"),
    ("GET", "/api/v1/system/backups/download/{ticket}/status"),
    ("GET", "/api/v1/terminal/hosts"),
}


def _api_routes(routes):
    """(Methoden, voller Pfad, Abhängigkeiten) je Route; FastAPI schreibt eingebundene Routen nicht mehr um."""
    for context in iter_route_contexts(routes):
        effective = context._route_context
        if isinstance(context.original_route, APIWebSocketRoute):
            route = effective.starlette_route if effective is not None else context.original_route
            yield {"WEBSOCKET"}, route.path, [dependency.call for dependency in route.dependant.dependencies]
        elif isinstance(context.original_route, APIRoute):
            dependencies = (
                [dependency.dependency for dependency in effective.dependencies] if effective is not None
                else [dependency.call for dependency in context.original_route.dependant.dependencies]
            )
            yield set(context.methods), context.path, dependencies
        else:
            yield set(), context.path or "", []


def _scope(method, path, **extra):
    """Scope wie nach dem Routing: Bei HTTP steht der volle Pfad im Routenkontext, nicht an der Route selbst."""
    if method == "WEBSOCKET":
        return {"type": "websocket", "path": path, "route": SimpleNamespace(path=path), **extra}
    return {
        "type": "http", "method": method, "path": path, "route": SimpleNamespace(path=path.rsplit("/", 1)[-1]),
        "fastapi": {"effective_route_context": SimpleNamespace(path=path)}, **extra,
    }


@pytest.fixture
def core_app(test_settings, monkeypatch):
    from nodvard_deck import config
    from nodvard_deck.main import create_app

    monkeypatch.setattr(config, "_settings", test_settings)
    return create_app()


def test_every_core_route_has_an_edition_entry(core_app):
    registered = {
        (method, path)
        for methods, path, _dependencies in _api_routes(core_app.routes) if path.startswith("/api/")
        for method in methods
    }
    allowed = {(method, path) for path, methods in DOCK_ROUTES.items() for method in methods}
    assert not allowed & DECK_ROUTES
    assert registered == allowed | DECK_ROUTES
    for method, path in registered:
        assert dock_route_allowed(path, method) == ((method, path) in allowed)


@pytest.mark.parametrize("method,path", sorted(DECK_ROUTES))
async def test_dock_rejects_deck_routes(core_app, test_settings, monkeypatch, method, path):
    from fastapi import HTTPException
    from starlette.requests import HTTPConnection

    monkeypatch.setattr(test_settings, "edition", "dock")
    assert any(path == known and method in methods for methods, known, _deps in _api_routes(core_app.routes))
    request = HTTPConnection(_scope(method, path, path_params={}))
    with pytest.raises(HTTPException) as error:
        await require_edition_route(request, test_settings)
    assert error.value.status_code == 404


@pytest.mark.parametrize("path,method", [
    ("/future", "GET"),
    ("/auth/future", "POST"),
    ("/hosts/{host_id}/future", "GET"),
    ("/extensions/{ext_id}/future", "POST"),
    ("/settings", "POST"),
])
async def test_new_routes_are_closed_in_dock(client, test_settings, monkeypatch, path, method):
    from nodvard_deck.main import app

    extra = APIRouter(prefix="/api/v1", dependencies=[Depends(require_edition_route)])

    async def endpoint():
        return {"ok": True}

    extra.add_api_route(path, endpoint, methods=[method])
    previous = list(app.router.routes)
    try:
        app.include_router(extra)
        url = "/api/v1" + path.replace("{host_id}", "server").replace("{ext_id}", "docker")
        monkeypatch.setattr(test_settings, "edition", "dock")
        response = await client.request(method, url)
        assert response.status_code == 404, response.text
        monkeypatch.setattr(test_settings, "edition", "deck")
        assert (await client.request(method, url)).status_code == 200
    finally:
        app.router.routes[:] = previous


def test_core_routes_include_the_edition_guard(core_app):
    for methods, path, dependencies in _api_routes(core_app.routes):
        if not path.startswith("/api/"):
            continue
        assert methods, path
        assert require_edition_route in dependencies, path


async def test_new_websocket_routes_are_closed_in_dock(test_settings, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import HTTPConnection

    extra = APIRouter(prefix="/api/v1", dependencies=[Depends(require_edition_route)])

    @extra.websocket("/ws/future")
    async def endpoint():
        pass

    request = HTTPConnection({
        "type": "websocket", "path": "/api/v1/ws/future", "route": extra.routes[0], "path_params": {},
    })
    monkeypatch.setattr(test_settings, "edition", "dock")
    with pytest.raises(HTTPException) as error:
        await require_edition_route(request, test_settings)
    assert error.value.status_code == 404
    monkeypatch.setattr(test_settings, "edition", "deck")
    await require_edition_route(request, test_settings)


@pytest.mark.parametrize("path", ["/dashboard/layouts", "/widgets", "/demo", "/jobs", "/system/openapi.json"])
async def test_dock_returns_404_before_auth(client, test_settings, monkeypatch, path):
    monkeypatch.setattr(test_settings, "edition", "dock")
    response = await client.get("/api/v1" + path)
    assert response.status_code == 404
    assert response.json()["detail"] == "Diese Funktion ist in dieser Ausgabe nicht verfügbar."


async def test_dock_head_matches_deck_method_check(client, test_settings, monkeypatch):
    # Die API kennt kein HEAD (Methodenprüfung in api/methods.py); die Ausgabe Dock ändert daran nichts.
    monkeypatch.setattr(test_settings, "edition", "dock")
    for path in ("/api/v1/health", "/api/v1/jobs"):
        response = await client.head(path)
        assert response.status_code == 405
        assert "GET" in response.headers["allow"]


@pytest.mark.parametrize("path,methods", sorted(DOCK_ROUTES.items()))
async def test_dock_allows_frontend_routes(core_app, test_settings, monkeypatch, path, methods):
    from starlette.requests import HTTPConnection

    from nodvard_deck.ext.runtime import get_extension_runtime

    monkeypatch.setattr(test_settings, "edition", "dock")
    monkeypatch.setitem(get_extension_runtime().discovered, "docker", DOCK_MODULE)
    known = {(method, route_path) for found, route_path, _deps in _api_routes(core_app.routes) for method in found}
    for method in methods:
        assert (method, path) in known
        request = HTTPConnection(_scope(method, path, path_params={"ext_id": "docker"} if "{ext_id}" in path else {}))
        await require_edition_route(request, test_settings)


@pytest.mark.parametrize("ext_id,expected", [("docker", 200), ("service-matrix", 200), ("documents", 404)])
async def test_dock_only_serves_docker_extension_routes(client, test_settings, monkeypatch, ext_id, expected):
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.main import app

    monkeypatch.setattr(test_settings, "edition", "dock")
    runtime = get_extension_runtime()
    monkeypatch.setitem(runtime.legacy_owner, "service-matrix", "docker")
    monkeypatch.setitem(runtime.discovered, "docker", DOCK_MODULE)
    monkeypatch.setitem(runtime.discovered, "documents", SimpleNamespace(manifest=SimpleNamespace(editions=[])))
    extra = APIRouter()

    @extra.get("/probe")
    async def endpoint():
        return {"ok": True}

    routes = runtime.mount_router(app, ext_id, extra)
    try:
        response = await client.get(f"/api/v1/ext/{ext_id}/probe")
        assert response.status_code == expected, response.text
        monkeypatch.setattr(test_settings, "edition", "deck")
        assert (await client.get(f"/api/v1/ext/{ext_id}/probe")).status_code == 200
    finally:
        runtime.unmount_router(app, routes)


async def test_dock_refuses_other_discovered_extensions(client, test_settings, monkeypatch):
    from nodvard_deck.ext.runtime import get_extension_runtime

    monkeypatch.setattr(test_settings, "edition", "dock")
    monkeypatch.setitem(get_extension_runtime().discovered, "documents", object())
    response = await client.get("/api/v1/extensions/documents/settings")
    assert response.status_code == 404
    assert response.json()["detail"] == "Dieses Modul ist in dieser Ausgabe nicht verfügbar."


def test_dock_openapi_filters_paths_and_methods(test_settings, monkeypatch):
    from nodvard_deck import config
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.main import create_app

    monkeypatch.setattr(test_settings, "edition", "dock")
    monkeypatch.setattr(config, "_settings", test_settings)
    app = create_app()

    @app.post("/api/v1/settings")
    @app.get("/api/v1/auth/future")
    async def endpoint():
        return {}

    extra = APIRouter()
    extra.add_api_route("/probe", endpoint)
    runtime = get_extension_runtime()
    monkeypatch.setitem(runtime.discovered, "docker", DOCK_MODULE)
    runtime.mount_router(app, "docker", extra)
    runtime.mount_router(app, "documents", extra)
    schema = app.openapi()["paths"]
    assert set(schema["/api/v1/settings"]) == {"get"}
    assert "/api/v1/auth/future" not in schema
    assert "/api/v1/ext/docker/probe" in schema
    assert "/api/v1/ext/documents/probe" not in schema
    for method, path in DECK_ROUTES:
        assert method.lower() not in schema.get(path, {})


def test_the_docker_module_declares_the_dock_edition():
    """Die Positivliste der Dock-Ausgabe steht im Manifest des Moduls, nicht im Kern."""
    import tomllib
    from pathlib import Path

    from nodvard_sdk import ExtensionManifest

    path = Path(__file__).resolve().parents[2] / "extensions" / "docker" / "extension.toml"
    manifest = ExtensionManifest.model_validate(tomllib.loads(path.read_text(encoding="utf-8"))["extension"])
    assert manifest.editions == ["dock"]


async def test_fresh_dock_can_switch_on_the_docker_module(client, db_session, test_settings, monkeypatch):
    """Der Einrichtungsassistent und der Rauchtest schalten `docker` per POST ein; ein Nicht-Dock-Modul bleibt zu."""
    from pathlib import Path

    from nodvard_deck.services import extensions as extensions_service

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "extensions" / "docker" / "src"))
    test_settings.extensions_dir = Path(__file__).resolve().parents[2] / "extensions"
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={
        "username": "owner1", "password": "correct-horse-battery", "setup_code": test_settings.setup_code,
    })
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    monkeypatch.setattr(test_settings, "edition", "dock")

    enabled = await client.post("/api/v1/extensions/docker/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["state"] == "enabled"
    listed = await client.get("/api/v1/extensions", headers=headers)
    assert {row["id"]: row["state"] for row in listed.json()}["docker"] == "enabled"

    other = await client.post("/api/v1/extensions/documents/enable", headers=headers)
    assert other.status_code == 404
    assert other.json()["detail"] == "Dieses Modul ist in dieser Ausgabe nicht verfügbar."
    disable = await client.post("/api/v1/extensions/docker/disable", headers=headers)
    assert disable.status_code == 404


async def test_dock_service_refuses_module_without_dock_mark(db_session, test_settings, monkeypatch):
    from pathlib import Path

    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.services import extensions as extensions_service

    test_settings.extensions_dir = Path(__file__).resolve().parents[2] / "extensions"
    await extensions_service.discover_and_sync(db_session, test_settings)
    runtime = get_extension_runtime()
    assert "documents" in runtime.discovered
    monkeypatch.setattr(test_settings, "edition", "dock")
    with pytest.raises(extensions_service.ExtensionLoadError, match="nicht verfügbar"):
        await extensions_service.enable_extension(None, db_session, test_settings, "documents")
    assert "documents" not in runtime.loaded
    await extensions_service.enable_extension(None, db_session, test_settings, "documents", at_boot=True)
    assert "documents" not in runtime.loaded


async def test_dock_auto_enable_leaves_module_without_dock_mark_alone(db_session, test_settings, monkeypatch):
    from pathlib import Path

    from nodvard_deck.services import extensions as extensions_service

    test_settings.extensions_dir = Path(__file__).resolve().parents[2] / "extensions"
    await extensions_service.discover_and_sync(db_session, test_settings)
    monkeypatch.setattr(test_settings, "edition", "dock")
    enabled = await extensions_service.auto_enable_for(None, db_session, test_settings, "host_credential")
    assert enabled == []
    record = await extensions_service.get_record(db_session, "terminal")
    assert record.state == "disabled" and record.last_error is None
