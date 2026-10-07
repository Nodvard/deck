"""Schutzwert und Hinweise bei veralteten Virensignaturen, letztes erfolgreiches Audit."""

from __future__ import annotations

import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))

from nodvard_deck_ext_shield import protection
from nodvard_deck_ext_shield.defender import (
    Defender,
    _score,
    build_briefing,
)
from nodvard_deck_ext_shield.models import AuditRecord, Base
from nodvard_sdk import Host
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DAY = 86400


@pytest.mark.parametrize(("text", "expected"), [
    ("Wed Sep 24 08:23:12 2026", datetime(2026, 9, 24, 8, 23, 12, tzinfo=UTC)),
    ("Wed Sep  4 08:23 2026", datetime(2026, 9, 4, 8, 23, tzinfo=UTC)),
    ("Thu Sep 25 2026", datetime(2026, 9, 25, tzinfo=UTC)),
    ("Fri Oct  2 06:00:01 UTC 2026", datetime(2026, 10, 2, 6, 0, 1, tzinfo=UTC)),
])
def test_signature_date_is_read(text, expected):
    assert protection.parse_signature_date(text) == expected.timestamp()


@pytest.mark.parametrize("text", [None, "", "gestern", "Wed Foo 24 08:23:12 2026", "Wed Feb 31 08:23:12 2026", "Wed Sep 24 25:00:00 2026"])
def test_unreadable_signature_date_is_unknown(text):
    assert protection.parse_signature_date(text) is None


def test_signature_age_and_stale_threshold():
    now = 1_800_000_000.0
    assert protection.signature_info(now - 7 * DAY, now) == {"signature_age_days": 7, "signature_stale": False}
    assert protection.signature_info(now - 7 * DAY - 60, now) == {"signature_age_days": 7, "signature_stale": True}
    assert protection.signature_info(now - 36 * DAY, now) == {"signature_age_days": 36, "signature_stale": True}
    assert protection.signature_info(now + 3600, now) == {"signature_age_days": 0, "signature_stale": False}  # Uhr geht vor
    assert protection.signature_info(None, now) == {"signature_age_days": None, "signature_stale": None}


def _row(name: str, stale: bool | None, days: int | None = None, **extra):
    return {
        "host_id": f"h-{name}", "host_name": name, "reachable": True, "clamav_installed": True, "signature_stale": stale,
        "signature_age_days": days, "last_scan": {"status": "clean", "started_at": time.time() - 3600}, **extra,
    }


def test_stale_signatures_pull_the_score_out_of_the_good_range():
    fresh = [_row(f"s{i}", False, 1) for i in range(5)]
    stale = [_row(f"s{i}", True, 14 + 7 * i) for i in range(5)]
    good = _score(fresh, 0, [60])
    assert good >= 80  # Ausgangslage: gut geschuetzt
    assert _score(stale, 0, [60]) == good - protection.STALE_PENALTY_POINTS < 80


def test_score_penalty_is_proportional_and_unknown_age_costs_little():
    rows = [_row(f"s{i}", False, 1) for i in range(9)] + [_row("alt", True, 20)]
    assert protection.signature_penalty(rows) == 3
    assert protection.signature_penalty([_row("a", None)]) == protection.UNKNOWN_PENALTY_POINTS
    assert protection.signature_penalty([_row("a", None, reachable=False)]) == 0
    assert protection.signature_penalty([{"host_name": "x", "clamav_installed": False}]) == 0
    assert protection.signature_penalty([]) == 0


def test_stale_clamav_never_scores_below_no_clamav():
    """Abzug anteilig nach allen Servern: ClamAV mit alten Signaturen ist besser als gar kein ClamAV."""
    others = [{"host_id": f"h-n{i}", "host_name": f"n{i}", "reachable": True, "clamav_installed": False, "last_scan": None} for i in range(4)]
    with_stale = others + [_row("alt", True, 30)]
    without = others + [{"host_id": "h-alt", "host_name": "alt", "reachable": True, "clamav_installed": False, "last_scan": None}]
    assert protection.signature_penalty(with_stale) == 6
    assert _score(with_stale, 0, [60]) > _score(without, 0, [60])


def test_attention_items_name_the_action():
    rows = [_row("a", True, 36), _row("b", False, 2), _row("c", None), {"host_id": "h-d", "host_name": "d", "clamav_installed": False}]
    items = protection.attention_items(rows)
    assert [(i["host_id"], i["kind"], i["tone"]) for i in items] == [("h-a", "signatures", "warn"), ("h-c", "signatures", "info")]
    assert items[0]["title"] == "a: Virensignaturen sind 5 Wochen alt"
    assert items[0]["action_label"] == "Signaturen aktualisieren"
    assert "unbekannt" in items[1]["title"]


def test_briefing_lists_stale_signatures_but_not_unknown_age():
    overview = {
        "summary": {"score": 58, "protected": 2, "hosts": 2, "open_threats": 0, "quarantined": 0, "findings_30d": 0, "avg_hardening": None},
        "hosts": [_row("pi", True, 10, freshclam_active=True), _row("nas", None, freshclam_active=True)],
    }
    _title, body, level = build_briefing(overview, [])
    assert "pi: Signaturen 10 Tage alt" in body and "nas" not in body
    assert level == "warning"


