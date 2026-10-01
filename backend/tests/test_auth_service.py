"""services.auth: Rollen-Seeding, Permission-Aggregation, is_owner-Sonderfall.

`ensure_builtin_roles` ist die Funktion, die beim Boot-Test live mit MissingGreenlet
crashte (Zugriff auf `role.permissions` eines gerade erst erzeugten, nicht per SELECT
geladenen Role-Objekts). Diese Tests fixieren das behobene Verhalten als Regression.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from nodvard_deck.core.rbac import BUILTIN_ROLES
from nodvard_deck.models import Role, User
from nodvard_deck.services import auth as auth_service


@pytest.mark.asyncio
async def test_ensure_builtin_roles_creates_all_three(db_session):
    roles = await auth_service.ensure_builtin_roles(db_session)
    assert set(roles.keys()) == {"admin", "operator", "viewer"}
    for name, role in roles.items():
        perms = {p.permission for p in role.permissions}
        assert perms == set(BUILTIN_ROLES[name])


@pytest.mark.asyncio
async def test_ensure_builtin_roles_is_idempotent(db_session):
    """Zweiter Aufruf darf weder Duplikate anlegen noch crashen -- genau der Aufruf,
    der beim Boot-Test (Lifespan laeuft bei jedem Start) mehrfach passiert."""
    await auth_service.ensure_builtin_roles(db_session)
    await auth_service.ensure_builtin_roles(db_session)

    result = await db_session.execute(select(Role).where(Role.name == "admin"))
    admins = result.scalars().all()
    assert len(admins) == 1
    assert {p.permission for p in admins[0].permissions} == set(BUILTIN_ROLES["admin"])


@pytest.mark.asyncio
async def test_owner_gets_wildcard_permissions(db_session):
    user = User(username="owner1", password_hash="x", is_owner=True)
    db_session.add(user)
    await db_session.flush()

    assert auth_service.user_permissions(user) == ["*"]
    assert auth_service.user_has_permission(user, "irgendwas.beliebiges")


@pytest.mark.asyncio
async def test_non_owner_permissions_come_from_assigned_roles(db_session):
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="operator1", password_hash="x", is_owner=False)
    user.roles.append(roles["operator"])
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user, attribute_names=["roles"])

    perms = auth_service.user_permissions(user)
    assert "hosts.execute" in perms
    assert not auth_service.user_has_permission(user, "actions.approve:high")
    assert auth_service.user_has_permission(user, "actions.approve:low")


@pytest.mark.asyncio
async def test_user_without_roles_has_no_permissions(db_session):
    """`user.roles` bewusst NICHT vor dem Lesen geflusht: user_permissions() ist reine
    Logik auf dem in-memory-Objekt, eine Persistenz ist fuer diese Aussage nicht noetig
    -- und ein Flush ohne anschliessenden expliziten Refresh wuerde denselben
    MissingGreenlet-Fallstrick ausloesen wie bei ensure_builtin_roles (siehe dortiger
    Kommentar): auf einem bereits geflushten User waere `.roles` ungeladen und ein
    synchroner Lesezugriff wuerde im Async-Kontext crashen."""
    user = User(username="niemand", password_hash="x", is_owner=False)

    assert auth_service.user_permissions(user) == []
    assert not auth_service.user_has_permission(user, "hosts.read")


@pytest.mark.asyncio
async def test_bootstrap_owner_fails_when_user_exists(db_session):
    await auth_service.bootstrap_owner(db_session, username="erster", password="whatever123")
    with pytest.raises(auth_service.UsersAlreadyExist):
        await auth_service.bootstrap_owner(db_session, username="zweiter", password="whatever123")
