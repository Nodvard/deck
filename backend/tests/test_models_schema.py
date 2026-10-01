"""Formalisiert, was vorher nur ad hoc geprueft wurde: das Kern-Schema erzeugt sich
dialektneutral (docs/00-DECISIONS.md D-02) und die Begruendungspflicht aus
docs/01-ARCHITECTURE.md §4/§7 ist im Schema selbst verankert (actions.reason NOT NULL),
nicht nur in Anwendungslogik."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_schema_has_27_core_tables(db_session: AsyncSession) -> None:
    def _table_names(sync_conn) -> set[str]:
        return set(inspect(sync_conn).get_table_names())

    names = await db_session.connection()
    names = await names.run_sync(_table_names)
    # 27 seit `custom_apps` (eigene App-Kacheln im Cockpit).
    assert len(names) == 27, names


@pytest.mark.asyncio
async def test_actions_reason_is_not_nullable(db_session: AsyncSession) -> None:
    def _reason_nullable(sync_conn) -> bool:
        cols = inspect(sync_conn).get_columns("actions")
        (reason_col,) = [c for c in cols if c["name"] == "reason"]
        return bool(reason_col["nullable"])

    conn = await db_session.connection()
    nullable = await conn.run_sync(_reason_nullable)
    assert nullable is False
