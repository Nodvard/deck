"""POST/PUT/GET/DELETE /api/v1/system/restore/... und /system/restart (Owner + Passwort)."""

from __future__ import annotations

import json
import logging
import os
import threading

import pytest
from nodvard_deck.core import restart as restart_service
from nodvard_deck.core.backup import crypto, restore, store
from nodvard_deck.services import backups as backups_service
from nodvard_deck.services import restore as restore_service
from restore_api_fixtures import (  # noqa: F401 - Fixtures
    OCTET,
    OWNER_PW,
    Tripwire,
    backup_bytes,
    chunks,
    enc,
    make_owner,
    make_role_user,
    rapi,
)
from restore_helpers import PASSWORD

BASE = "/api/v1/system"


def up_headers(owner: dict, password: str = OWNER_PW, **extra) -> dict:
    return {**owner, **OCTET, "X-Confirm-Password": enc(password), **extra}


async def upload(client, owner, data: bytes, **kw):
    return await client.put(f"{BASE}/restore/upload", content=data, headers=up_headers(owner, **kw))


async def uploaded(client, owner, tmp_path, **kw) -> str:
    r = await upload(client, owner, backup_bytes(tmp_path, **kw))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def inspected(client, owner, tmp_path, secret=PASSWORD, **kw) -> tuple[str, dict]:
    rid = await uploaded(client, owner, tmp_path, **kw)
    r = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": secret}, headers=owner)
    assert r.status_code == 200, r.text
    return rid, r.json()


# ---------------------------------------------------------------------------
# Rechte
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_endpoints_need_a_login(client, rapi):
    for method, url in [("GET", "/restore/status"), ("PUT", "/restore/upload"), ("POST", f"/restore/{'a' * 32}/inspect"),
                        ("POST", f"/restore/{'a' * 32}/schedule"), ("DELETE", "/restore/pending"), ("DELETE", "/restore/replaced"),
                        ("POST", "/restart")]:
        assert (await client.request(method, BASE + url)).status_code == 401, (method, url)


@pytest.mark.asyncio
async def test_admin_without_owner_gets_403_everywhere_and_viewer_cannot_even_look(client, rapi, db_session):
    await make_owner(client)
    admin = await make_role_user(client, db_session, "admin", "admin1")
    viewer = await make_role_user(client, db_session, "viewer", "viewer1")
    rid = "a" * 32
    pw = {"current_password": "whatever-1234"}
    for method, url, kw in [
        ("PUT", "/restore/upload", {"content": b"x", "headers": {**OCTET, "X-Confirm-Password": enc("whatever-1234")}}),
        ("POST", f"/restore/{rid}/inspect", {"json": {"password": PASSWORD}}),
        ("POST", f"/restore/{rid}/schedule", {"json": pw}),
        ("DELETE", "/restore/pending", {}),
        ("DELETE", "/restore/replaced", {"json": pw}),
        ("POST", "/restart", {"json": pw}),
    ]:
        kw.setdefault("headers", {})
        kw["headers"] = {**kw["headers"], **admin}
        r = await client.request(method, BASE + url, **kw)
        assert r.status_code == 403 and "Owner" in r.json()["detail"], (method, url, r.text)
    # Ansehen darf der Admin (system.read), aber ohne den Zwischenstand; ein Betrachter nicht.
    seen = await client.get(f"{BASE}/restore/status", headers=admin)
    assert seen.status_code == 200 and seen.json()["staged"] is None
    assert (await client.get(f"{BASE}/restore/status", headers=viewer)).status_code == 403


@pytest.mark.asyncio
async def test_password_is_checked_before_the_body_is_read(client, rapi):
    owner = await make_owner(client)
    trip = Tripwire()
    for headers in ({**owner, **OCTET}, up_headers(owner, "falsches-passwort"), up_headers(owner, "")):
        trip.consumed = False
        r = await client.put(f"{BASE}/restore/upload", content=trip, headers=headers)
        assert r.status_code in (400, 403), r.text
        assert trip.consumed is False, "der Body darf nicht gelesen werden, bevor das Passwort stimmt"
    assert not rapi.layout.restore_dir.exists() or not list(restore.list_ids(rapi.layout))
    # Gegenprobe: mit richtigem Passwort wird der Strom sehr wohl gelesen (hier Muell -> 422).
    trip.consumed = False
    r = await client.put(f"{BASE}/restore/upload", content=trip, headers=up_headers(owner))
    assert trip.consumed is True and r.status_code == 422


