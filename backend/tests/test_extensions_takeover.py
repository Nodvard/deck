"""Alte Kennung einer entfernten Erweiterung (`legacy_ids`): Übernahme ihrer Daten erst mit Erlaubnis eines Admins.

Ohne Erlaubnis bekäme eine nachinstallierte Erweiterung die Zeile der entfernten samt Zustand, Einstellungen und
Rechten; war die eingeschaltet, liefe sie beim nächsten Start mit allen Rechten aus ihrem eigenen Manifest.
"""

from __future__ import annotations

import dataclasses
import shutil
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from nodvard_deck.ext.discovery import DiscoveredExtension
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import AuditEntry, ExtensionRecord, Setting
from nodvard_deck.services import extensions as extensions_service
from sqlalchemy import select

_CODE = """
class Extension:
    async def setup(self, ctx):
        pass

    async def on_start(self, ctx):
        pass

    async def on_stop(self, ctx):
        pass
"""


@pytest.fixture(autouse=True)
def _clean_runtime():
    reset_extension_runtime()
    before = list(sys.path)
    yield
    reset_extension_runtime()
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_"):
            del sys.modules[name]


def _write_ext(
    root: Path, ext_id: str, *, legacy_ids: list[str], permissions: list[str] | None = None, enable_on: str = "",
) -> None:
    module = "nodvard_deck_ext_" + ext_id.replace("-", "_")
    ext_dir = root / ext_id
    (ext_dir / "src" / module).mkdir(parents=True)
    granted = ", ".join(f'"{p}"' for p in permissions or ["schedule.register"])
    old = ", ".join(f'"{x}"' for x in legacy_ids)
    (ext_dir / "extension.toml").write_text(
        f'[extension]\nid = "{ext_id}"\nname = "Neu {ext_id}"\nversion = "0.2.0"\napi_version = "0.1"\n'
        f'entrypoint = "{module}:Extension"\npermissions = [{granted}]\nlegacy_ids = [{old}]\n{enable_on}',
        encoding="utf-8",
    )
    (ext_dir / "src" / module / "__init__.py").write_text(_CODE, encoding="utf-8")


def _as_pip(monkeypatch, root: Path, *ext_ids: str) -> None:
    real = extensions_service.discover_all

    def discover(*args, **kwargs):
        return [dataclasses.replace(d, source="pip") if d.id in ext_ids else d for d in real(*args, **kwargs)]

    monkeypatch.setattr(extensions_service, "discover_all", discover)
    for ext_id in ext_ids:  # ein Paket liegt schon auf dem Suchpfad
        sys.path.insert(0, str(root / ext_id / "src"))


def _old_row(
    row_id: str = "old-ext", *, source: str = "pip", state: str = "enabled", manifest: dict | None = None,
    granted: list[str] | None = None,
) -> ExtensionRecord:
    return ExtensionRecord(
        id=row_id, version="0.1.0", api_version="0.1", state=state, source=source, settings={"wert": 1},
        manifest=manifest or {"id": row_id, "name": "Alt"},
        granted_permissions=["schedule.register"] if granted is None else granted,
    )


@pytest.fixture
def settings(test_settings, tmp_path):
    return test_settings.model_copy(
        update={"extensions_dir": tmp_path / "extensions", "ext_data_dir": tmp_path / "ext-data"}
    )


async def _boot(db_session, settings) -> FastAPI:
    reset_extension_runtime()
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_"):
            del sys.modules[name]
    app = FastAPI()
    app.state.ext_mount_index = len(app.router.routes)
    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.load_enabled_from_registry(app, db_session, settings)
    await db_session.commit()
    return app


async def _stored(db_session, row_id: str) -> ExtensionRecord:
    await db_session.commit()
    db_session.expire_all()
    record = await db_session.get(ExtensionRecord, row_id)
    assert record is not None, row_id
    return record


async def _row_ids(db_session) -> list[str]:
    return sorted((await db_session.execute(select(ExtensionRecord.id))).scalars().all())


