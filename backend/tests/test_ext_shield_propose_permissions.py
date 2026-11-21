"""Shield-Vorschläge, die als root laufen, brauchen neben soc.manage auch hosts.execute."""

from __future__ import annotations

import asyncio
import sys
import tomllib
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from nodvard_deck import config
from nodvard_deck.api.deps import get_session
from nodvard_deck.core import gate, security, ssh
from nodvard_deck.db import utcnow
from nodvard_deck.db.session import session_scope
from nodvard_deck.ext.context import build_context
from nodvard_deck.ext.runtime import (
    LoadedExtension,
    get_extension_runtime,
    reset_extension_runtime,
)
from nodvard_deck.models import Action, ExtensionRecord, Host, RefreshToken, Role, RolePermission, User
from nodvard_deck.services import settings as settings_service
from nodvard_sdk import ExtensionManifest
from nodvard_sdk.capabilities import ActionExecutor
from sqlalchemy import select

EXT = Path(__file__).resolve().parents[2] / "extensions" / "shield"

ROUTES = [
    ("ban", "hosts/{host}/ban", {"ip": "203.0.113.7", "jail": "sshd", "unban": True}),
    ("reboot", "hosts/{host}/upgrade", {"mode": "reboot"}),
    ("upgrade", "hosts/{host}/upgrade", {"mode": "all"}),
    ("propose_all", "updates/propose-all", {"mode": "all"}),
    ("restore", "findings/f1/restore", None),
    ("delete", "findings/f1/delete", None),
    ("realtime", "realtime/{host}", {"enable": False}),
    ("install", "hosts/{host}/install", {"package": "signatures"}),
]


@pytest.fixture
async def shield(monkeypatch, test_settings, db_session, tmp_path):
    monkeypatch.syspath_prepend(str(EXT / "src"))
    monkeypatch.setattr(config, "_settings", test_settings)
    from nodvard_deck_ext_shield import defender_api
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.guard import Guard
    from nodvard_deck_ext_shield.patching import UpdateCenter

    reset_extension_runtime()
    host = Host(name="pi", address="192.0.2.2")
    db_session.add(host)
    await settings_service.set_global(db_session, "autonomy.mode", "full")
    await settings_service.set_global(db_session, "autonomy.max_risk", "medium")
    await db_session.commit()

    runtime = get_extension_runtime()
    manifest = ExtensionManifest(
        id="shield", name="Shield", version="0.1.0", api_version="0.1", entrypoint="m:E",
        permissions=["hosts.read", "hosts.execute", "audit.write"], legacy_ids=tomllib.loads((EXT / "extension.toml").read_text())["extension"]["legacy_ids"],
    )
    loaded = LoadedExtension(manifest=manifest, instance=None, ctx=None, granted_permissions=manifest.permissions)
    ctx = build_context(runtime, loaded, manifest, manifest.permissions, tmp_path / "shield", test_settings)
    loaded.ctx = ctx
    calls: list[str] = []

    async def run(connection, command, **kwargs):
        calls.append(command)
        if " stat " in f" {command} ":
            return 0, "755 0 directory\n" * len(command.split(" -- ")[1].split()), ""
        if "/proc/self/mountinfo" in command:
            return 0, "28 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw\n", ""
        return 0, "", ""

    monkeypatch.setattr(ctx.exec, "_connection", AsyncMock(return_value=object()))
    monkeypatch.setattr(ssh, "run", run)
    defender = Defender(ctx)
    updates = UpdateCenter(ctx, defender)
    for spec in defender_api.ACTION_SPECS:
        ctx.actions.register(spec)
    runtime.capabilities.provide("shield", ActionExecutor, defender_api.DefenderExecutor(ctx, defender, updates))
    _, manage = defender_api.build_routers(ctx, defender, updates, Guard(ctx, defender))
    ctx.api.include_router(manage, permission="soc.manage")
    app = FastAPI()
    app.include_router(loaded.router, prefix=manifest.api_prefix)
    app.state.defender = defender

    async def session():
        async with session_scope() as current:
            yield current

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[config.get_settings] = lambda: test_settings
    try:
        yield app, host.id, calls
    finally:
        await gate.shutdown_running()
        reset_extension_runtime()
        for name in list(sys.modules):
            if name.startswith("nodvard_deck_ext_shield"):
                del sys.modules[name]


