"""system-Erweiterung: Warnungen bei voller Platte und hoher Temperatur (warnung.py).

Parser gegen echte df-/thermal-Ausgaben, Schwellwert und Hysterese, keine Doppelmeldung
nach einem Neustart, Erinnerung nach 24 h, Wartungsfenster, nicht erreichbare Hosts,
Einstellungen aus."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "system" / "src"))

from nodvard_deck_ext_system import warnung  # noqa: E402
from nodvard_deck_ext_system.sysinfo import apply_thresholds, parse_system_info, summary_findings  # noqa: E402
from nodvard_deck_ext_system.warnung import decide, parse_warn_output, run_warnungen  # noqa: E402

from nodvard_sdk import NotifyResult, Severity  # noqa: E402

GB = 1024 * 1024  # 1K-Bloecke von df

KI_HOST = """@@disks
/dev/sda1      ext4    102400000  40000000  62400000  40% /
/dev/sdb1      ext4    500000000 455000000  45000000  91% /var
/dev/sda15     vfat       523244      6220    517024   2% /boot/efi
/dev/mapper/vg-home btrfs 100000000 50000000 50000000 50% /home
/dev/mapper/vg-home btrfs 100000000 50000000 50000000 50% /home/.snapshots
@@thermal
@@vcgencmd
@@end
"""

PI = """@@disks
/dev/mmcblk0p2 ext4     61200196   2072000  55000000   4% /
/dev/mmcblk0p1 vfat       523244     68472    454772  14% /boot/firmware
@@thermal
thermal_zone0|cpu-thermal|78400
@@vcgencmd
temp=78.4'C
@@end
"""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_script_is_read_only_and_excludes_fake_filesystems():
    script = warnung.WARN_SCRIPT
    for fstype in ("tmpfs", "devtmpfs", "overlay", "squashfs"):
        assert f"-x {fstype}" in script
    assert "df -P -T" in script and "/sys/class/thermal/thermal_zone" in script and "vcgencmd measure_temp" in script
    for verb in ("rm ", "sudo", "systemctl restart", ">/", "tee "):
        assert verb not in script.replace(">/dev/null", "").replace("2>/dev/null", "")


def test_parse_df_drops_boot_efi_and_duplicate_devices():
    parsed = parse_warn_output(KI_HOST)
    assert parsed["complete"] is True
    assert [(d["mount"], d["percent"]) for d in parsed["disks"]] == [("/", 39.1), ("/home", 50.0), ("/var", 91.0)]
    assert parsed["temps"] == []  # keine Sensoren: kein Fehler, einfach nichts


def test_parse_df_keeps_mount_points_with_spaces_and_skips_junk():
    parsed = parse_warn_output("@@disks\n/dev/sdc1 ext4 1000 900 100 90% /mnt/mein Laufwerk\nkaputt\n/dev/x ext4 abc 1 1 1% /x\n@@end\n")
    assert [(d["mount"], d["percent"]) for d in parsed["disks"]] == [("/mnt/mein Laufwerk", 90.0)]


def test_parse_thermal_in_millidegrees_and_vcgencmd_only_as_fallback():
    parsed = parse_warn_output(PI)
    # thermal_zone0 und vcgencmd sind auf dem Pi derselbe Sensor: nur einmal melden.
    assert parsed["temps"] == [{"sensor": "thermal_zone0", "label": "cpu-thermal", "celsius": 78.4}]
    fallback = parse_warn_output("@@thermal\n@@vcgencmd\ntemp=81.2'C\n@@end\n")
    assert fallback["temps"] == [{"sensor": "vcgencmd", "label": "Raspberry Pi", "celsius": 81.2}]


def test_parse_thermal_ignores_garbage_and_implausible_values():
    text = (
        "@@thermal\nthermal_zone0|acpitz|45000\nthermal_zone1|x86_pkg_temp|\nthermal_zone2|foo|abc\n"
        "thermal_zone3|bar|2147483647\nthermal_zone4||52000\n@@vcgencmd\nvcgencmd: command failed\n@@end\n"
    )
    assert [(t["sensor"], t["label"], t["celsius"]) for t in parse_warn_output(text)["temps"]] == [
        ("thermal_zone0", "acpitz", 45.0), ("thermal_zone4", "thermal_zone4", 52.0),
    ]


def test_truncated_output_is_incomplete_and_empty_output_is_harmless():
    assert parse_warn_output("@@disks\n")["complete"] is False
    assert parse_warn_output("") == {"complete": False, "disks": [], "temps": []}


# ---------------------------------------------------------------------------
# Schwellwert und Hysterese (rein)
# ---------------------------------------------------------------------------


def test_decide_threshold_hysteresis_and_reminder():
    now = 1_000_000.0
    assert decide(None, 84, 85, now) is None
    assert decide(None, 85, 85, now) == "warn"
    alerting = {"alerting": True, "last_sent": now}
    assert decide(alerting, 95, 85, now + 60) is None  # schon gemeldet
    assert decide(alerting, 83, 85, now + 60) is None  # unter der Schwelle, aber nicht 5 darunter
    assert decide(alerting, 81, 85, now + 60) is None
    assert decide(alerting, 80, 85, now + 60) == "recover"  # genau 5 darunter
    assert decide(alerting, 90, 85, now + 24 * 3600 - 1) is None
    assert decide(alerting, 90, 85, now + 24 * 3600) == "remind"
    # Erinnerung nur, solange es noch ueber der Schwelle liegt.
    assert decide(alerting, 83, 85, now + 48 * 3600) is None


# ---------------------------------------------------------------------------
# Lauf gegen einen Doppelgaenger des Kontexts
# ---------------------------------------------------------------------------


def _host(hid: str, name: str, *, status: str = "up", os_family: str = "linux", credential: bool = True, display: str | None = None) -> Any:
    return SimpleNamespace(id=hid, name=name, display_name=display or name, os_family=os_family, has_credential=credential, status=SimpleNamespace(value=status))


class FakeCtx:
    def __init__(self, tmp_path: Path, hosts: list[Any], outputs: dict[str, Any], settings: dict[str, Any] | None = None) -> None:
        self.data_dir = tmp_path
        self._hosts = hosts
        self.outputs = outputs  # host.id -> Text oder Exception
        self._settings = settings or {}
        self.sent: list[Any] = []
        self.suppressed = False  # Wartungsfenster laeuft
        self.send_error: Exception | None = None
        self.runs: list[tuple[str, str]] = []
        self.logger = logging.getLogger("test-system-warnung")
        ctx = self
        ctx.hosts = SimpleNamespace(list=ctx._list_hosts)
        ctx.settings = SimpleNamespace(get=ctx._get_settings)
        ctx.exec = SimpleNamespace(run=ctx._run)
        ctx.notify = SimpleNamespace(send=ctx._send, would_suppress=ctx._would_suppress)

    async def _list_hosts(self, **_: Any) -> list[Any]:
        return self._hosts

    async def _get_settings(self) -> dict[str, Any]:
        return self._settings

    async def _run(self, host: Any, command: str, *, timeout_s: int = 60, **_: Any) -> Any:
        self.runs.append((host.id, command))
        out = self.outputs[host.id]
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(stdout=out, stderr="", exit_code=0)

    async def _send(self, notification: Any, **_: Any) -> Any:
        if self.send_error:
            raise self.send_error
        self.sent.append(notification)
        return NotifyResult(notification_id=str(len(self.sent)), suppressed=self.suppressed)

    async def _would_suppress(self, **_: Any) -> bool:
        return self.suppressed

    def titles(self) -> list[str]:
        return [n.title for n in self.sent]


def _disk_output(percent: int, mount: str = "/var") -> str:
    used = percent * 10
    return f"@@disks\n/dev/sdb1 ext4 1000 {used} {1000 - used} {percent}% {mount}\n@@thermal\n@@vcgencmd\n@@end\n"


def _temp_output(celsius: float) -> str:
    return f"@@disks\n@@thermal\nthermal_zone0|cpu-thermal|{int(celsius * 1000)}\n@@vcgencmd\n@@end\n"


@pytest.fixture
def clock(monkeypatch):
    state = {"now": 1_700_000_000.0}
    monkeypatch.setattr(warnung.time, "time", lambda: state["now"])
    return state


@pytest.mark.asyncio
async def test_disk_warning_message_severity_and_path(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h-ki", "ki-server")], {"h-ki": KI_HOST})
    result = await run_warnungen(ctx)
    assert result == {"checked": 1, "unreachable": 0, "notified": 1}
    (note,) = ctx.sent
    assert note.title == "Speicher fast voll: /var auf ki-server bei 91 %"
    assert note.severity is Severity.WARNING
    assert note.payload["path"] == "/ext/system/system?host=h-ki"
    assert note.payload["host_id"] == "h-ki" and note.payload["tags"]
    assert "Warnung ab 85 %" in note.body
    # Nur EIN Befehl pro Host, und der ist das lesende Warn-Skript.
    assert ctx.runs == [("h-ki", warnung.WARN_SCRIPT)]


@pytest.mark.asyncio
async def test_temperature_warning_on_the_pi(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h-pi", "pi", display="Raspberry Pi")], {"h-pi": PI})
    await run_warnungen(ctx)
    assert ctx.titles() == ["Raspberry Pi ist heiß: 78 °C"]
    assert ctx.sent[0].severity is Severity.WARNING
    assert ctx.sent[0].payload["path"] == "/ext/system/system?host=h-pi"


@pytest.mark.asyncio
async def test_no_repeat_while_the_value_stays_high(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})
    for _ in range(5):
        await run_warnungen(ctx)
        clock["now"] += 600
    assert len(ctx.sent) == 1


@pytest.mark.asyncio
async def test_below_threshold_is_silent(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(84)})
    assert (await run_warnungen(ctx))["notified"] == 0
    assert ctx.sent == []


@pytest.mark.asyncio
async def test_hysteresis_then_recovery_then_new_warning(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})
    await run_warnungen(ctx)
    for percent in (86, 82, 84, 91):  # pendelt um die Schwelle: bleibt still
        ctx.outputs["h"] = _disk_output(percent)
        clock["now"] += 600
        await run_warnungen(ctx)
    assert len(ctx.sent) == 1
    ctx.outputs["h"] = _disk_output(79)  # mehr als 5 darunter
    clock["now"] += 600
    await run_warnungen(ctx)
    assert ctx.titles()[-1] == "Speicher wieder im grünen Bereich: /var auf ki-server bei 79 %"
    assert ctx.sent[-1].severity is Severity.INFO
    ctx.outputs["h"] = _disk_output(80)  # noch unter der Schwelle: nichts
    clock["now"] += 600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 2
    ctx.outputs["h"] = _disk_output(88)  # wieder drueber: neue Warnung
    clock["now"] += 600
    await run_warnungen(ctx)
    assert ctx.titles() == [
        "Speicher fast voll: /var auf ki-server bei 91 %",
        "Speicher wieder im grünen Bereich: /var auf ki-server bei 79 %",
        "Speicher fast voll: /var auf ki-server bei 88 %",
    ]


@pytest.mark.asyncio
async def test_no_double_notification_after_restart(tmp_path, clock):
    hosts = [_host("h", "ki-server")]
    first = FakeCtx(tmp_path, hosts, {"h": _disk_output(91)})
    await run_warnungen(first)
    assert (tmp_path / "warnung-state.json").exists()
    # Neustart: neues Objekt, derselbe data_dir
    second = FakeCtx(tmp_path, hosts, {"h": _disk_output(92)})
    clock["now"] += 600
    await run_warnungen(second)
    assert second.sent == []
    # Und die Entwarnung kommt trotzdem, weil der Zustand die Warnung kennt.
    second.outputs["h"] = _disk_output(70)
    clock["now"] += 600
    await run_warnungen(second)
    assert second.titles() == ["Speicher wieder im grünen Bereich: /var auf ki-server bei 70 %"]


@pytest.mark.asyncio
async def test_reminder_after_24_hours_then_quiet_again(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})
    await run_warnungen(ctx)
    clock["now"] += 24 * 3600 - 60
    await run_warnungen(ctx)
    assert len(ctx.sent) == 1
    clock["now"] += 60
    await run_warnungen(ctx)
    assert len(ctx.sent) == 2
    assert ctx.sent[1].severity is Severity.WARNING and "Erinnerung" in ctx.sent[1].body
    clock["now"] += 600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 2
    clock["now"] += 24 * 3600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 3


@pytest.mark.asyncio
async def test_each_mount_and_sensor_is_tracked_separately(tmp_path, clock):
    output = (
        "@@disks\n/dev/a ext4 1000 900 100 90% /\n/dev/b ext4 1000 950 50 95% /var\n"
        "@@thermal\nthermal_zone0|acpitz|80000\nthermal_zone1|x86_pkg_temp|90000\n@@vcgencmd\n@@end\n"
    )
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": output})
    await run_warnungen(ctx)
    assert sorted(ctx.titles()) == [
        "Speicher fast voll: / auf ki-server bei 90 %",
        "Speicher fast voll: /var auf ki-server bei 95 %",
        "ki-server ist heiß: 80 °C (acpitz)",
        "ki-server ist heiß: 90 °C (x86_pkg_temp)",
    ]
    await run_warnungen(ctx)
    assert len(ctx.sent) == 4


@pytest.mark.asyncio
async def test_hosts_are_tracked_separately(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("a", "alpha"), _host("b", "beta")], {"a": _disk_output(91), "b": _disk_output(91)})
    await run_warnungen(ctx)
    assert sorted(ctx.titles()) == ["Speicher fast voll: /var auf alpha bei 91 %", "Speicher fast voll: /var auf beta bei 91 %"]


@pytest.mark.asyncio
async def test_unreachable_host_is_only_logged_and_keeps_its_state(tmp_path, clock, caplog):
    hosts = [_host("a", "alpha"), _host("b", "beta")]
    ctx = FakeCtx(tmp_path, hosts, {"a": _disk_output(91), "b": _disk_output(91)})
    await run_warnungen(ctx)
    ctx.sent.clear()
    ctx.outputs["a"] = TimeoutError("ssh timeout")
    ctx.outputs["b"] = "@@disks\n"  # abgeschnittene Antwort
    clock["now"] += 600
    with caplog.at_level(logging.INFO, logger="test-system-warnung"):
        result = await run_warnungen(ctx)
    assert result == {"checked": 0, "unreachable": 2, "notified": 0}
    assert ctx.sent == []
    assert "alpha nicht erreichbar" in caplog.text
    # Wieder erreichbar und immer noch voll: keine zweite Warnung.
    ctx.outputs["a"] = _disk_output(91)
    clock["now"] += 600
    await run_warnungen(ctx)
    assert ctx.sent == []


@pytest.mark.asyncio
async def test_unreachable_host_does_not_stop_the_others(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("a", "alpha"), _host("b", "beta")], {"a": OSError("Verbindung abgelehnt"), "b": _disk_output(91)})
    result = await run_warnungen(ctx)
    assert result["unreachable"] == 1 and ctx.titles() == ["Speicher fast voll: /var auf beta bei 91 %"]


@pytest.mark.asyncio
async def test_only_linux_hosts_with_access_that_are_not_down_or_skipped(tmp_path, clock):
    hosts = [
        _host("lin", "linux-ok"), _host("win", "game-win", os_family="windows"), _host("nocred", "ohne-zugang", credential=False),
        _host("down", "aus", status="down"), _host("skip", "test-vm", display="Test VM"),
    ]
    outputs = {h.id: _disk_output(91) for h in hosts}
    ctx = FakeCtx(tmp_path, hosts, outputs, {"warn_skip_hosts": [" Test VM "]})
    await run_warnungen(ctx)
    assert [h for h, _ in ctx.runs] == ["lin"]
    assert ctx.titles() == ["Speicher fast voll: /var auf linux-ok bei 91 %"]


@pytest.mark.asyncio
async def test_custom_thresholds_from_settings(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)}, {"warn_disk_percent": 95})
    await run_warnungen(ctx)
    assert ctx.sent == []
    ctx = FakeCtx(tmp_path / "t", [_host("h", "pi")], {"h": _temp_output(68)}, {"warn_temp_c": 65})
    await run_warnungen(ctx)
    assert ctx.titles() == ["pi ist heiß: 68 °C"]
    assert "Warnung ab 65" in ctx.sent[0].body


@pytest.mark.asyncio
async def test_switches_disable_each_warning_and_both_skip_the_ssh_call(tmp_path, clock):
    both = "@@disks\n/dev/a ext4 1000 910 90 91% /var\n@@thermal\nthermal_zone0|cpu|80000\n@@vcgencmd\n@@end\n"
    ctx = FakeCtx(tmp_path / "1", [_host("h", "x")], {"h": both}, {"warn_disk_enabled": False})
    await run_warnungen(ctx)
    assert ctx.titles() == ["x ist heiß: 80 °C"]
    ctx = FakeCtx(tmp_path / "2", [_host("h", "x")], {"h": both}, {"warn_temp_enabled": False})
    await run_warnungen(ctx)
    assert ctx.titles() == ["Speicher fast voll: /var auf x bei 91 %"]
    ctx = FakeCtx(tmp_path / "3", [_host("h", "x")], {"h": both}, {"warn_disk_enabled": False, "warn_temp_enabled": False})
    assert await run_warnungen(ctx) == {"checked": 0, "unreachable": 0, "notified": 0}
    assert ctx.runs == [] and ctx.sent == []


@pytest.mark.asyncio
async def test_failed_delivery_is_retried_next_run(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})
    ctx.send_error = RuntimeError("Kanal kaputt")
    assert (await run_warnungen(ctx))["notified"] == 0
    ctx.send_error = None
    clock["now"] += 600
    await run_warnungen(ctx)
    assert ctx.titles() == ["Speicher fast voll: /var auf ki-server bei 91 %"]


@pytest.mark.asyncio
async def test_corrupt_state_file_starts_fresh(tmp_path, clock):
    (tmp_path / "warnung-state.json").write_text("{kaputt", encoding="utf-8")
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})
    await run_warnungen(ctx)
    assert len(ctx.sent) == 1
    assert json.loads((tmp_path / "warnung-state.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_state_of_removed_hosts_is_dropped(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})
    await run_warnungen(ctx)
    ctx._hosts = []
    await run_warnungen(ctx)
    assert json.loads((tmp_path / "warnung-state.json").read_text(encoding="utf-8")) == {}


# ---------------------------------------------------------------------------
# Wartungsfenster
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maintenance_window_warning_is_repeated_audibly_after_the_window(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})
    ctx.suppressed = True
    await run_warnungen(ctx)  # im Fenster: im Verlauf, aber still
    clock["now"] += 600
    await run_warnungen(ctx)  # Fenster laeuft noch: nichts Neues
    assert len(ctx.sent) == 1
    ctx.suppressed = False
    clock["now"] += 600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 2 and "Wartungsfenster" in ctx.sent[1].body
    clock["now"] += 600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 2


@pytest.mark.asyncio
async def test_window_that_ends_with_the_problem_gone_needs_no_late_warning_or_recovery(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})
    ctx.suppressed = True
    await run_warnungen(ctx)
    ctx.outputs["h"] = _disk_output(70)
    clock["now"] += 600
    await run_warnungen(ctx)  # Entwarnung im Fenster (still)
    ctx.suppressed = False
    clock["now"] += 600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 2  # Warnung + Entwarnung, beide nur im Verlauf; nichts Hoerbares nachgeholt


@pytest.mark.asyncio
async def test_heard_warning_gets_a_heard_recovery_even_if_it_falls_into_a_window(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})
    await run_warnungen(ctx)  # hoerbar
    ctx.suppressed = True
    ctx.outputs["h"] = _disk_output(70)
    clock["now"] += 600
    await run_warnungen(ctx)  # Entwarnung faellt ins Fenster
    assert len(ctx.sent) == 2
    clock["now"] += 600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 2  # Fenster laeuft noch
    ctx.suppressed = False
    clock["now"] += 600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 3 and ctx.sent[2].severity is Severity.INFO
    clock["now"] += 600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 3


@pytest.mark.asyncio
async def test_older_core_without_notify_result_counts_as_delivered(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "ki-server")], {"h": _disk_output(91)})

    async def send(notification: Any, **_: Any) -> None:
        ctx.sent.append(notification)

    ctx.notify = SimpleNamespace(send=send)  # auch ohne would_suppress
    await run_warnungen(ctx)
    clock["now"] += 600
    await run_warnungen(ctx)
    assert len(ctx.sent) == 1


# ---------------------------------------------------------------------------
# Kachel: Schwellen in der Anzeige
# ---------------------------------------------------------------------------


def test_thresholds_colour_the_disk_and_temperature_tiles():
    info = parse_system_info(
        "@@os\nDebian\n@@disks\n/dev/a ext4 1000 700 300 70% /\n/dev/b ext4 1000 850 150 85% /var\n"
        "@@temp\n78000\n@@end\n"
    )
    assert [d["tone"] for d in info["disks"]] == ["good", "warn"]  # Standard: ab 80 % gelb, hier Schwelle 65
    apply_thresholds(info, 65, 75)
    assert [d["tone"] for d in info["disks"]] == ["warn", "warn"]
    assert info["temperature_tone"] == "warn"
    assert "Temperatur 78 °C" in [f["text"] for f in summary_findings(info)]
    apply_thresholds(info, 95, 80)
    assert info["temperature_tone"] == "good"
    assert not any("Temperatur" in f["text"] for f in summary_findings(info))
    # Gelb bleibt ab 80 % auch bei hoher Warnschwelle (bestehende Anzeige); rot ab 90 % ebenso.
    assert [d["tone"] for d in info["disks"]] == ["warn", "warn"]


# ---------------------------------------------------------------------------
# Registrierung im Kern
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_is_registered_and_follows_changed_settings(client, db_session, test_settings):
    from sqlalchemy import select

    from nodvard_deck.models import Job
    from nodvard_deck.services import extensions as extensions_service

    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    token = (await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    assert (await client.post("/api/v1/extensions/system/enable", headers=headers)).status_code == 200

    async def job() -> Any:
        db_session.expire_all()
        return (await db_session.execute(select(Job).where(Job.ext_id == "system", Job.ext_job_key == "warnung"))).scalar_one()

    row = await job()
    assert (row.schedule, row.enabled) == ("*/10 * * * *", True)

    saved = await client.put("/api/v1/extensions/system/settings", headers=headers, json={"values": {"warn_interval_min": 30}})
    assert saved.status_code == 200, saved.text
    assert (await job()).schedule == "*/30 * * * *"
    off = await client.put("/api/v1/extensions/system/settings", headers=headers,
                           json={"values": {"warn_disk_enabled": False, "warn_temp_enabled": False}})
    assert off.status_code == 200, off.text
    assert (await job()).enabled is False

    props = json.loads((REPO_EXTENSIONS_DIR / "system" / "settings.schema.json").read_text(encoding="utf-8"))["properties"]
    assert props["warn_disk_percent"]["default"] == warnung.DEFAULT_DISK_PERCENT == 85
    assert props["warn_temp_c"]["default"] == warnung.DEFAULT_TEMP_C == 75
    assert props["warn_interval_min"]["default"] == warnung.DEFAULT_INTERVAL_MIN == 10
    assert props["warn_disk_enabled"]["default"] is True and props["warn_temp_enabled"]["default"] is True


def test_job_spec_follows_the_settings():
    from nodvard_deck_ext_system import _WarnJobSpec

    default = _WarnJobSpec(None, {})
    assert (default.id, default.schedule, default.enabled) == ("warnung", "*/10 * * * *", True)
    assert _WarnJobSpec(None, {"warn_interval_min": 15}).schedule == "*/15 * * * *"
    assert _WarnJobSpec(None, {"warn_interval_min": 1}).schedule == "*/5 * * * *"
    off = _WarnJobSpec(None, {"warn_disk_enabled": False, "warn_temp_enabled": False})
    assert off.enabled is False and off.schedule == "0 0 31 2 *"
    assert _WarnJobSpec(None, {"warn_disk_enabled": False}).enabled is True
