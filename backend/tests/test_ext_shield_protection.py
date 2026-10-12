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


def test_posture_penalty_counts_updates_fail2ban_ssh_and_events_in_proportion():
    hosts = ["a", "b", "c", "d"]
    updates = {
        "a": {"security_count": 3, "reboot_required": True},
        "b": {"security_count": 0, "reboot_required": True, "reboot_pending_since": 1.0},  # Neustart laeuft: zaehlt nicht
        "c": {"error": "nicht erreichbar", "security_count": 9},  # Fehler: zaehlt nicht
    }
    guard = {
        "a": {"fail2ban": "running", "ssh_findings": [{"severity": "critical"}, {"severity": "warning"}]},
        "b": {"fail2ban": "none", "ssh_findings": [{"severity": "warning"}]},
        "c": {"fail2ban": "noaccess", "ssh_findings": None},  # nicht lesbar: zaehlt nicht
        "d": {"fail2ban": "stopped", "ssh_findings": [{"severity": "info"}]},
    }
    points, reasons = protection.posture_penalty(hosts, updates, guard, critical_events=1)
    expected = [
        round(protection.UPDATES_PENALTY_POINTS / 4),
        round(protection.REBOOT_PENALTY_POINTS / 4),
        round(protection.FAIL2BAN_PENALTY_POINTS * 2 / 4),
        round(protection.SSH_ROOT_PASSWORD_POINTS / 4),
        round(protection.SSH_PASSWORD_POINTS / 4),
        protection.EVENT_POINTS,
    ]
    assert points == sum(expected)
    assert reasons[0] == f"Sicherheitsupdates offen auf 1 von 4 Servern (−{expected[0]})"
    assert any(r.startswith("Fail2ban fehlt oder läuft nicht auf 2 von 4 Servern") for r in reasons)
    assert any(r.startswith("Gefährliche SSH-Anmeldung erlaubt (root mit Passwort oder leeres Passwort) auf 1 von 4 Servern")
               for r in reasons)
    assert f"1 offenes kritisches Sicherheitsereignis (−{protection.EVENT_POINTS})" in reasons
    # Server c: Update-Pruefung gescheitert -- kein Abzug, aber genannt.
    assert reasons[-1] == "Updates auf 1 von 4 Servern zuletzt nicht geprüft (ohne Abzug)"


def test_posture_penalty_ignores_stale_states_and_names_them():
    now = 1_000_000_000.0
    old = now - protection.STALE_AFTER_S - 1
    updates = {"a": {"security_count": 3, "checked_at": old}, "b": {"security_count": 3, "checked_at": now - 60},
               "c": {"error": "kein apt", "unsupported": True}}
    guard = {"a": {"fail2ban": "none", "checked_at": old}, "b": {"error": "nicht erreichbar", "checked_at": now}}
    points, reasons = protection.posture_penalty(["a", "b", "c"], updates, guard, 0, now=now)
    assert points == round(protection.UPDATES_PENALTY_POINTS / 3)
    assert reasons == [
        f"Sicherheitsupdates offen auf 1 von 3 Servern (−{round(protection.UPDATES_PENALTY_POINTS / 3)})",
        "Updates auf 1 von 3 Servern zuletzt nicht geprüft (ohne Abzug)",
        "Einbruchschutz auf 2 von 3 Servern zuletzt nicht geprüft (ohne Abzug)",
    ]


def test_one_affected_server_among_many_still_costs_a_point():
    hosts = [f"h{i}" for i in range(30)]
    points, reasons = protection.posture_penalty(hosts, {}, {"h0": {"ssh_findings": [{"severity": "critical"}]}}, 0)
    assert points == 1 and reasons[0].endswith("auf 1 von 30 Servern (−1)")