async def _token(db_session, test_settings, permissions: list[str]) -> str:
    user = User(
        username="schutz", password_hash="unused",
        roles=[Role(name="schutz", permissions=[RolePermission(permission=p) for p in permissions])],
    )
    db_session.add(user)
    await db_session.flush()
    login = RefreshToken(
        user_id=user.id, token_hash=security.hash_opaque_token(user.id), expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(login)
    await db_session.commit()
    return security.create_jwt(
        subject=user.id, token_type="access", secret=test_settings.get_or_create_jwt_secret(),
        ttl_seconds=60, extra_claims={"sid": login.id},
    )


async def _post(app: FastAPI, token: str, path: str, body: dict | None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/ext/shield/defender/{path}", headers={"Authorization": f"Bearer {token}"}, json=body,
        )
    await gate.shutdown_running()
    return response


async def _actions() -> list[Action]:
    async with session_scope() as current:
        return list((await current.scalars(select(Action))).all())


@pytest.mark.parametrize(("name", "path", "body"), ROUTES, ids=[r[0] for r in ROUTES])
async def test_proposal_needs_hosts_execute(shield, db_session, test_settings, name, path, body):
    app, host_id, calls = shield
    token = await _token(db_session, test_settings, ["soc.manage"])
    response = await _post(app, token, path.format(host=host_id), body)
    assert response.status_code == 403, response.text
    assert "hosts.execute" in response.json()["detail"]
    assert await _actions() == []
    assert calls == []


@pytest.mark.parametrize(("name", "path", "body"), ROUTES, ids=[r[0] for r in ROUTES])
async def test_proposal_still_needs_soc_manage(shield, db_session, test_settings, name, path, body):
    app, host_id, calls = shield
    token = await _token(db_session, test_settings, ["hosts.execute"])
    response = await _post(app, token, path.format(host=host_id), body)
    assert response.status_code == 403, response.text
    assert await _actions() == []
    assert calls == []


async def test_install_runs_with_both_permissions(shield, db_session, test_settings):
    app, host_id, calls = shield
    token = await _token(db_session, test_settings, ["soc.manage", "hosts.execute"])
    response = await _post(app, token, f"hosts/{host_id}/install", {"package": "signatures"})
    assert response.status_code == 200, response.text
    [action] = await _actions()
    assert action.status == "succeeded"
    assert action.gate_decision["rule"] == "autonomy:full"
    assert calls


# Nur Prüfen, Lesen und Markieren in Deck; nichts davon ändert eine Datei oder ein Paket auf dem Server.
SOC_MANAGE_ONLY = {
    "/scans", "/updates/check", "/guard/check", "/events/acknowledge-all", "/events/{event_id}/acknowledge",
    "/briefing", "/audits", "/findings/{finding_id}/ignore", "/findings/{finding_id}/reopen",
    "/findings/reopen-folder",
}


def _write_routes(app: FastAPI) -> list[str]:
    prefix = "/api/v1/ext/shield/defender"
    return sorted(
        path.removeprefix(prefix) for path, methods in app.openapi()["paths"].items()
        if path.startswith(prefix) and set(methods) - {"get", "head"}
    )


async def test_every_changing_route_needs_hosts_execute(shield, db_session, test_settings):
    app, host_id, calls = shield
    await _shield_tables()
    token = await _token(db_session, test_settings, ["soc.manage"])
    routes = _write_routes(app)
    assert SOC_MANAGE_ONLY <= set(routes)
    for path in routes:
        if path in SOC_MANAGE_ONLY:
            continue
        concrete = path.replace("{action}", "restore")
        for name in ("host_id", "finding_id", "run_id", "event_id"):
            concrete = concrete.replace("{" + name + "}", host_id if name == "host_id" else "x1")
        response = await _post(app, token, concrete.lstrip("/"), {})
        assert response.status_code == 403, (path, response.text)
        assert "hosts.execute" in response.json()["detail"], path
    assert calls == []


async def _shield_tables() -> None:
    from nodvard_deck_ext_shield.models import Base

    async with session_scope() as current:
        await (await current.connection()).run_sync(Base.metadata.create_all)
        await current.commit()


async def _detected_finding(host_id: str) -> None:
    from nodvard_deck_ext_shield.models import FindingRecord

    await _shield_tables()
    async with session_scope() as current:
        current.add(FindingRecord(
            id="f9", host_id=host_id, host_name="pi", path="/srv/daten/upload/evil.sh", signature="EICAR",
            status="detected", detected_at=utcnow(),
        ))
        await current.commit()


async def _register_shield(settings: dict) -> None:
    async with session_scope() as current:
        current.add(ExtensionRecord(
            id=get_extension_runtime().store_id("shield"), version="0.1.0", api_version="0.1", state="enabled",
            settings=settings,
        ))
        await current.commit()


async def _saved_exclude_paths() -> list[str]:
    async with session_scope() as current:
        record = await current.get(ExtensionRecord, get_extension_runtime().store_id("shield"))
        return list(record.settings.get("exclude_paths") or [])


async def _finding_status() -> str:
    from nodvard_deck_ext_shield.models import FindingRecord

    async with session_scope() as current:
        return (await current.get(FindingRecord, "f9")).status


async def test_quarantine_needs_hosts_execute(shield, db_session, test_settings):
    app, host_id, calls = shield
    await _detected_finding(host_id)
    token = await _token(db_session, test_settings, ["soc.manage"])
    response = await _post(app, token, "findings/f9/quarantine", None)
    assert response.status_code == 403, response.text
    assert "hosts.execute" in response.json()["detail"]
    assert calls == []
    assert await _finding_status() == "detected"


async def test_quarantine_runs_with_both_permissions(shield, db_session, test_settings):
    app, host_id, calls = shield
    await _detected_finding(host_id)
    token = await _token(db_session, test_settings, ["soc.manage", "hosts.execute"])
    response = await _post(app, token, "findings/f9/quarantine", None)
    assert response.status_code == 200, response.text
    assert any("evil.sh" in command for command in calls)


@pytest.fixture
def infected_scan(shield, monkeypatch):
    from nodvard_deck_ext_shield import antivirus as av
    from nodvard_deck_ext_shield.defender import Defender

    _, host_id, _ = shield
    done: list[str] = []
    run_many = Defender._run_many

    async def targets(self):
        return [await self._ctx.hosts.get(host_id)]

    async def scan_on_host(self, host, scan_id, kind, paths, settings):
        result = av.ScanResult(status="infected", infected=1, files_scanned=3, findings=[("/srv/www/shell.php", "Php.Webshell")])
        return result, "", 0

    async def tracked(self, *args, **kwargs):
        try:
            await run_many(self, *args, **kwargs)
        finally:
            done.append("x")

    monkeypatch.setattr(Defender, "target_hosts", targets)
    monkeypatch.setattr(Defender, "_scan_on_host", scan_on_host)
    monkeypatch.setattr(Defender, "_run_many", tracked)
    return done


async def _start_scan(app: FastAPI, token: str, host_id: str, done: list[str]):
    await _shield_tables()
    response = await _post(app, token, "scans", {"kind": "custom", "paths": ["/srv/www"], "host_ids": [host_id]})
    for _ in range(500):
        if done:
            break
        await asyncio.sleep(0.01)
    assert done, "Scan nicht fertig"
    return response


async def _scan_findings() -> list:
    from nodvard_deck_ext_shield.models import FindingRecord

    async with session_scope() as current:
        return list((await current.scalars(select(FindingRecord))).all())


async def test_scan_without_hosts_execute_leaves_findings_open(shield, infected_scan, db_session, test_settings):
    from nodvard_deck_ext_shield.defender import NO_EXECUTE_NOTE

    app, host_id, calls = shield
    token = await _token(db_session, test_settings, ["soc.manage"])
    response = await _start_scan(app, token, host_id, infected_scan)
    assert response.status_code == 200, response.text
    assert response.json()["auto_quarantine"] is False
    assert "nicht automatisch in die Quarantäne" in response.json()["hint"]
    [finding] = await _scan_findings()
    assert finding.status == "detected"
    assert finding.note == NO_EXECUTE_NOTE
    assert not any("shell.php" in command for command in calls)


async def test_scan_with_hosts_execute_quarantines(shield, infected_scan, db_session, test_settings):
    app, host_id, calls = shield
    token = await _token(db_session, test_settings, ["soc.manage", "hosts.execute"])
    response = await _start_scan(app, token, host_id, infected_scan)
    assert response.status_code == 200, response.text
    assert "auto_quarantine" not in response.json()
    [finding] = await _scan_findings()
    assert finding.note is None
    assert any("shell.php" in command for command in calls)


async def test_scheduled_scan_still_quarantines(shield, infected_scan):
    app, _, calls = shield
    await _shield_tables()
    defender = app.state.defender
    await defender.start_scans(await defender.target_hosts(), "quick", paths=None, trigger="schedule", wait=True)
    [finding] = await _scan_findings()
    assert finding.note is None
    assert any("shell.php" in command for command in calls)


async def test_excluding_a_folder_closes_its_findings_with_both_permissions(shield, db_session, test_settings):
    app, host_id, calls = shield
    await _register_shield({})
    await _detected_finding(host_id)
    token = await _token(db_session, test_settings, ["soc.manage", "hosts.execute", "extensions.manage"])
    response = await _post(app, token, "findings/exclude-folder", {"host_id": host_id, "folder": "/srv/daten/upload", "all_servers": True})
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "folder": "/srv/daten/upload", "closed": 1}
    assert await _finding_status() == "ignored"
    assert await _saved_exclude_paths() == ["/srv/daten/upload"]
    assert len(calls) == 3 and " stat " in f" {calls[0]} " and " find " in f" {calls[1]} " and "mountinfo" in calls[2] and all("LC_ALL=C" in c for c in calls)


