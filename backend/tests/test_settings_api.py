"""GET /settings, PUT /settings/{key} -- Gap-Fill fuer das Gate, siehe
api/v1/settings.py Modul-Docstring fuer die volle Begruendung."""

from __future__ import annotations

import pytest


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    assert (await client.get("/api/v1/settings")).status_code == 401


@pytest.mark.asyncio
async def test_list_settings_returns_defaults_when_unset(client):
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert r.status_code == 200
    by_key = {row["key"]: row["value"] for row in r.json()}
    assert by_key == {
        "autonomy.mode": "propose", "autonomy.max_risk": "low",
        "security.deny_patterns": [], "maintenance.windows": [],
        # Vorgaben aus der Umgebung: Europe/Berlin ohne NODVARD_DECK_TIMEZONE/TZ, 90 Tage.
        "system.timezone": "Europe/Berlin", "audit.retention_days": 90,
        # Job-Verlauf: 30 Tage.
        "jobs.run_retention_days": 30,
        # Erreichbarkeitspruefung: an, alle 2 Minuten.
        "hosts.reachability.enabled": True, "hosts.reachability.interval_minutes": 2,
        # Nach Updates suchen: an, nur fertige Versionen.
        "system.update_check.enabled": True, "system.update_check.channel": "stable",
        # Neue Server-Schluessel bestaetigen: die Test-Einstellungen legen die Umgebungsvariable auf aus fest.
        "ssh.confirm_new_host_keys": False,
    }


@pytest.mark.asyncio
async def test_put_and_get_roundtrip(client):
    token = await _bootstrap_owner(client)
    put = await client.put(
        "/api/v1/settings/autonomy.mode", json={"value": "full"}, headers=_auth_header(token)
    )
    assert put.status_code == 200
    assert put.json() == {"key": "autonomy.mode", "value": "full"}

    listed = await client.get("/api/v1/settings", headers=_auth_header(token))
    by_key = {row["key"]: row["value"] for row in listed.json()}
    assert by_key["autonomy.mode"] == "full"


@pytest.mark.asyncio
async def test_put_rejects_invalid_autonomy_mode(client):
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/settings/autonomy.mode", json={"value": "yolo"}, headers=_auth_header(token)
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_rejects_wrong_type(client):
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/settings/security.deny_patterns", json={"value": "not-a-list"}, headers=_auth_header(token)
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_rejects_non_string_list_items(client):
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/settings/security.deny_patterns", json={"value": [123]}, headers=_auth_header(token)
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_maintenance_windows_roundtrip(client):
    token = await _bootstrap_owner(client)
    windows = [{"cron": "45 3 * * *", "duration_minutes": 60, "host_ids": "all"}]
    put = await client.put(
        "/api/v1/settings/maintenance.windows", json={"value": windows}, headers=_auth_header(token)
    )
    assert put.status_code == 200
    assert put.json()["value"] == windows

    listed = await client.get("/api/v1/settings", headers=_auth_header(token))
    by_key = {row["key"]: row["value"] for row in listed.json()}
    assert by_key["maintenance.windows"] == windows


