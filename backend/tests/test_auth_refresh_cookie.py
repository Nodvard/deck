"""Refresh-Cookie in der Uebergangszeit der Umbenennung (Teil B, PR 4).

Neu heisst das Cookie `nodvard_deck_refresh`, alt `lattice_refresh`. Solange ein Rollback aufs
alte Image moeglich sein soll, gilt:

* **Schreiben:** jeder neue Refresh-Token steht in BEIDEN Cookies, mit gleichem Wert und gleichen
  Attributen (sonst waere man nach einem Rollback abgemeldet).
* **Lesen:** erst `nodvard_deck_refresh`, dann `lattice_refresh`, doppelte Werte entfallen; der
  erste Erfolg gilt. Ein fehlgeschlagener Versuch schreibt nie etwas (kein Widerruf einer Kette),
  deshalb kann ein veralteter Wert in einem der beiden Cookies niemanden abmelden.
* **Gnadenfrist:** kein Set-Cookie und kein Loeschen.
* **Abmelden:** beide Tokens widerrufen, beide Cookies geloescht.

Die Anfragen setzen den `Cookie`-Kopf selbst und leeren vorher den Cookie-Speicher von httpx: so
steht genau das im Test, was ein Browser nach dem jeweiligen Ablauf haette (der Browser ist nach
einem Rollback und der Rueckkehr in einem Zustand, den httpx von sich aus nie herstellen wuerde).
"""

from __future__ import annotations

from datetime import timedelta

import pyotp
import pytest
from nodvard_deck.core import security
from nodvard_deck.db import new_id, utcnow
from nodvard_deck.models import RefreshToken, User
from nodvard_deck.services import auth as auth_service
from sqlalchemy import select

NEW = "nodvard_deck_refresh"
OLD = "lattice_refresh"
PASSWORD = "correct-horse-battery"
API = "/api/v1/auth"
GENERIC_401 = "Refresh-Token ungültig, widerrufen oder abgelaufen."


# --------------------------------------------------------------------------- Hilfen


async def _bootstrap(client, username="nico"):
    r = await client.post(
        f"{API}/bootstrap",
        json={"username": username, "password": PASSWORD, "setup_code": "TEST-CODE-2345"},
    )
    assert r.status_code == 201, r.text


def _parse(line: str) -> tuple[str, str, dict]:
    parts = [p.strip() for p in line.split(";")]
    name, _, value = parts[0].partition("=")
    attrs: dict = {}
    for part in parts[1:]:
        key, _, val = part.partition("=")
        attrs[key.strip().lower()] = val.strip().lower() or True
    return name, value, attrs


def _set_cookies(response) -> dict[str, tuple[str, dict]]:
    """Alle `Set-Cookie`-Zeilen einer Antwort: Name -> (Wert, Attribute)."""
    found: dict[str, tuple[str, dict]] = {}
    for line in response.headers.get_list("set-cookie"):
        name, value, attrs = _parse(line)
        assert name not in found, f"{name} kommt in der Antwort doppelt vor"
        found[name] = (value, attrs)
    return found


def _same_value(response) -> str:
    """Beide Cookies gesetzt, gleicher Wert, gleiche Attribute -> der Wert."""
    cookies = _set_cookies(response)
    assert set(cookies) == {NEW, OLD}, list(cookies)
    assert cookies[NEW][0] == cookies[OLD][0] != ""
    assert cookies[NEW][1] == cookies[OLD][1]
    return cookies[NEW][0]


def _header(new: str | None, old: str | None) -> dict:
    pairs = [f"{name}={value}" for name, value in ((NEW, new), (OLD, old)) if value is not None]
    return {"Cookie": "; ".join(pairs)} if pairs else {}


async def _post(client, path: str, *, new=None, old=None, json=None, base="http://test"):
    client.cookies.clear()
    return await client.post(f"{base}{API}{path}", json={} if json is None else json, headers=_header(new, old))


async def _login(client, base="http://test", username="nico"):
    r = await _post(client, "/login", json={"username": username, "password": PASSWORD}, base=base)
    assert r.status_code == 200, r.text
    return _same_value(r), r


async def _refresh(client, new=None, old=None, base="http://test", json=None):
    return await _post(client, "/refresh", new=new, old=old, json=json, base=base)


async def _logout(client, new=None, old=None, base="http://test", json=None):
    return await _post(client, "/logout", new=new, old=old, json=json, base=base)


