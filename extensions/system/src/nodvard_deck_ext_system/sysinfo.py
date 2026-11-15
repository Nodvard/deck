"""Systemzustand eines Linux-Servers in einem SSH-Aufruf: Betriebssystem, Last, RAM, Platten, Dienste, Updates, Neustart."""

from __future__ import annotations

import re
from typing import Any

# Ohne root und ohne Änderung am Server: `apt list --upgradable` liest nur die vorhandenen Paketlisten (Stand der letzten
# Aktualisierung durch den Server selbst). Einzige Ausnahme ist eine kurzlebige Datei von `mktemp` für `df`. Die
# Abschnitte mit `@@name` sorgen dafür, dass ein fehlender Befehl (kein systemd, kein apt) nur seinen eigenen Abschnitt
# leer lässt.
# `df` läuft im Hintergrund in diese Datei und wird nach 5 s abgeschossen: Ein hängendes Netzlaufwerk leert nur den
# Platten-Abschnitt (`@@dfstatus`: ok, timeout, failed oder unavailable; ein Exit-Code ungleich 0 mit gelieferten Zeilen zählt als ok, `df` meldet so einzelne unlesbare Einhängungen), und das Skript wartet nie auf den Prozess und
# hält keine Leitung offen. Scheitert `mktemp`, läuft `df` gar nicht, einen festen Ersatzpfad gibt es mit Absicht nicht.
# Netzlaufwerke werden nicht abgefragt, sondern nur aus /proc/mounts aufgelistet. Keine Variable als Befehl (zsh).
_DF_EXCLUDES = "-x tmpfs -x devtmpfs -x overlay -x squashfs -x efivarfs -x ramfs -x iso9660 -x udf -x nfs -x nfs4 -x cifs -x smb3 -x fuse.sshfs"
DF_CMD = (
    'dfs=unavailable; dff=$(mktemp 2>/dev/null)\n'
    'if [ -n "$dff" ]; then\n'
    "  trap 'kill -9 $dfp 2>/dev/null; unlink \"$dff\" 2>/dev/null; exit 1' INT TERM HUP\n"
    f'  df -P -T {_DF_EXCLUDES} >"$dff" 2>/dev/null </dev/null &\n'
    "  dfp=$!; dfi=0; dfstep=0.2; dfmax=25\n"
    "  sleep 0.01 2>/dev/null || { dfstep=1; dfmax=5; }\n"
    '  while kill -0 $dfp 2>/dev/null && [ $dfi -lt $dfmax ]; do sleep $dfstep; dfi=$((dfi+1)); done\n'
    '  if kill -0 $dfp 2>/dev/null; then kill -9 $dfp 2>/dev/null; dfs=timeout\n'
    '  elif wait $dfp || [ -n "$(tail -n +2 "$dff")" ]; then dfs=ok; else dfs=failed; fi\n'
    '  dfp=; tail -n +2 "$dff"; unlink "$dff" 2>/dev/null; trap - INT TERM HUP\n'
    'fi\n'
    'echo @@dfstatus; echo $dfs\n'
    "echo @@netmounts; awk '$3==\"nfs\"||$3==\"nfs4\"||$3==\"cifs\"||$3==\"smb3\"||$3==\"fuse.sshfs\" {print $3, $2, $1}' /proc/mounts 2>/dev/null"
)

# `LC_ALL=C`: apt übersetzt die Marke „upgradable“ sonst in die Sprache des Servers, und `grep` fände nichts.
_SCRIPT_TEMPLATE = r"""echo @@os; (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")
echo @@kernel; uname -r
echo @@arch; uname -m
echo @@hostname; hostname
echo @@uptime; cut -d' ' -f1 /proc/uptime
echo @@load; cut -d' ' -f1-3 /proc/loadavg
echo @@cpus; nproc 2>/dev/null
echo @@cpu; head -1 /proc/stat; sleep 1; head -1 /proc/stat
echo @@mem; grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
echo @@disks; {DF_CMD}
echo @@failed; systemctl list-units --state=failed --no-legend --plain 2>/dev/null | awk '{print $1}'
echo @@services; systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null | awk '{print $1}'
echo @@updates; if command -v apt >/dev/null 2>&1; then LC_ALL=C apt list --upgradable 2>/dev/null | grep upgradable | cut -d' ' -f1; else echo @unsupported; fi
echo @@reboot; if [ -f /var/run/reboot-required ]; then echo yes; else echo no; fi
echo @@kernels; ls -1 /boot/vmlinuz-* 2>/dev/null | sed 's#.*/vmlinuz-##'
echo @@temp; cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null
echo @@end
"""
SCRIPT = _SCRIPT_TEMPLATE.replace("{DF_CMD}", DF_CMD)


