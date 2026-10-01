"""Nach Updates suchen: `GET /system/updates`, `POST /system/updates/check`, Einstellungen und Kern-Job.

Kein echter Netzzugriff: `core.updates._make_client` bekommt einen `httpx.MockTransport`."""

from __future__ import annotations

import httpx
import pytest
from nodvard_deck.core import updates
from nodvard_deck.version import __version__

OWNER_PW = "correct-horse-battery"


def _bump(version: str) -> str:
    major, minor, _patch = version.split("-")[0].split(".")
    return f"{major}.{int(minor) + 1}.0"


NEWER = _bump(__version__)


@pytest.fixture
def registry(monkeypatch):
    """ghcr.io-Attrappe; `state["tags"]` und `state["fail"]` steuern die Antworten, `state["calls"]` zaehlt."""
    state: dict = {"tags": [__version__, NEWER, f"{_bump(NEWER)}-rc1", "latest"], "fail": False, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["fail"]:
            raise httpx.ConnectError("offline", request=request)
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "t"})
        if request.url.path.endswith("/tags/list"):
            return httpx.Response(200, json={"tags": state["tags"]})
        return httpx.Response(200, content=b"{}")

    real = updates._make_client
    monkeypatch.setattr(updates, "_make_client", lambda transport=None: real(httpx.MockTransport(handler)))
    return state