def test_signature_reasons_say_stale_or_unknown():
    stale = [{"clamav_installed": True, "signature_stale": True, "signature_age_days": 9}, {"clamav_installed": True}]
    reasons = protection.signature_reasons(stale)
    assert len(reasons) == 1 and reasons[0].startswith("Virensignaturen veraltet auf 1 von 2 Servern (−")
    unknown = protection.signature_reasons([{"clamav_installed": True, "signature_stale": None}])
    assert len(unknown) == 1 and unknown[0].startswith("Alter der Virensignaturen unbekannt (−")
    assert protection.signature_reasons([{"clamav_installed": True, "signature_stale": False}]) == []


def test_posture_penalty_without_data_or_hosts_costs_nothing_and_events_are_capped():
    assert protection.posture_penalty([], {}, {}, 0) == (0, [])
    assert protection.posture_penalty(["a"], {}, {}, 0) == (0, [])
    points, reasons = protection.posture_penalty(["a"], {}, {}, critical_events=9)
    assert points == protection.EVENT_MAX_POINTS
    assert reasons == [f"9 offene kritische Sicherheitsereignisse (−{protection.EVENT_MAX_POINTS})"]
    one, _ = protection.posture_penalty(["a"], {"a": {"security_count": 1}}, {}, 0)
    assert one == protection.UPDATES_PENALTY_POINTS


@pytest.mark.asyncio
async def test_overview_score_counts_updates_and_names_the_reasons():
    from nodvard_deck_ext_shield.models import BaselineRecord, SecurityEventRecord

    engine, sm = await _engine()
    recent = (datetime.now(UTC) - timedelta(days=1)).strftime("%a %b %d %H:%M:%S %Y")
    async with sm() as session:
        session.add(_scan("s0", "clean", datetime.now(UTC) - timedelta(days=1)))
        await session.commit()
    before = (await Defender(_Ctx(sm, _status(recent))).overview())["summary"]
    assert before["score_reasons"] == []
    async with sm() as session:
        session.add(BaselineRecord(id="h1:updates", host_id="h1", kind="updates", data={"security_count": 2}, updated_at=datetime.now(UTC)))
        session.add(BaselineRecord(id="h1:guard", host_id="h1", kind="guard", data={"fail2ban": "none", "ssh_findings": []}, updated_at=datetime.now(UTC)))
        session.add(SecurityEventRecord(
            id="e1", host_id="h1", host_name="docker", kind="bruteforce", severity="critical", title="Angriff", detail={},
            acknowledged=False, created_at=datetime.now(UTC),
        ))
        await session.commit()
    after = (await Defender(_Ctx(sm, _status(recent))).overview())["summary"]
    lost = protection.UPDATES_PENALTY_POINTS + protection.FAIL2BAN_PENALTY_POINTS + protection.EVENT_POINTS
    assert after["score"] == max(0, before["score"] - lost)
    assert [r.split(" (")[0] for r in after["score_reasons"]] == [
        "Sicherheitsupdates offen auf dem Server", "Fail2ban fehlt oder läuft nicht auf dem Server",
        "1 offenes kritisches Sicherheitsereignis",
    ]
    await engine.dispose()


def _scan(scan_id: str, status: str, started: datetime, host_id: str = "h1"):
    from nodvard_deck_ext_shield.models import ScanRecord

    return ScanRecord(id=scan_id, host_id=host_id, host_name="Pi", kind="quick", paths=["/home"], trigger="manual",
                      status=status, files_scanned=10, infected=0, started_at=started,
                      finished_at=None if status == "running" else started + timedelta(minutes=5))