@pytest.mark.asyncio
async def test_wrong_password_is_throttled_like_everywhere(client, rapi):
    owner = await make_owner(client)
    codes = [(await client.put(f"{BASE}/restore/upload", content=b"x", headers=up_headers(owner, "falsch"))).status_code for _ in range(12)]
    assert codes[0] == 400 and codes[-1] == 429
    # Auch das richtige Passwort kommt dann nicht durch (gleicher Zaehler wie die anderen Passwortabfragen).
    assert (await upload(client, owner, b"x")).status_code == 429


@pytest.mark.asyncio
async def test_unicode_and_percent_in_the_password_survive_the_header(client, rapi, tmp_path):
    owner = await make_owner(client)
    tricky = "Grüße-aus-Köln-100%-🙂 ok"
    # Passwort des Owners auf eines mit Umlauten, Prozentzeichen und Emoji aendern.
    r = await client.post("/api/v1/me/password", json={"current_password": OWNER_PW, "new_password": tricky}, headers=owner)
    assert r.status_code in (200, 204), r.text
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": tricky})
    owner = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.put(f"{BASE}/restore/upload", content=backup_bytes(tmp_path), headers=up_headers(owner, tricky))
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Hochladen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_streams_to_disk_and_reports_the_header(client, rapi, tmp_path):
    owner = await make_owner(client)
    data = backup_bytes(tmp_path)
    r = await client.put(f"{BASE}/restore/upload", content=chunks(data, 4096), headers=up_headers(owner))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "uploaded" and body["size"] == len(data) and body["header"]["mode"] == "passwort"
    assert body["expires_in"] > 3000 and r.headers["cache-control"] == "no-store"
    stored = rapi.layout.restore_dir / body["id"] / restore.UPLOAD_NAME
    assert stored.read_bytes() == data and oct(stored.stat().st_mode & 0o777) == "0o600"
    assert oct(rapi.layout.restore_dir.stat().st_mode & 0o777) == "0o700"
    status = (await client.get(f"{BASE}/restore/status", headers=owner)).json()
    assert status["staged"]["id"] == body["id"] and status["staged"]["state"] == "uploaded" and status["pending"] is None
    assert status["limits"]["max_upload_bytes"] == rapi.settings.restore_max_upload_bytes


@pytest.mark.asyncio
async def test_upload_never_holds_the_whole_body_in_memory(client, rapi, tmp_path):
    import tracemalloc

    owner = await make_owner(client)
    big = backup_bytes(tmp_path, files={"ext/beispiel/gross.bin": os.urandom(12 * 1024 * 1024)})
    assert len(big) > 11 * 1024 * 1024

    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    tracemalloc.reset_peak()
    r = await client.put(f"{BASE}/restore/upload", content=chunks(big, 256 * 1024), headers=up_headers(owner))
    peak = tracemalloc.get_traced_memory()[1] - baseline
    tracemalloc.stop()
    assert r.status_code == 201, r.text
    # Der Test selbst haelt `big` (schon vor `baseline`). Die Anwendung puffert hoechstens ein paar MiB.
    assert peak < 6 * 1024 * 1024, f"Spitze {peak / 1024 / 1024:.1f} MiB"


@pytest.mark.asyncio
async def test_content_length_over_the_limit_is_413_without_reading(client, rapi):
    owner = await make_owner(client)
    rapi.settings.restore_max_upload_bytes = 1000
    trip = Tripwire()
    r = await client.put(f"{BASE}/restore/upload", content=trip, headers=up_headers(owner, **{"Content-Length": "5000"}))
    assert r.status_code == 413 and trip.consumed is False
    assert "zu groß" in r.json()["detail"]
    assert not list(restore.list_ids(rapi.layout))


