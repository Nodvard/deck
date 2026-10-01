"""Testet `require_permission()` als tatsaechliche FastAPI-Dependency (nicht nur die
darunterliegende reine Funktion, die schon in test_rbac.py/test_auth_service.py
abgedeckt ist) -- gebaut gegen eine eigene, isolierte pve1-App statt der echten
`nodvard_deck.main.app`, damit kein Test-Route dauerhaft im echten App-Objekt haengen bleibt.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from nodvard_deck.api.deps import get_session, require_permission
from nodvard_deck.config import Settings, get_settings
from nodvard_deck.core import security
from nodvard_deck.models import User


@pytest_asyncio.fixture
async def rbac_client(db_session, tmp_path) -> AsyncIterator[AsyncClient]:
    app = FastAPI()

    @app.get("/needs-low", dependencies=[Depends(require_permission("actions.approve:low"))])
    async def needs_low() -> dict:
        return {"ok": True}

    @app.get("/needs-high", dependencies=[Depends(require_permission("actions.approve:high"))])
    async def needs_high() -> dict:
        return {"ok": True}

    test_settings = Settings(
        data_dir=tmp_path,
        master_key_path=tmp_path / "master.key",
        jwt_secret_path=tmp_path / "jwt_secret.key",
    )

    async def _session():
        yield db_session

    def _settings():
        return test_settings

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = _settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.__dict__["_nodvard_settings"] = test_settings
        yield ac


def _token_for(user: User, settings: Settings) -> str:
    return security.create_jwt(
        subject=user.id,
        token_type="access",
        secret=settings.get_or_create_jwt_secret(),
        ttl_seconds=60,
    )


@pytest.mark.asyncio
async def test_operator_allowed_low_denied_high(db_session, rbac_client):
    from nodvard_deck.services.auth import ensure_builtin_roles

    roles = await ensure_builtin_roles(db_session)
    user = User(username="op", password_hash="x")
    user.roles.append(roles["operator"])
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user, attribute_names=["roles"])

    token = _token_for(user, rbac_client._nodvard_settings)
    headers = {"Authorization": f"Bearer {token}"}

    r_low = await rbac_client.get("/needs-low", headers=headers)
    assert r_low.status_code == 200

    r_high = await rbac_client.get("/needs-high", headers=headers)
    assert r_high.status_code == 403


@pytest.mark.asyncio
async def test_owner_bypasses_every_check(db_session, rbac_client):
    user = User(username="owner", password_hash="x", is_owner=True)
    db_session.add(user)
    await db_session.flush()

    token = _token_for(user, rbac_client._nodvard_settings)
    headers = {"Authorization": f"Bearer {token}"}

    assert (await rbac_client.get("/needs-low", headers=headers)).status_code == 200
    assert (await rbac_client.get("/needs-high", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_viewer_denied_everything_that_needs_approval(db_session, rbac_client):
    from nodvard_deck.services.auth import ensure_builtin_roles

    roles = await ensure_builtin_roles(db_session)
    user = User(username="viewer1", password_hash="x")
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.flush()
    await db_session.refresh(user, attribute_names=["roles"])

    token = _token_for(user, rbac_client._nodvard_settings)
    headers = {"Authorization": f"Bearer {token}"}

    assert (await rbac_client.get("/needs-low", headers=headers)).status_code == 403
