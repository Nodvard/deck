"""Nodvard Shield: ein kaputter Zeitplan in den Einstellungen legt den Start nicht mehr lahm."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from nodvard_deck.core.cron import cron_trigger

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))

from nodvard_deck_ext_shield import defender_api
from nodvard_deck_ext_shield.ids import LOGGER


class _Settings:
    def __init__(self, values: dict) -> None:
        self._values = values

    async def get(self) -> dict:
        return dict(self._values)


class _Scheduler:
    def __init__(self) -> None:
        self.jobs: list = []

    def validate_schedule(self, schedule: str) -> None:
        cron_trigger(schedule)

    async def register_job(self, spec) -> None:
        if spec.enabled:
            cron_trigger(spec.schedule)  # wie der Kern: ein kaputter Zeitplan wirft hier
        self.jobs.append(spec)


class _Notify:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, notification, **_kwargs) -> None:
        self.sent.append(notification)


class _Ctx:
    def __init__(self, values: dict) -> None:
        self.settings = _Settings(values)
        self.scheduler = _Scheduler()
        self.notify = _Notify()


class _Updates:
    def schedule_catch_up(self) -> None:
        pass


@pytest.mark.asyncio
async def test_a_broken_schedule_from_the_settings_falls_back_to_the_default(caplog):
    """Frueher warf `register_job()` beim ersten kaputten Zeitplan, `setup()` brach ab, und die ganze
    Erweiterung stand auf 'error' (keine Scans, kein Waechter, kein Executor)."""
    logging.getLogger(LOGGER).disabled = False
    ctx = _Ctx({"quick_scan_cron": "@daily", "deep_scan_cron": "0 24 * * *", "audit_cron": "0 3 * * 0"})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await defender_api.register_jobs(ctx, object(), _Updates())

    by_id = {j.id: j for j in ctx.scheduler.jobs}
    assert by_id["defender-quick"].schedule == defender_api.DEFAULT_CRONS["quick"]
    assert by_id["defender-deep"].schedule == defender_api.DEFAULT_CRONS["deep"]
    assert by_id["defender-audit"].schedule == "0 3 * * 0"  # gueltig: bleibt
    assert len(by_id) == 7  # alle Jobs angemeldet (ohne Guard)
    assert [n.title for n in ctx.notify.sent] == [
        "Zeitplan „Virenschutz: Schnellscan“ ist ungültig",
        "Zeitplan „Virenschutz: Tiefenscan“ ist ungültig",
    ]
    assert "@daily" in ctx.notify.sent[0].body and defender_api.DEFAULT_CRONS["quick"] in ctx.notify.sent[0].body
    records = [r for r in caplog.records if "shield_job_schedule_invalid" in r.getMessage()]
    assert len(records) == 2 and all(r.exc_info is None for r in records)  # Warnung, kein Traceback


@pytest.mark.asyncio
async def test_a_disabled_job_with_a_broken_schedule_needs_no_fallback():
    ctx = _Ctx({"quick_scan_cron": "@daily", "quick_scan_enabled": False})
    await defender_api.register_jobs(ctx, object(), _Updates())
    assert ctx.notify.sent == []
    assert {j.id for j in ctx.scheduler.jobs} >= {"defender-quick", "defender-deep"}
