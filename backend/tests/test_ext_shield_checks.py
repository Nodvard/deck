from __future__ import annotations

import asyncio
import json
import sys
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from nodvard_sdk import Host
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "extensions" / "shield"


def _changelog_entries():
    # Findet Eintraege in unreleased/ ODER versions/: ein Release rollt die Bruchstuecke in die Versionsdatei.
    base = ROOT / "backend/src/nodvard_deck/changelog"
    entries = []
    for teil in ("unreleased", "versions"):
        for datei in sorted((base / teil).glob("*.toml")):
            entries.extend(tomllib.loads(datei.read_text(encoding="utf-8")).get("entries", []))
    return entries


@pytest.fixture
def checks(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT / "src"))
    from nodvard_deck_ext_shield import checks

    yield checks
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


@pytest_asyncio.fixture
async def ctx(checks):
    from nodvard_deck_ext_shield.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session():
        async with sessions() as db:
            yield db
            await db.commit()

    values = {}
    host = Host(id="pi", name="pi", display_name="Pi", address="pi")
    context = SimpleNamespace(
        db=SimpleNamespace(session=session),
        settings=SimpleNamespace(get=AsyncMock(side_effect=lambda: dict(values))),
        hosts=SimpleNamespace(get=AsyncMock(return_value=host)),
        defender=SimpleNamespace(target_hosts=AsyncMock(return_value=[host])),
        exec=SimpleNamespace(run=AsyncMock(side_effect=AssertionError("Keine Serverabfrage"))),
        values=values, host=host,
    )
    yield context
    await engine.dispose()


@pytest.fixture
def probes(checks, monkeypatch):
    cve = AsyncMock(return_value={"status": "ok", "checked_at": 123.0, "findings": [
        {"package": "openssl", "installed_version": "1.0", "cve": "CVE-2026-1000", "severity": "high", "fixed_version": "1.1"},
    ], "counts": {"high": 1, "critical": 0}, "error": None})
    external = AsyncMock(return_value={"status": "ok", "checked_at": 123.0, "open_ports": [22, 443],
                                      "new_ports": [], "certificates": [], "warnings": [], "error": None})
    monkeypatch.setattr(checks.cve, "check_host", cve)
    monkeypatch.setattr(checks.external, "check_host", external)
    return cve, external


async def test_overview_reads_saved_results_without_probes(checks, ctx, probes):
    service = checks.ServerChecks(ctx, ctx.defender)
    assert (await service.overview())["hosts"] == [{"host_id": "pi", "host_name": "Pi", "running": False, "external_enabled": True,
                                                 "cve": None, "external": None}]
    for probe in probes:
        probe.assert_not_awaited()
    result = await service.run_host(ctx.host)
    restarted = checks.ServerChecks(ctx, ctx.defender)
    assert (await restarted.overview())["hosts"] == [result]
    assert result["cve"]["counts"]["high"] == 1
    ctx.exec.run.assert_not_awaited()


async def test_failed_probe_keeps_last_successful_port_baseline(checks, ctx, probes):
    service = checks.ServerChecks(ctx, ctx.defender)
    first = await service.run_host(ctx.host)
    probes[1].return_value = {**first["external"], "status": "error", "open_ports": [], "error": "Nicht erreichbar"}
    failed = await service.run_host(ctx.host)
    assert failed["external"]["error"] == "Nicht erreichbar"
    probes[1].return_value = {**first["external"], "open_ports": [22, 443, 8080], "new_ports": [8080]}
    await service.run_host(ctx.host)
    assert probes[1].await_args.args[2] == first["external"]


async def test_disabling_external_checks_survives_restart_and_keeps_history(checks, ctx, probes):
    service = checks.ServerChecks(ctx, ctx.defender)
    first = await service.run_host(ctx.host)
    await service.set_external(ctx.host, False)
    probes[1].reset_mock()
    service = checks.ServerChecks(ctx, ctx.defender)
    result = await service.run_host(ctx.host)
    assert result["external_enabled"] is False
    assert result["external"] == first["external"]
    probes[1].assert_not_awaited()
    await service.set_external(ctx.host, True)
    await service.run_host(ctx.host)
    assert probes[1].await_args.args[2] == first["external"]


async def test_global_switch_also_prevents_manual_external_checks(checks, ctx, probes):
    ctx.values.update(external_checks_enabled=False, checks_enabled=False)
    service = checks.ServerChecks(ctx, ctx.defender)
    assert await service.run() == {"hosts": 0, "finished": 0}
    probes[0].assert_not_awaited()
    result = await service.run_host(ctx.host)
    assert result["external_enabled"] is False and result["external"] is None
    probes[0].assert_awaited_once()
    probes[1].assert_not_awaited()
    with pytest.raises(ValueError, match="ausgeschaltet"):
        await service.set_external(ctx.host, True)


