"""Einstellungen -> System -> Sicherung: /api/v1/system/... (services/backups.py).

Laeuft gegen eine echte Datei-SQLite (`file_db`), weil die Online-Kopie eine Datei braucht.
Schnelle KDF-Parameter (das Verfahren selbst prueft test_core_backup.py)."""

from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from nodvard_deck.core.backup import container, crypto, store
from nodvard_deck.core.backup import format as fmt
from nodvard_deck.core.backup.tickets import get_ticket_registry
from nodvard_deck.services import backups as backups_service

OWNER_PW = "correct-horse-battery"
BACKUP_PW = "mein-sicherungs-passwort"
ONE_TIME_PW = "einmal-passwort-fuer-den-download"


@pytest.fixture
def env(client, file_db, test_settings, tmp_path, monkeypatch):
    """Owner-Installation mit Datei-DB, eigenem `/backups`, schneller KDF."""
    from nodvard_deck import config

    db_file = tmp_path / "lattice-file.db"
    test_settings.database_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setattr(config, "_settings", test_settings)
    external = tmp_path / "extern"
    external.mkdir()
    monkeypatch.setattr(store, "EXTERNAL_ROOT", external)
    monkeypatch.setattr(crypto, "ARGON2_T", 1)
    monkeypatch.setattr(crypto, "ARGON2_M_KIB", 1024)
    monkeypatch.setattr(crypto, "SCRYPT_WRITE_LOG_N", 10)
    (test_settings.ext_data_dir / "beispiel").mkdir(parents=True)
    (test_settings.ext_data_dir / "beispiel" / "dokument.txt").write_text("Inhalt")
    test_settings.master_key_path.write_bytes(b"MASTER")
    times = iter(datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=i) for i in range(1000))

    async def _fake_now(session):
        return next(times)

    monkeypatch.setattr(backups_service, "_local_now", _fake_now)
    backups_service.reset_for_tests()
    yield {"settings": test_settings, "external": external, "sessionmaker": file_db}
    backups_service.reset_for_tests()


