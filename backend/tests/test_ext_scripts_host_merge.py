from __future__ import annotations

import sys
from pathlib import Path

import pytest
from nodvard_deck.models import Action, Host, HostMerge
from nodvard_deck.services import hosts
from nodvard_sdk import DiscoveredHost
from test_ext_context import _build


@pytest.fixture
def scripts_module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "extensions" / "scripts" / "src"))
    import nodvard_deck_ext_scripts

    yield nodvard_deck_ext_scripts
    for name in list(sys.modules):
        if name == "nodvard_deck_ext_scripts" or name.startswith("nodvard_deck_ext_scripts."):
            del sys.modules[name]


@pytest.mark.asyncio
async def test_existing_script_keeps_target_after_host_merge(tmp_path, db_session, test_settings, scripts_module):
    _, _, provider_ctx = _build(tmp_path / "provider", ext_id="proxmox", permissions=["hosts.write"])
    source = (await provider_ctx.hosts.upsert_discovered([
        DiscoveredHost(
            provider_ref="node/100", name="gast", address="192.0.2.20", address_verified=True, kind="vm",
            metadata={"identity_addresses": ["192.0.2.20"]},
        ),
    ]))[0]
    _, _, ctx = _build(
        tmp_path / "scripts", ext_id="scripts", settings=test_settings,
        permissions=[
            "hosts.read", "hosts.execute", "secrets.read:scripts-*", "schedule.register", "notify.send",
            "audit.write", "actions.standing_approval",
        ],
    )
    extension = scripts_module.Extension()
    await extension.setup(ctx)
    extension._repo.save(
        scripts_module.ScriptMeta(
            id="host-check", name="Server prüfen", target={"kind": "host", "host_id": source.id}, enabled=True,
        ),
        "echo ok\n",
        commit_message="Serverprüfung angelegt",
    )
    before = await scripts_module.run_script(ctx, extension._repo, "host-check", param_overrides={})
    assert before["targets"] == 1 and before["results"][0]["host_id"] == source.id

    manual = await hosts.create_host(db_session, name="von-hand", address="192.0.2.20")
    await db_session.commit()
    assert await db_session.get(Host, source.id) is None
    assert (await db_session.get(HostMerge, source.id)).host_id == manual.id
    assert extension._repo.get("host-check").meta.target == {"kind": "host", "host_id": source.id}

    result = await scripts_module.run_script(ctx, extension._repo, "host-check", param_overrides={})
    assert result["targets"] == 1
    assert result["results"][0]["host_id"] == manual.id
    action = await db_session.get(Action, result["results"][0]["action_id"])
    assert action.host_id == manual.id and action.status == "proposed"
    assert action.payload["command"] == "echo ok\n"
