"""Ein geplanter Skript-Lauf, der fehlschlaegt, meldet sich (scripts-Extension, failnotice.py).

Seit ein Lauf ohne Ergebnis als fehlgeschlagen zaehlt, stand das nur im Verlauf des Zeitplans. Jetzt kommt eine
Meldung -- einmal je Fehler, erst wieder nach einem anderen Fehler oder einem Erfolg dazwischen. Und ein Lauf, der auf
dem Server selbst scheitert (Rueckgabecode nicht 0), zaehlte bisher als Erfolg."""

from __future__ import annotations

import pytest
from nodvard_deck.models import Notification as NotificationRow
from sqlalchemy import select
from test_ext_scripts import (  # noqa: F401 -- _cleanup_sys_path ist eine autouse-Fixture
    _cleanup_sys_path,
    _host_with_credential,
    _scripts_extension_class,
    _setup_scripts_extension,
)
from test_ext_scripts_standing import (  # noqa: F401 -- Fixture
    _fleet,
    _global_runtime,
    _grant,
    _user,
)


@pytest.fixture(autouse=True)
def _scripts_on_path(_cleanup_sys_path):  # noqa: F811 -- haengt bewusst an der Aufraeum-Fixture
    _scripts_extension_class()


async def _notes(db_session, script_id):
    rows = await db_session.execute(
        select(NotificationRow).where(NotificationRow.correlation_id == f"scripts-failed:{script_id}")
    )
    return rows.scalars().all()


async def _run(handler, trigger="schedule"):
    from nodvard_sdk.context import _JOB_TRIGGER
    from nodvard_sdk.errors import NodvardError

    token = _JOB_TRIGGER.set(trigger)
    try:
        return await handler()
    except NodvardError as exc:
        return str(exc)
    finally:
        _JOB_TRIGGER.reset(token)


def _exec_with(monkeypatch, ctx, codes):
    """`ctx.exec.run` liefert der Reihe nach diese Rueckgabecodes (der letzte bleibt)."""
    from nodvard_sdk.types import ExecResult

    async def _run(host, command, *, timeout_s=60, user=None, log_command=None):
        code = codes.pop(0) if len(codes) > 1 else codes[0]
        return ExecResult(exit_code=code, stdout="geheime Ausgabe" if code else "ok", stderr="", duration_ms=1)

    monkeypatch.setattr(ctx.exec, "run", _run)


@pytest.mark.asyncio
async def test_a_scheduled_run_that_fails_on_the_server_is_a_failure_and_notifies_once(
    tmp_path, db_session, test_settings, monkeypatch
):
    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    admin = await _user(db_session, username="chefin", role="admin")
    await _grant(ctx, ext, admin)
    _exec_with(monkeypatch, ctx, [1, 0, 1])
    from nodvard_deck_ext_scripts import _ScriptJobSpec

    handler = _ScriptJobSpec(ctx, ext._repo, "audit", schedule="0 1 * * *", enabled=True).handler

    failed = await _run(handler)
    assert failed == "Auf pve1 ist das Skript fehlgeschlagen; die Ausgabe steht unter „Aktionen“."
    notes = await _notes(db_session, "audit")
    assert len(notes) == 1
    assert notes[0].title == "Skript „Sicherheitsaudit“ fehlgeschlagen"
    assert notes[0].severity == "warning"
    assert failed in notes[0].body and "geheime Ausgabe" not in notes[0].body

    ok = await _run(handler)
    assert isinstance(ok, dict) and {r["status"] for r in ok["results"]} == {"succeeded"}

    assert await _run(handler) == failed  # nach einem Erfolg meldet sich derselbe Fehler wieder
    assert len(await _notes(db_session, "audit")) == 2

    # Dieselbe Aktion zum vierten Mal in 15 Minuten: Das Gate lehnt ab (core/flap.py). Auch das ist ein Fehlschlag,
    # mit anderem Satz und darum mit eigener Meldung -- aber nur einmal, nicht bei jedem weiteren Lauf.
    denied = await _run(handler)
    assert denied == "Auf pve1 hat das Dashboard den Lauf abgelehnt (siehe „Aktionen“)."
    assert len(await _notes(db_session, "audit")) == 3
    assert await _run(handler) == denied
    assert len(await _notes(db_session, "audit")) == 3