async def _owner(client) -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": OWNER_PW, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": OWNER_PW})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user_with_role(client, sessionmaker, role: str, username: str) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    async with sessionmaker() as session:
        roles = await auth_service.ensure_builtin_roles(session)
        user = User(username=username, password_hash=security.hash_password("whatever-1234"), is_active=True)
        user.roles.append(roles[role])
        session.add(user)
        await session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever-1234"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _set_key(client, headers) -> dict:
    r = await client.put("/api/v1/system/backups/key", json={"current_password": OWNER_PW, "password": BACKUP_PW}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _run(client, headers) -> None:
    r = await client.post("/api/v1/system/backups/run", headers=headers)
    assert r.status_code == 202, r.text
    await backups_service.wait_for_tasks()


async def _overview(client, headers) -> dict:
    r = await client.get("/api/v1/system/backups", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _read(blob: bytes, secret: str, dest: Path) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    return container.read_backup(io.BufferedReader(io.BytesIO(blob)), secret, dest)


# ---------------------------------------------------------------------------
# Rechte
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requires_login(client):
    assert (await client.get("/api/v1/system/backups")).status_code == 401
    assert (await client.get("/api/v1/system/info")).status_code == 401


@pytest.mark.asyncio
async def test_viewer_cannot_see_and_admin_without_owner_gets_403_for_critical_actions(client, env):
    await _owner(client)
    viewer = await _user_with_role(client, env["sessionmaker"], "viewer", "viewer1")
    admin = await _user_with_role(client, env["sessionmaker"], "admin", "admin1")
    assert (await client.get("/api/v1/system/backups", headers=viewer)).status_code == 403
    assert (await client.get("/api/v1/system/backups", headers=admin)).status_code == 200
    assert (await client.get("/api/v1/system/info", headers=admin)).status_code == 200
    body = {"current_password": "whatever-1234"}
    for method, url, payload in [
        ("PUT", "/api/v1/system/backups/key", {**body, "password": BACKUP_PW}),
        ("PUT", "/api/v1/system/backups/config", {"enabled": False, "schedule": "0 3 * * *", "keep": 3}),
        ("POST", "/api/v1/system/backups/run", None),
        ("POST", "/api/v1/system/backups/download", {**body, "mode": "passwort", "password": ONE_TIME_PW}),
        ("POST", "/api/v1/system/backups/nodvard-deck-sicherung-20261001-030000.ndbak/ticket", body),
        ("POST", "/api/v1/system/backups/nodvard-deck-sicherung-20261001-030000.ndbak/verify", None),
        ("DELETE", "/api/v1/system/backups/nodvard-deck-sicherung-20261001-030000.ndbak", body),
    ]:
        r = await client.request(method, url, json=payload, headers=admin)
        assert r.status_code == 403, (method, url, r.text)
        assert "Owner" in r.json()["detail"]


@pytest.mark.asyncio
async def test_missing_password_is_403_and_wrong_password_is_throttled(client, env):
    owner = await _owner(client)
    r = await client.put("/api/v1/system/backups/key", json={"password": BACKUP_PW}, headers=owner)
    assert r.status_code == 403
    codes = []
    for _ in range(12):
        r = await client.put("/api/v1/system/backups/key", json={"current_password": "falsch", "password": BACKUP_PW}, headers=owner)
        codes.append(r.status_code)
    assert codes[0] == 400 and codes[-1] == 429
    assert (await _overview(client, owner))["key"] is None


# ---------------------------------------------------------------------------
# Schluessel und Einstellungen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_key_returns_recovery_key_once_and_stores_no_secret(client, env):
    owner = await _owner(client)
    short = await client.put("/api/v1/system/backups/key", json={"current_password": OWNER_PW, "password": "zu-kurz"}, headers=owner)
    assert short.status_code == 422
    out = await _set_key(client, owner)
    assert crypto.is_recovery_key(out["recovery_key"]) and out["recipient"].startswith("age1")
    overview = await _overview(client, owner)
    assert overview["key"]["key_id"] == out["key_id"]
    text = json.dumps(overview)
    assert out["recovery_key"] not in text and BACKUP_PW not in text
    # In der Datenbank: Empfaenger, Salz, Parameter -- kein Passwort, kein privater Schluessel.
    from nodvard_deck.services import settings as settings_service

    async with env["sessionmaker"]() as session:
        stored = await settings_service.get_global(session, backups_service.SETTING_KEY)
    assert set(stored) == {"recipient", "key_id", "kdf", "created_at"}
    assert out["recovery_key"] not in json.dumps(stored) and BACKUP_PW not in json.dumps(stored)
    # Aus Passwort + gespeicherten Parametern entsteht wieder derselbe Schluessel.
    again = crypto.derive_key(BACKUP_PW, crypto.KdfParams.from_dict(stored["kdf"]))
    assert again.identity == out["recovery_key"]


@pytest.mark.asyncio
async def test_config_rejects_directories_outside_the_allowlist(client, env, tmp_path):
    owner = await _owner(client)
    await _set_key(client, owner)
    base = {"current_password": OWNER_PW, "enabled": True, "schedule": "0 3 * * *", "keep": 5}
    external: Path = env["external"]
    (tmp_path / "anderswo").mkdir()
    (external / "link").symlink_to(tmp_path / "anderswo")
    for bad in ["/etc", "/app", str(tmp_path / "anderswo"), f"{external}/../anderswo", f"{external}/link",
                f"{external}/link/unter", "relativ/pfad"]:
        r = await client.put("/api/v1/system/backups/config", json={**base, "dir": bad}, headers=owner)
        assert r.status_code == 422, (bad, r.text)
    for keep in (0, 61):
        r = await client.put("/api/v1/system/backups/config", json={**base, "keep": keep}, headers=owner)
        assert r.status_code == 422
    r = await client.put("/api/v1/system/backups/config", json={**base, "schedule": "kein cron"}, headers=owner)
    assert r.status_code == 422
    ok = await client.put("/api/v1/system/backups/config", json={**base, "dir": f"{external}/nas"}, headers=owner)
    assert ok.status_code == 200, ok.text
    assert ok.json()["config"]["dir"] == f"{external}/nas"
    assert oct((external / "nas").stat().st_mode & 0o777) == "0o700"


@pytest.mark.asyncio
async def test_dangerous_config_changes_need_the_account_password(client, env):
    owner = await _owner(client)
    await _set_key(client, owner)
    url = "/api/v1/system/backups/config"
    external: Path = env["external"]
    default_dir = str(store.default_dir(env["settings"].data_dir))
    base = {"enabled": True, "schedule": "0 3 * * *", "keep": 7, "include_runs": False}

    # Harmlos, ohne Passwort: einschalten, Zeitplan, mehr behalten, Laufprotokolle, Ordner gleich geschrieben.
    for change in ({}, {"schedule": "15 4 * * *"}, {"keep": 10}, {"include_runs": True}, {"dir": default_dir}, {"dir": default_dir + "/"}):
        r = await client.put(url, json={**base, **change}, headers=owner)
        assert r.status_code == 200, (change, r.text)
        base.update(change)
    assert (await _overview(client, owner))["config"]["keep"] == 10

    # Gefaehrlich: ohne Passwort (fehlt oder leer) 403 mit lesbarem Text, nichts wird gespeichert.
    cases = [
        ({"keep": 9}, "weniger Sicherungen behalten"),
        ({"enabled": False}, "automatische Sicherung ausschalten"),
        ({"dir": f"{external}/nas"}, "Ordner ändern"),
        ({"keep": 3, "enabled": False, "dir": f"{external}/nas"}, "weniger Sicherungen behalten, die automatische"),
    ]
    for change, text in cases:
        for pw in ({}, {"current_password": ""}):
            r = await client.put(url, json={**base, **change, **pw}, headers=owner)
            assert r.status_code == 403, (change, r.text)
            assert text in r.json()["detail"] and "Passwort" in r.json()["detail"]
    assert not (external / "nas").exists()  # ohne Passwort auch kein Ordner angelegt
    config = (await _overview(client, owner))["config"]
    assert config["keep"] == 10 and config["enabled"] is True

    # Falsches Passwort: 400, richtiges: es geht durch.
    wrong = await client.put(url, json={**base, "keep": 9, "current_password": "falsch"}, headers=owner)
    assert wrong.status_code == 400
    assert (await _overview(client, owner))["config"]["keep"] == 10
    for change in ({"keep": 9}, {"enabled": False}, {"dir": f"{external}/nas"}):
        r = await client.put(url, json={**base, **change, "current_password": OWNER_PW}, headers=owner)
        assert r.status_code == 200, (change, r.text)
        base.update(change)
    config = (await _overview(client, owner))["config"]
    assert config["keep"] == 9 and config["enabled"] is False and config["dir"] == f"{external}/nas"
    # Zurueck in den Datenordner ist ebenfalls eine Ordner-Aenderung.
    r = await client.put(url, json={**base, "dir": None}, headers=owner)
    assert r.status_code == 403
    # Das Passwort steht nicht im Audit-Protokoll.
    audit = json.dumps((await client.get("/api/v1/audit?limit=500", headers=owner)).json())
    assert OWNER_PW not in audit and "system.backup.config_changed" in audit


@pytest.mark.asyncio
async def test_config_password_check_is_throttled_like_the_others(client, env):
    owner = await _owner(client)
    await _set_key(client, owner)
    body = {"enabled": True, "schedule": "0 3 * * *", "keep": 1, "current_password": "falsch"}
    codes = [(await client.put("/api/v1/system/backups/config", json=body, headers=owner)).status_code for _ in range(12)]
    assert codes[0] == 400 and codes[-1] == 429


@pytest.mark.asyncio
async def test_status_id_is_separate_from_the_ticket(client, env, tmp_path):
    owner = await _owner(client)
    ticket = await _prepare(client, owner, mode="passwort", password=ONE_TIME_PW)
    assert ticket["job_id"] and ticket["job_id"] != ticket["ticket"] and ticket["job_id"] not in ticket["url"]
    # Die Status-ID taugt nicht zum Herunterladen und verbraucht nichts.
    assert (await client.get(f"/api/v1/system/backups/download/{ticket['job_id']}")).status_code == 404
    status = await client.get(f"/api/v1/system/backups/download-jobs/{ticket['job_id']}", headers=owner)
    assert status.status_code == 200 and status.json()["ticket"] == ticket["ticket"]
    assert status.headers["cache-control"] == "no-store"
    # Das Ticket ist keine Status-ID, ohne Anmeldung gibt es keinen Stand, fremde IDs auch nicht.
    assert (await client.get(f"/api/v1/system/backups/download-jobs/{ticket['ticket']}", headers=owner)).status_code == 404
    assert (await client.get(f"/api/v1/system/backups/download-jobs/{ticket['job_id']}")).status_code == 401
    foreign = get_ticket_registry().create(user_id="jemand-anderes", path=tmp_path / "x", filename="x", delete_after=False, ready=True)
    assert (await client.get(f"/api/v1/system/backups/download-jobs/{foreign.job_id}", headers=owner)).status_code == 404
    # Der alte Status-Pfad (im API-Vertrag) bleibt als Uebergang.
    old = await client.get(f"/api/v1/system/backups/download/{ticket['ticket']}/status", headers=owner)
    assert old.status_code == 200 and old.json()["job_id"] == ticket["job_id"]
    # Nach dem Herunterladen ist auch der Stand weg.
    assert (await client.get(ticket["url"])).status_code == 200
    assert (await client.get(f"/api/v1/system/backups/download-jobs/{ticket['job_id']}", headers=owner)).status_code == 404


@pytest.mark.asyncio
async def test_skipped_symlinks_show_up_as_warning_in_last_run(client, env, tmp_path):
    ext = env["settings"].ext_data_dir / "beispiel"
    owner = await _owner(client)
    await _set_key(client, owner)
    await _run(client, owner)
    ok = (await _overview(client, owner))["last_run"]
    assert ok["ok"] is True and ok["warnings"] == []

    (ext / "verweis").symlink_to(tmp_path)
    for i in range(7):
        (ext / f"datei{i}").symlink_to(ext / "dokument.txt")
    await _run(client, owner)
    last = (await _overview(client, owner))["last_run"]
    assert last["ok"] is True and last["name"]
    [warning] = last["warnings"]
    assert warning.startswith("8 Verknüpfungen in Erweiterungsdaten nicht gesichert: ")
    assert "ext/beispiel/verweis" in warning and warning.endswith("und 3 weitere")
    assert warning.count("ext/beispiel/") == 5
    audit = (await client.get("/api/v1/audit?action=system.backup.created", headers=owner)).json()
    assert audit[0]["detail"]["warnings"] == [warning]
    # Ohne Verknuepfungen verschwindet die Warnung wieder.
    for entry in ext.iterdir():
        if entry.is_symlink():
            entry.unlink()
    await _run(client, owner)
    assert (await _overview(client, owner))["last_run"]["warnings"] == []


def test_link_warning_text():
    assert backups_service.link_warnings([]) == []
    assert backups_service.link_warnings(["ext/a/b"]) == ["1 Verknüpfung in Erweiterungsdaten nicht gesichert: ext/a/b"]
    assert backups_service.link_warnings(["ext/a", "master.key"]) == ["2 Verknüpfungen nicht gesichert: ext/a, master.key"]


@pytest.mark.asyncio
async def test_enabling_without_key_is_409_and_job_follows_the_config(client, env):
    from nodvard_deck.services import jobs as jobs_service

    owner = await _owner(client)
    r = await client.put("/api/v1/system/backups/config", json={"current_password": OWNER_PW, "enabled": True, "schedule": "0 3 * * *", "keep": 5}, headers=owner)
    assert r.status_code == 409
    await _set_key(client, owner)
    r = await client.put("/api/v1/system/backups/config", json={"current_password": OWNER_PW, "enabled": True, "schedule": "15 4 * * *", "keep": 5}, headers=owner)
    assert r.status_code == 200
    async with env["sessionmaker"]() as session:
        job = await jobs_service.get_job_by_key(session, ext_id=None, ext_job_key="system-backup")
    assert job.enabled and job.schedule == "15 4 * * *" and job.next_run_at is not None


@pytest.mark.asyncio
async def test_admin_cannot_pause_or_delete_the_backup_job_via_jobs_api(client, env):
    """Sonst koennte ein Admin (`jobs.write` ueber `*`) die automatischen Sicherungen still
    abschalten, waehrend die Karte weiter "an" zeigt."""
    from nodvard_deck.services import jobs as jobs_service

    owner = await _owner(client)
    await _set_key(client, owner)
    r = await client.put("/api/v1/system/backups/config", json={"current_password": OWNER_PW, "enabled": True, "schedule": "15 4 * * *", "keep": 5}, headers=owner)
    assert r.status_code == 200
    admin = await _user_with_role(client, env["sessionmaker"], "admin", "admin1")
    async with env["sessionmaker"]() as session:
        job = await jobs_service.get_job_by_key(session, ext_id=None, ext_job_key="system-backup")
    for headers in (admin, owner):
        r = await client.patch(f"/api/v1/jobs/{job.id}", json={"enabled": False}, headers=headers)
        assert r.status_code == 409, r.text
        r = await client.delete(f"/api/v1/jobs/{job.id}", headers=headers)
        assert r.status_code == 409, r.text
    async with env["sessionmaker"]() as session:
        job = await jobs_service.get_job_by_key(session, ext_id=None, ext_job_key="system-backup")
    assert job is not None and job.enabled and job.next_run_at is not None


# ---------------------------------------------------------------------------
# Laeufe, Liste, Pruefen, Rotation, Loeschen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_now_writes_encrypted_backup_with_sidecar(client, env):
    owner = await _owner(client)
    key = await _set_key(client, owner)
    await _run(client, owner)
    overview = await _overview(client, owner)
    assert overview["last_run"]["ok"] is True, overview["last_run"]
    [item] = overview["backups"]
    assert item["status"] == "ok" and item["key_current"] and item["mode"] == "schluessel"
    target = Path(overview["target"]["dir"])
    path = target / item["name"]
    assert oct(path.stat().st_mode & 0o777) == "0o600" and oct(target.stat().st_mode & 0o777) == "0o700"
    sidecar = json.loads((target / f"{item['name']}.json").read_text())
    assert sidecar["size"] == path.stat().st_size and len(sidecar["sha256"]) == 64
    assert overview["target"]["same_storage_as_data"] is True
    blob = path.read_bytes()
    assert b"MASTER" not in blob and b"Inhalt" not in blob
    manifest = _read(blob, key["recovery_key"], env["settings"].data_dir / "probe")
    assert manifest["header"]["key_id"] == key["key_id"]
    assert (env["settings"].data_dir / "probe" / "files" / "master.key").read_bytes() == b"MASTER"
    assert (env["settings"].data_dir / "probe" / "files" / "ext" / "beispiel" / "dokument.txt").read_text() == "Inhalt"
    # Keine Klartext-Reste im Temp-Ordner, kein .part im Ziel
    assert not any((target / ".tmp").iterdir()) if (target / ".tmp").exists() else True
    assert not [p for p in target.iterdir() if p.name.endswith(".part")]
    entries = (await client.get("/api/v1/audit?action=system.backup.created", headers=owner)).json()
    assert entries and entries[0]["detail"]["trigger"] == "manual"


@pytest.mark.asyncio
async def test_second_run_while_one_is_running_is_409(client, env):
    owner = await _owner(client)
    await _set_key(client, owner)
    token = await backups_service._try_acquire("test")
    try:
        assert (await client.post("/api/v1/system/backups/run", headers=owner)).status_code == 409
        r = await client.post("/api/v1/system/backups/download", json={"current_password": OWNER_PW, "mode": "schluessel"}, headers=owner)
        assert r.status_code == 409
        assert (await _overview(client, owner))["running"]["kind"] == "test"
    finally:
        backups_service._release(token)


@pytest.mark.asyncio
async def test_scheduled_run_waits_for_a_running_download(client, env):
    """Faellt der Zeitplan in einen Download-Bau, wird nachher gesichert statt gar nicht."""
    import asyncio

    owner = await _owner(client)
    await _set_key(client, owner)
    token = await backups_service._try_acquire("download")
    scheduled = asyncio.ensure_future(backups_service.run_backup(trigger="schedule"))
    await asyncio.sleep(0.05)
    assert not scheduled.done()
    # waehrend jemand wartet, bekommt "Jetzt sichern" sofort 409 (kein Haengen)
    assert (await client.post("/api/v1/system/backups/run", headers=owner)).status_code == 409
    backups_service._release(token)
    result = await asyncio.wait_for(scheduled, 30)
    assert fmt.is_backup_name(result["name"])
    assert (await _overview(client, owner))["last_run"]["ok"] is True


@pytest.mark.asyncio
async def test_scheduled_run_that_cannot_get_the_lock_is_reported(client, env, monkeypatch):
    owner = await _owner(client)
    await _set_key(client, owner)
    monkeypatch.setattr(backups_service, "SCHEDULED_WAIT_S", 0.05)
    token = await backups_service._try_acquire("download")
    try:
        with pytest.raises(backups_service.BackupBusy):
            await backups_service.run_backup(trigger="schedule")
    finally:
        backups_service._release(token)
    overview = await _overview(client, owner)
    assert overview["last_run"]["ok"] is False and "läuft gerade schon" in overview["last_run"]["error"]
    notes = (await client.get("/api/v1/notifications", headers=owner)).json()
    assert any(n["title"] == "Sicherung fehlgeschlagen" for n in notes)


@pytest.mark.asyncio
async def test_rotation_keeps_n_and_leaves_foreign_files(client, env):
    owner = await _owner(client)
    await _set_key(client, owner)
    target = store.default_dir(env["settings"].data_dir)
    target.mkdir(parents=True, exist_ok=True)
    foreign = target / "nodvard-deck-sicherung-20000101-000000.ndbak"
    foreign.write_bytes(b"nicht von uns")
    (target / "urlaubsfotos.zip").write_bytes(b"x")
    r = await client.put("/api/v1/system/backups/config", json={"current_password": OWNER_PW, "enabled": False, "schedule": "0 3 * * *", "keep": 2}, headers=owner)
    assert r.status_code == 200
    for _ in range(4):
        await _run(client, owner)
    names = sorted(p.name for p in target.iterdir() if fmt.is_backup_name(p.name))
    assert names == [foreign.name, "nodvard-deck-sicherung-20261001-030200.ndbak", "nodvard-deck-sicherung-20261001-030300.ndbak"]
    assert (target / "urlaubsfotos.zip").exists()
    assert not (target / "nodvard-deck-sicherung-20261001-030000.ndbak.json").exists()
    statuses = {i["name"]: i["status"] for i in (await _overview(client, owner))["backups"]}
    assert statuses[foreign.name] == "beschaedigt"


@pytest.mark.asyncio
async def test_verify_detects_bit_rot(client, env):
    owner = await _owner(client)
    await _set_key(client, owner)
    await _run(client, owner)
    [item] = (await _overview(client, owner))["backups"]
    url = f"/api/v1/system/backups/{item['name']}/verify"
    assert (await client.post(url, headers=owner)).json()["ok"] is True
    path = Path((await _overview(client, owner))["target"]["dir"]) / item["name"]
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    result = (await client.post(url, headers=owner)).json()
    assert result["ok"] is False
    [item] = (await _overview(client, owner))["backups"]
    assert item["status"] == "beschaedigt" and item["check_ok"] is False


@pytest.mark.asyncio
async def test_delete_needs_password_and_only_touches_our_files(client, env):
    owner = await _owner(client)
    await _set_key(client, owner)
    await _run(client, owner)
    [item] = (await _overview(client, owner))["backups"]
    url = f"/api/v1/system/backups/{item['name']}"
    assert (await client.request("DELETE", url, json={}, headers=owner)).status_code == 403
    assert (await client.request("DELETE", url, json={"current_password": "falsch"}, headers=owner)).status_code == 400
    for bad in ["..%2Fmaster.key", "urlaubsfotos.zip", "nodvard-deck-sicherung-20261001-030000.ndbak.json"]:
        r = await client.request("DELETE", f"/api/v1/system/backups/{bad}", json={"current_password": OWNER_PW}, headers=owner)
        # 404 (unbekannt) oder 405 (ein kodierter Schraegstrich trifft gar keine Route): beides loescht nichts.
        assert r.status_code in (404, 405), bad
    assert env["settings"].master_key_path.exists()
    assert len((await _overview(client, owner))["backups"]) == 1
    assert (await client.request("DELETE", url, json={"current_password": OWNER_PW}, headers=owner)).status_code == 204
    assert (await _overview(client, owner))["backups"] == []


@pytest.mark.asyncio
async def test_failed_backup_is_reported_and_notified(client, env):
    owner = await _owner(client)
    await _set_key(client, owner)
    nas = env["external"] / "nas"
    r = await client.put("/api/v1/system/backups/config", json={"current_password": OWNER_PW, "enabled": False, "schedule": "0 3 * * *", "keep": 2, "dir": str(nas)}, headers=owner)
    assert r.status_code == 200
    nas.rmdir()
    os.symlink("/tmp", nas)  # Ziel wurde heimlich gegen einen Link getauscht
    await _run(client, owner)
    overview = await _overview(client, owner)
    assert overview["last_run"]["ok"] is False and "Verknüpfung" in overview["last_run"]["error"]
    notes = (await client.get("/api/v1/notifications", headers=owner)).json()
    assert any(n["title"] == "Sicherung fehlgeschlagen" for n in notes)
    failed = (await client.get("/api/v1/audit?action=system.backup.failed", headers=owner)).json()
    assert failed and failed[0]["outcome"] == "failure"


# ---------------------------------------------------------------------------
# Download und Tickets
# ---------------------------------------------------------------------------


async def _prepare(client, headers, **body) -> dict:
    r = await client.post("/api/v1/system/backups/download", json={"current_password": OWNER_PW, **body}, headers=headers)
    assert r.status_code == 202, r.text
    await backups_service.wait_for_tasks()
    status = await client.get(f"/api/v1/system/backups/download-jobs/{r.json()['job_id']}", headers=headers)
    assert status.status_code == 200
    assert status.json()["status"] == "ready", status.json()
    assert r.headers["cache-control"] == "no-store"
    return r.json()


@pytest.mark.asyncio
async def test_download_with_backup_key_is_one_time(client, env):
    owner = await _owner(client)
    key = await _set_key(client, owner)
    ticket = await _prepare(client, owner, mode="schluessel")
    first = await client.get(ticket["url"])  # ohne Anmelde-Header: das Ticket genuegt
    assert first.status_code == 200
    assert "nodvard-deck-sicherung-" in first.headers["content-disposition"]
    manifest = _read(first.content, BACKUP_PW, env["settings"].data_dir / "probe")
    assert manifest["header"]["mode"] == "schluessel"
    assert (await client.get(ticket["url"])).status_code == 404
    downloads = store.default_dir(env["settings"].data_dir) / ".downloads"
    assert list(downloads.iterdir()) == []  # nach dem Ausliefern geloescht
    assert key["key_id"] == manifest["header"]["key_id"]


@pytest.mark.asyncio
async def test_download_with_one_time_password(client, env):
    owner = await _owner(client)
    short = await client.post("/api/v1/system/backups/download",
                              json={"current_password": OWNER_PW, "mode": "passwort", "password": "kurz"}, headers=owner)
    assert short.status_code == 422
    ticket = await _prepare(client, owner, mode="passwort", password=ONE_TIME_PW)
    blob = (await client.get(ticket["url"])).content
    manifest = _read(blob, ONE_TIME_PW, env["settings"].data_dir / "probe")
    assert manifest["header"]["mode"] == "passwort" and "key_id" not in manifest["header"]


@pytest.mark.asyncio
async def test_download_with_key_mode_needs_a_key(client, env):
    owner = await _owner(client)
    r = await client.post("/api/v1/system/backups/download", json={"current_password": OWNER_PW, "mode": "schluessel"}, headers=owner)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_expired_ticket_is_gone_and_its_file_deleted(client, env):
    owner = await _owner(client)
    ticket = await _prepare(client, owner, mode="passwort", password=ONE_TIME_PW)
    entry = get_ticket_registry().get(ticket["ticket"])
    entry.ready_at -= 301
    path = entry.path
    assert path.exists()
    assert (await client.get(ticket["url"])).status_code == 404
    assert not path.exists()


@pytest.mark.asyncio
async def test_ticket_is_bound_to_its_user(client, env, tmp_path):
    owner = await _owner(client)
    foreign = get_ticket_registry().create(user_id="jemand-anderes", path=tmp_path / "x", filename="x", delete_after=False, ready=True)
    r = await client.get(f"/api/v1/system/backups/download/{foreign.ticket}/status", headers=owner)
    assert r.status_code == 404
    # Ein Ticket, dessen Nutzer kein Owner (mehr) ist, liefert nichts aus.
    (tmp_path / "x").write_bytes(b"geheim")
    assert (await client.get(f"/api/v1/system/backups/download/{foreign.ticket}")).status_code == 404
    assert (await client.get("/api/v1/system/backups/download/erfunden")).status_code == 404


@pytest.mark.asyncio
async def test_stored_backup_ticket_keeps_the_file(client, env):
    owner = await _owner(client)
    await _set_key(client, owner)
    await _run(client, owner)
    [item] = (await _overview(client, owner))["backups"]
    url = f"/api/v1/system/backups/{item['name']}/ticket"
    assert (await client.post(url, json={}, headers=owner)).status_code == 403
    r = await client.post(url, json={"current_password": OWNER_PW}, headers=owner)
    assert r.status_code == 200
    got = await client.get(r.json()["url"])
    path = Path((await _overview(client, owner))["target"]["dir"]) / item["name"]
    assert got.content == path.read_bytes() and path.exists()
    assert (await client.get(r.json()["url"])).status_code == 404


@pytest.mark.asyncio
async def test_no_secret_in_logs_audit_or_job_output(client, env, caplog):
    caplog.set_level(logging.DEBUG)
    owner = await _owner(client)
    key = await _set_key(client, owner)
    await _prepare(client, owner, mode="passwort", password=ONE_TIME_PW)
    await _run(client, owner)
    secrets = [BACKUP_PW, ONE_TIME_PW, key["recovery_key"], OWNER_PW]
    for secret in secrets:
        assert secret not in caplog.text
    audit = json.dumps((await client.get("/api/v1/audit?limit=500", headers=owner)).json())
    assert "system.backup.key_set" in audit and "system.backup.download_prepared" in audit
    for secret in secrets:
        assert secret not in audit
    runs = env["settings"].data_dir / "runs"
    for log in runs.glob("*.log"):
        for secret in secrets:
            assert secret not in log.read_text()


@pytest.mark.asyncio
async def test_memory_database_is_refused(client, test_settings, monkeypatch):
    from nodvard_deck import config

    monkeypatch.setattr(config, "_settings", test_settings)
    backups_service.reset_for_tests()
    owner = await _owner(client)
    r = await client.post("/api/v1/system/backups/download",
                          json={"current_password": OWNER_PW, "mode": "passwort", "password": ONE_TIME_PW}, headers=owner)
    assert r.status_code == 409 and "SQLite" in r.json()["detail"]
    info = (await client.get("/api/v1/system/info", headers=owner)).json()
    assert info["database"] == "sqlite" and info["version"]


# ---------------------------------------------------------------------------
# Vorher-Kopien (2b): GET /system/info zeigt die letzten Kopien vor einer Migration
# ---------------------------------------------------------------------------


def _fake_pre_update_copies(data_dir: Path, count: int) -> list[str]:
    folder = data_dir / "backups" / "vor-update"
    folder.mkdir(parents=True)
    names = []
    for i in range(count):
        name = f"2026100{i + 1}T030000Z_0.{i + 5}.0_0.{i + 6}.0.db"
        (folder / name).write_bytes(b"x" * (1000 + i))
        (folder / (name + ".json")).write_text(json.dumps({
            "created_at": f"2026-10-0{i + 1}T03:00:00Z", "from_version": f"0.{i + 5}.0", "to_version": f"0.{i + 6}.0",
            "from_heads": ["a1b2c3d4e5f6"], "to_heads": ["b1b2c3d4e5f6"],
        }))
        names.append(name)
    return names


@pytest.mark.asyncio
async def test_info_without_any_copy_has_an_empty_list(client, env):
    await _owner(client)
    admin = await _user_with_role(client, env["sessionmaker"], "admin", "admin1")
    info = (await client.get("/api/v1/system/info", headers=admin)).json()
    assert info["pre_update_copies"] == []
    assert {"version", "build", "image", "timezone", "data_dir", "data_free_bytes", "database", "updater_available"} <= set(info), "das Bisherige bleibt"


@pytest.mark.asyncio
async def test_info_lists_the_latest_pre_update_copies_newest_first_without_paths(client, env):
    owner = await _owner(client)
    names = _fake_pre_update_copies(env["settings"].data_dir, 4)
    info = (await client.get("/api/v1/system/info", headers=owner)).json()
    copies = info["pre_update_copies"]
    assert [c["name"] for c in copies] == [names[3], names[2], names[1]], "die drei neuesten, neueste zuerst"
    first = copies[0]
    assert set(first) == {"name", "created_at", "from_version", "to_version", "size"}
    assert first["from_version"] == "0.8.0" and first["to_version"] == "0.9.0" and first["size"] == 1003
    assert first["created_at"] == "2026-10-04T03:00:00Z"
    assert str(env["settings"].data_dir) not in json.dumps(copies), "keine Pfade"


@pytest.mark.asyncio
async def test_info_with_pre_update_copies_needs_system_read(client, env):
    await _owner(client)
    viewer = await _user_with_role(client, env["sessionmaker"], "viewer", "viewer1")
    admin = await _user_with_role(client, env["sessionmaker"], "admin", "admin1")
    _fake_pre_update_copies(env["settings"].data_dir, 1)
    assert (await client.get("/api/v1/system/info", headers=viewer)).status_code == 403
    assert (await client.get("/api/v1/system/info", headers=admin)).json()["pre_update_copies"]


@pytest.mark.asyncio
async def test_info_ignores_foreign_files_in_the_copy_folder(client, env):
    owner = await _owner(client)
    folder = env["settings"].data_dir / "backups" / "vor-update"
    folder.mkdir(parents=True)
    (folder / "geheim.txt").write_text("x")
    (folder / "evil.db").write_text("x")
    (folder / "20261001T030000Z_a_b.db").symlink_to(env["settings"].data_dir / "geheim.txt")
    assert (await client.get("/api/v1/system/info", headers=owner)).json()["pre_update_copies"] == []
