import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cryptography.fernet import Fernet

from nodvard_deck.core import ssh, ssh_retry, vault
from nodvard_deck.db.session import session_scope
from nodvard_deck.models import Base, Secret, Setting


def target(host_id="h1"):
    return ssh.ConnectionTarget(host_id, "127.0.0.1", 22, "deck", "ssh_password", "wrong")


@pytest.fixture
def denied(monkeypatch):
    connect = AsyncMock(side_effect=ssh.SshAuthError("abgelehnt"))
    monkeypatch.setattr(ssh, "connect", connect)
    return connect


async def attempt(pool, session, host_id="h1", credential_id="c1"):
    with pytest.raises(ssh.SshAuthError):
        await pool.get(session, target(host_id), credential_id=credential_id)


@pytest.mark.asyncio
async def test_backoff_survives_new_pools_and_stops_at_six_hours(db_session, denied, monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr(ssh_retry.time, "time", lambda: now[0])
    for _ in range(2):
        await attempt(ssh.SshPool(), db_session)
        assert (await ssh_retry.load(db_session, "h1")).retry_at == 0
    for delay in (1800, 3600, 7200, 14400, 21600, 21600):
        await attempt(ssh.SshPool(), db_session)
        async with session_scope() as session:
            state = await ssh_retry.load(session, "h1")
        assert state.retry_at == now[0] + delay
        calls = denied.await_count
        now[0] += delay - 1
        with pytest.raises(ssh.SshAuthPaused, match="Zugang abgelehnt"):
            await ssh.SshPool().get(db_session, target(), credential_id="another")
        assert denied.await_count == calls
        now[0] += 1


@pytest.mark.asyncio
async def test_parallel_credentials_share_the_limit_but_other_hosts_do_not(db_session, denied):
    pool = ssh.SshPool()
    await asyncio.gather(*(attempt(pool, db_session, credential_id=f"c{i}") for i in range(12)))
    assert denied.await_count == 3
    await attempt(pool, db_session, host_id="h2")
    assert denied.await_count == 4
    async with session_scope() as session:
        assert (await ssh_retry.load(session, "h1")).failures == 3
        assert (await ssh_retry.load(session, "h2")).failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ssh.SshUnreachable("weg"), ssh.SshTimeout("Zeitlimit"), ssh.SshError("Protokoll")])
async def test_only_auth_errors_count(db_session, denied, error):
    pool = ssh.SshPool()
    await attempt(pool, db_session)
    denied.side_effect = error
    for _ in range(5):
        with pytest.raises(type(error)):
            await pool.get(db_session, target(), credential_id="c1")
    async with session_scope() as session:
        assert (await ssh_retry.load(session, "h1")) == ssh_retry.RetryState(failures=1)
    assert denied.await_count == 6


@pytest.mark.asyncio
async def test_reset_clears_count_and_delay_only_for_this_host(db_session, denied):
    pool = ssh.SshPool()
    for host_id in ("h1", "h2"):
        for _ in range(3):
            await attempt(pool, db_session, host_id)
    await pool.reset_auth_retry("h1")
    await attempt(pool, db_session)
    async with session_scope() as session:
        assert await ssh_retry.load(session, "h1") == ssh_retry.RetryState(failures=1)
        assert (await ssh_retry.load(session, "h2")).failures == 3
    assert denied.await_count == 7


@pytest.mark.asyncio
async def test_success_clears_previous_failures(db_session, denied):
    pool = ssh.SshPool()
    await attempt(pool, db_session)
    denied.side_effect = None
    conn = SimpleNamespace(is_closed=lambda: False)
    denied.return_value = conn
    assert await pool.get(db_session, target(), credential_id="c1") is conn
    await db_session.commit()
    async with session_scope() as session:
        assert await ssh_retry.load(session, "h1") == ssh_retry.RetryState()


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    """Datei-Datenbank mit kurzer Wartezeit auf Sperren: Wer die Schreibsperre hält, fällt sofort auf."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}", connect_args={"timeout": 0.2})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def scope():
        async with sessions() as session:
            yield session
            await session.commit()

    monkeypatch.setattr(ssh, "session_scope", scope)
    monkeypatch.setattr(ssh_retry, "session_scope", scope)
    yield sessions
    await engine.dispose()


async def other_writer_gets_through(sessions) -> None:
    async with sessions() as other:
        other.add(Setting(key="probe", scope="global", user_id="", value={"value": 1}))
        await other.commit()


@pytest.mark.asyncio
async def test_failure_is_saved_even_when_caller_rolls_back(file_db, denied):
    async with file_db() as session:
        await attempt(ssh.SshPool(), session)
        await session.rollback()
    async with file_db() as session:
        assert await ssh_retry.load(session, "h1") == ssh_retry.RetryState(failures=1)


@pytest.mark.asyncio
async def test_success_after_failures_leaves_no_write_lock_behind(file_db, denied):
    pool = ssh.SshPool()
    async with file_db() as session:
        await attempt(pool, session)
    denied.side_effect = None
    denied.return_value = SimpleNamespace(is_closed=lambda: False)
    async with file_db() as session:
        await pool.get(session, target(), credential_id="c1")
        # Der Aufrufer wartet danach auf SSH, ohne zu committen: Andere Schreiber müssen trotzdem durchkommen.
        await other_writer_gets_through(file_db)
        assert await ssh_retry.load(session, "h1") == ssh_retry.RetryState()


@pytest.mark.asyncio
async def test_failure_is_counted_after_the_secret_was_rekeyed_on_read(file_db, denied):
    old = {1: Fernet.generate_key()}
    async with file_db() as session:
        secret = await vault.create_secret(session, old, label="ssh", kind="ssh_password", plaintext="wrong")
        await session.commit()
    rotated = {**old, 2: Fernet.generate_key()}
    async with file_db() as session:
        # Wie `resolve_connection_target()`: Lesen schlüsselt um und schreibt in der Session des Aufrufers.
        assert await vault.read_secret_plaintext(session, rotated, secret.id) == "wrong"
        await attempt(ssh.SshPool(), session)
        await other_writer_gets_through(file_db)
    async with file_db() as session:
        assert await ssh_retry.load(session, "h1") == ssh_retry.RetryState(failures=1)
        assert (await session.get(Secret, secret.id)).key_version == 2
