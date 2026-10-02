"""system-Extension: Parser gegen die ECHTE Ausgabe eines Raspberry Pi
(Debian 13), Grenzfaelle, Registrierung."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "system" / "src"))

from nodvard_deck_ext_system.sysinfo import parse_system_info, summary_findings  # noqa: E402

from nodvard_deck.services import extensions as extensions_service  # noqa: E402

PI = """@@os
Debian GNU/Linux 13 (trixie)
@@kernel
6.18.39+rpt-rpi-v8
@@arch
aarch64
@@hostname
Raspberry Pi
@@uptime
70454.95
@@load
0.58 1.00 0.76
@@cpus
4
@@cpu
cpu  1215487 607 419623 26328615 27883 0 21499 0 0 0
cpu  1215494 607 419627 26329004 27883 0 21500 0 0 0
@@mem
MemTotal:        3885880 kB
MemAvailable:    2171408 kB
SwapTotal:       2097148 kB
SwapFree:         609720 kB
@@disks
/dev/sda2      ext4   244500116 68310968 163696436      30% /
/dev/sda1      vfat      523244    68472    454772      14% /boot/firmware
/dev/mmcblk0p1 ext4    61200196     2072  58056904       1% /mnt/sd_backup
@@failed
@@updates
linux-base-rpi-2712/stable
linux-image-rpi-v8/stable
openssl/stable-security
@@reboot
no
@@temp
44303
@@end
"""


def test_parses_the_real_pi_output():
    info = parse_system_info(PI)
    assert info["complete"] is True
    assert (info["os"], info["kernel"], info["arch"], info["hostname"]) == (
        "Debian GNU/Linux 13 (trixie)", "6.18.39+rpt-rpi-v8", "aarch64", "Raspberry Pi",
    )
    assert (info["uptime_s"], info["load"], info["cpus"]) == (70454, [0.58, 1.0, 0.76], 4)
    assert info["cpu_percent"] == 3.0  # 12 aktive von 401 Ticks
    assert info["mem_total"] == 3885880 * 1024
    assert info["swap_used"] == (2097148 - 609720) * 1024
    assert [d["mount"] for d in info["disks"]] == ["/", "/boot/firmware", "/mnt/sd_backup"]
    assert info["disks"][0]["percent"] == 29.4 and info["disks"][0]["tone"] == "good"
    assert info["failed_units"] == []
    assert info["updates"] == [
        {"package": "linux-base-rpi-2712", "security": False},
        {"package": "linux-image-rpi-v8", "security": False},
        {"package": "openssl", "security": True},
    ]
    assert (info["reboot_required"], info["temperature_c"]) == (False, 44.3)
    # Swap zu 71 % belegt und ein Sicherheits-Update -- genau das soll auffallen.
    assert [f["text"] for f in summary_findings(info)] == [
        "Swap zu 71 % belegt – RAM knapp", "1 Sicherheits-Update(s) ausstehend",
    ]


def test_running_services_are_listed_sorted_and_only_services():
    info = parse_system_info("@@os\nDebian\n@@services\nnginx.service\ncron.service\nnginx.service\nfoo.mount\n@@updates\n@unsupported\n@@end\n")
    assert info["running_units"] == ["cron.service", "nginx.service"]
    # Ohne den Abschnitt (kein systemd): leer, kein Fehler.
    assert parse_system_info(PI)["running_units"] == []


def test_script_lists_running_services():
    from nodvard_deck_ext_system.sysinfo import SCRIPT

    assert "@@services" in SCRIPT and "--state=running" in SCRIPT


def test_missing_tools_leave_only_their_section_empty():
    info = parse_system_info(
        "@@os\nAlpine Linux v3.20\n@@disks\n/dev/vda1 ext4 1000 950 50 95% /\n@@failed\nnginx.service\n"
        "@@updates\n@unsupported\n@@reboot\nyes\n@@temp\n@@end\n"
    )
    assert info["updates"] is None
    assert info["cpu_percent"] is None and info["swap_used"] is None and info["temperature_c"] is None
    texts = [f["text"] for f in summary_findings(info)]
    assert texts == ["/ zu 95 % voll", "1 Dienst(e) fehlgeschlagen: nginx.service", "Neustart nötig (nach Updates)"]


def _with_kernels(running: str, installed: list[str], reboot_file: str = "no") -> str:
    return (
        f"@@os\nDebian\n@@kernel\n{running}\n@@reboot\n{reboot_file}\n@@kernels\n"
        + "".join(f"{k}\n" for k in installed) + "@@end\n"
    )


def test_newer_installed_kernel_means_reboot_even_without_reboot_required_file():
    """Proxmox und Debian legen /var/run/reboot-required nicht an: ein
    neuerer installierter Kernel derselben Art zaehlt trotzdem als "Neustart noetig".
    Versionsvergleich numerisch ("-10-" ist neuer als "-4-"), nicht als Text."""
    proxmox = parse_system_info(_with_kernels("6.8.12-4-pve", ["6.8.12-10-pve", "6.8.12-4-pve", "6.8.12-9-pve"]))
    assert proxmox["reboot_required"] is True
    assert proxmox["newest_kernel"] == "6.8.12-10-pve"
    assert "Neustart nötig: neuer Kernel 6.8.12-10-pve installiert" in [f["text"] for f in summary_findings(proxmox)]

    debian = parse_system_info(_with_kernels("6.1.0-25-amd64", ["6.1.0-25-amd64", "6.1.0-26-amd64"]))
    assert debian["reboot_required"] is True and debian["newest_kernel"] == "6.1.0-26-amd64"

    # Der alte Kernel wurde schon entfernt, laeuft aber noch.
    removed = parse_system_info(_with_kernels("6.8.12-4-pve", ["6.8.12-10-pve"]))
    assert removed["reboot_required"] is True


def test_running_newest_kernel_is_no_reboot():
    current = parse_system_info(_with_kernels("6.8.12-10-pve", ["6.8.12-4-pve", "6.8.12-10-pve"]))
    assert current["reboot_required"] is False and current["newest_kernel"] is None
    assert summary_findings(current) == []
    # Keine Kernel-Liste (z. B. kein /boot/vmlinuz-*): nur die Datei zaehlt.
    assert parse_system_info(_with_kernels("6.8.12-4-pve", []))["reboot_required"] is False
    assert parse_system_info(_with_kernels("6.8.12-4-pve", [], reboot_file="yes"))["reboot_required"] is True


def test_raspberry_pi_compares_only_kernels_of_the_same_flavor():
    """Pi 5: 2712- und v8-Kernel liegen nebeneinander in /boot; nur die laufende Art
    zaehlt, und der Vergleich darf nicht an gemischten Text/Zahl-Teilen scheitern."""
    pi5 = parse_system_info(_with_kernels("6.12.47+rpt-rpi-2712", ["6.12.47+rpt-rpi-2712", "6.12.47+rpt-rpi-v8"]))
    assert pi5["reboot_required"] is False
    pi5_old = parse_system_info(_with_kernels(
        "6.12.34+rpt-rpi-2712", ["6.12.34+rpt-rpi-2712", "6.12.34+rpt-rpi-v8", "6.12.47+rpt-rpi-2712", "6.12.47+rpt-rpi-v8"],
    ))
    assert pi5_old["reboot_required"] is True and pi5_old["newest_kernel"] == "6.12.47+rpt-rpi-2712"
    pi4 = parse_system_info(_with_kernels("6.18.39+rpt-rpi-v8", ["6.18.39+rpt-rpi-2712", "6.18.39+rpt-rpi-v8", "6.12.47+rpt-rpi-v8"]))
    assert pi4["reboot_required"] is False
    # Aelteres Raspberry-Pi-OS-Schema ("-v8+"): kein Absturz, richtig erkannt.
    old_pi = parse_system_info(_with_kernels("6.1.21-v8+", ["6.1.21-v8+", "6.1.63-v8+", "6.1.63-v7l+"]))
    assert old_pi["reboot_required"] is True and old_pi["newest_kernel"] == "6.1.63-v8+"


def test_script_lists_installed_kernels():
    from nodvard_deck_ext_system.sysinfo import SCRIPT

    assert "@@kernels" in SCRIPT and "/boot/vmlinuz-" in SCRIPT


def test_truncated_output_is_marked_incomplete():
    assert parse_system_info("@@os\nDebian\n@@kernel\n6.1\n")["complete"] is False


@pytest.mark.asyncio
async def test_page_and_linux_only_host_tool_are_registered(client, db_session, test_settings):
    from nodvard_deck.ext.runtime import get_extension_runtime

    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    token = (await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})).json()["access_token"]
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    enabled = await client.post("/api/v1/extensions/system/enable", headers={"Authorization": f"Bearer {token}"})
    assert enabled.status_code == 200, enabled.text

    pages = (await client.get("/api/v1/pages", headers={"Authorization": f"Bearer {token}"})).json()
    assert any(p["ext_id"] == "system" and p["component"] == "SystemPage" for p in pages)
    tools = [t for e, t in get_extension_runtime().ui.all_host_tools() if e == "system"]
    assert [(t.id, t.os_families) for t in tools] == [("system", ["linux"])]
    # Ohne Login kein Zugriff.
    assert (await client.get("/api/v1/ext/system/hosts/x/info")).status_code == 401


@pytest.mark.asyncio
async def test_system_asks_for_root_to_restart_services(client, db_session, test_settings):
    from nodvard_deck.ext.runtime import get_extension_runtime

    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    token = (await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})).json()["access_token"]
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    assert (await client.post("/api/v1/extensions/system/enable", headers={"Authorization": f"Bearer {token}"})).status_code == 200

    specs = [r for e, r in get_extension_runtime().ui.all_host_requirements() if e == "system"]
    assert [(r.id, r.needs_root, r.root_reason, r.tags, r.os_families) for r in specs] == [
        ("root", True, "Dienste neu starten", [], ["linux"]),
    ]


def test_widget_cache_is_dropped_when_the_server_list_changes():
    """Ein neuer Server darf nicht bis zu 5 Minuten im Widget „Server-Zustand“ fehlen."""
    from types import SimpleNamespace

    from nodvard_deck_ext_system import WidgetCache, hosts_key

    def host(id_: str, name: str, address: str = "192.168.2.10") -> SimpleNamespace:
        return SimpleNamespace(id=id_, name=name, display_name=name.title(), address=address)

    cache = WidgetCache(300)
    empty = hosts_key([])
    assert cache.get(empty, now=0) is None
    cache.put(empty, now=0, rows=[])
    assert cache.get(empty, now=100) == [], "gleiche Serverliste, noch frisch: aus dem Speicher"
    assert cache.get(empty, now=301) is None, "nach fünf Minuten wird neu gefragt"

    cache.put(empty, now=1000, rows=[])
    with_pi = hosts_key([host("h1", "bastel-pi")])
    assert cache.get(with_pi, now=1001) is None, "ein neuer Server macht den Speicher ungültig"
    cache.put(with_pi, now=1001, rows=[{"name": "Bastel-Pi"}])
    assert cache.get(with_pi, now=1002) == [{"name": "Bastel-Pi"}]
    # Neue Adresse oder anderer Name: ebenfalls neu abfragen.
    assert cache.get(hosts_key([host("h1", "bastel-pi", "192.168.2.11")]), now=1003) is None
    assert cache.get(hosts_key([host("h1", "anderer-name")]), now=1003) is None
    # Die Reihenfolge der Liste spielt keine Rolle.
    both = [host("h1", "bastel-pi"), host("h2", "nas")]
    assert hosts_key(both) == hosts_key(list(reversed(both)))


def test_error_text_is_plain_german_even_without_an_exception_message():
    import asyncio

    from nodvard_deck_ext_system import error_text

    assert "TimeoutError" not in error_text(TimeoutError())
    assert error_text(TimeoutError()) == "keine Antwort innerhalb der Zeitgrenze – ist der Server an?"
    assert error_text(asyncio.TimeoutError()) == error_text(TimeoutError())
    assert error_text(ConnectionRefusedError()).startswith("Verbindung abgelehnt")
    assert error_text(RuntimeError("Anmeldung abgelehnt")) == "Anmeldung abgelehnt"
    assert error_text(RuntimeError("")) == "Verbindung fehlgeschlagen"
    assert "Error" not in error_text(OSError())


def test_widget_row_worst_first_and_unreachable_is_danger():
    from types import SimpleNamespace

    from nodvard_deck_ext_system import widget_row

    host = SimpleNamespace(id="h1", name="pi", display_name="Raspberry Pi")
    info = parse_system_info(PI)
    info["findings"] = summary_findings(info)
    assert widget_row(host, info, None) == {
        "name": "Raspberry Pi", "host_id": "h1", "summary": "Swap zu 71 % belegt – RAM knapp", "badge": "2 Befunde", "tone": "warn",
    }
    ok = {**info, "findings": []}
    assert widget_row(host, ok, None)["summary"] == "Debian GNU/Linux 13 (trixie) – alles in Ordnung"
    assert widget_row(host, None, "nicht erreichbar: timeout")["tone"] == "danger"


def test_widget_row_text_is_the_worst_finding_not_the_first():
    """Platte zu 82 % voll (warn) steht in der Liste VOR dem ausgefallenen
    Dienst (danger) -- die rote Kachel zeigte trotzdem den Platten-Text."""
    from types import SimpleNamespace

    from nodvard_deck_ext_system import widget_row

    host = SimpleNamespace(id="h1", name="pi", display_name="Raspberry Pi")
    info = parse_system_info(
        "@@os\nDebian\n@@disks\n/dev/sda2 ext4 1000 820 180 82% /\n@@failed\nnginx.service\n@@reboot\nno\n@@end\n"
    )
    info["findings"] = summary_findings(info)
    assert [f["tone"] for f in info["findings"]] == ["warn", "danger"]
    row = widget_row(host, info, None)
    assert (row["summary"], row["tone"], row["badge"]) == ("1 Dienst(e) fehlgeschlagen: nginx.service", "danger", "2 Befunde")


@pytest.mark.asyncio
async def test_host_without_own_provider_gets_metrics_from_a_supporting_provider(client, db_session):
    """Der Pi hat keinen provider_ext_id -- bisher 404, jetzt misst ein Anbieter, der
    `supports()` bejaht. Ein Anbieter ohne `supports` wird nie ungefragt benutzt."""
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_sdk.capabilities import MetricsProvider

    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    token = (await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})).json()["access_token"]
    pi_id = (await client.post("/api/v1/hosts", json={"name": "pi-host", "address": "10.0.0.2"}, headers={"Authorization": f"Bearer {token}"})).json()["id"]

    class _NoSupports:
        async def sample(self, host):
            raise AssertionError("darf nicht gefragt werden")

        async def metric_names(self):
            return []

    class _Ssh:
        async def supports(self, host):
            return host.os_family == "linux"

        async def sample(self, host):
            return {"cpu_percent": 12.5}

        async def metric_names(self):
            return ["cpu_percent"]

    caps = get_extension_runtime().capabilities
    caps.provide("fremd", MetricsProvider, _NoSupports())
    caps.provide("system", MetricsProvider, _Ssh())
    r = await client.get(f"/api/v1/hosts/{pi_id}/metrics", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {"cpu_percent": 12.5}

