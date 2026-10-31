"""Längst beendete Anmeldungen aufräumen (`services.auth.purge_old_logins`, Kern-Job `logins-retention`)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from nodvard_deck import config
from nodvard_deck.config import Settings
from nodvard_deck.core import security
from nodvard_deck.core.scheduler import (
    CORE_SCHEDULER_EXT_ID,
    LOGINS_RETENTION_KEY,
    NOTIFICATIONS_RETENTION_KEY,
    get_scheduler_service,
    register_core_jobs,
)
from nodvard_deck.db import utcnow
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import RefreshToken, User
from nodvard_deck.services import jobs as jobs_service
from nodvard_deck.services import session_guard

DAY = timedelta(days=1)


async def _logins(session, settings: Settings) -> dict[str, RefreshToken]:
    user = User(username="nico", password_hash="x")
    session.add(user)
    await session.flush()
    now = utcnow()
    ttl = settings.refresh_token_ttl_days * DAY
    rows = {
        "lange abgelaufen": {"expires_at": now - ttl - DAY},
        "kürzlich abgelaufen": {"expires_at": now - ttl + DAY},
        "lange beendet": {"expires_at": now + DAY, "revoked_at": now - ttl - DAY},
        "kürzlich erneuert": {"expires_at": now + ttl, "revoked_at": now - DAY},
        "gültig": {"expires_at": now + ttl},
    }
    tokens = {
        name: RefreshToken(user_id=user.id, token_hash=security.hash_opaque_token(name), **fields)
        for name, fields in rows.items()
    }
    session.add_all(tokens.values())
    await session.flush()
    return tokens


async def _left(session) -> set[str]:
    return set((await session.execute(select(RefreshToken.id))).scalars())


@pytest.mark.asyncio
async def test_only_logins_ended_longer_than_a_refresh_lifetime_ago_go(db_session, test_settings):
    from nodvard_deck.services import auth as auth_service

    tokens = await _logins(db_session, test_settings)

    assert await auth_service.purge_old_logins(db_session, test_settings) == 2
    assert await _left(db_session) == {
        tokens[name].id for name in ("kürzlich abgelaufen", "kürzlich erneuert", "gültig")
    }


@pytest.mark.asyncio
async def test_old_logins_go_in_chunks_with_a_commit_each(db_session, test_settings, monkeypatch):
    from nodvard_deck.services import auth as auth_service

    user = User(username="nico", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    ended = utcnow() - (test_settings.refresh_token_ttl_days + 1) * DAY
    db_session.add_all(
        RefreshToken(user_id=user.id, token_hash=security.hash_opaque_token(f"t{i}"), expires_at=ended)
        for i in range(5)
    )
    await db_session.flush()
    left_at_commit: list[int] = []
    commit = db_session.commit

    async def counting_commit():
        left_at_commit.append(len(await _left(db_session)))
        await commit()

    monkeypatch.setattr(db_session, "commit", counting_commit)
    assert await auth_service.purge_old_logins(db_session, test_settings, chunk_size=2, pause=0) == 5
    assert left_at_commit == [3, 1, 0]


@pytest.mark.asyncio
async def test_a_chain_still_leads_to_its_live_login(db_session, test_settings):
    from nodvard_deck.services import auth as auth_service

    user = User(username="nico", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    now, ttl = utcnow(), test_settings.refresh_token_ttl_days * DAY
    live = RefreshToken(user_id=user.id, token_hash=security.hash_opaque_token("c"), expires_at=now + ttl)
    db_session.add(live)
    await db_session.flush()
    recent = RefreshToken(user_id=user.id, token_hash=security.hash_opaque_token("b"), expires_at=now + ttl,
                          revoked_at=now - 10 * DAY, replaced_by_id=live.id)
    db_session.add(recent)
    await db_session.flush()
    db_session.add(RefreshToken(user_id=user.id, token_hash=security.hash_opaque_token("a"), expires_at=now,
                                revoked_at=now - ttl - DAY, replaced_by_id=recent.id))
    await db_session.flush()

    assert await auth_service.purge_old_logins(db_session, test_settings) == 1
    assert await session_guard._live_login_head(db_session, user.id, recent.id) == live.id


@pytest.mark.asyncio
async def test_purge_takes_only_the_front_of_a_chain_and_logout_still_reaches_its_end(db_session, test_settings):
    from nodvard_deck.services import auth as auth_service

    user = User(username="nico", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    now, ttl = utcnow(), test_settings.refresh_token_ttl_days * DAY
    # Erneuert alle fünf Tage über zwei Refresh-Laufzeiten hinweg, die letzte Zeile ist die laufende Anmeldung; der
    # halbe Tag hält jede Zeile von der Grenze des Aufräumens fern.
    steps = (2 * ttl) // (5 * DAY)
    issued = [now - (steps - i) * 5 * DAY + DAY / 2 for i in range(steps + 1)]
    rows = [
        RefreshToken(
            user_id=user.id, token_hash=security.hash_opaque_token(f"k{i}"), expires_at=at + ttl,
            revoked_at=issued[i + 1] if i < steps else None,
        )
        for i, at in enumerate(issued)
    ]
    db_session.add_all(rows)
    await db_session.flush()
    for row, successor in zip(rows, rows[1:]):
        row.replaced_by_id = successor.id
    await db_session.commit()

    deleted = await auth_service.purge_old_logins(db_session, test_settings)

    left = await _left(db_session)
    assert 0 < deleted < len(rows) - 1
    assert left == {row.id for row in rows[deleted:]}
    live = rows[-1].id
    for row in rows[deleted:]:
        assert await session_guard._live_login_head(db_session, user.id, row.id) == live
    await auth_service.logout(db_session, raw_refresh_token=f"k{deleted}")
    await db_session.commit()
    db_session.expire_all()
    assert (await db_session.get(RefreshToken, live)).revoked_at is not None


@pytest.mark.asyncio
async def test_the_core_job_runs_nightly_at_its_own_minute(db_session, tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    reset_extension_runtime()
    monkeypatch.setattr(config, "_settings", settings)
    await register_core_jobs(tmp_path / "runs")

    job = await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=LOGINS_RETENTION_KEY)
    assert job is not None and job.kind == "core" and job.enabled
    assert job.schedule.split()[1:] == ["3", "*", "*", "*"]
    assert get_scheduler_service()._scheduler.get_job(job.id) is not None
    minutes = set()
    for key in ("audit-retention-purge", "job-runs-retention", "restore-cleanup", NOTIFICATIONS_RETENTION_KEY,
                LOGINS_RETENTION_KEY):
        minutes.add((await jobs_service.get_job_by_key(db_session, ext_id=None, ext_job_key=key)).schedule.split()[0])
    assert len(minutes) == 5

    tokens = await _logins(db_session, settings)
    await db_session.commit()
    handler = get_extension_runtime().scheduler.get(CORE_SCHEDULER_EXT_ID, LOGINS_RETENTION_KEY)
    assert await handler() == {"deleted": 2}
    assert tokens["lange beendet"].id not in await _left(db_session)
