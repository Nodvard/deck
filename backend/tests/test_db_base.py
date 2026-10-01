"""`db.refresh_relationships()` -- docs/00-DECISIONS.md D-12.

Regressionstest fuer den dreimal live gefundenen `lazy="selectin"`-Fallstrick: eine
Relationship auf einem frisch per `session.add()` angelegten Objekt ist nicht geladen
und crasht beim Zugriff mit `MissingGreenlet`, wenn sie nicht vorher explizit
aufgefrischt wird.
"""

from __future__ import annotations

import pytest

from nodvard_deck.db import refresh_relationships
from nodvard_deck.models import Role, RolePermission


@pytest.mark.asyncio
async def test_refresh_relationships_loads_collection_on_freshly_added_object(db_session):
    role = Role(name="fixture-role", is_builtin=False)
    db_session.add(role)
    await db_session.flush()

    db_session.add(RolePermission(role_id=role.id, permission="hosts.read"))
    await db_session.flush()

    await refresh_relationships(db_session, role, "permissions")

    assert {p.permission for p in role.permissions} == {"hosts.read"}


@pytest.mark.asyncio
async def test_reading_relationship_without_refresh_would_crash_without_it(db_session):
    """Haelt die Regression konkret fest: OHNE refresh_relationships() ist die
    Collection auf dem frisch angelegten Objekt nicht geladen. Dieser Test greift
    nicht direkt auf `.permissions` zu (das wuerde ausserhalb eines echten Async-
    Greenlet-Kontexts nicht zuverlaessig denselben Fehler reproduzieren) -- er
    dokumentiert stattdessen ueber `inspect`, dass das Attribut vor dem Refresh
    tatsaechlich als "nicht geladen" gilt."""
    from sqlalchemy import inspect

    role = Role(name="fixture-role-2", is_builtin=False)
    db_session.add(role)
    await db_session.flush()
    db_session.add(RolePermission(role_id=role.id, permission="hosts.read"))
    await db_session.flush()

    state = inspect(role)
    assert "permissions" in state.unloaded

    await refresh_relationships(db_session, role, "permissions")
    state_after = inspect(role)
    assert "permissions" not in state_after.unloaded
