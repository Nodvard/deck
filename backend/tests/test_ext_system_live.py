"""Live-Detail der system-Extension (Task-Manager/HWiNFO-Ansicht): Parser gegen eine
Pi-typische Ausgabe, Raten, Duplikat-Sensoren, virtuelle Netzwerkkarten."""

from __future__ import annotations

import subprocess
import sys

import pytest
from pathlib import Path

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "system" / "src"))

from nodvard_deck_ext_system.live import LIVE_SCRIPT, metrics_from_live, parse_live  # noqa: E402

_NET_HEAD = "    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0"


def _snapshot(n: int, *, t: str, cpu: list[str], eth_rx: int, eth_tx: int, sda: str, proc_ticks: tuple[int, int]) -> str:
    return "\n".join([
        f"@@t{n}", t,
        f"@@stat{n}", *cpu,
        f"@@net{n}", _NET_HEAD,
        f"  eth0: {eth_rx} 100 0 0 0 0 0 0 {eth_tx} 90 0 0 0 0 0 0",
        f"docker0: 500 5 0 0 0 0 0 0 500 5 0 0 0 0 0 0",
        f"veth12ab: 999999 5 0 0 0 0 0 0 999999 5 0 0 0 0 0 0",
        f"@@dsk{n}",
        f"   8       0 sda {sda}",
        "   8       1 sda1 1 0 8 0 1 0 8 0 0 0 0",
        f"@@proc{n}",
        f"1 (systemd) S 0 1 1 0 -1 4194560 0 0 0 0 {proc_ticks[0]} 0 0 0 20 0 1 0 5 1000 2000",
        f"812 (python3 (lattice)) S 1 812 812 0 -1 4194560 0 0 0 0 {proc_ticks[1]} 0 0 0 20 0 9 0 99 1000 25600",
    ])


PI_LIVE = "\n".join([
    _snapshot(0, t="70454.00", cpu=[
        "cpu  1000 0 500 8000 100 0 0 0 0 0",
        "cpu0 250 0 125 2000 25 0 0 0 0 0",
        "cpu1 250 0 125 2000 25 0 0 0 0 0",
    ], eth_rx=1_000_000, eth_tx=500_000, sda="100 0 2000 0 50 0 4000 0 0 1000 0", proc_ticks=(10, 100)),
    _snapshot(1, t="70456.00", cpu=[
        # +400 Ticks gesamt: 100 user, 50 system, 200 idle, 50 iowait -> 62.5 % belegt
        "cpu  1100 0 550 8200 150 0 0 0 0 0",
        # Kern 0 voll ausgelastet, Kern 1 leer
        "cpu0 350 0 175 2000 25 0 0 0 0 0",
        "cpu1 250 0 125 2150 75 0 0 0 0 0",
    ], eth_rx=3_000_000, eth_tx=900_000, sda="120 0 6096 0 70 0 8096 0 0 1500 0", proc_ticks=(10, 300)),
    "@@sys", "4096", "100",
    "@@mem",
    "MemTotal:        3885880 kB", "MemFree:          400000 kB", "MemAvailable:    2171408 kB",
    "Buffers:           50000 kB", "Cached:          1500000 kB", "SReclaimable:     100000 kB",
    "Shmem:             20000 kB", "Dirty:               100 kB",
    "SwapTotal:       2097148 kB", "SwapFree:         609720 kB",
    "@@load", "0.58 1.00 0.76 2/300 999",
    "@@model", "Model\t\t: Raspberry Pi 4 Model B Rev 1.5",
    "@@freq", "0 1500000", "1 600000",
    "@@temps",
    "zone cpu-thermal 52100",
    "hwmon cpu_thermal|temp1|53000",
    "hwmon nvme|Composite|41850",
    "@@fans",
    "@@links", "eth0 1000 up", "docker0 - down", "lo - unknown",
    "@@fs",
    "/dev/sda2      ext4 245000000 80000000 152000000  35% /",
    "@@users", "    1 root", "  812 lattice",
    "@@throttled", "throttled=0x50000",
    "@@end",
])


