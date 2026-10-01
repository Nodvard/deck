"""services.audit: Filtern, Retention-Job, NDJSON-Export.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from nodvard_deck.core import audit as core_audit
from nodvard_deck.db import utcnow
from nodvard_deck.models import AuditEntry
from nodvard_deck.services import audit as audit_service


@pytest.mark.asyncio
async def test_list_entries_filters_by_action_and_outcome(db_session):
    await core_audit.write_entry(
        db_session, actor_type="user", actor_id="u1", action="login.failed", outcome="failure"
    )
    await core_audit.write_entry(
        db_session, actor_type="user", actor_id="u1", action="login.succeeded", outcome="success"
    )
    await core_audit.write_entry(
        db_session, actor_type="user", actor_id="u2", action="login.failed", outcome="failure"
    )

    failed = await audit_service.list_entries(db_session, action="login.failed")
    assert {e.actor_id for e in failed} == {"u1", "u2"}

    succeeded = await audit_service.list_entries(db_session, outcome="success")
    assert len(succeeded) == 1
    assert succeeded[0].action == "login.succeeded"


@pytest.mark.asyncio
async def test_list_entries_filters_by_target_and_correlation(db_session):
    await core_audit.write_entry(
        db_session,
        actor_type="extension",
        actor_id="ext-1",
        action="secret.used",
        outcome="success",
        target_type="secret",
        target_id="secret-1",
        correlation_id="corr-xyz",
    )
    await core_audit.write_entry(
        db_session,
        actor_type="extension",
        actor_id="ext-1",
        action="secret.used",
        outcome="success",
        target_type="secret",
        target_id="secret-2",
    )

    by_target = await audit_service.list_entries(db_session, target_id="secret-1")
    assert len(by_target) == 1
    assert by_target[0].target_id == "secret-1"

    by_correlation = await audit_service.list_entries(db_session, correlation_id="corr-xyz")
    assert len(by_correlation) == 1


@pytest.mark.asyncio
async def test_list_entries_orders_newest_first(db_session):
    # `ts` explizit auseinandergezogen: mehrere Schreibvorgaenge innerhalb derselben
    # Millisekunde haetten sonst identische `ts`-Werte, und `new_id()` ist
    # innerhalb einer Millisekunde NICHT monoton (Zufalls-Suffix) -- die Reihenfolge
    # waere dann von der Testausfuehrung abhaengig, nicht vom Code.
    entries_written = []
    for i in range(3):
        entry = await core_audit.write_entry(
            db_session, actor_type="system", actor_id="s", action=f"a{i}", outcome="success"
        )
        entry.ts = utcnow() + timedelta(milliseconds=i)
        entries_written.append(entry)
    await db_session.flush()

    entries = await audit_service.list_entries(db_session)
    assert [e.action for e in entries] == ["a2", "a1", "a0"]


@pytest.mark.asyncio
async def test_purge_expired_deletes_only_entries_older_than_cutoff(db_session):
    old_entry = await core_audit.write_entry(
        db_session, actor_type="system", actor_id="s", action="old", outcome="success"
    )
    old_entry.ts = utcnow() - timedelta(days=200)
    recent_entry = await core_audit.write_entry(
        db_session, actor_type="system", actor_id="s", action="recent", outcome="success"
    )
    await db_session.flush()

    deleted = await audit_service.purge_expired(db_session, retention_days=90)

    assert deleted == 1
    remaining = (await db_session.execute(select(AuditEntry))).scalars().all()
    assert [e.id for e in remaining] == [recent_entry.id]


@pytest.mark.asyncio
async def test_purge_expired_returns_zero_when_nothing_to_delete(db_session):
    await core_audit.write_entry(
        db_session, actor_type="system", actor_id="s", action="recent", outcome="success"
    )
    deleted = await audit_service.purge_expired(db_session, retention_days=90)
    assert deleted == 0


@pytest.mark.asyncio
async def test_to_ndjson_joins_lines_with_trailing_newline(db_session):
    e1 = await core_audit.write_entry(
        db_session, actor_type="system", actor_id="s", action="a", outcome="success"
    )
    e2 = await core_audit.write_entry(
        db_session, actor_type="system", actor_id="s", action="b", outcome="success"
    )

    body = audit_service.to_ndjson([e1, e2])
    lines = body.splitlines()
    assert len(lines) == 2
    assert body.endswith("\n")


def test_to_ndjson_empty_list_returns_empty_string():
    assert audit_service.to_ndjson([]) == ""
