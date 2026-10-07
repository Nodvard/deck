"""Drosselung fehlgeschlagener Anmeldungen (core/login_limit.py)."""

from __future__ import annotations

import time

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient
from nodvard_deck.core import login_limit
from nodvard_deck.models import AuditEntry
from sqlalchemy import select
from totp_helpers import setup_confirm_code

PASSWORD = "correct-horse-battery"


async def _bootstrap(client, username="nico"):
    r = await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"})
    assert r.status_code == 201


async def _login(client, username="nico", password=PASSWORD):
    return await client.post("/api/v1/auth/login", json={"username": username, "password": password})


def _other_ip_client(ip: str) -> AsyncClient:
    from nodvard_deck.main import app

    transport = ASGITransport(app=app, client=(ip, 4711), raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(login_limit, "_now", lambda: now[0])
    return now


async def _audit(db_session, action: str) -> list[AuditEntry]:
    result = await db_session.execute(select(AuditEntry).where(AuditEntry.action == action))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_eleventh_attempt_is_429_even_with_correct_password(client, db_session, monkeypatch):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        assert (await _login(client, password="falsch")).status_code == 401
    assert len(await _audit(db_session, "login.locked")) == 1

    from nodvard_deck.core import security

    calls = []
    real_verify = security.verify_password
    monkeypatch.setattr(
        security, "verify_password", lambda *a, **k: calls.append(1) or real_verify(*a, **k)
    )
    r = await _login(client)
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Zu viele Fehlversuche. Bitte in ")
    assert r.json()["detail"].endswith("erneut versuchen.")
    assert 0 < int(r.headers["Retry-After"]) <= login_limit.WINDOW_SECONDS
    assert calls == []  # gesperrt, bevor Argon2 ueberhaupt laeuft

    assert (await _login(client, password="falsch")).status_code == 429
    # Genau ein Audit-Eintrag je Sperre, weitere 429er schreiben keinen neuen.
    assert len(await _audit(db_session, "login.locked")) == 1
    assert len(await _audit(db_session, "login.failed")) == login_limit.MAX_FAILURES_PER_USER


@pytest.mark.asyncio
async def test_lock_ends_after_the_window(client, clock):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        assert (await _login(client, password="falsch")).status_code == 401
        clock[0] += 1
    r = await _login(client)
    assert r.status_code == 429
    # Aeltester Fehlversuch bei t=1000, jetzt t=1010 -> noch 290 s.
    assert r.headers["Retry-After"] == str(login_limit.WINDOW_SECONDS - 10)
    assert "in 5 Minuten" in r.json()["detail"]

    clock[0] += login_limit.WINDOW_SECONDS - 10
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_successful_login_resets_the_counter(client):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER - 1):
        assert (await _login(client, password="falsch")).status_code == 401
    assert (await _login(client)).status_code == 200
    for _ in range(login_limit.MAX_FAILURES_PER_USER - 1):
        assert (await _login(client, password="falsch")).status_code == 401
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_other_username_and_other_ip_are_not_locked(client):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        await _login(client, password="falsch")
    assert (await _login(client)).status_code == 429
    # Anderer Name von derselben IP: normale Pruefung (401), keine Sperre.
    assert (await _login(client, username="anna", password="x")).status_code == 401
    # Derselbe Name von einem anderen Geraet: klappt.
    async with _other_ip_client("192.168.1.50") as other:
        assert (await _login(other)).status_code == 200


@pytest.mark.asyncio
async def test_many_usernames_from_one_ip_are_throttled(client, db_session):
    await _bootstrap(client)
    for i in range(login_limit.MAX_FAILURES_PER_IP):
        assert (await _login(client, username=f"user{i}", password="x")).status_code == 401
    r = await _login(client)
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    locked = await _audit(db_session, "login.locked")
    assert [e.detail["scopes"] for e in locked] == [["ip"]]
    assert locked[0].detail["window_seconds"] == login_limit.WINDOW_SECONDS
    assert "account_window_seconds" not in locked[0].detail and "day_window_seconds" not in locked[0].detail
    async with _other_ip_client("192.168.1.50") as other:
        assert (await _login(other)).status_code == 200


