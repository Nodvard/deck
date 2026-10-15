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


def test_decide_with_confirm_waits_for_the_second_reading_in_a_row():
    now = 1_000_000.0
    assert decide(None, 80, 75, now, confirm=2) == "wait"
    assert decide(None, 74, 75, now, confirm=2) is None
    once = {"above": 1, "above_at": now, "above_limit": 75}
    assert decide(once, 80, 75, now + 600, confirm=2) == "warn"
    assert decide(once, 74, 75, now + 600, confirm=2) is None
    # Die Messung davor zaehlt nur, wenn sie hoechstens zwei Stunden her ist.
    assert decide(once, 80, 75, now + warnung.CONFIRM_WITHIN_S, confirm=2) == "warn"
    assert decide(once, 80, 75, now + warnung.CONFIRM_WITHIN_S + 1, confirm=2) == "wait"
    # Kaputter Zaehler zaehlt nicht.
    for broken in ({"above": True}, {"above": "1"}, {"above": -3}):
        assert decide({**once, **broken}, 80, 75, now + 600, confirm=2) == "wait"
    # Gezaehlt wurde gegen eine andere oder unbekannte Grenze: von vorn.
    assert decide(once, 80, 70, now + 600, confirm=2) == "wait"
    assert decide({"above": 1, "above_at": now}, 80, 75, now + 600, confirm=2) == "wait"
    # Laeuft schon eine Warnung, aendert `confirm` nichts an Erinnerung und Entwarnung.
    alerting = {"alerting": True, "last_sent": now}
    assert decide(alerting, 70, 75, now + 600, confirm=2) == "recover"
    assert decide(alerting, 80, 75, now + 24 * 3600, confirm=2) == "remind"


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


async def _twice(ctx: FakeCtx, clock: dict[str, float]) -> None:
    """Zwei Prüfungen im Abstand von 10 Minuten: Eine Temperatur meldet erst die zweite in Folge."""
    await run_warnungen(ctx)
    clock["now"] += 600
    await run_warnungen(ctx)


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
    await _twice(ctx, clock)
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
    # Platten melden sofort, Temperaturen erst bei der zweiten Messung in Folge.
    assert sorted(ctx.titles()) == ["Speicher fast voll: / auf ki-server bei 90 %", "Speicher fast voll: /var auf ki-server bei 95 %"]
    clock["now"] += 600
    await run_warnungen(ctx)
    assert sorted(ctx.titles()[2:]) == ["ki-server ist heiß: 80 °C (acpitz)", "ki-server ist heiß: 90 °C (x86_pkg_temp)"]
    clock["now"] += 600
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
    await _twice(ctx, clock)
    assert ctx.titles() == ["pi ist heiß: 68 °C"]
    assert "Warnung ab 65" in ctx.sent[0].body


@pytest.mark.asyncio
async def test_switches_disable_each_warning_and_both_skip_the_ssh_call(tmp_path, clock):
    both = "@@disks\n/dev/a ext4 1000 910 90 91% /var\n@@thermal\nthermal_zone0|cpu|80000\n@@vcgencmd\n@@end\n"
    ctx = FakeCtx(tmp_path / "1", [_host("h", "x")], {"h": both}, {"warn_disk_enabled": False})
    await _twice(ctx, clock)
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
# Temperatur: zwei Messungen in Folge, eigene Grenze je Server
# ---------------------------------------------------------------------------


async def _temps(ctx: FakeCtx, clock: dict[str, float], host_id: str, values: list[Any]) -> None:
    """Je Wert eine Pruefung im Abstand von 10 Minuten; eine Exception heisst: Server antwortet nicht."""
    for value in values:
        ctx.outputs[host_id] = value if isinstance(value, Exception) else _temp_output(value)
        await run_warnungen(ctx)
        clock["now"] += 600


@pytest.mark.asyncio
async def test_a_single_hot_reading_stays_silent(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "pi")], {})
    # Spitze, wieder kuehl, Spitze: nie zweimal in Folge ueber 75 -- still.
    await _temps(ctx, clock, "h", [80, 70, 82, 74, 79])
    assert ctx.sent == []
    await _temps(ctx, clock, "h", [79])
    assert ctx.titles() == ["pi ist heiß: 79 °C"]
    # Die Entwarnung kommt wie bisher bei der ersten Messung 5 Grad unter der Grenze.
    await _temps(ctx, clock, "h", [70])
    assert ctx.titles()[-1] == "pi wieder im grünen Bereich: 70 °C"
    # Danach zaehlt es von vorn.
    await _temps(ctx, clock, "h", [81])
    assert len(ctx.sent) == 2
    await _temps(ctx, clock, "h", [81])
    assert ctx.titles()[-1] == "pi ist heiß: 81 °C"