async def test_excluding_a_folder_keeps_existing_exclusions(shield, db_session, test_settings):
    app, host_id, _ = shield
    await _register_shield({"exclude_paths": ["/srv/cache"]})
    await _detected_finding(host_id)
    token = await _token(db_session, test_settings, ["soc.manage", "hosts.execute", "extensions.manage"])
    response = await _post(app, token, "findings/exclude-folder", {"host_id": host_id, "folder": "/srv/daten/upload", "all_servers": True})
    assert response.status_code == 200, response.text
    assert await _saved_exclude_paths() == ["/srv/cache", "/srv/daten/upload"]


async def test_excluding_an_unknown_folder_is_refused(shield, db_session, test_settings):
    app, host_id, _ = shield
    await _detected_finding(host_id)
    token = await _token(db_session, test_settings, ["soc.manage", "hosts.execute", "extensions.manage"])
    response = await _post(app, token, "findings/exclude-folder", {"host_id": host_id, "folder": "/srv/anders", "all_servers": True})
    assert response.status_code == 409, response.text
    assert await _finding_status() == "detected"


async def test_excluding_a_folder_needs_the_settings_permission_too(shield, db_session, test_settings):
    app, host_id, _ = shield
    await _detected_finding(host_id)
    token = await _token(db_session, test_settings, ["soc.manage", "hosts.execute"])
    response = await _post(app, token, "findings/exclude-folder", {"host_id": host_id, "folder": "/srv/daten/upload", "all_servers": True})
    assert response.status_code == 403, response.text
    assert "extensions.manage" in response.json()["detail"]
    assert await _finding_status() == "detected"