@pytest.mark.asyncio
async def test_put_maintenance_windows_rejects_invalid_cron(client):
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/settings/maintenance.windows",
        json={"value": [{"cron": "not-a-cron", "duration_minutes": 60}]},
        headers=_auth_header(token),
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_maintenance_windows_rejects_missing_duration(client):
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/settings/maintenance.windows",
        json={"value": [{"cron": "0 3 * * *"}]},
        headers=_auth_header(token),
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_put_unknown_key_is_rejected(client):
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/settings/not.a.managed.key", json={"value": "x"}, headers=_auth_header(token)
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_settings_require_settings_write_permission(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    await _bootstrap_owner(client)
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.flush()

    login = await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
    token = login.json()["access_token"]

    assert (await client.get("/api/v1/settings", headers=_auth_header(token))).status_code == 403
    assert (
        await client.put(
            "/api/v1/settings/autonomy.mode", json={"value": "full"}, headers=_auth_header(token)
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_list_settings_survives_saved_branding(client):
    """Branding legt rohe Werte in dieselbe Tabelle -- GET /settings lief danach auf 500."""
    token = await _bootstrap_owner(client)
    branding = (await client.get("/api/v1/branding")).json()
    assert (await client.put("/api/v1/branding", json={**branding, "product_name": "Muster"}, headers=_auth_header(token))).status_code == 200
    r = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert {row["key"] for row in r.json()} == {
        "autonomy.mode", "autonomy.max_risk", "security.deny_patterns", "maintenance.windows",
        "system.timezone", "audit.retention_days", "jobs.run_retention_days",
        "hosts.reachability.enabled", "hosts.reachability.interval_minutes",
        "system.update_check.enabled", "system.update_check.channel", "ssh.confirm_new_host_keys",
    }


def test_gate_errors_are_never_blank():
    import asyncio

    from nodvard_deck.core.gate import describe_error

    assert describe_error(asyncio.TimeoutError()).startswith("Zeitüberschreitung")
    assert "nicht erreichbar" in describe_error(ConnectionResetError())
    assert describe_error(RuntimeError("kaputt")) == "kaputt"
    assert describe_error(KeyError()) == "KeyError"


@pytest.mark.asyncio
async def test_audit_can_hide_automatic_entries(client, db_session):
    from nodvard_deck.services import audit as audit_service

    token = await _bootstrap_owner(client)
    for action in ("secret.used", "secret.used", "login.succeeded"):
        await audit_service.log(db_session, actor_type="extension", actor_id="proxmox", action=action, outcome="success")
    await db_session.flush()
    r = await client.get("/api/v1/audit?exclude_action=secret.used", headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert "secret.used" not in {e["action"] for e in r.json()}
    assert "login.succeeded" in {e["action"] for e in r.json()}


@pytest.mark.asyncio
async def test_put_maintenance_windows_accepts_seven_as_sunday(client):
    token = await _bootstrap_owner(client)
    windows = [{"cron": "0 3 * * 7", "duration_minutes": 60, "host_ids": "all"}]
    r = await client.put(
        "/api/v1/settings/maintenance.windows", json={"value": windows}, headers=_auth_header(token)
    )
    assert r.status_code == 200
    assert r.json()["value"] == windows


@pytest.mark.asyncio
async def test_put_maintenance_windows_rejects_weekday_eight(client):
    token = await _bootstrap_owner(client)
    r = await client.put(
        "/api/v1/settings/maintenance.windows",
        json={"value": [{"cron": "0 3 * * 8", "duration_minutes": 60}]},
        headers=_auth_header(token),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# system.timezone und audit.retention_days
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_timezone_roundtrip_and_moves_jobs(client, db_session):
    from nodvard_deck.core.scheduler import get_scheduler_service
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.services import jobs as jobs_service

    token = await _bootstrap_owner(client)
    get_scheduler_service().start()

    async def handler(**_):
        return "ok"

    job = await jobs_service.upsert_job(
        db_session, ext_id="shield", ext_job_key="defender-briefing", name="Briefing", kind="ext",
        schedule="0 7 * * *", params={}, enabled=True,
    )
    get_extension_runtime().scheduler.register("shield", "defender-briefing", handler)
    await get_scheduler_service().schedule(job, handler)
    await db_session.refresh(job)
    before = job.next_run_at

    put = await client.put(
        "/api/v1/settings/system.timezone", json={"value": "Asia/Tokyo"}, headers=_auth_header(token)
    )
    assert put.status_code == 200, put.text
    assert put.json() == {"key": "system.timezone", "value": "Asia/Tokyo"}

    listed = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert {r["key"]: r["value"] for r in listed.json()}["system.timezone"] == "Asia/Tokyo"
    await db_session.refresh(job)
    assert job.timezone == "Asia/Tokyo"
    assert job.next_run_at != before
    jobs = (await client.get("/api/v1/jobs", headers=_auth_header(token))).json()
    assert [j["timezone"] for j in jobs if j["id"] == job.id] == ["Asia/Tokyo"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["Mitteleuropa/Nirgendwo", "", "europe/berlin", 5, None, ["Europe/Berlin"]])
async def test_put_timezone_rejects_invalid_zone_with_422(client, bad):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/settings/system.timezone", json={"value": bad}, headers=_auth_header(token))
    assert r.status_code == 422
    listed = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert {row["key"]: row["value"] for row in listed.json()}["system.timezone"] == "Europe/Berlin"


@pytest.mark.asyncio
async def test_timezone_default_comes_from_the_environment(client, test_settings, monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    test_settings.timezone = None
    monkeypatch.setattr("nodvard_deck.config._settings", test_settings)
    token = await _bootstrap_owner(client)
    listed = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert {row["key"]: row["value"] for row in listed.json()}["system.timezone"] == "America/New_York"


@pytest.mark.asyncio
async def test_put_retention_roundtrip_overrides_environment_value(client, test_settings):
    test_settings.audit_retention_days = 45
    token = await _bootstrap_owner(client)
    before = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert {r["key"]: r["value"] for r in before.json()}["audit.retention_days"] == 45

    put = await client.put("/api/v1/settings/audit.retention_days", json={"value": 365}, headers=_auth_header(token))
    assert put.status_code == 200, put.text
    assert put.json() == {"key": "audit.retention_days", "value": 365}
    after = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert {r["key"]: r["value"] for r in after.json()}["audit.retention_days"] == 365


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, 6, -1, 3651, 100000, "90", 90.5, True, None])
async def test_put_retention_rejects_values_outside_the_limits_with_422(client, bad):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/settings/audit.retention_days", json={"value": bad}, headers=_auth_header(token))
    assert r.status_code == 422, (bad, r.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("good", [7, 3650])
async def test_put_retention_accepts_both_limits(client, good):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/settings/audit.retention_days", json={"value": good}, headers=_auth_header(token))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_put_job_run_retention_roundtrip(client):
    token = await _bootstrap_owner(client)
    before = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert {r["key"]: r["value"] for r in before.json()}["jobs.run_retention_days"] == 30

    put = await client.put("/api/v1/settings/jobs.run_retention_days", json={"value": 120}, headers=_auth_header(token))
    assert put.status_code == 200, put.text
    assert put.json() == {"key": "jobs.run_retention_days", "value": 120}
    after = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert {r["key"]: r["value"] for r in after.json()}["jobs.run_retention_days"] == 120


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, 3651, 100000, "30", 30.5, True, False, None, [30]])
async def test_put_job_run_retention_rejects_invalid_values_with_422(client, bad):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/settings/jobs.run_retention_days", json={"value": bad}, headers=_auth_header(token))
    assert r.status_code == 422, (bad, r.text)
    after = await client.get("/api/v1/settings", headers=_auth_header(token))
    assert {r["key"]: r["value"] for r in after.json()}["jobs.run_retention_days"] == 30  # nichts gespeichert


@pytest.mark.asyncio
@pytest.mark.parametrize("good", [1, 3650])
async def test_put_job_run_retention_accepts_both_limits(client, good):
    token = await _bootstrap_owner(client)
    r = await client.put("/api/v1/settings/jobs.run_retention_days", json={"value": good}, headers=_auth_header(token))
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_changing_job_run_retention_is_written_to_the_audit_log(client):
    token = await _bootstrap_owner(client)
    await client.put("/api/v1/settings/jobs.run_retention_days", json={"value": 14}, headers=_auth_header(token))
    await client.put("/api/v1/settings/jobs.run_retention_days", json={"value": 60}, headers=_auth_header(token))
    await client.put("/api/v1/settings/jobs.run_retention_days", json={"value": 0}, headers=_auth_header(token))  # 422
    entries = (await client.get("/api/v1/audit?action=system.settings.changed", headers=_auth_header(token))).json()
    details = [e["detail"] for e in entries if e["target_id"] == "jobs.run_retention_days"]
    assert sorted((d["old"], d["new"]) for d in details) == [(14, 60), (30, 14)]
    assert all(d["key"] == "jobs.run_retention_days" for d in details)
    assert all(e["outcome"] == "success" and e["target_type"] == "setting" for e in entries)


@pytest.mark.asyncio
async def test_changing_system_settings_is_written_to_the_audit_log(client):
    token = await _bootstrap_owner(client)
    await client.put("/api/v1/settings/audit.retention_days", json={"value": 30}, headers=_auth_header(token))
    await client.put("/api/v1/settings/system.timezone", json={"value": "UTC"}, headers=_auth_header(token))
    entries = (await client.get("/api/v1/audit?action=system.settings.changed", headers=_auth_header(token))).json()
    by_key = {e["target_id"]: e["detail"] for e in entries}
    assert by_key["audit.retention_days"]["old"] == 90 and by_key["audit.retention_days"]["new"] == 30
    assert by_key["system.timezone"]["old"] == "Europe/Berlin" and by_key["system.timezone"]["new"] == "UTC"


@pytest.mark.asyncio
async def test_system_settings_keep_the_settings_write_permission(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    await _bootstrap_owner(client)
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer2", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.flush()
    login = await client.post("/api/v1/auth/login", json={"username": "viewer2", "password": "whatever123"})
    token = login.json()["access_token"]
    for key, value in (("system.timezone", "UTC"), ("audit.retention_days", 30), ("jobs.run_retention_days", 10)):
        r = await client.put(f"/api/v1/settings/{key}", json={"value": value}, headers=_auth_header(token))
        assert r.status_code == 403