@pytest.mark.asyncio
async def test_the_reading_before_counts_only_if_it_is_recent(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "pi")], {})
    await _temps(ctx, clock, "h", [80])
    clock["now"] += 3 * 3600  # Warnungen waren eine Weile aus
    await _temps(ctx, clock, "h", [80])
    assert ctx.sent == []
    await _temps(ctx, clock, "h", [80])
    assert ctx.titles() == ["pi ist heiß: 80 °C"]


@pytest.mark.asyncio
async def test_a_check_without_answer_in_between_keeps_the_count(tmp_path, clock):
    ctx = FakeCtx(tmp_path, [_host("h", "pi")], {})
    await _temps(ctx, clock, "h", [80, TimeoutError("weg")])
    assert ctx.sent == []
    await _temps(ctx, clock, "h", [80])
    assert ctx.titles() == ["pi ist heiß: 80 °C"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"warn_temp_c": 70}, {"warn_temp_c": 78}, {"warn_temp_by_host": {"h": 70}}],
                         ids=["allgemein-tiefer", "allgemein-hoeher", "eigene-grenze"])
async def test_a_changed_limit_restarts_the_count(tmp_path, clock, change):
    settings = {"warn_temp_c": 75}
    ctx = FakeCtx(tmp_path, [_host("h", "pi")], {}, settings)
    await _temps(ctx, clock, "h", [80])
    settings.update(change)
    # Auch wenn der Wert ueber beiden Grenzen liegt: Die Messung gegen die neue Grenze ist die erste.
    await _temps(ctx, clock, "h", [80])
    assert ctx.sent == []
    await _temps(ctx, clock, "h", [80])
    assert ctx.titles() == ["pi ist heiß: 80 °C"]


@pytest.mark.asyncio
async def test_the_old_count_does_not_come_back_with_the_old_limit(tmp_path, clock):
    settings = {"warn_temp_c": 75}
    ctx = FakeCtx(tmp_path, [_host("h", "pi")], {}, settings)
    await _temps(ctx, clock, "h", [80])
    settings["warn_temp_c"] = 85  # der Wert liegt jetzt darunter
    await _temps(ctx, clock, "h", [80])
    settings["warn_temp_c"] = 75
    await _temps(ctx, clock, "h", [80])
    assert ctx.sent == []
    await _temps(ctx, clock, "h", [80])
    assert len(ctx.sent) == 1


@pytest.mark.asyncio
async def test_the_count_survives_a_restart(tmp_path, clock):
    await _temps(FakeCtx(tmp_path, [_host("h", "pi")], {}), clock, "h", [80])
    again = FakeCtx(tmp_path, [_host("h", "pi")], {})
    await _temps(again, clock, "h", [80])
    assert again.titles() == ["pi ist heiß: 80 °C"]


@pytest.mark.asyncio
async def test_own_limit_per_server_keeps_a_warm_server_quiet(tmp_path, clock):
    """Ein Server, der unter Last immer warm läuft, bleibt mit eigener Grenze still; die anderen warnen weiter ab 75."""
    hosts = [_host("server-a", "server-a"), _host("pi", "pi")]
    ctx = FakeCtx(tmp_path, hosts, {"pi": _temp_output(60)}, {"warn_temp_by_host": {"server-a": 90}})
    await _temps(ctx, clock, "server-a", [77, 87, 79, 86, 69, 84, 87, 80])
    assert ctx.sent == []
    ctx.outputs["pi"] = _temp_output(80)
    await _temps(ctx, clock, "server-a", [85, 85])
    assert ctx.titles() == ["pi ist heiß: 80 °C"]
    assert "Warnung ab 75 °C." in ctx.sent[0].body and "eigene Grenze" not in ctx.sent[0].body
    await _temps(ctx, clock, "server-a", [91, 92])
    assert ctx.titles()[-1] == "server-a ist heiß: 92 °C"
    assert "Warnung ab 90 °C, eigene Grenze dieses Servers." in ctx.sent[-1].body
    await _temps(ctx, clock, "server-a", [85])
    assert ctx.titles()[-1] == "server-a wieder im grünen Bereich: 85 °C"
    assert "(Warnung ab 90 °C, eigene Grenze dieses Servers)" in ctx.sent[-1].body


def test_unusable_own_limits_fall_back_to_the_general_one():
    settings = {"warn_temp_c": 80, "warn_temp_by_host": {"a": True, "b": "90", "c": 200, "d": 39, "e": 90.5, "f": None, "g": 95}}
    assert warnung.own_temp_limits(settings) == {"g": 95}
    assert [warnung.temp_limit(settings, h) for h in "abcdefg"] == [80, 80, 80, 80, 80, 80, 95]
    assert warnung.temp_limit({"warn_temp_by_host": ["a"]}, "a") == warnung.DEFAULT_TEMP_C
    assert warnung.own_temp_limit({}, "a") is None