def _row_query(raw: str):
    return (
        select(RefreshToken)
        .where(RefreshToken.token_hash == security.hash_opaque_token(raw))
        .execution_options(populate_existing=True)
    )


async def _row(db_session, raw: str) -> RefreshToken:
    return (await db_session.execute(_row_query(raw))).scalar_one()


async def _snapshot(db_session) -> dict[str, tuple]:
    """id -> (revoked_at, replaced_by_id) aller Refresh-Token-Zeilen, frisch aus der Datenbank."""
    rows = (
        await db_session.execute(select(RefreshToken).execution_options(populate_existing=True))
    ).scalars().all()
    return {row.id: (row.revoked_at, row.replaced_by_id) for row in rows}


def _live(snapshot: dict[str, tuple]) -> set[str]:
    return {row_id for row_id, (revoked_at, _) in snapshot.items() if revoked_at is None}


def _changed(before: dict, after: dict) -> set[str]:
    return {row_id for row_id in before if after.get(row_id) != before[row_id]}


async def _age_revocations(db_session, seconds: int = 31):
    """Schiebt alle Widerrufszeitpunkte hinter die Gnadenfrist (statt echt zu warten)."""
    rows = (
        await db_session.execute(select(RefreshToken).execution_options(populate_existing=True))
    ).scalars().all()
    for row in rows:
        if row.revoked_at is not None:
            row.revoked_at = row.revoked_at - timedelta(seconds=seconds)
    await db_session.flush()


async def _old_image_refresh(client, old: str) -> str:
    """Das alte Image nach einem Rollback: liest nur `lattice_refresh`, rotiert und schreibt
    nur diesen Namen. Der Wert des neuen Namens bleibt im Browser, wie er war."""
    r = await _refresh(client, old=old)
    assert r.status_code == 200, r.text
    return _set_cookies(r)[OLD][0]


# --------------------------------------------------------------------------- Schreiben


@pytest.mark.asyncio
@pytest.mark.parametrize("base,secure", [("http://test", False), ("https://test", True)])
async def test_login_writes_both_cookies_with_identical_value_and_attributes(client, base, secure):
    await _bootstrap(client)
    value, response = await _login(client, base)

    assert list(_set_cookies(response)) == [NEW, OLD], "neuer Name zuerst"
    attrs = _set_cookies(response)[NEW][1]
    assert attrs["httponly"] is True
    assert attrs["samesite"] == "strict"
    assert attrs["path"] == "/api/v1/auth"
    assert 2_591_000 < int(attrs["max-age"]) <= 30 * 86400
    assert ("secure" in attrs) is secure
    assert "refresh_token" not in response.json(), "Web bekommt den Token nie im Body"
    assert value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base,setting,expected",
    [
        ("http://test", None, False),
        ("https://test", None, True),
        ("http://test", True, True),
        ("https://test", False, False),
    ],
)
async def test_secure_flag_is_the_same_for_both_cookies_on_login_refresh_and_logout(
    client, test_settings, base, setting, expected
):
    test_settings.cookie_secure = setting
    await _bootstrap(client)
    value, login = await _login(client, base)
    rotated = await _refresh(client, new=value, old=value, base=base)
    assert rotated.status_code == 200
    logout = await _logout(client, new=_same_value(rotated), old=_same_value(rotated), base=base)
    assert logout.status_code == 204

    for response in (login, rotated, logout):
        cookies = _set_cookies(response)
        assert set(cookies) == {NEW, OLD}
        assert cookies[NEW][1] == cookies[OLD][1]
        for _, attrs in cookies.values():
            assert ("secure" in attrs) is expected
            assert attrs["httponly"] is True and attrs["samesite"] == "strict"
            assert attrs["path"] == "/api/v1/auth"