@pytest.mark.asyncio
async def test_chunked_upload_over_the_limit_is_stopped_midway_and_leaves_nothing(client, rapi):
    owner = await make_owner(client)
    rapi.settings.restore_max_upload_bytes = 100_000

    async def endless():
        for _ in range(1000):
            yield b"a" * 10_000
        raise AssertionError("haette laengst abgebrochen werden muessen")

    r = await client.put(f"{BASE}/restore/upload", content=endless(), headers=up_headers(owner))
    assert r.status_code == 413
    assert not list(restore.list_ids(rapi.layout))


@pytest.mark.asyncio
async def test_wrong_content_type_empty_and_garbage_uploads(client, rapi):
    owner = await make_owner(client)
    pw = {"X-Confirm-Password": enc(OWNER_PW)}
    r = await client.put(f"{BASE}/restore/upload", content=b"x", headers={**owner, **pw, "Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 415
    r = await client.put(f"{BASE}/restore/upload", content=b"", headers=up_headers(owner))
    assert r.status_code in (400, 422)
    r = await upload(client, owner, b"das ist keine Sicherung von irgendwas")
    assert r.status_code == 422 and "keine Sicherung" in r.json()["detail"]
    assert not list(restore.list_ids(rapi.layout)), "Muell bleibt nicht liegen"


@pytest.mark.asyncio
async def test_not_enough_space_is_507_before_reading(client, rapi, monkeypatch):
    owner = await make_owner(client)
    monkeypatch.setattr(store, "free_bytes", lambda path: 10 * 1024 * 1024)
    trip = Tripwire()
    r = await client.put(f"{BASE}/restore/upload", content=trip, headers=up_headers(owner, **{"Content-Length": str(50 * 1024 * 1024)}))
    assert r.status_code == 507 and trip.consumed is False and "Platz" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_new_upload_replaces_the_old_staging(client, rapi, tmp_path):
    owner = await make_owner(client)
    first = await uploaded(client, owner, tmp_path, name="eins")
    second = await uploaded(client, owner, tmp_path, name="zwei")
    assert first != second and restore.list_ids(rapi.layout) == [second]


@pytest.mark.asyncio
async def test_second_upload_while_one_is_running_is_409(client, rapi, tmp_path):
    owner = await make_owner(client)
    gate = threading.Event()
    started = threading.Event()
    data = backup_bytes(tmp_path)

    import asyncio

    async def slow():
        yield data[:100]
        started.set()
        while not gate.is_set():
            await asyncio.sleep(0.01)
        yield data[100:]

    first = asyncio.ensure_future(client.put(f"{BASE}/restore/upload", content=slow(), headers=up_headers(owner)))
    while not started.is_set():
        await asyncio.sleep(0.01)
    second = await upload(client, owner, data)
    gate.set()
    assert second.status_code == 409 and "gerade" in second.json()["detail"]
    assert (await first).status_code == 201


@pytest.mark.asyncio
async def test_pending_restore_blocks_new_uploads(client, rapi, tmp_path):
    owner = await make_owner(client)
    rid, _ = await inspected(client, owner, tmp_path)
    ok = await client.post(f"{BASE}/restore/{rid}/schedule", json={"current_password": OWNER_PW}, headers=owner)
    assert ok.status_code == 200
    r = await upload(client, owner, backup_bytes(tmp_path, name="noch-eine"))
    assert r.status_code == 409 and "vorgemerkt" in r.json()["detail"]
    assert restore.pending_exists(rapi.layout) and (rapi.layout.restore_dir / rid / restore.STAGING_NAME).is_dir()


@pytest.mark.asyncio
async def test_expired_pending_restore_no_longer_blocks_uploads(client, rapi, tmp_path):
    import time

    owner = await make_owner(client)
    rid, _ = await inspected(client, owner, tmp_path)
    await client.post(f"{BASE}/restore/{rid}/schedule", json={"current_password": OWNER_PW}, headers=owner)
    pending = restore.read_json(rapi.layout.restore_dir / restore.PENDING_NAME)
    pending["scheduled_at"] = time.time() - restore.PENDING_TTL_S - 5
    restore.write_json_atomic(rapi.layout.restore_dir / restore.PENDING_NAME, pending)
    assert (await upload(client, owner, backup_bytes(tmp_path, name="neu"))).status_code == 201
    assert not restore.pending_exists(rapi.layout)


# ---------------------------------------------------------------------------
# Pruefen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_returns_the_summary_and_drops_the_encrypted_upload(client, rapi, tmp_path):
    owner = await make_owner(client)
    rid, view = await inspected(client, owner, tmp_path)
    s = view["summary"]
    assert view["state"] == "ready"
    assert (s["owner_name"], s["users"], s["hosts"], s["app_version"], s["instance_id"]) == ("anna", 2, 3, "0.5.0", "inst-1")
    assert s["created_at"] == "2026-10-01T03:00:00Z" and isinstance(s["warnings"], list)
    directory = rapi.layout.restore_dir / rid
    assert not (directory / restore.UPLOAD_NAME).exists() and (directory / restore.STAGING_NAME / "db" / "lattice.db").is_file()
    again = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)
    assert again.status_code == 200 and again.json()["summary"] == s, "ein zweites Pruefen aendert nichts"
    audit = (await client.get("/api/v1/audit?limit=100", headers=owner)).json()
    assert {"system.restore.uploaded", "system.restore.inspected"} <= {a["action"] for a in audit}
    assert PASSWORD not in json.dumps(audit)


@pytest.mark.asyncio
async def test_inspect_with_wrong_secret_keeps_the_upload_for_another_try(client, rapi, tmp_path):
    owner = await make_owner(client)
    rid = await uploaded(client, owner, tmp_path)
    bad = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": "falsches-passwort-123"}, headers=owner)
    assert bad.status_code == 400 and "passt nicht" in bad.json()["detail"]
    directory = rapi.layout.restore_dir / rid
    assert (directory / restore.UPLOAD_NAME).is_file() and not (directory / restore.STAGING_NAME).exists()
    ok = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)
    assert ok.status_code == 200
    audit = json.dumps((await client.get("/api/v1/audit?limit=100", headers=owner)).json())
    assert "falsches-passwort-123" not in audit and "system.restore.inspect_failed" in audit


