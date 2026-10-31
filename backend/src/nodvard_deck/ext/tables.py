"""Tabellen einer Extension: Präfix-Prüfung beim Laden und SQLite-Wächter für jede Anweisung über `ctx.db`."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.util import await_only

GUARD_OPTION = "nodvard_table_guard"
_LOCK = "nodvard_table_guard_lock"

_log = logging.getLogger(__name__)

# Alles andere (PRAGMA, ATTACH, CREATE, DROP, ALTER, ANALYZE, virtuelle Tabellen) lehnt der Wächter ab.
_FREE_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
    sqlite3.SQLITE_RECURSIVE,
})
_TABLE_ACTIONS = frozenset({sqlite3.SQLITE_READ, sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE})
# `fts3_tokenizer` verrät eine Speicheradresse, `load_extension` lädt Code (ist ohnehin abgeschaltet).
_DENIED_FUNCTIONS = frozenset({"fts3_tokenizer", "load_extension"})
# Spalten, die SQLite für `USING (…)` und `NATURAL` selbst bildet, legt es dem Autorisierer nie vor: Mit
# `FULL JOIN users USING (password_hash)` stünde der Hash ungeprüft im Ergebnis. ORM und Core erzeugen beides nie.
_UNCHECKED_JOIN = re.compile(r"(?<![A-Za-z0-9_])(?:using|natural)(?![A-Za-z0-9_])", re.IGNORECASE)
_ACTION_NAMES = {
    getattr(sqlite3, f"SQLITE_{name}"): name
    for name in (
        "CREATE_INDEX", "CREATE_TABLE", "CREATE_TEMP_INDEX", "CREATE_TEMP_TABLE", "CREATE_TEMP_TRIGGER",
        "CREATE_TEMP_VIEW", "CREATE_TRIGGER", "CREATE_VIEW", "DROP_INDEX", "DROP_TABLE", "DROP_TEMP_INDEX",
        "DROP_TEMP_TABLE", "DROP_TEMP_TRIGGER", "DROP_TEMP_VIEW", "DROP_TRIGGER", "DROP_VIEW", "PRAGMA", "ATTACH",
        "DETACH", "ALTER_TABLE", "REINDEX", "ANALYZE", "CREATE_VTABLE", "DROP_VTABLE",
    )
}


class TablePrefixViolation(Exception):
    pass


def validate_table_prefix(
    ext_id: str, table_names: Iterable[str], *, expected_prefix: str | tuple[str, ...]
) -> None:
    """Jede Tabelle muss mit einem erlaubten Präfix beginnen (nach einer Umbenennung auch die der alten Kennungen)."""
    prefixes = (expected_prefix,) if isinstance(expected_prefix, str) else tuple(expected_prefix)
    violations = [name for name in table_names if not name.startswith(prefixes)]
    if violations:
        allowed = " oder ".join(f"'{p}'" for p in prefixes)
        raise TablePrefixViolation(
            f"Extension '{ext_id}': Tabellen {sorted(violations)} verletzen den "
            f"Pflicht-Präfix {allowed} (docs/02-EXTENSION-API.md §7)."
        )


class TableGuard:
    """SQLite-Autorisierer einer `ctx.db`-Session: Lesen und Schreiben nur in den Tabellen mit Präfix der Extension."""

    def __init__(self, ext_id: str, prefixes: tuple[str, ...]) -> None:
        self.ext_id = ext_id
        self.prefixes = prefixes
        self.open = True

    def __call__(self, action: int, arg1: str | None, arg2: str | None, _db: str | None, _source: str | None) -> int:
        # Nach dem Ende der Session kann der Wächter noch auf der Verbindung im Pool stehen; der Kern ist dann frei.
        if not self.open or action in _FREE_ACTIONS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            if arg2 not in _DENIED_FUNCTIONS:
                return sqlite3.SQLITE_OK
            what = f"Funktion {arg2}"
        elif action == sqlite3.SQLITE_PRAGMA and arg1 == "data_version" and arg2 is None:
            return sqlite3.SQLITE_OK  # liest nur einen Zähler; FTS5 fragt ihn bei jeder Abfrage selbst ab
        elif action in _TABLE_ACTIONS:
            if arg1 is not None and arg1.startswith(self.prefixes):
                return sqlite3.SQLITE_OK
            what = f"Tabelle {arg1}"
        else:
            what = f"SQLite-Aktion {_ACTION_NAMES.get(action, action)}" + (f" ({arg1})" if arg1 else "")
        self.refused(what)
        return sqlite3.SQLITE_DENY

    def refused(self, what: str, rule: str | None = None) -> None:
        if rule is None:
            rule = "ctx.db darf nur an Tabellen " + " oder ".join(f"{p}*" for p in self.prefixes)
        _log.warning("Erweiterung %s: %s abgelehnt, %s", self.ext_id, what, rule)


def _connection_lock(pooled: Any) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    held = pooled.info.get(_LOCK)
    if held is None or held[0] is not loop:
        held = (loop, asyncio.Lock())
        pooled.info[_LOCK] = held
    return held[1]


def _guard_of(dialect: Any, engine: Any) -> TableGuard | None:
    """Der Wächter einer `ctx.db`-Verbindung; `None` für den Kern, synchrone Engines und Datenbanken ohne Wächter."""
    if not dialect.is_async:
        return None  # `ctx.db` nutzt nur die asynchrone Engine
    # Aus der Engine der Session, nicht aus den Optionen von Verbindung oder Anweisung: Die setzt das Modul selbst.
    guard = engine.get_execution_options().get(GUARD_OPTION)
    if guard is not None and dialect.name != "sqlite":
        raise RuntimeError(f"Erweiterung {guard.ext_id}: ctx.db prüft die Tabellen bisher nur unter SQLite")
    return guard


def _run_with(pooled: Any, guard: TableGuard | None, execute: Callable[[], None]) -> None:
    # Umstellen und Ausführen sind zwei Aufträge an den Thread von aiosqlite. Teilen sich Sessions eine Verbindung
    # (StaticPool), stellte eine andere sonst dazwischen ihren Wächter ein.
    lock = _connection_lock(pooled)
    await_only(lock.acquire())
    try:
        if pooled.info.get(GUARD_OPTION) is not guard:
            # `set_authorizer` verwirft zugleich die vorbereiteten Anweisungen der Verbindung. Sonst bekäme eine
            # Extension, die denselben SQL-Text wie der Kern schickt, dessen schon geprüfte Anweisung aus dem Cache.
            await_only(pooled.driver_connection.set_authorizer(guard))
            pooled.info[GUARD_OPTION] = guard
        execute()
    finally:
        lock.release()


def _execute_guarded(context: Any, statement: str, execute: Callable[[], None]) -> bool:
    """Führt die Anweisung mit dem Wächter ihrer Session aus, für den Kern ohne Wächter."""
    guard = _guard_of(context.dialect, context.root_connection.engine)
    if not context.dialect.is_async or context.dialect.name != "sqlite":
        return False
    if guard is not None:
        if not guard.open:
            raise RuntimeError(f"Erweiterung {guard.ext_id}: Session nach dem Ende von ctx.db.session() benutzt")
        if _UNCHECKED_JOIN.search(statement):
            guard.refused("Verknüpfung mit USING oder NATURAL", "SQLite prüft deren Spalten nicht, JOIN … ON geht")
            raise sqlite3.DatabaseError("not authorized: ctx.db erlaubt kein USING und kein NATURAL")
    _run_with(context.root_connection.connection, guard, execute)
    return True


@event.listens_for(Engine, "engine_connect")
def _engine_connect(connection: Any) -> None:
    # Schon beim Ausleihen: Auch was ein Modul über `get_raw_connection()` am Treiber ausführt, prüft dann SQLite.
    # Die Sperre für USING und NATURAL liest dagegen den SQL-Text und sieht nur Anweisungen über SQLAlchemy.
    if connection.dialect.name != "sqlite":
        return  # ein Fehler hier ließe die Verbindung ausgeliehen; ohne SQLite meldet sich die erste Anweisung
    guard = _guard_of(connection.dialect, connection.engine)
    if guard is not None:
        _run_with(connection.connection, guard, lambda: None)


@event.listens_for(Engine, "do_execute")
def _do_execute(cursor: Any, statement: str, parameters: Any, context: Any) -> bool:
    return _execute_guarded(
        context, statement, lambda: context.dialect.do_execute(cursor, statement, parameters, context)
    )


@event.listens_for(Engine, "do_execute_no_params")
def _do_execute_no_params(cursor: Any, statement: str, context: Any) -> bool:
    return _execute_guarded(
        context, statement, lambda: context.dialect.do_execute_no_params(cursor, statement, context)
    )


@event.listens_for(Engine, "do_executemany")
def _do_executemany(cursor: Any, statement: str, parameters: Any, context: Any) -> bool:
    return _execute_guarded(
        context, statement, lambda: context.dialect.do_executemany(cursor, statement, parameters, context)
    )
