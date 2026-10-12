"""Engine und Session-Factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..config import Settings, get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


# WAL, Fremdschlüssel, Wartezeit bei Sperren und synchronous=NORMAL: ohne diese vier ist SQLite bei gleichzeitigem
# Zugriff (Request, Scheduler-Job, Watcher-Task) unzuverlässig.
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_engine_for(settings: Settings) -> AsyncEngine:
    engine = create_async_engine(settings.database_url, echo=False)
    if settings.database_url.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine_for(get_settings())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


def reset_engine_cache() -> None:
    """Nur für Tests: erzwingt beim nächsten `get_engine()` eine frische Engine (etwa für eine neue In-Memory-URL)."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None


def set_engine_for_testing(engine: AsyncEngine) -> None:
    """Nur für Tests: bindet `get_engine()`, `get_sessionmaker()` und `session_scope()` an eine bestehende Engine."""
    # Ohne das öffnet `core.vault.vault_use()` über `get_sessionmaker()` eine eigene Session auf die echte Datenbank
    # statt auf die der Tests.
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Ein Session-Pfad für die ganze Anwendung (keine parallelen Writer)."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-Dependency: `session: AsyncSession = Depends(get_session)`."""
    async with session_scope() as session:
        yield session
