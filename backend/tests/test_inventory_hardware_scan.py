"""Teile eines Servers auslesen (`nodvard_deck_ext_inventory.hardware`): Ausgaben echter Rechnertypen -> Vorschläge."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "extensions" / "inventory" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_deck_ext_inventory.hardware import (
    _SCRIPT,
    SCAN_COMMAND,
    format_size,
    memory_label,
    parse_scan,
)
from test_ext_inventory import (  # noqa: F401
    _auth_header,
    _cleanup_sys_path,
    _create_inventory_tables,
    _enable_inventory,
)

LSCPU_AMD = """Architecture:            x86_64
Vendor ID:               AuthenticAMD
Model name:              AMD Ryzen 7 5700G with Radeon Graphics
Thread(s) per core:      2
Core(s) per socket:      8
Socket(s):               1
"""

DISKS = json.dumps({"blockdevices": [
    {"name": "nvme0n1", "model": "Samsung SSD 980 PRO 1TB", "serial": "S5GXNX0R000000A", "size": 1000204886016, "rota": False, "tran": "nvme", "type": "disk"},
    {"name": "sda", "model": "WDC WD40EFZX-68AWUN0", "serial": "WD-WX00000000", "size": 4000787030016, "rota": True, "tran": "sata", "type": "disk"},
    {"name": "sdb", "model": "CT1000MX500SSD1", "serial": "2000E0000000", "size": 1000204886016, "rota": False, "tran": "sata", "type": "disk"},
    {"name": "loop0", "model": None, "serial": None, "size": 104857600, "rota": False, "tran": None, "type": "loop"},
    {"name": "sdc", "model": "Flash Disk", "serial": None, "size": 32010928128, "rota": False, "tran": "usb", "type": "disk"},
]})

PCI = """00:01.0 "VGA compatible controller" "Advanced Micro Devices, Inc. [AMD/ATI]" "Cezanne" -rc8 "ASRock Incorporation" "Device 1636"
02:00.0 "Ethernet controller" "Intel Corporation" "I350 Gigabit Network Connection" -r01 "Intel Corporation" "Ethernet Server Adapter I350-T2"
02:00.1 "Ethernet controller" "Intel Corporation" "I350 Gigabit Network Connection" -r01 "Intel Corporation" "Ethernet Server Adapter I350-T2"
00:14.0 "USB controller" "Advanced Micro Devices, Inc. [AMD]" "Device 1639"
"""


def _output(
    dmi: dict[str, str], cpu: str = LSCPU_AMD, mem: str = "MemTotal:       65701944 kB", disks: str = DISKS, pci: str = PCI,
    virt: str = "none", missing: str = "",
) -> str:
    dmi_lines = "\n".join(f"{k}={v}" for k, v in dmi.items())
    return (
        f"@@dmi\n{dmi_lines}\n@@virt\n{virt}\n@@cpu\n{cpu}\n@@mem\n{mem}\n@@disks\n{disks}\n@@pci\n{pci}\n"
        f"@@missing\n{missing}\n"
    )


def _by_kind(result):
    return {s["kind"]: s for s in result["suggestions"]}


def _disks(*devices: dict) -> str:
    base = {"model": None, "serial": None, "rota": False, "tran": "sata", "type": "disk", "size": 500107862016}
    return json.dumps({"blockdevices": [{**base, **dev} for dev in devices]})


def test_a_selfbuilt_server_becomes_a_list_of_parts():
    result = parse_scan(_output({
        "sys_vendor": "To Be Filled By O.E.M.", "product_name": "To Be Filled By O.E.M.",
        "board_vendor": "ASRock", "board_name": "B550M Pro4",
    }))
    assert result["virtual"] is False and result["missing"] == []
    names = [s["name"] for s in result["suggestions"]]
    # Platzhalter der Firmware fallen weg: kein „Gerät To Be Filled By O.E.M.“.
    assert not any("O.E.M" in n for n in names)
    kinds = _by_kind(result)
    assert "Gerät" not in kinds
    assert (kinds["Mainboard"]["manufacturer"], kinds["Mainboard"]["model"]) == ("ASRock", "B550M Pro4")
    assert (kinds["Prozessor"]["name"], kinds["Prozessor"]["manufacturer"], kinds["Prozessor"]["detail"]) == (
        "AMD Ryzen 7 5700G with Radeon Graphics", "AMD", "8 Kerne",
    )
    assert kinds["Arbeitsspeicher"]["name"] == "Arbeitsspeicher 64 GB"

    disks = [s for s in result["suggestions"] if s["key"].startswith("disk:")]
    assert [(d["kind"], d["name"], d["manufacturer"], d["serial_number"]) for d in disks] == [
        ("NVMe-SSD", "NVMe-SSD 1 TB (Samsung SSD 980 PRO 1TB)", "Samsung", "S5GXNX0R000000A"),
        ("Festplatte", "Festplatte 4 TB (WDC WD40EFZX-68AWUN0)", "WD", "WD-WX00000000"),
        ("SSD", "SSD 1 TB (CT1000MX500SSD1)", "Crucial", "2000E0000000"),
        ("USB-Datenträger", "USB-Datenträger 32 GB (Flash Disk)", None, None),
    ], "loop-Geräte zählen nicht"

    # Zwei Anschlüsse derselben Netzwerkkarte sind ein Teil; USB-Controller sind kein eigenes Teil.
    nic = kinds["Netzwerkkarte"]
    assert (nic["quantity"], nic["detail"], nic["hint"], nic["manufacturer"]) == (1, "2 Anschlüsse", "kann auch auf dem Mainboard sitzen", "Intel")
    # Was ins Inventar kommt (`detail`), ist eine echte Angabe; Hinweise für die Vorschau stehen getrennt (`hint`).
    assert (kinds["Arbeitsspeicher"]["detail"], kinds["Arbeitsspeicher"]["hint"]) == (None, "Summe aller Riegel")
    assert (kinds["Festplatte"]["detail"], kinds["Festplatte"]["hint"]) == (None, "/dev/sda")
    assert (kinds["Grafikkarte"]["model"], kinds["Grafikkarte"]["manufacturer"]) == ("Cezanne", "AMD")
    assert not any("USB controller" in n for n in names)
    # Vorab angekreuzt ist, was sicher ein eigenes Teil ist; Karten können auf dem Mainboard sitzen.
    assert {s["kind"]: s["preselect"] for s in result["suggestions"]} == {
        "Mainboard": True, "Prozessor": True, "Arbeitsspeicher": True, "NVMe-SSD": True, "Festplatte": True, "SSD": True,
        "USB-Datenträger": True, "Grafikkarte": False, "Netzwerkkarte": False,
    }


def test_a_branded_machine_is_one_device_with_its_model_and_no_extra_mainboard():
    result = parse_scan(_output(
        {"sys_vendor": "Dell Inc.", "product_name": "OptiPlex 7090", "board_vendor": "Dell Inc.", "board_name": "0K6CP3"},
        pci="",
    ))
    kinds = _by_kind(result)
    assert (kinds["Gerät"]["name"], kinds["Gerät"]["manufacturer"], kinds["Gerät"]["model"]) == ("Dell OptiPlex 7090", "Dell", "OptiPlex 7090")
    assert "Mainboard" not in kinds


def test_lenovo_shows_the_plain_model_name_instead_of_the_machine_type():
    result = parse_scan(_output(
        {"sys_vendor": "LENOVO", "product_name": "10T7S0X000", "product_version": "ThinkCentre M720q"}, pci="",
    ))
    assert _by_kind(result)["Gerät"]["name"] == "Lenovo ThinkCentre M720q"


@pytest.mark.parametrize(("dmi", "virt"), [
    ({"sys_vendor": "QEMU", "product_name": "Standard PC (Q35 + ICH9, 2009)"}, "none"),
    ({"sys_vendor": "", "product_name": ""}, "kvm"),
    ({"sys_vendor": "", "product_name": ""}, "lxc"),
])
def test_a_virtual_machine_or_container_is_marked_and_nothing_is_preselected(dmi, virt):
    result = parse_scan(_output(dmi, virt=virt, mem="MemTotal:        4022000 kB"))
    assert result["virtual"] is True
    assert result["suggestions"] and not any(s["preselect"] for s in result["suggestions"])
    # Zugeteilter Speicher ist krumm: aufgerundet, nicht auf die nächste Riegelgröße gezogen.
    assert _by_kind(result)["Arbeitsspeicher"]["name"] == "Arbeitsspeicher 4 GB"


def test_vm_words_count_only_as_whole_words():
    result = parse_scan(_output({"sys_vendor": "Xenon Systems", "product_name": "Workstation"}, pci=""))
    assert result["virtual"] is False


def test_a_raspberry_pi_without_dmi_and_lspci_still_gives_device_cpu_memory_and_card():
    cpu = "Architecture:  aarch64\nVendor ID:     ARM\nModel name:    Cortex-A72\nCore(s) per cluster: 4\nSocket(s): -\n"
    disks = json.dumps({"blockdevices": [
        {"name": "mmcblk0", "model": None, "serial": "0x00000000", "size": 31914983424, "rota": False, "tran": None, "type": "disk"},
        {"name": "mmcblk0boot0", "model": None, "serial": None, "size": 4194304000, "rota": False, "tran": None, "type": "disk"},
    ]})
    result = parse_scan(_output(
        {"sys_vendor": "", "product_name": "", "dt_model": "Raspberry Pi 4 Model B Rev 1.4"},
        cpu=cpu, mem="MemTotal:        3884188 kB", disks=disks, pci="", missing="lspci",
    ))
    kinds = _by_kind(result)
    assert (kinds["Gerät"]["name"], kinds["Gerät"]["manufacturer"]) == ("Raspberry Pi 4 Model B Rev 1.4", "Raspberry Pi")
    assert (kinds["Prozessor"]["name"], kinds["Prozessor"]["manufacturer"], kinds["Prozessor"]["detail"]) == ("Cortex-A72", "ARM", "4 Kerne")
    assert kinds["Arbeitsspeicher"]["name"] == "Arbeitsspeicher 4 GB"
    # SD-Karte und eMMC lassen sich von außen nicht unterscheiden; die Bootbereiche sind kein eigenes Teil.
    card = kinds["Speicherkarte"]
    assert (card["name"], card["serial_number"]) == ("Speicherkarte 32 GB", None), "0x00000000 ist keine Seriennummer"
    assert len([s for s in result["suggestions"] if s["key"].startswith("disk:")]) == 1
    assert result["missing"] == ["lspci"]


def test_virtual_and_partial_block_devices_are_skipped():
    disks = _disks(
        {"name": "sda", "model": "Samsung SSD 870 EVO 500GB", "serial": "S6PXNX0T000000"},
        {"name": "zd0"}, {"name": "zd16"}, {"name": "drbd0"}, {"name": "md0"}, {"name": "dm-0"}, {"name": "nbd0"},
        {"name": "sr0", "type": "rom"}, {"name": "zram0"}, {"name": "rbd0"},
        {"name": "sdd", "model": "Card Reader", "size": 0},
        {"name": "sde", "model": "Tiny", "size": 512 * 1024 * 1024},
    )
    result = parse_scan(_output({}, disks=disks, pci=""))
    assert [s["hint"] for s in result["suggestions"] if s["key"].startswith("disk:")] == ["/dev/sda"]


def test_placeholder_serials_are_no_serials():
    disks = _disks(
        {"name": "sda", "model": "SSD", "serial": "0000000000"},
        {"name": "sdb", "model": "SSD", "serial": "To Be Filled By O.E.M."},
        {"name": "sdc", "model": "SSD", "serial": "Default string"},
        {"name": "sdd", "model": "SSD", "serial": "123"},
    )
    result = parse_scan(_output({}, disks=disks, pci=""))
    disk_entries = [s for s in result["suggestions"] if s["key"].startswith("disk:")]
    assert [s["serial_number"] for s in disk_entries] == [None, None, None, None]
    # Jeder Datenträger bleibt ein eigener Vorschlag, auch ohne Seriennummer (Schlüssel mit Gerätenamen).
    assert len({s["key"] for s in disk_entries}) == 4


def test_red_hat_virtio_card_gets_a_short_maker_name():
    pci = '00:03.0 "Ethernet controller" "Red Hat, Inc." "Virtio network device" -r00 "Red Hat, Inc." "Device 0001"\n'
    result = parse_scan(_output({}, pci=pci))
    assert _by_kind(result)["Netzwerkkarte"]["manufacturer"] == "Red Hat"


def test_missing_tools_and_broken_output_do_not_crash():
    assert parse_scan("") == {"virtual": False, "missing": [], "suggestions": []}
    assert parse_scan("@@disks\nkein json\n@@cpu\n")["suggestions"] == []
    for broken in ('{"blockdevices": [null, "x", {"name": "sda", "size": "abc"}]}', '{"blockdevices": {}}', "[1, 2]"):
        assert parse_scan(f"@@disks\n{broken}\n")["suggestions"] == [], broken


def test_sizes_like_the_label():
    assert format_size(1000204886016) == "1 TB"
    assert format_size(4000787030016) == "4 TB"
    assert format_size(500107862016) == "500 GB"
    assert format_size(256060514304) == "256 GB"
    assert format_size(960197124096) == "960 GB"
    assert format_size(1920383410176) == "1,92 TB"
    assert format_size(3840755982336) == "3,84 TB"


def test_memory_like_bought():
    gib = 1024**3
    assert memory_label(int(62.66 * gib), virtual=False) == "64 GB"
    assert memory_label(int(15.5 * gib), virtual=False) == "16 GB"
    assert memory_label(470_000 * 1024, virtual=False) == "512 MB"
    assert memory_label(int(5.78 * gib), virtual=True) == "6 GB"


@pytest.mark.parametrize(("vendor", "product", "name"), [
    ("HP", "HP EliteDesk 800 G3 SFF", "HP EliteDesk 800 G3 SFF"),
    ("ASUSTeK COMPUTER INC.", "ASUS TUF Gaming A15", "ASUS TUF Gaming A15"),
    ("GIGABYTE", "GIGABYTE Brix GB-BSi7", "GIGABYTE Brix GB-BSi7"),
    ("Dell Inc.", "PowerEdge T140", "Dell PowerEdge T140"),
])
def test_the_maker_is_not_named_twice(vendor, product, name):
    result = parse_scan(_output({"sys_vendor": vendor, "product_name": product, "board_name": "0ABC12"}, pci=""))
    assert _by_kind(result)["Gerät"]["name"] == name


@pytest.mark.parametrize(("dmi", "board"), [
    ({"sys_vendor": "Gigabyte Technology Co., Ltd.", "product_name": "B550 AORUS ELITE V2", "board_vendor": "Gigabyte Technology Co., Ltd.",
      "board_name": "B550 AORUS ELITE V2"}, ("Gigabyte", "B550 AORUS ELITE V2")),
    ({"sys_vendor": "Micro-Star International Co., Ltd.", "product_name": "MS-7C37", "board_vendor": "Micro-Star International Co., Ltd.",
      "board_name": "MAG B550 TOMAHAWK (MS-7C37)"}, ("MSI", "MAG B550 TOMAHAWK (MS-7C37)")),
])
def test_selfbuilt_with_a_branded_board_is_a_mainboard_not_a_device(dmi, board):
    kinds = _by_kind(parse_scan(_output(dmi, pci="")))
    assert "Gerät" not in kinds
    assert (kinds["Mainboard"]["manufacturer"], kinds["Mainboard"]["model"]) == board


@pytest.mark.parametrize(("dmi", "device", "board"), [
    # ASUS-Board: die Firmware schreibt nur einen Platzhalter als Gerät.
    ({"sys_vendor": "ASUS", "product_name": "System Product Name", "board_vendor": "ASUSTeK COMPUTER INC.",
      "board_name": "PRIME B450M-A"}, None, ("ASUS", "PRIME B450M-A")),
    # Fertige Rechner, deren Gerät und Board gleich heißen: bleiben ein Gerät.
    ({"sys_vendor": "Supermicro", "product_name": "X10SLM-F", "board_vendor": "Supermicro", "board_name": "X10SLM-F"},
     "Supermicro X10SLM-F", None),
    ({"sys_vendor": "GIGABYTE", "product_name": "GB-BXBT-2807", "board_vendor": "GIGABYTE", "board_name": "GB-BXBT-2807"},
     "Gigabyte GB-BXBT-2807", None),
    ({"sys_vendor": "innotek GmbH", "product_name": "VirtualBox", "board_vendor": "Oracle Corporation", "board_name": "VirtualBox"},
     "innotek VirtualBox", None),
])
def test_device_or_mainboard(dmi, device, board):
    kinds = _by_kind(parse_scan(_output(dmi, pci="")))
    assert (kinds["Gerät"]["name"] if "Gerät" in kinds else None) == device
    assert ((kinds["Mainboard"]["manufacturer"], kinds["Mainboard"]["model"]) if "Mainboard" in kinds else None) == board


def test_values_fit_the_inventory_fields():
    long = "M" * 290
    result = parse_scan(_output({"sys_vendor": "Dell Inc.", "product_name": long}, pci=""))
    device = _by_kind(result)["Gerät"]
    assert len(device["model"]) == 200 and len(device["manufacturer"]) <= 200


def test_a_broken_section_does_not_cost_the_others():
    """Zahlen und JSON, die kein echter Server liefert: das betroffene Teil fehlt, der Rest kommt."""
    cpu = LSCPU_AMD.replace("Socket(s):               1", "Socket(s):               ²")
    huge_mem = "MemTotal: " + "9" * 400 + " kB"
    deep = '{"blockdevices": ' + "[" * 5000 + "]" * 5000 + "}"
    result = parse_scan(_output({"board_name": "B550M Pro4"}, cpu=cpu, mem=huge_mem, disks=deep))
    kinds = _by_kind(result)
    assert kinds["Prozessor"]["quantity"] == 1, "² ist keine Anzahl"
    assert "Arbeitsspeicher" not in kinds
    assert {"Mainboard", "Grafikkarte", "Netzwerkkarte"} <= set(kinds)
    infinite = '{"blockdevices": [{"name": "sda", "size": Infinity, "type": "disk"}, {"name": "sdb", "size": 1e999, "type": "disk"}]}'
    assert parse_scan(f"@@disks\n{infinite}\n")["suggestions"] == []


def test_long_values_from_a_prepared_server_are_handled_quickly():
    started = time.monotonic()
    vendor = "a" + " " * 200_000 + "b"
    result = parse_scan(_output({"sys_vendor": vendor, "product_name": "x" * 100_000}, pci=f'00:01.0 "Ethernet controller" "{vendor}" "x"\n'))
    assert time.monotonic() - started < 2
    assert all(len(s["name"]) < 1000 for s in result["suggestions"])


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("sh"), reason="braucht eine POSIX-Shell")
def test_the_command_runs_in_a_real_shell_without_noise():
    """Läuft hier wie auf einem Server: alle Abschnitte da, keine Fehlermeldung, und das Ergebnis lässt sich lesen."""
    done = subprocess.run(SCAN_COMMAND, shell=True, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0
    assert done.stderr == ""
    for section in ("dmi", "virt", "cpu", "mem", "disks", "pci", "missing"):
        assert f"@@{section}" in done.stdout
    assert parse_scan(done.stdout)["suggestions"] is not None


def test_the_command_only_reads_and_passes_the_core_block_list():
    from nodvard_deck.core.deny_patterns import match_deny_patterns

    assert match_deny_patterns(SCAN_COMMAND) is None
    # Läuft in `sh -c '…'`: ein einfaches Anführungszeichen im Skript würde den Befehl zerbrechen.
    assert "'" not in _SCRIPT
    for forbidden in ("rm ", ">", "sudo", "dd ", "mkfs", "tee "):
        assert forbidden not in SCAN_COMMAND.replace("2>/dev/null", "").replace(">/dev/null 2>&1", ""), forbidden


# --- Endpunkt `POST /ext/inventory/hosts/{id}/scan` ---------------------------------------------------------------

API = "/api/v1/ext/inventory"


@pytest.fixture
def ssh_answer(monkeypatch):
    """Ersetzt die SSH-Antwort: `answer["stdout"]`, oder `answer["error"]` als Ausnahme. Merkt sich die Befehle."""
    from types import SimpleNamespace

    from nodvard_deck.ext.context import ExecHandle

    answer: dict = {"stdout": "", "stderr": "Permission denied", "exit_code": 0, "error": None, "commands": []}

    async def run(self, host, command, *args, **kwargs):
        answer["commands"].append(command)
        if answer["error"]:
            raise answer["error"]
        return SimpleNamespace(exit_code=answer["exit_code"], stdout=answer["stdout"], stderr=answer["stderr"], duration_ms=1)

    monkeypatch.setattr(ExecHandle, "run", run)
    return answer


async def _host(client, headers, *, credential=True, os_family="linux", name="nas", kind=None):
    res = await client.post(
        "/api/v1/hosts", json={"name": name, "address": "10.0.0.7", "os_family": os_family, "kind": kind}, headers=headers,
    )
    assert res.status_code == 201, res.text
    host = res.json()
    if credential:
        cred = await client.post(
            f"/api/v1/hosts/{host['id']}/credentials", headers=headers,
            json={"kind": "ssh_password", "username": "root", "secret_value": "geheim-123"},
        )
        assert cred.status_code in (200, 201), cred.text
    return host


async def _user_with(client, db_session, username: str, permissions: list[str]) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.db.base import refresh_relationships
    from nodvard_deck.models import Role, RolePermission, User

    role = Role(name=f"{username}-rolle", is_builtin=False, description="Testrolle")
    db_session.add(role)
    await db_session.flush()
    for permission in permissions:
        db_session.add(RolePermission(role_id=role.id, permission=permission))
    await db_session.flush()
    await refresh_relationships(db_session, role, "permissions")
    user = User(username=username, password_hash=security.hash_password("correct-horse-battery"), is_active=True)
    user.roles.append(role)
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    return _auth_header(login.json()["access_token"])


@pytest.mark.asyncio
async def test_scan_returns_suggestions_and_writes_an_audit_entry(client, db_session, test_settings, ssh_answer):
    from nodvard_deck.models import AuditEntry
    from sqlalchemy import select

    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    host = await _host(client, headers)
    ssh_answer["stdout"] = _output({"sys_vendor": "", "product_name": "", "board_vendor": "ASRock", "board_name": "B550M Pro4"})

    res = await client.post(f"{API}/hosts/{host['id']}/scan", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["host_id"], body["host_name"], body["virtual"], body["missing"]) == (host["id"], "nas", False, [])
    assert ssh_answer["commands"] == [SCAN_COMMAND]
    assert {s["kind"] for s in body["suggestions"]} >= {"Mainboard", "Prozessor", "NVMe-SSD", "Festplatte"}

    entries = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "inventory.hardware_scan"))).scalars().all()
    assert [(e.outcome, e.target_id, e.actor_type) for e in entries] == [("success", host["id"], "user")]


@pytest.mark.asyncio
async def test_a_proxmox_guest_counts_as_virtual_even_if_it_shows_real_names(client, db_session, test_settings, ssh_answer):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    guest = await _host(client, headers, name="ct-101", kind="lxc")
    # Ein Container sieht die Teile des Wirts, ganz ohne VM-Kennzeichen in der Ausgabe.
    ssh_answer["stdout"] = _output({"sys_vendor": "", "product_name": "", "board_vendor": "ASRock", "board_name": "B550M Pro4"})
    body = (await client.post(f"{API}/hosts/{guest['id']}/scan", headers=headers)).json()
    assert body["virtual"] is True
    assert body["suggestions"] and not any(s["preselect"] for s in body["suggestions"])


@pytest.mark.asyncio
async def test_scan_says_why_it_cannot_read(client, db_session, test_settings, ssh_answer):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    without = await _host(client, headers, credential=False, name="ohne-zugang")
    res = await client.post(f"{API}/hosts/{without['id']}/scan", headers=headers)
    assert res.status_code == 422 and "kein SSH-Zugang" in res.json()["detail"]
    assert ssh_answer["commands"] == [], "ohne Zugang wird nichts versucht"

    assert (await client.post(f"{API}/hosts/gibt-es-nicht/scan", headers=headers)).status_code == 404

    host = await _host(client, headers)
    prefix = "Der Server ließ sich nicht auslesen: "
    cases = [
        (ConnectionRefusedError(111, "Connection refused"), "Verbindung abgelehnt – läuft der SSH-Dienst?"),
        (TimeoutError(), "keine Antwort innerhalb der Zeitgrenze – ist der Server an?"),
        # Unbekannte Fehler können Pfade oder Adressen tragen: die bleiben im Protokoll, nicht in der Antwort.
        (RuntimeError("kaputt in /etc/ssh/ssh_config bei 10.0.0.7"), "unerwarteter Fehler (Einzelheiten im Protokoll des Dashboards)."),
    ]
    for error, text in cases:
        ssh_answer["error"] = error
        res = await client.post(f"{API}/hosts/{host['id']}/scan", headers=headers)
        assert (res.status_code, res.json()["detail"]) == (502, prefix + text)

    ssh_answer["error"] = None
    ssh_answer["exit_code"] = 1
    res = await client.post(f"{API}/hosts/{host['id']}/scan", headers=headers)
    assert (res.status_code, res.json()["detail"]) == (502, prefix + "Permission denied")

    # Das Skript lief gar nicht (eingeschränkte Shell): Fehler mit dem, was der Server sagt, statt „Nichts gefunden“.
    ssh_answer.update(exit_code=1, stdout="restricted", stderr="")
    res = await client.post(f"{API}/hosts/{host['id']}/scan", headers=headers)
    assert (res.status_code, res.json()["detail"]) == (502, prefix + "restricted")
    ssh_answer.update(exit_code=0, stdout="", stderr="")
    res = await client.post(f"{API}/hosts/{host['id']}/scan", headers=headers)
    assert (res.status_code, res.json()["detail"]) == (502, prefix + "Der Server hat nichts geantwortet.")
    ssh_answer.update(exit_code=1, stderr="Permission denied")

    # Fremde Fehlerausgabe: eine Zeile, ohne Steuerzeichen, gekürzt.
    ssh_answer["stderr"] = "\x1b[31mzeile1\nzeile2\x07 " + "x" * 1000
    detail = (await client.post(f"{API}/hosts/{host['id']}/scan", headers=headers)).json()["detail"]
    reason = detail.removeprefix(prefix)
    assert reason.startswith("[31mzeile1 zeile2 x") and len(reason) == 300 and reason.endswith("…")
    assert not any(ch in detail for ch in "\x1b\n\x07")


@pytest.mark.asyncio
async def test_scan_error_kinds_and_failures_in_the_audit_log(client, db_session, test_settings, ssh_answer):
    from nodvard_deck.models import AuditEntry
    from nodvard_sdk.errors import HostUnreachable
    from sqlalchemy import select

    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    host = await _host(client, headers)
    prefix = "Der Server ließ sich nicht auslesen: "

    class ConnectionLost(Exception):  # wie asyncssh.ConnectionLost, ohne asyncssh hier vorauszusetzen
        __module__ = "asyncssh.misc"

    class PermissionDenied(Exception):
        __module__ = "asyncssh.misc"

    class Readable(Exception):
        readable = True

    cases = [
        (HostUnreachable("Für „nas“ ist noch kein SSH-Zugang eingerichtet."), "Für „nas“ ist noch kein SSH-Zugang eingerichtet."),
        (Readable("Der Server-Schlüssel hat sich geändert."), "Der Server-Schlüssel hat sich geändert."),
        (ConnectionLost("lost"), "Verbindung abgebrochen – war der Server kurz weg?"),
        (ConnectionResetError(104, "reset"), "Verbindung abgebrochen – war der Server kurz weg?"),
        (PermissionDenied("denied"), "Anmeldung abgelehnt – stimmt der Zugang unter Einstellungen → Server & Zugänge?"),
        (OSError(113, "No route to host"), "keine Verbindung zum Server."),
    ]
    for error, text in cases:
        ssh_answer["error"] = error
        res = await client.post(f"{API}/hosts/{host['id']}/scan", headers=headers)
        assert (res.status_code, res.json()["detail"]) == (502, prefix + text), type(error).__name__

    entries = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "inventory.hardware_scan"))).scalars().all()
    assert len(entries) == len(cases) and {e.outcome for e in entries} == {"failure"}


@pytest.mark.asyncio
async def test_scan_only_for_linux(client, db_session, test_settings, ssh_answer):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    win = await _host(client, headers, os_family="windows")
    res = await client.post(f"{API}/hosts/{win['id']}/scan", headers=headers)
    assert res.status_code == 422 and "Linux" in res.json()["detail"]
    assert (await client.post(f"{API}/hosts/{win['id']}/scan")).status_code == 401


@pytest.mark.asyncio
async def test_scan_needs_the_right_to_run_commands_on_servers(client, db_session, test_settings, ssh_answer):
    """Auslesen führt einen Befehl auf dem Server aus: dafür reicht das Recht am Inventar nicht."""
    owner = _auth_header(await _enable_inventory(client, db_session, test_settings))
    host = await _host(client, owner)
    ssh_answer["stdout"] = _output({}, pci="")

    inventory_only = await _user_with(client, db_session, "lager", ["inventory.read", "inventory.write", "hosts.read"])
    assert (await client.post(f"{API}/hosts/{host['id']}/scan", headers=inventory_only)).status_code == 403
    assert ssh_answer["commands"] == []

    operator = await _user_with(client, db_session, "technik", ["inventory.read", "inventory.write", "hosts.read", "hosts.execute"])
    assert (await client.post(f"{API}/hosts/{host['id']}/scan", headers=operator)).status_code == 200
