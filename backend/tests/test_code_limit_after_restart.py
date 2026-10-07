"""Die Grenzen fuer falsche Zwei-Faktor-Codes stehen nur im Speicher (`core.login_limit`). Beim Start traegt
`services.auth.restore_code_limits` die Fehlversuche der letzten 24 Stunden aus dem Protokoll wieder ein. Sonst koennte,
wer Sitzung und Passwort hat, im Wechsel raten und Nodvard Deck neu starten (`POST /system/restart`), und die Grenze je
Konto finge jedes Mal von vorn an.

Ein Neustart wird hier wie beim Start nachgestellt: Speicher leeren (`login_limit.reset()`), dann
`restore_code_limits` gegen dieselbe Datenbank."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import pyotp
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from nodvard_deck import config
from nodvard_deck.core import bootstate, login_limit
from nodvard_deck.models import AuditEntry, Notification, User
from nodvard_deck.services import auth as auth_service
from sqlalchemy import select
from totp_helpers import setup_confirm_code

PASSWORD = "correct-horse-battery"
LIMIT = login_limit.MAX_MFA_FAILURES_PER_ACCOUNT


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def clock(monkeypatch):
    """Feste Uhr fuer die Zwei-Faktor-Pruefung (`auth_service._totp_now`)."""
    now = [time.time()]
    monkeypatch.setattr(auth_service, "_totp_now", lambda: now[0])
    return now


def _code(secret: str, clock) -> str:
    return pyotp.TOTP(secret).at(clock[0])


def _wrong_codes(secret: str, clock, n: int) -> list[str]:
    totp = pyotp.TOTP(secret)
    valid = {totp.at(clock[0] + d) for d in (-30, 0, 30)}
    return [c for c in (f"{i:06d}" for i in range(n + 3)) if c not in valid][:n]


def _from(ip: str) -> AsyncClient:
    from nodvard_deck.main import app

    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 4711), raise_app_exceptions=False), base_url="http://test")


async def _owner(client, clock) -> tuple[str, str, list[str]]:
    r = await client.post(
        "/api/v1/auth/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    assert r.status_code == 201, r.text
    token = (await client.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})).json()["access_token"]
    secret = (await client.post("/api/v1/me/totp/setup", json={"current_password": PASSWORD}, headers=_h(token))).json()[
        "secret"
    ]
    r = await client.post("/api/v1/me/totp/confirm", json={"code": setup_confirm_code(secret, clock[0])}, headers=_h(token))
    assert r.status_code == 200, r.text
    return token, secret, r.json()["recovery_codes"]


async def _mfa_attempts(ip: str, codes: list[str]) -> list[int]:
    """Neu anmelden (Passwort) und die Codes mit diesem Anmeldeschritt eingeben, von der Adresse `ip`."""
    async with _from(ip) as other:
        r = await other.post("/api/v1/auth/login", json={"username": "nico", "password": PASSWORD})
        assert r.status_code == 202, r.text
        mfa_token = r.json()["mfa_token"]
        return [
            (await other.post("/api/v1/auth/mfa", json={"mfa_token": mfa_token, "code": code})).status_code
            for code in codes
        ]


async def _disable(client, token, code: str):
    return await client.request(
        "DELETE", "/api/v1/me/totp", json={"current_password": PASSWORD, "totp_code": code}, headers=_h(token)
    )


async def _restart(db_session) -> int:
    """Wie ein Neustart: alles im Speicher weg, dann das Vorbefuellen beim Start."""
    login_limit.reset()
    db_session.expire_all()
    return await auth_service.restore_code_limits(db_session)


async def _user_id(db_session) -> str:
    return (await db_session.execute(select(User.id).where(User.username == "nico"))).scalar_one()


async def _audit(db_session, action: str) -> list[AuditEntry]:
    db_session.expire_all()
    return list((await db_session.execute(select(AuditEntry).where(AuditEntry.action == action))).scalars().all())


async def _write_failures(db_session, user_id: str, ages: list[float], *, action="mfa.failed", detail=None, now=None):
    """Fehlversuche direkt ins Protokoll, `ages` Sekunden vor `now` (negativ: in der Zukunft)."""
    now = time.time() if now is None else now
    for age in ages:
        db_session.add(
            AuditEntry(
                ts=datetime.fromtimestamp(now - age, UTC), actor_type="user", actor_id=user_id, action=action,
                outcome="failure", target_type="user", target_id=user_id, detail=dict(detail or {}),
            )
        )
    await db_session.commit()


# ---------------------------------------------------------------------------
# Der Angriff: raten, neu starten, weiter raten
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_codes_at_sign_in_still_count_after_a_restart(client, db_session, clock):
    _token, secret, _codes = await _owner(client, clock)
    wrong = _wrong_codes(secret, clock, LIMIT)
    # Neun falsche Codes von drei Adressen (je Adresse und je Anmeldeschritt noch nicht gesperrt).
    assert await _mfa_attempts("192.168.2.60", wrong[:4]) == [401] * 4
    assert await _mfa_attempts("192.168.2.61", wrong[4:8]) == [401] * 4
    assert await _mfa_attempts("192.168.2.66", wrong[8:9]) == [401]

    assert await _restart(db_session) == LIMIT - 1

    # Der zehnte falsche Code: die Sperre beginnt, mit genau einem Eintrag und einer Meldung wie ohne Neustart.
    assert await _mfa_attempts("192.168.2.62", [wrong[9]]) == [401]
    (locked,) = await _audit(db_session, "login.locked")
    assert locked.detail["scopes"] == ["account"]
    notes = (await db_session.execute(select(Notification))).scalars().all()
    assert [n.title for n in notes] == ["Zwei-Faktor-Code wird durchprobiert"]
    # Gesperrt, auch fuer den richtigen Code und von einer neuen Adresse.
    assert await _mfa_attempts("192.168.2.63", [_code(secret, clock)]) == [429]


@pytest.mark.asyncio
async def test_guessing_with_a_session_cannot_go_on_after_a_restart(client, db_session, clock):
    """Wer Sitzung und Passwort hat, raet ueber Bestaetigungen (hier: Zwei-Faktor abschalten) und startet neu."""
    token, secret, _codes = await _owner(client, clock)
    wrong = _wrong_codes(secret, clock, LIMIT)
    for code in wrong[: LIMIT - 1]:
        assert (await _disable(client, token, code)).status_code == 400

    await _restart(db_session)

    assert (await _disable(client, token, wrong[-1])).status_code == 400
    blocked = await _disable(client, token, _code(secret, clock))
    assert blocked.status_code == 429, blocked.text
    assert "Retry-After" in blocked.headers
    # Anmeldung und Bestaetigung teilen sich die Grenze, auch nach dem Neustart.
    assert await _mfa_attempts("192.168.2.64", [_code(secret, clock)]) == [429]
    assert (await client.get("/api/v1/me", headers=_h(token))).json()["totp_enabled"] is True


@pytest.mark.asyncio
async def test_sign_in_and_confirmations_count_together_after_a_restart(client, db_session, clock):
    token, secret, _codes = await _owner(client, clock)
    wrong = _wrong_codes(secret, clock, LIMIT)
    assert await _mfa_attempts("192.168.2.65", wrong[:4]) == [401] * 4
    for code in wrong[4:9]:
        assert (await _disable(client, token, code)).status_code == 400

    await _restart(db_session)

    r = await client.post(
        "/api/v1/me/recovery-codes", json={"current_password": PASSWORD, "totp_code": wrong[9]}, headers=_h(token)
    )
    assert r.status_code == 400, r.text
    assert (await _disable(client, token, _code(secret, clock))).status_code == 429


@pytest.mark.asyncio
async def test_every_way_with_a_code_is_restored_exactly_as_it_was_counted(client, db_session, clock):
    """Jeder Weg, auf dem ein falscher Code auf die Grenze je Konto zaehlt, steht genau einmal im Protokoll: Nach dem
    Neustart ist der Stand derselbe wie davor, nicht mehr und nicht weniger."""
    token, secret, _codes = await _owner(client, clock)
    user_id = await _user_id(db_session)
    wrong = iter(_wrong_codes(secret, clock, LIMIT))
    confirm = {"current_password": PASSWORD}
    ways = [
        ("DELETE", "/api/v1/me/totp", {}),
        ("POST", "/api/v1/me/recovery-codes", {}),
        ("POST", "/api/v1/system/backups/download", {"mode": "schluessel"}),
        ("POST", "/api/v1/system/backups/sicherung.nvdbak/ticket", {}),
        ("POST", "/api/v1/system/restore/abc/schedule", {}),
        ("POST", "/api/v1/system/updates/apply", {"version": "9.9.9"}),
        ("POST", "/api/v1/system/updates/rollback", {}),
    ]
    for method, path, extra in ways:
        r = await client.request(method, path, json={**confirm, **extra, "totp_code": next(wrong)}, headers=_h(token))
        assert r.status_code == 400, (path, r.text)
    assert await _mfa_attempts("192.168.2.67", [next(wrong)]) == [401]
    before = list(login_limit._by_account[user_id])
    assert len(before) == len(ways) + 1

    assert await _restart(db_session) == len(before)
    assert len(login_limit._by_account[user_id]) == len(before)


@pytest.mark.asyncio
async def test_without_failures_a_restart_changes_nothing(client, db_session, clock):
    token, secret, _codes = await _owner(client, clock)
    assert await _restart(db_session) == 0
    assert (await _disable(client, token, _code(secret, clock))).status_code == 204


# ---------------------------------------------------------------------------
# Was zaehlt und was nicht
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failures_older_than_a_day_do_not_count(client, db_session, clock):
    token, secret, _codes = await _owner(client, clock)
    user_id = await _user_id(db_session)
    day = login_limit.ACCOUNT_DAY_SECONDS
    # Viel mehr als die Tagesgrenze, aber alle aelter als 24 Stunden.
    await _write_failures(db_session, user_id, [day + 60 + i * 60 for i in range(3 * login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY)])
    assert await _restart(db_session) == 0
    assert (await _disable(client, token, _code(secret, clock))).status_code == 204


@pytest.mark.asyncio
async def test_failures_within_the_day_lock_until_the_oldest_is_a_day_old(client, db_session, clock):
    token, secret, _codes = await _owner(client, clock)
    user_id = await _user_id(db_session)
    day_limit = login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY
    # Tagesgrenze erreicht, verteilt ueber die letzten 23 Stunden (nie 10 in 15 Minuten).
    hours = 3600
    await _write_failures(db_session, user_id, [23 * hours - i * hours for i in range(day_limit)])

    assert await _restart(db_session) == day_limit

    r = await _disable(client, token, _code(secret, clock))
    assert r.status_code == 429, r.text
    # Die Wartezeit rechnet mit dem Alter aus dem Protokoll: der aelteste Fehlversuch faellt in einer Stunde heraus.
    assert abs(int(r.headers["Retry-After"]) - hours) <= 120


@pytest.mark.asyncio
async def test_only_as_many_failures_as_the_limits_need_are_loaded(client, db_session, clock):
    await _owner(client, clock)
    user_id = await _user_id(db_session)
    other = "00000000-0000-0000-0000-00000000beef"
    await _write_failures(db_session, user_id, [60 + i for i in range(500)])
    await _write_failures(db_session, other, [60 + i for i in range(30)])

    assert await _restart(db_session) == 2 * login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY
    assert len(login_limit._by_account[user_id]) == login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY
    assert len(login_limit._by_account[other]) == login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY


@pytest.mark.asyncio
async def test_the_query_reads_only_the_window_and_only_the_newest_per_account(client, db_session, clock):
    """Schon die Abfrage begrenzt: nur das Fenster, je Konto nur so viele, wie die Grenze braucht. So haelt auch ein
    sehr volles Protokoll den Start nicht auf."""
    await _owner(client, clock)
    user_id = await _user_id(db_session)
    other = "00000000-0000-0000-0000-00000000beef"
    now = time.time()
    day = login_limit.ACCOUNT_DAY_SECONDS
    await _write_failures(db_session, user_id, [60 + i for i in range(50)] + [day + 60 + i for i in range(50)], now=now)
    await _write_failures(db_session, other, [day + 120], now=now)
    found = await auth_service._recent_code_failures(
        db_session, actions=auth_service.CODE_FAILURE_ACTIONS, recovery=False, window=day, per_account=20, now_wall=now,
    )
    assert set(found) == {user_id}
    assert len(found[user_id]) == 20
    # Die juengsten 20: 60 bis 79 Sekunden alt.
    assert max(now - t for t in found[user_id]) == pytest.approx(79, abs=1)


@pytest.mark.asyncio
async def test_recovery_codes_do_not_count_on_the_account_limit_after_a_restart(client, db_session, clock):
    """Wie im Speicher: falsche Wiederherstellungs-Codes zaehlen nicht auf die Grenze je Konto, sonst kaeme der Besitzer
    nach einem Neustart in eine Sperre, die vorher nicht bestand."""
    token, secret, _codes = await _owner(client, clock)
    user_id = await _user_id(db_session)
    # Anmelden mit falschen Wiederherstellungs-Codes ...
    assert await _mfa_attempts("192.168.2.70", ["AAAAA-AAAAA", "BBBBB-BBBBB"]) == [401, 401]
    # Und von gestern, mehr als die Tagesgrenze.
    await _write_failures(
        db_session, user_id, [3600 + i for i in range(2 * login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY)],
        detail={"via": "recovery_code"},
    )

    assert await _restart(db_session) == 0
    assert user_id not in login_limit._by_account
    assert (await _disable(client, token, _code(secret, clock))).status_code == 204


@pytest.mark.asyncio
async def test_replayed_codes_count_after_a_restart_like_before(client, db_session, clock):
    """Ein schon benutzter Code zaehlt als Fehlversuch (`replayed`), im Speicher wie nach dem Neustart."""
    _token, _secret, _codes = await _owner(client, clock)
    user_id = await _user_id(db_session)
    await _write_failures(db_session, user_id, [30, 20], detail={"replayed": True})
    await _write_failures(db_session, user_id, [10], action="auth.totp_check_failed", detail={"aktion": "2fa_abschalten"})
    # Andere Eintraege zaehlen nicht: andere Aktionen, ein Erfolg, ein falsches Passwort.
    await _write_failures(db_session, user_id, [5], action="auth.password_check_failed", detail={"aktion": "neustart"})
    await _write_failures(db_session, user_id, [5], action="auth.totp_confirm_failed")
    db_session.add(
        AuditEntry(actor_type="user", actor_id=user_id, action="mfa.failed", outcome="success", target_type="user",
                   target_id=user_id, detail={})
    )
    await db_session.commit()

    assert await _restart(db_session) == 3
    assert len(login_limit._by_account[user_id]) == 3


@pytest.mark.asyncio
async def test_wrong_recovery_codes_for_confirmations_stay_counted_after_a_restart(client, db_session, clock, monkeypatch):
    """Der eigene Zaehler fuer falsche Wiederherstellungs-Codes bei Bestaetigungen (10 in 5 Minuten je Konto) haengt am
    Konto und liesse sich ebenso mit Neustarts umgehen; er wird mit denselben Eintraegen (`via: recovery_code`)
    wieder eingetragen. Die Grenze ist hier klein, damit der Test schnell bleibt."""
    monkeypatch.setattr(login_limit, "MAX_FAILURES_PER_USER", 3)
    token, _secret, codes = await _owner(client, clock)
    limit = login_limit.MAX_FAILURES_PER_USER
    for i in range(limit - 1):
        assert (await _disable(client, token, f"AAAAA-AAA{i:02d}")).status_code == 400

    assert await _restart(db_session) == limit - 1

    assert (await _disable(client, token, "BBBBB-BBBBB")).status_code == 400
    blocked = await _disable(client, token, codes[0])
    assert blocked.status_code == 429, blocked.text
    # Der Code ist nicht verbraucht: Nach dem Fenster geht er (hier: nach einem weiteren Neustart mit spaeterer Uhr).
    login_limit.reset()
    db_session.expire_all()
    assert await auth_service.restore_code_limits(db_session, now_wall=time.time() + login_limit.WINDOW_SECONDS + 1) == 0
    assert (await _disable(client, token, codes[0])).status_code == 204


# ---------------------------------------------------------------------------
# Uhr: Wanduhr des Protokolls -> Uhr des Prozesses
# ---------------------------------------------------------------------------


def test_age_from_the_log_becomes_the_same_age_in_the_process(monkeypatch):
    monkeypatch.setattr(login_limit, "_now", lambda: 500.0)
    wall = 1_800_000_000.0
    assert login_limit.restore_account_failures("u1", [wall - 600, wall - 60], now_wall=wall) == 2
    assert list(login_limit._by_account["u1"]) == [500.0 - 600, 500.0 - 60]


def test_only_failures_within_the_window_and_only_the_newest_are_kept(monkeypatch):
    monkeypatch.setattr(login_limit, "_now", lambda: 500.0)
    wall = 1_800_000_000.0
    day = login_limit.ACCOUNT_DAY_SECONDS
    day_limit = login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY
    times = [wall - 60 * i for i in range(1, 2 * day_limit + 1)] + [wall - day, wall - day - 3600]
    assert login_limit.restore_account_failures("u1", times, now_wall=wall) == day_limit
    assert list(login_limit._by_account["u1"]) == [500.0 - 60 * i for i in range(day_limit, 0, -1)]
    # Der Zaehler je (Adresse, Name) hat ein Fenster von 5 Minuten und braucht hoechstens 10.
    window = login_limit.WINDOW_SECONDS
    assert login_limit.restore_failures("konto:u1", "x", [wall - 10 * i for i in range(1, 40)] + [wall - window], now_wall=wall) == (
        login_limit.MAX_FAILURES_PER_USER
    )
    assert login_limit._by_user[("konto:u1", "x")][0] == 500.0 - 10 * login_limit.MAX_FAILURES_PER_USER
    # Weniger als die Grenze: Was aelter als das Fenster ist, faellt trotzdem heraus.
    assert login_limit.restore_account_failures("u2", [wall - 60, wall - day, wall - day - 1], now_wall=wall) == 1
    assert login_limit.restore_failures("konto:u2", "x", [wall - 10, wall - window], now_wall=wall) == 1


def test_timestamps_from_the_future_count_as_now_but_not_beyond_a_day(monkeypatch):
    """Ging die Uhr beim Schreiben vor (oder beim Start nach, etwa ohne Uhrenbaustein vor dem Zeitabgleich), zaehlt ein
    Fehlversuch aus der "Zukunft" als gerade eben. Liegt er mehr als einen Tag voraus, passt er zu keiner brauchbaren
    Uhr und zaehlt nicht -- sonst sperrte eine falsch gestellte Uhr jedes Konto einen Tag lang."""
    monkeypatch.setattr(login_limit, "_now", lambda: 500.0)
    wall = 1_800_000_000.0
    day = login_limit.ACCOUNT_DAY_SECONDS
    restored = login_limit.restore_account_failures("u1", [wall + 7200, wall + day + 60, wall + 100 * day], now_wall=wall)
    assert restored == 1
    assert list(login_limit._by_account["u1"]) == [500.0]


def test_restored_failures_lock_like_counted_ones(monkeypatch):
    now = [500.0]
    monkeypatch.setattr(login_limit, "_now", lambda: now[0])
    wall = 1_800_000_000.0
    login_limit.restore_account_failures("u1", [wall - 60 * i for i in range(LIMIT)], now_wall=wall)
    with pytest.raises(login_limit.Locked) as err:
        login_limit.begin("192.168.2.1", "nico", mfa_token="t", account="u1")
    # Der aelteste der zehn ist 9 Minuten alt: noch 6 Minuten bis zum Ende des 15-Minuten-Fensters.
    assert abs(err.value.retry_after - (login_limit.ACCOUNT_WINDOW_SECONDS - 9 * 60)) <= 1
    now[0] += login_limit.ACCOUNT_WINDOW_SECONDS
    login_limit.begin("192.168.2.1", "nico", mfa_token="t2", account="u1")


# ---------------------------------------------------------------------------
# Beim echten Start (`main.lifespan`)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def started_app(tmp_path, monkeypatch):
    """Startet den echten Lifespan gegen eine Datei-Datenbank; liefert (Starter, Sessionmaker)."""
    from nodvard_deck.core.events import reset_event_bus
    from nodvard_deck.core.metrics_history import reset_metrics_collector
    from nodvard_deck.core.scheduler import reset_scheduler_service
    from nodvard_deck.db.session import (
        create_engine_for,
        reset_engine_cache,
        set_engine_for_testing,
    )
    from nodvard_deck.main import app, lifespan
    from nodvard_deck.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker

    settings = config.Settings(
        env="dev", data_dir=tmp_path, database_url=f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}",
        master_key_path=tmp_path / "master.key", vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key", extensions_dir=tmp_path / "extensions", ext_data_dir=tmp_path / "ext-data",
        metrics_interval_s=0,
    )
    monkeypatch.setattr(config, "_settings", settings)
    engine = create_engine_for(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    set_engine_for_testing(engine)
    original_routes = list(app.router.routes)
    reset_metrics_collector()
    try:
        yield (lambda: lifespan(app)), async_sessionmaker(engine, expire_on_commit=False), tmp_path
    finally:
        app.router.routes[:] = original_routes
        await engine.dispose()
        reset_engine_cache()
        reset_scheduler_service()
        reset_metrics_collector()
        reset_event_bus()


@pytest.mark.asyncio
async def test_the_start_loads_the_failures_before_the_first_request(started_app):
    make, sessions, _data = started_app
    user_id = "00000000-0000-0000-0000-0000000000aa"
    async with sessions() as session:
        await _write_failures(session, user_id, [60 * i for i in range(1, LIMIT + 1)])
    login_limit.reset()
    async with make():
        with pytest.raises(login_limit.Locked):
            login_limit.begin("192.168.2.1", "nico", mfa_token="t", account=user_id)


@pytest.mark.asyncio
async def test_a_failure_while_reading_the_log_does_not_stop_the_start(started_app, monkeypatch, caplog):
    from nodvard_deck import main

    make, _sessions, data = started_app

    async def _broken(_session):
        raise RuntimeError("Protokoll kaputt")

    monkeypatch.setattr(main, "restore_code_limits", _broken)
    bootstate.write_state(data, {"app_version": "0.6.0", "started_ok": False})
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.boot"):
        async with make():
            assert bootstate.read_state(data)["started_ok"] is True
    (warning,) = [r for r in caplog.records if r.getMessage().startswith("code_limits_restore_failed")]
    assert warning.levelno == logging.WARNING
    assert "RuntimeError" in warning.getMessage() and "Protokoll kaputt" in warning.getMessage()
    # Eine Zeile ohne Traceback: Die Pruefung nach einem Deploy (scripts/deploy_pi.sh) wertet jeden Traceback in den
    # Logs als Fehler und schaltet auf die alte Version zurueck -- genau das soll dieser Fehler nicht ausloesen.
    assert warning.exc_info is None
    assert "Traceback" not in caplog.text
    # Leere Zaehler, wie frueher nach jedem Neustart.
    assert login_limit._by_account == {}


@pytest.mark.asyncio
async def test_a_long_error_while_reading_the_log_stays_one_short_line(started_app, monkeypatch, caplog):
    from nodvard_deck import main

    make, _sessions, _data = started_app

    async def _broken(_session):
        raise RuntimeError("erste Zeile\n[SQL: SELECT ...]\n" + "x" * 2000)

    monkeypatch.setattr(main, "restore_code_limits", _broken)
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.boot"):
        async with make():
            pass
    (warning,) = [r for r in caplog.records if r.getMessage().startswith("code_limits_restore_failed")]
    assert "\n" not in warning.getMessage()
    assert "erste Zeile" in warning.getMessage()
    assert len(warning.getMessage()) < 400
