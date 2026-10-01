"""Live-Detail eines Linux-Hosts wie im Task-Manager/HWiNFO -- in EINEM SSH-Aufruf.

Moeglichst genau: pro Kern, Takt, Temperaturen,
RAM-Aufschluesselung, jede Platte und jede Netzwerkkarte einzeln, Prozesse. Alles aus
`/proc` und `/sys`, ohne root und ohne Zusatzpakete (auf dem Pi zusaetzlich
`vcgencmd` fuer Drosselung/Unterspannung, falls vorhanden).

Raten (CPU, Platten-/Netz-Durchsatz, Prozess-CPU) brauchen zwei Messpunkte: das Skript
liest die Zaehler, schlaeft 1 s und liest sie erneut. Die echte Zeitspanne kommt aus
`/proc/uptime` (Hundertstelsekunden), nicht aus der Annahme "genau 1 s".

`metrics_from_live()` macht daraus die flachen Werte fuer den Verlauf (Kern:
core/metrics_history.py) -- dieselben Schluessel wie der Proxmox-Anbieter, soweit es
sie dort gibt.
"""

from __future__ import annotations

import re
from typing import Any

_SNAPSHOT = r"""echo @@t{n}; cut -d' ' -f1 /proc/uptime
echo @@stat{n}; grep '^cpu' /proc/stat
echo @@net{n}; tail -n +3 /proc/net/dev
echo @@dsk{n}; cat /proc/diskstats
echo @@proc{n}; cat /proc/[0-9]*/stat 2>/dev/null
"""

LIVE_SCRIPT = (
    _SNAPSHOT.format(n=0)
    + "sleep 1\n"
    + _SNAPSHOT.format(n=1)
    + r"""echo @@sys; getconf PAGESIZE; getconf CLK_TCK
echo @@mem; cat /proc/meminfo
echo @@load; cat /proc/loadavg
echo @@model; grep -m1 -E '^(model name|Model|Hardware)' /proc/cpuinfo
echo @@freq; for f in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq; do [ -r "$f" ] && echo "${f#/sys/devices/system/cpu/cpu}" | cut -d/ -f1 | tr '\n' ' ' && cat "$f"; done 2>/dev/null
echo @@temps; for z in /sys/class/thermal/thermal_zone*; do [ -r "$z/temp" ] && echo "zone $(cat $z/type) $(cat $z/temp)"; done 2>/dev/null; for h in /sys/class/hwmon/hwmon*; do n=$(cat $h/name 2>/dev/null); for t in $h/temp*_input; do [ -r "$t" ] || continue; l=${t%_input}_label; if [ -r "$l" ]; then lab=$(cat $l); else lab=$(basename ${t%_input}); fi; echo "hwmon $n|$lab|$(cat $t)"; done; done 2>/dev/null
echo @@fans; for h in /sys/class/hwmon/hwmon*; do n=$(cat $h/name 2>/dev/null); for t in $h/fan*_input; do [ -r "$t" ] || continue; echo "$n|$(basename ${t%_input})|$(cat $t)"; done; done 2>/dev/null
echo @@links; for n in /sys/class/net/*; do echo "$(basename $n) $(cat $n/speed 2>/dev/null || echo -) $(cat $n/operstate 2>/dev/null)"; done
echo @@fs; df -P -T -x tmpfs -x devtmpfs -x overlay -x squashfs -x efivarfs 2>/dev/null | tail -n +2
echo @@users; ps -eo pid=,user= 2>/dev/null
echo @@throttled; vcgencmd get_throttled 2>/dev/null
echo @@end
"""
)

