"""Teile eines Linux-Servers per SSH auslesen und als Vorschläge fürs Inventar aufbereiten."""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

_log = logging.getLogger(__name__)

SECTIONS = ("dmi", "virt", "cpu", "mem", "disks", "pci", "missing")

# Ein einziger, nur lesender Befehl für alles, was ohne root lesbar ist: Seriennummern aus DMI gibt der Kernel nur root,
# einzelne Speicherriegel bräuchten `dmidecode` als root. Die Seriennummern der Datenträger liefert `lsblk` auch so, beim
# Raspberry Pi steht das Modell im Gerätebaum (`/proc/device-tree/model`).
_SCRIPT = (
    "echo @@dmi; for f in sys_vendor product_name product_version board_vendor board_name; do "
    'printf "%s=" "$f"; cat "/sys/class/dmi/id/$f" 2>/dev/null || echo; done; '
    'printf "dt_model="; { tr -d "\\000" < /proc/device-tree/model; } 2>/dev/null; echo; '
    "echo @@virt; systemd-detect-virt 2>/dev/null; "
    "echo @@cpu; LC_ALL=C lscpu 2>/dev/null; "
    "echo @@mem; grep -m1 ^MemTotal: /proc/meminfo 2>/dev/null; "
    "echo @@disks; lsblk -J -b -d -o NAME,MODEL,SERIAL,SIZE,ROTA,TRAN,TYPE 2>/dev/null; "
    "echo @@pci; lspci -mm 2>/dev/null; "
    "echo @@missing; for p in lscpu lsblk lspci; do command -v $p >/dev/null 2>&1 || echo $p; done; "
    "true"
)

# Ohne Eingaben von außen. Läuft über `sh -c`, damit auch eine andere Login-Shell (fish, csh) ihn versteht. Fehlt ein
# Programm, bleibt sein Abschnitt leer, der Rest kommt trotzdem.
SCAN_COMMAND = f"sh -c '{_SCRIPT}'"

# Mehr liest `parse_scan` nicht: die echte Ausgabe hat wenige KB, ein präparierter Server soll nichts aufhalten.
MAX_OUTPUT = 256_000
# Längster Wert (Hersteller, Modell, Zeile von lspci), der berücksichtigt wird.
_MAX_VALUE = 300

# Platzhalter, die Hersteller statt echter Angaben in die Firmware schreiben.
_PLACEHOLDERS = {
    "", "to be filled by o.e.m.", "to be filled by oem", "default string", "system product name", "system manufacturer",
    "system version", "not specified", "none", "unknown", "o.e.m.", "oem", "base board product name",
    "base board manufacturer", "123456789", "n/a", "na", "not applicable", "undefined", "type2 - board manufacturer",
    "type2 - board product name", "type2 - board vendor name", "type1productconfigid", "x.x", "0", "-",
}

# Teile eines Namens, an denen man eine VM erkennt (als ganze Wörter).
_VIRTUAL_WORDS = (
    "qemu", "kvm", "virtualbox", "vmware", "bochs", "xen", "hyper-v", "virtual machine", "standard pc", "amazon ec2",
    "google compute engine", "openstack", "digitalocean", "droplet", "parallels", "bhyve", "cloud hypervisor",
)

_CPU_VENDORS = {"genuineintel": "Intel", "authenticamd": "AMD", "arm": "ARM"}

# Erster Teil des Modellnamens -> Hersteller (lsblk nennt meist nur das Modell).
_DISK_VENDORS = {
    "samsung": "Samsung", "wdc": "WD", "wd": "WD", "western": "WD", "ct": "Crucial", "crucial": "Crucial",
    "kingston": "Kingston", "sandisk": "SanDisk", "toshiba": "Toshiba", "seagate": "Seagate", "intel": "Intel",
    "micron": "Micron", "hgst": "HGST", "hitachi": "Hitachi", "sk": "SK hynix", "hynix": "SK hynix", "transcend": "Transcend",
    "adata": "ADATA", "lexar": "Lexar", "kioxia": "Kioxia", "corsair": "Corsair", "gigabyte": "Gigabyte", "teamgroup": "TeamGroup",
}