@pytest.mark.asyncio
async def test_overlong_username_is_clipped_in_log_and_memory(client, db_session):
    await _bootstrap(client)
    huge = "x" * 300_000
    r = await _login(client, username=huge)
    assert r.status_code == 401
    assert r.json()["detail"] == (await _login(client, username="gibtsnicht", password="x")).json()["detail"]
    entries = await _audit(db_session, "login.failed")
    assert entries
    assert all(len(e.actor_id) <= 128 for e in entries)
    assert sum(len(e.actor_id) + len(e.reason or "") + len(str(e.detail)) for e in entries) < 2000
    assert all(len(user) <= 64 for _ip, user in login_limit._by_user)


@pytest.mark.asyncio
async def test_overlong_username_and_password_never_log_in(client, db_session):
    await _bootstrap(client)
    prefix_user = "nico" + "x" * 100
    assert (await _login(client, username=prefix_user)).status_code == 401
    assert (await _login(client, password=PASSWORD + "y" * 5000)).status_code == 401
    # Das eigene Konto bleibt davon unberuehrt.
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_overlong_username_lockout_entry_is_short(client, db_session):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        assert (await _login(client, username="y" * 200_000)).status_code == 401
    locked = await _audit(db_session, "login.locked")
    assert len(locked) == 1
    assert "username" not in locked[0].detail
    assert locked[0].detail["username_length"] == 200_000
    assert locked[0].actor_id == "unbekannt"


async def test_audit_entries_are_clipped(db_session):
    from nodvard_deck.core import audit

    entry = await audit.write_entry(
        db_session,
        actor_type="user",
        actor_id="a" * 5000,
        action="x.y",
        outcome="failure",
        reason="r" * 50_000,
        ip="1" * 500,
        user_agent="u" * 5000,
    )
    assert len(entry.actor_id) == 128
    assert len(entry.ip) == 64
    assert len(entry.user_agent) == 255
    # Begruendungen (z. B. beim Ablehnen einer Aktion) bleiben vollstaendig.
    assert entry.reason == "r" * 50_000


@pytest.mark.asyncio
async def test_argon2_runs_once_for_active_disabled_and_unknown_accounts(client, db_session, monkeypatch):
    """Die Antwortzeit darf nicht verraten, ob ein Konto existiert oder deaktiviert ist: in allen
    Faellen laeuft genau eine Argon2-Pruefung (auch bei richtigem Passwort eines deaktivierten Kontos)."""
    from nodvard_deck.core import security
    from nodvard_deck.models import User

    await _bootstrap(client)
    owner = (await _login(client)).json()["access_token"]
    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers={"Authorization": f"Bearer {owner}"})).json()}
    created = await client.post(
        "/api/v1/users",
        json={"username": "gesperrt", "password": PASSWORD, "role_ids": [roles["viewer"]]},
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert created.status_code == 201, created.text
    user = (await db_session.execute(select(User).where(User.username == "gesperrt"))).scalars().one()
    user.is_active = False
    await db_session.flush()

    calls: list[int] = []
    real_verify = security.verify_password
    monkeypatch.setattr(security, "verify_password", lambda *a, **k: calls.append(1) or real_verify(*a, **k))

    async def count(username: str, password: str) -> tuple[int, int]:
        calls.clear()
        r = await _login(client, username=username, password=password)
        return r.status_code, len(calls)

    assert await count("nico", "falsch") == (401, 1)  # aktiv, falsches Passwort
    assert await count("gesperrt", "falsch") == (401, 1)  # deaktiviert, falsches Passwort
    assert await count("gesperrt", PASSWORD) == (401, 1)  # deaktiviert, richtiges Passwort: trotzdem abgelehnt
    assert await count("gibtsnicht", "falsch") == (401, 1)  # unbekannt
    assert await count("x" * 500, "falsch") == (401, 1)  # zu lang, laeuft wie unbekannt
    assert await count("nico", PASSWORD) == (200, 1)  # aktiv, richtig


@pytest.mark.asyncio
async def test_typed_name_of_unknown_user_never_reaches_the_audit_log(client, db_session):
    """Wer sein Passwort ins Benutzerfeld tippt, findet es nicht im Protokoll: bei einem Namen, den es
    nicht gibt, steht als Akteur nur „unbekannt“ dort, dazu eine Kennung und die Laenge -- bei
    `login.failed` wie bei `login.locked`."""
    await _bootstrap(client)
    typed = "mein-geheimes-passwort-2026"
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        assert (await _login(client, username=typed, password="x")).status_code == 401

    failed = await _audit(db_session, "login.failed")
    locked = await _audit(db_session, "login.locked")
    assert len(failed) == login_limit.MAX_FAILURES_PER_USER
    assert len(locked) == 1
    everything = repr([(e.actor_id, e.reason, e.detail, e.target_id) for e in failed + locked])
    assert typed not in everything
    assert {(e.actor_type, e.actor_id) for e in failed + locked} == {("anonymous", "unbekannt")}
    assert "username" not in locked[0].detail
    refs = {e.detail["username_ref"] for e in failed + locked}
    assert len(refs) == 1  # gleicher Name, gleiche Kennung
    (ref,) = refs
    assert len(ref) == 12 and set(ref) <= set("0123456789abcdef")
    assert {e.detail["username_length"] for e in failed + locked} == {len(typed)}

    # Ein anderer Name bekommt eine andere Kennung; ein bekannter Name bleibt erkennbar.
    await _login(client, username="anderer-name", password="x")
    other = (await _audit(db_session, "login.failed"))[-1]
    assert other.actor_id == "unbekannt" and other.detail["username_ref"] != ref
    await _login(client, username="nico", password="falsch")
    last = (await _audit(db_session, "login.failed"))[-1]
    assert last.target_type == "user" and last.actor_id == last.target_id != "unbekannt"
    assert "username_ref" not in (last.detail or {})


def test_lockout_message_singular():
    assert login_limit.lockout_message(30) == "Zu viele Fehlversuche. Bitte in 1 Minute erneut versuchen."
    assert login_limit.lockout_message(61) == "Zu viele Fehlversuche. Bitte in 2 Minuten erneut versuchen."


# ---------------------------------------------------------------------------
# Zweiter Faktor
# ---------------------------------------------------------------------------


async def _enable_totp(client, now: float | None = None) -> str:
    """`now`: die feste TOTP-Uhr des Tests (`totp_clock`), sonst die echte. Sonst gehoerte der Code der Einrichtung an
    einer Schrittgrenze schon zum Schritt der Test-Uhr und gaelte dort als benutzt."""
    await _bootstrap(client)
    token = (await _login(client)).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=headers)).json()["secret"]
    confirm = await client.post(
        "/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret, now)}, headers=headers
    )
    assert confirm.status_code == 200
    return secret