async def test_excluding_a_folder_needs_the_all_servers_confirmation(shield, db_session, test_settings):
    app, host_id, calls = shield
    await _register_shield({})
    await _detected_finding(host_id)
    token = await _token(db_session, test_settings, ["soc.manage", "hosts.execute", "extensions.manage"])
    for body in ({"host_id": host_id, "folder": "/srv/daten/upload"},
                 {"host_id": host_id, "folder": "/srv/daten/upload", "all_servers": False}):
        response = await _post(app, token, "findings/exclude-folder", body)
        assert response.status_code == 409, response.text
        assert "alle Server" in response.json()["detail"]
    assert await _finding_status() == "detected"
    assert await _saved_exclude_paths() == []
    assert calls == []


async def test_excluding_a_system_folder_is_refused_through_the_api(shield, db_session, test_settings):
    from nodvard_deck_ext_shield.models import FindingRecord

    app, host_id, _ = shield
    await _register_shield({})
    await _shield_tables()
    async with session_scope() as current:
        current.add(FindingRecord(id="t1", host_id=host_id, host_name="pi", path="/tmp/a.sh", signature="X",
                                  status="detected", detected_at=utcnow()))
        await current.commit()
    token = await _token(db_session, test_settings, ["soc.manage", "hosts.execute", "extensions.manage"])
    response = await _post(app, token, "findings/exclude-folder", {"host_id": host_id, "folder": "/tmp", "all_servers": True})
    assert response.status_code == 409, response.text
    assert "Systemordner" in response.json()["detail"]
    assert await _saved_exclude_paths() == []
