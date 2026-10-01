"""Dashboard-Layout-Persistenz (docs/03-DATA-MODEL.md §9)."""

from __future__ import annotations

import pytest

from nodvard_deck.services import dashboard as dashboard_service


@pytest.mark.asyncio
async def test_get_or_create_default_is_idempotent(db_session):
    first = await dashboard_service.get_or_create_default(db_session, user_id="u1")
    second = await dashboard_service.get_or_create_default(db_session, user_id="u1")
    assert first.id == second.id
    assert first.is_default is True
    assert first.items == []


@pytest.mark.asyncio
async def test_different_users_get_different_default_layouts(db_session):
    a = await dashboard_service.get_or_create_default(db_session, user_id="u1")
    b = await dashboard_service.get_or_create_default(db_session, user_id="u2")
    assert a.id != b.id


@pytest.mark.asyncio
async def test_get_layout_scoped_to_owner(db_session):
    layout = await dashboard_service.get_or_create_default(db_session, user_id="u1")
    assert await dashboard_service.get_layout(db_session, layout.id, user_id="u1") is not None
    assert await dashboard_service.get_layout(db_session, layout.id, user_id="u2") is None
    assert await dashboard_service.get_layout(db_session, "does-not-exist", user_id="u1") is None


@pytest.mark.asyncio
async def test_update_layout_name_and_items(db_session):
    layout = await dashboard_service.get_or_create_default(db_session, user_id="u1")
    items = [{"widget_id": "hello", "ext_id": "hello-world", "x": 0, "y": 0, "w": 2, "h": 1, "config": {}}]
    updated = await dashboard_service.update_layout(db_session, layout, name="Mein Dashboard", items=items)
    assert updated.name == "Mein Dashboard"
    assert updated.items == items


@pytest.mark.asyncio
async def test_list_layouts_only_returns_own(db_session):
    await dashboard_service.get_or_create_default(db_session, user_id="u1")
    await dashboard_service.get_or_create_default(db_session, user_id="u2")
    rows = await dashboard_service.list_layouts(db_session, user_id="u1")
    assert len(rows) == 1
    assert rows[0].user_id == "u1"