@pytest.mark.asyncio
async def test_inspect_with_recovery_key(client, rapi, tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, "ARGON2_T", 1)
    monkeypatch.setattr(crypto, "ARGON2_M_KIB", 1024)
    owner = await make_owner(client)
    pw = "sicherungs-passwort-1"
    rid = await uploaded(client, owner, tmp_path, secret=pw, mode="schluessel")
    key = crypto.derive_key(pw, crypto.KdfParams(t=1, m_kib=1024, p=1, salt=b"s" * 16))
    r = await client.post(f"{BASE}/restore/{rid}/inspect", json={"recovery_key": key.identity.lower()}, headers=owner)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_inspect_needs_exactly_one_secret(client, rapi, tmp_path):
    owner = await make_owner(client)
    rid = await uploaded(client, owner, tmp_path)
    for body in ({}, {"password": "a", "recovery_key": "b"}, {"password": ""}):
        r = await client.post(f"{BASE}/restore/{rid}/inspect", json=body, headers=owner)
        assert r.status_code == 422, body
        assert PASSWORD not in json.dumps(r.json())


@pytest.mark.asyncio
async def test_damaged_or_unusable_backup_is_422_and_removes_everything(client, rapi, tmp_path):
    owner = await make_owner(client)
    data = bytearray(backup_bytes(tmp_path))
    data[len(data) // 2] ^= 0x01
    rid = (await upload(client, owner, bytes(data))).json()["id"]
    r = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)
    assert r.status_code == 422 and "beschädigt" in r.json()["detail"]
    assert not (rapi.layout.restore_dir / rid).exists()
    assert (await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)).status_code == 404


