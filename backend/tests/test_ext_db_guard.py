"""`ctx.db`: SQLite lässt eine Extension nur an ihre eigenen Tabellen."""

from __future__ import annotations

import asyncio
import logging
import sqlite3

import pytest
import pytest_asyncio
from sqlalchemy import Integer, String, create_engine, event, insert, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

from nodvard_deck.config import Settings
from nodvard_deck.db.session import (
    create_engine_for,
    reset_engine_cache,
    session_scope,
    set_engine_for_testing,
)
from nodvard_deck.ext import tables
from nodvard_deck.ext.context import DbHandle
from nodvard_deck.models import Base, Setting, User

DENIED = "not authorized|prohibited"

TABLES = [
    "CREATE TABLE ext_demo_items (id INTEGER PRIMARY KEY, name TEXT)",
    ("CREATE TABLE ext_demo_tags (id INTEGER PRIMARY KEY,"
     " item_id INTEGER REFERENCES ext_demo_items(id) ON DELETE CASCADE)"),
    "CREATE TABLE ext_old_items (id INTEGER PRIMARY KEY)",
    "CREATE TABLE ext_other_items (id INTEGER PRIMARY KEY)",
]


class _DemoBase(DeclarativeBase):
    pass


class DemoItem(_DemoBase):
    __tablename__ = "ext_demo_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)


def _foreign_keys_on(dbapi_connection, _record) -> None:
    dbapi_connection.cursor().execute("PRAGMA foreign_keys=ON")


@pytest_asyncio.fixture(params=["geteilt", "datei"])
async def db(request, tmp_path):
    if request.param == "geteilt":
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        event.listen(engine.sync_engine, "connect", _foreign_keys_on)
    else:
        engine = create_engine_for(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'deck.db'}"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in TABLES:
            await conn.execute(text(statement))
    set_engine_for_testing(engine)
    try:
        yield DbHandle("demo", ("ext_demo_", "ext_old_"))
    finally:
        await engine.dispose()
        reset_engine_cache()


@pytest.mark.asyncio
async def test_own_tables_can_be_read_and_written(db):
    async with db.session() as s:
        await s.execute(text("INSERT INTO ext_demo_items (id, name) VALUES (1, 'Netzteil')"))
        await s.execute(text("INSERT INTO ext_demo_tags (id, item_id) VALUES (1, 1)"))
        await s.execute(text("INSERT INTO ext_old_items (id) VALUES (7)"))
        await s.execute(text("UPDATE ext_demo_items SET name = 'Lüfter' WHERE id = 1"))
    async with db.session() as s:
        names = (await s.execute(text(
            "WITH x AS (SELECT name FROM ext_demo_items) SELECT name FROM x"
        ))).scalars().all()
        await s.execute(text("DELETE FROM ext_demo_items WHERE id = 1"))
        tags = (await s.execute(text("SELECT count(*) FROM ext_demo_tags"))).scalar()
    assert names == ["Lüfter"] and tags == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    "SELECT password_hash FROM users",
    "SELECT name FROM ext_demo_items UNION SELECT password_hash FROM users",
    "SELECT name FROM ext_demo_items WHERE id IN (SELECT id FROM secrets)",
    "INSERT INTO settings (key, value) VALUES ('x', '1')",
    "DELETE FROM audit_log",
    "SELECT id FROM ext_other_items",
    "SELECT name FROM sqlite_master",
    "PRAGMA foreign_keys=OFF",
    "PRAGMA data_version = 1",
    "ATTACH DATABASE ':memory:' AS other",
    "CREATE TABLE ext_demo_new (x)",
    "DROP TABLE ext_demo_tags",
], ids=["kern", "union", "unterabfrage", "kern-schreiben", "protokoll-loeschen", "fremdes-modul", "schema",
        "pragma", "pragma-zuweisung", "attach", "create", "drop"])
async def test_everything_else_is_refused(db, sql):
    with pytest.raises(DatabaseError, match="not authorized|prohibited"):
        async with db.session() as s:
            await s.execute(text(sql))


@pytest.mark.asyncio
async def test_a_core_statement_from_the_cache_is_checked_again(db):
    async with session_scope() as core:
        await core.execute(text("SELECT username FROM users"))
    with pytest.raises(DatabaseError, match="prohibited"):
        async with db.session() as s:
            await s.execute(text("SELECT username FROM users"))


@pytest.mark.asyncio
async def test_the_core_stays_free_during_and_after_an_extension_session(db):
    async with db.session() as s:
        await s.execute(text("INSERT INTO ext_demo_items (id, name) VALUES (2, 'Kabel')"))
        async with session_scope() as core:
            assert (await core.execute(text("SELECT count(*) FROM users"))).scalar() == 0
        with pytest.raises(DatabaseError):
            await s.execute(text("SELECT count(*) FROM users"))
    async with session_scope() as core:
        assert (await core.execute(text("SELECT count(*) FROM users"))).scalar() == 0


