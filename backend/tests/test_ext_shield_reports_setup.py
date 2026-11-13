"""Wochenberichte sind beim Start und nach Einstellungsänderungen erreichbar."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from nodvard_deck.core.cron import cron_trigger

EXT = Path(__file__).resolve().parents[2] / "extensions" / "shield"


@pytest.fixture
def shield(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT / "src"))
    import nodvard_deck_ext_shield

    monkeypatch.setattr(nodvard_deck_ext_shield.UpdateCenter, "schedule_catch_up", lambda _: None)
    yield nodvard_deck_ext_shield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


async def test_setup_registers_read_routes_and_replaces_the_report_schedule(shield):
    ctx = MagicMock()
    ctx.settings.get = AsyncMock(return_value={})
    ctx.scheduler.validate_schedule.side_effect = cron_trigger
    ctx.scheduler.register_job = AsyncMock()
    extension = shield.Extension()
    await extension.setup(ctx)

    jobs = {call.args[0].id: call.args[0] for call in ctx.scheduler.register_job.call_args_list}
    assert jobs["weekly-report"].enabled is True
    assert jobs["weekly-report"].schedule == "0 8 * * 1"
    routes = {
        route.path: call.kwargs["permission"]
        for call in ctx.api.include_router.call_args_list
        for route in call.args[0].routes
    }
    assert routes["/reports"] == routes["/reports/{report_id}"] == "soc.read"

    ctx.settings.get.return_value = {"weekly_report_cron": "15 9 * * 5", "weekly_report_enabled": False}
    ctx.scheduler.register_job.reset_mock()
    await extension.on_settings_changed(ctx, ctx.settings.get.return_value)
    jobs = {call.args[0].id: call.args[0] for call in ctx.scheduler.register_job.call_args_list}
    assert jobs["weekly-report"].enabled is False

    ctx.settings.get.return_value["weekly_report_enabled"] = True
    await extension.on_settings_changed(ctx, ctx.settings.get.return_value)
    assert ctx.scheduler.register_job.call_args.args[0].schedule == "15 9 * * 5"


def test_weekly_report_settings_and_notification_are_discoverable():
    schema = json.loads((EXT / "settings.schema.json").read_text())["properties"]
    assert schema["weekly_report_enabled"]["default"] is True
    assert schema["weekly_report_cron"]["default"] == "0 8 * * 1"
    assert schema["weekly_report_cron"]["x-widget"] == "schedule"
    types = json.loads((EXT / "notification-types.json").read_text())
    assert types["shield.weekly_report"] == "Shield-Wochenbericht"
