"""Audit-Kern: Zeilen schreiben, NDJSON-Zeilen serialisieren
(docs/03-DATA-MODEL.md §4).
"""

from __future__ import annotations

import json

import pytest

from nodvard_deck.core import audit


@pytest.mark.asyncio
async def test_write_entry_persists_all_fields(db_session):
    entry = await audit.write_entry(
        db_session,
        actor_type="user",
        actor_id="user-1",
        action="login.succeeded",
        outcome="success",
        target_type="user",
        target_id="user-1",
        reason="Testgrund",
        detail={"via": "password"},
        correlation_id="corr-1",
        ip="127.0.0.1",
        user_agent="pytest",
    )

    assert entry.id is not None
    assert entry.ts is not None
    assert entry.actor_type == "user"
    assert entry.action == "login.succeeded"
    assert entry.outcome == "success"
    assert entry.detail == {"via": "password"}
    assert entry.correlation_id == "corr-1"


@pytest.mark.asyncio
async def test_write_entry_defaults_detail_to_empty_dict(db_session):
    entry = await audit.write_entry(
        db_session, actor_type="system", actor_id="scheduler", action="x", outcome="success"
    )
    assert entry.detail == {}


@pytest.mark.asyncio
async def test_write_entry_rejects_invalid_outcome(db_session):
    with pytest.raises(ValueError):
        await audit.write_entry(
            db_session, actor_type="user", actor_id="u1", action="x", outcome="erfunden"
        )


@pytest.mark.asyncio
async def test_to_ndjson_line_is_valid_json_with_expected_keys(db_session):
    entry = await audit.write_entry(
        db_session,
        actor_type="user",
        actor_id="u1",
        action="secret.used",
        outcome="success",
        detail={"label": "ssh-fleet"},
    )

    line = audit.to_ndjson_line(entry)
    parsed = json.loads(line)

    assert "\n" not in line, "eine NDJSON-Zeile darf selbst keinen Zeilenumbruch enthalten"
    assert parsed["id"] == entry.id
    assert parsed["action"] == "secret.used"
    assert parsed["detail"] == {"label": "ssh-fleet"}
    assert isinstance(parsed["ts"], str)