# Geräte, die keine eigenen Teile sind: virtuelle und Teil-Geräte (ZFS-Volumes, DRBD, eMMC-Bootbereiche).
_SKIP_DISK = re.compile(r"^(loop|zram|ram|rbd|nbd|zd|drbd|md|dm-|sr|fd)\d*|^mmcblk\d+(boot\d+|rpmb)$")
# Kleiner ist kein Datenträger, den man inventarisiert (leerer Kartenleser, Bootbereich).
_MIN_DISK_BYTES = 1_000_000_000

_PCI_KINDS = {
    "vga compatible controller": "Grafikkarte", "3d controller": "Grafikkarte", "display controller": "Grafikkarte",
    "ethernet controller": "Netzwerkkarte", "network controller": "WLAN-Karte",
}

# Lange Firmennamen -> wie man sie kennt (Anfang des Namens, ohne Rechtsform).
_VENDORS = {
    "advanced micro devices": "AMD", "intel": "Intel", "nvidia": "NVIDIA", "realtek": "Realtek", "red hat": "Red Hat",
    "asustek": "ASUS", "asus": "ASUS", "micro-star": "MSI", "hewlett-packard": "HP", "hewlett packard": "HP", "gigabyte": "Gigabyte",
    "fujitsu": "Fujitsu", "supermicro": "Supermicro", "super micro": "Supermicro", "broadcom": "Broadcom",
    "biostar": "Biostar", "mellanox": "Mellanox", "marvell": "Marvell", "qualcomm atheros": "Qualcomm Atheros", "aquantia": "Aquantia",
}

# Hersteller, die Mainboards einzeln verkaufen: nur bei ihnen kann „Gerät“ in Wahrheit das Mainboard sein.
_BOARD_MAKERS = {"ASUS", "ASRock", "Biostar", "Gigabyte", "MSI"}
# So lang dürfen Hersteller, Modell und Seriennummer im Inventar sein.
_FIELD_MAX = 200

_LEGAL_SUFFIX = re.compile(r",?\s+(Corporation|Corp\.?|Inc\.?|Co\.,?\s*Ltd\.?|Ltd\.?|GmbH|AG|LLC|Technology|Semiconductor(\s+Co\.,?\s*Ltd\.?)?)\s*$", re.IGNORECASE)


def _clean(value: Any) -> str | None:
    """Ein Wert vom Server: Leerraum zusammengefasst, gekürzt, Platzhalter der Firmware fallen weg."""
    text = " ".join(str(value or "").split())[:_MAX_VALUE].strip()
    return None if text.lower() in _PLACEHOLDERS else text


def _vendor_name(value: str | None) -> str | None:
    """"Dell Inc." -> "Dell", "LENOVO" -> "Lenovo", "Advanced Micro Devices, Inc. [AMD/ATI]" -> "AMD"."""
    if not value:
        return None
    # Erst Leerraum zusammenfassen und kürzen: sonst wird der Suffix-Ausdruck bei langen Leerzeichenketten langsam.
    text = re.sub(r"\s*\[.*?\]\s*", " ", " ".join(value.split())[:_MAX_VALUE]).strip()
    for _ in range(2):
        text = _LEGAL_SUFFIX.sub("", text).strip()
    known = next((name for key, name in _VENDORS.items() if text.lower().startswith(key)), None)
    if known:
        return known
    return text.capitalize() if text.isupper() and len(text) > 3 else text or None


def _bad_serial(serial: str | None) -> bool:
    """Seriennummern, die keine sind: Nullen, Platzhalter, zu kurz."""
    if not serial:
        return True
    plain = serial.strip().lower().removeprefix("0x")
    return (
        len(plain) < 4 or set(plain) <= {"0"} or plain in {"0123456789abcdef", "unknown", "none", "not available", "default string"}
    )