@pytest.mark.asyncio
async def test_finishing_two_factor_login_writes_both_cookies(client):
    await _bootstrap(client)
    _, login = await _login(client)
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    secret = (await client.post("/api/v1/me/totp/setup", headers=headers)).json()["secret"]
    confirm = await client.post("/api/v1/me/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=headers)
    assert confirm.status_code == 200, confirm.text

    first = await _post(client, "/login", json={"username": "nico", "password": PASSWORD})
    assert first.status_code == 202
    assert _set_cookies(first) == {}, "vor dem zweiten Faktor gibt es kein Cookie"

    done = await _post(client, "/mfa", json={"mfa_token": first.json()["mfa_token"], "code": pyotp.TOTP(secret).now()})
    assert done.status_code == 200, done.text
    value = _same_value(done)
    assert "refresh_token" not in done.json()
    assert _set_cookies(done)[NEW][1].keys() == _set_cookies(login)[NEW][1].keys()
    assert (await _refresh(client, new=value)).status_code == 200


@pytest.mark.asyncio
async def test_bootstrap_sets_no_cookie_and_the_wizard_login_gets_both(client):
    """Das Konto anlegen (Assistent) setzt kein Cookie; die Anmeldung danach (SetupPage) setzt beide."""
    r = await client.post(
        f"{API}/bootstrap", json={"username": "nico", "password": PASSWORD, "setup_code": "TEST-CODE-2345"}
    )
    assert r.status_code == 201
    assert _set_cookies(r) == {}
    await _login(client)


@pytest.mark.asyncio
async def test_android_client_gets_the_token_in_the_body_and_no_cookie_at_all(client):
    await _bootstrap(client)
    r = await _post(client, "/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"})
    assert r.status_code == 200
    assert r.json()["refresh_token"] and _set_cookies(r) == {}
    rotated = await _post(client, "/refresh", json={"refresh_token": r.json()["refresh_token"]})
    assert rotated.status_code == 200 and rotated.json()["refresh_token"] and _set_cookies(rotated) == {}


# --------------------------------------------------------------------------- Lesen


@pytest.mark.asyncio
async def test_only_the_old_cookie_present_refresh_works_and_both_are_set_afterwards(client, db_session):
    await _bootstrap(client)
    value, _ = await _login(client)

    r = await _refresh(client, old=value)
    assert r.status_code == 200
    rotated = _same_value(r)
    assert rotated != value
    assert "refresh_token" not in r.json()

    old_row, new_row = await _row(db_session, value), await _row(db_session, rotated)
    assert old_row.revoked_at is not None and old_row.replaced_by_id == new_row.id
    assert _live(await _snapshot(db_session)) == {new_row.id}


@pytest.mark.asyncio
async def test_only_the_new_cookie_present_refresh_works_and_both_are_set_afterwards(client, db_session):
    await _bootstrap(client)
    value, _ = await _login(client)

    r = await _refresh(client, new=value)
    assert r.status_code == 200
    rotated = _same_value(r)
    assert rotated != value
    assert _live(await _snapshot(db_session)) == {(await _row(db_session, rotated)).id}


@pytest.mark.asyncio
async def test_a_token_issued_before_the_update_works_with_the_old_cookie_name_only(client, db_session):
    """Der Zustand beim ersten Deploy: Der Browser hat nur `lattice_refresh`, in der Datenbank steht
    eine Zeile, die das alte Image angelegt hat (Hash wie `security.hash_opaque_token`). Danach hat er beide."""
    await _bootstrap(client)
    user = (await db_session.execute(select(User))).scalars().one()
    raw = security.generate_opaque_token()
    db_session.add(
        RefreshToken(
            id=new_id(),
            user_id=user.id,
            token_hash=security.hash_opaque_token(raw),
            client_type="web",
            expires_at=utcnow() + timedelta(days=10),
        )
    )
    await db_session.flush()

    r = await _refresh(client, old=raw)
    assert r.status_code == 200, r.text
    rotated = _same_value(r)
    assert rotated != raw
    assert (await _refresh(client, new=rotated)).status_code == 200


@pytest.mark.asyncio
async def test_service_without_any_candidate_is_an_invalid_login(db_session, test_settings):
    with pytest.raises(auth_service.InvalidCredentials):
        await auth_service.refresh_with_candidates(db_session, test_settings, raw_refresh_tokens=[])


@pytest.mark.asyncio
async def test_no_refresh_cookie_at_all_is_still_a_400(client):
    r = await _refresh(client)
    assert r.status_code == 400 and _set_cookies(r) == {}
    client.cookies.clear()
    only_foreign = await client.post(f"{API}/refresh", json={}, headers={"Cookie": "theme=dunkel"})
    assert only_foreign.status_code == 400


@pytest.mark.asyncio
async def test_an_empty_cookie_value_is_skipped_not_tried(client):
    await _bootstrap(client)
    value, _ = await _login(client)
    r = await _refresh(client, new="", old=value)
    assert r.status_code == 200
    only_empty = await _refresh(client, new="", old="")
    assert only_empty.status_code == 400