def _wrong_code(secret: str) -> str:
    totp = pyotp.TOTP(secret)
    now = time.time()
    valid = {totp.at(now + offset) for offset in (-30, 0, 30)}
    return next(c for c in (f"{n:06d}" for n in range(1000000)) if c not in valid)


async def _mfa_token(client) -> str:
    r = await _login(client)
    assert r.status_code == 202
    return r.json()["mfa_token"]


async def _mfa(client, mfa_token: str, code: str):
    return await client.post("/api/v1/auth/mfa", json={"mfa_token": mfa_token, "code": code})


@pytest.mark.asyncio
async def test_mfa_token_is_dead_after_five_wrong_codes(client):
    secret = await _enable_totp(client)
    wrong = _wrong_code(secret)
    mfa_token = await _mfa_token(client)

    for _ in range(login_limit.MAX_MFA_FAILURES - 1):
        r = await _mfa(client, mfa_token, wrong)
        assert r.status_code == 401
        assert r.json()["detail"] == "Ungültiger Zwei-Faktor-Code."
    r = await _mfa(client, mfa_token, wrong)
    assert r.status_code == 429
    assert "erneut an" in r.json()["detail"]

    # Auch der richtige Code hilft mit diesem Token nicht mehr.
    r = await _mfa(client, mfa_token, pyotp.TOTP(secret).now())
    assert r.status_code == 429
    assert "erneut an" in r.json()["detail"]

    # Neu anmelden -> neues Token -> klappt.
    fresh = await _mfa_token(client)
    assert (await _mfa(client, fresh, pyotp.TOTP(secret).now())).status_code == 200


