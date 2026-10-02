"""Argon2 beim Anmelden: nie im Event-Loop, gleichzeitig nur wenige, und ein deaktiviertes Konto
rechnet genauso wie ein aktives oder unbekanntes (keine Auskunft ueber die Antwortzeit)."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from sqlalchemy import select

from nodvard_deck.core import login_limit, security
from nodvard_deck.models import User

PASSWORD = "correct-horse-battery"


async def _bootstrap(client, username="nico"):
    r = await client.post(
        "/api/v1/auth/bootstrap", json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    assert r.status_code == 201, r.text


async def _login(client, username="nico", password=PASSWORD):
    return await client.post("/api/v1/auth/login", json={"username": username, "password": password})


@pytest.fixture
def traced_verify(monkeypatch):
    """Ersetzt `verify_password` durch eine Hilfe, die aufschreibt, in welchem Thread und mit
    welchem Hash sie lief (rechnet aber echt weiter)."""
    calls: list[tuple[str, bool]] = []
    real = security.verify_password

    def _traced(password, password_hash):
        calls.append((password_hash, threading.current_thread() is threading.main_thread()))
        return real(password, password_hash)

    monkeypatch.setattr(security, "verify_password", _traced)
    return calls


@pytest.mark.asyncio
async def test_login_checks_the_password_outside_the_event_loop(client, traced_verify):
    await _bootstrap(client)
    traced_verify.clear()
    assert (await _login(client)).status_code == 200
    assert (await _login(client, password="falsch")).status_code == 401
    assert (await _login(client, username="gibtsnicht")).status_code == 401
    assert len(traced_verify) == 3
    assert not any(in_main_thread for _, in_main_thread in traced_verify)


@pytest.mark.asyncio
async def test_creating_and_changing_passwords_hash_outside_the_event_loop(client, monkeypatch):
    await _bootstrap(client)
    token = (await _login(client)).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    in_loop: list[str] = []
    real = security.hash_password

    def _traced(password):
        if threading.current_thread() is threading.main_thread():
            in_loop.append(password)
        return real(password)

    monkeypatch.setattr(security, "hash_password", _traced)
    made = await client.post("/api/v1/users", json={"username": "anna", "password": "noch-ein-passwort-1"}, headers=headers)
    assert made.status_code == 201, made.text
    changed = await client.post(
        "/api/v1/me/password", json={"current_password": PASSWORD, "new_password": "neues-passwort-77"}, headers=headers
    )
    assert changed.status_code == 204, changed.text
    anna = made.json()["id"]
    set_other = await client.patch(f"/api/v1/users/{anna}", json={"password": "wieder-was-neues-5"}, headers=headers)
    assert set_other.status_code == 200, set_other.text
    assert in_loop == []


@pytest.mark.asyncio
async def test_event_loop_stays_responsive_while_passwords_are_checked(monkeypatch):
    """Waehrend mehrere Pruefungen laufen, kommt der Loop weiter zum Zug (ein Zaehler-Task tickt).
    Mit einer Pruefung direkt im Loop stuende er so lange still."""
    monkeypatch.setattr(security, "verify_password", lambda *_: time.sleep(0.15) or False)
    ticks = 0
    stop = False

    async def _ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(_ticker())
    await asyncio.gather(*(security.verify_password_async("x", "h") for _ in range(4)))
    stop = True
    await task
    # 4 Pruefungen zu je 0,15 s, zwei gleichzeitig: mindestens 0,3 s. Ein blockierter Loop kaeme
    # hoechstens auf ein, zwei Ticks.
    assert ticks >= 10


@pytest.mark.asyncio
async def test_only_a_few_hashes_run_at_the_same_time(monkeypatch):
    running = 0
    peak = 0
    lock = threading.Lock()

    def _slow(*_):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return False

    monkeypatch.setattr(security, "verify_password", _slow)
    await asyncio.gather(*(security.verify_password_async("x", "h") for _ in range(security.MAX_WAITING_HASHES)))
    assert peak == security.MAX_PARALLEL_HASHES


@pytest.mark.asyncio
async def test_too_many_waiting_hashes_are_turned_away(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(security, "verify_password", lambda *_: release.wait(5) and False)
    total = security.MAX_PARALLEL_HASHES + security.MAX_WAITING_HASHES
    tasks = [asyncio.create_task(security.verify_password_async("x", "h")) for _ in range(total)]
    await asyncio.sleep(0.05)
    with pytest.raises(security.HashingBusy):
        await security.verify_password_async("x", "h")
    release.set()
    await asyncio.gather(*tasks)
    # Danach ist wieder Platz.
    assert await security.verify_password_async("x", "h") is False


@pytest.mark.asyncio
async def test_busy_hashing_answers_503_and_does_not_count_as_a_failed_attempt(client, monkeypatch):
    await _bootstrap(client)

    async def _busy(*_, **__):
        raise security.HashingBusy

    monkeypatch.setattr(security, "verify_password_async", _busy)
    for _ in range(login_limit.MAX_FAILURES_PER_USER + 2):
        r = await _login(client, password="falsch")
        assert r.status_code == 503
        assert r.headers["Retry-After"]
    monkeypatch.undo()
    # Keine Sperre durch die abgewiesenen Versuche.
    assert (await _login(client)).status_code == 200


@pytest.mark.asyncio
async def test_locked_attempts_do_not_hash_at_all(client, monkeypatch):
    await _bootstrap(client)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        assert (await _login(client, password="falsch")).status_code == 401
    calls = []

    async def _count(*args, **_):
        calls.append(args)
        return False

    monkeypatch.setattr(security, "verify_password_async", _count)
    assert (await _login(client)).status_code == 429
    assert calls == []


@pytest.mark.asyncio
async def test_inactive_unknown_and_wrong_accounts_take_the_same_way(client, db_session, traced_verify):
    """Deaktiviert, unbekannt und falsches Passwort: gleiche Antwort, und jedes Mal laeuft genau
    eine Argon2-Pruefung. Unbekannte Namen und deaktivierte Konten gegen den Dummy-Hash (der echte
    Hash eines deaktivierten Kontos wird nicht angefasst), ein aktives Konto gegen seinen echten."""
    await _bootstrap(client)
    created = await client.post(
        "/api/v1/users",
        json={"username": "weg", "password": PASSWORD},
        headers={"Authorization": f"Bearer {(await _login(client)).json()['access_token']}"},
    )
    assert created.status_code == 201
    weg = (await db_session.execute(select(User).where(User.username == "weg"))).scalar_one()
    weg.is_active = False
    await db_session.flush()

    from nodvard_deck.services import auth as auth_service

    answers = []
    hashes = []
    for name, password in (("weg", PASSWORD), ("weg", "falsch"), ("gibtsnicht", PASSWORD), ("nico", "falsch")):
        traced_verify.clear()
        r = await _login(client, username=name, password=password)
        answers.append((r.status_code, r.json()))
        hashes.append([h for h, _ in traced_verify])
    assert len({repr(a) for a in answers}) == 1
    assert answers[0][0] == 401
    assert all(len(h) == 1 for h in hashes)
    assert hashes[0] == hashes[1] == hashes[2] == [auth_service._DUMMY_PASSWORD_HASH]
    nico = (await db_session.execute(select(User).where(User.username == "nico"))).scalar_one()
    assert hashes[3] == [nico.password_hash]


@pytest.mark.asyncio
async def test_inactive_account_with_right_password_is_still_refused(client, db_session):
    await _bootstrap(client)
    token = (await _login(client)).json()["access_token"]
    await client.post(
        "/api/v1/users", json={"username": "weg", "password": PASSWORD}, headers={"Authorization": f"Bearer {token}"}
    )
    weg = (await db_session.execute(select(User).where(User.username == "weg"))).scalar_one()
    weg.is_active = False
    await db_session.flush()
    r = await _login(client, username="weg")
    assert r.status_code == 401
    assert r.json()["detail"] == "Ungültiger Benutzername oder Passwort."


@pytest.mark.asyncio
async def test_signed_in_work_waits_instead_of_being_turned_away(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(security, "verify_password", lambda *_: release.wait(5) and False)
    total = security.MAX_PARALLEL_HASHES + security.MAX_WAITING_HASHES
    tasks = [asyncio.create_task(security.verify_password_async("x", "h")) for _ in range(total)]
    await asyncio.sleep(0.05)
    signed_in = asyncio.create_task(security.verify_password_async("x", "h", signed_in=True))
    await asyncio.sleep(0.05)
    assert not signed_in.done()
    release.set()
    assert await signed_in is False
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_a_login_flood_does_not_lock_signed_in_users_out_of_their_settings(client):
    """Stauen sich Anmeldeversuche von aussen, bekommt der Anmelde-Weg 503. Wer schon angemeldet
    ist, kann trotzdem sein Passwort aendern: die Abfrage des alten und das Setzen des neuen warten
    kurz, statt abgewiesen zu werden."""
    await _bootstrap(client)
    headers = {"Authorization": f"Bearer {(await _login(client)).json()['access_token']}"}
    release = threading.Event()
    total = security.MAX_PARALLEL_HASHES + security.MAX_WAITING_HASHES
    flood = [asyncio.create_task(security.run_hashing(release.wait, 5)) for _ in range(total)]
    await asyncio.sleep(0.05)
    try:
        assert (await _login(client)).status_code == 503
        change = asyncio.create_task(
            client.post(
                "/api/v1/me/password",
                json={"current_password": PASSWORD, "new_password": "neues-passwort-77"},
                headers=headers,
            )
        )
        await asyncio.sleep(0.1)
        assert not change.done()
    finally:
        release.set()
        await asyncio.gather(*flood)
    r = await change
    assert r.status_code == 204, r.text