_KB = 1024


def _sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("@@"):
            current = line[2:].strip()
            sections.setdefault(current, [])
        elif current is not None and line.strip():
            sections[current].append(line)
    return sections


def _first(sections: dict[str, list[str]], key: str) -> str | None:
    values = sections.get(key) or []
    return values[0].strip() if values else None


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _cpu_percent(lines: list[str]) -> float | None:
    """Zwei `cpu`-Zeilen aus /proc/stat im Abstand von 1 s -> Auslastung in Prozent."""
    samples = []
    for line in lines[:2]:
        parts = line.split()
        if not parts or parts[0] != "cpu":
            return None
        values = [int(v) for v in parts[1:] if v.isdigit()]
        if len(values) < 4:
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        samples.append((sum(values), idle))
    if len(samples) != 2:
        return None
    total = samples[1][0] - samples[0][0]
    idle = samples[1][1] - samples[0][1]
    return round(100.0 * (total - idle) / total, 1) if total > 0 else None


# Kernel-Name = Versionsnummer + Art ("6.8.12-4" + "-pve", "6.12.47" + "+rpt-rpi-2712",
# "6.1.0-25" + "-amd64", "6.1.21" + "-v8+").
_KERNEL_RE = re.compile(r"^(\d+(?:[.-]\d+)*)(.*)$")


def _kernel_key(version: str) -> tuple[tuple[int, int | str], ...]:
    """Typsicherer Versionsvergleich: Zahlen als (0, int), Text als (1, str), nie `str > int`."""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-+~]", version) if p)


def _newer_kernel(running: str | None, installed: list[str]) -> str | None:
    """Neuester installierter Kernel derselben Art wie der laufende, falls er neuer ist."""
    # Debian und Proxmox legen `/var/run/reboot-required` nicht an; ein neuerer Kernel in /boot heißt trotzdem: Neustart
    # nötig. Auf dem Pi liegen 2712- und v8-Kernel nebeneinander, deshalb zählen nur Kernel mit gleicher Endung.
    match = _KERNEL_RE.match(running or "")
    if not match:
        return None
    number, flavor = match.groups()
    candidates = []
    for name in installed:
        other = _KERNEL_RE.match(name.strip())
        if other and other.group(2) == flavor:
            candidates.append((_kernel_key(other.group(1)), name.strip()))
    if not candidates:
        return None
    newest_key, newest = max(candidates)
    return newest if newest_key > _kernel_key(number) else None


def _disk_tone(percent: float) -> str:
    return "danger" if percent >= 90 else "warn" if percent >= 80 else "good"


def _ascii_digits(text: str) -> bool:
    return text.isascii() and text.isdigit()


def parse_disks(lines: list[str]) -> list[dict[str, Any]]:
    """Zeilen von `df -P -T` (ohne Kopfzeile) -> Dateisysteme mit Belegung und Ton."""
    disks = []
    for line in lines:
        parts = line.split()
        # `df -P -T`: Gerät, Typ, Größe, belegt, frei, Prozent, Einhängepunkt (in 1K-Blöcken)
        if len(parts) < 7 or not all(_ascii_digits(parts[i]) for i in (2, 3, 4)):
            continue
        size, used, avail = (int(parts[i]) * _KB for i in (2, 3, 4))
        percent = round(100.0 * used / (used + avail), 1) if used + avail else 0.0
        disks.append({
            "device": parts[0], "fstype": parts[1], "mount": " ".join(parts[6:]),
            "size": size, "used": used, "available": avail, "percent": percent, "tone": _disk_tone(percent),
        })
    return disks


_OCTAL = re.compile(r"\\([0-7]{3})")


def _unescape(text: str) -> str:
    return _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), text)


def parse_network_mounts(lines: list[str]) -> list[dict[str, str]]:
    """Zeilen `Typ Einhängepunkt Quelle` aus /proc/mounts (Leerzeichen als \\040) -> Netzlaufwerke ohne Messwerte."""
    mounts = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            mounts.append({"fstype": parts[0], "mount": _unescape(parts[1]), "device": _unescape(parts[2])})
    return mounts


def df_state(sections: dict[str, list[str]]) -> dict[str, Any]:
    """Wie `df` endete (`df_status`: ok, timeout, failed, unavailable; ohne Marke None) und ob das ein Fehler war."""
    status = _first(sections, "dfstatus")
    return {"df_status": status, "df_failed": status not in (None, "ok")}