@pytest.mark.asyncio
async def test_a_session_is_closed_after_its_block(db):
    async with db.session() as s:
        pass
    try:
        with pytest.raises(RuntimeError, match="nach dem Ende"):
            await s.execute(text("SELECT count(*) FROM ext_demo_items"))
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_a_refusal_names_extension_and_table_in_the_log(db, caplog):
    caplog.set_level(logging.WARNING, logger="nodvard_deck.ext.tables")
    with pytest.raises(DatabaseError):
        async with db.session() as s:
            await s.execute(text("SELECT id FROM secrets"))
    assert "Erweiterung demo: Tabelle secrets abgelehnt" in caplog.text


@pytest.mark.asyncio
async def test_parallel_sessions_keep_their_own_guard(db):
    async def extension() -> None:
        for i in range(40):
            async with db.session() as s:
                await s.execute(text(f"INSERT INTO ext_demo_items (id, name) VALUES ({i}, 'x')"))

    async def core() -> None:
        for _ in range(40):
            async with session_scope() as c:
                await c.execute(text("SELECT count(*) FROM users"))

    await asyncio.gather(extension(), core(), core())
    async with db.session() as s:
        assert (await s.execute(text("SELECT count(*) FROM ext_demo_items"))).scalar() == 40


@pytest.mark.asyncio
async def test_the_orm_goes_through_the_guard_from_the_first_statement(db):
    async with db.session() as s:
        s.add_all([DemoItem(id=1, name="a"), DemoItem(id=2, name="b")])
        await s.flush()
        # Den rekursiven Teil meldet SQLite als Lesen einer Tabelle mit dem Namen der Abfrage.
        assert (await s.execute(text(
            "WITH RECURSIVE ext_demo_n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM ext_demo_n WHERE i < 3) "
            "SELECT count(*) FROM ext_demo_n"
        ))).scalar() == 3
        async with s.begin_nested():
            s.add(DemoItem(id=3))
    with pytest.raises(DatabaseError, match=DENIED):
        async with db.session() as s:
            s.add(Setting(key="x", value={}))
            await s.flush()
    async with session_scope() as core:
        assert (await core.execute(text("SELECT count(*) FROM settings"))).scalar() == 0


@pytest.mark.asyncio
async def test_many_rows_or_no_parameters_as_the_first_statement_are_checked(db):
    with pytest.raises(DatabaseError, match=DENIED):
        async with db.session() as s:
            await s.execute(insert(Setting), [{"key": "a", "value": {}}, {"key": "b", "value": {}}])
    with pytest.raises(DatabaseError, match=DENIED):
        async with db.session() as s:
            await s.execute(text("DELETE FROM audit_log"), execution_options={"no_parameters": True})
    async with db.session() as s:
        await s.execute(text("INSERT INTO ext_demo_items (id) VALUES (:id)"), [{"id": 1}, {"id": 2}])
    async with session_scope() as core:
        assert (await core.execute(text("SELECT count(*) FROM settings"))).scalar() == 0


@pytest.mark.asyncio
async def test_vacuum_into_writes_no_copy(db, tmp_path):
    copy = tmp_path / "kopie.db"
    with pytest.raises(DatabaseError):
        async with db.session() as s:
            await s.execute(text(f"VACUUM INTO '{copy}'"))
    assert not copy.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(("sql", "allowed"), [
    ("SELECT json_extract('{\"a\": 1}', '$.a')", True),
    ("SELECT fts3_tokenizer('simple')", False),
    ("SELECT load_extension('x')", False),
], ids=["json", "fts3-tokenizer", "load-extension"])
async def test_only_harmless_functions_are_free(db, sql, allowed):
    if allowed:
        async with db.session() as s:
            assert (await s.execute(text(sql))).scalar() == 1
        return
    with pytest.raises(DatabaseError, match="not authorized"):
        async with db.session() as s:
            await s.execute(text(sql))


@pytest.mark.asyncio
async def test_a_refused_action_is_named_in_the_log(db, caplog):
    caplog.set_level(logging.WARNING, logger="nodvard_deck.ext.tables")
    with pytest.raises(DatabaseError):
        async with db.session() as s:
            await s.execute(text("PRAGMA foreign_keys=OFF"))
    assert "Erweiterung demo: SQLite-Aktion PRAGMA (foreign_keys) abgelehnt" in caplog.text


