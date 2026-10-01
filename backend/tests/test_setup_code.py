"""Einrichtungscode fuer die Erstinbetriebnahme (core/setup_code.py, POST /auth/bootstrap).

Ohne den Code darf niemand das erste Konto anlegen -- sonst wuerde der Owner, wer die
Seite nach der Installation zuerst aufruft."""

from __future__ import annotations

import os
import re
import stat
import sys

import pytest
from nodvard_deck.core import login_limit, setup_code
from nodvard_deck.models import AuditEntry, User
from sqlalchemy import select

URL = "/api/v1/auth/bootstrap"
BODY = {"username": "nico", "password": "correct-horse-battery"}


@pytest.fixture
def real_code_settings(test_settings):
    """Kein vorgegebener Code: Nodvard Deck wuerfelt selbst (wie bei einer echten Installation)."""
    test_settings.setup_code = None
    return test_settings


def _file(settings):
    return setup_code.code_file(settings.data_dir)


# --- reine Bausteine ---------------------------------------------------------


def test_generated_code_has_three_groups_without_ambiguous_characters():
    for _ in range(200):
        code = setup_code.generate()
        assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code)
        assert not set("IO01") & set(code)


def test_normalize_ignores_case_dashes_and_spaces():
    assert setup_code.normalize(" abcd-efgh  jkmn ") == "ABCDEFGHJKMN"


def test_ensure_creates_private_file_and_keeps_it(tmp_path):
    code, created = setup_code.ensure(tmp_path)
    assert created is True
    path = setup_code.code_file(tmp_path)
    if sys.platform != "win32":  # Windows kennt keine POSIX-Rechte (immer 0o666)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again, created_again = setup_code.ensure(tmp_path)
    assert (again, created_again) == (code, False)


def test_preset_wins_and_writes_no_file(tmp_path):
    code, created = setup_code.ensure(tmp_path, "meincode-2345")
    assert code == "MEIN-CODE-2345"
    assert created is False
    assert not setup_code.code_file(tmp_path).exists()
    assert setup_code.verify(tmp_path, "mein code 2345", "meincode-2345")


def test_too_short_preset_is_ignored(tmp_path):
    code, created = setup_code.ensure(tmp_path, "abc")
    assert created is True
    assert code != "ABC"


def test_verify_without_any_code_is_false(tmp_path):
    assert setup_code.verify(tmp_path, "") is False
    assert setup_code.verify(tmp_path, "irgendwas") is False


def test_clear(tmp_path):
    setup_code.ensure(tmp_path)
    assert setup_code.clear(tmp_path) is True
    assert setup_code.clear(tmp_path) is False


# --- Endpunkt ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_without_code_is_forbidden(client, real_code_settings, db_session):
    r = await client.post(URL, json=BODY)
    assert r.status_code == 403
    assert "Einrichtungscode" in r.json()["detail"]
    assert "Protokoll des Containers" in r.json()["detail"]
    assert (await db_session.execute(select(User))).first() is None


@pytest.mark.asyncio
async def test_setup_with_wrong_code_is_forbidden_and_audited(client, real_code_settings, db_session):
    setup_code.ensure(real_code_settings.data_dir)
    r = await client.post(URL, json={**BODY, "setup_code": "AAAA-BBBB-CCCC"})
    assert r.status_code == 403
    assert (await db_session.execute(select(User))).first() is None
    entries = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "setup.failed"))).scalars().all()
    assert len(entries) == 1
    assert entries[0].outcome == "failure"
    # Der richtige Code darf nie im Protokoll landen, der falsche auch nicht.
    assert "AAAA" not in str(entries[0].detail) + (entries[0].reason or "")


@pytest.mark.asyncio
async def test_setup_with_right_code_creates_owner_and_deletes_code(client, real_code_settings, db_session):
    code, _ = setup_code.ensure(real_code_settings.data_dir)
    assert _file(real_code_settings).exists()
    # Eingabe darf klein und ohne Striche sein.
    r = await client.post(URL, json={**BODY, "setup_code": code.lower().replace("-", " ")})
    assert r.status_code == 201, r.text
    assert r.json()["is_owner"] is True
    assert not _file(real_code_settings).exists()
    done = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "setup.completed"))).scalars().all()
    assert len(done) == 1


@pytest.mark.asyncio
async def test_code_is_invalid_after_setup(client, real_code_settings):
    code, _ = setup_code.ensure(real_code_settings.data_dir)
    assert (await client.post(URL, json={**BODY, "setup_code": code})).status_code == 201
    second = await client.post(URL, json={"username": "zweiter", "password": "whatever123", "setup_code": code})
    assert second.status_code == 409
    # Und eine wieder hingelegte Datei wird nicht akzeptiert/bleibt nicht liegen.
    setup_code.ensure(real_code_settings.data_dir)
    third = await client.post(URL, json={"username": "dritter", "password": "whatever123", "setup_code": "egal"})
    assert third.status_code == 409
    assert not _file(real_code_settings).exists()