@pytest.mark.asyncio
async def test_a_package_waits_for_consent_before_it_takes_over(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()

    app = await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert "renamed-ext" not in runtime.loaded
    assert runtime.legacy_owner == {}
    assert set(runtime.awaiting_consent) == {("renamed-ext", "old-ext")}
    assert "erst, wenn ein Admin das" in runtime.refused["renamed-ext"]
    assert await _row_ids(db_session) == ["old-ext"]
    row = await _stored(db_session, "old-ext")
    assert row.state == "enabled" and row.settings == {"wert": 1}
    assert row.last_error.startswith("„renamed-ext“ soll diese Erweiterung ersetzen")
    assert "beim nächsten Start wieder geladen" not in row.last_error
    assert [(e, o) for e, o, *_ in extensions_service.takeover_requests(row)] == [("renamed-ext", "old-ext")]

    assert await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1") == []
    await db_session.commit()
    assert not get_extension_runtime().awaiting_consent
    audit = (await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "extension.takeover_allowed")
    )).scalar_one()
    assert (audit.actor_id, audit.target_id) == ("u1", "renamed-ext")
    assert audit.detail == {
        "from": "old-ext", "source": "pip", "permissions": ["schedule.register"], "extra_permissions": [],
        "switched_off": False,
    }

    await _boot(db_session, settings)
    runtime = get_extension_runtime()
    assert runtime.loaded["renamed-ext"].store_id == "old-ext"
    assert runtime.legacy_owner == {"old-ext": "renamed-ext"}
    assert not runtime.awaiting_consent
    row = await _stored(db_session, "old-ext")
    assert row.state == "enabled" and row.last_error is None and row.settings == {"wert": 1}
    await extensions_service.disable_extension(app, db_session, "renamed-ext")


@pytest.mark.asyncio
async def test_more_rights_than_the_removed_extension_had_keep_it_off(db_session, settings, monkeypatch):
    _write_ext(
        settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"],
        permissions=["schedule.register", "hosts.execute"],
    )
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()
    await _boot(db_session, settings)

    extra = await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")

    assert extra == ["hosts.execute"]
    row = await _stored(db_session, "old-ext")
    assert row.state == "disabled" and row.last_error is None
    await _boot(db_session, settings)
    assert "renamed-ext" not in get_extension_runtime().loaded
    assert get_extension_runtime().store_ids["renamed-ext"] == "old-ext"
    assert (await _stored(db_session, "old-ext")).state == "disabled"


@pytest.mark.asyncio
async def test_a_rename_among_bundled_modules_needs_no_consent(db_session, settings):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    db_session.add(_old_row(source="bundled"))
    await db_session.commit()

    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert not runtime.awaiting_consent
    assert runtime.loaded["renamed-ext"].store_id == "old-ext"


@pytest.mark.asyncio
async def test_a_bundled_module_taking_over_a_package_waits_too(db_session, settings):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    db_session.add(_old_row(source="pip"))
    await db_session.commit()

    await _boot(db_session, settings)

    assert set(get_extension_runtime().awaiting_consent) == {("renamed-ext", "old-ext")}
    assert "renamed-ext" not in get_extension_runtime().loaded