_KB = 1024
_SECTOR = 512
_PHYSICAL_DISK = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")
_VIRTUAL_NIC = re.compile(r"^(lo|veth.*|br-.*|docker\d*|virbr.*|tap.*|fwbr.*|fwpr.*|fwln.*|ifb\d+|tailscale\d*|wg\d*|tun\d*|vmbr\d+v\d+)$")
_THROTTLE_BITS = {
    0: "Unterspannung", 1: "Takt begrenzt", 2: "gedrosselt", 3: "Temperaturgrenze",
    16: "Unterspannung (seit Start)", 17: "Takt begrenzt (seit Start)",
    18: "gedrosselt (seit Start)", 19: "Temperaturgrenze (seit Start)",
}


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


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _cpu_lines(lines: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for line in lines:
        parts = line.split()
        if parts and parts[0].startswith("cpu"):
            out[parts[0]] = [int(v) for v in parts[1:] if v.isdigit()]
    return out


def _cpu_usage(a: list[int], b: list[int]) -> dict[str, float] | None:
    """Felder: user nice system idle iowait irq softirq steal ..."""
    if len(a) < 5 or len(b) < 5:
        return None
    delta = [y - x for x, y in zip(a, b)]
    total = sum(delta[:8])
    if total <= 0:
        return None

    def pct(*idx: int) -> float:
        return round(100.0 * sum(delta[i] for i in idx if i < len(delta)) / total, 1)

    return {
        "percent": round(100.0 - pct(3, 4), 1),
        "user": pct(0, 1),
        "system": pct(2, 5, 6),
        "iowait": pct(4),
        "steal": pct(7),
    }


def _net(lines: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for line in lines:
        name, _, rest = line.partition(":")
        values = [int(v) for v in rest.split() if v.isdigit()]
        if name.strip() and len(values) >= 16:
            out[name.strip()] = values
    return out


def _diskstats(lines: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for line in lines:
        parts = line.split()
        if len(parts) >= 14 and _PHYSICAL_DISK.match(parts[2]):
            out[parts[2]] = [int(v) for v in parts[3:14]]
    return out


def _procs(lines: list[str]) -> dict[int, tuple[str, int, int]]:
    """/proc/<pid>/stat: `pid (comm) state ...` -- comm darf Leerzeichen/Klammern
    enthalten, deshalb am LETZTEN ')' trennen. -> pid: (name, utime+stime, rss_pages)."""
    out: dict[int, tuple[str, int, int]] = {}
    for line in lines:
        head, sep, tail = line.rpartition(")")
        if not sep:
            continue
        pid_str, _, name = head.partition(" (")
        fields = tail.split()
        if not pid_str.strip().isdigit() or len(fields) < 22:
            continue
        # tail beginnt bei Feld 3 (state): utime=Feld 14 -> Index 11, stime 12, rss 24 -> 21
        try:
            out[int(pid_str)] = (name, int(fields[11]) + int(fields[12]), int(fields[21]))
        except ValueError:
            continue
    return out


def _meminfo(lines: list[str]) -> dict[str, int]:
    mem: dict[str, int] = {}
    for line in lines:
        key, _, rest = line.partition(":")
        number = rest.strip().split(" ")[0]
        if number.isdigit():
            mem[key.strip()] = int(number) * _KB
    return mem


def _temperatures(lines: list[str]) -> list[dict[str, Any]]:
    """thermal_zone* und hwmon* melden auf vielen Systemen (z. B. dem Pi) denselben
    Sensor doppelt: hwmon-Eintraege ohne eigenes Label ("temp1") mit demselben Wert wie
    eine thermal_zone werden verworfen; beschriftete hwmon-Sensoren ("Core 0",
    "Composite") bleiben immer."""
    zones: list[dict[str, Any]] = []
    hwmon: list[dict[str, Any]] = []
    for line in lines:
        if line.startswith("zone "):
            parts = line.split()
            if len(parts) >= 3 and parts[-1].lstrip("-").isdigit():
                zones.append({"source": "thermal", "label": " ".join(parts[1:-1]), "celsius": round(int(parts[-1]) / 1000, 1)})
        elif line.startswith("hwmon "):
            chunks = line[len("hwmon "):].split("|")
            if len(chunks) == 3 and chunks[2].strip().lstrip("-").isdigit():
                hwmon.append({"source": chunks[0].strip(), "label": chunks[1].strip(), "celsius": round(int(chunks[2].strip()) / 1000, 1)})
    # Derselbe Sensor unter zwei Namen (Pi: thermal_zone "cpu-thermal" == hwmon
    # "cpu_thermal/temp1"), zwei Lesezeitpunkte -> leicht andere Werte. Ein unbeschrifteter
    # hwmon-Eintrag faellt weg, wenn eine thermal_zone gleichen Namens oder gleichen Werts existiert.
    def _norm(name: str) -> str:
        return re.sub(r"[^a-z0-9]", "", name.lower())

    zone_names = {_norm(z["label"]) for z in zones}
    zone_values = {z["celsius"] for z in zones}
    hwmon = [
        h for h in hwmon
        if not (re.fullmatch(r"temp\d+", h["label"]) and (_norm(h["source"]) in zone_names or h["celsius"] in zone_values))
    ]
    return [t for t in zones + hwmon if -40 < t["celsius"] < 150]


def _throttled(lines: list[str]) -> dict[str, Any] | None:
    for line in lines:
        if line.startswith("throttled="):
            try:
                value = int(line.split("=", 1)[1], 16)
            except ValueError:
                return None
            return {"raw": hex(value), "flags": [label for bit, label in _THROTTLE_BITS.items() if value & (1 << bit)]}
    return None


def parse_live(text: str) -> dict[str, Any]:
    s = _sections(text)
    t0, t1 = _float((s.get("t0") or [None])[0]), _float((s.get("t1") or [None])[0])
    dt = (t1 - t0) if t0 is not None and t1 is not None and t1 > t0 else 1.0
    sysconf = s.get("sys", [])
    page_size = int(sysconf[0]) if sysconf and sysconf[0].isdigit() else 4096
    clk_tck = int(sysconf[1]) if len(sysconf) > 1 and sysconf[1].isdigit() else 100

    # CPU gesamt + je Kern
    c0, c1 = _cpu_lines(s.get("stat0", [])), _cpu_lines(s.get("stat1", []))
    total = _cpu_usage(c0.get("cpu", []), c1.get("cpu", [])) if "cpu" in c0 and "cpu" in c1 else None
    freqs: dict[str, float] = {}
    for line in s.get("freq", []):
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            freqs[parts[0]] = round(int(parts[1]) / 1000)
    cores = []
    for name in sorted((k for k in c1 if k != "cpu" and k in c0), key=lambda k: int(k[3:]) if k[3:].isdigit() else 0):
        usage = _cpu_usage(c0[name], c1[name])
        core_id = name[3:]
        cores.append({"id": int(core_id) if core_id.isdigit() else core_id,
                      "percent": usage["percent"] if usage else None, "freq_mhz": freqs.get(core_id)})
    model_line = (s.get("model") or [""])[0]
    load = [v for v in (_float(x) for x in ((s.get("load") or [""])[0].split()[:3])) if v is not None]

    # Speicher
    m = _meminfo(s.get("mem", []))
    memory = None
    if "MemTotal" in m:
        available = m.get("MemAvailable", m.get("MemFree", 0))
        memory = {
            "total": m["MemTotal"], "used": m["MemTotal"] - available, "available": available,
            "free": m.get("MemFree"), "buffers": m.get("Buffers"),
            "cached": (m.get("Cached", 0) + m.get("SReclaimable", 0)) if "Cached" in m else None,
            "shared": m.get("Shmem"), "dirty": m.get("Dirty"),
            "swap_total": m.get("SwapTotal"),
            "swap_used": (m["SwapTotal"] - m["SwapFree"]) if "SwapTotal" in m and "SwapFree" in m else None,
        }

    # Platten
    d0, d1 = _diskstats(s.get("dsk0", [])), _diskstats(s.get("dsk1", []))
    disks = []
    for name in sorted(d1):
        if name not in d0:
            continue
        a, b = d0[name], d1[name]
        disks.append({
            "name": name,
            "read_bps": round((b[2] - a[2]) * _SECTOR / dt),
            "write_bps": round((b[6] - a[6]) * _SECTOR / dt),
            "read_iops": round((b[0] - a[0]) / dt, 1),
            "write_iops": round((b[4] - a[4]) / dt, 1),
            # "Aktive Zeit" wie im Task-Manager: Anteil der Zeit mit laufendem I/O.
            "busy_percent": min(100.0, round((b[9] - a[9]) / (dt * 10), 1)),
        })

    # Netzwerk
    n0, n1 = _net(s.get("net0", [])), _net(s.get("net1", []))
    links = {}
    for line in s.get("links", []):
        parts = line.split()
        if parts:
            speed = parts[1] if len(parts) > 1 else "-"
            links[parts[0]] = {"speed_mbps": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None,
                               "state": parts[2] if len(parts) > 2 else None}
    network = []
    for name in sorted(n1):
        if name not in n0:
            continue
        a, b = n0[name], n1[name]
        network.append({
            "name": name, "virtual": bool(_VIRTUAL_NIC.match(name)),
            "rx_bps": round((b[0] - a[0]) / dt), "tx_bps": round((b[8] - a[8]) / dt),
            "rx_total": b[0], "tx_total": b[8],
            "errors": b[2] + b[10], "drops": b[3] + b[11],
            **links.get(name, {"speed_mbps": None, "state": None}),
        })

    # Dateisysteme
    filesystems = []
    for line in s.get("fs", []):
        parts = line.split()
        if len(parts) < 7 or not parts[2].isdigit():
            continue
        size, used, avail = (int(parts[i]) * _KB for i in (2, 3, 4))
        filesystems.append({"device": parts[0], "fstype": parts[1], "mount": " ".join(parts[6:]),
                            "size": size, "used": used, "available": avail,
                            "percent": round(100.0 * used / (used + avail), 1) if used + avail else 0.0})

    # Prozesse: CPU wie im Task-Manager = Anteil an ALLEN Kernen.
    p0, p1 = _procs(s.get("proc0", [])), _procs(s.get("proc1", []))
    users: dict[int, str] = {}
    for line in s.get("users", []):
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            users[int(parts[0])] = parts[1]
    ncores = max(1, len(cores))
    processes = []
    for pid, (name, ticks, rss) in p1.items():
        prev = p0.get(pid)
        cpu = ((ticks - prev[1]) / clk_tck / dt * 100.0 / ncores) if prev else 0.0
        processes.append({"pid": pid, "name": name, "user": users.get(pid),
                          "cpu_percent": round(max(cpu, 0.0), 1), "mem_bytes": rss * page_size})
    processes.sort(key=lambda p: (p["cpu_percent"], p["mem_bytes"]), reverse=True)

    fans = []
    for line in s.get("fans", []):
        chunks = line.split("|")
        if len(chunks) == 3 and chunks[2].strip().isdigit():
            fans.append({"label": f"{chunks[0]} {chunks[1]}".strip(), "rpm": int(chunks[2])})

    uptime = t1 if t1 is not None else t0
    return {
        "complete": "end" in s,
        "interval_s": round(dt, 2),
        "uptime_s": int(uptime) if uptime is not None else None,
        "cpu": {
            "model": model_line.split(":", 1)[1].strip() if ":" in model_line else None,
            "cores": len(cores),
            "total": total,
            "per_core": cores,
            "load": load,
        },
        "memory": memory,
        "temperatures": _temperatures(s.get("temps", [])),
        "fans": fans,
        "disks": disks,
        "network": network,
        "filesystems": filesystems,
        "processes": processes[:25],
        "process_count": len(p1),
        "throttled": _throttled(s.get("throttled", [])),
    }


def metrics_from_live(live: dict[str, Any]) -> dict[str, float]:
    """Flache Werte fuer Live-Ringe und Verlauf -- Schluessel wie beim Proxmox-Anbieter."""
    values: dict[str, float] = {}
    total = live["cpu"].get("total")
    if total:
        values["cpu_percent"] = total["percent"]
        values["cpu_iowait_percent"] = total["iowait"]
    for key, value in zip(("load_1", "load_5", "load_15"), live["cpu"].get("load") or []):
        values[key] = value
    mem = live.get("memory")
    if mem:
        values["mem_total_bytes"] = float(mem["total"])
        values["mem_used_bytes"] = float(mem["used"])
        if mem.get("swap_total"):
            values["swap_total_bytes"] = float(mem["swap_total"])
            values["swap_used_bytes"] = float(mem["swap_used"] or 0)
    physical = [n for n in live["network"] if not n["virtual"]]
    if physical:
        values["net_in_bps"] = float(sum(n["rx_bps"] for n in physical))
        values["net_out_bps"] = float(sum(n["tx_bps"] for n in physical))
    if live["disks"]:
        values["disk_read_bps"] = float(sum(d["read_bps"] for d in live["disks"]))
        values["disk_write_bps"] = float(sum(d["write_bps"] for d in live["disks"]))
        values["disk_busy_percent"] = max(d["busy_percent"] for d in live["disks"])
    if live["temperatures"]:
        values["temp_c"] = max(t["celsius"] for t in live["temperatures"])
    root = next((f for f in live["filesystems"] if f["mount"] == "/"), None)
    if root:
        values["root_used_bytes"] = float(root["used"])
        values["root_total_bytes"] = float(root["size"])
    if live.get("uptime_s") is not None:
        values["uptime_s"] = float(live["uptime_s"])
    return values