async def _owner(client) -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": OWNER_PW, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": OWNER_PW})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user_with_role(client, db_session, role: str, username: str) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password("whatever-1234"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever-1234"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_requires_login_and_system_read(client, db_session, registry):
    assert (await client.get("/api/v1/system/updates")).status_code == 401
    assert (await client.post("/api/v1/system/updates/check")).status_code == 401
    await _owner(client)
    viewer = await _user_with_role(client, db_session, "viewer", "viewer1")
    assert (await client.get("/api/v1/system/updates", headers=viewer)).status_code == 403
    assert (await client.post("/api/v1/system/updates/check", headers=viewer)).status_code == 403
    admin = await _user_with_role(client, db_session, "admin", "admin1")
    assert (await client.get("/api/v1/system/updates", headers=admin)).status_code == 200
    assert registry["calls"] == 0, "Ansehen fragt nie im Netz nach"


@pytest.mark.asyncio
async def test_get_without_a_check_shows_the_current_version_only(client, registry):
    owner = await _owner(client)
    body = (await client.get("/api/v1/system/updates", headers=owner)).json()
    assert body["current"] == __version__
    assert body["latest"] is None and body["available"] is False and body["checked_at"] is None
    assert body["source"] == "cache" and body["error"] is None
    assert body["channel"] == "stable" and body["enabled"] is True
    assert body["official_image"] is False and body["image"] is None
    assert body["official_image_name"] == "ghcr.io/nodvard/deck"
    assert body["helper"] is False
    assert registry["calls"] == 0


@pytest.mark.asyncio
async def test_check_finds_a_newer_version_and_get_returns_it_from_the_cache(client, registry, test_settings):
    owner = await _owner(client)
    r = await client.post("/api/v1/system/updates/check", headers=owner)
    assert r.status_code == 200, r.text
    live = r.json()
    assert live["source"] == "live" and live["latest"] == NEWER and live["available"] is True
    assert live["release_notes_url"] == f"https://github.com/nodvard/deck/blob/v{NEWER}/CHANGELOG.md"
    assert (test_settings.data_dir / updates.CACHE_NAME).is_file()
    cached = (await client.get("/api/v1/system/updates", headers=owner)).json()
    assert cached["source"] == "cache" and cached["latest"] == NEWER and cached["checked_at"] == live["checked_at"]


@pytest.mark.asyncio
async def test_check_is_throttled_to_one_per_minute(client, registry):
    owner = await _owner(client)
    assert (await client.post("/api/v1/system/updates/check", headers=owner)).status_code == 200
    calls = registry["calls"]
    second = await client.post("/api/v1/system/updates/check", headers=owner)
    assert second.status_code == 429
    assert "Minute" in second.json()["detail"]
    assert second.headers.get("retry-after")
    assert registry["calls"] == calls, "gedrosselt heisst: keine Anfrage an die Registry"


@pytest.mark.asyncio
async def test_offline_check_keeps_the_last_result(client, registry):
    from nodvard_deck.core import rate_limit

    owner = await _owner(client)
    await client.post("/api/v1/system/updates/check", headers=owner)
    rate_limit.reset_all()
    registry["fail"] = True
    r = await client.post("/api/v1/system/updates/check", headers=owner)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "offline" and body["error"] == updates.OFFLINE_TEXT and body["latest"] == NEWER
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["source"] == "offline"


@pytest.mark.asyncio
async def test_official_image_from_the_environment(client, registry, test_settings):
    owner = await _owner(client)
    test_settings.image = updates.OFFICIAL_IMAGE
    body = (await client.get("/api/v1/system/updates", headers=owner)).json()
    assert body["official_image"] is True and body["image"] == updates.OFFICIAL_IMAGE
    info = (await client.get("/api/v1/system/info", headers=owner)).json()
    assert info["image"] == updates.OFFICIAL_IMAGE
    test_settings.image = "nodvard-deck"
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["official_image"] is False


async def _check_again(client, headers) -> dict:
    from nodvard_deck.core import rate_limit

    rate_limit.reset_all()
    r = await client.post("/api/v1/system/updates/check", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_prerelease_image_compares_with_its_own_version(client, registry, test_settings):
    """Das offizielle rc1-Image vergleicht mit 0.6.0-rc1 (aus der Datei im Image), nicht mit `__version__`."""
    owner = await _owner(client)
    test_settings.image = updates.OFFICIAL_IMAGE
    test_settings.build = "0.6.0-rc1"
    assert (await client.put("/api/v1/settings/system.update_check.channel", json={"value": "beta"}, headers=owner)).status_code == 200

    registry["tags"] = ["0.5.0", "0.6.0-rc1", "latest"]
    same = await _check_again(client, owner)
    assert same["current"] == "0.6.0-rc1" and same["latest"] == "0.6.0-rc1"
    assert same["available"] is False, "meldet sich nicht selbst als Update"
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["current"] == "0.6.0-rc1"

    registry["tags"] = ["0.5.0", "0.6.0-rc1", "0.6.0-rc2"]
    rc2 = await _check_again(client, owner)
    assert rc2["latest"] == "0.6.0-rc2" and rc2["available"] is True

    registry["tags"] = ["0.5.0", "0.6.0-rc1", "0.6.0-rc2", "0.6.0"]
    final = await _check_again(client, owner)
    assert final["latest"] == "0.6.0" and final["available"] is True

    assert (await client.put("/api/v1/settings/system.update_check.channel", json={"value": "stable"}, headers=owner)).status_code == 200
    stable = await _check_again(client, owner)
    assert stable["latest"] == "0.6.0" and stable["available"] is True, "auch im Kanal stable sieht rc1 die fertige Version"


@pytest.mark.asyncio
async def test_image_file_beats_a_stale_build_variable(client, registry, test_settings, tmp_path):
    """Eine von Hand oder aus einem alten Container mitgenommene `NODVARD_DECK_BUILD` darf den Vergleich nicht
    verfaelschen: Beim offiziellen Image gilt die Version aus der Datei im Image."""
    import json

    info = tmp_path / "image-info.json"
    info.write_text(json.dumps({"image": updates.OFFICIAL_IMAGE, "version": __version__}), encoding="utf-8")
    owner = await _owner(client)
    test_settings.image = updates.OFFICIAL_IMAGE
    test_settings.build = "0.0.1"
    test_settings.image_info_path = info
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["current"] == __version__
    assert (await _check_again(client, owner))["current"] == __version__


@pytest.mark.asyncio
@pytest.mark.parametrize("image,build", [(None, "9.9.9"), ("nodvard-deck", "9.9.9"), (updates.OFFICIAL_IMAGE, "abc123"), (updates.OFFICIAL_IMAGE, None)])
async def test_other_images_compare_with_the_code_version(client, registry, test_settings, image, build):
    owner = await _owner(client)
    test_settings.image = image
    test_settings.build = build
    body = (await client.get("/api/v1/system/updates", headers=owner)).json()
    assert body["current"] == __version__
    assert (await _check_again(client, owner))["current"] == __version__


@pytest.mark.asyncio
async def test_settings_switch_and_channel(client, registry, db_session):
    from nodvard_deck.models import Job
    from nodvard_deck.services import update_check
    from sqlalchemy import select

    owner = await _owner(client)
    rows = {r["key"]: r["value"] for r in (await client.get("/api/v1/settings", headers=owner)).json()}
    assert rows["system.update_check.enabled"] is True
    assert rows["system.update_check.channel"] == "stable"

    assert (await client.put("/api/v1/settings/system.update_check.channel", json={"value": "nightly"}, headers=owner)).status_code == 422
    assert (await client.put("/api/v1/settings/system.update_check.enabled", json={"value": "ja"}, headers=owner)).status_code == 422

    r = await client.put("/api/v1/settings/system.update_check.channel", json={"value": "beta"}, headers=owner)
    assert r.status_code == 200, r.text
    live = (await client.post("/api/v1/system/updates/check", headers=owner)).json()
    assert live["channel"] == "beta" and live["latest"] == f"{_bump(NEWER)}-rc1"

    r = await client.put("/api/v1/settings/system.update_check.enabled", json={"value": False}, headers=owner)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["enabled"] is False
    job = (await db_session.execute(select(Job).where(Job.ext_job_key == update_check.JOB_KEY))).scalar_one()
    await db_session.refresh(job)
    assert job.enabled is False and job.kind == "core"


@pytest.mark.asyncio
async def test_core_job_is_registered_and_runs_the_check(client, registry, db_session, test_settings, monkeypatch):
    from nodvard_deck import config
    from nodvard_deck.core.scheduler import CORE_SCHEDULER_EXT_ID, register_core_jobs
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.models import Job
    from nodvard_deck.services import update_check
    from sqlalchemy import select

    monkeypatch.setattr(config, "_settings", test_settings)
    await register_core_jobs(test_settings.data_dir / "runs")
    job = (await db_session.execute(select(Job).where(Job.ext_job_key == update_check.JOB_KEY))).scalar_one()
    assert job.ext_id is None and job.enabled and job.schedule.count(" ") == 4
    handler = get_extension_runtime().scheduler.get(CORE_SCHEDULER_EXT_ID, update_check.JOB_KEY)
    result = await handler()
    assert result["latest"] == NEWER and result["available"] is True and result["source"] == "live"


@pytest.mark.asyncio
async def test_core_job_cannot_be_switched_via_jobs_api(client, registry, db_session, test_settings, monkeypatch):
    from nodvard_deck import config
    from nodvard_deck.core.scheduler import register_core_jobs
    from nodvard_deck.models import Job
    from nodvard_deck.services import update_check
    from sqlalchemy import select

    owner = await _owner(client)
    monkeypatch.setattr(config, "_settings", test_settings)
    await register_core_jobs(test_settings.data_dir / "runs")
    job = (await db_session.execute(select(Job).where(Job.ext_job_key == update_check.JOB_KEY))).scalar_one()
    r = await client.patch(f"/api/v1/jobs/{job.id}", json={"enabled": False}, headers=owner)
    assert r.status_code == 409
    assert "System" in r.json()["detail"]


@pytest.mark.asyncio
async def test_core_job_cannot_be_run_around_the_check_throttle(client, registry, db_session, test_settings, monkeypatch):
    """`POST /jobs/{id}/run` darf die Minuten-Drossel von `/system/updates/check` nicht umgehen: Kern-Jobs lassen
    sich dort nicht ausloesen, der Job nennt stattdessen den Knopf unter Einstellungen -> System -> Updates."""
    from nodvard_deck import config
    from nodvard_deck.core.scheduler import register_core_jobs
    from nodvard_deck.models import Job
    from nodvard_deck.services import update_check
    from sqlalchemy import select

    owner = await _owner(client)
    monkeypatch.setattr(config, "_settings", test_settings)
    await register_core_jobs(test_settings.data_dir / "runs")
    job = (await db_session.execute(select(Job).where(Job.ext_job_key == update_check.JOB_KEY))).scalar_one()
    before = registry["calls"]
    r = await client.post(f"/api/v1/jobs/{job.id}/run", headers=owner)
    assert r.status_code == 409
    assert "Updates" in r.json()["detail"] and "Jetzt suchen" in r.json()["detail"]
    assert registry["calls"] == before, "es wurde nichts bei der Registry angefragt"
    assert (await client.delete(f"/api/v1/jobs/{job.id}", headers=owner)).status_code == 409