@pytest.mark.asyncio
async def test_without_data_under_the_old_id_nothing_waits(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")

    for _ in range(2):  # beim zweiten Start trägt die eigene Zeile die alte Kennung im Manifest
        await _boot(db_session, settings)
        runtime = get_extension_runtime()
        assert not runtime.awaiting_consent and "renamed-ext" not in runtime.refused
        assert runtime.legacy_owner == {"old-ext": "renamed-ext"}
        assert await _row_ids(db_session) == ["renamed-ext"]


@pytest.mark.asyncio
async def test_an_old_id_only_named_in_another_rows_manifest_waits(db_session, settings, monkeypatch):
    # Die entfernte hieß selbst schon einmal anders: Ihre Zeile trägt die frühere Kennung, das Manifest die spätere.
    _write_ext(settings.extensions_dir, "intruder", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "intruder")
    db_session.add(_old_row("older-ext", manifest={"id": "old-ext", "name": "Alt", "legacy_ids": ["older-ext"]}))
    await db_session.commit()

    await _boot(db_session, settings)

    assert set(get_extension_runtime().awaiting_consent) == {("intruder", "old-ext")}
    row = await _stored(db_session, "older-ext")
    assert [e for e, *_ in extensions_service.takeover_requests(row)] == ["intruder"]


@pytest.mark.asyncio
async def test_a_waiting_package_does_not_push_out_the_bundled_owner(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _write_ext(settings.extensions_dir, "intruder", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "intruder")
    db_session.add(_old_row(source="bundled"))
    await db_session.commit()

    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert runtime.loaded["renamed-ext"].store_id == "old-ext"
    assert runtime.legacy_owner == {"old-ext": "renamed-ext"}
    assert set(runtime.awaiting_consent) == {("intruder", "old-ext")}
    assert extensions_service.takeover_requests(await _stored(db_session, "old-ext")) == []
    with pytest.raises(extensions_service.TakeoverConflict, match="„old-ext“ gehört schon zu „renamed-ext“"):
        await extensions_service.allow_takeover(db_session, "intruder", "old-ext", user_id="u1")
    assert not (await db_session.execute(select(Setting.key).where(Setting.key.contains("takeover")))).all()


@pytest.mark.asyncio
async def test_consent_is_only_for_a_waiting_pair(db_session, settings):
    await _boot(db_session, settings)
    with pytest.raises(extensions_service.TakeoverError, match="wartet nicht"):
        await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")


async def _consent_keys(db_session) -> list[str]:
    await db_session.commit()
    return sorted((await db_session.execute(select(Setting.key).where(Setting.key.contains("takeover")))).scalars())


@pytest.mark.asyncio
async def test_a_package_with_the_id_of_a_removed_bundled_module_waits(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "old-ext", legacy_ids=[], permissions=["schedule.register", "hosts.execute"])
    _as_pip(monkeypatch, settings.extensions_dir, "old-ext")
    db_session.add(_old_row(source="bundled"))
    await db_session.commit()

    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert "old-ext" not in runtime.loaded
    assert set(runtime.awaiting_consent) == {("old-ext", "old-ext")}
    row = await _stored(db_session, "old-ext")
    assert row.state == "enabled" and row.source == "bundled"
    assert [(e, o) for e, o, *_ in extensions_service.takeover_requests(row)] == [("old-ext", "old-ext")]

    assert await extensions_service.allow_takeover(db_session, "old-ext", "old-ext", user_id="u1") == ["hosts.execute"]
    await _boot(db_session, settings)
    row = await _stored(db_session, "old-ext")
    assert get_extension_runtime().store_ids["old-ext"] == "old-ext" and row.source == "pip"
    assert row.state == "disabled" and not get_extension_runtime().awaiting_consent


@pytest.mark.asyncio
async def test_a_package_with_the_old_id_of_a_bundled_module_does_not_push_it_out(db_session, settings, monkeypatch):
    # Wie ein Paket `service-matrix` neben dem mitgelieferten `docker`.
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _write_ext(settings.extensions_dir, "old-ext", legacy_ids=[])
    _as_pip(monkeypatch, settings.extensions_dir, "old-ext")
    db_session.add(_old_row(source="bundled", manifest={"id": "renamed-ext", "legacy_ids": ["old-ext"]}))
    await db_session.commit()

    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert runtime.loaded["renamed-ext"].store_id == "old-ext"
    assert "old-ext" not in runtime.loaded and set(runtime.awaiting_consent) == {("old-ext", "old-ext")}
    with pytest.raises(extensions_service.TakeoverConflict):
        await extensions_service.allow_takeover(db_session, "old-ext", "old-ext", user_id="u1")


@pytest.mark.asyncio
async def test_one_package_among_the_holders_is_enough_to_wait(db_session, settings):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    db_session.add(_old_row(source="bundled"))
    db_session.add(_old_row("other-ext", source="pip", manifest={"id": "other-ext", "legacy_ids": ["old-ext"]}))
    await db_session.commit()

    await _boot(db_session, settings)

    assert set(get_extension_runtime().awaiting_consent) == {("renamed-ext", "old-ext")}


@pytest.mark.asyncio
async def test_an_old_id_only_named_in_stored_legacy_ids_waits(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "intruder", legacy_ids=["middle-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "intruder")
    db_session.add(_old_row("first-ext", manifest={"id": "last-ext", "legacy_ids": ["middle-ext", "first-ext"]}))
    await db_session.commit()

    await _boot(db_session, settings)

    assert set(get_extension_runtime().awaiting_consent) == {("intruder", "middle-ext")}


@pytest.mark.asyncio
async def test_actions_under_an_old_id_without_any_row_wait_too(db_session, settings, monkeypatch):
    from nodvard_deck.models import Action

    _write_ext(settings.extensions_dir, "intruder", legacy_ids=["gone-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "intruder")
    db_session.add(Action(
        ext_id="gone-ext", action_type="gone.tu_was", payload={"command": "echo geheim"}, proposed_by_type="user",
        proposed_by_id="u1", reason="Test",
    ))
    await db_session.commit()

    await _boot(db_session, settings)

    assert set(get_extension_runtime().awaiting_consent) == {("intruder", "gone-ext")}


@pytest.mark.asyncio
async def test_consent_holds_only_for_the_allowed_rights(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()
    await _boot(db_session, settings)
    await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")
    await db_session.commit()

    # Vor dem Neustart ersetzt eine Fassung mit mehr Rechten das Paket.
    shutil.rmtree(settings.extensions_dir / "renamed-ext")
    _write_ext(
        settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"],
        permissions=["schedule.register", "hosts.execute"],
    )
    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert "renamed-ext" not in runtime.loaded
    assert set(runtime.awaiting_consent) == {("renamed-ext", "old-ext")}


@pytest.mark.asyncio
async def test_consent_ends_when_the_package_is_gone_at_a_start(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()
    await _boot(db_session, settings)
    await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")
    assert await _consent_keys(db_session) == ["extension.takeover_allowed.renamed-ext:old-ext"]

    moved = settings.extensions_dir.parent / "beiseite"
    shutil.move(settings.extensions_dir / "renamed-ext", moved)
    await _boot(db_session, settings)
    assert await _consent_keys(db_session) == []

    shutil.move(moved, settings.extensions_dir / "renamed-ext")
    await _boot(db_session, settings)
    assert set(get_extension_runtime().awaiting_consent) == {("renamed-ext", "old-ext")}


@pytest.mark.asyncio
async def test_with_its_own_row_nothing_is_inherited_or_switched_off(db_session, settings, monkeypatch):
    _write_ext(
        settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"],
        permissions=["schedule.register", "hosts.execute"],
    )
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    db_session.add(_old_row("renamed-ext", state="disabled", manifest={"id": "renamed-ext"}))
    await db_session.commit()
    await _boot(db_session, settings)

    assert await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1") == []
    assert (await _stored(db_session, "old-ext")).state == "enabled"


@pytest.mark.asyncio
async def test_a_second_consent_for_the_same_pair_is_refused_cleanly(db_session, settings, monkeypatch):
    import asyncio

    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()
    await _boot(db_session, settings)

    results = await asyncio.gather(
        extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1"),
        extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u2"),
        return_exceptions=True,
    )

    assert [type(r) for r in results] == [list, extensions_service.TakeoverError]


@pytest.mark.asyncio
async def test_a_user_setting_with_the_key_is_no_consent(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    db_session.add(Setting(
        key="extension.takeover_allowed.renamed-ext:old-ext", scope="user", user_id="u1",
        value={"value": {"source": "pip", "permissions": ["schedule.register"]}},
    ))
    await db_session.commit()

    await _boot(db_session, settings)

    assert set(get_extension_runtime().awaiting_consent) == {("renamed-ext", "old-ext")}


@pytest.mark.asyncio
async def test_a_second_package_does_not_ride_on_a_waiting_one(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "old-ext", legacy_ids=[])
    _write_ext(
        settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"],
        permissions=["schedule.register", "hosts.execute"],
    )
    _as_pip(monkeypatch, settings.extensions_dir, "old-ext", "renamed-ext")
    db_session.add(_old_row(source="bundled"))
    await db_session.commit()

    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert runtime.loaded == {} and runtime.legacy_owner == {}
    assert set(runtime.awaiting_consent) == {("old-ext", "old-ext"), ("renamed-ext", "old-ext")}
    row = await _stored(db_session, "old-ext")
    assert (row.source, row.granted_permissions) == ("bundled", ["schedule.register"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row_source", "found_source"), [("pip", "pip"), ("pip", "bundled"), ("bundled", "bundled")],
    ids=["paket-auf-paket", "ordner-auf-paket", "ordner-auf-ordner"],
)
async def test_the_same_id_from_a_trusted_or_equal_source_needs_no_consent(
    db_session, settings, monkeypatch, row_source, found_source,
):
    _write_ext(settings.extensions_dir, "old-ext", legacy_ids=[])
    if found_source == "pip":
        _as_pip(monkeypatch, settings.extensions_dir, "old-ext")
    db_session.add(_old_row(source=row_source))
    await db_session.commit()

    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert not runtime.awaiting_consent
    assert runtime.loaded["old-ext"].store_id == "old-ext"


@pytest.mark.asyncio
async def test_a_bundled_rename_with_actions_and_secrets_under_the_old_id_needs_no_consent(db_session, settings):
    from nodvard_deck.models import Action, Secret

    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    db_session.add(_old_row(source="bundled"))
    db_session.add(Action(
        ext_id="old-ext", action_type="old.tu_was", payload={}, proposed_by_type="user", proposed_by_id="u1",
        reason="Test",
    ))
    db_session.add(Secret(label="old-ext-token", kind="generic", ciphertext=b"x", owner_ext_id="old-ext"))
    await db_session.commit()

    await _boot(db_session, settings)

    assert not get_extension_runtime().awaiting_consent
    assert get_extension_runtime().loaded["renamed-ext"].store_id == "old-ext"


@pytest.mark.asyncio
async def test_after_consent_with_more_rights_no_occasion_switches_it_on(db_session, settings, monkeypatch):
    _write_ext(
        settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"],
        permissions=["schedule.register", "hosts.execute"], enable_on='enable_on = ["host_credential"]\n',
    )
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row(state="disabled"))
    untouched = extensions_service.UNTOUCHED_KEY_PREFIX + "old-ext"
    db_session.add(Setting(key=untouched, scope="global", value={"value": True}))
    await db_session.commit()
    await _boot(db_session, settings)
    await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")
    await db_session.commit()

    app = await _boot(db_session, settings)
    assert await extensions_service.auto_enable_for(app, db_session, settings, "host_credential") == []
    assert (await _stored(db_session, "old-ext")).state == "disabled"


@pytest.mark.asyncio
async def test_a_package_back_on_its_old_id_after_a_takeover_asks_again(db_session, settings, monkeypatch):
    # Rückweg über die Umbenennung: Die Zeile trägt jetzt das Manifest der neuen, die alte fragt deshalb neu.
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext", "old-ext")
    db_session.add(_old_row())
    await db_session.commit()
    await _boot(db_session, settings)
    await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")
    await _boot(db_session, settings)
    assert get_extension_runtime().loaded["renamed-ext"].store_id == "old-ext"

    shutil.rmtree(settings.extensions_dir / "renamed-ext")
    _write_ext(settings.extensions_dir, "old-ext", legacy_ids=[])
    await _boot(db_session, settings)

    assert set(get_extension_runtime().awaiting_consent) == {("old-ext", "old-ext")}


@pytest.mark.asyncio
async def test_only_one_package_can_be_allowed_the_same_data(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "old-ext", legacy_ids=[])
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "old-ext", "renamed-ext")
    db_session.add(_old_row(source="bundled"))
    await db_session.commit()
    await _boot(db_session, settings)

    await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")
    with pytest.raises(extensions_service.TakeoverConflict, match="übernimmt schon „renamed-ext“"):
        await extensions_service.allow_takeover(db_session, "old-ext", "old-ext", user_id="u1")

    # Jedes Entdecken fängt von vorn an: Erlaubt ist dann, was gespeichert ist.
    await extensions_service.discover_and_sync(db_session, settings)
    assert not get_extension_runtime().takeovers_allowed


@pytest.mark.asyncio
async def test_a_rename_that_is_refused_anyway_is_not_offered(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"], permissions=["hosts.write"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()

    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert not runtime.awaiting_consent
    assert "können noch nicht umbenannt werden" in runtime.refused["renamed-ext"]


@pytest.mark.asyncio
async def test_a_broken_package_of_the_same_name_does_not_spare_the_valid_one_the_consent(
    db_session, settings, monkeypatch,
):
    _write_ext(settings.extensions_dir, "old-ext", legacy_ids=[])
    _as_pip(monkeypatch, settings.extensions_dir, "old-ext")
    relabelled = extensions_service.discover_all
    broken = DiscoveredExtension(
        id="old-ext", source="pip", source_path=settings.extensions_dir, manifest=None, error="Manifest unlesbar",
    )
    # Eine zweite Distribution gleichen Namens, die in der Reihenfolge der Entry-Points vor dem gültigen Paket steht.
    monkeypatch.setattr(extensions_service, "discover_all", lambda *a, **k: [broken, *relabelled(*a, **k)])
    db_session.add(_old_row(source="bundled"))
    await db_session.commit()

    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert "old-ext" not in runtime.loaded
    assert set(runtime.awaiting_consent) == {("old-ext", "old-ext")}
    assert runtime.awaiting_consent["old-ext", "old-ext"].ok
    assert (await _stored(db_session, "old-ext")).source == "bundled"


@pytest.mark.asyncio
@pytest.mark.parametrize(("allowed_as", "starts_as"), [("pip", "bundled"), ("bundled", "pip")])
async def test_consent_holds_only_for_the_allowed_source(db_session, settings, monkeypatch, allowed_as, starts_as):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    folder = extensions_service.discover_all
    if allowed_as == "pip":
        _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()
    await _boot(db_session, settings)
    await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")
    await db_session.commit()

    if starts_as == "pip":
        _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    else:
        monkeypatch.setattr(extensions_service, "discover_all", folder)
    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert "renamed-ext" not in runtime.loaded
    assert set(runtime.awaiting_consent) == {("renamed-ext", "old-ext")}


_CONSENT_KEY = "extension.takeover_allowed.renamed-ext:old-ext"
_CONSENT = {"source": "pip", "permissions": ["schedule.register"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broken",
    [
        lambda: _old_row("odd-ext", manifest=["kaputt"]),
        lambda: _old_row("odd-ext", manifest={"id": ["x"], "name": "Alt"}),
        lambda: _old_row("odd-ext", manifest={"id": "odd-ext", "legacy_ids": 5}),
        lambda: Setting(key=_CONSENT_KEY, scope="global", value={"value": {"source": "pip", "permissions": 5}}),
        lambda: Setting(key=_CONSENT_KEY, scope="global", value={"value": {"source": "pip", "permissions": [5]}}),
        lambda: Setting(key=_CONSENT_KEY, scope="global", value=["kaputt"]),
        lambda: Setting(key=_CONSENT_KEY, scope="global", user_id="u9", value={"value": _CONSENT}),
        lambda: Setting(key="extension.takeover_allowed.gone:old-ext", scope="global", user_id="u9", value={}),
    ],
    ids=[
        "manifest-liste", "manifest-id-liste", "legacy-ids-zahl", "erlaubnis-rechte-zahl", "erlaubnis-rechte-ohne-text",
        "einstellung-liste", "erlaubnis-mit-benutzer", "verwaist-mit-benutzer",
    ],
)
async def test_a_broken_row_or_consent_does_not_stop_the_start(db_session, settings, monkeypatch, broken):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    db_session.add(broken())
    await db_session.commit()

    await _boot(db_session, settings)

    runtime = get_extension_runtime()
    assert "renamed-ext" not in runtime.loaded
    assert set(runtime.awaiting_consent) == {("renamed-ext", "old-ext")}
    for row_id in await _row_ids(db_session):  # wie die Übernahmen in `GET /extensions`
        extensions_service.takeover_requests(await _stored(db_session, row_id))


@pytest.mark.asyncio
async def test_two_packages_allowed_the_same_data_at_once_only_one_gets_it(db_session, settings, monkeypatch):
    import asyncio

    _write_ext(settings.extensions_dir, "old-ext", legacy_ids=[])
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "old-ext", "renamed-ext")
    db_session.add(_old_row(source="bundled"))
    await db_session.commit()
    await _boot(db_session, settings)
    real = extensions_service.settings_service.set_global

    async def set_global(*args, **kwargs):
        await asyncio.sleep(0)  # hier kommt die zweite Erlaubnis dazwischen
        return await real(*args, **kwargs)

    monkeypatch.setattr(extensions_service.settings_service, "set_global", set_global)

    results = await asyncio.gather(
        extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1"),
        extensions_service.allow_takeover(db_session, "old-ext", "old-ext", user_id="u2"),
        return_exceptions=True,
    )

    assert [type(r) for r in results] == [list, extensions_service.TakeoverConflict]
    assert await _consent_keys(db_session) == ["extension.takeover_allowed.renamed-ext:old-ext"]
    assert set(get_extension_runtime().takeovers_allowed) == {("renamed-ext", "old-ext")}


@pytest.mark.asyncio
async def test_a_failed_consent_is_not_kept_as_allowed(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()
    await _boot(db_session, settings)

    async def fail(*args, **kwargs):
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(extensions_service.audit_service, "log", fail)
    with pytest.raises(RuntimeError):
        await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")

    runtime = get_extension_runtime()
    assert not runtime.takeovers_allowed
    assert set(runtime.awaiting_consent) == {("renamed-ext", "old-ext")}


@pytest.mark.asyncio
async def test_secrets_under_an_old_id_without_any_row_wait_too(db_session, settings, monkeypatch):
    from nodvard_deck.models import Secret

    _write_ext(settings.extensions_dir, "intruder", legacy_ids=["gone-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "intruder")
    db_session.add(Secret(label="gone-ext-token", kind="generic", ciphertext=b"x", owner_ext_id="gone-ext"))
    await db_session.commit()

    await _boot(db_session, settings)

    assert set(get_extension_runtime().awaiting_consent) == {("intruder", "gone-ext")}


@pytest.mark.asyncio
async def test_consent_ends_when_the_manifest_is_unreadable_at_a_start(db_session, settings, monkeypatch):
    _write_ext(settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _as_pip(monkeypatch, settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()
    await _boot(db_session, settings)
    await extensions_service.allow_takeover(db_session, "renamed-ext", "old-ext", user_id="u1")
    assert await _consent_keys(db_session) == [_CONSENT_KEY]

    manifest = settings.extensions_dir / "renamed-ext" / "extension.toml"
    readable = manifest.read_text(encoding="utf-8")
    manifest.write_text("[extension\nkaputt", encoding="utf-8")
    await _boot(db_session, settings)
    assert "renamed-ext" not in get_extension_runtime().loaded
    assert await _consent_keys(db_session) == []

    manifest.write_text(readable, encoding="utf-8")
    await _boot(db_session, settings)
    assert set(get_extension_runtime().awaiting_consent) == {("renamed-ext", "old-ext")}


# -- API -----------------------------------------------------------------------------------------------------------


async def _token(client, db_session, role: str | None = None) -> dict:
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    username, password = "owner1", "correct-horse-battery"
    if role is not None:
        from nodvard_deck.core import security
        from nodvard_deck.models import User
        from nodvard_deck.services import auth as auth_service

        roles = await auth_service.ensure_builtin_roles(db_session)
        user = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
        user.roles.append(roles[role])
        db_session.add(user)
        await db_session.commit()
        username, password = "viewer1", "whatever123"
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _waiting(db_session, test_settings, monkeypatch, permissions: list[str] | None = None) -> None:
    _write_ext(test_settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"], permissions=permissions)
    _as_pip(monkeypatch, test_settings.extensions_dir, "renamed-ext")
    db_session.add(_old_row())
    await db_session.commit()
    await extensions_service.discover_and_sync(db_session, test_settings)
    await db_session.commit()


@pytest.mark.asyncio
async def test_api_lists_the_request_and_allows_it(client, db_session, test_settings, monkeypatch):
    await _waiting(db_session, test_settings, monkeypatch, permissions=["schedule.register", "hosts.execute"])
    headers = await _token(client, db_session)

    r = await client.get("/api/v1/extensions", headers=headers)
    assert r.status_code == 200
    (old,) = [item for item in r.json() if item["id"] == "old-ext"]
    assert old["takeover_requests"] == [{
        "id": "renamed-ext", "old_id": "old-ext", "name": "Neu renamed-ext", "source": "pip",
        "permissions": ["schedule.register", "hosts.execute"], "allowed": False,
    }]

    r = await client.post("/api/v1/extensions/renamed-ext/takeover", headers=headers, json={"old_id": "old-ext"})
    assert r.status_code == 200, r.text
    assert r.json() == {"restart_needed": True, "switched_off": True, "extra_permissions": ["hosts.execute"]}
    consent = (await db_session.execute(select(Setting).where(Setting.key.contains("takeover")))).scalar_one()
    assert consent.key == "extension.takeover_allowed.renamed-ext:old-ext"
    assert consent.value == {"value": {"source": "pip", "permissions": ["hosts.execute", "schedule.register"]}}

    r = await client.get("/api/v1/extensions", headers=headers)
    (old,) = [item for item in r.json() if item["id"] == "old-ext"]
    assert [(t["id"], t["allowed"]) for t in old["takeover_requests"]] == [("renamed-ext", True)]
    assert old["state"] == "disabled"
    r = await client.post("/api/v1/extensions/renamed-ext/takeover", headers=headers, json={"old_id": "old-ext"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_refuses_an_old_id_that_already_has_an_owner(client, db_session, test_settings, monkeypatch):
    _write_ext(test_settings.extensions_dir, "renamed-ext", legacy_ids=["old-ext"])
    _write_ext(test_settings.extensions_dir, "old-ext", legacy_ids=[])
    _as_pip(monkeypatch, test_settings.extensions_dir, "old-ext")
    db_session.add(_old_row(source="bundled", manifest={"id": "renamed-ext", "legacy_ids": ["old-ext"]}))
    await db_session.commit()
    await extensions_service.discover_and_sync(db_session, test_settings)
    await db_session.commit()
    headers = await _token(client, db_session)

    r = await client.post("/api/v1/extensions/old-ext/takeover", headers=headers, json={"old_id": "old-ext"})

    assert r.status_code == 409 and "gehört schon zu „renamed-ext“" in r.json()["detail"]


@pytest.mark.asyncio
async def test_api_without_extensions_manage_neither_sees_nor_allows(client, db_session, test_settings, monkeypatch):
    await _waiting(db_session, test_settings, monkeypatch)
    headers = await _token(client, db_session, role="viewer")

    r = await client.get("/api/v1/extensions", headers=headers)
    assert all(item["takeover_requests"] == [] for item in r.json())
    r = await client.post("/api/v1/extensions/renamed-ext/takeover", headers=headers, json={"old_id": "old-ext"})
    assert r.status_code == 403
    assert set(get_extension_runtime().awaiting_consent) == {("renamed-ext", "old-ext")}