@pytest.mark.asyncio
async def test_wrong_mfa_codes_count_towards_the_login_lock(client, db_session):
    """Sonst liesse sich mit dem richtigen Passwort ueber immer neue Tokens doch
    beliebig oft raten."""
    secret = await _enable_totp(client)
    wrong = _wrong_code(secret)
    per_token = login_limit.MAX_MFA_FAILURES
    for _ in range(login_limit.MAX_FAILURES_PER_USER // per_token):
        mfa_token = await _mfa_token(client)
        for _ in range(per_token):
            await _mfa(client, mfa_token, wrong)

    r = await _login(client)
    assert r.status_code == 429
    assert r.json()["detail"].startswith("Zu viele Fehlversuche.")

    # Von einer Adresse greifen beide Grenzen zugleich: die fuer (Adresse, Name) und die je Konto. Das Protokoll
    # nennt dann die Fenster beider Grenzen, nicht nur die 5 Minuten.
    (locked,) = await _audit(db_session, "login.locked")
    assert locked.detail["scopes"] == ["user", "account"]
    assert locked.detail["window_seconds"] == login_limit.WINDOW_SECONDS
    assert locked.detail["account_window_seconds"] == login_limit.ACCOUNT_WINDOW_SECONDS
    assert locked.detail["day_window_seconds"] == login_limit.ACCOUNT_DAY_SECONDS


# ---------------------------------------------------------------------------
# Code und mfa_token gelten nur einmal; Sperre je Konto
# ---------------------------------------------------------------------------


@pytest.fixture
def totp_clock(monkeypatch):
    """Die TOTP-Uhr des Servers; `now[0]` verschieben = naechster Zeitschritt."""
    from nodvard_deck.services import auth as auth_service

    now = [time.time()]
    monkeypatch.setattr(auth_service, "_totp_now", lambda: now[0])
    return now


@pytest.mark.asyncio
async def test_used_totp_code_is_rejected_for_the_same_and_for_a_new_token(client, totp_clock):
    secret = await _enable_totp(client, totp_clock[0])
    code = pyotp.TOTP(secret).at(totp_clock[0])
    first = await _mfa_token(client)
    assert (await _mfa(client, first, code)).status_code == 200

    # Derselbe Code mit einem neuen Anmeldeschritt: abgewiesen, mit klarem Hinweis.
    second = await _mfa_token(client)
    r = await _mfa(client, second, code)
    assert r.status_code == 401
    assert "schon benutzt" in r.json()["detail"]

    # Der Code des naechsten Zeitschritts geht (mit demselben Token, der Fehlversuch hat es nicht verbraucht).
    totp_clock[0] += 30
    assert (await _mfa(client, second, pyotp.TOTP(secret).at(totp_clock[0]))).status_code == 200


@pytest.mark.asyncio
async def test_older_step_than_the_last_used_is_rejected(client, totp_clock):
    secret = await _enable_totp(client, totp_clock[0])
    newer = pyotp.TOTP(secret).at(totp_clock[0] + 30)
    older = pyotp.TOTP(secret).at(totp_clock[0] - 30)
    assert (await _mfa(client, await _mfa_token(client), newer)).status_code == 200
    assert (await _mfa(client, await _mfa_token(client), older)).status_code == 401


@pytest.mark.asyncio
async def test_mfa_token_works_only_once(client, totp_clock):
    secret = await _enable_totp(client, totp_clock[0])
    token = await _mfa_token(client)
    assert (await _mfa(client, token, pyotp.TOTP(secret).at(totp_clock[0]))).status_code == 200
    totp_clock[0] += 30
    r = await _mfa(client, token, pyotp.TOTP(secret).at(totp_clock[0]))
    assert r.status_code == 401
    assert "erneut an" in r.json()["detail"]


@pytest.mark.asyncio
async def test_used_code_is_logged_as_replay(client, db_session, totp_clock):
    secret = await _enable_totp(client, totp_clock[0])
    code = pyotp.TOTP(secret).at(totp_clock[0])
    assert (await _mfa(client, await _mfa_token(client), code)).status_code == 200
    assert (await _mfa(client, await _mfa_token(client), code)).status_code == 401
    failed = await _audit(db_session, "mfa.failed")
    assert [e.detail for e in failed] == [{"replayed": True}]


@pytest.mark.asyncio
async def test_wrong_codes_from_many_addresses_lock_the_account(client, db_session, clock):
    from nodvard_deck.models import Notification

    secret = await _enable_totp(client)
    wrong = _wrong_code(secret)

    # Zwei fremde Adressen, je ein Token mit fuenf falschen Codes: je Adresse noch lange nicht gesperrt.
    for ip in ("192.168.2.60", "192.168.2.61"):
        async with _other_ip_client(ip) as other:
            token = await _mfa_token(other)
            for _ in range(login_limit.MAX_MFA_FAILURES):
                await _mfa(other, token, wrong)

    # Eine dritte Adresse mit dem richtigen Passwort UND dem richtigen Code: das Konto ist gesperrt.
    async with _other_ip_client("192.168.2.62") as third:
        token = await _mfa_token(third)
        r = await _mfa(third, token, pyotp.TOTP(secret).now())
        assert r.status_code == 429
        assert "Retry-After" in r.headers

    locked = await _audit(db_session, "login.locked")
    assert [e.detail["scopes"] for e in locked] == [["account"]]
    assert locked[0].detail["window_seconds"] == login_limit.ACCOUNT_WINDOW_SECONDS
    assert locked[0].detail["account_window_seconds"] == login_limit.ACCOUNT_WINDOW_SECONDS
    assert locked[0].detail["day_window_seconds"] == login_limit.ACCOUNT_DAY_SECONDS
    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert [n.title for n in notes] == ["Zwei-Faktor-Code wird durchprobiert"]
    # Meldung und Protokoll sagen „Zwei-Faktor“, nicht „2FA“ (wie alle anderen Texte der Anmeldung).
    assert "2FA" not in notes[0].body
    assert "ändere gleich dein Passwort, das geht auch während der Sperre" in notes[0].body
    assert "Anmelden kannst du dich in der Zeit mit einem Wiederherstellungs-Code" in notes[0].body
    assert "Bestätigungen mit Zwei-Faktor-Code" in notes[0].body
    assert "2FA" not in locked[0].reason

    # Nach dem Fenster geht es wieder.
    clock[0] += login_limit.ACCOUNT_WINDOW_SECONDS + 1
    async with _other_ip_client("192.168.2.63") as fourth:
        token = await _mfa_token(fourth)
        assert (await _mfa(fourth, token, pyotp.TOTP(secret).now())).status_code == 200


def test_account_day_limit_holds_beyond_the_short_window(clock):
    first = login_limit.begin("192.168.2.1", "nico", mfa_token="t0", account="u1")
    login_limit.failed(first)
    for i in range(1, login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY):
        clock[0] += login_limit.ACCOUNT_WINDOW_SECONDS  # nie mehr als 1 je Kurzfenster
        login_limit.failed(login_limit.begin(f"192.168.2.{i + 1}", "nico", mfa_token=f"t{i}", account="u1"))
    with pytest.raises(login_limit.Locked) as err:
        login_limit.begin("192.168.2.99", "nico", mfa_token="neu", account="u1")
    assert err.value.retry_after > login_limit.ACCOUNT_WINDOW_SECONDS
    # Ein anderes Konto ist nicht betroffen.
    login_limit.begin("192.168.2.99", "anna", mfa_token="neu2", account="u2")


@pytest.mark.asyncio
async def test_recovery_code_still_works_while_the_account_is_locked(client, db_session):
    """Wer das Passwort kennt, kann das Konto ueber falsche sechsstellige Codes sperren. Der
    Besitzer kommt dann mit einem Wiederherstellungs-Code trotzdem hinein, und falsche
    Wiederherstellungs-Codes zaehlen nicht zur Sperre je Konto."""
    await _bootstrap(client)
    token = (await _login(client)).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=headers)).json()["secret"]
    confirm = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret)}, headers=headers)
    recovery_codes = confirm.json()["recovery_codes"]
    wrong = _wrong_code(secret)

    for ip in ("192.168.2.70", "192.168.2.71"):
        async with _other_ip_client(ip) as other:
            mfa_token = await _mfa_token(other)
            for _ in range(login_limit.MAX_MFA_FAILURES):
                await _mfa(other, mfa_token, wrong)

    async with _other_ip_client("192.168.2.72") as owner:
        mfa_token = await _mfa_token(owner)
        assert (await _mfa(owner, mfa_token, pyotp.TOTP(secret).now())).status_code == 429
        # Falsche Wiederherstellungs-Codes: 401, nicht 429, und sie verlaengern die Sperre nicht.
        assert (await _mfa(owner, mfa_token, "AAAAA-AAAAA")).status_code == 401
        assert (await _mfa(owner, mfa_token, recovery_codes[0])).status_code == 200


def test_used_mfa_tokens_are_swept_after_their_lifetime(clock):
    """Verbrauchte `mfa_token` werden nur aufgehoben, solange ein Token ueberhaupt gelten kann --
    auch wenn sonst kaum Eintraege anfallen (viele erfolgreiche Anmeldungen ueber Monate)."""
    for i in range(login_limit._SWEEP_THRESHOLD + 1):
        attempt = login_limit.begin("192.168.2.5", "nico", mfa_token=f"tok{i}", account="u1")
        assert login_limit.claim_success(attempt)
        login_limit.succeeded(attempt)
    clock[0] += login_limit.MFA_ENTRY_TTL_SECONDS + 1
    login_limit.begin("192.168.2.5", "nico", mfa_token="frisch", account="u1")
    assert len(login_limit._mfa_used) == 0