@pytest.mark.asyncio
async def test_unknown_and_malformed_ids_are_404(client, rapi):
    owner = await make_owner(client)
    for rid in ("0" * 32, "%2e%2e", "zzz", "A" * 32):
        r = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": "x"}, headers=owner)
        assert r.status_code == 404, rid
        r = await client.post(f"{BASE}/restore/{rid}/schedule", json={"current_password": OWNER_PW}, headers=owner)
        assert r.status_code == 404, rid


@pytest.mark.asyncio
async def test_only_one_decryption_at_a_time(client, rapi, tmp_path, monkeypatch):
    owner = await make_owner(client)
    rid = await uploaded(client, owner, tmp_path)
    entered = threading.Event()
    release = threading.Event()
    real = restore_service._inspect_sync  # noqa: SLF001

    def slow(*args, **kw):
        entered.set()
        assert release.wait(10)
        return real(*args, **kw)

    monkeypatch.setattr(restore_service, "_inspect_sync", slow)
    import asyncio

    first = asyncio.ensure_future(client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner))
    while not entered.is_set():
        await asyncio.sleep(0.01)
    second = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)
    third = await upload(client, owner, backup_bytes(tmp_path, name="drei"))
    cancel = await client.delete(f"{BASE}/restore/pending", headers=owner)
    run = await client.post(f"{BASE}/backups/run", headers=owner)
    release.set()
    assert second.status_code == 409 and "geprüft" in second.json()["detail"]
    assert third.status_code == 409 and cancel.status_code == 409
    assert (await first).status_code == 200
    # Die Sperre ist frei; die Sicherungskarte hat waehrenddessen keine "laufende Sicherung" gezeigt.
    assert backups_service.running() is None
    del run


@pytest.mark.asyncio
async def test_a_running_backup_blocks_inspect_and_is_not_confused_with_it(client, rapi, tmp_path):
    owner = await make_owner(client)
    rid = await uploaded(client, owner, tmp_path)
    token = await backups_service.acquire_exclusive("jetzt")
    r = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)
    assert r.status_code == 409
    backups_service.release_exclusive(token)
    assert (await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)).status_code == 200


@pytest.mark.asyncio
async def test_not_enough_space_when_unpacking_keeps_the_upload(client, rapi, tmp_path, monkeypatch):
    owner = await make_owner(client)
    rid = await uploaded(client, owner, tmp_path)
    monkeypatch.setattr(store, "free_bytes", lambda path: 100 * 1024 * 1024)
    r = await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)
    assert r.status_code == 507
    assert (rapi.layout.restore_dir / rid / restore.UPLOAD_NAME).is_file()


# ---------------------------------------------------------------------------
# Vormerken, Abbrechen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_needs_password_and_a_checked_backup(client, rapi, tmp_path):
    owner = await make_owner(client)
    rid = await uploaded(client, owner, tmp_path)
    url = f"{BASE}/restore/{rid}/schedule"
    assert (await client.post(url, json={}, headers=owner)).status_code == 403
    assert (await client.post(url, json={"current_password": "falsch"}, headers=owner)).status_code == 400
    early = await client.post(url, json={"current_password": OWNER_PW}, headers=owner)
    assert early.status_code == 409 and "noch nicht geprüft" in early.json()["detail"]
    await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)
    ok = await client.post(url, json={"current_password": OWNER_PW}, headers=owner)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["id"] == rid and body["sign_out_all"] is True and body["source"] == "owner" and body["expires_in"] > 3000
    pending = restore.read_pending(rapi.layout)
    assert pending["actor"]["label"] == "owner1" and pending["backup"]["owner_name"] == "anna"
    assert (await client.post(url, json={"current_password": OWNER_PW}, headers=owner)).status_code == 409
    status = (await client.get(f"{BASE}/restore/status", headers=owner)).json()
    assert status["pending"]["id"] == rid
    audit = json.dumps((await client.get("/api/v1/audit?limit=100", headers=owner)).json())
    assert "system.restore.scheduled" in audit and OWNER_PW not in audit