@pytest.mark.asyncio
async def test_a_run_started_by_hand_fails_the_same_way_but_does_not_notify(
    tmp_path, db_session, test_settings, monkeypatch
):
    ctx, ext, _group, _members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    from nodvard_deck_ext_scripts import _ScriptJobSpec
    from nodvard_deck_ext_scripts.repo import ScriptMeta

    ext._repo.save(
        ScriptMeta(id="ohne-wert", name="Ohne Wert", params_schema={"ziel": {"type": "string"}},
                   target=ext._repo.get("audit").meta.target, schedule="0 3 * * *", enabled=True),
        "tar czf $ziel /etc\n",
        commit_message="ohne wert",
    )
    handler = _ScriptJobSpec(ctx, ext._repo, "ohne-wert", schedule="0 3 * * *", enabled=True).handler

    assert (await _run(handler, trigger="manual")).startswith("Nicht ausgeführt auf pve1: Parameter 'ziel' fehlt")
    assert await _notes(db_session, "ohne-wert") == []

    assert (await _run(handler)).startswith("Nicht ausgeführt auf pve1")
    notes = await _notes(db_session, "ohne-wert")
    assert len(notes) == 1 and "Parameter 'ziel' fehlt" in notes[0].body
    assert (await _run(handler)).startswith("Nicht ausgeführt auf pve1")  # derselbe Fehler: keine zweite Meldung
    assert len(await _notes(db_session, "ohne-wert")) == 1


@pytest.mark.asyncio
async def test_all_hosts_skipped_is_reported_by_the_skip_notice_only(tmp_path, db_session, test_settings):
    """Lief das Skript nirgends, weil alle Server uebersprungen wurden, sagt das schon die Meldung zu den
    uebersprungenen Servern -- keine zweite Meldung zum selben Lauf."""
    ctx, ext, _group, members = await _fleet(tmp_path, db_session, test_settings, hosts=("pve1",))
    from nodvard_deck.services import hosts as hosts_service
    from nodvard_deck_ext_scripts import _ScriptJobSpec

    for cred in await hosts_service.list_credentials(db_session, members[0].id):
        await hosts_service.delete_credential(db_session, members[0].id, cred.id)
    await db_session.commit()
    handler = _ScriptJobSpec(ctx, ext._repo, "audit", schedule="0 1 * * *", enabled=True).handler

    assert (await _run(handler)).startswith("Das Skript lief auf keinem Server, alle wurden übersprungen")
    assert await _notes(db_session, "audit") == []
    skip = await db_session.execute(select(NotificationRow).where(NotificationRow.correlation_id == "scripts-skip:audit"))
    assert len(skip.scalars().all()) == 1


def test_failure_notices_claim_once_forget_and_stay_quiet_when_they_cannot_save(tmp_path):
    from nodvard_deck_ext_scripts.failnotice import FailureNotices

    notices = FailureNotices(tmp_path / "failure-notices.json")
    assert notices.claim("a", "kaputt") is True
    assert notices.claim("a", "kaputt") is False
    assert notices.claim("a", "anders kaputt") is True
    assert notices.claim("b", "kaputt") is True
    notices.forget("a")
    assert notices.claim("a", "anders kaputt") is True
    assert FailureNotices(tmp_path / "failure-notices.json").claim("b", "kaputt") is False  # Stand liegt in der Datei

    (tmp_path / "datei").write_text("kein Ordner", encoding="utf-8")
    blocked = FailureNotices(tmp_path / "datei" / "failure-notices.json")
    assert blocked.claim("a", "kaputt") is False  # lieber keine Meldung als eine bei jedem Lauf