async def test_one_probe_failure_does_not_skip_the_other(checks, ctx, probes):
    probes[0].side_effect = RuntimeError("Ausfall")
    result = await checks.ServerChecks(ctx, ctx.defender).run_host(ctx.host)
    assert result["cve"]["status"] == "error"
    assert result["external"]["status"] == "ok"
    probes[1].assert_awaited_once()


async def test_external_switch_changed_during_cve_download_is_respected(checks, ctx, probes):
    result = probes[0].return_value

    async def cve_finished(*_):
        ctx.values["external_checks_enabled"] = False
        return result

    probes[0].side_effect = cve_finished
    row = await checks.ServerChecks(ctx, ctx.defender).run_host(ctx.host)
    assert row["external_enabled"] is False
    probes[1].assert_not_awaited()


async def test_schedule_checks_all_targets_with_at_most_two_hosts_at_once(checks, ctx, probes, monkeypatch):
    hosts = [Host(id=f"host-{n}", name=f"host-{n}", display_name=f"host-{n}", address=f"host-{n}") for n in range(5)]
    ctx.defender.target_hosts.return_value = hosts
    stored = {}

    async def load(_ctx, host_id, kind):
        return stored.get((host_id, kind))

    async def save(_ctx, host_id, kind, data):
        stored[host_id, kind] = data

    monkeypatch.setattr(checks, "load_baseline", load)
    monkeypatch.setattr(checks, "save_baseline", save)
    active, peak = 0, 0
    result = probes[0].return_value

    async def probe(*_):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return result

    probes[0].side_effect = probe
    service = checks.ServerChecks(ctx, ctx.defender)
    assert await service.run() == {"hosts": 5, "finished": 5}
    assert peak == 2
    assert {call.args[1] for call in probes[0].await_args_list} == {host.id for host in hosts}
    assert {call.args[1] for call in probes[1].await_args_list} == {host.id for host in hosts}
    assert all(stored[host.id, "checks"]["cve"]["status"] == "ok" for host in hosts)


async def test_duplicate_runs_and_setting_changes_are_rejected_while_running(checks, ctx, probes):
    entered, release = asyncio.Event(), asyncio.Event()
    result = probes[0].return_value

    async def blocked(*_):
        entered.set()
        await release.wait()
        return result

    probes[0].side_effect = blocked
    service = checks.ServerChecks(ctx, ctx.defender)
    task = asyncio.create_task(service.run_host(ctx.host))
    await entered.wait()
    try:
        with pytest.raises(checks.CheckRunning):
            await service.run_host(ctx.host)
        with pytest.raises(checks.CheckRunning):
            await service.set_external(ctx.host, False)
    finally:
        release.set()
        await task


async def test_api_contract_validates_settings_and_restricts_host_scope(checks, ctx, probes):
    app = FastAPI()
    service = checks.ServerChecks(ctx, ctx.defender)
    for router in checks.build_routers(service):
        app.include_router(router, prefix="/ext/shield")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get("/ext/shield/defender/checks")
        assert result.status_code == 200
        assert result.json()["hosts"][0]["cve"] is None
        result = await client.post("/ext/shield/defender/checks/pi/run")
        assert result.status_code == 202
        assert result.json()["running"] is True
        await asyncio.gather(*service._tasks.values())
        result = await client.get("/ext/shield/defender/checks")
        row = result.json()["hosts"][0]
        assert row["running"] is False
        assert row["cve"]["findings"][0]["fixed_version"] == "1.1"
        result = await client.patch("/ext/shield/defender/checks/pi", json={"external_enabled": False})
        assert result.status_code == 200 and result.json()["external_enabled"] is False
        for payload in ({"external_enabled": "false"}, {"external_enabled": True, "address": "other"}, {}):
            assert (await client.patch("/ext/shield/defender/checks/pi", json=payload)).status_code == 422
        assert (await client.post("/ext/shield/defender/checks/other/run")).status_code == 404
        assert (await client.patch("/ext/shield/defender/checks/other", json={"external_enabled": False})).status_code == 404
    schema = app.openapi()
    assert schema["paths"]["/ext/shield/defender/checks"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CheckList",
    }


async def test_background_start_is_immediate_and_stopping_cancels_it(checks, ctx, probes):
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*_):
        entered.set()
        await release.wait()

    probes[0].side_effect = blocked
    service = checks.ServerChecks(ctx, ctx.defender)
    result = await service.start_host(ctx.host)
    assert result["running"] is True
    await entered.wait()
    with pytest.raises(checks.CheckRunning):
        await service.start_host(ctx.host)
    assert (await service.overview())["hosts"][0]["running"] is True
    tasks = list(service._tasks.values())
    service.cancel_tasks()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert all(task.cancelled() for task in tasks)
    assert (await service.overview())["hosts"][0]["running"] is False
    probes[1].assert_not_awaited()