def _sections(output: str) -> dict[str, str]:
    parts: dict[str, list[str]] = {name: [] for name in SECTIONS}
    current: str | None = None
    for line in output.splitlines():
        marker = line.strip()
        if marker.startswith("@@") and marker[2:] in parts:
            current = marker[2:]
            continue
        if current:
            parts[current].append(line)
    return {name: "\n".join(lines) for name, lines in parts.items()}


def format_size(num_bytes: int) -> str:
    """Größe wie auf dem Etikett (Hersteller rechnen mit 1000): 1000204886016 -> "1 TB", 960197124096 -> "960 GB",
    3840755982336 -> "3,84 TB"."""
    for unit, factor in (("TB", 10**12), ("GB", 10**9), ("MB", 10**6)):
        if num_bytes >= factor:
            value = num_bytes / factor
            text = f"{value:.2f}".rstrip("0").rstrip(".") if value < 10 else str(round(value))
            return f"{text.replace('.', ',')} {unit}"
    return f"{num_bytes} B"


def memory_label(total_bytes: int, virtual: bool) -> str:
    """Arbeitsspeicher wie gekauft. Der Kernel behält etwas für sich (Grafik, Firmware): knapp unter einer üblichen
    Größe heißt diese Größe. Eine VM bekommt krumme Werte zugeteilt: dort auf volle GB aufgerundet."""
    gib = total_bytes / 1024**3
    if gib < 0.95:
        mib = total_bytes / 1024**2
        size = next((s for s in (128, 256, 512, 768) if mib <= s), 1024)
        return f"{size} MB" if size < 1024 else "1 GB"
    if not virtual:
        usual = next((s for s in (1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768, 1024, 1536, 2048) if gib <= s), None)
        if usual and gib >= usual * 0.9:
            return f"{usual} GB"
    return f"{math.ceil(gib - 0.02)} GB"


def _suggestion(key: str, kind: str, name: str, **fields: Any) -> dict[str, Any]:
    def short(value: str | None) -> str | None:
        return value[:_FIELD_MAX] if value else value

    return {
        "key": key, "kind": kind, "name": name, "manufacturer": short(fields.get("manufacturer")),
        "model": short(fields.get("model")), "serial_number": short(fields.get("serial_number")),
        "quantity": fields.get("quantity", 1),
        # `detail` kommt als Beschreibung ins Inventar ("8 Kerne"), `hint` steht nur in der Vorschau ("/dev/sda").
        "detail": fields.get("detail"), "hint": fields.get("hint"),
        # Grafik- und Netzwerkkarten sitzen oft auf dem Mainboard: nicht vorab ankreuzen.
        "preselect": fields.get("preselect", True),
    }


def _number(value: Any, limit: int) -> int | None:
    """Ganze Zahl aus einer Serverantwort, nur ASCII-Ziffern und höchstens `limit`; sonst None (auch für 1e999, "²")."""
    text = str(value).strip() if isinstance(value, (int, str)) and not isinstance(value, bool) else ""
    if not re.fullmatch(r"[0-9]{1,20}", text):
        return None
    number = int(text)
    return number if number <= limit else None


def _virtual(dmi_text: str, virt: str) -> bool:
    if virt and virt.strip().lower() not in ("", "none"):
        return True
    text = f" {dmi_text.lower()} "
    return any(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) for word in _VIRTUAL_WORDS)