@pytest.mark.asyncio
async def test_candidates_are_tried_new_name_first_without_duplicates(client, monkeypatch):
    calls: list[str] = []
    real = auth_service.refresh_access_token

    async def spy(session, settings, *, raw_refresh_token):
        calls.append(raw_refresh_token)
        return await real(session, settings, raw_refresh_token=raw_refresh_token)

    monkeypatch.setattr(auth_service, "refresh_access_token", spy)
    await _bootstrap(client)

    assert (await _refresh(client, new="aaa", old="bbb")).status_code == 401
    assert calls == ["aaa", "bbb"]
    calls.clear()
    assert (await _refresh(client, new="same", old="same")).status_code == 401
    assert calls == ["same"], "gleicher Wert nur einmal versuchen"
    calls.clear()
    assert (await _refresh(client, old="nur-alt")).status_code == 401
    assert calls == ["nur-alt"]
    calls.clear()
    assert (await _refresh(client, new="nur-neu")).status_code == 401
    assert calls == ["nur-neu"]


@pytest.mark.asyncio
async def test_a_token_in_the_body_wins_and_the_cookies_are_never_tried(client):
    """Android/CLI: wer einen Token im Body schickt, bekommt genau dessen Ergebnis -- ein ungueltiger
    Body-Token darf nicht ueber die Cookies im selben Request doch noch durchgehen."""
    await _bootstrap(client)
    web, _ = await _login(client)
    phone = await _post(client, "/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"})
    phone_token = phone.json()["refresh_token"]
    assert (await _post(client, "/logout", json={"refresh_token": phone_token})).status_code == 204

    r = await _refresh(client, new=web, old=web, json={"refresh_token": phone_token})
    assert r.status_code == 401
    assert _set_cookies(r) == {}
    assert (await _refresh(client, new=web, old=web)).status_code == 200, "die Web-Anmeldung lebt weiter"


@pytest.mark.asyncio
async def test_new_name_is_preferred_when_both_cookies_hold_different_live_sessions(client, db_session):
    await _bootstrap(client)
    first, _ = await _login(client)
    second, _ = await _login(client)
    before = await _snapshot(db_session)

    r = await _refresh(client, new=first, old=second)
    assert r.status_code == 200
    after = await _snapshot(db_session)
    assert _changed(before, after) == {(await _row(db_session, first)).id}, "nur die Sitzung des neuen Namens rotiert"
    assert (await _row(db_session, second)).revoked_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["new", "old"])
async def test_an_unknown_value_in_one_cookie_falls_through_to_the_other(client, db_session, which):
    await _bootstrap(client)
    value, _ = await _login(client)
    before = await _snapshot(db_session)

    kwargs = {"old": value, "new": "gibt-es-nicht"} if which == "new" else {"new": value, "old": "gibt-es-nicht"}
    r = await _refresh(client, **kwargs)
    assert r.status_code == 200
    rotated = _same_value(r)
    after = await _snapshot(db_session)
    assert _changed(before, after) == {(await _row(db_session, value)).id}
    assert _live(after) == {(await _row(db_session, rotated)).id}


# --------------------------------------------------------------------------- Rollback


@pytest.mark.asyncio
async def test_rollback_and_back_again_never_signs_the_user_out(client, db_session):
    """Neu angemeldet (beide Cookies) -> Rollback: das alte Image rotiert und schreibt nur
    `lattice_refresh` -> wieder aufs neue Image: der neue Name ist veraltet, der alte aktuell.
    Zweimal hintereinander, weil ein Deploy-Zyklus sich wiederholen kann."""
    await _bootstrap(client)
    value, _ = await _login(client)
    browser = {NEW: value, OLD: value}

    for _round in range(2):
        browser[OLD] = await _old_image_refresh(client, browser[OLD])
        assert browser[NEW] != browser[OLD]
        await _age_revocations(db_session)  # der Rollback dauert laenger als die Gnadenfrist

        before = await _snapshot(db_session)
        current_row = await _row(db_session, browser[OLD])
        r = await _refresh(client, new=browser[NEW], old=browser[OLD])
        assert r.status_code == 200, "der Nutzer bleibt angemeldet"
        browser = {NEW: _same_value(r), OLD: _same_value(r)}

        after = await _snapshot(db_session)
        assert _changed(before, after) == {current_row.id}, "nur die aktuelle Sitzung rotiert, keine andere wird widerrufen"
        assert _live(after) == {(await _row(db_session, browser[NEW])).id}, "genau eine lebende Sitzung"

    assert (await _refresh(client, new=browser[NEW], old=browser[OLD])).status_code == 200


