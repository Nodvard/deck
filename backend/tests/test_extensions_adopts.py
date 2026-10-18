"""Übernahme eines Teils aus einem anderen Modul (`adopts` im Manifest), etwa Pi-hole aus „Netzwerk“.

Beim allerersten Entdecken des neuen Moduls übernimmt der Kern Einstellungen, Ein/Aus und Kacheln; die Zeile des alten
Moduls bleibt unverändert (Rückweg aufs alte Image). Alte Adressen zeigen als veraltet auf die Routen des neuen Moduls.

Test-Module im tmp-Ordner: `old-src` (gibt ab) und `new-mod` (übernimmt mit `adopts`).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from nodvard_deck.core.scheduler import reset_scheduler_service
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import AuditEntry, DashboardLayout, ExtensionRecord, Setting
from nodvard_deck.services import extensions as extensions_service

SERVICE_LOGGER = "nodvard_deck.services.extensions"
UNTOUCHED = extensions_service.UNTOUCHED_KEY_PREFIX + "new-mod"


@pytest.fixture(autouse=True)
def _reset_runtime_and_sys_path(monkeypatch):
    monkeypatch.setattr(logging.getLogger(SERVICE_LOGGER), "disabled", False)
    reset_extension_runtime()
    reset_scheduler_service()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    reset_scheduler_service()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_"):
            del sys.modules[name]


_CODE = '''
from fastapi import APIRouter, HTTPException, Path


class Extension:
    async def setup(self, ctx):
        read = APIRouter()

        @read.get("/summary")
        async def summary(x: int = 1):
            return {"settings": await ctx.settings.get(), "x": x}

        act = APIRouter()

        @act.post("/hosts/{host_id}/enable")
        async def enable(host_id: int = Path(ge=1)):
            return {"host_id": host_id}

        ctx.api.include_router(read, permission="hosts.read")
        ctx.api.include_router(act, prefix="/act", permission="hosts.execute")

    async def on_start(self, ctx):
        pass

    async def on_stop(self, ctx):
        pass
'''

ADOPTS = '''
[[extension.adopts]]
from = "old-src"
settings = "teil"
widgets = { alt = "neu" }
routes = { "/teil" = "/summary", "/teil/hosts/{host_id}/an" = "/act/hosts/{host_id}/enable", "/fehlt" = "/gibt-es-nicht" }
'''


def _write_ext(root: Path, ext_id: str, extra: str = "", permissions: str = "[]", code: str = _CODE) -> None:
    module = "nodvard_deck_ext_" + ext_id.replace("-", "_")
    ext_dir = root / ext_id
    (ext_dir / "src" / module).mkdir(parents=True)
    (ext_dir / "extension.toml").write_text(
        f'[extension]\nid = "{ext_id}"\nname = "Test {ext_id}"\nversion = "0.2.0"\napi_version = "0.1"\n'
        f'entrypoint = "{module}:Extension"\npermissions = {permissions}\n{extra}',
        encoding="utf-8",
    )
    (ext_dir / "src" / module / "__init__.py").write_text(code, encoding="utf-8")


def _settings(test_settings, tmp_path):
    return test_settings.model_copy(
        update={"extensions_dir": tmp_path / "extensions", "ext_data_dir": tmp_path / "ext-data"}
    )


def _app() -> FastAPI:
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    return app


async def _boot(app, db_session, settings) -> None:
    reset_extension_runtime()
    reset_scheduler_service()
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_"):
            del sys.modules[name]
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.load_enabled_from_registry(app, db_session, settings)
    await db_session.commit()


OLD_SETTINGS = {"teil": {"url": "http://pi.hole", "tls": False}, "anderes": {"url": "http://npm:81"}}


async def _old_installation(
    db_session, *, state: str = "enabled", settings: dict | None = None, granted: list[str] | None = None,
) -> None:
    db_session.add(ExtensionRecord(
        id="old-src", version="0.1.0", api_version="0.1", state=state, source="bundled",
        settings=OLD_SETTINGS if settings is None else settings, manifest={"id": "old-src", "name": "Test old-src"},
        granted_permissions=granted or [],
    ))
    db_session.add(DashboardLayout(user_id="u1", name="Standard", is_default=True, items=[
        {"ext_id": "old-src", "widget_id": "alt", "x": 2, "y": 3, "w": 2, "h": 2, "config": {"hidden": True}},
        {"ext_id": "old-src", "widget_id": "bleibt", "x": 0, "y": 0, "w": 2, "h": 2, "config": {}},
        {"ext_id": "fremd", "widget_id": "alt", "x": 4, "y": 0, "w": 2, "h": 2, "config": {}},
    ]))
    db_session.add(DashboardLayout(user_id="u2", name="Zweites", is_default=False, items=[
        {"ext_id": "old-src", "widget_id": "alt", "x": 0, "y": 0, "w": 2, "h": 2, "config": {}},
        {"ext_id": "new-mod", "widget_id": "neu", "x": 6, "y": 0, "w": 2, "h": 2, "config": {}},
    ]))
    await db_session.commit()


async def _row(db_session, ext_id: str) -> ExtensionRecord | None:
    await db_session.commit()
    db_session.expire_all()
    return await db_session.get(ExtensionRecord, ext_id)


async def _layouts(db_session) -> dict[str, list]:
    await db_session.commit()
    db_session.expire_all()
    return {r.user_id: r.items for r in (await db_session.execute(select(DashboardLayout))).scalars()}


async def _untouched(db_session) -> bool:
    return await db_session.get(Setting, (UNTOUCHED, "global", "")) is not None


async def _adopted_entries(db_session) -> list[dict]:
    rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "extension.adopted"))).scalars().all()
    return [r.detail for r in rows]


@pytest.mark.asyncio
async def test_the_new_module_takes_over_settings_state_and_tiles_once(tmp_path, db_session, test_settings):
    _write_ext(tmp_path / "extensions", "old-src")
    _write_ext(tmp_path / "extensions", "new-mod", ADOPTS)
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session)

    await _boot(_app(), db_session, settings)

    new = await _row(db_session, "new-mod")
    assert new.state == "enabled" and new.settings == OLD_SETTINGS["teil"]
    assert new.manifest["adopts"][0]["from"] == "old-src" and "from_" not in new.manifest["adopts"][0]
    old = await _row(db_session, "old-src")
    assert old.state == "enabled" and old.settings == OLD_SETTINGS, "die alte Zeile bleibt, wie sie war"
    assert not await _untouched(db_session), "Ein/Aus kam aus dem alten Modul, das war eine Wahl"
    assert await get_extension_runtime().loaded["new-mod"].ctx.settings.get() == OLD_SETTINGS["teil"]
    layouts = await _layouts(db_session)
    assert layouts["u1"] == [
        {"ext_id": "new-mod", "widget_id": "neu", "x": 2, "y": 3, "w": 2, "h": 2, "config": {"hidden": True}},
        {"ext_id": "old-src", "widget_id": "bleibt", "x": 0, "y": 0, "w": 2, "h": 2, "config": {}},
        {"ext_id": "fremd", "widget_id": "alt", "x": 4, "y": 0, "w": 2, "h": 2, "config": {}},
    ]
    assert layouts["u2"] == [{"ext_id": "new-mod", "widget_id": "neu", "x": 6, "y": 0, "w": 2, "h": 2, "config": {}}], (
        "die neue Kachel steht schon da: keine zweite"
    )
    assert await _adopted_entries(db_session) == [
        {"from": "old-src", "settings": True, "enabled": True, "widgets": 1, "secrets": 0, "extra_permissions": []},
    ]

    # Ein zweiter Start übernimmt nichts mehr, auch wenn sich das alte Modul geändert hat.
    old.settings = {"teil": {"url": "http://anders"}}
    await db_session.commit()
    await _boot(_app(), db_session, settings)
    assert (await _row(db_session, "new-mod")).settings == OLD_SETTINGS["teil"]
    assert len(await _adopted_entries(db_session)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "section", "enabled"), [
    ("enabled", {"url": "", "tls": False}, False),
    ("enabled", None, False),
    ("disabled", {"url": "http://pi.hole"}, False),
    ("error", {"url": "http://pi.hole"}, False),
    ("enabled", {"url": "", "notify": True}, False),
    ("enabled", {"url": "", "notify": False}, False),
    ("enabled", {"url": "http://pi.hole"}, True),
], ids=["nur-leere-felder", "ohne-abschnitt", "alt-aus", "alt-fehler", "nur-vorgaben", "vorgabe-abgeschaltet", "alt-an"])
async def test_switched_on_only_when_the_old_module_was_on_and_set_up(tmp_path, db_session, test_settings, state, section, enabled):
    _write_ext(tmp_path / "extensions", "old-src")
    _write_ext(tmp_path / "extensions", "new-mod", ADOPTS + '\n[settings]\nschema = "settings.schema.json"\n')
    (tmp_path / "extensions" / "new-mod" / "settings.schema.json").write_text(json.dumps({
        "type": "object",
        "properties": {"url": {"type": "string", "default": ""}, "notify": {"type": "boolean", "default": True}},
    }), encoding="utf-8")
    old_settings = {"anderes": {"url": "x"}} | ({"teil": section} if section is not None else {})
    await _old_installation(db_session, state=state, settings=old_settings)

    await _boot(_app(), db_session, _settings(test_settings, tmp_path))

    new = await _row(db_session, "new-mod")
    assert (new.state == "enabled") is enabled
    assert new.settings == (section or {})
    assert (await _row(db_session, "old-src")).settings == old_settings


@pytest.mark.asyncio
async def test_vault_entries_change_owner_only_where_readable_and_owned(tmp_path, db_session, test_settings, caplog):
    from nodvard_deck.core import vault
    from nodvard_deck.models import Secret

    _write_ext(tmp_path / "extensions", "old-src")
    _write_ext(
        tmp_path / "extensions", "new-mod",
        '[[extension.adopts]]\nfrom = "old-src"\nsecrets = ["old-teil-pw", "old-fremd-pw", "old-geheim", "old-fehlt"]\n',
        permissions='["secrets.read:old-teil-*", "secrets.read:old-fremd-pw"]',
    )
    await _old_installation(db_session)
    keyring = vault.load_keyring(test_settings)
    for label, owner in (("old-teil-pw", "old-src"), ("old-fremd-pw", "fremd"), ("old-geheim", "old-src")):
        await vault.create_secret(db_session, keyring, label=label, kind="generic", plaintext="x", owner_ext_id=owner)
    await db_session.commit()

    await _boot(_app(), db_session, _settings(test_settings, tmp_path))

    db_session.expire_all()
    owners = {s.label: s.owner_ext_id for s in (await db_session.execute(select(Secret))).scalars()}
    assert owners == {"old-teil-pw": "new-mod", "old-fremd-pw": "fremd", "old-geheim": "old-src"}
    assert (await _adopted_entries(db_session))[0]["secrets"] == 1
    assert "übernimmt Tresor-Einträge von 'old-src' nicht" in caplog.text and "Anzahl: 2," in caplog.text
    assert "old-geheim" not in caplog.text


@pytest.mark.asyncio
async def test_a_new_installation_takes_over_nothing(tmp_path, db_session, test_settings):
    _write_ext(tmp_path / "extensions", "new-mod", ADOPTS)

    await _boot(_app(), db_session, _settings(test_settings, tmp_path))

    new = await _row(db_session, "new-mod")
    assert new.state == "disabled" and new.settings == {}
    assert await _untouched(db_session), "ohne altes Modul ganz normal neu und unberührt"
    assert await _adopted_entries(db_session) == []


@pytest.mark.asyncio
async def test_on_a_fresh_installation_both_modules_are_simply_new(tmp_path, db_session, test_settings):
    """Beide Module werden im selben Start zum ersten Mal entdeckt: Es gibt nichts zu übernehmen."""
    _write_ext(tmp_path / "extensions", "old-src")
    _write_ext(tmp_path / "extensions", "new-mod", ADOPTS)

    await _boot(_app(), db_session, _settings(test_settings, tmp_path))

    new = await _row(db_session, "new-mod")
    assert new.state == "disabled" and new.settings == {}
    assert await _untouched(db_session)
    assert await _adopted_entries(db_session) == []


@pytest.mark.asyncio
async def test_back_to_the_old_image_finds_the_old_module_as_it_was(tmp_path, db_session, test_settings):
    """Rückweg: Das alte Image kennt das neue Modul nicht. Die Zeile des alten Moduls ist unverändert und läuft weiter."""
    root = tmp_path / "extensions"
    _write_ext(root, "old-src")
    _write_ext(root, "new-mod", ADOPTS)
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session)
    await _boot(_app(), db_session, settings)

    import shutil

    shutil.rmtree(root / "new-mod")
    await _boot(_app(), db_session, settings)

    old = await _row(db_session, "old-src")
    assert old.state == "enabled" and old.settings == OLD_SETTINGS
    assert "old-src" in get_extension_runtime().loaded and "new-mod" not in get_extension_runtime().loaded


def _client(app: FastAPI) -> AsyncClient:
    """Angemeldet als Inhaber (darf alles)."""
    from types import SimpleNamespace

    from nodvard_deck.api.deps import get_current_user

    owner = SimpleNamespace(id="u1", username="deck", is_owner=True, is_active=True)
    app.dependency_overrides[get_current_user] = lambda: owner
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_old_addresses_lead_to_the_new_module(tmp_path, db_session, test_settings, caplog):
    _write_ext(tmp_path / "extensions", "old-src")
    _write_ext(tmp_path / "extensions", "new-mod", ADOPTS)
    settings = _settings(test_settings, tmp_path)
    app = _app()
    await _boot(app, db_session, settings)
    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        await extensions_service.enable_extension(app, db_session, settings, "new-mod")
    await db_session.commit()
    assert any("/fehlt" in r.getMessage() for r in caplog.records), "eine Zuordnung ohne eigene Route wird gemeldet"

    paths = app.openapi()["paths"]
    assert paths["/api/v1/ext/old-src/teil"]["get"]["deprecated"] is True
    assert paths["/api/v1/ext/old-src/teil/hosts/{host_id}/an"]["post"]["deprecated"] is True
    assert not paths["/api/v1/ext/new-mod/summary"]["get"].get("deprecated")
    assert "/api/v1/ext/old-src/fehlt" not in paths

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        assert (await ac.get("/api/v1/ext/old-src/teil")).status_code == 401, "dieselbe Anmeldeprüfung"
    async with _client(app) as ac:
        new = await ac.get("/api/v1/ext/new-mod/summary?x=3")
        old = await ac.get("/api/v1/ext/old-src/teil?x=3")
        assert new.status_code == old.status_code == 200 and old.json() == new.json() == {"settings": {}, "x": 3}
        assert (await ac.post("/api/v1/ext/old-src/teil/hosts/7/an")).json() == {"host_id": 7}
        assert (await ac.post("/api/v1/ext/old-src/teil/hosts/0/an")).status_code == 422, "dieselbe Prüfung der Angaben"

    await extensions_service.disable_extension(app, db_session, "new-mod")
    app.openapi_schema = None
    assert not [p for p in app.openapi()["paths"] if p.startswith("/api/v1/ext/old-src/")], "Ausschalten nimmt sie mit"


@pytest.mark.asyncio
async def test_the_permission_of_the_route_counts_at_the_old_address(tmp_path, db_session, test_settings, monkeypatch):
    """Die Weiterleitung hat dieselbe Rechteprüfung wie die Route des neuen Moduls (`permission=`)."""
    from nodvard_deck.api import deps

    _write_ext(tmp_path / "extensions", "old-src")
    _write_ext(tmp_path / "extensions", "new-mod", ADOPTS)
    settings = _settings(test_settings, tmp_path)
    app = _app()
    await _boot(app, db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "new-mod")
    await db_session.commit()

    # Jemand, der nur lesen darf.
    monkeypatch.setattr(deps, "user_has_permission", lambda user, permission: permission == "hosts.read")
    async with _client(app) as ac:
        assert (await ac.get("/api/v1/ext/old-src/teil")).status_code == 200
        new = await ac.post("/api/v1/ext/new-mod/act/hosts/7/enable")
        old = await ac.post("/api/v1/ext/old-src/teil/hosts/7/an")
    assert new.status_code == old.status_code == 403


@pytest.mark.asyncio
async def test_modules_without_adopts_store_no_adopts(tmp_path, db_session, test_settings):
    _write_ext(tmp_path / "extensions", "old-src")
    await _boot(_app(), db_session, _settings(test_settings, tmp_path))
    assert "adopts" not in (await _row(db_session, "old-src")).manifest


# --- Nur unter mitgelieferten Modulen, nie mehr Rechte, nie vor die Quelle -------------------------------------------


def _as_pip(monkeypatch, tmp_path, *ext_ids: str) -> None:
    """Diese Funde kommen wie nachinstallierte Pakete (pip), nicht aus dem Ordner der mitgelieferten Module."""
    import dataclasses

    real = extensions_service.discover_all

    def discover(*args, **kwargs):
        return [dataclasses.replace(d, source="pip") if d.id in ext_ids else d for d in real(*args, **kwargs)]

    monkeypatch.setattr(extensions_service, "discover_all", discover)
    for ext_id in ext_ids:  # ein pip-Paket liegt schon auf dem Suchpfad
        sys.path.insert(0, str(tmp_path / "extensions" / ext_id / "src"))


@pytest.mark.asyncio
@pytest.mark.parametrize("pip", ["new-mod", "old-src"], ids=["paket-uebernimmt", "paket-gibt-ab"])
async def test_installed_packages_take_over_nothing(tmp_path, db_session, test_settings, monkeypatch, pip):
    """Bisher galt `adopts` für jedes Manifest: Ein nachinstalliertes Paket mit `from = "<beliebiges Modul>"` bekam
    dessen Einstellungen, Kacheln und Tresor-Einträge, wurde eingeschaltet und hängte sich unter dessen Adressen."""
    from nodvard_deck.core import vault
    from nodvard_deck.models import Secret

    root = tmp_path / "extensions"
    _write_ext(root, "old-src")
    _write_ext(root, "new-mod", ADOPTS + 'secrets = ["old-teil-pw"]\n', permissions='["secrets.read:old-teil-*"]')
    _as_pip(monkeypatch, tmp_path, pip)
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session)
    await vault.create_secret(db_session, vault.load_keyring(test_settings), label="old-teil-pw", kind="generic",
                              plaintext="x", owner_ext_id="old-src")
    await db_session.commit()
    app = _app()

    await _boot(app, db_session, settings)

    new = await _row(db_session, "new-mod")
    assert new.state == "disabled" and new.settings == {}
    assert await _untouched(db_session) and await _adopted_entries(db_session) == []
    assert (await _layouts(db_session))["u1"][0]["ext_id"] == "old-src"
    db_session.expire_all()
    assert (await db_session.execute(select(Secret.owner_ext_id))).scalar_one() == "old-src"

    await extensions_service.enable_extension(app, db_session, settings, "new-mod")
    await db_session.commit()
    paths = app.openapi()["paths"]
    assert "/api/v1/ext/new-mod/summary" in paths
    assert not [p for p in paths if p.startswith("/api/v1/ext/old-src/teil")], "keine alten Adressen"


@pytest.mark.asyncio
@pytest.mark.parametrize("twin", ["new-mod", "old-src"], ids=["zwilling-uebernimmt", "zwilling-gibt-ab"])
async def test_a_package_with_the_id_of_a_bundled_module_is_not_loaded(
    tmp_path, db_session, test_settings, monkeypatch, caplog, twin,
):
    """Ein pip-Paket mit derselben Kennung wie ein mitgeliefertes Modul überschrieb nach dem Ordner-Scan Fund und Zeile:
    Es war danach eingeschaltet, mit den Einstellungen und Rechten, die das mitgelieferte Modul übernommen hatte."""
    import dataclasses

    root = tmp_path / "extensions"
    _write_ext(root, "old-src")
    _write_ext(root, "new-mod", ADOPTS)
    real = extensions_service.discover_all

    def discover(*args, **kwargs):
        found = real(*args, **kwargs)
        return [*found, *(dataclasses.replace(d, source="pip", source_path=tmp_path / "site") for d in found if d.id == twin)]

    monkeypatch.setattr(extensions_service, "discover_all", discover)
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session, granted=["hosts.read", "hosts.execute"])
    caplog.set_level(logging.WARNING, logger=SERVICE_LOGGER)

    await _boot(_app(), db_session, settings)

    # Es gilt der mitgelieferte Fund; das Paket wird mit Warnung verworfen.
    assert {ext_id: d.source for ext_id, d in get_extension_runtime().discovered.items()} == {
        "old-src": "bundled", "new-mod": "bundled",
    }
    assert (await _row(db_session, twin)).source == "bundled"
    assert f"Erweiterung '{twin}' (pip" in caplog.text and "Die Kennung gibt es schon (bundled" in caplog.text
    # Die Übernahme unter den mitgelieferten Modulen läuft wie ohne Zwilling.
    new = await _row(db_session, "new-mod")
    assert new.state == "enabled" and new.settings == OLD_SETTINGS["teil"]


_SERVES_TEIL = _CODE.replace('@read.get("/summary")', '@read.get("/teil")').replace(
    'return {"settings": await ctx.settings.get(), "x": x}', 'return {"von": "old-src"}',
)


@pytest.mark.asyncio
async def test_an_address_the_loaded_source_serves_itself_stays_with_it(tmp_path, db_session, test_settings, caplog):
    """Bedient die geladene Quelle eine alte Adresse noch selbst, hängt sich das neue Modul nicht davor (die zuletzt
    eingehängten Routen stehen vorn)."""
    root = tmp_path / "extensions"
    _write_ext(root, "old-src", code=_SERVES_TEIL)
    _write_ext(root, "new-mod", ADOPTS)
    settings = _settings(test_settings, tmp_path)
    await _old_installation(db_session)
    app = _app()
    await _boot(app, db_session, settings)
    assert {"old-src", "new-mod"} <= set(get_extension_runtime().loaded)
    # Das neue Modul nach der Quelle laden: so lägen seine Weiterleitungen vor ihren Routen.
    await extensions_service.disable_extension(app, db_session, "new-mod")
    with caplog.at_level(logging.WARNING, logger=SERVICE_LOGGER):
        await extensions_service.enable_extension(app, db_session, settings, "new-mod")
    await db_session.commit()
    assert any("bedient '/teil' selbst" in r.getMessage() for r in caplog.records)

    async with _client(app) as ac:
        assert (await ac.get("/api/v1/ext/old-src/teil")).json() == {"von": "old-src"}
        assert (await ac.post("/api/v1/ext/old-src/teil/hosts/7/an")).json() == {"host_id": 7}, "die übrige bleibt"


@pytest.mark.asyncio
@pytest.mark.parametrize(("granted", "enabled", "extra"), [
    ([], False, ["hosts.read", "hosts.execute"]),
    (["hosts.read"], False, ["hosts.execute"]),
    (["hosts.read", "hosts.execute"], True, []),
    (["*"], True, []),
])
async def test_switched_on_only_with_rights_the_old_module_had(tmp_path, db_session, test_settings, granted, enabled, extra):
    """Eingeschaltet wird das neue Modul nur, wenn die Quelle schon alle seine Rechte hatte; sonst übernimmt es
    Einstellungen und Kacheln, bleibt aber aus, bis es jemand bewusst einschaltet."""
    _write_ext(tmp_path / "extensions", "old-src")
    _write_ext(tmp_path / "extensions", "new-mod", ADOPTS, permissions='["hosts.read", "hosts.execute"]')
    await _old_installation(db_session, granted=granted)

    await _boot(_app(), db_session, _settings(test_settings, tmp_path))

    new = await _row(db_session, "new-mod")
    assert (new.state == "enabled") is enabled and new.settings == OLD_SETTINGS["teil"]
    assert (await _adopted_entries(db_session))[0]["extra_permissions"] == extra
    assert not await _untouched(db_session)