def _disk(dev: Any, used_keys: set[str]) -> dict[str, Any] | None:
    if not isinstance(dev, dict):
        return None
    name = str(dev.get("name") or "")[:40]
    if (dev.get("type") or "disk") != "disk" or not name or _SKIP_DISK.match(name):
        return None
    size = _number(dev.get("size"), 10**17) or 0  # mehr als 100 PB ist keine echte Platte
    if size < _MIN_DISK_BYTES:
        return None
    model = _clean(dev.get("model"))
    serial = _clean(dev.get("serial"))
    if _bad_serial(serial):
        serial = None
    tran = str(dev.get("tran") or "").lower()
    rota = dev.get("rota") in (True, 1, "1", "true")
    if tran == "usb":
        kind = "USB-Datenträger"
    elif name.startswith("mmcblk"):
        kind = "Speicherkarte"  # SD-Karte oder eMMC, von außen nicht zu unterscheiden
    elif tran == "nvme" or name.startswith("nvme"):
        kind = "NVMe-SSD"
    else:
        kind = "Festplatte" if rota else "SSD"
    vendor_key = re.split(r"[ _-]", model or "")[0].lower()
    if vendor_key.startswith("ct") and vendor_key[2:3].isdigit():
        vendor_key = "ct"  # Crucial: "CT1000MX500SSD1"
    if vendor_key.startswith("st") and vendor_key[2:3].isdigit():
        vendor_key = "seagate"  # Seagate: "ST4000VN008-2DR166"
    key = f"disk:{name}:{serial or ''}"
    if key in used_keys:
        return None
    used_keys.add(key)
    return _suggestion(
        key, kind, f"{kind} {format_size(size)}" + (f" ({model})" if model else ""),
        manufacturer=_DISK_VENDORS.get(vendor_key), model=model, serial_number=serial, hint=f"/dev/{name}",
    )


def _system_parts(dmi: dict[str, str | None], virtual: bool) -> list[dict[str, Any]]:
    vendor, product = _vendor_name(dmi.get("sys_vendor")), dmi.get("product_name")
    # Lenovo schreibt den Maschinentyp in product_name, den Klarnamen in product_version ("ThinkCentre M720q").
    if vendor == "Lenovo" and dmi.get("product_version"):
        product = dmi["product_version"]
    board_vendor, board = _vendor_name(dmi.get("board_vendor")), dmi.get("board_name")
    # Selbst gebaut: Die Firmware füllt das „Gerät“ mit einem Platzhalter (ASUS "System Product Name", fällt weg) oder dem
    # Boardnamen, ganz oder als Teil (Gigabyte "B550 AORUS ELITE V2", MSI "MS-7C37" in "MAG B550 TOMAHAWK (MS-7C37)"). Nur
    # bei Board-Herstellern, nie in VMs (VirtualBox, Hyper-V nennen beide gleich); Gigabyte BRIX ("GB-B…") ist ein Mini-PC.
    maker = board_vendor or vendor
    selfbuilt = bool(board) and not virtual and (
        not product
        or (maker in _BOARD_MAKERS and product.lower() in board.lower() and not product.upper().startswith("GB-B"))
    )
    parts: list[dict[str, Any]] = []
    if (vendor or product) and not selfbuilt:
        # "HP" + "HP EliteDesk 800 G3" ergäbe "HP HP …": der Hersteller nur, wenn er nicht schon vorne steht.
        name = product if vendor and product and product.lower().startswith(vendor.lower()) else " ".join(filter(None, (vendor, product)))
        parts.append(_suggestion("system", "Gerät", name, manufacturer=vendor, model=product))
    elif not (vendor or product) and dmi.get("dt_model"):
        dt_model = dmi["dt_model"]
        maker = "Raspberry Pi" if dt_model.lower().startswith("raspberry pi") else None
        parts.append(_suggestion("system", "Gerät", dt_model, manufacturer=maker, model=dt_model))
    # Das Mainboard ist nur beim selbst gebauten Rechner ein eigenes Teil; ein Markenrechner ist das Gerät selbst.
    if board and (selfbuilt or not (vendor or product)):
        parts.append(_suggestion("board", "Mainboard", f"Mainboard {board}", manufacturer=board_vendor or vendor, model=board))
    return parts


def _cpu_parts(text: str) -> list[dict[str, Any]]:
    cpu: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() not in cpu:
            cpu[key.strip()] = value.strip()
    model = _clean(cpu.get("Model name"))
    if not model:
        return []
    sockets = _number(cpu.get("Socket(s)"), 64)
    cores = _number(cpu.get("Core(s) per socket") or cpu.get("Core(s) per cluster"), 4096)
    return [_suggestion(
        "cpu", "Prozessor", model, manufacturer=_CPU_VENDORS.get(cpu.get("Vendor ID", "").lower()), model=model,
        quantity=sockets or 1, detail=f"{cores} Kerne" if cores else None,
    )]