async def test_setup_registers_permissions_and_replaces_schedule(checks, monkeypatch):
    import nodvard_deck_ext_shield as shield
    from nodvard_deck.core.cron import cron_trigger

    monkeypatch.setattr(shield.UpdateCenter, "schedule_catch_up", lambda _: None)
    ctx = MagicMock()
    ctx.settings.get = AsyncMock(return_value={})
    ctx.scheduler.validate_schedule.side_effect = cron_trigger
    ctx.scheduler.register_job = AsyncMock()
    ctx.notify.send = AsyncMock()
    extension = shield.Extension()
    await extension.setup(ctx)
    jobs = {call.args[0].id: call.args[0] for call in ctx.scheduler.register_job.call_args_list}
    assert jobs["server-checks"].enabled is True
    assert jobs["server-checks"].schedule == "30 6 * * *"
    routes = {(route.path, next(iter(route.methods))): call.kwargs["permission"]
              for call in ctx.api.include_router.call_args_list for route in call.args[0].routes}
    assert routes["/defender/checks", "GET"] == "soc.read"
    assert routes["/defender/checks/{host_id}/run", "POST"] == "soc.manage"
    assert routes["/defender/checks/{host_id}", "PATCH"] == "soc.manage"
    ctx.settings.get.return_value = {"checks_cron": "0 4 * * *", "checks_enabled": False}
    ctx.scheduler.register_job.reset_mock()
    await extension.on_settings_changed(ctx, {})
    jobs = {call.args[0].id: call.args[0] for call in ctx.scheduler.register_job.call_args_list}
    assert jobs["server-checks"].enabled is False
    ctx.settings.get.return_value = {"checks_cron": "broken"}
    await checks.register_job(ctx, extension._checks)
    assert ctx.scheduler.register_job.call_args.args[0].schedule == checks.DEFAULT_CRON
    ctx.notify.send.assert_awaited_once()


def test_check_settings_and_documented_contract(checks):
    properties = json.loads((EXT / "settings.schema.json").read_text(encoding="utf-8"))["properties"]
    assert properties["checks_enabled"]["default"] is True
    assert properties["external_checks_enabled"]["default"] is True
    assert properties["checks_cron"]["default"] == checks.DEFAULT_CRON
    assert properties["checks_cron"]["x-widget"] == "schedule"
    manifest = tomllib.loads((EXT / "extension.toml").read_text(encoding="utf-8"))["extension"]
    # Helfer-Versionen sind Mindestangaben (-ge): das Manifest darf neuer sein als die älteste verlangte Version.
    assert manifest["root_helper"]["version"] >= checks.cve.HELPER_CVE_VERSION
    assert "docker" in manifest["requires"]
    cve_aussen = [
        entry for entry in _changelog_entries()
        if "Sicherheitsmeldungen ab" in entry["text"] or "offene TCP-Ports" in entry["text"]
    ]
    assert len(cve_aussen) == 2 and all(entry["kind"] == "neu" for entry in cve_aussen)
    api = (ROOT / "docs" / "04-API.md").read_text(encoding="utf-8")
    for path in ("/ext/shield/defender/checks", "/ext/shield/defender/checks/{host_id}/run", "/ext/shield/defender/checks/{host_id}"):
        assert path in api
    for path in (ROOT / "docs" / "18-SHIELD.md", ROOT / "docs" / "en" / "SHIELD.md"):
        assert "CVE" in path.read_text(encoding="utf-8")


def test_external_check_texts_say_home_network_not_internet(checks):
    """Die Außenprüfung läuft von Deck aus im Heimnetz; kein Text darf eine Prüfung aus dem Internet versprechen."""
    version = checks.cve.HELPER_CVE_VERSION
    de = (ROOT / "docs" / "18-SHIELD.md").read_text(encoding="utf-8")
    en = (ROOT / "docs" / "en" / "SHIELD.md").read_text(encoding="utf-8")
    assert "aus dem Heimnetz" in de and "nicht aus dem Internet" in de and f"Hilfsskript Version {version}" in de
    assert "from the home network" in en and "not from the internet" in en and f"helper version {version}" in en
    assert "nicht aus dem Internet" in (ROOT / "docs" / "04-API.md").read_text(encoding="utf-8")
    schema = json.loads((EXT / "settings.schema.json").read_text(encoding="utf-8"))["properties"]
    assert "aus dem Heimnetz, nicht aus dem Internet" in schema["external_checks_enabled"]["description"]
    assert any("nicht aus dem Internet" in entry["text"] for entry in _changelog_entries())
    ui = (EXT / "frontend" / "src" / "SecurityChecks.tsx").read_text(encoding="utf-8")
    assert "nicht aus dem Internet" in ui