@pytest.mark.asyncio
async def test_stale_new_cookie_within_the_grace_period_does_not_touch_the_current_session(client, db_session):
    """Rollback und sofort zurueck (weniger als 30 s): der veraltete neue Wert faellt in die
    Gnadenfrist und liefert nur einen Access-Token. Die aktuelle Sitzung im alten Cookie bleibt
    unangetastet und gilt beim naechsten Mal weiter."""
    await _bootstrap(client)
    value, _ = await _login(client)
    current = await _old_image_refresh(client, value)

    before = await _snapshot(db_session)
    r = await _refresh(client, new=value, old=current)
    assert r.status_code == 200
    assert r.headers.get_list("set-cookie") == []
    assert "refresh_token" not in r.json()
    assert await _snapshot(db_session) == before, "nichts widerrufen, nichts angelegt"

    after_grace = await _refresh(client, new=current, old=current)
    assert after_grace.status_code == 200, "die aktuelle Sitzung gilt weiter"


@pytest.mark.asyncio
async def test_current_new_cookie_with_a_stale_old_cookie_works_through_the_new_one(client, db_session):
    await _bootstrap(client)
    value, _ = await _login(client)
    current = _same_value(await _refresh(client, new=value, old=value))
    await _age_revocations(db_session)

    before = await _snapshot(db_session)
    stale_row = await _row(db_session, value)
    current_row = await _row(db_session, current)
    r = await _refresh(client, new=current, old=value)
    assert r.status_code == 200
    rotated = _same_value(r)

    after = await _snapshot(db_session)
    assert after[stale_row.id] == before[stale_row.id], "der veraltete alte Token bleibt, wie er war"
    assert _changed(before, after) == {current_row.id}
    assert _live(after) == {(await _row(db_session, rotated)).id}


@pytest.mark.asyncio
async def test_failed_attempts_write_nothing_and_never_revoke_a_chain(client, db_session):
    """Kern der Sicherheitsfrage: Ein veralteter oder fremder Wert -- in welchem Cookie auch
    immer -- liefert 401 und aendert KEINE Zeile. Die aktuelle Sitzung desselben Nutzers bleibt gueltig."""
    await _bootstrap(client)
    stale, _ = await _login(client)
    current = _same_value(await _refresh(client, new=stale, old=stale))
    await _age_revocations(db_session)
    before = await _snapshot(db_session)

    attempts = [
        {"new": stale},
        {"old": stale},
        {"new": stale, "old": stale},
        {"new": stale, "old": "fremd"},
        {"new": "fremd", "old": stale},
        {"new": "fremd", "old": "auch-fremd"},
    ]
    for kwargs in attempts:
        r = await _refresh(client, **kwargs)
        assert r.status_code == 401, kwargs
        assert r.json()["detail"] == GENERIC_401
        assert r.headers.get_list("set-cookie") == [], "ein Fehlschlag fasst die Cookies nicht an"
        assert await _snapshot(db_session) == before, kwargs

    assert (await _refresh(client, new=current, old=current)).status_code == 200


# --------------------------------------------------------------------------- Gnadenfrist, zwei Tabs


@pytest.mark.asyncio
@pytest.mark.parametrize("cookies", ["both", "new", "old"])
async def test_grace_period_sets_no_cookie_and_deletes_none(client, db_session, test_settings, cookies):
    await _bootstrap(client)
    value, _ = await _login(client)
    rotated = _same_value(await _refresh(client, new=value, old=value))  # Tab A

    kwargs = {"both": {"new": value, "old": value}, "new": {"new": value}, "old": {"old": value}}[cookies]
    tab_b = await _refresh(client, **kwargs)
    assert tab_b.status_code == 200
    assert tab_b.headers.get_list("set-cookie") == []
    body = tab_b.json()
    assert body["access_token"] and "refresh_token" not in body

    claims = security.decode_jwt(
        body["access_token"], secret=test_settings.get_or_create_jwt_secret(), expected_type="access"
    )
    assert claims["sid"] == (await _row(db_session, rotated)).id, "gehoert zur Nachfolger-Sitzung"
    assert _live(await _snapshot(db_session)) == {claims["sid"]}, "keine zweite Sitzungskette"
    assert (await _refresh(client, new=rotated, old=rotated)).status_code == 200