@pytest.mark.asyncio
async def test_schedule_can_keep_sessions(client, rapi, tmp_path):
    owner = await make_owner(client)
    rid, _ = await inspected(client, owner, tmp_path)
    r = await client.post(f"{BASE}/restore/{rid}/schedule", json={"current_password": OWNER_PW, "sign_out_all": False}, headers=owner)
    assert r.status_code == 200 and restore.read_pending(rapi.layout)["sign_out_all"] is False


@pytest.mark.asyncio
async def test_cancel_removes_pending_and_all_staging(client, rapi, tmp_path):
    owner = await make_owner(client)
    rid, _ = await inspected(client, owner, tmp_path)
    await client.post(f"{BASE}/restore/{rid}/schedule", json={"current_password": OWNER_PW}, headers=owner)
    assert (await client.delete(f"{BASE}/restore/pending", headers=owner)).status_code == 204
    assert not restore.pending_exists(rapi.layout) and restore.list_ids(rapi.layout) == []
    status = (await client.get(f"{BASE}/restore/status", headers=owner)).json()
    assert status["pending"] is None and status["staged"] is None
    assert (await client.delete(f"{BASE}/restore/pending", headers=owner)).status_code == 204


@pytest.mark.asyncio
async def test_status_shows_the_last_result_and_the_old_state(client, rapi):
    owner = await make_owner(client)
    restore.make_private_dir(rapi.layout.restore_dir)
    restore.write_result(rapi.layout, {"ok": False, "at": "2026-10-01T03:00:00Z", "message": "kaputt", "source": "owner",
                                       "actor": {"label": "owner1", "ip": "1.2.3.4"}, "backup": {}, "rolled_back": True})
    (rapi.layout.restore_dir / "replaced-20261001T030000").mkdir()
    (rapi.layout.restore_dir / "replaced-20261001T030000" / "master.key").write_bytes(b"x" * 10)
    status = (await client.get(f"{BASE}/restore/status", headers=owner)).json()
    assert status["result"] == {"ok": False, "at": "2026-10-01T03:00:00Z", "message": "kaputt", "source": "owner",
                                "backup": {}, "replaced": None, "actor": "owner1", "rolled_back": True}
    assert "1.2.3.4" not in json.dumps(status), "keine IP-Adressen in der Statusantwort"
    assert status["replaced"]["name"] == "replaced-20261001T030000" and status["replaced"]["size"] == 10


@pytest.mark.asyncio
async def test_delete_old_state_needs_password(client, rapi):
    owner = await make_owner(client)
    restore.make_private_dir(rapi.layout.restore_dir)
    (rapi.layout.restore_dir / "replaced-20261001T030000").mkdir()
    assert (await client.request("DELETE", f"{BASE}/restore/replaced", json={}, headers=owner)).status_code == 403
    assert (await client.request("DELETE", f"{BASE}/restore/replaced", json={"current_password": "falsch"}, headers=owner)).status_code == 400
    assert (rapi.layout.restore_dir / "replaced-20261001T030000").exists()
    ok = await client.request("DELETE", f"{BASE}/restore/replaced", json={"current_password": OWNER_PW}, headers=owner)
    assert ok.status_code == 204 and not (rapi.layout.restore_dir / "replaced-20261001T030000").exists()


@pytest.mark.asyncio
async def test_non_sqlite_database_is_409(client, rapi):
    owner = await make_owner(client)
    rapi.settings.database_url = "sqlite+aiosqlite:///:memory:"
    r = await client.get(f"{BASE}/restore/status", headers=owner)
    assert r.status_code == 409 and "SQLite" in r.json()["detail"]
    assert (await upload(client, owner, b"x")).status_code == 409


