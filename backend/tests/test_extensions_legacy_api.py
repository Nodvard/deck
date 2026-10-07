"""Alte Adressen einer umbenannten Erweiterung (`legacy_ids` im Manifest) in der API `/api/v1`.

Eine umbenannte Erweiterung bleibt unter ihren alten Kennungen erreichbar: ihre Routen zusaetzlich unter
`/api/v1/ext/<alt>/...` (im OpenAPI-Schema als veraltet markiert), die Kern-Adressen
`/api/v1/extensions/<alt>/...` loesen die Kennung zentral auf. Antworten nennen immer die heutige Kennung,
dazu die alten (`legacy_ids`, `legacy_ext_ids`). Liegt unter einer alten Kennung noch ein verwaister Zwilling,
wird er nie gelistet, und ueber seine Adresse aendert sich nichts. Ohne `legacy_ids` bleibt alles wie vorher.

Test-Erweiterung im tmp-Ordner: Kennung `renamed-ext`, `legacy_ids = ["old-ext"]`, eine geschuetzte und eine
oeffentliche Route, eine Seite, ein Widget, eine Aktionsart, ein Einstellungs-Schema und ein Bundle.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Action, AuditEntry, ExtensionRecord, Secret, Setting
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

BUNDLE = "export const kennung = 'renamed-ext';\n"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    reset_extension_runtime()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


_CODE = '''
from fastapi import APIRouter, WebSocket
from nodvard_sdk import ActionSpec, GridSize, HealthReport, ListItem, ListView, PageSpec, WidgetSpec


class Extension:
    async def setup(self, ctx):
        router = APIRouter()

        @router.get("/x")
        async def x():
            return {"settings": await ctx.settings.get()}

        ctx.api.include_router(router)

        hook = APIRouter()

        @hook.get("/ping")
        async def ping():
            return {"pong": True}

        ctx.api.include_router(hook, prefix="/hook", public=True)
        if EXTRAS:
            secure = APIRouter()

            @secure.get("/geheim")
            async def geheim():
                return {"geheim": True}

            # Nur mit der Berechtigung `hosts.execute` (Anmeldung allein reicht nicht).
            ctx.api.include_router(secure, prefix="/secure", permission="hosts.execute")

            live = APIRouter()

            @live.websocket("/live")
            async def live_ws(websocket: WebSocket):
                # Wie vorgesehen selbst abgesichert: erste Nachricht mit dem Wert der Einstellung "ziel".
                await websocket.accept()
                hello = await websocket.receive_json()
                settings = await ctx.settings.get()
                if hello.get("token") != settings.get("ziel"):
                    await websocket.close(code=4401)
                    return
                await websocket.send_json({"pfad": websocket.url.path, "settings": settings})
                await websocket.close()

            ctx.api.include_router(live, prefix="/ws", public=True)
        ctx.ui.register_page(PageSpec(id="main", path="/main", title="Haupt", component="Main"))
        ctx.ui.register_widget(WidgetSpec(
            id="kachel", title="Kachel", size=GridSize(w=2, h=1), data_endpoint="x",
            view=ListView(item=ListItem(title="{{ title }}")),
        ))
        ctx.actions.register(ActionSpec(action_type=ACTION_TYPE, label="Etwas tun", host_bound=False))

    async def on_start(self, ctx):
        pass

    async def on_stop(self, ctx):
        pass

    async def health(self, ctx):
        return HealthReport(healthy=True)
'''

SCHEMA = {
    "type": "object",
    "properties": {
        "wert": {"type": "integer", "title": "Wert"},
        "ziel": {"type": "string", "title": "Ziel"},
    },
    "required": ["ziel"],
    "x-secrets": [{"label": "renamed-token", "title": "Token", "optional": True}],
}


def _write_ext(root: Path, ext_id: str, *, legacy_ids: list[str] | None = None, extras: bool = False) -> Path:
    module = "nodvard_deck_ext_" + ext_id.replace("-", "_")
    ext_dir = root / ext_id
    (ext_dir / "src" / module).mkdir(parents=True)
    extra = "legacy_ids = [" + ", ".join(f'"{x}"' for x in legacy_ids) + "]\n" if legacy_ids else ""
    (ext_dir / "extension.toml").write_text(
        f'[extension]\nid = "{ext_id}"\nname = "Erweiterung {ext_id}"\nversion = "0.2.0"\napi_version = "0.1"\n'
        f'entrypoint = "{module}:Extension"\nfrontend = "frontend/dist/index.js"\npermissions = ["api.public"]\n'
        f'{extra}\n[settings]\nschema = "settings.schema.json"\n',
        encoding="utf-8",
    )
    (ext_dir / "settings.schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    (ext_dir / "frontend" / "dist").mkdir(parents=True)
    (ext_dir / "frontend" / "dist" / "index.js").write_text(BUNDLE, encoding="utf-8")
    action_type = ext_id.replace("-", "_") + ".tu_was"
    header = f"ACTION_TYPE = {action_type!r}\nEXTRAS = {extras!r}\n"
    (ext_dir / "src" / module / "__init__.py").write_text(header + _CODE, encoding="utf-8")
    return ext_dir


def _row(ext_id: str, *, state: str, settings: dict, name: str | None = None) -> ExtensionRecord:
    return ExtensionRecord(
        id=ext_id, version="0.1.0", api_version="0.1", state=state, source="bundled", settings=settings,
        manifest={"id": ext_id, "name": name or f"Erweiterung {ext_id}"}, granted_permissions=[],
    )


async def _boot(db_session, test_settings) -> None:
    """Ein Start: Entdecken, eingeschaltete Erweiterungen laden (an der echten App der `client`-Fixture)."""
    from nodvard_deck.main import app

    await extensions_service.discover_and_sync(db_session, test_settings)
    await extensions_service.load_enabled_from_registry(app, db_session, test_settings)
    await db_session.commit()


async def _owner_token(client) -> str:
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


async def _renamed_with_old_row(client, db_session, test_settings) -> dict:
    """Bestehende Installation nach dem Update: nur die Zeile `old-ext` (eingeschaltet, mit Einstellungen),
    die Erweiterung heisst jetzt `renamed-ext`. -> Kopfzeilen mit Anmeldung."""
    _write_ext(test_settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    db_session.add(_row("old-ext", state="enabled", settings={"wert": 1}))
    await db_session.commit()
    await _boot(db_session, test_settings)
    assert get_extension_runtime().loaded["renamed-ext"].store_id == "old-ext"
    return {"Authorization": f"Bearer {await _owner_token(client)}"}


async def _stored(db_session, ext_id: str) -> dict | None:
    """Die Zeile so, wie sie in der Datenbank steht (nicht aus der Identity Map)."""
    await db_session.commit()
    db_session.expire_all()
    record = await db_session.get(ExtensionRecord, ext_id)
    if record is None:
        return None
    return {"state": record.state, "settings": dict(record.settings or {}), "last_error": record.last_error}


async def _row_ids(db_session) -> list[str]:
    return sorted((await db_session.execute(select(ExtensionRecord.id))).scalars().all())


def _openapi_paths() -> dict:
    from nodvard_deck.main import app

    app.openapi_schema = None
    try:
        return app.openapi()["paths"]
    finally:
        app.openapi_schema = None


async def _audits(db_session, action: str) -> list[AuditEntry]:
    return list((await db_session.execute(select(AuditEntry).where(AuditEntry.action == action))).scalars().all())


# -- Routen der Erweiterung unter der alten Kennung ---------------------------------------------------------


@pytest.mark.asyncio
async def test_old_route_address_answers_like_the_new_one_and_is_deprecated(client, db_session, test_settings):
    headers = await _renamed_with_old_row(client, db_session, test_settings)

    new = await client.get("/api/v1/ext/renamed-ext/x", headers=headers)
    old = await client.get("/api/v1/ext/old-ext/x", headers=headers)
    assert new.status_code == old.status_code == 200
    assert old.json() == new.json() == {"settings": {"wert": 1}}
    # Dieselbe Anmeldepruefung: ohne Anmeldung 401 unter beiden Adressen, die oeffentliche Route geht ohne.
    assert (await client.get("/api/v1/ext/old-ext/x")).status_code == 401
    assert (await client.get("/api/v1/ext/renamed-ext/x")).status_code == 401
    assert (await client.get("/api/v1/ext/old-ext/hook/ping")).json() == {"pong": True}

    paths = _openapi_paths()
    assert paths["/api/v1/ext/old-ext/x"]["get"].get("deprecated") is True
    assert paths["/api/v1/ext/old-ext/hook/ping"]["get"].get("deprecated") is True
    assert not paths["/api/v1/ext/renamed-ext/x"]["get"].get("deprecated")
    assert not paths["/api/v1/ext/renamed-ext/hook/ping"]["get"].get("deprecated")
    # Die oeffentlichen Adressen fuers Protokoll nennen auch die unter der alten Kennung.
    assert extensions_service.public_route_prefixes("renamed-ext") == [
        "/api/v1/ext/renamed-ext/hook", "/api/v1/ext/old-ext/hook",
    ]

    # Ausschalten nimmt die Routen unter beiden Kennungen weg, auch aus dem Schema.
    r = await client.post("/api/v1/extensions/renamed-ext/disable", headers=headers)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/ext/old-ext/x", headers=headers)).status_code == 404
    assert (await client.get("/api/v1/ext/renamed-ext/x", headers=headers)).status_code == 404
    paths = _openapi_paths()
    assert not [p for p in paths if p.startswith(("/api/v1/ext/old-ext", "/api/v1/ext/renamed-ext"))]

    # Wieder einschalten ueber die alte Kennung: Routen unter beiden Kennungen, Protokoll mit der heutigen.
    r = await client.post("/api/v1/extensions/old-ext/enable", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "renamed-ext" and r.json()["state"] == "enabled"
    assert (await client.get("/api/v1/ext/old-ext/x", headers=headers)).status_code == 200
    enabled = await _audits(db_session, "extension.enabled")
    assert [a.target_id for a in enabled] == ["renamed-ext"]
    assert enabled[0].detail["public_routes"] == ["/api/v1/ext/renamed-ext/hook", "/api/v1/ext/old-ext/hook"]
    assert await _row_ids(db_session) == ["old-ext"]


@pytest.mark.asyncio
async def test_current_id_comes_first_for_url_building_and_in_the_schema(client, db_session, test_settings):
    """Die Routen unter der heutigen Kennung stehen vor denen unter den alten: `url_path_for` (und damit
    `request.url_for` einer Erweiterung) baut die heutige Adresse, nie eine veraltete, und im OpenAPI-Schema
    folgen die alten Pfade der heutigen in der Reihenfolge des Manifests."""
    from nodvard_deck.main import app

    _write_ext(test_settings.extensions_dir, "renamed-ext", legacy_ids=["mid-ext", "old-ext"])
    await _boot(db_session, test_settings)
    headers = {"Authorization": f"Bearer {await _owner_token(client)}"}
    assert (await client.post("/api/v1/extensions/renamed-ext/enable", headers=headers)).status_code == 200

    assert app.url_path_for("x") == "/api/v1/ext/renamed-ext/x"
    assert app.url_path_for("ping") == "/api/v1/ext/renamed-ext/hook/ping"
    ext_paths = [p for p in _openapi_paths() if p.startswith("/api/v1/ext/") and p.endswith("/x")]
    assert ext_paths == ["/api/v1/ext/renamed-ext/x", "/api/v1/ext/mid-ext/x", "/api/v1/ext/old-ext/x"]
    for ext_id in ("renamed-ext", "mid-ext", "old-ext"):
        assert (await client.get(f"/api/v1/ext/{ext_id}/x", headers=headers)).status_code == 200


# -- Kern-Adressen /api/v1/extensions/<alt>/... -------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_addresses_resolve_the_old_id_to_the_same_row(client, db_session, test_settings):
    _write_ext(test_settings.extensions_dir, "pear-ext")
    headers = await _renamed_with_old_row(client, db_session, test_settings)

    by_old = await client.get("/api/v1/extensions/old-ext", headers=headers)
    by_new = await client.get("/api/v1/extensions/renamed-ext", headers=headers)
    assert by_old.status_code == by_new.status_code == 200
    assert by_old.json() == by_new.json()
    body = by_new.json()
    assert body["id"] == "renamed-ext" and body["legacy_ids"] == ["old-ext"]
    assert body["name"] == "Erweiterung renamed-ext" and body["state"] == "enabled"
    # Schema und Einrichtungs-Pruefung kommen ueber die heutige Kennung, nicht ueber die Zeile `old-ext`.
    assert body["has_settings"] is True
    assert body["needs_setup"] is True and body["setup_reasons"]

    # Sortiert nach der Kennung, die die Liste zeigt (nicht nach der Zeile `old-ext`).
    listed = (await client.get("/api/v1/extensions", headers=headers)).json()
    assert [(e["id"], e["legacy_ids"]) for e in listed] == [("pear-ext", []), ("renamed-ext", ["old-ext"])]

    settings_old = await client.get("/api/v1/extensions/old-ext/settings", headers=headers)
    assert settings_old.status_code == 200
    assert settings_old.json()["values"] == {"wert": 1}
    assert settings_old.json()["schema"]["properties"]["ziel"]["title"] == "Ziel"
    assert settings_old.json() == (await client.get("/api/v1/extensions/renamed-ext/settings", headers=headers)).json()

    r = await client.put(
        "/api/v1/extensions/old-ext/settings", headers=headers, json={"values": {"wert": 2, "ziel": "pve1"}}
    )
    assert r.status_code == 200, r.text
    assert (await _stored(db_session, "old-ext"))["settings"] == {"wert": 2, "ziel": "pve1"}
    assert await _row_ids(db_session) == ["old-ext", "pear-ext"]
    assert (await client.get("/api/v1/ext/renamed-ext/x", headers=headers)).json() == {"settings": {"wert": 2, "ziel": "pve1"}}
    assert [a.target_id for a in await _audits(db_session, "extension.settings")] == ["renamed-ext"]
    assert (await client.get("/api/v1/extensions/old-ext", headers=headers)).json()["needs_setup"] is False

    r = await client.put("/api/v1/extensions/old-ext/secrets", headers=headers, json={"label": "renamed-token", "value": "geheim"})
    assert r.status_code == 204, r.text
    secret = (await db_session.execute(select(Secret).where(Secret.label == "renamed-token"))).scalar_one()
    assert secret.owner_ext_id == "renamed-ext"

    r = await client.post("/api/v1/extensions/old-ext/test", headers=headers, json={})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    keys = (await db_session.execute(select(Setting.key).where(Setting.key.like("extension.test.%")))).scalars().all()
    assert keys == ["extension.test.old-ext"]

    r = await client.delete("/api/v1/extensions/old-ext/secrets", headers=headers, params={"label": "renamed-token"})
    assert r.status_code == 204, r.text
    assert (await db_session.execute(select(Secret).where(Secret.label == "renamed-token"))).scalar_one_or_none() is None

    r = await client.post("/api/v1/extensions/old-ext/disable", headers=headers)
    assert r.status_code == 200, r.text
    assert (r.json()["id"], r.json()["state"]) == ("renamed-ext", "disabled")
    assert (await _stored(db_session, "old-ext"))["state"] == "disabled"
    assert get_extension_runtime().loaded == {}
    assert [a.target_id for a in await _audits(db_session, "extension.disabled")] == ["renamed-ext"]
    assert await _row_ids(db_session) == ["old-ext", "pear-ext"]


@pytest.mark.asyncio
async def test_old_address_works_on_a_new_installation(client, db_session, test_settings):
    """Neuinstallation (nur die Zeile `renamed-ext`, kein Zwilling): auch hier gilt die alte Kennung als
    anderer Name der Erweiterung, auch zum Aendern; eine Zeile `old-ext` entsteht dabei nie."""
    _write_ext(test_settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    await _boot(db_session, test_settings)
    headers = {"Authorization": f"Bearer {await _owner_token(client)}"}
    assert await _row_ids(db_session) == ["renamed-ext"]

    r = await client.post("/api/v1/extensions/old-ext/enable", headers=headers)
    assert r.status_code == 200, r.text
    assert (r.json()["id"], r.json()["state"]) == ("renamed-ext", "enabled")
    r = await client.put("/api/v1/extensions/old-ext/settings", headers=headers, json={"values": {"ziel": "pve1"}})
    assert r.status_code == 200, r.text
    assert (await _stored(db_session, "renamed-ext"))["settings"] == {"ziel": "pve1"}
    assert (await client.get("/api/v1/ext/old-ext/x", headers=headers)).json() == {"settings": {"ziel": "pve1"}}
    assert (await client.post("/api/v1/extensions/old-ext/disable", headers=headers)).status_code == 200
    assert (await _stored(db_session, "renamed-ext"))["state"] == "disabled"
    assert await _row_ids(db_session) == ["renamed-ext"]


def test_legacy_ids_of_names_only_old_ids_the_scan_gave_to_the_extension():
    """Alte Adressen gibt es nur fuer alte Kennungen, die die Erweiterung laut Scan ohne Konflikt besitzt
    (`ExtensionRuntime.legacy_owner`) -- nie fuer eine, die gerade einer anderen gehoert."""
    from types import SimpleNamespace

    from nodvard_sdk import ExtensionManifest

    runtime = get_extension_runtime()
    manifest = ExtensionManifest(
        id="renamed-ext", name="x", version="0.1.0", api_version="0.1", entrypoint="m:E",
        legacy_ids=["mid-ext", "old-ext"],
    )
    runtime.discovered = {"renamed-ext": SimpleNamespace(ok=True, manifest=manifest)}
    runtime.legacy_owner = {"mid-ext": "renamed-ext", "old-ext": "other-ext"}
    assert runtime.legacy_ids_of("renamed-ext") == ["mid-ext"]
    runtime.legacy_owner = {"old-ext": "renamed-ext", "mid-ext": "renamed-ext"}
    assert runtime.legacy_ids_of("renamed-ext") == ["mid-ext", "old-ext"]
    assert runtime.legacy_ids_of("other-ext") == []


@pytest.mark.asyncio
async def test_bundle_is_served_under_the_old_id(client, db_session, test_settings):
    await _renamed_with_old_row(client, db_session, test_settings)
    for ext_id in ("old-ext", "renamed-ext"):
        r = await client.get(f"/api/v1/extensions/{ext_id}/frontend/index.js")
        assert r.status_code == 200, (ext_id, r.text)
        assert r.text == BUNDLE
    assert (await client.get("/api/v1/extensions/sonst-ext/frontend/index.js")).status_code == 404


# -- Kataloge und Aktionen ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pages_and_widgets_name_the_old_ids(client, db_session, test_settings):
    headers = await _renamed_with_old_row(client, db_session, test_settings)
    pages = (await client.get("/api/v1/pages", headers=headers)).json()
    widgets = (await client.get("/api/v1/widgets", headers=headers)).json()
    assert [(p["ext_id"], p["id"], p["legacy_ext_ids"]) for p in pages] == [("renamed-ext", "main", ["old-ext"])]
    assert [(w["ext_id"], w["id"], w["legacy_ext_ids"]) for w in widgets] == [("renamed-ext", "kachel", ["old-ext"])]


@pytest.mark.asyncio
async def test_actions_carry_the_action_label_and_the_extension_name(client, db_session, test_settings):
    """`action_label` aus der angemeldeten Aktionsart, `ext_name` aus dem Manifest -- auch fuer Vorschlaege,
    die noch die alte Kennung tragen (von vor der Umbenennung)."""
    headers = await _renamed_with_old_row(client, db_session, test_settings)
    for ext_id, action_type in (
        ("old-ext", "renamed_ext.tu_was"), ("renamed-ext", "renamed_ext.tu_was"), ("core", "unbekannt.tu_was"),
    ):
        db_session.add(Action(
            ext_id=ext_id, action_type=action_type, host_id=None, payload={}, risk="low", status="proposed",
            proposed_by_type="extension", proposed_by_id=ext_id, reason="Test", gate_decision={},
        ))
    await db_session.commit()

    r = await client.get("/api/v1/actions", headers=headers)
    assert r.status_code == 200, r.text
    got = sorted((a["ext_id"], a["action_label"], a["ext_name"]) for a in r.json())
    assert got == [
        ("core", None, None),
        ("old-ext", "Etwas tun", "Erweiterung renamed-ext"),
        ("renamed-ext", "Etwas tun", "Erweiterung renamed-ext"),
    ]
    one = next(a for a in r.json() if a["ext_id"] == "old-ext")
    single = await client.get(f"/api/v1/actions/{one['id']}", headers=headers)
    assert (single.json()["action_label"], single.json()["ext_name"]) == ("Etwas tun", "Erweiterung renamed-ext")


# -- verwaister Zwilling -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_twin_is_not_listed_and_its_address_changes_nothing(client, db_session, test_settings):
    """Neuinstallation (Zeile `renamed-ext`), Rueckweg aufs alte Image (Zeile `old-ext`, dort eingeschaltet),
    wieder neu: `old-ext` ist ein verwaister Zwilling. Er fehlt in der Liste. Lesen ueber seine Adresse zeigt
    die Erweiterung (mit heutiger Kennung); Ein-/Ausschalten, Einstellungen, Zugangsdaten und Test ueber sie
    antworten 409 und aendern weder den Zwilling noch die Erweiterung."""
    _write_ext(test_settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    db_session.add(_row("renamed-ext", state="disabled", settings={"wert": 5, "ziel": "pve1"}))
    db_session.add(_row("old-ext", state="enabled", settings={"wert": 1}))
    await db_session.commit()
    await _boot(db_session, test_settings)
    headers = {"Authorization": f"Bearer {await _owner_token(client)}"}
    runtime = get_extension_runtime()
    assert runtime.loaded == {} and runtime.is_stale_twin("old-ext")
    twin = await _stored(db_session, "old-ext")

    listed = (await client.get("/api/v1/extensions", headers=headers)).json()
    assert [(e["id"], e["state"]) for e in listed] == [("renamed-ext", "disabled")]

    r = await client.post("/api/v1/extensions/old-ext/enable", headers=headers)
    assert r.status_code == 409, r.text
    assert "renamed-ext" in r.json()["detail"]
    assert runtime.loaded == {}
    assert (await _stored(db_session, "renamed-ext"))["state"] == "disabled"
    assert await _stored(db_session, "old-ext") == twin

    assert (await client.post("/api/v1/extensions/renamed-ext/enable", headers=headers)).status_code == 200
    assert "renamed-ext" in runtime.loaded
    real = await _stored(db_session, "renamed-ext")

    for method, path, kwargs in (
        ("post", "/api/v1/extensions/old-ext/disable", {}),
        ("put", "/api/v1/extensions/old-ext/settings", {"json": {"values": {"wert": 9, "ziel": "pve2"}}}),
        ("put", "/api/v1/extensions/old-ext/secrets", {"json": {"label": "renamed-token", "value": "x"}}),
        ("delete", "/api/v1/extensions/old-ext/secrets", {"params": {"label": "renamed-token"}}),
        ("post", "/api/v1/extensions/old-ext/test", {"json": {}}),
    ):
        r = await getattr(client, method)(path, headers=headers, **kwargs)
        assert r.status_code == 409, (method, path, r.text)
    assert "renamed-ext" in runtime.loaded
    assert await _stored(db_session, "renamed-ext") == real
    assert await _stored(db_session, "old-ext") == twin
    assert (await db_session.execute(select(Secret))).scalars().all() == []
    assert not (await db_session.execute(select(Setting.key).where(Setting.key.like("extension.test.%")))).scalars().all()
    assert not await _audits(db_session, "extension.disabled")
    assert not await _audits(db_session, "extension.settings")

    # Lesen geht weiter und nennt die heutige Kennung; die Routen der Erweiterung gelten unter beiden.
    by_old = (await client.get("/api/v1/extensions/old-ext", headers=headers)).json()
    assert (by_old["id"], by_old["state"]) == ("renamed-ext", "enabled")
    assert (await client.get("/api/v1/extensions/old-ext/settings", headers=headers)).json()["values"] == {
        "wert": 5, "ziel": "pve1",
    }
    assert (await client.get("/api/v1/ext/old-ext/x", headers=headers)).json() == {"settings": {"wert": 5, "ziel": "pve1"}}


# -- Berechtigung und WebSocket ueber die alte Adresse -------------------------------------------------------


async def _viewer_headers(client, db_session) -> dict:
    """Ein Nutzer der Rolle "viewer": angemeldet, aber ohne `hosts.execute`."""
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="leser", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": "leser", "password": "whatever123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_route_with_a_permission_answers_the_same_under_the_old_address(client, db_session, test_settings):
    """Eine Route mit `permission=` verlangt die Berechtigung unter der alten Kennung genauso wie unter der
    heutigen: ohne Anmeldung 401, ohne die Berechtigung 403 (gleiche Antwort), mit ihr 200. Beide Adressen
    sind dieselben Route-Objekte, die Pruefung haengt nicht an der Kennung."""
    _write_ext(test_settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"], extras=True)
    db_session.add(_row("old-ext", state="enabled", settings={"ziel": "pve1"}))
    await db_session.commit()
    await _boot(db_session, test_settings)
    owner = {"Authorization": f"Bearer {await _owner_token(client)}"}
    viewer = await _viewer_headers(client, db_session)

    answers = {}
    for ext_id in ("renamed-ext", "old-ext"):
        url = f"/api/v1/ext/{ext_id}/secure/geheim"
        anonymous = await client.get(url)
        denied = await client.get(url, headers=viewer)
        allowed = await client.get(url, headers=owner)
        answers[ext_id] = (anonymous.status_code, denied.status_code, denied.json(), allowed.status_code, allowed.json())
    assert answers["renamed-ext"] == answers["old-ext"]
    anonymous_status, denied_status, denied_body, allowed_status, allowed_body = answers["old-ext"]
    assert (anonymous_status, denied_status, allowed_status) == (401, 403, 200)
    assert "hosts.execute" in denied_body["detail"] and allowed_body == {"geheim": True}


async def _live(ws_base: str, ext_id: str, token: str):
    """Die Antwort des WebSockets der Test-Erweiterung, oder der Schliess-Code, wenn sie ihn ablehnt."""
    import websockets
    from websockets.exceptions import ConnectionClosed

    async with websockets.connect(f"{ws_base}/api/v1/ext/{ext_id}/ws/live") as ws:
        await ws.send(json.dumps({"token": token}))
        try:
            return json.loads(await ws.recv())
        except ConnectionClosed as closed:
            return closed.rcvd.code


async def _hub_login(http_base: str) -> str:
    from httpx import AsyncClient

    async with AsyncClient(base_url=http_base) as ac:
        await ac.post(
            "/api/v1/auth/bootstrap",
            json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
        )
        login = await ac.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]


@pytest.mark.asyncio
async def test_websockets_work_under_the_old_address_and_old_channels(running_app, db_session, test_settings):
    """Erweiterungen bieten WebSockets auf zwei Wegen an, beide gelten nach einer Umbenennung weiter:

    1. Eigene WebSocket-Route (`ctx.api.include_router(..., public=True)`, selbst abgesichert): laeuft wie jede
       Route ueber `mount_router`, steht also unter der heutigen und der alten Kennung. Die Absicherung der
       Erweiterung (hier: ein Wert aus ihren Einstellungen) wirkt unter beiden gleich; die Einstellungen kommen
       aus der Zeile der alten Kennung.
    2. `ctx.ws.broadcast()` an den Kern-Hub (`/api/v1/ws`, Anmeldung mit Zugangs-Token): die Nachricht geht auf
       `ext.<heutige>.<kanal>` und auf `ext.<alte>.<kanal>` -- eine Seite, die ihren Kanal noch unter der alten
       Kennung abonniert hat, bekommt sie weiter."""
    import websockets

    http_base, ws_base = running_app
    _write_ext(test_settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"], extras=True)
    db_session.add(_row("old-ext", state="enabled", settings={"ziel": "pve1", "wert": 1}))
    await db_session.commit()
    await _boot(db_session, test_settings)
    token = await _hub_login(http_base)

    for ext_id in ("renamed-ext", "old-ext"):
        reply = await _live(ws_base, ext_id, "pve1")
        assert reply == {"pfad": f"/api/v1/ext/{ext_id}/ws/live", "settings": {"ziel": "pve1", "wert": 1}}
        assert await _live(ws_base, ext_id, "falsch") == 4401

    ctx = get_extension_runtime().loaded["renamed-ext"].ctx
    async with websockets.connect(f"{ws_base}/api/v1/ws") as hub:
        await hub.send(json.dumps({"type": "auth", "token": token}))
        assert json.loads(await hub.recv()) == {"type": "auth_ok"}
        for channel in ("ext.renamed-ext.tick", "ext.old-ext.tick"):
            await hub.send(json.dumps({"type": "subscribe", "channel": channel}))
            assert json.loads(await hub.recv()) == {"type": "subscribed", "channel": channel}

        await ctx.ws.broadcast("tick", {"n": 1})

        received = []
        for _ in range(2):  # kurze Frist: fehlt eine Nachricht, soll der Test schnell rot werden
            try:
                received.append(json.loads(await asyncio.wait_for(hub.recv(), timeout=3)))
            except TimeoutError:
                break
    assert sorted((m["channel"], m["payload"]) for m in received) == [
        ("ext.old-ext.tick", {"n": 1}), ("ext.renamed-ext.tick", {"n": 1}),
    ]


@pytest.mark.asyncio
async def test_broadcast_without_legacy_ids_goes_to_one_channel_only(running_app, db_session, test_settings):
    import websockets

    from nodvard_deck.main import app

    http_base, ws_base = running_app
    _write_ext(test_settings.extensions_dir, "plain-ext", extras=True)
    await _boot(db_session, test_settings)
    await extensions_service.enable_extension(app, db_session, test_settings, "plain-ext")
    await db_session.commit()
    token = await _hub_login(http_base)
    ctx = get_extension_runtime().loaded["plain-ext"].ctx

    async with websockets.connect(f"{ws_base}/api/v1/ws") as hub:
        await hub.send(json.dumps({"type": "auth", "token": token}))
        await hub.recv()
        await hub.send(json.dumps({"type": "subscribe", "channel": "ext.plain-ext.tick"}))
        await hub.recv()

        await ctx.ws.broadcast("tick", {"n": 1})

        assert json.loads(await hub.recv())["channel"] == "ext.plain-ext.tick"
        await hub.send(json.dumps({"type": "ping"}))
        assert json.loads(await hub.recv()) == {"type": "pong"}  # keine zweite Nachricht davor


# -- ohne legacy_ids: alles wie vorher ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extension_without_legacy_ids_keeps_its_addresses_and_answers(client, db_session, test_settings):
    _write_ext(test_settings.extensions_dir, "plain-ext")
    await _boot(db_session, test_settings)
    headers = {"Authorization": f"Bearer {await _owner_token(client)}"}
    before = set(_openapi_paths())

    r = await client.post("/api/v1/extensions/plain-ext/enable", headers=headers)
    assert r.status_code == 200, r.text
    assert (r.json()["id"], r.json()["legacy_ids"]) == ("plain-ext", [])
    runtime = get_extension_runtime()
    paths = _openapi_paths()
    assert set(paths) - before == {"/api/v1/ext/plain-ext/hook/ping", "/api/v1/ext/plain-ext/x"}
    assert not [p for p in paths if p.startswith("/api/v1/ext/") and paths[p].get("get", {}).get("deprecated")]
    assert extensions_service.public_route_prefixes("plain-ext") == ["/api/v1/ext/plain-ext/hook"]
    assert runtime.legacy_ids_of("plain-ext") == []
    assert await extensions_service.is_stale_twin_address(db_session, "plain-ext") is False

    listed = (await client.get("/api/v1/extensions", headers=headers)).json()
    assert [(e["id"], e["legacy_ids"], e["has_settings"]) for e in listed] == [("plain-ext", [], True)]
    assert [p["legacy_ext_ids"] for p in (await client.get("/api/v1/pages", headers=headers)).json()] == [[]]
    assert [w["legacy_ext_ids"] for w in (await client.get("/api/v1/widgets", headers=headers)).json()] == [[]]
    r = await client.put("/api/v1/extensions/plain-ext/settings", headers=headers, json={"values": {"ziel": "pve1"}})
    assert r.status_code == 200 and r.json()["values"] == {"ziel": "pve1"}, r.text
    assert (await client.get("/api/v1/extensions/old-ext", headers=headers)).status_code == 404
    assert (await client.post("/api/v1/extensions/plain-ext/disable", headers=headers)).json()["state"] == "disabled"
    assert set(_openapi_paths()) == before
