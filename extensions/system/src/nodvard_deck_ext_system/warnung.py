"""Warnungen der system-Erweiterung: Platte fast voll und Temperatur zu hoch."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nodvard_sdk import Notification, Severity

from .sysinfo import _sections, parse_disks

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

# Ein Job (`warnung`) fragt jeden Linux-Server mit SSH-Zugang rein lesend mit einem kurzen Skript ab, nicht mit der
# vollen Systemabfrage (die wartet 1 s auf die CPU-Messung und liest die Paketlisten). Der Zustand je Server und
# Dateisystem bzw. Sensor steht in `data_dir/warnung-state.json`, ein Neustart meldet also nichts doppelt.
PAGE_PATH = "/ext/system/system"
STATE_FILE = "warnung-state.json"

DEFAULT_DISK_PERCENT = 85
DEFAULT_TEMP_C = 75
DEFAULT_INTERVAL_MIN = 10
HYSTERESIS = 5  # Prozentpunkte bzw. Grad, die der Wert wieder darunter liegen muss
REMIND_S = 24 * 3600
_PARALLEL_HOSTS = 6  # Server, die gleichzeitig abgefragt werden

# Netzlaufwerke (nfs, cifs, sshfs) fehlen mit Absicht: Ein hängendes Laufwerk würde `df` und damit die ganze Prüfung
# blockieren. Dazu fehlen die üblichen Nicht-Platten (tmpfs, overlay, squashfs, devtmpfs, efivarfs, CD/DVD).
WARN_SCRIPT = r"""echo @@disks
df -P -T -x tmpfs -x devtmpfs -x overlay -x squashfs -x efivarfs -x ramfs -x iso9660 -x udf -x nfs -x nfs4 -x cifs -x smb3 -x fuse.sshfs 2>/dev/null | tail -n +2
echo @@thermal
for z in /sys/class/thermal/thermal_zone*; do [ -r "$z/temp" ] || continue; echo "$(basename "$z")|$(cat "$z/type" 2>/dev/null)|$(cat "$z/temp" 2>/dev/null)"; done
echo @@vcgencmd
if command -v vcgencmd >/dev/null 2>&1; then vcgencmd measure_temp 2>/dev/null; fi
echo @@end
"""

_VCGENCMD_RE = re.compile(r"temp\s*=\s*(-?\d+(?:\.\d+)?)")


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------


def _is_boot_efi(mount: str) -> bool:
    return mount == "/boot/efi" or mount.startswith("/boot/efi/")


def _plausible(celsius: float) -> bool:
    return -50.0 <= celsius <= 150.0


def parse_warn_output(text: str) -> dict[str, Any]:
    """Ausgabe von `WARN_SCRIPT` als {"complete", "disks", "temps"}; fehlende oder unsinnige Werte fallen weg."""
    s = _sections(text)
    # Je Gerät zählt nur der kürzeste Einhängepunkt, sonst zeigen btrfs-Unterdatenträger und Bind-Mounts dieselbe Platte
    # mehrfach.
    best: dict[str, dict[str, Any]] = {}
    for disk in parse_disks(s.get("disks", [])):
        if _is_boot_efi(disk["mount"]):
            continue
        known = best.get(disk["device"])
        if known is None or len(disk["mount"]) < len(known["mount"]):
            best[disk["device"]] = disk
    disks = sorted(best.values(), key=lambda d: d["mount"])

    temps: list[dict[str, Any]] = []
    for line in s.get("thermal", []):
        parts = line.strip().split("|")
        if len(parts) != 3 or not parts[2].strip().lstrip("-").isdigit():
            continue
        celsius = int(parts[2]) / 1000  # Millgrad
        if _plausible(celsius):
            temps.append({"sensor": parts[0], "label": parts[1].strip() or parts[0], "celsius": round(celsius, 1)})
    if not temps:  # Raspberry Pi ohne thermal_zone (selten): `vcgencmd` als Rückfall
        for line in s.get("vcgencmd", []):
            match = _VCGENCMD_RE.search(line)
            if match and _plausible(float(match.group(1))):
                temps.append({"sensor": "vcgencmd", "label": "Raspberry Pi", "celsius": round(float(match.group(1)), 1)})
                break
    return {"complete": "end" in s, "disks": disks, "temps": temps}


# ---------------------------------------------------------------------------
# Entscheiden (rein, ohne Netz)
# ---------------------------------------------------------------------------


def whole(value: float) -> int:
    """Kaufmännisch gerundet: Die Zahl, die in der Meldung steht, entscheidet auch."""
    return int(value + 0.5)


def decide(entry: dict[str, Any] | None, value: float, threshold: float, now: float) -> str | None:
    """Was tun bei diesem Messwert? "warn", "remind", "recover" oder None."""
    # Hysterese: Nach einer Warnung gilt das Problem erst als behoben, wenn der Wert mindestens `HYSTERESIS` unter der
    # Schwelle liegt; ein pendelnder Wert meldet so nicht jedes Mal neu. Ein anhaltendes Problem erinnert nach `REMIND_S`.
    entry = entry or {}
    if not entry.get("alerting"):
        return "warn" if value >= threshold else None
    if value <= threshold - HYSTERESIS:
        return "recover"
    if value >= threshold and now - float(entry.get("last_sent") or 0) >= REMIND_S:
        return "remind"
    return None


# ---------------------------------------------------------------------------
# Meldungen
# ---------------------------------------------------------------------------


def _bytes(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{n} B"


def disk_notification(kind: str, name: str, host_id: str, disk: dict[str, Any], threshold: int, *, note: str = "") -> Notification:
    percent = whole(disk["percent"])
    if kind == "recover":
        title = f"Speicher wieder im grünen Bereich: {disk['mount']} auf {name} bei {percent} %"
        body = f"{disk['mount']} ist nur noch zu {percent} % belegt (Warnung ab {threshold} %)."
        severity = Severity.INFO
    else:
        title = f"Speicher fast voll: {disk['mount']} auf {name} bei {percent} %"
        body = (
            f"{disk['mount']} ({disk['device']}, {disk['fstype']}): {_bytes(disk['used'])} von {_bytes(disk['size'])} belegt, "
            f"noch {_bytes(disk['available'])} frei. Warnung ab {threshold} %."
        )
        severity = Severity.WARNING
    return Notification(
        title=title, body=f"{body}\n{note}" if note else body, severity=severity,
        correlation_id=f"system-warnung:{host_id}:disk:{disk['mount']}",
        payload={"path": f"{PAGE_PATH}?host={host_id}", "tags": ["floppy_disk" if kind != "recover" else "white_check_mark"], "host_id": host_id},
    )


def temp_notification(kind: str, name: str, host_id: str, temp: dict[str, Any], threshold: int, *, several: bool, note: str = "") -> Notification:
    degrees = whole(temp["celsius"])
    where = f" ({temp['label']})" if several else ""
    if kind == "recover":
        title = f"{name} wieder im grünen Bereich: {degrees} °C{where}"
        body = f"Die Temperatur ist wieder auf {degrees} °C gesunken (Warnung ab {threshold} °C)."
        severity = Severity.INFO
    else:
        title = f"{name} ist heiß: {degrees} °C{where}"
        body = f"Sensor {temp['label']}: {degrees} °C. Warnung ab {threshold} °C. Lüfter, Staub und Belüftung prüfen."
        severity = Severity.WARNING
    return Notification(
        title=title, body=f"{body}\n{note}" if note else body, severity=severity,
        correlation_id=f"system-warnung:{host_id}:temp:{temp['sensor']}",
        payload={"path": f"{PAGE_PATH}?host={host_id}", "tags": ["fire" if kind != "recover" else "white_check_mark"], "host_id": host_id},
    )


# ---------------------------------------------------------------------------
# Lauf
# ---------------------------------------------------------------------------


def _load(path: Path, logger: Any) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("system-Warnungen: Zustandsdatei unlesbar (%s) -- fange neu an", exc)
        return {}


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def warn_hosts(hosts: list[Any], skip: list[str]) -> list[Any]:
    """Linux-Server mit SSH-Zugang wie im Widget „Server-Zustand“, ohne die in den Einstellungen ausgenommenen."""
    skipped = {s.strip().lower() for s in skip if isinstance(s, str) and s.strip()}
    return [
        h for h in hosts
        if h.os_family == "linux" and h.has_credential
        and not ({h.id.lower(), h.name.lower(), (h.display_name or "").lower()} & skipped)
    ]


async def _send(ctx: "ExtensionContext", notification: Notification) -> bool | None:
    """True, wenn ein Wartungsfenster den Push unterdrückt hat, False bei Zustellung, None bei Sendefehler (nächster
    Lauf versucht es erneut)."""
    # Ältere Kerne liefern kein Ergebnis; dann gilt die Meldung als zugestellt.
    try:
        result = await ctx.notify.send(notification)
    except Exception as exc:  # noqa: BLE001 - eine Meldung darf den Job nicht abbrechen
        ctx.logger.warning("system-Warnungen: Meldung konnte nicht gesendet werden (%s)", exc)
        return None
    return getattr(result, "suppressed", False) is True


async def _window_over(ctx: "ExtensionContext", host_id: str) -> bool:
    """Ist das Wartungsfenster für diesen Server vorbei? Im Zweifel ja: lieber einmal zu oft melden als eine verlieren."""
    try:
        return await ctx.notify.would_suppress(host_id=host_id) is not True
    except Exception:  # noqa: BLE001
        return True


async def _step(ctx: "ExtensionContext", state: dict[str, Any], key: str, host_id: str, value: float, threshold: int, now: float, build: Any) -> int:
    """Prüft einen Messwert gegen seinen Zustand, liefert die Zahl gesendeter Meldungen; `build(kind, note)` baut sie."""
    # Eine im Wartungsfenster stumm geschaltete Meldung (`muted`) wird nur still vermerkt; ist das Fenster vorbei und
    # das Problem noch da, kommt sie einmal hörbar nach. Eine gehörte Warnung bekommt immer eine gehörte Entwarnung.
    entry = state.get(key)
    action = decide(entry, value, threshold, now)
    sent = 0
    if action in ("warn", "remind"):
        muted = await _send(ctx, build("warn", "Die Warnung besteht weiterhin (Erinnerung nach 24 Stunden)." if action == "remind" else ""))
        if muted is None:
            return 0
        state[key] = {"alerting": True, "last_sent": now, "muted": muted, "heard": not muted or bool(entry and entry.get("heard") and action == "remind"),
                      "ok_pending": False, "value": value}
        return 1
    if action == "recover":
        muted = await _send(ctx, build("recover", ""))
        if muted is None:
            return 0
        # Fiel die Entwarnung einer gehörten Warnung ins Fenster, kommt sie danach nach (`ok_pending`).
        state[key] = {"alerting": False, "last_sent": now, "muted": False, "heard": False,
                      "ok_pending": bool(muted and entry and entry.get("heard")), "value": value}
        return 1
    if entry is None:
        return 0
    entry["value"] = value
    if entry.get("alerting") and value >= threshold and entry.get("muted") and await _window_over(ctx, host_id):
        # Im Fenster nur still gemeldet, Fenster vorbei, Problem besteht noch: einmal hörbar.
        muted = await _send(ctx, build("warn", "Das begann im Wartungsfenster (dort nur im Verlauf, ohne Push) und hält noch an."))
        if muted is not None:
            entry.update(muted=muted, heard=not muted, last_sent=now)
            sent = 1
    elif not entry.get("alerting") and entry.get("ok_pending") and await _window_over(ctx, host_id):
        muted = await _send(ctx, build("recover", "Das wurde im Wartungsfenster wieder gut (dort nur im Verlauf, ohne Push)."))
        if muted is not None:
            entry["ok_pending"] = muted
            sent = 1
    return sent


async def _read_host(ctx: "ExtensionContext", host: Any, sem: asyncio.Semaphore) -> dict[str, Any] | None:
    """Messwerte eines Servers oder None, wenn er nicht antwortet; das ist keine Warnung, nur ein Log-Eintrag."""
    async with sem:
        try:
            # Das äußere Zeitlimit (40 s) liegt über dem des SSH-Aufrufs (30 s), damit auch ein hängender
            # Verbindungsaufbau endet.
            result = await asyncio.wait_for(ctx.exec.run(host, WARN_SCRIPT, timeout_s=30), timeout=40)
        except Exception as exc:  # noqa: BLE001 - ein Host darf die anderen nicht aufhalten
            ctx.logger.info("system-Warnungen: %s nicht erreichbar, übersprungen (%s)", host.name, str(exc) or type(exc).__name__)
            return None
    if "@@end" not in result.stdout:
        ctx.logger.info("system-Warnungen: %s lieferte keine vollständige Antwort, übersprungen", host.name)
        return None
    return parse_warn_output(result.stdout)


async def run_warnungen(ctx: "ExtensionContext") -> dict[str, int]:
    settings = await ctx.settings.get()
    disk_on = settings.get("warn_disk_enabled", True) is not False
    temp_on = settings.get("warn_temp_enabled", True) is not False
    disk_limit = int(settings.get("warn_disk_percent") or DEFAULT_DISK_PERCENT)
    temp_limit = int(settings.get("warn_temp_c") or DEFAULT_TEMP_C)
    if not (disk_on or temp_on):
        return {"checked": 0, "unreachable": 0, "notified": 0}

    path = Path(str(ctx.data_dir)) / STATE_FILE
    state = _load(path, ctx.logger)
    all_hosts = await ctx.hosts.list()
    hosts = warn_hosts(all_hosts, settings.get("warn_skip_hosts") or [])
    # Zustand entfernter Server aufräumen, sonst wächst die Datei ewig.
    known = {h.id for h in all_hosts}
    for key in [k for k in state if k.split("|", 1)[0] not in known]:
        del state[key]

    candidates = [h for h in hosts if getattr(h.status, "value", h.status) != "down"]
    for host in hosts:
        if host not in candidates:
            ctx.logger.info("system-Warnungen: %s ist aus, übersprungen", host.name)
    sem = asyncio.Semaphore(_PARALLEL_HOSTS)
    readings = await asyncio.gather(*(_read_host(ctx, h, sem) for h in candidates))

    now = time.time()
    sent = unreachable = 0
    for host, reading in zip(candidates, readings):
        if reading is None:
            unreachable += 1
            continue
        name = host.display_name or host.name
        if disk_on:
            for disk in reading["disks"]:
                sent += await _step(
                    ctx, state, f"{host.id}|disk|{disk['mount']}", host.id, whole(disk["percent"]), disk_limit, now,
                    lambda kind, note, d=disk: disk_notification(kind, name, host.id, d, disk_limit, note=note),
                )
        if temp_on:
            several = len(reading["temps"]) > 1
            for temp in reading["temps"]:
                sent += await _step(
                    ctx, state, f"{host.id}|temp|{temp['sensor']}", host.id, whole(temp["celsius"]), temp_limit, now,
                    lambda kind, note, t=temp: temp_notification(kind, name, host.id, t, temp_limit, several=several, note=note),
                )
    try:
        _save(path, state)
    except OSError as exc:
        ctx.logger.warning("system-Warnungen: Zustand konnte nicht gespeichert werden (%s)", exc)
    return {"checked": len(candidates) - unreachable, "unreachable": unreachable, "notified": sent}