def test_the_page_colours_the_temperature_with_the_limit_of_this_server():
    from nodvard_deck_ext_system import _thresholds

    settings = {"warn_temp_c": 75, "warn_temp_by_host": {"server-a": 90}}
    for host_id, tone in (("server-a", "good"), ("pi", "warn")):
        info = {"disks": [], "temperature_c": 82.0}
        apply_thresholds(info, *_thresholds(settings, host_id))
        assert info["temperature_tone"] == tone


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
    limits = {"minimum": warnung.TEMP_LIMIT_MIN, "maximum": warnung.TEMP_LIMIT_MAX}
    assert {k: props["warn_temp_c"][k] for k in limits} == limits
    assert props["warn_temp_by_host"]["additionalProperties"] == {"type": "integer", **limits}
    assert props["warn_temp_by_host"]["x-hidden"] is True
    assert props["warn_interval_min"]["default"] == warnung.DEFAULT_INTERVAL_MIN == 10
    assert props["warn_disk_enabled"]["default"] is True and props["warn_temp_enabled"]["default"] is True


def test_job_spec_follows_the_settings():
    from nodvard_deck_ext_system import _WarnJobSpec

    default = _WarnJobSpec(None, {})
    assert (default.id, default.schedule, default.enabled) == ("warnung", "*/10 * * * *", True)
    assert _WarnJobSpec(None, {"warn_interval_min": 15}).schedule == "*/15 * * * *"
    assert _WarnJobSpec(None, {"warn_interval_min": 1}).schedule == "*/5 * * * *"
    # Ein Abstand von frueher, der 60 nicht glatt teilt, laeuft mit dem naechstkleineren Teiler.
    assert _WarnJobSpec(None, {"warn_interval_min": 7}).schedule == "*/6 * * * *"
    assert _WarnJobSpec(None, {"warn_interval_min": 59}).schedule == "*/30 * * * *"
    assert _WarnJobSpec(None, {"warn_interval_min": 25}).schedule == "*/20 * * * *"
    off = _WarnJobSpec(None, {"warn_disk_enabled": False, "warn_temp_enabled": False})
    assert off.enabled is False and off.schedule == "0 0 31 2 *"
    assert _WarnJobSpec(None, {"warn_disk_enabled": False}).enabled is True


async def _role_headers(client, db_session, role: str) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=f"{role}1", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": f"{role}1", "password": "whatever123"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture
async def limit_api(client, db_session, test_settings):
    from nodvard_deck.services import extensions as extensions_service
    from nodvard_deck.services import hosts as hosts_service

    host = await hosts_service.create_host(db_session, name="server-a", address="192.0.2.10")
    await db_session.commit()
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    token = (await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.post("/api/v1/extensions/system/enable", headers=headers)).status_code == 200
    return SimpleNamespace(host=host, headers=headers, url=f"/api/v1/ext/system/hosts/{host.id}/temp-limit")


async def _stored_limits(db_session) -> Any:
    from nodvard_deck.models import ExtensionRecord

    record = await db_session.get(ExtensionRecord, "system")
    await db_session.refresh(record)
    return (record.settings or {}).get("warn_temp_by_host")