def test_briefing_names_stale_signatures_once_per_host():
    overview = {
        "summary": {"score": 40, "protected": 2, "hosts": 2, "open_threats": 0, "quarantined": 0, "findings_30d": 0, "avg_hardening": None},
        "hosts": [_row("pi", True, 21, freshclam_active=False), _row("nas", False, 1, freshclam_active=False)],
    }
    _title, body, _level = build_briefing(overview, [])
    todo = next(line for line in body.splitlines() if line.startswith("Zu tun: "))
    assert todo == "Zu tun: pi: Signaturen 3 Wochen alt, Signatur-Update aus; nas: Signatur-Update aus"


class _Ctx:
    def __init__(self, sm, stdout: str):
        self._sm = sm
        self._stdout = stdout
        host = Host(id="h1", name="pi", display_name="Pi", address="10.0.0.2")
        self.settings = SimpleNamespace(get=self._settings)
        self.hosts = SimpleNamespace(list=self._list, get=self._get)
        self.exec = SimpleNamespace(run=self._run)
        self.db = SimpleNamespace(session=self._session)
        self._host = host

    async def _settings(self):
        return {}

    async def _list(self, tag=None):
        return [self._host]

    async def _get(self, host_id):
        return self._host

    async def _run(self, host, command, timeout_s=60):
        return SimpleNamespace(exit_code=0, stdout=self._stdout, stderr="", duration_ms=5)

    @asynccontextmanager
    async def _session(self):
        async with self._sm() as s:
            yield s
            await s.commit()


def _status(sig_date: str) -> str:
    return f"@@clam\nClamAV 1.0.7/27410/{sig_date}\n@@fresh\ninactive\n@@lynis\n3.0.8\n@@quarantine\n0\n@@os\ndebian\n@@end\n"


async def _engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_overview_reports_signature_age_and_lowers_the_score():
    engine, sm = await _engine()
    old = (datetime.now(UTC) - timedelta(days=36)).strftime("%a %b %d %H:%M:%S %Y")
    stale = await Defender(_Ctx(sm, _status(old))).overview()
    row = stale["hosts"][0]
    assert (row["signature_stale"], row["signature_age_days"]) == (True, 36)
    assert stale["summary"]["stale_signatures"] == 1
    assert stale["attention"][0]["kind"] == "signatures" and stale["attention"][0]["host_id"] == "h1"

    recent = (datetime.now(UTC) - timedelta(days=1)).strftime("%a %b %d %H:%M:%S %Y")
    fresh = await Defender(_Ctx(sm, _status(recent))).overview()
    assert fresh["hosts"][0]["signature_stale"] is False and fresh["attention"] == []
    assert fresh["summary"]["score"] - stale["summary"]["score"] == protection.STALE_PENALTY_POINTS
    await engine.dispose()


@pytest.mark.asyncio
async def test_overview_without_signature_date_is_unknown_not_fresh():
    engine, sm = await _engine()
    out = await Defender(_Ctx(sm, "@@clam\nClamAV 1.0.7\n@@fresh\nactive\n@@lynis\nnone\n@@quarantine\n0\n@@os\ndebian\n@@end\n")).overview()
    assert out["hosts"][0]["signature_stale"] is None and out["hosts"][0]["signature_age_days"] is None
    assert out["attention"][0]["tone"] == "info"
    await engine.dispose()


@pytest.mark.asyncio
async def test_overview_with_unreadable_signature_date_is_unknown_too():
    engine, sm = await _engine()
    out = await Defender(_Ctx(sm, _status("Wed Foo 24 08:23:12 2026"))).overview()
    row = out["hosts"][0]
    # Das Datum ist da, aber nicht lesbar: wie "kein Datum" ein unbekanntes Alter, nicht "frisch".
    assert row["signature_date"] == "Wed Foo 24 08:23:12 2026"
    assert row["signature_stale"] is None and row["signature_age_days"] is None
    assert [(i["kind"], i["tone"]) for i in out["attention"]] == [("signatures", "info")]
    assert "fehlt oder ist nicht lesbar" in out["attention"][0]["hint"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_audit_keeps_the_earlier_successful_result_visible():
    engine, sm = await _engine()
    recent = (datetime.now(UTC) - timedelta(days=1)).strftime("%a %b %d %H:%M:%S %Y")
    now = datetime.now(UTC)
    async with sm() as s:
        s.add(AuditRecord(id="a1", host_id="h1", host_name="Pi", status="ok", hardening_index=64, warnings=["w"], suggestions=[],
                          error=None, created_at=now - timedelta(days=2)))
        s.add(AuditRecord(id="a2", host_id="h1", host_name="Pi", status="error", hardening_index=None, warnings=[], suggestions=[],
                          error="Lynis hat nicht geantwortet", created_at=now - timedelta(hours=3)))
        await s.commit()
    out = await Defender(_Ctx(sm, _status(recent))).overview()
    row = out["hosts"][0]
    assert row["last_audit"]["status"] == "error" and row["last_audit"]["error"] == "Lynis hat nicht geantwortet"
    assert row["last_ok_audit"]["hardening_index"] == 64 and row["last_ok_audit"]["warnings"] == 1
    assert out["summary"]["avg_hardening"] == 64
    await engine.dispose()