@pytest.mark.asyncio
async def test_env_preset_is_the_required_code(client, test_settings):
    test_settings.setup_code = "vorab-gesetzt-9"
    wrong = await client.post(URL, json={**BODY, "setup_code": "TEST-CODE-2345"})
    assert wrong.status_code == 403
    ok = await client.post(URL, json={**BODY, "setup_code": "VORAB GESETZT 9"})
    assert ok.status_code == 201, ok.text
    assert not _file(test_settings).exists()


@pytest.mark.asyncio
async def test_brute_force_is_limited_like_login(client, real_code_settings, db_session):
    code, _ = setup_code.ensure(real_code_settings.data_dir)
    for _ in range(login_limit.MAX_FAILURES_PER_USER):
        assert (await client.post(URL, json={**BODY, "setup_code": "AAAA-AAAA-AAAA"})).status_code == 403
    blocked = await client.post(URL, json={**BODY, "setup_code": "AAAA-AAAA-AAAA"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    # Auch der richtige Code kommt waehrend der Sperre nicht durch.
    assert (await client.post(URL, json={**BODY, "setup_code": code})).status_code == 429
    assert (await db_session.execute(select(User))).first() is None
    locked = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "login.locked"))).scalars().all()
    assert len(locked) == 1


@pytest.mark.asyncio
async def test_missing_code_file_is_recreated_not_bypassed(client, real_code_settings, capsys):
    """Datei verschwunden (z. B. Volume-Panne): kein Freifahrtschein, stattdessen wird ein
    neuer Code angelegt und ins Protokoll geschrieben."""
    assert not _file(real_code_settings).exists()
    r = await client.post(URL, json={**BODY, "setup_code": ""})
    assert r.status_code == 403
    assert _file(real_code_settings).exists()
    assert "Einrichtungscode: " in capsys.readouterr().out


# --- Start (Lifespan-Baustein) -------------------------------------------------


@pytest.mark.asyncio
async def test_startup_logs_code_until_setup_is_done(db_session, real_code_settings, capsys):
    from nodvard_deck.services import auth as auth_service

    first = await auth_service.prepare_setup_code(db_session, real_code_settings)
    out = capsys.readouterr().out
    assert first is not None
    assert f"Einrichtungscode: {first} – im Browser eingeben" in out

    # Neustart: derselbe Code, wieder im Protokoll.
    second = await auth_service.prepare_setup_code(db_session, real_code_settings)
    assert second == first
    assert first in capsys.readouterr().out


@pytest.mark.asyncio
async def test_startup_with_existing_owner_prints_nothing_and_cleans_up(db_session, real_code_settings, capsys):
    from nodvard_deck.services import auth as auth_service

    setup_code.ensure(real_code_settings.data_dir)
    await auth_service.bootstrap_owner(db_session, username="nico", password="correct-horse-battery")
    assert await auth_service.prepare_setup_code(db_session, real_code_settings) is None
    assert "Einrichtungscode" not in capsys.readouterr().out
    assert not _file(real_code_settings).exists()


@pytest.mark.asyncio
async def test_startup_preset_is_shown_and_no_file_written(db_session, test_settings, capsys):
    from nodvard_deck.services import auth as auth_service

    test_settings.setup_code = "mein-eigener-code"
    code = await auth_service.prepare_setup_code(db_session, test_settings)
    assert code == "MEIN-EIGE-NERC-ODE"
    assert "MEIN-EIGE-NERC-ODE" in capsys.readouterr().out
    assert not _file(test_settings).exists()


# --- Protokoll-Block: der Code ist gross und einzeln zu finden ---------------


def test_banner_shows_the_code_alone_in_a_line_between_blank_lines():
    lines = setup_code.banner("K7MQ-X2VD-H9PA").split("\n")
    at = [i for i, line in enumerate(lines) if line.strip() == "K7MQ-X2VD-H9PA"]
    assert len(at) == 1, "der Code steht genau einmal allein in einer Zeile"
    assert lines[at[0] - 1].strip() == "" and lines[at[0] + 1].strip() == ""
    # Rahmen aus '='-Zeilen um den ganzen Block.
    frame = [i for i, line in enumerate(lines) if set(line) == {"="}]
    assert len(frame) == 2 and frame[0] < at[0] < frame[1]


def test_banner_keeps_the_text_that_docs_and_grep_rely_on():
    text = setup_code.banner("K7MQ-X2VD-H9PA")
    assert "Einrichtungscode: K7MQ-X2VD-H9PA – im Browser eingeben" in text
    assert text.startswith("\n") and text.endswith("\n")
