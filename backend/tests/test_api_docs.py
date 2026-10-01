"""API-Doku nur im Entwicklungsmodus und fuer Admins.

FastAPI lieferte `/docs`, `/redoc` und `/openapi.json` ohne Anmeldung aus -- jeder im Netz sah
jeden Endpunkt samt Feldern. Jetzt gibt es sie ohne Anmeldung nur im Entwicklungsmodus
(`Settings.api_docs_public`); sonst 404. Fuer angemeldete Admins/Owner (Recht `system.read`)
liefert `GET /api/v1/system/openapi.json` das Dokument immer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nodvard_deck import config
from nodvard_deck.config import Settings
from nodvard_deck.main import create_app

PW = "correct-horse-battery"
DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
ADMIN_URL = "/api/v1/system/openapi.json"


# --------------------------------------------------------------------------- Einstellung


def test_standard_ist_der_entwicklungsmodus_der_umgebung():
    assert Settings(env="dev").api_docs_public is True
    assert Settings(env="DEV").api_docs_public is True
    assert Settings(env="prod").api_docs_public is False
    assert Settings(env="staging").api_docs_public is False


def test_ausdruecklicher_wert_gewinnt_ueber_die_umgebung():
    assert Settings(env="prod", api_docs=True).api_docs_public is True
    assert Settings(env="dev", api_docs=False).api_docs_public is False


def test_umgebungsvariable_und_alter_name(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NODVARD_DECK_ENV", "prod")
    monkeypatch.delenv("NODVARD_DECK_API_DOCS", raising=False)
    monkeypatch.delenv("LATTICE_API_DOCS", raising=False)
    assert Settings().api_docs_public is False
    monkeypatch.setenv("NODVARD_DECK_API_DOCS", "1")
    assert Settings().api_docs_public is True
    monkeypatch.delenv("NODVARD_DECK_API_DOCS")
    monkeypatch.setenv("LATTICE_API_DOCS", "true")
    assert Settings().api_docs_public is True


# --------------------------------------------------------------------------- oeffentliche Routen


def _app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **settings):
    """Frische App aus `create_app()` mit eigenen Settings (die Routen stehen beim Erzeugen fest)."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setattr(config, "_settings", Settings(data_dir=data, **settings))
    return create_app()