def test_a_sync_engine_is_left_alone(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sync.db'}")
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()


def _postgres(options):
    engine = type("Engine", (), {"get_execution_options": lambda self: options})()
    dialect = type("Dialect", (), {"is_async": True, "name": "postgresql"})()
    connection = type("Connection", (), {"dialect": dialect, "engine": engine})()
    return type("Context", (), {"dialect": dialect, "root_connection": connection})(), connection


def test_another_database_refuses_ctx_db_with_a_clear_message():
    guard = tables.TableGuard("demo", ("ext_demo_",))
    context, connection = _postgres({tables.GUARD_OPTION: guard})
    with pytest.raises(RuntimeError, match="nur unter SQLite"):
        tables._execute_guarded(context, "SELECT 1", lambda: None)
    tables._engine_connect(connection)
    assert tables._execute_guarded(_postgres({})[0], "SELECT 1", lambda: None) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    ("SELECT username, password_hash FROM (SELECT NULL AS username, NULL AS password_hash)"
     " FULL JOIN users USING (username, password_hash)"),
    "SELECT password_hash FROM (SELECT NULL AS password_hash) NATURAL FULL JOIN users",
    "SELECT ciphertext FROM (SELECT NULL AS ciphertext) full join secrets using (ciphertext)",
    "SELECT token_hash FROM (SELECT NULL AS token_hash) Natural Left Join refresh_tokens",
    "SELECT name FROM ext_demo_items JOIN ext_demo_tags USING (id)",
], ids=["full-join-using", "natural-full-join", "kleinbuchstaben", "gemischt", "eigene-tabellen"])
async def test_joins_with_using_or_natural_are_refused(db, sql):
    async with session_scope() as core:
        core.add(User(username="nico", password_hash="hash"))
    with pytest.raises(DatabaseError, match="not authorized: ctx.db erlaubt kein USING und kein NATURAL"):
        async with db.session() as s:
            await s.execute(text(sql))


@pytest.mark.asyncio
async def test_a_hash_does_not_reach_an_own_table_through_using(db, caplog):
    caplog.set_level(logging.WARNING, logger="nodvard_deck.ext.tables")
    async with session_scope() as core:
        core.add(User(username="nico", password_hash="hash"))
    with pytest.raises(DatabaseError):
        async with db.session() as s:
            await s.execute(text(
                "INSERT INTO ext_demo_items (name) SELECT password_hash"
                " FROM (SELECT NULL AS password_hash) FULL JOIN users USING (password_hash)"
            ))
    async with session_scope() as core:
        assert (await core.execute(text("SELECT count(*) FROM ext_demo_items"))).scalar() == 0
    assert "Erweiterung demo: Verknüpfung mit USING oder NATURAL abgelehnt" in caplog.text


@pytest.mark.asyncio
async def test_words_that_only_contain_using_or_natural_stay_allowed(db):
    async with db.session() as s:
        await s.execute(text("INSERT INTO ext_demo_items (id, name) VALUES (1, 'pausing')"))
        rows = await s.execute(
            text("SELECT name AS causing, id AS natural_key, id AS using_since, id AS x_natural1, id AS using2,"
                 " id AS a2using, id AS x_using, id AS usingX FROM ext_demo_items WHERE name = :n"),
            {"n": "using"},
        )
        assert rows.all() == []


@pytest.mark.asyncio
async def test_the_core_may_join_with_using_and_natural(db):
    async with session_scope() as core:
        core.add(User(username="nico", password_hash="hash"))
    async with db.session() as s:
        await s.execute(text("SELECT count(*) FROM ext_demo_items"))
        async with session_scope() as core:
            rows = await core.execute(text("SELECT a.username FROM users a JOIN users b USING (id)"))
            assert rows.scalars().all() == ["nico"]
            natural = await core.execute(text("SELECT count(*) FROM users NATURAL JOIN (SELECT id FROM users)"))
            assert natural.scalar() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("option", [None, tables.TableGuard("x", ("",))], ids=["aus", "fremder-waechter"])
async def test_options_of_statement_or_connection_keep_the_guard(db, option):
    async with session_scope() as core:
        core.add(User(username="nico", password_hash="hash"))
    options = {tables.GUARD_OPTION: option}
    async with db.session() as s:
        with pytest.raises(DatabaseError, match=DENIED):
            await s.execute(text("SELECT password_hash FROM users"), execution_options=options)
    async with db.session() as s:
        connection = await s.connection(execution_options=options)
        with pytest.raises(DatabaseError, match=DENIED):
            await connection.execute(text("SELECT password_hash FROM users"))


@pytest.mark.asyncio
async def test_the_raw_connection_is_guarded_from_the_start(db):
    async with db.session() as s:
        raw = await (await s.connection()).get_raw_connection()
        with pytest.raises(sqlite3.DatabaseError, match=DENIED):
            await raw.driver_connection.execute("SELECT password_hash FROM users")
    async with session_scope() as core:
        assert (await core.execute(text("SELECT count(*) FROM users"))).scalar() == 0


@pytest.mark.asyncio
async def test_own_fts5_tables_work(db):
    async with session_scope() as core:
        try:
            await core.execute(text("CREATE VIRTUAL TABLE ext_demo_search USING fts5(body)"))
        except DatabaseError as exc:
            if "no such module: fts5" not in str(exc):
                raise
            pytest.skip("SQLite ohne FTS5")
    async with db.session() as s:
        await s.execute(text("INSERT INTO ext_demo_search (body) VALUES ('Netzteil mit Lüfter')"))
        hits = (await s.execute(text("SELECT body FROM ext_demo_search WHERE ext_demo_search MATCH 'Netzteil'"))).all()
    assert [h.body for h in hits] == ["Netzteil mit Lüfter"]