async def _limit_audit(db_session) -> list[Any]:
    from nodvard_deck.models import AuditEntry
    from sqlalchemy import select

    rows = await db_session.execute(select(AuditEntry).where(AuditEntry.action == "ext_system.temp_limit_changed").order_by(AuditEntry.ts))
    return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_own_temperature_limit_over_the_api(client, db_session, limit_api):
    from nodvard_deck.models import ExtensionRecord

    host, headers, url = limit_api.host, limit_api.headers, limit_api.url
    got = await client.get(url, headers=headers)
    assert got.status_code == 200, got.text
    assert got.json() == {"host_id": host.id, "own_c": None, "global_c": 75, "effective_c": 75}

    for bad in (39, 111, "90", 90.5, True):
        r = await client.put(url, json={"own_c": bad}, headers=headers)
        assert r.status_code == 422, (bad, r.text)
    assert await _stored_limits(db_session) is None

    # Eine Grenze eines geloeschten Servers faellt beim Speichern heraus.
    record = await db_session.get(ExtensionRecord, "system")
    record.settings = {**(record.settings or {}), "warn_temp_by_host": {"weg": 80}}
    await db_session.commit()
    ok = await client.put(url, json={"own_c": 90}, headers=headers)
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"host_id": host.id, "own_c": 90, "global_c": 75, "effective_c": 90}
    assert await _stored_limits(db_session) == {host.id: 90}

    # Das Einstellungsformular aendert die allgemeine Grenze, die eigene bleibt (versteckt, nicht ueberschreibbar).
    form = await client.put("/api/v1/extensions/system/settings", headers=headers,
                            json={"values": {"warn_temp_c": 80, "warn_temp_by_host": {host.id: 50}}})
    assert form.status_code == 200, form.text
    assert await _stored_limits(db_session) == {host.id: 90}
    assert (await client.get(url, headers=headers)).json() == {"host_id": host.id, "own_c": 90, "global_c": 80, "effective_c": 90}

    # Ausdruecklich leer: wieder die allgemeine Grenze.
    cleared = await client.put(url, json={"own_c": None}, headers=headers)
    assert cleared.json() == {"host_id": host.id, "own_c": None, "global_c": 80, "effective_c": 80}
    assert await _stored_limits(db_session) == {}

    assert (await client.get("/api/v1/ext/system/hosts/gibt-es-nicht/temp-limit", headers=headers)).status_code == 404
    assert (await client.put("/api/v1/ext/system/hosts/gibt-es-nicht/temp-limit", json={"own_c": 90}, headers=headers)).status_code == 404
    assert (await client.get(url)).status_code == 401
    assert (await client.put(url, json={"own_c": 90})).status_code == 401

    # Lesen darf, wer Server sehen darf; aendern nur, wer Einstellungen aendern darf.
    for role in ("viewer", "operator"):
        role_headers = await _role_headers(client, db_session, role)
        assert (await client.get(url, headers=role_headers)).status_code == 200
        assert (await client.put(url, json={"own_c": 90}, headers=role_headers)).status_code == 403
    assert await _stored_limits(db_session) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {}, {"own_cc": 50}, {"own_c": 60, "extra": 1}, {"own_c": 39}, {"own_c": 111}, {"own_c": "60"}, {"own_c": 60.5}, {"own_c": True},
], ids=["leer", "tippfehler", "unbekanntes-feld", "39", "111", "text", "kommazahl", "bool"])
async def test_a_stored_limit_is_only_removed_by_an_explicit_null(client, db_session, limit_api, body):
    host, headers, url = limit_api.host, limit_api.headers, limit_api.url
    assert (await client.put(url, json={"own_c": 80}, headers=headers)).status_code == 200
    rejected = await client.put(url, json=body, headers=headers)
    assert rejected.status_code == 422, rejected.text
    assert await _stored_limits(db_session) == {host.id: 80}

    assert (await client.put(url, json={"own_c": None}, headers=headers)).status_code == 200
    assert await _stored_limits(db_session) == {}


@pytest.mark.asyncio
async def test_a_change_of_the_limit_is_written_to_the_audit_log(client, db_session, limit_api):
    from nodvard_deck.models import User
    from sqlalchemy import select

    host, headers, url = limit_api.host, limit_api.headers, limit_api.url
    owner = (await db_session.execute(select(User).where(User.username == "owner1"))).scalar_one()
    assert (await client.get(url, headers=headers)).status_code == 200
    assert await _limit_audit(db_session) == []  # Lesen schreibt nichts

    assert (await client.put(url, json={"own_c": 90}, headers=headers)).status_code == 200
    (entry,) = await _limit_audit(db_session)
    assert (entry.outcome, entry.target_type, entry.target_id) == ("success", "host", host.id)
    assert (entry.actor_type, entry.actor_id) == ("user", owner.id)
    assert entry.detail == {"own_c_before": None, "own_c": 90, "effective_c": 90, "via": "system"}

    assert (await client.put(url, json={"own_c": None}, headers=headers)).status_code == 200
    second = (await _limit_audit(db_session))[-1]
    assert second.detail == {"own_c_before": 90, "own_c": None, "effective_c": 75, "via": "system"}
    assert len(await _limit_audit(db_session)) == 2


@pytest.mark.asyncio
async def test_the_limit_is_saved_even_without_the_audit_permission(client, db_session, limit_api, caplog):
    from nodvard_deck.ext.runtime import get_extension_runtime

    granted = get_extension_runtime().loaded["system"].granted_permissions
    assert "audit.write" in granted
    granted.remove("audit.write")
    with caplog.at_level(logging.WARNING):
        saved = await client.put(limit_api.url, json={"own_c": 90}, headers=limit_api.headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["own_c"] == 90
    assert await _stored_limits(db_session) == {limit_api.host.id: 90}
    assert await _limit_audit(db_session) == []
    assert any("Protokolleintrag" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_saving_the_same_limit_again_writes_no_audit_entry(client, db_session, limit_api):
    for _ in range(2):
        assert (await client.put(limit_api.url, json={"own_c": 90}, headers=limit_api.headers)).status_code == 200
    assert len(await _limit_audit(db_session)) == 1
    assert (await client.put(limit_api.url, json={"own_c": None}, headers=limit_api.headers)).status_code == 200
    assert (await client.put(limit_api.url, json={"own_c": None}, headers=limit_api.headers)).status_code == 200
    assert len(await _limit_audit(db_session)) == 2