def _http(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")


@pytest.mark.parametrize("path", DOC_PATHS)
async def test_ohne_entwicklungsmodus_gibt_es_die_doku_nicht(monkeypatch, tmp_path, path):
    async with _http(_app(monkeypatch, tmp_path, env="prod")) as http:
        res = await http.get(path)
    assert res.status_code == 404


async def test_im_entwicklungsmodus_ist_die_doku_erreichbar(monkeypatch, tmp_path):
    async with _http(_app(monkeypatch, tmp_path, env="dev")) as http:
        swagger = await http.get("/docs")
        redoc = await http.get("/redoc")
        schema = await http.get("/openapi.json")
    assert swagger.status_code == 200 and "swagger" in swagger.text.lower()
    assert redoc.status_code == 200 and "redoc" in redoc.text.lower()
    assert schema.status_code == 200
    assert schema.json()["info"]["title"] == "Nodvard Deck"


@pytest.mark.parametrize("path", DOC_PATHS)
async def test_schalter_schaltet_die_doku_in_beide_richtungen(monkeypatch, tmp_path, path):
    async with _http(_app(monkeypatch, tmp_path, env="prod", api_docs=True)) as http:
        assert (await http.get(path)).status_code == 200
    async with _http(_app(monkeypatch, tmp_path, env="dev", api_docs=False)) as http:
        assert (await http.get(path)).status_code == 404


def test_das_dokument_laesst_sich_auch_ohne_route_bauen(monkeypatch, tmp_path):
    """`openapi_url=None` schaltet nur die Route ab. Die Vertragstests (`app.openapi()`) und der
    Admin-Endpunkt brauchen das Dokument weiterhin."""
    app = _app(monkeypatch, tmp_path, env="prod")
    assert app.openapi_url is None
    doc = app.openapi()
    assert doc["paths"]["/api/v1/system/info"]
    assert ADMIN_URL in doc["paths"]
    for path in DOC_PATHS:
        assert path not in doc["paths"]  # die Doku beschreibt sich nie selbst


@pytest.fixture
def frontend_dist(tmp_path: Path) -> Path:
    """`<data_dir>/../frontend/dist` -- dort haengt `create_app()` die App-Shell ein."""
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>App-Shell</html>", encoding="utf-8")
    return dist


@pytest.mark.parametrize("path", [*DOC_PATHS, "/docs/oauth2-redirect", "/docs/"])
async def test_die_app_shell_beantwortet_die_doku_adressen_nicht(monkeypatch, tmp_path, frontend_dist, path):
    """Mit ausgeliefertem Frontend (Produktion) wuerde der Rueckfall auf `index.html` `/openapi.json`
    sonst mit 200 und HTML beantworten. Es bleibt ein 404; andere Seiten der App gehen weiter."""
    async with _http(_app(monkeypatch, tmp_path, env="prod")) as http:
        res = await http.get(path)
        assert res.status_code == 404, path
        assert "App-Shell" not in res.text
        shell = await http.get("/settings")
    assert shell.status_code == 200 and "App-Shell" in shell.text


async def test_mit_frontend_liefert_der_entwicklungsmodus_die_doku_statt_der_app_shell(
    monkeypatch, tmp_path, frontend_dist
):
    async with _http(_app(monkeypatch, tmp_path, env="dev")) as http:
        schema = await http.get("/openapi.json")
        swagger = await http.get("/docs")
    assert schema.json()["info"]["title"] == "Nodvard Deck"
    assert "App-Shell" not in swagger.text


# --------------------------------------------------------------------------- Admin-Endpunkt


@pytest.fixture
def prod_app(db_session, test_settings, monkeypatch):
    """Eine App wie in der Produktion (`env=prod`, keine oeffentliche Doku) gegen die Test-Datenbank."""
    from nodvard_deck.api.deps import get_session
    from nodvard_deck.ext.runtime import reset_extension_runtime

    test_settings.env = "prod"
    monkeypatch.setattr(config, "_settings", test_settings)
    app = create_app()

    async def _session():
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[config.get_settings] = lambda: test_settings
    reset_extension_runtime()
    yield app
    reset_extension_runtime()


@pytest_asyncio.fixture
async def prod_client(prod_app):
    async with _http(prod_app) as http:
        yield http


async def _login(http, username: str) -> dict:
    res = await http.post("/api/v1/auth/login", json={"username": username, "password": PW})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


async def _owner(http) -> dict:
    res = await http.post(
        "/api/v1/auth/bootstrap", json={"username": "owner1", "password": PW, "setup_code": "TEST-CODE-2345"}
    )
    assert res.status_code in (200, 201), res.text
    return await _login(http, "owner1")


async def _with_role(http, db_session, role: str) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=role, password_hash=security.hash_password(PW), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    return await _login(http, role)


async def test_admin_endpunkt_ohne_token_ist_401(prod_client):
    assert (await prod_client.get(ADMIN_URL)).status_code == 401
    assert (await prod_client.get(ADMIN_URL, headers={"Authorization": "Bearer kaputt"})).status_code == 401


@pytest.mark.parametrize("role", ["viewer", "operator"])
async def test_admin_endpunkt_ohne_das_recht_ist_403(prod_client, db_session, role):
    await _owner(prod_client)
    headers = await _with_role(prod_client, db_session, role)
    res = await prod_client.get(ADMIN_URL, headers=headers)
    assert res.status_code == 403
    assert "system.read" in res.json()["detail"]


@pytest.mark.parametrize("who", ["owner", "admin"])
async def test_admin_endpunkt_mit_dem_recht_liefert_ein_gueltiges_schema(prod_client, db_session, who):
    owner = await _owner(prod_client)
    headers = owner if who == "owner" else await _with_role(prod_client, db_session, "admin")

    res = await prod_client.get(ADMIN_URL, headers=headers)

    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/json")
    assert res.headers["cache-control"] == "no-store"
    doc = res.json()
    assert doc["openapi"].startswith("3.")
    assert doc["info"] == {"title": "Nodvard Deck", "version": doc["info"]["version"]}
    assert doc["info"]["version"]
    paths = doc["paths"]
    assert ADMIN_URL in paths and "get" in paths[ADMIN_URL]
    assert "/api/v1/system/info" in paths and "/api/v1/auth/login" in paths
    # Jeder Pfad nennt mindestens eine Methode mit Antworten (grobe Formpruefung statt eines Validators).
    for path, item in paths.items():
        methods = [m for m in item if m in ("get", "put", "post", "delete", "patch")]
        assert methods, path
        assert all(item[m]["responses"] for m in methods), path
    assert doc["components"]["schemas"]


async def test_dieselbe_app_antwortet_oeffentlich_mit_404_und_dem_admin_mit_dem_dokument(prod_client):
    """Das Zusammenspiel: die oeffentlichen Adressen sind weg, der geschuetzte Weg bleibt."""
    owner = await _owner(prod_client)
    for path in DOC_PATHS:
        assert (await prod_client.get(path)).status_code == 404, path
        assert (await prod_client.get(path, headers=owner)).status_code == 404, path
    assert (await prod_client.get(ADMIN_URL, headers=owner)).status_code == 200


async def test_admin_endpunkt_baut_das_dokument_neu_auf(prod_app, prod_client):
    """FastAPI merkt sich das Dokument nach dem ersten Abruf; Erweiterungen koennen danach dazukommen
    oder wegfallen. Ein veralteter Zwischenspeicher darf nicht ausgeliefert werden."""
    owner = await _owner(prod_client)
    prod_app.openapi_schema = {"openapi": "3.1.0", "info": {"title": "veraltet", "version": "0"}, "paths": {}}

    doc = (await prod_client.get(ADMIN_URL, headers=owner)).json()

    assert doc["info"]["title"] == "Nodvard Deck"
    assert ADMIN_URL in doc["paths"]