# ---------------------------------------------------------------------------
# Neustart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_needs_owner_and_password_and_exits_with_75(client, rapi, monkeypatch):
    owner = await make_owner(client)
    fired: list[int] = []
    real = restart_service.request_restart

    def fake(app, **kw):
        real(app, terminate=lambda: fired.append(1))

    monkeypatch.setattr(restart_service, "request_restart", fake)
    assert (await client.post(f"{BASE}/restart", json={}, headers=owner)).status_code == 403
    assert (await client.post(f"{BASE}/restart", json={"current_password": "falsch"}, headers=owner)).status_code == 400
    assert fired == []
    r = await client.post(f"{BASE}/restart", json={"current_password": OWNER_PW}, headers=owner)
    assert r.status_code == 202 and r.json() == {"restarting": True, "exit_code": 75}
    import asyncio

    await asyncio.sleep(restart_service.SIGNAL_DELAY_S + 0.3)
    assert fired == [1]
    audit = (await client.get("/api/v1/audit?action=system.restart.requested", headers=owner)).json()
    assert audit and audit[0]["actor_type"] == "user"


def test_exit_if_requested_ends_the_process_with_75(monkeypatch):
    from types import SimpleNamespace

    codes: list[int] = []
    monkeypatch.setattr(os, "_exit", lambda code: codes.append(code))
    app = SimpleNamespace(state=SimpleNamespace())
    restart_service.exit_if_requested(app)
    assert codes == []
    app.state.restart_requested = True
    restart_service.exit_if_requested(app)
    assert codes == [75] and restart_service.EXIT_CODE == 75


# ---------------------------------------------------------------------------
# Nichts Geheimes im Protokoll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_secret_ends_up_in_logs_or_audit(client, rapi, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    owner = await make_owner(client)
    bad = await client.post(f"{BASE}/restore/{'a' * 32}/inspect", json={"password": "geheimes-passwort-xyz"}, headers=owner)
    assert bad.status_code == 404
    rid = await uploaded(client, owner, tmp_path)
    await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": "geheimes-passwort-xyz"}, headers=owner)
    await client.post(f"{BASE}/restore/{rid}/inspect", json={"password": PASSWORD}, headers=owner)
    await client.post(f"{BASE}/restore/{rid}/schedule", json={"current_password": OWNER_PW}, headers=owner)
    await client.put(f"{BASE}/restore/upload", content=b"x", headers=up_headers(owner, "falsches-konto-passwort"))
    logs = "\n".join(r.getMessage() for r in caplog.records)
    audit = json.dumps((await client.get("/api/v1/audit?limit=500", headers=owner)).json())
    for secret in ("geheimes-passwort-xyz", PASSWORD, OWNER_PW, "falsches-konto-passwort", enc(OWNER_PW)):
        assert secret not in logs and secret not in audit, secret


# ---------------------------------------------------------------------------
# Ergebnis ins Audit-Protokoll (nach dem Start)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_result_writes_the_audit_entry_once(client, rapi, db_session):
    owner = await make_owner(client)
    restore.make_private_dir(rapi.layout.restore_dir)
    restore.write_result(rapi.layout, {
        "ok": True, "at": "2026-10-01T03:00:00Z", "id": "a" * 32, "source": "owner", "message": "ok",
        "actor": {"type": "user", "id": "u-alt", "label": "anna", "ip": "10.0.0.5"},
        "backup": {"instance_id": "inst-1"}, "replaced": "replaced-20261001T030000", "sign_out_all": True,
    })
    await restore_service.record_result(rapi.settings)
    await restore_service.record_result(rapi.settings)
    entries = (await client.get("/api/v1/audit?action=system.restore.applied", headers=owner)).json()
    assert len(entries) == 1
    e = entries[0]
    assert e["actor_id"] == "u-alt" and e["outcome"] == "success" and e["detail"]["replaced"] == "replaced-20261001T030000"
    assert restore.read_result(rapi.layout)["audit_logged"] is True
    restore.write_result(rapi.layout, {"ok": False, "id": None, "source": "cli", "message": "nicht gegangen", "actor": {}, "backup": {}})
    await restore_service.record_result(rapi.settings)
    failed = (await client.get("/api/v1/audit?action=system.restore.failed", headers=owner)).json()
    assert len(failed) == 1 and failed[0]["outcome"] == "failure" and failed[0]["actor_type"] == "system" and failed[0]["actor_id"] == "cli"