def test_parses_cpu_per_core_and_breakdown():
    live = parse_live(PI_LIVE)
    assert live["complete"] is True
    assert live["interval_s"] == 2.0
    cpu = live["cpu"]
    assert cpu["model"] == "Raspberry Pi 4 Model B Rev 1.5"
    assert cpu["total"] == {"percent": 37.5, "user": 25.0, "system": 12.5, "iowait": 12.5, "steal": 0.0}
    assert cpu["per_core"] == [
        {"id": 0, "percent": 100.0, "freq_mhz": 1500},
        {"id": 1, "percent": 0.0, "freq_mhz": 600},
    ]
    assert cpu["load"] == [0.58, 1.0, 0.76]


def test_rates_use_the_real_interval_not_one_second():
    live = parse_live(PI_LIVE)
    # 2 s Abstand laut /proc/uptime: Netz 2 MB/2 s, Platte 4096 Sektoren/2 s
    eth0 = next(n for n in live["network"] if n["name"] == "eth0")
    assert (eth0["rx_bps"], eth0["tx_bps"], eth0["speed_mbps"], eth0["virtual"]) == (1_000_000, 200_000, 1000, False)
    assert [d["name"] for d in live["disks"]] == ["sda"], "Partitionen (sda1) zaehlen nicht doppelt"
    sda = live["disks"][0]
    assert (sda["read_bps"], sda["write_bps"], sda["read_iops"], sda["busy_percent"]) == (1_048_576, 1_048_576, 10.0, 25.0)


def test_memory_temps_throttling_and_processes():
    live = parse_live(PI_LIVE)
    mem = live["memory"]
    assert mem["used"] == (3885880 - 2171408) * 1024
    assert mem["cached"] == 1_600_000 * 1024
    assert mem["swap_used"] == (2097148 - 609720) * 1024
    # hwmon "cpu_thermal/temp1" ist dieselbe thermal_zone "cpu-thermal" (Wert leicht anders gelesen).
    assert live["temperatures"] == [
        {"source": "thermal", "label": "cpu-thermal", "celsius": 52.1},
        {"source": "nvme", "label": "Composite", "celsius": 41.9},
    ]
    assert live["throttled"] == {"raw": "0x50000", "flags": ["Unterspannung (seit Start)", "gedrosselt (seit Start)"]}
    top = live["processes"][0]
    # 200 Ticks / 100 Hz / 2 s = 100 % eines Kerns = 50 % von 2 Kernen; Name mit Klammern bleibt heil.
    assert top == {"pid": 812, "name": "python3 (lattice)", "user": "lattice", "cpu_percent": 50.0, "mem_bytes": 25600 * 4096}


def test_flat_metrics_skip_virtual_nics():
    values = metrics_from_live(parse_live(PI_LIVE))
    assert values["net_in_bps"] == 1_000_000.0, "docker0/veth duerfen nicht mitzaehlen"
    assert values["cpu_percent"] == 37.5
    assert values["cpu_iowait_percent"] == 12.5
    assert values["temp_c"] == 52.1
    assert values["disk_busy_percent"] == 25.0
    assert values["root_total_bytes"] == 245000000 * 1024.0
    assert values["swap_used_bytes"] == (2097148 - 609720) * 1024.0


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell (Git-Bash-sh scheitert an Windows-Pfaden)")
def test_script_runs_on_a_real_linux_shell():
    """Das Skript selbst (nicht nur der Parser): laeuft ohne root und liefert alle
    Pflicht-Abschnitte -- hier gegen die Shell des Test-Rechners."""
    out = subprocess.run(["sh", "-c", LIVE_SCRIPT], capture_output=True, text=True, timeout=30).stdout
    live = parse_live(out)
    assert live["complete"] is True
    assert live["cpu"]["cores"] >= 1 and live["cpu"]["total"] is not None
    assert live["memory"]["total"] > 0
    assert live["processes"], "mindestens dieser Prozess selbst"
