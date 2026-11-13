"""Schema, Eingaben und Methoden der Kern-API."""

from __future__ import annotations

import ast
import inspect
import re
from typing import get_args

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import ASGITransport, AsyncClient
from nodvard_deck.api.v1 import router
from nodvard_deck.main import app, create_app
from pydantic import BaseModel, ValidationError
from starlette.routing import Mount


def _models(annotation):
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        yield annotation
        for field in annotation.model_fields.values():
            yield from _models(field.annotation)
    else:
        for arg in get_args(annotation):
            yield from _models(arg)


def test_public_imports_stehen_vor_konstanten():
    from nodvard_deck.api.v1 import public

    tree = ast.parse(inspect.getsource(public))
    imports = [
        node.lineno for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assignments = [node.lineno for node in tree.body if isinstance(node, ast.Assign)]
    assert max(imports) < min(assignments)


REQUEST_MODELS = sorted({
    model for route in iter_route_contexts(router.routes) if isinstance(route.original_route, APIRoute)
    for param in route.dependant.body_params for model in _models(param.field_info.annotation)
}, key=lambda model: model.__name__)


def test_eingabemodelle_enthalten_kern_und_verschachtelte_modelle():
    from nodvard_deck.api.v1.auth import BootstrapRequest, LoginRequest
    from nodvard_deck.api.v1.branding import BrandingColorsIn, BrandingIn
    from nodvard_deck.api.v1.files import TransferEndpoint, TransferIn

    assert {BootstrapRequest, LoginRequest, BrandingIn, BrandingColorsIn, TransferIn, TransferEndpoint} <= set(REQUEST_MODELS)


@pytest.mark.parametrize("model", REQUEST_MODELS, ids=lambda model: model.__name__)
def test_eingabemodelle_verbieten_unbekannte_felder(model):
    with pytest.raises(ValidationError) as caught:
        model.model_validate({"unbekanntes_feld": True})
    assert any(error["type"] == "extra_forbidden" for error in caught.value.errors())
    assert model.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize("model", REQUEST_MODELS, ids=lambda model: model.__name__)
def test_zahlen_und_schalter_werden_nicht_umgewandelt(model):
    for name, field in model.model_fields.items():
        types = get_args(field.annotation) or (field.annotation,)
        for field_type, value, error_type in ((bool, "true", "bool_type"), (int, True, "int_type")):
            if field_type not in types:
                continue
            with pytest.raises(ValidationError) as caught:
                model.model_validate({field.alias or name: value})
            assert any(error["type"] == error_type for error in caught.value.errors()), name


@pytest.fixture
async def owner(client):
    credentials = {"username": "owner1", "password": "correct-horse-battery"}
    created = await client.post("/api/v1/auth/bootstrap", json={**credentials, "setup_code": "TEST-CODE-2345"})
    assert created.status_code == 201, created.text
    login = await client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.parametrize("payload", [
    {"unbekannt": True}, {"first_steps_dismissed": "true"}, {"first_steps_dismissed": 1},
    {"theme": "unbekannt"}, {"theme": 1},
])
async def test_kaputte_einstellungen_geben_422(client, owner, payload):
    response = await client.patch("/api/v1/me/preferences", headers=owner, json=payload)
    assert response.status_code == 422, response.text
    assert all(set(error) == {"type", "loc", "msg"} for error in response.json()["detail"])


@pytest.mark.parametrize("change", [{"unbekannt": True}, {"client_type": "browser"}, {"username": 123}])
async def test_kaputte_anmeldung_ist_keine_anmeldung(client, change):
    response = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "secret", **change})
    assert response.status_code == 422


async def test_gueltige_einstellungen_bleiben_gueltig(client, owner):
    payload = {
        "first_steps_dismissed": True, "theme": "simple", "design_notice_dismissed": False,
        "totp_setup_skipped": True, "totp_reminder_dismissed": False,
    }
    response = await client.patch("/api/v1/me/preferences", headers=owner, json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == payload


async def test_verschachtelte_felder_werden_geprueft(client, owner):
    response = await client.put("/api/v1/branding", headers=owner, json={
        "product_name": "Deck", "short_name": "Deck", "colors": {"unbekannt": "red"},
    })
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "colors", "unbekannt"]


def test_freie_nutzdaten_und_datum_bleiben_gueltig():
    from nodvard_deck.api.v1.dashboard import LayoutPatch
    from nodvard_deck.api.v1.extension_settings import ExtensionSettingsIn
    from nodvard_deck.api.v1.hosts import TriggerActionIn
    from nodvard_deck.api.v1.secrets import SecretPatch
    from nodvard_deck.api.v1.settings import SettingIn

    data = {"eigener_schluessel": {"wert": [1, True, "Text"]}}
    assert ExtensionSettingsIn(values=data).values == data
    assert TriggerActionIn(reason="Test", payload=data).payload == data
    assert LayoutPatch(items=[data]).items == [data]
    assert SettingIn(value=data).value == data
    assert SecretPatch.model_validate({"expires_at": "2027-01-02T03:04:05Z"}).expires_at.year == 2027
    with pytest.raises(ValidationError):
        SecretPatch.model_validate({"expires_at": 123456789})