def parse_system_info(text: str) -> dict[str, Any]:
    s = _sections(text)
    mem = {}
    for line in s.get("mem", []):
        key, _, rest = line.partition(":")
        number = rest.strip().split(" ")[0]
        if number.isdigit():
            mem[key.strip()] = int(number) * _KB

    disks = parse_disks(s.get("disks", []))

    updates_raw = s.get("updates", [])
    if updates_raw[:1] == ["@unsupported"]:
        updates: list[dict[str, Any]] | None = None
    else:
        updates = [
            {"package": line.split("/", 1)[0], "security": "-security" in line.split("/", 1)[-1]}
            for line in updates_raw
        ]

    temp_raw = _first(s, "temp")
    temp = round(int(temp_raw) / 1000, 1) if temp_raw and temp_raw.lstrip("-").isdigit() else None
    load = [_float(v) for v in (_first(s, "load") or "").split()]
    cpus = _first(s, "cpus")
    uptime = _float(_first(s, "uptime"))
    kernel = _first(s, "kernel")
    newest_kernel = _newer_kernel(kernel, s.get("kernels", []))

    return {
        "complete": "end" in s,
        "os": _first(s, "os"),
        "kernel": kernel,
        "arch": _first(s, "arch"),
        "hostname": _first(s, "hostname"),
        "uptime_s": int(uptime) if uptime is not None else None,
        "load": [v for v in load if v is not None],
        "cpus": int(cpus) if cpus and cpus.isdigit() else None,
        "cpu_percent": _cpu_percent(s.get("cpu", [])),
        "mem_total": mem.get("MemTotal"),
        "mem_available": mem.get("MemAvailable"),
        "swap_total": mem.get("SwapTotal"),
        "swap_used": (mem["SwapTotal"] - mem["SwapFree"]) if "SwapTotal" in mem and "SwapFree" in mem else None,
        "disks": disks,
        "network_mounts": parse_network_mounts(s.get("netmounts", [])),
        **df_state(s),
        "failed_units": [u for u in s.get("failed", []) if u.strip()],
        "running_units": sorted({u.strip() for u in s.get("services", []) if u.strip().endswith(".service")}),
        "updates": updates,
        "reboot_required": _first(s, "reboot") == "yes" or newest_kernel is not None,
        "newest_kernel": newest_kernel,
        "temperature_c": temp,
    }


def apply_thresholds(info: dict[str, Any], disk_percent: float, temp_c: float) -> None:
    """Trägt die Warnschwellen in die Anzeige ein: Dateisysteme ab `disk_percent` und Temperatur ab `temp_c` gelb."""
    for disk in info.get("disks", []):
        if disk["percent"] >= disk_percent and disk["tone"] == "good":
            disk["tone"] = "warn"
    info["temp_warn_c"] = temp_c
    temp = info.get("temperature_c")
    info["temperature_tone"] = "warn" if temp is not None and temp >= temp_c else "good"


_DF_FINDINGS = {
    "timeout": "Plattenbelegung nicht gelesen: keine Antwort in der Zeitgrenze, wahrscheinlich hängt ein Laufwerk",
    "unavailable": "Plattenbelegung konnte auf dem Server nicht gemessen werden",
}


def summary_findings(info: dict[str, Any]) -> list[dict[str, str]]:
    """Was Aufmerksamkeit braucht, mit denselben Schwellen wie die Anzeige."""
    findings: list[dict[str, str]] = []
    for disk in info.get("disks", []):
        if disk["tone"] != "good":
            findings.append({"tone": disk["tone"], "text": f"{disk['mount']} zu {disk['percent']:.0f} % voll"})
    if info.get("df_failed"):
        text = _DF_FINDINGS.get(info.get("df_status") or "", "Plattenbelegung konnte nicht gelesen werden")
        findings.append({"tone": "warn", "text": text})
    if info.get("failed_units"):
        findings.append({"tone": "danger", "text": f"{len(info['failed_units'])} Dienst(e) fehlgeschlagen: {', '.join(info['failed_units'][:5])}"})
    if info.get("swap_total") and info.get("swap_used") is not None and info["swap_used"] / info["swap_total"] >= 0.5:
        findings.append({"tone": "warn", "text": f"Swap zu {100 * info['swap_used'] / info['swap_total']:.0f} % belegt – RAM knapp"})
    if info.get("newest_kernel"):
        findings.append({"tone": "warn", "text": f"Neustart nötig: neuer Kernel {info['newest_kernel']} installiert"})
    elif info.get("reboot_required"):
        findings.append({"tone": "warn", "text": "Neustart nötig (nach Updates)"})
    security = [u for u in info.get("updates") or [] if u["security"]]
    if security:
        findings.append({"tone": "warn", "text": f"{len(security)} Sicherheits-Update(s) ausstehend"})
    if info.get("temperature_c") is not None and info["temperature_c"] >= info.get("temp_warn_c", 75):
        findings.append({"tone": "warn", "text": f"Temperatur {info['temperature_c']:.0f} °C"})
    return findings