@pytest.mark.asyncio
async def test_two_tabs_with_the_same_cookies_behave_as_before(client, db_session):
    await _bootstrap(client)
    value, _ = await _login(client)

    tab_a = await _refresh(client, new=value, old=value)
    tab_b = await _refresh(client, new=value, old=value)
    assert (tab_a.status_code, tab_b.status_code) == (200, 200)
    assert len(_set_cookies(tab_a)) == 2 and tab_b.headers.get_list("set-cookie") == []
    assert len(_live(await _snapshot(db_session))) == 1


@pytest.mark.asyncio
async def test_two_tabs_after_a_rollback_do_not_log_each_other_out(client, db_session):
    """Nach Rollback und Rueckkehr (neuer Name veraltet, alter aktuell) erneuern zwei Tabs fast
    gleichzeitig: Tab A rotiert ueber den alten Namen, Tab B kommt mit demselben Paar an."""
    await _bootstrap(client)
    value, _ = await _login(client)
    current = await _old_image_refresh(client, value)
    await _age_revocations(db_session)

    tab_a = await _refresh(client, new=value, old=current)
    tab_b = await _refresh(client, new=value, old=current)
    assert (tab_a.status_code, tab_b.status_code) == (200, 200)
    rotated = _same_value(tab_a)
    assert tab_b.headers.get_list("set-cookie") == []
    assert _live(await _snapshot(db_session)) == {(await _row(db_session, rotated)).id}


# --------------------------------------------------------------------------- Abmelden


@pytest.mark.asyncio
@pytest.mark.parametrize("base,secure", [("http://test", False), ("https://test", True)])
async def test_logout_deletes_both_cookies_with_the_attributes_they_were_set_with(client, base, secure):
    await _bootstrap(client)
    value, login = await _login(client, base)

    r = await _logout(client, new=value, old=value, base=base)
    assert r.status_code == 204
    deleted = _set_cookies(r)
    assert list(deleted) == [NEW, OLD]
    for name in (NEW, OLD):
        cookie_value, attrs = deleted[name]
        assert cookie_value in ("", '""')
        assert attrs["max-age"] == "0"
        set_attrs = _set_cookies(login)[name][1]
        for key in ("httponly", "samesite", "path"):
            assert attrs[key] == set_attrs[key], key
        assert ("secure" in attrs) is secure


@pytest.mark.asyncio
@pytest.mark.parametrize("presented", ["both", "new", "old"])
async def test_logout_revokes_the_token_whichever_cookie_carried_it(client, presented):
    await _bootstrap(client)
    value, _ = await _login(client)

    kwargs = {"both": {"new": value, "old": value}, "new": {"new": value}, "old": {"old": value}}[presented]
    r = await _logout(client, **kwargs)
    assert r.status_code == 204 and set(_set_cookies(r)) == {NEW, OLD}

    for attempt in ({"new": value}, {"old": value}, {"new": value, "old": value}):
        assert (await _refresh(client, **attempt)).status_code == 401, attempt


@pytest.mark.asyncio
async def test_logout_revokes_both_tokens_when_the_cookies_hold_different_sessions(client, db_session):
    await _bootstrap(client)
    first, _ = await _login(client)
    second, _ = await _login(client)

    assert (await _logout(client, new=first, old=second)).status_code == 204
    assert (await _row(db_session, first)).revoked_at is not None
    assert (await _row(db_session, second)).revoked_at is not None
    assert _live(await _snapshot(db_session)) == set()
    assert (await _refresh(client, new=first)).status_code == 401
    assert (await _refresh(client, old=second)).status_code == 401


@pytest.mark.asyncio
async def test_logout_after_rollback_revokes_the_current_session_and_the_pair_is_dead(client, db_session):
    await _bootstrap(client)
    value, _ = await _login(client)
    current = await _old_image_refresh(client, value)
    await _age_revocations(db_session)

    assert (await _logout(client, new=value, old=current)).status_code == 204
    assert _live(await _snapshot(db_session)) == set()
    assert (await _refresh(client, new=value, old=current)).status_code == 401


@pytest.mark.asyncio
async def test_logout_without_any_token_is_204_and_still_clears_both_cookies(client):
    r = await _logout(client)
    assert r.status_code == 204
    assert set(_set_cookies(r)) == {NEW, OLD}