async def test_alle_bearer_routen_verlangen_den_kopf(client):
    app.openapi_schema = None
    for path, methods in app.openapi()["paths"].items():
        if path.startswith("/api/v1/ext/"):
            continue
        for method, operation in methods.items():
            if not any("HTTPBearer" in security for security in operation.get("security", [])):
                continue
            assert "401" in operation["responses"], (method, path)
            url = re.sub(r"\{[^}]+\}", "unbekannt", path)
            response = await client.request(method, url)
            assert response.status_code == 401, (method, path, response.text)
            assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("path,method,header", [
    ("/api/v1/auth/bootstrap/restore/pending", "delete", "x-setup-code"),
    ("/api/v1/system/restore/upload", "put", "x-confirm-password"),
])
def test_bestaetigungskopf_ist_im_schema_pflicht(path, method, header):
    app.openapi_schema = None
    operation = app.openapi()["paths"][path][method]
    param = next(param for param in operation["parameters"] if param["name"] == header)
    assert param["required"] is True
    assert "403" in operation["responses"]


async def test_ohne_einrichtungskopf_403(client):
    response = await client.delete("/api/v1/auth/bootstrap/restore/pending")
    assert response.status_code == 403
    assert "Einrichtungscode" in response.json()["detail"]


async def test_ohne_passwortkopf_403(client, owner):
    response = await client.put("/api/v1/system/restore/upload", headers=owner, content=b"unwichtig")
    assert response.status_code == 403
    assert "Passwort" in response.json()["detail"]


@pytest.mark.parametrize("frontend", [False, True])
async def test_unbekannte_methoden_mit_allow(monkeypatch, test_settings, frontend):
    from nodvard_deck import config
    from nodvard_deck.ext.runtime import ExtensionRuntime

    monkeypatch.setattr(config, "_settings", test_settings)
    dist = test_settings.data_dir / "frontend" / "dist"
    test_settings.frontend_dist = dist
    if frontend:
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("App-Shell", encoding="utf-8")
    application = create_app()
    assert any(isinstance(route, Mount) and route.name == "frontend" for route in application.routes) is frontend
    extension = APIRouter()

    @extension.get("/probe")
    async def probe():
        return {"ok": True}

    routes = ExtensionRuntime().mount_router(application, "probe", extension)
    responses = application.openapi()["paths"]["/api/v1/ext/probe/probe"]["get"]["responses"]
    assert responses["405"]["headers"]["Allow"]["schema"]["type"] == "string"
    assert "413" in responses and "500" in responses
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as http:
        response = await http.get("/")
        assert response.status_code == (200 if frontend else 404)
        if frontend:
            assert "App-Shell" in response.text
        for path, allowed in (
            ("/api/v1/health", {"GET"}),
            ("/api/v1/auth/bootstrap", {"GET", "POST"}),
            ("/api/v1/ext/probe/probe", {"GET"}),
        ):
            for method in ("TRACE", "OPTIONS", "BREW", "HEAD"):
                response = await http.request(method, path)
                assert response.status_code == 405, (method, path)
                assert set(response.headers["allow"].split(", ")) == allowed
                assert response.headers["content-type"] == "application/json"
        assert (await http.get("/api/v1/ext/probe/probe")).json() == {"ok": True}
        ExtensionRuntime().unmount_router(application, routes)
        assert (await http.get("/api/v1/ext/probe/probe")).status_code == 404


