"""Engine und Session-Factory.

Die SQLite-Pragmas hier sind die Disziplin-Regel aus docs/00-DECISIONS.md D-02
woertlich umgesetzt: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`,
`synchronous=NORMAL`. Ohne diese vier ist SQLite unter gleichzeitigem Zugriff
(Web-Request + Scheduler-Job + Watcher-Task) unzuverlaessig.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..config import Settings, get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


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
    """Nur fuer Tests: erzwingt beim naechsten get_engine() eine frische Engine,
    passend zu einer neuen (typischerweise in-memory) DATABASE_URL."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None


def set_engine_for_testing(engine: AsyncEngine) -> None:
    """Nur fuer Tests: bindet get_engine()/get_sessionmaker()/session_scope() an eine
    bereits bestehende Engine, statt beim naechsten Zugriff eine neue gegen die
    ECHTEN Settings (get_settings()) zu erzeugen.

    Noetig seit WP-3: core.vault.vault_use() oeffnet intern eine von der
    Aufrufer-Session unabhaengige, sofort committende zweite Session ueber
    get_sessionmaker() (siehe dortiger Docstring) -- ohne diese Funktion wuerde dieser
    Pfad in Tests unbemerkt die echte data/lattice.db beruehren statt die
    Test-Datenbank, exakt dasselbe Muster wie der in WP-1 gefundene
    `get_settings()`-Singleton-Fallstrick (siehe tests/conftest.py `test_settings`)."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Ein Session-Pfad fuer die ganze Anwendung (D-02: keine parallelen Writer)."""
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
