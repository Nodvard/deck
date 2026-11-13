"""Schutz des oeffentlichen API-Vertrags (Nodvard Link, docs/04-API.md "Kompatibilitaet").

Vergleicht die aktuelle `app.openapi()` -- inklusive aller mitgelieferten Extensions --
mit dem eingecheckten Schnappschuss `api_v1.json`:

1. Kein Bruch (entfernt/umbenannt, neue Pflichtfelder, Typwechsel), ausser er steht mit
   Begruendung in `breaking_exceptions.toml`.
2. Der Schnappschuss ist aktuell (Neues ist erlaubt, muss aber eingecheckt werden).
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import tomllib
from pathlib import Path
from textwrap import dedent
from types import ModuleType
from typing import get_args

import contract_lib as cl
import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Response, status
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import BaseModel, ValidationError

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[3] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


@pytest.fixture
def current_routes():
    return []


@pytest.fixture
async def current(client, db_session, test_settings, current_routes):
    from nodvard_deck.main import app

    doc = await cl.collect_openapi(app, db_session, test_settings, REPO_EXTENSIONS_DIR, routes=current_routes)
    return cl.build_api_snapshot(doc)


def _saved() -> dict:
    return json.loads(cl.API_SNAPSHOT.read_text(encoding="utf-8"))


def test_ausnahmeliste_ist_gueltig():
    cl.load_exceptions()


async def test_api_bricht_die_abwaertskompatibilitaet_nicht(current):
    breaks = cl.uncovered(cl.find_api_breaks(_saved(), current), cl.load_exceptions())
    assert not breaks, "\n\n" + cl.format_breaks(breaks)


async def test_api_schnappschuss_ist_aktuell(current):
    saved = _saved()
    assert cl.dump_json(current) == cl.dump_json(saved), "\n\n" + cl.stale_message("API", saved, current, "endpoints")


async def test_extension_routen_sind_im_schnappschuss(current, current_routes):
    """Der Vertrag enthält Kernrouten und Erweiterungsrouten mit ihren Präfixen."""
    assert any("/api/v1/ext/" in key for key in current["endpoints"])
    captured = {
        f"{method} {route.path_format}" for route in current_routes if route.include_in_schema
        for method in route.methods & {method.upper() for method in cl.HTTP_METHODS}
    }
    assert {
        "POST /api/v1/auth/login", "GET /api/v1/health",
        "GET /api/v1/ext/inventory/items", "GET /api/v1/ext/shield/defender/overview",
    } <= captured
    assert captured == current["endpoints"].keys()


async def test_erweiterungen_beschreiben_zugriffsfehler(current):
    for key, endpoint in current["endpoints"].items():
        if "/api/v1/ext/" in key:
            assert {"401", "403", "405", "413", "500", "507"} <= endpoint["response_content_types"].keys(), key


def _extension_input_models():
    checked = set()
    for name, module in list(sys.modules.items()):
        if not name.startswith("nodvard_deck_ext_") or module is None:
            continue
        for model in vars(module).values():
            if not inspect.isclass(model) or not issubclass(model, BaseModel):
                continue
            if not model.__module__.startswith("nodvard_deck_ext_"):
                continue
            if not model.__name__.endswith(("In", "Patch", "Request")) or model in checked:
                continue
            checked.add(model)
            yield model


def test_erweiterungsmodelle_beruecksichtigen_nur_eigene_definitionen(monkeypatch):
    from nodvard_sdk.actions import ActionRequest

    module = ModuleType("nodvard_deck_ext_contract_probe")

    class ProbeIn(BaseModel):
        pass

    ProbeIn.__module__ = module.__name__
    module.ProbeIn = ProbeIn
    module.AliasIn = ProbeIn
    module.ActionRequest = ActionRequest
    monkeypatch.setitem(sys.modules, module.__name__, module)

    models = list(_extension_input_models())
    assert models.count(ProbeIn) == 1
    assert ActionRequest not in models


async def test_erweiterungsmodelle_pruefen_felder_und_typen(current):
    checked = set(_extension_input_models())
    for model in checked:
        with pytest.raises(ValidationError) as caught:
            model.model_validate({"unbekanntes_feld": True})
        assert any(error["type"] == "extra_forbidden" for error in caught.value.errors()), model
        for field_name, field in model.model_fields.items():
            types = get_args(field.annotation) or (field.annotation,)
            for field_type, value, error_type in ((bool, "true", "bool_type"), (int, True, "int_type")):
                if field_type not in types:
                    continue
                with pytest.raises(ValidationError) as caught:
                    model.model_validate({field.alias or field_name: value})
                assert any(error["type"] == error_type for error in caught.value.errors()), (model, field_name)
    assert len(checked) >= 40


async def test_inventar_datum_bleibt_text_und_ist_keine_zahl(current):
    from nodvard_deck_ext_inventory import ItemIn

    item = ItemIn(name="Server", purchase_date="2026-11-06", warranty_until="2028-11-06")
    assert item.purchase_date.year == 2026 and item.warranty_until.year == 2028
    with pytest.raises(ValidationError):
        ItemIn(name="Server", purchase_date=0)


@pytest.mark.parametrize("path,media", [
    ("docker/containers/{host_id}/{container}/logs", "text/plain"),
    ("service-matrix/containers/{host_id}/{container}/logs", "text/plain"),
    ("documents/documents/{document_id}/download", "application/pdf"),
    ("inventory/items/{item_id}/images/{image_id}", "image/png"),
])
async def test_dateitypen_der_erweiterungen(current, path, media):
    endpoint = current["endpoints"][f"GET /api/v1/ext/{path}"]
    assert media in endpoint["response_content_types"]["200"]
    assert "application/json" not in endpoint["response_content_types"]["200"]


async def test_docker_git_quelle_beschreibt_fehlerantworten(current):
    responses = current["endpoints"]["PUT /api/v1/ext/docker/hosts/{host_id}/stacks/{project}/git"]["response_content_types"]
    for code in ("400", "409", "422", "503"):
        assert "application/json" in responses[code]


@pytest.mark.parametrize("method,path,codes", [
    ("GET", "proxmox/connections/{name}/old-tokens", {"404", "502"}),
    ("POST", "proxmox/connections/{name}/certificate", {"400", "409"}),
    ("POST", "backups/connections/{name}/certificate", {"400", "409"}),
])
async def test_verbindungsrouten_beschreiben_fehlerantworten(current, method, path, codes):
    responses = current["endpoints"][f"{method} /api/v1/ext/{path}"]["response_content_types"]
    for code in codes:
        assert "application/json" in responses[code]


@pytest.mark.parametrize("method,path,codes", [
    ("POST", "scans", {"400"}),
    ("POST", "updates/check", {"400"}),
    ("POST", "guard/check", {"400"}),
    ("POST", "events/{event_id}/acknowledge", {"409", "503"}),
    ("GET", "hosts/{host_id}/fail2ban", {"404", "409"}),
    ("POST", "hosts/{host_id}/fail2ban", {"400", "404", "409"}),
    ("POST", "hosts/{host_id}/ban", {"400", "404", "409"}),
    ("POST", "hosts/{host_id}/upgrade", {"404", "409"}),
    ("POST", "updates/propose-all", {"409"}),
    ("POST", "briefing", {"409"}),
    ("POST", "audits", {"400"}),
    ("POST", "findings/{finding_id}/quarantine", {"409"}),
    ("POST", "findings/{finding_id}/ignore", {"409"}),
    ("POST", "findings/{finding_id}/reopen", {"409"}),
    ("POST", "findings/{finding_id}/{action}", {"404", "409"}),
    ("POST", "realtime/{host_id}", {"400", "404", "409"}),
    ("POST", "hosts/{host_id}/install", {"404"}),
    ("POST", "watch-paths", {"400", "409"}),
])
async def test_shield_beschreibt_tatsaechliche_fehlerantworten(current, method, path, codes):
    responses = current["endpoints"][f"{method} /api/v1/ext/shield/defender/{path}"]["response_content_types"]
    for code in codes:
        assert "application/json" in responses[code]
    if path == "updates/propose-all":
        assert "404" not in responses


def _statuscodes(node, caught=False):
    if isinstance(node, ast.Try):
        handler = next((
            handler
            for handler in node.handlers
            if handler.type is None or isinstance(handler.type, ast.Name)
            and handler.type.id in {"HTTPException", "Exception", "BaseException"}
        ), None)
        handles = handler is not None and not any(
            isinstance(child, ast.Raise) for statement in handler.body for child in ast.walk(statement)
        )
        for statement in node.body:
            yield from _statuscodes(statement, caught or handles)
        for statement in [*node.handlers, *node.orelse, *node.finalbody]:
            yield from _statuscodes(statement, caught)
        return
    if isinstance(node, ast.Call):
        exception = isinstance(node.func, ast.Name) and node.func.id == "HTTPException"
        if not (caught and exception):
            values = [kw.value for kw in node.keywords if kw.arg == "status_code"]
            if exception and node.args:
                values.append(node.args[0])
            for value in values:
                if isinstance(value, ast.Constant) and isinstance(value.value, int):
                    yield str(value.value)
                elif isinstance(value, ast.Attribute) and value.attr.startswith("HTTP_"):
                    yield value.attr.split("_")[1]
    for child in ast.iter_child_nodes(node):
        yield from _statuscodes(child, caught)


async def test_statuscodes_der_erweiterungsrouten(current, current_routes):
    routes = [
        route for route in iter_route_contexts(current_routes)
        if isinstance(route.original_route, APIRoute) and route.include_in_schema and route.path.startswith("/api/v1/ext/")
    ]
    assert routes, "Keine Erweiterungsrouten für die Statuscode-Prüfung registriert"
    for route in routes:
        function = ast.parse(dedent(inspect.getsource(route.endpoint))).body[0]
        codes = {code for statement in function.body for code in _statuscodes(statement)}
        for method in sorted(route.methods & {method.upper() for method in cl.HTTP_METHODS}):
            key = f"{method} {route.path_format}"
            endpoint = current["endpoints"][key]
            assert codes <= endpoint["response_content_types"].keys(), (key, codes)


@pytest.mark.parametrize("ext_id", ["shield", "probe"])
@pytest.mark.parametrize("missing_code", ["202", "409"])
async def test_statuscode_pruefung_beachtet_routerpraefixe_und_fehlende_statuscodes(ext_id, missing_code):
    app = FastAPI()
    read = APIRouter(prefix="/defender")

    @read.get("/overview", responses={202: {"description": "Angenommen"}, 409: {"description": "Konflikt"}})
    async def overview(blocked: bool = False):
        if blocked:
            raise HTTPException(status.HTTP_409_CONFLICT)
        return Response(status_code=202)

    read.add_api_route("/hidden", overview, include_in_schema=False)
    router = APIRouter()
    router.include_router(read, prefix="/security")
    for registered_id in ("shield", "probe"):
        app.include_router(router, prefix=f"/api/v1/ext/{registered_id}")
    current = cl.build_api_snapshot(app.openapi())
    await test_statuscodes_der_erweiterungsrouten(current, app.routes)

    key = f"GET /api/v1/ext/{ext_id}/security/defender/overview"
    del current["endpoints"][key]["response_content_types"][missing_code]
    with pytest.raises(AssertionError) as caught:
        await test_statuscodes_der_erweiterungsrouten(current, app.routes)
    assert key in str(caught.value)
    assert missing_code in str(caught.value)


@pytest.mark.parametrize("path,missing_code", [("caught", "409"), ("reraised", "404")])
async def test_statuscode_pruefung_beachtet_abgefangene_und_erneut_geworfene_fehler(path, missing_code):
    app = FastAPI()
    router = APIRouter()

    @router.get("/caught", responses={409: {"description": "Konflikt"}})
    async def caught(conflict: bool = False):
        if conflict:
            raise HTTPException(status.HTTP_409_CONFLICT)
        try:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        except HTTPException as exc:
            return {"status": exc.status_code}

    @router.get("/reraised", responses={404: {"description": "Nicht gefunden"}})
    async def reraised():
        try:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001
            return {"status": "failed"}

    app.include_router(router, prefix="/api/v1/ext/probe")
    current = cl.build_api_snapshot(app.openapi())
    await test_statuscodes_der_erweiterungsrouten(current, app.routes)

    key = f"GET /api/v1/ext/probe/{path}"
    del current["endpoints"][key]["response_content_types"][missing_code]
    with pytest.raises(AssertionError) as caught_error:
        await test_statuscodes_der_erweiterungsrouten(current, app.routes)
    assert key in str(caught_error.value)
    assert missing_code in str(caught_error.value)


async def test_ssh_haertung_ist_im_vertrag(current):
    manifest = tomllib.loads((REPO_EXTENSIONS_DIR / "shield" / "extension.toml").read_text(encoding="utf-8"))
    extension = manifest["extension"]
    for snapshot in (_saved(), current):
        endpoints = snapshot["endpoints"]
        for ext_id in (extension["id"], *extension["legacy_ids"]):
            path = f"/api/v1/ext/{ext_id}/defender/hosts/{{host_id}}/ssh-hardening"
            assert endpoints[f"GET {path}"]["params"]["path:host_id"]["required"]
            fields = endpoints[f"POST {path}"]["body"]["schema"]["fields"]
            assert fields["mode"]["enum"] == ["add-key", "off", "on"]
            assert fields["mode"]["required"]
            for name in ("credential_id", "key_credential_id"):
                assert fields[name]["type"] == "null|string"
                assert not fields[name].get("required")
                query = endpoints[f"GET {path}"]["params"][f"query:{name}"]
                assert query["type"] == "null|string"
                assert not query.get("required")


@pytest.mark.parametrize("endpoint", [
    "GET /api/v1/ext/shield/defender/checks",
    "POST /api/v1/ext/shield/defender/checks/{host_id}/run",
    "PATCH /api/v1/ext/shield/defender/checks/{host_id}",
])
async def test_server_checks_sind_im_schnappschuss(current, endpoint):
    assert _saved()["endpoints"][endpoint] == current["endpoints"][endpoint]


async def test_push_arten_und_einstellungen_sind_im_vertrag(current):
    endpoint = current["endpoints"]["GET /api/v1/settings/notification-types"]
    assert endpoint["responses"]["200"] == {
        "type": "array", "items": {"type": "object", "fields": {"id": {"type": "string"}, "label": {"type": "string"}}},
    }
    keys = current["endpoints"]["PUT /api/v1/settings/{key}"]["params"]["path:key"]["enum"]
    assert "notifications.push_types" in keys