@pytest.mark.asyncio
async def test_a_running_scan_does_not_lower_the_score():
    """In der Demo gesehen: Waehrend eines Schnellscans fiel der Schutzwert von 88 auf 62 und sprang danach zurueck.
    Fuer „aktuell gescannt“ zaehlt der letzte fertige Scan, nicht der gerade laufende."""
    from nodvard_deck_ext_shield.models import ScanRecord

    engine, sm = await _engine()
    recent = (datetime.now(UTC) - timedelta(days=1)).strftime("%a %b %d %H:%M:%S %Y")
    now = datetime.now(UTC)

    def scan(scan_id: str, status: str, started: datetime) -> ScanRecord:
        return ScanRecord(id=scan_id, host_id="h1", host_name="Pi", kind="quick", paths=["/home"], trigger="manual",
                          status=status, files_scanned=10, infected=0, started_at=started,
                          finished_at=None if status == "running" else started + timedelta(minutes=5))

    async with sm() as s:
        s.add(scan("s1", "clean", now - timedelta(days=2)))
        await s.commit()
    before = await Defender(_Ctx(sm, _status(recent))).overview()
    async with sm() as s:
        s.add(scan("s2", "running", now - timedelta(minutes=1)))
        await s.commit()
    during = await Defender(_Ctx(sm, _status(recent))).overview()
    assert during["hosts"][0]["last_scan"]["status"] == "running"
    assert during["hosts"][0]["last_completed_scan_at"] == pytest.approx((now - timedelta(days=2)).timestamp())
    assert during["summary"]["score"] == before["summary"]["score"]

    async with sm() as s:
        s.add(scan("s3", "error", now))
        await s.commit()
    old_only = await Defender(_Ctx(sm, _status(recent))).overview()
    assert old_only["summary"]["score"] == before["summary"]["score"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_without_a_finished_scan_the_running_one_still_does_not_count():
    from nodvard_deck_ext_shield.models import ScanRecord

    engine, sm = await _engine()
    recent = (datetime.now(UTC) - timedelta(days=1)).strftime("%a %b %d %H:%M:%S %Y")
    none = await Defender(_Ctx(sm, _status(recent))).overview()
    async with sm() as s:
        s.add(ScanRecord(id="s1", host_id="h1", host_name="Pi", kind="quick", paths=["/home"], trigger="manual",
                         status="running", files_scanned=None, infected=0, started_at=datetime.now(UTC), finished_at=None))
        await s.commit()
    running = await Defender(_Ctx(sm, _status(recent))).overview()
    assert running["hosts"][0]["last_completed_scan_at"] is None
    assert running["summary"]["score"] == none["summary"]["score"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_an_old_scan_still_counts_as_old_while_a_new_one_runs_and_the_reasons_say_why():
    engine, sm = await _engine()
    recent = (datetime.now(UTC) - timedelta(days=1)).strftime("%a %b %d %H:%M:%S %Y")
    now = datetime.now(UTC)
    async with sm() as s:
        s.add(_scan("fresh", "clean", now - timedelta(days=1)))
        await s.commit()
    fresh = (await Defender(_Ctx(sm, _status(recent))).overview())["summary"]
    async with sm() as s:
        await s.delete(await s.get(type(_scan("x", "clean", now)), "fresh"))
        s.add(_scan("old", "infected", now - timedelta(days=9)))
        s.add(_scan("run", "running", now - timedelta(minutes=1)))
        await s.commit()
    stale = (await Defender(_Ctx(sm, _status(recent))).overview())["summary"]
    assert fresh["score"] - stale["score"] in (25, 26)  # gerundet
    assert stale["score_reasons"] == ["Kein fertiger Scan in den letzten 8 Tagen: Pi (−25)"]

    async with sm() as s:
        s.add(_scan("ok", "clean", now - timedelta(hours=2)))
        s.add(_scan("err", "error", now))  # neuer als der laufende von eben
        await s.commit()
    failed = (await Defender(_Ctx(sm, _status(recent))).overview())["summary"]
    # Ein gescheiterter Scan senkt den Wert nicht (der vorige fertige zaehlt), steht aber als Grund da.
    assert failed["score"] == fresh["score"]
    assert failed["score_reasons"] == ["Letzter Scan auf Pi fehlgeschlagen"]
    await engine.dispose()
