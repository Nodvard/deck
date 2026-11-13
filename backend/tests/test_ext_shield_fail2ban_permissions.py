"""Persönliche Rechte für Fail2ban bei voller Automatik."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from nodvard_deck import config
from nodvard_deck.api.deps import get_session
from nodvard_deck.core import gate, security, ssh
from nodvard_deck.db import utcnow
from nodvard_deck.db.session import session_scope
from nodvard_deck.ext.context import build_context
from nodvard_deck.ext.runtime import (
    LoadedExtension,
    get_extension_runtime,
    reset_extension_runtime,
)
from nodvard_deck.models import Action, Host, RefreshToken, Role, RolePermission, User
from nodvard_deck.services import settings as settings_service
from nodvard_sdk import ExtensionManifest
from nodvard_sdk.capabilities import ActionExecutor
from sqlalchemy import select

EXT = Path(__file__).resolve().parents[2] / "extensions" / "shield"


@pytest.fixture
async def shield(monkeypatch, test_settings):
    monkeypatch.syspath_prepend(str(EXT / "src"))
    monkeypatch.setattr(config, "_settings", test_settings)
    from nodvard_deck_ext_shield import defender_api

    reset_extension_runtime()
    try:
        yield defender_api
    finally:
        await gate.shutdown_running()
        reset_extension_runtime()
        for name in list(sys.modules):
            if name.startswith("nodvard_deck_ext_shield"):
                del sys.modules[name]


@pytest.mark.parametrize(
    ("permissions", "expected"),
    [(["soc.manage"], 403), (["hosts.execute"], 403), (["soc.manage", "hosts.execute"], 200)],
)
async def test_fail2ban_requires_both_personal_permissions(
    shield, db_session, test_settings, tmp_path, monkeypatch, permissions, expected,
):
    from nodvard_deck_ext_shield import fail2ban, intrusion
    from nodvard_deck_ext_shield.defender import Defender
    from test_ext_shield_fail2ban_config import READ

    user = User(
        username="schutz", password_hash="unused",
        roles=[Role(name="schutz", permissions=[RolePermission(permission=p) for p in permissions])],
    )
    host = Host(name="pi", address="192.0.2.2")
    db_session.add_all([user, host])
    await db_session.flush()
    login = RefreshToken(
        user_id=user.id, token_hash=security.hash_opaque_token(user.id), expires_at=utcnow() + timedelta(days=1),
    )
    db_session.add(login)
    await settings_service.set_global(db_session, "autonomy.mode", "full")
    await settings_service.set_global(db_session, "autonomy.max_risk", "medium")
    await db_session.commit()
    token = security.create_jwt(
        subject=user.id, token_type="access", secret=test_settings.get_or_create_jwt_secret(),
        ttl_seconds=60, extra_claims={"sid": login.id},
    )

    runtime = get_extension_runtime()
    manifest = ExtensionManifest(
        id="shield", name="Shield", version="0.1.0", api_version="0.1", entrypoint="m:E",
        permissions=["hosts.read", "hosts.execute", "audit.write"],
    )
    loaded = LoadedExtension(manifest=manifest, instance=None, ctx=None, granted_permissions=manifest.permissions)
    ctx = build_context(runtime, loaded, manifest, manifest.permissions, tmp_path / "shield", test_settings)
    loaded.ctx = ctx
    calls = []

    async def run(connection, command, **kwargs):
        calls.append(command)
        output = "203.0.113.9\n" if command == intrusion.DECK_ADDRESS_COMMAND else READ
        return 0, output, ""

    monkeypatch.setattr(ctx.exec, "_connection", AsyncMock(return_value=object()))
    monkeypatch.setattr(ssh, "run", run)
    defender = Defender(ctx)
    for spec in shield.ACTION_SPECS:
        ctx.actions.register(spec)
    runtime.capabilities.provide("shield", ActionExecutor, shield.DefenderExecutor(ctx, defender))
    _, manage = shield.build_routers(ctx, defender)
    ctx.api.include_router(manage, permission="soc.manage")
    app = FastAPI()
    app.include_router(loaded.router, prefix=manifest.api_prefix)

    async def session():
        async with session_scope() as current:
            yield current

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[config.get_settings] = lambda: test_settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/ext/shield/defender/hosts/{host.id}/fail2ban",
            headers={"Authorization": f"Bearer {token}"},
            json={"bantime": 3600, "findtime": 600, "maxretry": 3, "ignoreip": ["192.0.2.0/24"], "jails": ["sshd"]},
        )
    await gate.shutdown_running()
    assert response.status_code == expected, response.text
    async with session_scope() as current:
        actions = list((await current.scalars(select(Action))).all())
    if expected == 403:
        assert actions == []
        assert calls == []
    else:
        assert len(actions) == 1
        assert actions[0].status == "succeeded"
        assert actions[0].gate_decision["rule"] == "autonomy:full"
        assert actions[0].proposed_by_id == user.id
        assert calls == [
            intrusion.DECK_ADDRESS_COMMAND, fail2ban.read_command(),
            intrusion.DECK_ADDRESS_COMMAND, fail2ban.read_command(), actions[0].payload["command"],
        ]