@pytest.mark.parametrize("path,method,code,media", [
    ("/api/v1/auth/login", "post", "202", "application/json"),
    ("/api/v1/actions/{action_id}/approve", "post", "202", "application/json"),
    ("/api/v1/hosts/{host_id}/actions/{action_type}", "post", "202", "application/json"),
    ("/api/v1/branding/custom.css", "get", "200", "text/css"),
    ("/api/v1/branding/logo", "get", "206", "image/png"),
    ("/api/v1/branding/favicon", "get", "200", "image/x-icon"),
    ("/api/v1/app/manifest.webmanifest", "get", "200", "application/manifest+json"),
    ("/api/v1/app/icon-{name}.png", "get", "200", "image/png"),
    ("/api/v1/audit/export", "get", "200", "application/x-ndjson"),
    ("/api/v1/files/{source_id}/download", "get", "206", "*/*"),
    ("/api/v1/files/{source_id}/download", "get", "416", "application/json"),
    ("/api/v1/hosts/{host_id}/metrics", "get", "503", "application/json"),
    ("/api/v1/extensions/{ext_id}/frontend/index.js", "get", "200", "application/javascript"),
    ("/api/v1/system/backups/download/{ticket}", "get", "200", "application/octet-stream"),
    ("/api/v1/system/restore/upload", "put", "415", "application/json"),
    ("/api/v1/hosts", "post", "409", "application/json"),
    ("/api/v1/hosts/{host_id}/credentials/{credential_id}/setup-with-password", "post", "504", "application/json"),
])
def test_antwortstatus_und_medientyp_sind_beschrieben(path, method, code, media):
    app.openapi_schema = None
    response = app.openapi()["paths"][path][method]["responses"][code]
    assert response["description"]
    assert media in response["content"]


@pytest.mark.parametrize("path,method", [
    ("/api/v1/telemetry", "get"),
    ("/api/v1/telemetry", "put"),
    ("/api/v1/telemetry", "delete"),
    ("/api/v1/telemetry/preview", "post"),
    ("/api/v1/telemetry/send", "post"),
])
def test_telemetrie_erbt_api_fehlerantworten(path, method):
    app.openapi_schema = None
    responses = app.openapi()["paths"][path][method]["responses"]
    for code in ("405", "413", "500"):
        assert "application/json" in responses[code]["content"]
    assert responses["405"]["headers"]["Allow"]["schema"]["type"] == "string"


@pytest.mark.parametrize("path,method", [
    ("/api/v1/telemetry", "get"),
    ("/api/v1/telemetry", "put"),
    ("/api/v1/telemetry", "delete"),
    ("/api/v1/telemetry/preview", "post"),
    ("/api/v1/telemetry/send", "post"),
])
def test_telemetrie_beschreibt_anmeldung_und_inhaberrecht(path, method):
    app.openapi_schema = None
    responses = app.openapi()["paths"][path][method]["responses"]
    for code in ("401", "403"):
        assert "application/json" in responses[code]["content"]


def test_telemetrie_beschreibt_gesperrte_konfiguration():
    app.openapi_schema = None
    response = app.openapi()["paths"]["/api/v1/telemetry"]["put"]["responses"]["409"]
    assert "application/json" in response["content"]


def test_statuscodes_aus_den_routen_stehen_im_schema():
    app.openapi_schema = None
    paths = app.openapi()["paths"]
    routes = [route for route in iter_route_contexts(router.routes) if isinstance(route.original_route, APIRoute)]
    assert {"/api/v1/auth/login", "/api/v1/branding", "/api/v1/hosts"} <= {route.path_format for route in routes}
    for route in routes:
        tree = ast.parse(inspect.getsource(route.endpoint))
        function = next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
        codes = set()
        for statement in function.body:
            for node in ast.walk(statement):
                if isinstance(node, ast.keyword) and node.arg == "status_code":
                    value = node.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, int):
                        codes.add(str(value.value))
                    elif isinstance(value, ast.Attribute) and value.attr.startswith("HTTP_"):
                        codes.add(value.attr.split("_")[1])
        for method in route.methods:
            described = paths[route.path_format][method.lower()]["responses"]
            assert codes <= described.keys(), (method, route.path_format, codes - described.keys())


def test_statuscode_pruefung_erkennt_fehlende_kernantwort(monkeypatch):
    app.openapi_schema = None
    schema = app.openapi()
    del schema["paths"]["/api/v1/auth/login"]["post"]["responses"]["202"]
    monkeypatch.setattr(app, "openapi", lambda: schema)
    with pytest.raises(AssertionError, match="202") as caught:
        test_statuscodes_aus_den_routen_stehen_im_schema()
    assert "/api/v1/auth/login" in str(caught.value)


async def test_oeffentliche_antworten_passen_zum_schema(client):
    app.openapi_schema = None
    paths = app.openapi()["paths"]
    for path in ("/api/v1/branding/custom.css", "/api/v1/app/manifest.webmanifest", "/api/v1/app/icon-192.png"):
        response = await client.get(path)
        assert response.status_code == 200
        schema_path = path.replace("icon-192", "icon-{name}")
        media = response.headers["content-type"].split(";")[0]
        assert media in paths[schema_path]["get"]["responses"]["200"]["content"]
    css = await client.get("/api/v1/branding/custom.css")
    cached = await client.get("/api/v1/branding/custom.css", headers={"If-None-Match": css.headers["etag"]})
    assert cached.status_code == 304 and not cached.content
    assert "304" in paths["/api/v1/branding/custom.css"]["get"]["responses"]
