"""Update-Helfer ueber die API: `GET /system/updates/helper`, `POST /system/updates/apply`, `POST /system/updates/rollback`.

Der Kanal ist ein echter Ordner (`Settings.updater_dir`), den die Tests so einrichten, wie es der Helfer tut. Was der
Helfer tut, spielen sie nach, indem sie `status.json` schreiben bzw. Anforderungen aus `requests/` nehmen."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pyotp
import pytest
import pytest_asyncio
from nodvard_deck.core import bootstate, updater_client, updates
from nodvard_deck.core.backup import premigrate
from nodvard_deck.models import AuditEntry, Notification
from nodvard_deck.services import update_helper
from nodvard_deck.services.actor_labels import ActorLabels
from sqlalchemy import select
from totp_helpers import setup_confirm_code
from updater_helpers import (
    OTHER_ID,
    REQUEST_ID,
    make_channel,
    result,
    status_doc,
    vectors,
    write_status,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Kanal nur unter POSIX (dir_fd, O_NOFOLLOW)")

OWNER_PW = "correct-horse-battery"
COPY = "20261001T030000Z_0.7.0_0.7.1.db"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(updater_client, "EXPECTED_UID", os.getuid())
    update_helper.reset_for_tests()
    yield
    update_helper.reset_for_tests()


@pytest.fixture
def channel(test_settings) -> Path:
    """Kanal wie vom Helfer eingerichtet, offizielles Image 0.7.0, neueste gefundene Version 0.7.1."""
    test_settings.image = updates.OFFICIAL_IMAGE
    test_settings.build = "0.7.0"
    updates.save_cache(test_settings.data_dir, {
        "channel": "stable", "latest": "0.7.1", "latest_digest": None, "checked_at": "2026-10-01T03:00:00+00:00",
        "attempted_at": "2026-10-01T03:00:00+00:00", "error": None,
    })
    root = make_channel(test_settings.updater_dir)
    write_status(root, status_doc())
    return root


def requests_in(root: Path) -> list[str]:
    return sorted(os.listdir(root / "requests"))


def take_requests(root: Path) -> list[dict]:
    """Was der Helfer tut: die Anforderungen abholen."""
    found = []
    for name in requests_in(root):
        path = root / "requests" / name
        found.append(json.loads(path.read_bytes()))
        os.unlink(path)
    return found


async def _owner(client_) -> dict:
    await client_.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": OWNER_PW, "setup_code": "TEST-CODE-2345"})
    login = await client_.post("/api/v1/auth/login", json={"username": "owner1", "password": OWNER_PW})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user_with_role(client_, db_session, role: str, username: str, password: str = "whatever-1234") -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password(password), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client_.post("/api/v1/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _audit(db_session, prefix: str = "system.update.") -> list[AuditEntry]:
    db_session.expire_all()
    rows = (await db_session.execute(select(AuditEntry).order_by(AuditEntry.ts))).scalars().all()
    return [row for row in rows if row.action.startswith(prefix)]


def apply_body(**changes) -> dict:
    return {"version": "0.7.1", "current_password": OWNER_PW, **changes}


# ---------------------------------------------------------------------------
# Ansehen
# ---------------------------------------------------------------------------


async def test_status_needs_system_read_and_shows_a_missing_helper(client, db_session, test_settings):
    assert (await client.get("/api/v1/system/updates/helper")).status_code == 401
    owner = await _owner(client)
    viewer = await _user_with_role(client, db_session, "viewer", "viewer1")
    assert (await client.get("/api/v1/system/updates/helper", headers=viewer)).status_code == 403
    body = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert body["present"] is False and body["reason"] == "missing" and body["ready"] is False
    assert body["target"] is None and body["last_result"] is None and body["pending"] is None
    assert (await client.get("/api/v1/system/info", headers=owner)).json()["updater_available"] is False
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["helper"] is False


async def test_status_of_a_ready_helper_and_one_source_for_info_and_updates(client, db_session, channel):
    owner = await _owner(client)
    until = int(time.time()) + 3600
    write_status(channel, status_doc(previous={"version": "0.6.9", "until": until}, results=[result(outcome="applied")]))
    admin = await _user_with_role(client, db_session, "admin", "admin1")
    body = (await client.get("/api/v1/system/updates/helper", headers=admin)).json()
    assert body["present"] is True and body["ready"] is True and body["state"] == "idle"
    assert body["target"] == {"current_version": "0.7.0", "floating_tag": "latest", "pinned": False}
    assert body["previous"] == {"version": "0.6.9", "until": until, "data_revert": False, "data_since": None}
    assert body["last_result"]["outcome"] == "applied" and body["last_result"]["from"] == "0.7.0"
    assert (await client.get("/api/v1/system/info", headers=owner)).json()["updater_available"] is True
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["helper"] is True
    write_status(channel, status_doc(heartbeat_at=int(time.time()) - 600))
    body = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert body["present"] is False and body["reason"] == "stale"
    assert (await client.get("/api/v1/system/info", headers=owner)).json()["updater_available"] is False
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["helper"] is False


async def test_the_check_answers_with_the_state_of_the_helper_too(client, channel, monkeypatch):
    """`POST /system/updates/check` sagt wie `GET /system/updates`, ob ein Helfer antwortet (frischer Herzschlag)."""
    import httpx
    from nodvard_deck.core import rate_limit

    def registry(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "t"})
        if request.url.path.endswith("/tags/list"):
            return httpx.Response(200, json={"tags": ["0.7.0", "0.7.1", "latest"]})
        return httpx.Response(200, content=b"{}")

    real = updates._make_client
    monkeypatch.setattr(updates, "_make_client", lambda transport=None: real(httpx.MockTransport(registry)))
    owner = await _owner(client)
    fresh = await client.post("/api/v1/system/updates/check", headers=owner)
    assert fresh.status_code == 200 and fresh.json()["source"] == "live"
    assert fresh.json()["helper"] is True
    rate_limit.reset_all()  # hoechstens eine Suche pro Minute
    write_status(channel, status_doc(heartbeat_at=int(time.time()) - 600))
    stale = await client.post("/api/v1/system/updates/check", headers=owner)
    assert stale.status_code == 200 and stale.json()["source"] == "live"
    assert stale.json()["helper"] is False
    assert (await client.get("/api/v1/system/updates", headers=owner)).json()["helper"] is False


# ---------------------------------------------------------------------------
# Update anfordern
# ---------------------------------------------------------------------------


async def test_apply_writes_one_request_and_answers_202(client, db_session, channel):
    owner = await _owner(client)
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["action"] == "update" and body["from"] == "0.7.0" and body["to"] == "0.7.1"
    written = take_requests(channel)
    assert written == [{"v": 1, "id": body["request_id"], "action": "update", "version": "0.7.1",
                        "created_at": written[0]["created_at"]}]
    entries = await _audit(db_session)
    assert [(e.action, e.outcome) for e in entries] == [("system.update.requested", "success")]
    assert entries[0].detail == {"request_id": body["request_id"], "from": "0.7.0", "to": "0.7.1"}
    view = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert view["pending"]["id"] == body["request_id"] and view["pending"]["to"] == "0.7.1"


async def test_an_admin_who_is_not_owner_gets_403_and_the_attempt_is_logged(client, db_session, channel):
    await _owner(client)
    admin = await _user_with_role(client, db_session, "admin", "admin1")
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(current_password="whatever-1234"), headers=admin)
    assert r.status_code == 403 and r.json()["code"] == "not_owner"
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": "whatever-1234"}, headers=admin)
    assert r.status_code == 403
    assert requests_in(channel) == []
    entries = await _audit(db_session)
    assert [(e.action, e.outcome, e.detail["code"]) for e in entries] == [
        ("system.update.request_refused", "denied", "not_owner"),
        ("system.update.rollback_refused", "denied", "not_owner"),
    ]


async def test_missing_or_wrong_password(client, db_session, channel):
    owner = await _owner(client)
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(current_password=""), headers=owner)
    assert r.status_code == 403 and r.json()["code"] == "password_missing"
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(current_password="falsch-falsch"), headers=owner)
    assert r.status_code == 400
    assert requests_in(channel) == []
    codes = [e.detail["code"] for e in await _audit(db_session)]
    assert codes == ["password_missing", "confirm_400"]
    assert [e.action for e in await _audit(db_session, "auth.password_check_failed")] == ["auth.password_check_failed"]


@pytest.mark.parametrize(("changes", "code"), [
    (None, "helper_missing"),
    ({"heartbeat_at": 1}, "helper_missing"),
    ({"ready": False, "reason": "target_unhealthy"}, "helper_not_ready"),
    ({"state": "busy", "ready": False, "reason": "busy", "target": None,
      "busy": {"id": REQUEST_ID, "action": "update", "step": "started", "since": 1}}, "helper_busy"),
    ({"target": {"current_version": "0.7.0", "floating_tag": None, "pinned": True}}, "pinned"),
], ids=["fehlt", "veraltet", "nicht-bereit", "beschaeftigt", "fest-eingetragen"])
async def test_apply_409(client, db_session, channel, changes, code):
    owner = await _owner(client)
    if changes is None:
        os.unlink(channel / "status.json")
    else:
        write_status(channel, status_doc(**changes))  # erst hier: der Herzschlag ist frisch, egal wann gesammelt wurde
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 409, r.text
    assert r.json()["code"] == code
    assert requests_in(channel) == []
    entries = await _audit(db_session)
    assert [(e.action, e.outcome, e.detail["code"]) for e in entries] == [("system.update.request_refused", "failure", code)]


async def test_apply_409_without_the_official_image(client, channel, test_settings):
    owner = await _owner(client)
    test_settings.image = None
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "not_official_image"


@pytest.mark.parametrize(("version", "code", "tag"), [
    ("0.7.1-rc1", "prerelease", "latest"),
    ("0.7.01", "bad_version", "latest"),
    ("0.7.1\n", "bad_version", "latest"),
    ("0.8.0", "not_latest", "latest"),
    ("0.7.1", "tag_mismatch", "0.6"),
])
async def test_apply_422(client, channel, version, code, tag):
    owner = await _owner(client)
    write_status(channel, status_doc(target={"current_version": "0.7.0", "floating_tag": tag, "pinned": False}))
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(version=version), headers=owner)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == code
    assert requests_in(channel) == []


async def test_apply_422_when_not_newer(client, channel, test_settings):
    owner = await _owner(client)
    test_settings.build = "0.7.1"
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 422 and r.json()["code"] == "not_newer"
    test_settings.build = "0.7.0"
    write_status(channel, status_doc(target={"current_version": "0.7.2", "floating_tag": "latest", "pinned": False}))
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 422 and r.json()["code"] == "not_newer", "der Helfer kennt die echte laufende Version"


async def test_an_open_request_gives_409_then_the_window_gives_429(client, db_session, channel):
    owner = await _owner(client)
    assert (await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)).status_code == 202
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "request_pending"
    take_requests(channel)
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) > 500
    assert requests_in(channel) == []
    codes = [(e.action, e.detail.get("code")) for e in await _audit(db_session)]
    assert codes == [("system.update.requested", None), ("system.update.request_refused", "request_pending"),
                     ("system.update.request_refused", "rate_limited")]


async def test_a_refused_request_frees_the_window(client, channel):
    owner = await _owner(client)
    first = (await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)).json()
    take_requests(channel)
    write_status(channel, status_doc(results=[result(first["request_id"], outcome="refused", code="tag_not_on_version")]))
    assert (await client.get("/api/v1/system/updates/helper", headers=owner)).status_code == 200
    assert (await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)).status_code == 202


async def test_two_simultaneous_requests_write_only_one(client, channel):
    owner = await _owner(client)
    answers = await asyncio.gather(*(
        client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner) for _ in range(4)
    ))
    assert sorted(a.status_code for a in answers) == [202, 409, 409, 409]
    assert len(requests_in(channel)) == 1


async def test_a_broken_channel_gives_409_and_writes_nothing(client, channel):
    owner = await _owner(client)
    os.chmod(channel / "requests", 0o777)
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "channel_unsafe"
    os.chmod(channel / "requests", 0o1777)
    assert requests_in(channel) == []


# ---------------------------------------------------------------------------
# Zwei-Faktor
# ---------------------------------------------------------------------------


async def _enable_totp(client_, headers) -> str:
    setup = (await client_.post("/api/v1/me/totp/setup", json={"current_password": OWNER_PW}, headers=headers)).json()
    code = setup_confirm_code(setup["secret"])
    assert (await client_.post("/api/v1/me/totp/confirm", json={"code": code}, headers=headers)).status_code == 200
    return setup["secret"]


async def test_with_two_factor_the_code_is_needed_once(client, db_session, channel):
    owner = await _owner(client)
    secret = await _enable_totp(client, owner)
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 403 and "Zwei-Faktor" in r.json()["detail"]
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(totp_code="000000"), headers=owner)
    assert r.status_code == 400 and r.json()["code"] == "totp_wrong"
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(totp_code="ABCD-EFGH-12"), headers=owner)
    assert r.status_code == 400, "Wiederherstellungs-Codes gelten hier nicht"
    assert requests_in(channel) == []
    code = pyotp.TOTP(secret).now()
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(totp_code=code), headers=owner)
    assert r.status_code == 202, r.text
    take_requests(channel)
    update_helper.reset_for_tests()  # Fenster frei: nur die Wiederverwendung des Codes soll scheitern
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(totp_code=code), headers=owner)
    assert r.status_code == 400 and "schon benutzt" in r.json()["detail"] and r.json()["code"] == "totp_used"
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW, "totp_code": code},
                          headers=owner)
    assert r.status_code == 400, "auch nicht fuer den Rueckweg"
    assert requests_in(channel) == []
    failed = await _audit(db_session, "auth.totp_check_failed")
    assert len(failed) == 4 and failed[-1].detail.get("replayed") is True
    assert all("000000" not in json.dumps(e.detail) and code not in json.dumps(e.detail) for e in failed)


async def test_a_missing_code_has_its_own_error_code_like_a_missing_password(client, db_session, channel):
    owner = await _owner(client)
    await _enable_totp(client, owner)
    for path, body in (("apply", apply_body()), ("rollback", {"current_password": OWNER_PW})):
        r = await client.post(f"/api/v1/system/updates/{path}", json=body, headers=owner)
        assert r.status_code == 403, r.text
        assert r.json()["code"] == "totp_missing" and "Zwei-Faktor-Code" in r.json()["detail"]
    # Die Reihenfolge bleibt: erst das Passwort (falsch: 400, ohne zu verraten, dass ein Code noetig ist).
    wrong = await client.post("/api/v1/system/updates/apply", json=apply_body(current_password="falsch-falsch"),
                              headers=owner)
    assert wrong.status_code == 400 and "code" not in wrong.json()
    assert requests_in(channel) == []
    entries = await _audit(db_session)
    assert [(e.action, e.outcome, e.detail["code"]) for e in entries] == [
        ("system.update.request_refused", "denied", "totp_missing"),
        ("system.update.rollback_refused", "denied", "totp_missing"),
        ("system.update.request_refused", "denied", "confirm_400"),
    ]
    assert not await _audit(db_session, "auth.totp_check_failed"), "ein fehlender Code zaehlt nicht als Fehlversuch"


async def test_a_code_used_for_signing_in_is_not_accepted_again(client, channel):
    owner = await _owner(client)
    secret = await _enable_totp(client, owner)
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": OWNER_PW})
    code = pyotp.TOTP(secret).now()
    mfa = await client.post("/api/v1/auth/mfa", json={"mfa_token": login.json()["mfa_token"], "code": code})
    assert mfa.status_code == 200
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(totp_code=code), headers=owner)
    assert r.status_code == 400 and requests_in(channel) == []


async def test_guessing_the_code_is_throttled(client, channel):
    owner = await _owner(client)
    await _enable_totp(client, owner)
    statuses = [
        (await client.post("/api/v1/system/updates/apply", json=apply_body(totp_code=f"{i:06d}"), headers=owner)).status_code
        for i in range(12)
    ]
    assert statuses[0] == 400 and statuses[-1] == 429
    assert requests_in(channel) == []


async def test_the_lock_by_the_account_limit_names_that_limit(client, db_session, channel):
    from nodvard_deck.core import login_limit

    owner = await _owner(client)
    secret = await _enable_totp(client, owner)
    for i in range(login_limit.MAX_MFA_FAILURES_PER_ACCOUNT):
        await client.post("/api/v1/system/updates/apply", json=apply_body(totp_code=f"{i:06d}"), headers=owner)
    (locked,) = await _audit(db_session, "auth.password_check_locked")
    assert locked.detail["scopes"] == ["account"]
    assert locked.detail["window_seconds"] == login_limit.ACCOUNT_WINDOW_SECONDS, "das Fenster dieser Grenze, nicht 5 Minuten"
    assert locked.detail["account_window_seconds"] == login_limit.ACCOUNT_WINDOW_SECONDS
    assert locked.detail["day_window_seconds"] == login_limit.ACCOUNT_DAY_SECONDS
    assert "Zwei-Faktor-Codes für dieses Konto" in locked.reason
    assert f"{login_limit.MAX_FAILURES_PER_USER} Fehlversuche in" not in locked.reason, "nicht die Grenze der Passwörter"
    # Die Meldung dazu verspricht keinen Ausweg, den es bei der Bestaetigung nicht gibt: Wiederherstellungs-Codes
    # helfen nur bei der Anmeldung. Gesperrt sind nur Bestaetigungen mit Code, das Passwort laesst sich gleich aendern.
    from nodvard_deck.models import Notification

    db_session.expire_all()
    (note,) = (await db_session.execute(select(Notification))).scalars().all()
    assert note.title == "Zwei-Faktor-Code wird durchprobiert"
    assert "ändere gleich dein Passwort, das geht auch während der Sperre" in note.body
    assert "Anmelden kannst du dich in der Zeit mit einem Wiederherstellungs-Code" in note.body
    assert "Bestätigungen mit Zwei-Faktor-Code" in note.body and "Sperre vorbei" in note.body
    # Und so ist es auch: der richtige Code hilft bei der Bestaetigung nicht, das Passwort aendern geht.
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(totp_code=pyotp.TOTP(secret).now()), headers=owner)
    assert r.status_code == 429
    r = await client.post("/api/v1/me/password", json={"current_password": OWNER_PW, "new_password": "ein-neues-langes-passwort"},
                          headers=owner)
    assert r.status_code == 204, r.text


# ---------------------------------------------------------------------------
# Rueckweg
# ---------------------------------------------------------------------------


def _ready_for_rollback(channel: Path, test_settings, *, migrated: bool, copy_exists: bool = True,
                        from_version: str = "0.7.0") -> None:
    test_settings.build = "0.7.1"
    write_status(channel, status_doc(target={"current_version": "0.7.1", "floating_tag": "latest", "pinned": False},
                                     previous={"version": "0.7.0", "until": int(time.time()) + 3600}))
    if migrated:
        bootstate.write_state(test_settings.data_dir, {
            "app_version": "0.7.1", "started_ok": True,
            "last_migration": {"at": "2026-10-01T03:00:00Z", "from_version": from_version, "to_version": "0.7.1",
                               "from_heads": ["a"], "to_heads": ["b"], "copy": COPY, "state": "ok"},
        })
        if copy_exists:
            copies = premigrate.copies_dir(test_settings.data_dir)
            copies.mkdir(parents=True, exist_ok=True)
            (copies / COPY).write_bytes(b"kopie")


async def test_rollback_409_without_previous(client, channel):
    owner = await _owner(client)
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW}, headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "no_previous"
    write_status(channel, status_doc(previous={"version": "0.6.9", "until": int(time.time()) - 1}))
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW}, headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "no_previous"


async def test_rollback_without_migration_needs_no_data_revert(client, db_session, channel, test_settings):
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=False)
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW}, headers=owner)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["action"] == "rollback" and body["from"] == "0.7.1" and body["to"] == "0.7.0"
    assert body["data_revert"] is False
    assert bootstate.read_rollback(test_settings.data_dir) is None
    assert [w["action"] for w in take_requests(channel)] == ["rollback"]
    entries = await _audit(db_session)
    assert entries[-1].action == "system.update.rollback_requested"
    assert entries[-1].detail["data_revert"] is False and entries[-1].detail["copy"] is None


async def test_rollback_with_data_needs_consent_and_writes_the_marker_first(client, db_session, channel, test_settings,
                                                                           monkeypatch):
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=True)
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW}, headers=owner)
    assert r.status_code == 422 and r.json()["code"] == "accept_data_loss"
    assert bootstate.read_rollback(test_settings.data_dir) is None and requests_in(channel) == []

    order: list[str] = []
    real = updater_client.write_request

    def spy(*args, **kwargs):
        order.append("marker" if bootstate.read_rollback(test_settings.data_dir) else "no-marker")
        return real(*args, **kwargs)

    monkeypatch.setattr(updater_client, "write_request", spy)
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW, "accept_data_loss": True},
                          headers=owner)
    assert r.status_code == 202, r.text
    assert order == ["marker"], "die Vormerkung steht VOR der Anforderung"
    assert r.json()["data_revert"] is True
    marker = bootstate.read_rollback(test_settings.data_dir)
    assert marker["copy"] == COPY and marker["by"] == update_helper.ROLLBACK_BY
    request_id = r.json()["request_id"]

    # Der Helfer lehnt ab: die Vormerkung verschwindet sofort, nicht erst nach 24 Stunden.
    take_requests(channel)
    write_status(channel, status_doc(results=[result(request_id, action="rollback", outcome="refused",
                                                     code="previous_mismatch", frm="0.7.1", to="0.7.0")]))
    assert (await client.get("/api/v1/system/updates/helper", headers=owner)).status_code == 200
    assert bootstate.read_rollback(test_settings.data_dir) is None
    entries = await _audit(db_session)
    assert [e.action for e in entries][-2:] == ["system.update.rollback_requested", "system.update.refused"]
    assert entries[-1].actor_type == "system" and entries[-1].actor_id == "updater"


async def test_rollback_marker_is_removed_when_the_request_cannot_be_written(client, channel, test_settings, monkeypatch):
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=True)
    real = updater_client.write_request

    def broken(*args, **kwargs):
        os.chmod(channel / "requests", 0o755)
        try:
            return real(*args, **kwargs)
        finally:
            os.chmod(channel / "requests", 0o1777)

    monkeypatch.setattr(updater_client, "write_request", broken)
    r = await client.post("/api/v1/system/updates/rollback",
                          json={"current_password": OWNER_PW, "accept_data_loss": True}, headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "channel_unsafe"
    assert bootstate.read_rollback(test_settings.data_dir) is None
    assert requests_in(channel) == []


async def test_a_marker_that_cannot_be_saved_requests_nothing_and_keeps_the_window_free(client, db_session, channel,
                                                                                       test_settings, monkeypatch):
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=True)
    body = {"current_password": OWNER_PW, "accept_data_loss": True}
    real = bootstate.request_rollback
    disk_full = [True]

    def maybe_full(*args, **kwargs):
        if disk_full[0]:
            raise OSError(28, "No space left on device")
        return real(*args, **kwargs)

    monkeypatch.setattr(bootstate, "request_rollback", maybe_full)
    r = await client.post("/api/v1/system/updates/rollback", json=body, headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "marker_failed", r.text
    assert requests_in(channel) == [] and bootstate.read_rollback(test_settings.data_dir) is None
    assert (await _audit(db_session))[-1].detail["code"] == "marker_failed"

    disk_full[0] = False
    r = await client.post("/api/v1/system/updates/rollback", json=body, headers=owner)
    assert r.status_code == 202, "es wurde nichts angefordert: kein 429"
    assert bootstate.read_rollback(test_settings.data_dir)["copy"] == COPY


@pytest.mark.parametrize(("after", "data", "hint"), [
    ("migrated_again", "reset", "wurde dabei aber schon auf den Stand vor dem Update zurückgesetzt"),
    ("untouched", "kept", None),
    ("unknown", "unclear", "vielleicht auf den Stand vor dem Update zurückgesetzt"),
])
async def test_a_failed_rollback_with_data_says_honestly_what_happened_to_the_data(
        client, db_session, channel, test_settings, after, data, hint):
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=True)
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW, "accept_data_loss": True},
                          headers=owner)
    assert r.status_code == 202, r.text
    request_id = r.json()["request_id"]
    take_requests(channel)
    # Was der Helfer und die beiden Starts getan haben: die alte Version wurde nicht gesund, die neuere laeuft wieder.
    state = bootstate.read_state(test_settings.data_dir)
    if after == "migrated_again":  # die alte Version hat die Kopie eingespielt, die neuere hat erneut umgebaut
        state["last_migration"] = {**state["last_migration"], "at": bootstate.now_iso(), "copy": "20261002T120000Z_0.7.0_0.7.1.db"}
        bootstate.write_state(test_settings.data_dir, state)
    elif after == "unknown":
        state.pop("last_migration")
        bootstate.write_state(test_settings.data_dir, state)
    write_status(channel, status_doc(results=[result(request_id, action="rollback", outcome="rolled_back",
                                                     code="timeout", frm="0.7.1", to="0.7.0")]))
    assert (await client.get("/api/v1/system/updates/helper", headers=owner)).status_code == 200

    entry = (await _audit(db_session))[-1]
    assert entry.action == "system.update.rolled_back" and entry.detail["data"] == data
    reason = entry.reason
    note = (await db_session.execute(select(Notification))).scalars().one()
    assert note.title == "Rückweg hat nicht geklappt" and "Version 0.7.1 läuft weiter" in note.body
    if hint is None:
        assert "Datenbank" not in note.body and reason is None
    else:
        assert hint in note.body and "restore/replaced-" in note.body
        assert hint in reason


async def test_a_failed_update_or_a_rollback_without_data_has_no_data_hint(channel, db_session, test_settings):
    write_status(channel, status_doc(results=[
        result(REQUEST_ID, outcome="rolled_back", code="timeout"),
        result(OTHER_ID, action="rollback", outcome="rolled_back", code="timeout", frm="0.7.1", to="0.7.0"),
    ]))
    assert await update_helper.record_results(test_settings) == 2
    entries = await _audit(db_session)
    assert all("data" not in e.detail and e.reason is None for e in entries)
    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert len(notes) == 2 and all("Datenbank" not in n.body for n in notes)


@pytest.mark.parametrize(("migrated", "copy_exists", "from_version", "expected"), [
    (False, True, "0.7.0", (False, None)),
    (True, True, "0.7.0", (True, 1790823600)),
    (True, False, "0.7.0", (None, None)),
    (True, True, "0.6.5", (None, None)),
])
async def test_the_view_says_before_the_click_whether_the_data_go_back(client, channel, test_settings, migrated,
                                                                       copy_exists, from_version, expected):
    """Die Oberflaeche warnt nur, wenn die Daten wirklich mit zurueckgehen, und nennt den Zeitpunkt, seit dem alles
    verloren geht (Beginn des Umbaus; die Kopie ist von direkt davor). Unklar heisst: der Rueckweg wird abgelehnt."""
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=migrated, copy_exists=copy_exists, from_version=from_version)
    previous = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()["previous"]
    assert previous["version"] == "0.7.0"
    assert (previous["data_revert"], previous["data_since"]) == expected
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW}, headers=owner)
    code = {False: None, True: "accept_data_loss", None: "data_unclear"}[expected[0]]
    assert (r.json().get("code") if r.status_code != 202 else None) == code, "Ansicht und Rueckweg entscheiden gleich"


async def test_an_unreadable_time_of_the_rebuild_still_warns(client, channel, test_settings):
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=True)
    state = bootstate.read_state(test_settings.data_dir)
    bootstate.write_state(test_settings.data_dir, {**state, "last_migration": {**state["last_migration"], "at": "gestern"}})
    previous = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()["previous"]
    assert (previous["data_revert"], previous["data_since"]) == (True, None)


@pytest.mark.parametrize(("frm", "expected"), [("0.7.0", "Version 0.7.0, die vorher lief,"),
                                               (None, "die Version, die vorher lief,")])
def test_the_message_after_failed_manual_points_to_the_updates_card(frm, expected):
    """Die Karte Updates sagt, was zu tun ist (Einstellungen -> System -> Updates); die Meldung verweist genau dorthin."""
    title, body, severity = update_helper._message(
        {"id": REQUEST_ID, "action": "update", "from": frm, "to": "0.7.1", "outcome": "failed_manual",
         "code": "rollback_failed", "finished_at": 1})
    assert severity == "critical" and title == "Update: bitte von Hand nachsehen"
    assert expected in body and "Einstellungen → System → Updates" in body
    assert "?" not in body


@pytest.mark.parametrize(("copy_exists", "from_version"), [(False, "0.7.0"), (True, "0.6.5")])
async def test_rollback_409_when_the_data_do_not_fit(client, channel, test_settings, copy_exists, from_version):
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=True, copy_exists=copy_exists, from_version=from_version)
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW, "accept_data_loss": True},
                          headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "data_unclear"
    assert bootstate.read_rollback(test_settings.data_dir) is None and requests_in(channel) == []


async def test_a_rollback_marker_of_the_rescue_page_is_left_alone(client, channel, test_settings):
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=True)
    r = await client.post("/api/v1/system/updates/rollback", json={"current_password": OWNER_PW, "accept_data_loss": True},
                          headers=owner)
    request_id = r.json()["request_id"]
    take_requests(channel)
    bootstate.request_rollback(test_settings.data_dir, COPY, by="notseite")
    write_status(channel, status_doc(results=[result(request_id, action="rollback", outcome="refused", code="busy")]))
    await client.get("/api/v1/system/updates/helper", headers=owner)
    assert bootstate.read_rollback(test_settings.data_dir)["by"] == "notseite"


# ---------------------------------------------------------------------------
# Ergebnisse: genau einmal
# ---------------------------------------------------------------------------


async def test_each_result_is_logged_and_notified_exactly_once(client, db_session, channel, test_settings):
    owner = await _owner(client)
    write_status(channel, status_doc(results=[
        result(REQUEST_ID, outcome="applied"), result(OTHER_ID, outcome="refused", code="rate_limited"),
    ]))
    for _ in range(3):
        assert (await client.get("/api/v1/system/updates/helper", headers=owner)).status_code == 200
    update_helper.reset_for_tests()  # wie nach einem Neustart: nur die Datei weiss noch, was schon festgehalten ist
    assert await update_helper.record_results(test_settings) == 0
    entries = await _audit(db_session)
    assert sorted(e.action for e in entries) == ["system.update.applied", "system.update.refused"]
    applied = next(e for e in entries if e.action == "system.update.applied")
    assert (applied.actor_type, applied.actor_id, applied.outcome) == ("system", "updater", "success")
    assert applied.detail == {"request_id": REQUEST_ID, "action": "update", "from": "0.7.0", "to": "0.7.1", "code": None}
    db_session.expire_all()
    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert [(n.title, n.correlation_id) for n in notes] == [("Nodvard Deck ist aktualisiert", REQUEST_ID)]
    assert stat_mode(test_settings.data_dir / update_helper.SEEN_NAME) == 0o600

    # Ein neues Ergebnis kommt dazu, die alten bleiben einmalig.
    write_status(channel, status_doc(results=[
        result(REQUEST_ID, outcome="applied"), result(OTHER_ID, outcome="refused", code="rate_limited"),
        result("1c9e3a51-2c4d-4e6f-9a10-2b3c4d5e6f71", action="rollback", outcome="failed_manual",
               code="rollback_failed"),
    ]))
    assert await update_helper.record_results(test_settings) == 1
    assert len(await _audit(db_session)) == 3
    db_session.expire_all()
    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert [n.severity for n in notes] == ["info", "critical"]


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


async def test_results_of_a_stale_status_still_count(channel, db_session, test_settings):
    write_status(channel, status_doc(heartbeat_at=1, results=[result()]))
    assert await update_helper.record_results(test_settings) == 1


async def test_a_busy_helper_at_start_gets_a_follow_up(channel, db_session, test_settings):
    write_status(channel, status_doc(state="busy", ready=False, reason="busy", target=None,
                                     busy={"id": REQUEST_ID, "action": "update", "step": "started", "since": 1}))
    assert update_helper.follow_up_needed(test_settings)
    task = asyncio.create_task(update_helper.follow_up(test_settings, interval_s=0.01, max_s=5))
    await asyncio.sleep(0.05)
    write_status(channel, status_doc(results=[result()]))
    await asyncio.wait_for(task, 5)
    assert [e.action for e in await _audit(db_session)] == ["system.update.applied"]
    assert not update_helper.follow_up_needed(test_settings)
    assert update_helper.start_follow_up(test_settings) is None


# ---------------------------------------------------------------------------
# Der echte Start (`main.lifespan`)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_db(test_settings, monkeypatch, tmp_path):
    """Datei-Datenbank und Einstellungen dieses Tests fuer den echten Start (wie `test_demo_seed.py`); gibt einen
    Sessionmaker zum Nachsehen zurueck."""
    from nodvard_deck import config
    from nodvard_deck.core.events import reset_event_bus
    from nodvard_deck.core.metrics_history import reset_metrics_collector
    from nodvard_deck.core.scheduler import reset_scheduler_service
    from nodvard_deck.db.session import (
        create_engine_for,
        reset_engine_cache,
        set_engine_for_testing,
    )
    from nodvard_deck.main import app
    from nodvard_deck.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker

    test_settings.database_url = f"sqlite+aiosqlite:///{tmp_path / 'start.db'}"
    test_settings.metrics_interval_s = 0
    monkeypatch.setattr(config, "_settings", test_settings)
    engine = create_engine_for(test_settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_engine_for_testing(engine)
    original_routes = list(app.router.routes)
    reset_metrics_collector()
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        app.router.routes[:] = original_routes
        await engine.dispose()
        reset_engine_cache()
        reset_scheduler_service()
        reset_metrics_collector()
        reset_event_bus()


async def _logged_at_start(sessionmaker) -> list[AuditEntry]:
    async with sessionmaker() as verify:
        rows = (await verify.execute(select(AuditEntry).order_by(AuditEntry.ts))).scalars().all()
    return [row for row in rows if row.action.startswith("system.update.")]


async def test_the_start_records_a_result_the_helper_left_behind_exactly_once(channel, app_db):
    """Ein Update hat diesen Start ausgeloest: der Helfer hat sein Ergebnis in `status.json` hinterlassen. Schon der
    Start haelt es fest (Protokoll und Meldung), ohne dass jemand die Seite oeffnet."""
    from nodvard_deck.main import app, lifespan
    from nodvard_deck.models import Notification

    write_status(channel, status_doc(results=[result(outcome="applied")]))
    for _ in range(2):  # der zweite Start findet dasselbe Ergebnis wieder: nichts doppelt
        async with lifespan(app):
            pass
    (entry,) = await _logged_at_start(app_db)
    assert (entry.action, entry.actor_type, entry.actor_id) == ("system.update.applied", "system", "updater")
    assert entry.target_id == REQUEST_ID and entry.outcome == "success"
    async with app_db() as verify:
        titles = [n.title for n in (await verify.execute(select(Notification))).scalars()]
    assert titles == ["Nodvard Deck ist aktualisiert"]


async def test_the_start_follows_up_while_the_helper_is_busy_and_stops_that_at_the_end(channel, app_db, monkeypatch):
    from nodvard_deck.main import app, lifespan

    write_status(channel, status_doc(state="busy", ready=False, reason="busy", target=None,
                                     busy={"id": REQUEST_ID, "action": "update", "step": "started", "since": 1}))
    started: list[asyncio.Task | None] = []
    real = update_helper.start_follow_up

    def spy(settings):
        started.append(real(settings))
        return started[-1]

    monkeypatch.setattr(update_helper, "start_follow_up", spy)
    async with lifespan(app):
        assert len(started) == 1 and started[0] is not None, "der Helfer ist beschaeftigt: ein Nachlauf wird angelegt"
        assert not started[0].done()
    assert started[0].cancelled(), "beim Beenden wird der Nachlauf gestoppt, er laeuft nicht weiter"
    assert await _logged_at_start(app_db) == []


async def test_the_start_without_a_busy_helper_has_no_follow_up(channel, app_db, monkeypatch):
    from nodvard_deck.main import app, lifespan

    started: list[asyncio.Task | None] = []
    real = update_helper.start_follow_up
    monkeypatch.setattr(update_helper, "start_follow_up", lambda settings: started.append(real(settings)) or started[-1])
    async with lifespan(app):
        pass
    assert started == [None]


# ---------------------------------------------------------------------------
# Keine fremden Texte, keine Umgebung
# ---------------------------------------------------------------------------


async def test_no_environment_value_reaches_status_audit_or_api(client, db_session, channel, test_settings, monkeypatch):
    monkeypatch.setenv("X", "GEHEIM123")
    owner = await _owner(client)
    doc = status_doc(results=[result(), {**result(OTHER_ID), "log": "X=GEHEIM123"}], env={"X": "GEHEIM123"})
    doc["target"]["note"] = "X=GEHEIM123"
    write_status(channel, doc)
    seen: list[str] = []
    for path in ("/api/v1/system/updates/helper", "/api/v1/system/info", "/api/v1/system/updates"):
        seen.append((await client.get(path, headers=owner)).text)
    seen.append((await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)).text)
    seen += [path.read_text() for path in (channel / "requests").iterdir()]
    write_status(channel, status_doc(reason="X=GEHEIM123"))
    seen.append((await client.get("/api/v1/system/updates/helper", headers=owner)).text)
    seen.append((test_settings.data_dir / update_helper.SEEN_NAME).read_text())
    seen += [json.dumps([e.action, e.reason, e.detail]) for e in await _audit(db_session, "")]
    db_session.expire_all()
    seen += [n.title + n.body + json.dumps(n.payload) for n in (await db_session.execute(select(Notification))).scalars()]
    assert all("GEHEIM123" not in text for text in seen)
    assert len(seen) > 8


def test_the_updater_has_a_readable_name():
    labels = ActorLabels({}, {})
    assert labels.actor("system", "updater") == "Update-Helfer"
    assert labels.actor("updater", "x") == "updater/x", "`updater` ist keine Akteurart, nur eine Kennung unter `system`"
    assert labels.actor("system", "gate") == "System"


# ---------------------------------------------------------------------------
# Gegenpruefung
# ---------------------------------------------------------------------------


async def test_a_status_with_gigantic_numbers_does_not_break_the_system_pages(client, channel):
    owner = await _owner(client)
    write_status(channel, status_doc(heartbeat_at=10 ** 400))
    assert (await client.get("/api/v1/system/info", headers=owner)).status_code == 200
    body = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert body["present"] is False and body["reason"] == "invalid"


async def test_the_same_result_twice_in_one_status_is_logged_once(channel, db_session, test_settings):
    write_status(channel, status_doc(results=[result(outcome="applied"), result(outcome="applied")]))
    assert await update_helper.record_results(test_settings) == 1
    assert [e.action for e in await _audit(db_session)] == ["system.update.applied"]


async def test_results_stay_unique_when_the_seen_file_cannot_be_written(channel, db_session, test_settings):
    (test_settings.data_dir / update_helper.SEEN_NAME).mkdir()  # nicht schreibbar (z. B. Platte voll)
    write_status(channel, status_doc(results=[result()]))
    assert await update_helper.record_results(test_settings) == 1
    update_helper.reset_for_tests()  # Neustart: auch das Gedaechtnis des Prozesses ist weg
    assert await update_helper.record_results(test_settings) == 0
    assert [e.action for e in await _audit(db_session)] == ["system.update.applied"]


async def test_an_old_refusal_does_not_delete_the_marker_of_a_new_rollback(client, channel, test_settings):
    owner = await _owner(client)
    _ready_for_rollback(channel, test_settings, migrated=True)
    body = {"current_password": OWNER_PW, "accept_data_loss": True}
    first = await client.post("/api/v1/system/updates/rollback", json=body, headers=owner)
    assert first.status_code == 202, first.text
    take_requests(channel)
    # Der erste Rueckweg liegt 15 Minuten zurueck (Anforderung wie Vormerkung). Der Helfer hat ihn abgelehnt, das
    # Dashboard hat das aber noch nicht gesehen: Spaeter (Fenster frei) kommt ein zweiter Rueckweg auf dieselbe Kopie.
    book_path = test_settings.data_dir / update_helper.SEEN_NAME
    book = json.loads(book_path.read_text())
    book["requests"][0]["at"] -= 900
    book_path.write_text(json.dumps(book))
    bootstate.request_rollback(test_settings.data_dir, COPY, by=update_helper.ROLLBACK_BY, now=time.time() - 900)
    update_helper.reset_for_tests()
    second_started = time.time()
    second = await client.post("/api/v1/system/updates/rollback", json=body, headers=owner)
    assert second.status_code == 202, second.text
    assert second.json()["request_id"] != first.json()["request_id"]
    # Erst jetzt steht die alte Ablehnung im Status (der zweite Rueckweg hat sie beim Anfordern noch nicht gesehen).
    refused = result(first.json()["request_id"], action="rollback", outcome="refused", code="busy",
                     frm="0.7.1", to="0.7.0", finished_at=int(time.time()) - 900)
    write_status(channel, status_doc(target={"current_version": "0.7.1", "floating_tag": "latest", "pinned": False},
                                     previous={"version": "0.7.0", "until": int(time.time()) + 3600},
                                     results=[refused]))
    assert await update_helper.record_results(test_settings) == 1
    marker = bootstate.read_rollback(test_settings.data_dir)
    assert marker is not None and marker["copy"] == COPY, "die Vormerkung des zweiten Rueckwegs bleibt"
    assert marker["requested_at"] >= second_started, "und es ist die des zweiten, nicht die des ersten Rueckwegs"


async def test_the_callers_text_never_lands_in_the_audit(client, db_session, channel):
    await _owner(client)
    admin = await _user_with_role(client, db_session, "admin", "admin1")
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(version="X=GEHEIM123"), headers=admin)
    assert r.status_code == 403
    entries = await _audit(db_session)
    assert entries and all("GEHEIM123" not in json.dumps(e.detail) for e in entries)


async def test_while_the_helper_cleans_up_after_the_commit_the_result_is_there_and_nothing_is_busy(
        client, db_session, channel, test_settings):
    owner = await _owner(client)
    until = int(time.time()) + 7 * 24 * 3600
    cleaning = status_doc(state="busy", ready=False, reason="busy", target=None,
                          busy={"id": REQUEST_ID, "action": "update", "step": "committed", "since": 1},
                          previous={"version": "0.7.0", "until": until}, results=[result(outcome="applied")])
    write_status(channel, cleaning)
    body = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert body["busy"] is None and body["state"] == "idle"
    assert body["ready"] is False and body["ready_reason"] == "finishing"
    assert body["last_result"]["outcome"] == "applied"
    assert body["previous"] is None, "der Rueckweg erst, wenn der Helfer fertig ist (er kann noch der ersetzte sein)"
    assert [e.action for e in await _audit(db_session)] == ["system.update.applied"], "das Ergebnis zaehlt sofort"
    assert not update_helper.follow_up_needed(test_settings), "kein Nachlauf mehr noetig"
    r = await client.post("/api/v1/system/updates/apply", json=apply_body(), headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "helper_finishing"

    # Aufgeraeumt: derselbe Eintrag, nichts kommt doppelt.
    write_status(channel, status_doc(previous={"version": "0.7.0", "until": until}, results=[result(outcome="applied")]))
    body = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert body["ready"] is True and body["ready_reason"] is None
    assert body["previous"] == {"version": "0.7.0", "until": until, "data_revert": False, "data_since": None}
    assert [e.action for e in await _audit(db_session)][:1] == ["system.update.applied"]
    assert len([e for e in await _audit(db_session) if e.action == "system.update.applied"]) == 1


def _vector(name: str) -> dict:
    """Ein gueltiger Status aus den gemeinsamen Vektoren, mit frischem Herzschlag."""
    doc = next(case["doc"] for case in vectors("status")["valid"] if case["name"] == name)
    return {**json.loads(json.dumps(doc)), "heartbeat_at": int(time.time())}


@pytest.mark.parametrize("path", ["apply", "rollback"])
async def test_the_cleanup_vector_shows_the_result_and_no_way_back_yet(client, db_session, channel, test_settings,
                                                                        path):
    # So schreibt der Helfer den Status, solange er nach dem Commit aufraeumt: `busy.step` = `committed`, das Ergebnis
    # steht schon in `results`, `previous` zeigt womoeglich noch den Rueckweg, den das Update gleich ersetzt. Die Ansicht
    # zeigt dann keinen Rueckweg.
    owner = await _owner(client)
    doc = _vector("beschaeftigt, Aufraeumen")
    doc["previous"] = {**doc["previous"], "until": int(time.time()) + 3600}
    assert doc["busy"]["step"] == "committed" and doc["previous"] is not None
    assert [r["id"] for r in doc["results"]] == [doc["busy"]["id"]]
    write_status(channel, doc)
    body = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert (body["ready_reason"], body["busy"], body["state"], body["previous"]) == ("finishing", None, "idle", None)
    assert body["ready"] is False and body["last_result"]["outcome"] == "applied"
    assert not update_helper.follow_up_needed(test_settings)
    payload = apply_body() if path == "apply" else {"current_password": OWNER_PW, "accept_data_loss": True}
    r = await client.post(f"/api/v1/system/updates/{path}", json=payload, headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "helper_finishing"
    assert requests_in(channel) == []


@pytest.mark.parametrize("path", ["apply", "rollback"])
async def test_committed_without_its_result_is_still_busy(client, channel, test_settings, path):
    # Der Helfer schreibt den Status zu `committed` erst mit dem Ergebnis. Zeigt ein Status trotzdem `committed` ohne
    # das Ergebnis dieser Anforderung (etwa von einem aelteren Helfer), gilt er weiter als beschaeftigt -- nie als
    # "raeumt auf". Einen Rueckweg zeigt die Ansicht auch dann nicht: Der im Status kann schon ersetzt oder verbraucht
    # sein.
    owner = await _owner(client)
    doc = _vector("beschaeftigt, Aufraeumen")
    doc["results"] = []
    doc["previous"] = {**doc["previous"], "until": int(time.time()) + 3600}
    write_status(channel, doc)
    body = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert body["busy"]["step"] == "committed" and body["state"] == "busy" and body["ready_reason"] == "busy"
    assert body["last_result"] is None and body["previous"] is None
    assert update_helper.follow_up_needed(test_settings)
    payload = apply_body() if path == "apply" else {"current_password": OWNER_PW, "accept_data_loss": True}
    r = await client.post(f"/api/v1/system/updates/{path}", json=payload, headers=owner)
    assert r.status_code == 409 and r.json()["code"] == "helper_busy"
    assert requests_in(channel) == []


async def test_after_a_rollback_the_used_way_back_is_not_shown_while_cleaning_up(client, channel):
    # Nach einem Rueckweg zeigt `previous` im Status bis zum Speichern noch den gerade benutzten Slot (die Version, die
    # jetzt wieder laeuft). Die Ansicht zeigt ihn nicht, auch nicht danach: einen neuen Rueckweg gibt es nicht.
    owner = await _owner(client)
    doc = _vector("beschaeftigt, Aufraeumen")
    rid = doc["busy"]["id"]
    doc["busy"] = {**doc["busy"], "action": "rollback"}
    doc["previous"] = {"version": "0.7.0", "until": int(time.time()) + 3600}
    doc["results"] = [{**doc["results"][0], "action": "rollback", "from": "0.7.1", "to": "0.7.0",
                       "outcome": "reverted"}]
    assert doc["results"][0]["id"] == rid
    write_status(channel, doc)
    body = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert (body["ready_reason"], body["busy"], body["previous"]) == ("finishing", None, None)
    assert body["last_result"]["outcome"] == "reverted"


async def test_a_busy_step_before_the_commit_stays_busy(client, channel, test_settings):
    owner = await _owner(client)
    write_status(channel, status_doc(state="busy", ready=False, reason="busy", target=None,
                                     busy={"id": REQUEST_ID, "action": "update", "step": "committed", "since": 1},
                                     results=[result(OTHER_ID, outcome="applied")]))
    body = (await client.get("/api/v1/system/updates/helper", headers=owner)).json()
    assert body["busy"]["id"] == REQUEST_ID and body["state"] == "busy", "ohne eigenes Ergebnis noch beschaeftigt"
    assert update_helper.follow_up_needed(test_settings)