def _memory_parts(text: str, virtual: bool) -> list[dict[str, Any]]:
    mem = re.search(r"MemTotal:\s+([0-9]{1,15})\s*kB", text)
    if not mem:
        return []
    label = memory_label(int(mem.group(1)) * 1024, virtual)
    return [_suggestion("memory", "Arbeitsspeicher", f"Arbeitsspeicher {label}", hint="Summe aller Riegel")]


def _disk_parts(text: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(text or "{}")
    except (ValueError, RecursionError):
        return []
    devices = parsed.get("blockdevices") if isinstance(parsed, dict) else None
    used: set[str] = set()
    return [disk for dev in (devices if isinstance(devices, list) else [])[:200] if (disk := _disk(dev, used))]


def _pci_parts(text: str) -> list[dict[str, Any]]:
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in text.splitlines()[:500]:
        fields = re.findall(r'"([^"]*)"', line[:2000])
        if len(fields) < 3:
            continue
        kind = _PCI_KINDS.get(fields[0].strip().lower())
        if not kind:
            continue
        pci_vendor = _vendor_name(_clean(fields[1]))
        device = _clean(fields[2]) or ""
        key = (kind, pci_vendor or "", device)
        if key in seen:
            seen[key]["quantity"] += 1
            continue
        seen[key] = _suggestion(
            f"pci:{len(seen)}", kind, " ".join(filter(None, (kind, pci_vendor, device))), manufacturer=pci_vendor,
            model=device or None, preselect=False, hint="kann auch auf dem Mainboard sitzen",
        )
    # Mehrere Anschlüsse einer Netzwerkkarte sind ein Teil: die Anzahl steht in der Beschreibung, nicht als Menge.
    for entry in seen.values():
        if entry["quantity"] > 1 and entry["kind"] in ("Netzwerkkarte", "WLAN-Karte"):
            entry["detail"] = f"{entry['quantity']} Anschlüsse"
            entry["quantity"] = 1
    return list(seen.values())


def parse_scan(output: str) -> dict[str, Any]:
    """Ausgabe von `SCAN_COMMAND` -> `{"virtual": bool, "missing": [...], "suggestions": [...]}`. Wirft nicht: was
    sich nicht lesen lässt, fehlt einfach; ein kaputter Abschnitt nimmt die anderen nicht mit."""
    sections = _sections(str(output or "")[:MAX_OUTPUT])
    dmi_raw = {k.strip(): v for k, _, v in (line.partition("=") for line in sections["dmi"].splitlines()[:50]) if k.strip()}
    dmi = {k: _clean(v) for k, v in dmi_raw.items()}
    virtual = _virtual(" ".join(v for v in dmi.values() if v), sections["virt"][:200])

    parts = (
        lambda: _system_parts(dmi, virtual), lambda: _cpu_parts(sections["cpu"]),
        lambda: _memory_parts(sections["mem"], virtual), lambda: _disk_parts(sections["disks"]),
        lambda: _pci_parts(sections["pci"]),
    )
    suggestions: list[dict[str, Any]] = []
    for part in parts:
        try:
            suggestions.extend(part())
        except Exception as exc:  # noqa: BLE001 - eine seltsame Zeile soll nicht alle anderen Teile kosten
            _log.warning("Ein Abschnitt beim Auslesen war nicht lesbar (%s), die übrigen Teile kommen trotzdem", type(exc).__name__)

    if virtual:
        # Auf einer VM oder in einem Container sind die Teile nachgebildet oder gehören dem Wirt: nichts vorab ankreuzen.
        for entry in suggestions:
            entry["preselect"] = False
    # `missing` nennt fehlende Programme (etwa `lspci`), damit die Seite sagen kann, warum Karten fehlen.
    missing = [p.strip() for p in sections["missing"].splitlines() if p.strip() in ("lscpu", "lsblk", "lspci")]
    return {"virtual": virtual, "missing": missing, "suggestions": suggestions}