@pytest.mark.asyncio
async def test_logout_with_a_token_in_the_body_revokes_it_and_clears_both_cookies(client):
    await _bootstrap(client)
    phone = await _post(client, "/login", json={"username": "nico", "password": PASSWORD, "client_type": "android"})
    token = phone.json()["refresh_token"]

    r = await _logout(client, json={"refresh_token": token})
    assert r.status_code == 204 and set(_set_cookies(r)) == {NEW, OLD}
    assert (await _post(client, "/refresh", json={"refresh_token": token})).status_code == 401


@pytest.mark.asyncio
async def test_logout_does_not_write_any_cookie_through_the_grace_path(client):
    """Abmelden widerruft ohne Nachfolger: auch innerhalb der 30 s ist der Wert danach in beiden Namen tot."""
    await _bootstrap(client)
    value, _ = await _login(client)
    assert (await _logout(client, new=value, old=value)).status_code == 204
    for kwargs in ({"new": value}, {"old": value}):
        r = await _refresh(client, **kwargs)
        assert r.status_code == 401 and r.headers.get_list("set-cookie") == []


# --------------------------------------------------------------------------- Wiederverwendung erkennen


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["new", "old", "both"])
async def test_a_reused_token_is_rejected_after_the_grace_period_through_either_name(client, db_session, via):
    await _bootstrap(client)
    stolen, _ = await _login(client)
    current = _same_value(await _refresh(client, new=stolen, old=stolen))
    await _age_revocations(db_session)
    before = await _snapshot(db_session)

    kwargs = {"new": {"new": stolen}, "old": {"old": stolen}, "both": {"new": stolen, "old": stolen}}[via]
    r = await _refresh(client, **kwargs)
    assert r.status_code == 401
    assert r.json()["detail"] == GENERIC_401
    assert await _snapshot(db_session) == before
    assert (await _refresh(client, new=current, old=current)).status_code == 200, "wie bisher: der Fehlschlag trifft die echte Sitzung nicht"


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["new", "old"])
async def test_a_reused_token_inside_the_grace_period_gets_only_an_access_token(client, via):
    await _bootstrap(client)
    stolen, _ = await _login(client)
    await _refresh(client, new=stolen, old=stolen)

    r = await _refresh(client, **{via: stolen})
    assert r.status_code == 200
    assert "refresh_token" not in r.json() and r.headers.get_list("set-cookie") == []


@pytest.mark.asyncio
async def test_sign_out_everywhere_ends_both_names_even_in_a_mismatched_state(client, db_session):
    """Passwort aendern, Admin-Reset, 'ueberall abmelden' und Wiederherstellen widerrufen alle Tokens
    des Nutzers in der Datenbank -- ueber welchen Cookie-Namen auch immer sie ankaemen."""
    await _bootstrap(client)
    value, login = await _login(client)
    current = await _old_image_refresh(client, value)  # Browser: neu = veraltet, alt = aktuell
    user_id = login.json()["user"]["id"]

    assert await auth_service.revoke_refresh_tokens(db_session, user_id) == 1
    for kwargs in ({"new": value}, {"old": current}, {"new": value, "old": current}):
        assert (await _refresh(client, **kwargs)).status_code == 401, kwargs
    assert (await _logout(client, new=value, old=current)).status_code == 204


@pytest.mark.asyncio
async def test_own_password_change_keeps_the_current_browser_logged_in_with_both_cookies(client):
    await _bootstrap(client)
    value, login = await _login(client)
    rotated = _same_value(await _refresh(client, new=value, old=value))
    stale_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}  # Tab A: `sid` der alten Sitzung

    ok = await client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": "neues-passwort-1"},
        headers=stale_headers,
    )
    assert ok.status_code == 204, ok.text
    assert (await _refresh(client, new=value, old=value)).status_code == 401, "der Vorgaenger bekommt keine Gnadenfrist mehr"
    assert (await _refresh(client, new=rotated, old=rotated)).status_code == 200


@pytest.mark.asyncio
async def test_deactivated_user_is_rejected_through_either_name(client, db_session):
    await _bootstrap(client)
    value, _ = await _login(client)
    user = (await db_session.execute(select(User))).scalars().one()
    user.is_active = False
    await db_session.flush()

    for kwargs in ({"new": value}, {"old": value}, {"new": value, "old": value}):
        r = await _refresh(client, **kwargs)
        assert r.status_code == 401 and r.json()["detail"] == "Nutzer nicht mehr aktiv."
        assert r.headers.get_list("set-cookie") == []
