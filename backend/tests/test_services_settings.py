"""Globale Einstellungen -- services.settings (docs/03-DATA-MODEL.md §9,
siehe dortiger Modul-Docstring)."""

from __future__ import annotations

import pytest

from nodvard_deck.services import settings as settings_service


@pytest.mark.asyncio
async def test_get_global_returns_default_when_unset(db_session):
    assert await settings_service.get_global(db_session, "autonomy.mode", "propose") == "propose"
    assert await settings_service.get_global(db_session, "autonomy.mode") is None


@pytest.mark.asyncio
async def test_set_then_get_roundtrip(db_session):
    await settings_service.set_global(db_session, "autonomy.mode", "full", updated_by_user_id="user-1")
    assert await settings_service.get_global(db_session, "autonomy.mode") == "full"


@pytest.mark.asyncio
async def test_set_global_overwrites_existing_value(db_session):
    await settings_service.set_global(db_session, "autonomy.max_risk", "low")
    await settings_service.set_global(db_session, "autonomy.max_risk", "high")
    assert await settings_service.get_global(db_session, "autonomy.max_risk") == "high"


@pytest.mark.asyncio
async def test_set_global_supports_list_values_roundtrip(db_session):
    await settings_service.set_global(db_session, "security.deny_patterns", ["custom-pattern-\\d+"])
    assert await settings_service.get_global(db_session, "security.deny_patterns") == ["custom-pattern-\\d+"]


@pytest.mark.asyncio
async def test_list_global_returns_all_stored_keys(db_session):
    await settings_service.set_global(db_session, "autonomy.mode", "full")
    await settings_service.set_global(db_session, "autonomy.max_risk", "medium")
    result = await settings_service.list_global(db_session)
    assert result == {"autonomy.mode": "full", "autonomy.max_risk": "medium"}
