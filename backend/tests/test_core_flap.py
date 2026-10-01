"""Generalisiertes Anti-Flapping (docs/03-DATA-MODEL.md §5): 1:1-Schwellenwerte aus dem
Vorgaengersystem (RESTART_HISTORY/MAX_RESTARTS/FLAP_WINDOW_SECONDS)."""

from __future__ import annotations

import pytest

from nodvard_deck.core import flap
from nodvard_deck.models import ActionFlapHistory


def test_fingerprint_is_stable_regardless_of_payload_key_order():
    a = flap.fingerprint("host-1", "shell.exec", {"command": "true", "timeout": 5})
    b = flap.fingerprint("host-1", "shell.exec", {"timeout": 5, "command": "true"})
    assert a == b


def test_fingerprint_differs_for_different_hosts_types_or_payloads():
    base = flap.fingerprint("host-1", "shell.exec", {"command": "true"})
    assert base != flap.fingerprint("host-2", "shell.exec", {"command": "true"})
    assert base != flap.fingerprint("host-1", "docker.restart", {"command": "true"})
    assert base != flap.fingerprint("host-1", "shell.exec", {"command": "false"})


@pytest.mark.asyncio
async def test_check_and_record_blocks_after_max_count_within_window(db_session):
    kwargs = dict(host_id="host-1", action_type="shell.exec", payload={"command": "uptime"})

    for _ in range(flap.MAX_COUNT):
        fp, blocked = await flap.check_and_record(db_session, **kwargs)
        assert blocked is False

    fp, blocked = await flap.check_and_record(db_session, **kwargs)
    assert blocked is True
    assert fp == flap.fingerprint("host-1", "shell.exec", {"command": "uptime"})


@pytest.mark.asyncio
async def test_check_and_record_does_not_mix_different_fingerprints(db_session):
    for _ in range(flap.MAX_COUNT):
        _, blocked = await flap.check_and_record(
            db_session, host_id="host-1", action_type="shell.exec", payload={"command": "a"}
        )
        assert blocked is False

    # Anderer Fingerprint (anderer Befehl) -- eigenes, noch leeres Kontingent.
    _, blocked = await flap.check_and_record(
        db_session, host_id="host-1", action_type="shell.exec", payload={"command": "b"}
    )
    assert blocked is False


@pytest.mark.asyncio
async def test_a_blocked_attempt_does_not_itself_count_toward_the_limit_again(db_session):
    """Ein bereits blockierter Versuch wird als `blocked=True` aufgezeichnet, zaehlt
    aber nicht als weiterer 'erlaubter' Versuch fuer die naechste Pruefung -- die Zeile
    existiert nur zur Sichtbarkeit (`GET /audit` o.ae.), nicht um das Limit weiter
    aufzublaehen."""
    kwargs = dict(host_id="host-1", action_type="shell.exec", payload={"command": "x"})
    for _ in range(flap.MAX_COUNT + 2):
        await flap.check_and_record(db_session, **kwargs)

    from sqlalchemy import select

    rows = (await db_session.execute(select(ActionFlapHistory))).scalars().all()
    assert len(rows) == flap.MAX_COUNT + 2
    assert sum(1 for r in rows if r.blocked) == 2
