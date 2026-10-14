"""Einen Gast aus einer Sicherung als Kopie wiederherstellen; das Original bleibt immer unberührt."""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from nodvard_sdk import ActionResult, Actor, DryRunReport, Notification, Risk, Severity
from nodvard_sdk.actions import REQUEST_WAIT_S, ActionRequest, ActionSpec

from . import pending
from .connector import COPY_POOL, UPID_RE, ProxmoxBackupApiError, ProxmoxBackupConnector
from .inventory import file_kind
from .space import backed_up_disk_bytes
from .transport import clean_text, proxmox_reason

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nodvard_sdk import ExtensionContext

    ConnectorsFactory = Callable[[], Awaitable[dict[str, ProxmoxBackupConnector]]]

log = logging.getLogger("nodvard_deck.ext.backups.restore")

ACTION_TYPE = "backup.restore_copy"
AUDIT_ACTION = "backups.restore_copy"
PAGE_PATH = "/ext/backups/backups"

RESTORE_TIMEOUT_S = 45 * 60.0
"""So lange wartet die Aktion höchstens auf Proxmox, unter `EXECUTE_TIMEOUT_S` des Gates (60 Minuten), damit die
Aktion selbst sagen kann, was dann gilt, statt nur "Ausgang unbekannt"."""
POLL_INTERVAL_S = 5.0
MAX_POLL_ERRORS = 12
"""Aufeinanderfolgende Fehler oder unbrauchbare Antworten beim Nachfragen (etwa eine Minute), bevor die Aktion
aufgibt und das Fertig-Einrichten dem Wächter überlässt."""
FINISH_ATTEMPTS = 3
FINISH_RETRY_DELAY_S = 2.0
FOLLOW_UP_GIVE_UP_S = 24 * 3600.0
"""Nach so langer Zeit gibt der Wächter eine offene Kopie mit einer Meldung auf."""
NO_TASK_GRACE_S = 15 * 60.0
"""So lange darf eine Aufgabe fehlen (Anlegen ohne Antwort), bevor der Wächter annimmt, dass Proxmox nie anfing."""
FOLLOW_UP_INTERVAL_TEXT = "alle 15 Minuten"

SPACE_FACTOR = 1.1
SPACE_RESERVE_BYTES = 2 * 1024**3
"""Mindestens nötig auf dem Zielspeicher: Größe der Sicherung + 10 % + 2 GB Reserve. Die Sicherung ist meist
gepackt, die Kopie also eher größer; weniger Platz reicht sicher nicht."""
THICK_STORAGE_TYPES = frozenset({"lvm"})
"""Speicher, die beim Anlegen sofort die volle Plattengröße belegen: dort zählt die Größe der Platten aus der
Sicherung als Mindestbedarf."""

NAME_MAX = 63
DESCRIPTION_MAX = 8000
"""Bytes der Notiz an der Kopie (Proxmox: höchstens 8 KiB)."""
NOTE_MAX = 2000
SOURCE_TEXT_MAX = 200
"""Längen, auf die Notiz und Herkunft eines Vermerks beim Laden gekürzt werden. Was Nodvard Deck selbst schreibt, ist
deutlich kürzer (Namen aus der Sicherung sind auf `NAME_MAX` gekürzt); länger kann nur ein veränderter Vermerk sein."""
FUTURE_SLACK_S = 3600.0
"""So weit darf die Zeit eines Vermerks der Uhr voraus sein (Uhr zurückgestellt). Weiter in der Zukunft hielte ein
Vermerk die Frist von `FOLLOW_UP_GIVE_UP_S` für immer offen."""
SUGGEST_TRIES = 25
KIND_LABEL = {"qemu": "VM", "lxc": "Container"}
WORKER = {"qemu": "qmrestore", "lxc": "vzrestore"}

INVALID = 422
"""Ungültige Eingabe (Zahl statt der in neueren Starlette-Versionen umbenannten Konstante)."""

# Immer mit `fullmatch` benutzt: `$` ließe einen Zeilenumbruch am Ende durch. Die Muster haben keine verschachtelten
# Wiederholungen ohne trennendes Zeichen, damit lange Eingaben kein Backtracking aufblähen.
_NODE_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")
_STORAGE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*[A-Za-z0-9]")
_VMID_RE = re.compile(r"[1-9][0-9]{2,8}")
_DNS_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_NAME_RE = re.compile(rf"(?:{_DNS_LABEL}\.)*{_DNS_LABEL}")
_VOLID_RE = re.compile(r"[A-Za-z](?:[A-Za-z0-9]|[_.-]+[A-Za-z0-9])+:[A-Za-z0-9_.:/+-]{1,240}")
_NET_RE = re.compile(r"net\d+")
_IPCONFIG_RE = re.compile(r"ipconfig\d+")
_LXC_MP_RE = re.compile(r"mp\d+")
_LXC_DEV_RE = re.compile(r"dev\d+")
_HOSTPCI_RE = re.compile(r"hostpci\d+")
_USB_RE = re.compile(r"usb\d+")
_VIRTIOFS_RE = re.compile(r"virtiofs\d+")
_QEMU_DISK_RE = re.compile(r"(?:scsi|virtio|sata|ide|efidisk|tpmstate)\d+")
_CONNECTION_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_ACL_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_TAG_RE = re.compile(r"[0-9]{1,4}")
_VERSION_RE = re.compile(r"\s*(\d{1,4})\.(\d{1,4})(?!\d)", re.ASCII)
_VZDUMP_NAME_RE = re.compile(r"vzdump-(?:qemu|lxc|openvz)-(\d+)-")
_PBS_NAME_RE = re.compile(r"(?:^|/)(?:vm|ct)/(\d+)/")
_TAR_RE = re.compile(r"\.(?:tar|tgz)(?:\.(?:gz|lzo|zst))?$")
_TRUE = ("1", "true", "yes", "on")

MIN_VERSION_QEMU = (7, 2)
"""Erst ab Proxmox VE 7.2 (qemu-server 7.2-1, "allow overriding non-disk options during restore") übernimmt Proxmox
`name` und `onboot` beim Wiederherstellen einer VM aus dem Aufruf. Älter bliebe der Autostart des Originals, bis
Nodvard Deck ihn nach der Wiederherstellung abschaltet."""
MIN_VERSION_LXC = (6, 0)
"""`unique` (neue MAC-Adressen beim Wiederherstellen eines Containers) gibt es seit pve-container 2.0-37 (April 2019,
Proxmox VE 6). Ohne `unique` bekäme die Kopie die MAC-Adressen des Originals, das lässt Nodvard Deck nie zu."""
POOL_CHECK_FIXED_LXC = (7, 1)
"""Bis pve-container 4.1-4 (Februar 2022, Proxmox VE 7.1) verlangte Proxmox beim Anlegen eines Containers in einem Pool
zusätzlich `Permissions.Modify` oder `Pool.Allocate` auf dem Pool (Proxmox-Fehler 3635, "fix overly-strict pool
permission check on create")."""

ROLE_NAME = "NodvardDeckRestore"
ROLE_PRIVS = "VM.Allocate VM.Audit VM.Config.Options VM.Config.Network"
"""Die Rolle für den Pool. Dieselben Namen legt die Einrichtung mit Passwort in der Proxmox-Erweiterung an."""
POOL_PATH = f"/pool/{COPY_POOL}"
POOL_COMMENT = "Nodvard Deck: wiederhergestellte Kopien"
"""Derselbe Kommentar wie bei der Einrichtung mit Passwort (Proxmox-Erweiterung): nur einen Pool mit genau diesem
Kommentar übernimmt sie als eigenen."""

POOL_KEEP_HINT = (
    f"Willst du die Kopie behalten, nimm sie aus dem Pool {COPY_POOL}: in Proxmox unter Rechenzentrum → Rechte → Pools → "
    f"{COPY_POOL} → Mitglieder die Kopie auswählen und „Entfernen“ klicken (das löscht sie nicht, sie verlässt nur den Pool)."
)

RESTORE_PERMISSION_HINT = (
    "Proxmox hat die Wiederherstellung abgelehnt (keine Berechtigung), es wurde nichts angelegt. Dem Zugang fehlt "
    f"ein Recht, oder es gibt den Pool {COPY_POOL} nicht: VM.Allocate, VM.Audit, VM.Config.Options und VM.Config.Network "
    f"auf dem Pool {COPY_POOL} (dort entstehen alle Kopien), Datastore.AllocateSpace auf dem Zielspeicher, zum Lesen der "
    "Sicherung VM.Backup für den gesicherten Gast und Datastore.AllocateSpace auf dem Backup-Speicher, ab Proxmox VE 8 "
    "SDN.Use für die Bridges, bei privilegierten Containern Sys.Modify auf „/“. Verbinde Proxmox einmal neu mit "
    "Passwort, dann richtet Nodvard Deck Pool und Rechte ein. Anleitung für Zugänge von Hand: docs/10-PROXMOX-TOKEN.md, "
    "„Wiederherstellen als Kopie“."
)

UPDATES_TEXT = "Spiel die Updates ein (Shield → Updates), dann geht es."
MAJOR_UPGRADE_TEXT = (
    "Dafür ist ein großes Update auf eine neue Hauptversion von Proxmox VE nötig; die normalen Updates (Shield → Updates) "
    "schaffen das nicht. Proxmox beschreibt den Weg in seiner Upgrade-Anleitung im Proxmox-Wiki."
)

RECONNECT_HINT = (
    "Zum Verbinden in Proxmox bei der Kopie unter „Hardware“ (bei Containern „Netzwerk“) die Netzwerkkarte "
    "bearbeiten und „Getrennt“ abhaken – erst wenn die IP-Adresse geändert ist oder das Original aus ist."
)

FOLLOW_UP_NOTE = (
    "Nodvard Deck richtet die Kopie fertig ein (Netzwerkkarten trennen, Autostart und Name prüfen), sobald Proxmox "
    f"fertig ist – der Backup-Wächter sieht {FOLLOW_UP_INTERVAL_TEXT} nach – und meldet sich dann. Bis dahin die "
    "Kopie bitte nicht starten und in Proxmox prüfen, was schon da ist."
)


def leftover_note(vmid: int) -> str:
    return (
        f"Proxmox kann dabei einen Rest (Eintrag, teils Platten) unter der Nummer {vmid} hinterlassen; bitte in Proxmox "
        "prüfen und selbst löschen. Das Original ist unberührt."
    )


class RestoreError(Exception):
    """Ein Grund, nichts wiederherzustellen, als ganzer deutscher Satz. `status_code` für die Route."""

    def __init__(self, message: str, status_code: int = status.HTTP_409_CONFLICT) -> None:
        super().__init__(message)
        self.status_code = status_code


# --- Eingaben ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RestoreInput:
    volid: str
    node: str
    storage: str
    vmid: int
    name: str
    link_down: bool


def _text(values: dict[str, Any], key: str) -> str:
    """Genau der Wert, ohne Leerzeichen abzuschneiden: was nicht exakt passt, wird abgelehnt."""
    value = values.get(key)
    return value if isinstance(value, str) else ""


def check_volid(volid: Any) -> str:  # noqa: ANN401 - kommt als Text von der Seite oder aus dem Gate
    if not isinstance(volid, str) or not _VOLID_RE.fullmatch(volid) or ".." in volid:
        raise RestoreError("Diese Sicherung lässt sich nicht zuordnen (ungültige Kennung).", INVALID)
    return volid


def parse_input(values: Any) -> RestoreInput:  # noqa: ANN401 - kommt als JSON von der Seite bzw. aus dem Gate
    """Prüft Form und Grenzen, ohne Proxmox. Die Formate sind die von Proxmox selbst (pve-node, pve-storage-id,
    pve-vmid, dns-name), damit nichts Unerwartetes in einen Pfad oder Parameter gelangt."""
    if not isinstance(values, dict):
        raise RestoreError("Angaben zur Wiederherstellung fehlen.", INVALID)
    volid = check_volid(values.get("volid"))
    node = _text(values, "node")
    if not _NODE_RE.fullmatch(node):
        raise RestoreError("Bitte einen Zielknoten wählen.", INVALID)
    storage = _text(values, "storage")
    if not _STORAGE_RE.fullmatch(storage):
        raise RestoreError("Bitte einen Zielspeicher wählen.", INVALID)
    raw_vmid = values.get("vmid")
    vmid_text = str(raw_vmid) if isinstance(raw_vmid, (int, str)) and not isinstance(raw_vmid, bool) else ""
    if not _VMID_RE.fullmatch(vmid_text):
        raise RestoreError("Die neue Nummer muss eine Zahl von 100 bis 999999999 sein.", INVALID)
    name = _text(values, "name")
    if not name or len(name) > NAME_MAX or not _NAME_RE.fullmatch(name):
        raise RestoreError(
            f"Der Name darf nur Buchstaben ohne Umlaute, Ziffern, Bindestriche und Punkte enthalten "
            f"(höchstens {NAME_MAX} Zeichen, nicht mit einem Bindestrich anfangen oder enden).",
            INVALID,
        )
    link_down = values.get("link_down", True)
    if not isinstance(link_down, bool):
        raise RestoreError("Angabe zu den Netzwerkkarten ist ungültig.", INVALID)
    return RestoreInput(volid=volid, node=node, storage=storage, vmid=int(vmid_text), name=name, link_down=link_down)


def source_vmid_from_volid(volid: str) -> str | None:
    """Die Nummer des gesicherten Gastes aus dem Dateinamen (`vzdump-qemu-101-...`, PBS `backup/vm/101/...`), nur um
    die Liste auf dem Speicher von Proxmox vorfiltern zu lassen; maßgeblich ist der Eintrag dort."""
    match = _VZDUMP_NAME_RE.search(volid) or _PBS_NAME_RE.search(volid)
    return match.group(1) if match else None


def suggest_name(source_name: str | None, source_vmid: str) -> str:
    base = re.sub(r"[^A-Za-z0-9.-]+", "-", source_name or "").strip("-.") or f"gast-{source_vmid}"
    base = re.sub(r"-{2,}", "-", base)[: NAME_MAX - len("-kopie")].rstrip("-.")
    return f"{base}-kopie"


# --- Einstellungen aus der Sicherung ----------------------------------------------------------------


def parse_backup_config(text: str) -> dict[str, str]:
    """`key: value` bis zum ersten Abschnitt (`[snapshot]`, `[PENDING]`); Kommentare (`#...`, dort steht auch die
    Notiz) bleiben außen vor."""
    config: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("["):
            break
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip():
            config[key.strip()] = value.strip()
    return config


def _properties(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in value.split(","):
        key, sep, val = part.partition("=")
        if sep:
            out.setdefault(key.strip(), val.strip())
    return out


def _positional(value: str) -> str:
    """Der erste Eintrag ohne `name=` (`hostpci0: 0000:01:00.0,pcie=1` -> `0000:01:00.0`)."""
    first = value.split(",", 1)[0].strip()
    return "" if "=" in first else first


def _short_name(value: Any) -> str | None:  # noqa: ANN401 - Name aus der Sicherung oder aus Proxmox
    """Ein Name für Meldungen und Notiz: einzeilig, höchstens `NAME_MAX` Zeichen. Der Gastname in einer Sicherung ist
    frei beschreibbar (sie kann von sonstwoher stammen) und darf Meldung und Notiz nicht ausufern lassen."""
    return clean_text(value, NAME_MAX)


def _acl_name_ok(name: Any) -> bool:  # noqa: ANN401 - Text aus der Sicherung
    """Ob ein Name aus der Sicherung (Bridge, Zuordnung) in einen Rechte-Pfad passen darf: nur diese Zeichen, nicht lang,
    und nicht nur Punkte (`..` wäre ein Weg nach oben). Sonst wird das Recht nicht geprüft, und es gibt einen Hinweis."""
    return isinstance(name, str) and _ACL_NAME_RE.fullmatch(name) is not None and name.strip(".") != ""


def _static_address(value: str | None) -> str | None:
    if not value or value.lower() in ("dhcp", "manual", "auto", "dhcp6"):
        return None
    return value


@dataclass
class BackupInfo:
    name: str | None = None
    disk_bytes: int | None = None
    nets: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    refuse: str | None = None
    mappings: list[str] = field(default_factory=list)
    """Proxmox-Zuordnungen (`/mapping/pci/<name>` ...), für die der Token `Mapping.Use` braucht."""
    privileged: bool = False


def _devices(kind: str, config: dict[str, str], info: BackupInfo) -> None:
    """Hinweise und Ablehnungsgründe für Hardware des Servers, die die Sicherung durchreicht."""
    # VMs: `hostpci`/`usb` ohne Zuordnung (`host=...`) stellt Proxmox nur für root wieder her (`check_mapping_access`), und
    # Nodvard Deck ist nie root: gleich ablehnen. Zuordnungen brauchen `Mapping.Use`. Server-Platten (`/dev/...`) mit
    # `backup=0` lässt Proxmox weg, sonst wird daraus eine neue Platte. Container: `dev<N>` übernimmt Proxmox mit.
    raw: list[str] = []
    for key in sorted(config):
        value = config[key]
        props = _properties(value)
        if kind == "qemu" and (_HOSTPCI_RE.fullmatch(key) or _USB_RE.fullmatch(key)):
            area = "pci" if key.startswith("hostpci") else "usb"
            mapping = props.get("mapping")
            host = props.get("host") or _positional(value)
            if mapping and not _acl_name_ok(mapping):
                info.warnings.append(
                    f"{key} reicht ein Gerät des Servers durch (Zuordnung mit ungültigem Namen). Ob der Zugang sie nutzen darf "
                    "(Mapping.Use), ließ sich nicht prüfen; Proxmox prüft das beim Anlegen selbst. Die Kopie bekommt das Gerät "
                    "auch – laufen beide, kann nur einer es nutzen."
                )
            elif mapping:
                info.mappings.append(f"/mapping/{area}/{mapping}")
                info.warnings.append(
                    f"{key} reicht ein Gerät des Servers durch (Zuordnung „{mapping}“). Die Kopie bekommt es auch – laufen "
                    "beide, kann nur einer es nutzen. Läuft das Original, das Gerät vor dem Start der Kopie entfernen."
                )
            elif host and host.lower() != "spice":
                raw.append(key)
        elif kind == "qemu" and _VIRTIOFS_RE.fullmatch(key):
            dirid = props.get("dirid") or _positional(value)
            if dirid and not _acl_name_ok(dirid):
                info.warnings.append(
                    f"{key} teilt einen Ordner des Servers (Zuordnung mit ungültigem Namen) – die Kopie auch. Ob der Zugang "
                    "sie nutzen darf (Mapping.Use), ließ sich nicht prüfen; Proxmox prüft das beim Anlegen selbst."
                )
            elif dirid:
                info.mappings.append(f"/mapping/dir/{dirid}")
                info.warnings.append(f"{key} teilt einen Ordner des Servers (Zuordnung „{dirid}“) – die Kopie auch.")
        elif kind == "qemu" and _QEMU_DISK_RE.fullmatch(key):
            path = props.get("file") or _positional(value)
            if path.startswith("/dev/"):
                if props.get("backup") == "0":
                    info.warnings.append(
                        f"{key} ist eine Platte des Servers ({path}) und nicht in der Sicherung – die Kopie bekommt sie nicht."
                    )
                else:
                    info.warnings.append(
                        f"{key} war eine Platte des Servers ({path}); die Kopie bekommt stattdessen eine neue Platte "
                        "auf dem Zielspeicher."
                    )
        elif kind == "lxc" and _LXC_DEV_RE.fullmatch(key):
            info.warnings.append(
                f"{key} reicht ein Gerät des Servers in den Container durch ({_positional(value) or props.get('path', '?')}). "
                "Die Kopie bekommt es auch – laufen beide, teilen sie sich das Gerät."
            )
    if raw:
        info.refuse = (
            f"Die VM reicht Geräte des Servers direkt durch ({', '.join(raw)}). So eine Sicherung lässt Proxmox nur root "
            "wiederherstellen, nicht den Zugang von Nodvard Deck. Bitte in Proxmox selbst wiederherstellen."
        )


def describe_config(kind: str, config: dict[str, str] | None) -> BackupInfo:
    """Was die Sicherung über den Gast verrät: Name, Plattengröße (Obergrenze, space.py), Netzwerkkarten und feste
    Adressen, durchgereichte Hardware, und ob Proxmox die Wiederherstellung einem Token überhaupt erlaubt."""
    info = BackupInfo()
    if config is None:
        info.warnings.append(
            "Die Einstellungen in der Sicherung ließen sich nicht lesen. Die Kopie wird trotzdem wie beschrieben "
            "angelegt; nur Name, Plattengröße, Netzwerkkarten und Geräte des Originals sind hier unbekannt."
        )
        return info
    info.name = _short_name(config.get("hostname" if kind == "lxc" else "name"))
    info.disk_bytes = backed_up_disk_bytes(kind, config)
    cloud_init = {
        key.replace("ipconfig", "net"): _properties(value) for key, value in config.items() if _IPCONFIG_RE.fullmatch(key)
    }
    for key in sorted((k for k in config if _NET_RE.fullmatch(k)), key=lambda k: int(k[3:])):
        props = _properties(config[key])
        source = props if kind == "lxc" else cloud_init.get(key, {})
        addresses = [a for a in (_static_address(source.get("ip")), _static_address(source.get("ip6"))) if a]
        info.nets.append({"key": key, "bridge": props.get("bridge"), "tag": props.get("tag"), "addresses": addresses})
        if addresses:
            info.warnings.append(
                f"{key} hat in der Sicherung die feste Adresse {', '.join(addresses)} – die Kopie bekommt dieselbe."
            )
    if kind == "qemu" and info.nets and not cloud_init:
        info.warnings.append(
            "Bei einer VM steht die IP-Adresse meist im Gast-System selbst; Nodvard Deck sieht sie nicht. Die Kopie hat "
            "dieselbe wie das Original."
        )
    if kind == "lxc":
        binds = [k for k, v in config.items() if _LXC_MP_RE.fullmatch(k) and v.split(",", 1)[0].startswith("/")]
        if binds:
            info.refuse = (
                f"Der Container bindet Ordner oder Geräte des Servers ein ({', '.join(sorted(binds))}). So eine "
                "Sicherung lässt Proxmox nur root wiederherstellen, nicht den Zugang von Nodvard Deck. Bitte in "
                "Proxmox selbst wiederherstellen."
            )
        info.privileged = str(config.get("unprivileged", "0")).lower() not in _TRUE
        if info.privileged:
            info.warnings.append(
                "Privilegierter Container: Proxmox verlangt dafür vom Zugang zusätzlich Sys.Modify auf „/“."
            )
    _devices(kind, config, info)
    if str(config.get("template", "0")).lower() in _TRUE:
        info.warnings.append("Die Sicherung ist eine Vorlage (Template) – die Kopie wird wieder eine Vorlage.")
    return info


# --- Platz -------------------------------------------------------------------------------------------


def gb(value: float) -> str:
    """Wie `formatBytes` der Seite: unter 10 GB eine Nachkommastelle, darüber keine."""
    amount = value / 1024**3
    return f"{amount:.1f} GB" if amount < 10 else f"{amount:.0f} GB"


def needed_bytes(backup_size: int, disk_bytes: int | None, storage_type: str | None) -> int:
    need = math.ceil(backup_size * SPACE_FACTOR) + SPACE_RESERVE_BYTES
    if disk_bytes and storage_type in THICK_STORAGE_TYPES:
        need = max(need, disk_bytes + SPACE_RESERVE_BYTES)
    return need


def space_warning(disk_bytes: int | None, avail: int, storage_type: str | None) -> str | None:
    """Weiche Warnung: reicht der Platz nur, wenn der Speicher erst bei Bedarf belegt?"""
    if not disk_bytes or storage_type in THICK_STORAGE_TYPES or avail >= disk_bytes + SPACE_RESERVE_BYTES:
        return None
    return (
        f"Kann knapp werden: Die Platten der Kopie sind zusammen bis zu {gb(disk_bytes)} groß, frei sind {gb(avail)}. "
        "Das reicht nur, wenn der Speicher Platz erst bei Bedarf belegt (Thin Provisioning)."
    )


# --- Prüfungen gegen Proxmox --------------------------------------------------------------------------


async def online_nodes(connector: ProxmoxBackupConnector) -> list[str]:
    try:
        rows = await connector.list_nodes()
    except ProxmoxBackupApiError as exc:
        raise RestoreError(f"Proxmox ist nicht erreichbar: {exc}") from None
    return [str(r["node"]) for r in rows if r.get("node") and r.get("status") in (None, "online")]


async def find_backup(connector: ProxmoxBackupConnector, node: str, volid: str) -> dict[str, Any]:
    """Der Eintrag der Sicherung, so wie dieser Knoten den Backup-Speicher sieht. Fehlt sie dort (Speicher nur
    eines anderen Knotens, inzwischen gelöscht), wird nichts wiederhergestellt."""
    storage = volid.split(":", 1)[0]
    guess = source_vmid_from_volid(volid)
    try:
        rows = (
            await connector.guest_backup_files(node, storage, guess) if guess
            else await connector.backup_files(node, storage)
        )
    except ProxmoxBackupApiError as exc:
        if exc.status_code == 403:
            raise RestoreError(
                f"Dem Zugang fehlt das Recht, die Sicherungen auf „{storage}“ zu lesen (VM.Backup für den Gast und "
                "Datastore.AllocateSpace auf dem Speicher)."
            ) from None
        raise RestoreError(
            f"Der Backup-Speicher „{storage}“ ist von Knoten {node} aus nicht lesbar. Wähle einen Knoten, der ihn sieht. ({exc})"
        ) from None
    row = next((r for r in rows if r.get("volid") == volid), None)
    if row is None or row.get("vmid") is None:
        raise RestoreError(
            f"Die Sicherung liegt – von Knoten {node} aus gesehen – nicht (mehr) auf „{storage}“. Liegt sie auf einem "
            "Speicher nur eines Knotens, wähle diesen Knoten."
        )
    return row


async def _backup_locations(connector: ProxmoxBackupConnector, nodes: list[str]) -> list[tuple[str, str]]:
    """(Knoten, Backup-Speicher), gemeinsame Speicher nur einmal. Nicht lesbare Knoten fallen still heraus."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for node in nodes:
        try:
            storages = await connector.backup_storages(node)
        except ProxmoxBackupApiError:
            continue
        for row in storages:
            name = row.get("storage")
            if not name or not row.get("active", 1):
                continue
            key = ("*" if row.get("shared") else node, str(name))
            if key not in seen:
                seen.add(key)
                out.append((node, str(name)))
    return out


async def storages_with_backups_of(
    connector: ProxmoxBackupConnector, vmid: int | str, locations: list[tuple[str, str]]
) -> list[str]:
    """Auf welchen Backup-Speichern noch Sicherungen eines Gastes mit dieser Nummer liegen (bestmöglich: ein
    nicht lesbarer Speicher zählt nicht)."""
    found: list[str] = []
    for node, storage in locations:
        try:
            rows = await connector.guest_backup_files(node, storage, str(vmid))
        except ProxmoxBackupApiError:
            continue
        if any(str(r.get("vmid")) == str(vmid) for r in rows) and storage not in found:
            found.append(storage)
    return found


async def ha_vmids(connector: ProxmoxBackupConnector) -> set[str] | None:
    """Nummern mit HA-Regel (`vm:900`, `ct:900`, ältere Form `900`); None, wenn nicht lesbar."""
    try:
        rows = await connector.ha_resources()
    except ProxmoxBackupApiError:
        return None
    return {str(r.get("sid") or "").rsplit(":", 1)[-1] for r in rows if isinstance(r, dict)}


async def check_vmid(
    connector: ProxmoxBackupConnector, vmid: int, *, source_vmid: str, locations: list[tuple[str, str]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Frei, nicht die des Originals, keine alten Sicherungen und keine HA-Regel darunter.
    -> (`/cluster/resources` für den Namen, Hinweise)."""
    if str(vmid) == str(source_vmid):
        raise RestoreError(
            f"Die Kopie braucht eine neue Nummer, nicht die des Originals ({source_vmid}). Wähle eine andere."
        )
    try:
        resources = await connector.guest_resources()
        taken = next((r for r in resources if str(r.get("vmid")) == str(vmid)), None)
        if taken is not None:
            kind = KIND_LABEL.get(str(taken.get("type")), "Gast")
            name = taken.get("name") or "ohne Namen"
            raise RestoreError(
                f"Die Nummer {vmid} ist schon vergeben ({kind} „{name}“ auf {taken.get('node') or '?'}). Wähle eine freie Nummer."
            )
        free = await connector.vmid_is_free(vmid)
    except ProxmoxBackupApiError as exc:
        raise RestoreError(f"Ob die Nummer {vmid} frei ist, ließ sich nicht prüfen: {exc}") from None
    if not free:
        raise RestoreError(f"Die Nummer {vmid} ist in Proxmox schon vergeben. Wähle eine freie Nummer.")
    # Alte Sicherungen unter der Nummer gehörten ab jetzt zur Kopie, und die Aufbewahrung eines Backup-Jobs könnte
    # sie löschen. Eine HA-Regel würde die Kopie starten.
    old = await storages_with_backups_of(connector, vmid, locations)
    if old:
        raise RestoreError(
            f"Unter der Nummer {vmid} liegen noch Sicherungen eines früheren Gastes ({', '.join(old)}). Mit dieser "
            "Nummer gehörten sie ab jetzt zur Kopie, und die Aufbewahrung eines Backup-Jobs könnte sie löschen. Wähle "
            "eine andere Nummer."
        )
    hints: list[str] = []
    ha = await ha_vmids(connector)
    if ha is None:
        hints.append(
            "Ob für die neue Nummer eine HA-Regel besteht (die würde die Kopie starten), ließ sich nicht prüfen – dafür "
            "braucht der Zugang Sys.Audit auf „/“. Bitte in Proxmox unter Rechenzentrum → HA nachsehen."
        )
    elif str(vmid) in ha:
        raise RestoreError(
            f"Für die Nummer {vmid} gibt es in Proxmox noch eine HA-Regel. Sie könnte die Kopie sofort starten. Entferne "
            "die Regel (Rechenzentrum → HA) oder wähle eine andere Nummer."
        )
    return resources, hints


async def suggest_vmid(
    connector: ProxmoxBackupConnector, *, source_vmid: str, locations: list[tuple[str, str]]
) -> int | None:
    """Ab `GET /cluster/nextid` die erste Nummer, die frei ist und unter der weder alte Sicherungen liegen noch eine
    HA-Regel steht (nextid nennt die kleinste freie, oft genau die eines gelöschten Gastes)."""
    try:
        candidate = await connector.next_vmid()
    except ProxmoxBackupApiError:
        return None
    if candidate is None:
        return None
    ha = await ha_vmids(connector) or set()
    for _ in range(SUGGEST_TRIES):
        if str(candidate) != str(source_vmid) and str(candidate) not in ha:
            try:
                free = await connector.vmid_is_free(candidate)
            except ProxmoxBackupApiError:
                return None
            if free and not await storages_with_backups_of(connector, candidate, locations):
                return candidate
        candidate += 1
    return None


async def target_storage(connector: ProxmoxBackupConnector, node: str, storage: str, kind: str) -> dict[str, Any]:
    try:
        status_row = await connector.storage_status(node, storage)
    except ProxmoxBackupApiError as exc:
        if exc.status_code == 403:
            raise RestoreError(
                f"Dem Zugang fehlt das Recht, den Speicher „{storage}“ zu sehen (Datastore.AllocateSpace)."
            ) from None
        raise RestoreError(f"Den Speicher „{storage}“ gibt es auf Knoten {node} nicht, oder er ist dort nicht verfügbar.") from None
    content = {c.strip() for c in str(status_row.get("content") or "").split(",") if c.strip()}
    needed = "rootdir" if kind == "lxc" else "images"
    if needed not in content:
        what = "Container" if kind == "lxc" else "Disk-Image"
        raise RestoreError(
            f"Auf „{storage}“ darf Proxmox keine {'Container' if kind == 'lxc' else 'VM-Platten'} ablegen (dort fehlt der "
            f"Inhaltstyp „{what}“). Wähle einen anderen Speicher."
        )
    if str(status_row.get("enabled", 1)) in ("0", "False", "false"):
        raise RestoreError(f"Der Speicher „{storage}“ ist in Proxmox ausgeschaltet.")
    if str(status_row.get("active", 1)) in ("0", "False", "false"):
        raise RestoreError(f"Der Speicher „{storage}“ ist auf Knoten {node} gerade nicht verfügbar.")
    return status_row


Version = tuple[int, int]


def parse_version(text: Any) -> Version | None:  # noqa: ANN401 - Text aus der Antwort von Proxmox
    """`8.2.4` oder `7.4-3` -> (8, 2) bzw. (7, 4); None, wenn nichts Brauchbares dasteht. Nur ASCII-Ziffern und höchstens
    vier je Zahl: keine Ziffernflut, keine fremden Ziffern (arabisch-indisch, volle Breite)."""
    match = _VERSION_RE.match(text) if isinstance(text, str) else None
    return (int(match.group(1)), int(match.group(2))) if match else None


async def read_version(connector: ProxmoxBackupConnector) -> tuple[Version | None, str]:
    """`GET /version` -> (Version, Text für die Meldung); (None, "") wenn sie sich nicht lesen lässt."""
    try:
        text = await connector.version()
    except ProxmoxBackupApiError:
        return None, ""
    version = parse_version(text)
    return version, (re.sub(r"[^0-9A-Za-z.+~-]", "", text)[:20] if version else "")


def upgrade_text(version: Version | None, needed: Version) -> str:
    """Wie der Server auf `needed` kommt: innerhalb derselben Hauptversion holen die normalen Updates es ein; eine neue
    Hauptversion (VE 6 auf 7, VE 5 auf 6) ist ein eigenes, großes Update, das die Update-Zentrale nicht einspielt."""
    return MAJOR_UPGRADE_TEXT if version is not None and version[0] < needed[0] else UPDATES_TEXT


def version_too_old_text(kind: str, version: Version, version_text: str) -> str:
    if kind == "lxc":
        return (
            f"Einen Container als Kopie wiederherstellen braucht Proxmox VE {MIN_VERSION_LXC[0]} oder neuer (erst dort gibt "
            f"es neue MAC-Adressen beim Wiederherstellen). Dein Server hat {version_text}. "
            f"{upgrade_text(version, MIN_VERSION_LXC)}"
        )
    return (
        f"Eine VM als Kopie wiederherstellen braucht Proxmox VE {MIN_VERSION_QEMU[0]}.{MIN_VERSION_QEMU[1]} oder neuer. "
        f"Dein Server hat {version_text}. {upgrade_text(version, MIN_VERSION_QEMU)}"
    )


def check_version(kind: str, version: Version | None, version_text: str) -> list[str]:
    """Zu alt -> `RestoreError` (nichts wird angelegt). Nicht lesbar -> Hinweis, Proxmox prüft beim Anlegen selbst.
    Container gehen auch auf älteren Versionen; was dort fehlt, steht bei `MIN_VERSION_LXC` und `POOL_CHECK_FIXED_LXC`."""
    if version is None:
        need = f"{MIN_VERSION_QEMU[0]}.{MIN_VERSION_QEMU[1]}" if kind == "qemu" else str(MIN_VERSION_LXC[0])
        return [
            f"Die Proxmox-Version ließ sich nicht lesen. {KIND_LABEL[kind]} als Kopie wiederherstellen braucht Proxmox VE "
            f"{need} oder neuer, ab VE 8 braucht der Zugang außerdem SDN.Use für die Bridges – Proxmox prüft das beim Anlegen selbst."
        ]
    minimum = MIN_VERSION_QEMU if kind == "qemu" else MIN_VERSION_LXC
    if version < minimum:
        raise RestoreError(version_too_old_text(kind, version, version_text))
    return []


def pool_setup_text(token_id: str) -> str:
    """Die Befehle, die Pool und Rechte von Hand anlegen, für Leute ohne die Einrichtung mit Passwort."""
    # Die Rolle muss zuerst da sein, sonst nimmt `acl modify` sie nicht. Benutzer und Token bekommen sie: der Token ist
    # von den Rechten seines Benutzers abhängig (Rechtetrennung) und hat nie mehr als beide zusammen.
    valid = re.fullmatch(r"([A-Za-z0-9._-]+@[A-Za-z0-9._-]+)![A-Za-z0-9._-]+", token_id or "")
    user, token = (valid.group(1), valid.group(0)) if valid else ("<Benutzer@Bereich>", "<Token-ID>")
    return (
        f"Verbinde Proxmox einmal neu mit Passwort, dann legt Nodvard Deck ihn an. Oder leg ihn von Hand an (als root auf "
        f"dem Proxmox-Server): „pveum pool add {COPY_POOL} --comment '{POOL_COMMENT}'“, dann „pveum role add {ROLE_NAME} --privs '{ROLE_PRIVS}'“ und "
        f"„pveum acl modify {POOL_PATH} --roles {ROLE_NAME} --users {user} --tokens '{token}'“. "
        "Mehr dazu: docs/10-PROXMOX-TOKEN.md, „Wiederherstellen als Kopie“."
    )


def pool_problem(
    have: dict[str, Any], kind: str, version: Version | None, token_id: str
) -> str | None:
    """Reichen die Rechte des Tokens (`have`: `GET /access/permissions` für den Pool), um dort eine Kopie anzulegen?"""
    # Ob es den Pool gibt, sieht der Zugang nicht ohne `Pool.Audit`; wer dort `VM.Allocate` hat, hat den Pool (Proxmox
    # entfernt die Rechte mit dem Pool). Fehlt `VM.Allocate`, fehlt der Pool oder das Recht: derselbe Weg.
    if "VM.Allocate" not in have:
        return (
            f"Für Kopien braucht Nodvard Deck den Pool {COPY_POOL}, und der Zugang braucht dort das Recht VM.Allocate – "
            f"der Pool oder das Recht fehlt. {pool_setup_text(token_id)} Es wurde nichts angelegt."
        )
    if (
        kind == "lxc" and version is not None and version < POOL_CHECK_FIXED_LXC
        and "Permissions.Modify" not in have and "Pool.Allocate" not in have
    ):
        return (
            f"Dein Proxmox ist älter als VE {POOL_CHECK_FIXED_LXC[0]}.{POOL_CHECK_FIXED_LXC[1]}: Dort verlangt es beim Anlegen "
            f"eines Containers in einem Pool zusätzlich das Recht Permissions.Modify oder Pool.Allocate auf dem Pool "
            f"{COPY_POOL}. Das gibt Nodvard Deck dem Zugang nicht. {upgrade_text(version, POOL_CHECK_FIXED_LXC)} "
            "Es wurde nichts angelegt."
        )
    return None


async def pool_rights(connector: ProxmoxBackupConnector) -> dict[str, Any] | None:
    """Die Rechte des Tokens auf dem Pool; None, wenn sich das nicht lesen lässt (dann prüft Proxmox selbst)."""
    try:
        return await connector.permissions(POOL_PATH)
    except ProxmoxBackupApiError:
        return None


def _missing_rights_error(missing: list[str]) -> RestoreError:
    return RestoreError(
        f"Dem Zugang fehlt ein Recht: {'; '.join(missing)}. Ohne legt Nodvard Deck keine Kopie an – sonst entstünde sie "
        "womöglich halb oder mit verbundenen Netzwerkkarten. Anleitung: docs/10-PROXMOX-TOKEN.md, „Wiederherstellen als Kopie“."
    )


async def check_rights(
    connector: ProxmoxBackupConnector, inp: RestoreInput, kind: str, info: BackupInfo, version: Version | None
) -> list[str]:
    """Die eigenen Rechte des Tokens vor dem Anlegen (`GET /access/permissions`).

    Fehlt eines sicher, wird gar nicht erst angelegt; ist die Abfrage nicht lesbar, gibt es nur einen Hinweis."""
    # Fehlt ein Recht, entstünde die Kopie womöglich trotzdem, und ihre Netzwerkkarten ließen sich nicht trennen. Sicher
    # fehlende Rechte bleiben ein Ablehnungsgrund, auch wenn sich ein weiteres nicht lesen lässt; Namen aus der Sicherung
    # (Bridge, VLAN-Tag) kommen nur in einen Pfad, wenn `_acl_name_ok` sie durchlässt, sonst bleibt es bei einem Hinweis.
    # Rechte für den neuen Gast gelten auf dem Pool `COPY_POOL`: Vor dem Anlegen steckt die neue Nummer in keinem Pool, und
    # Proxmox prüft `VM.Allocate` bei gesetztem `pool` auf dem Pool (`create_vm`); Name, `onboot` und Netzwerkkarten prüft
    # `check_vm_perm` ebenfalls zuerst dort.
    vm_rights = ["VM.Allocate", "VM.Audit", "VM.Config.Options"]
    if inp.link_down or kind == "lxc":  # Container: auch der neue Name (`hostname`) braucht VM.Config.Network
        vm_rights.append("VM.Config.Network")
    unreadable = [(
        "Die Rechte des Zugangs ließen sich vorab nicht prüfen. Fehlt eines, lehnt Proxmox ab, oder Nodvard Deck "
        "kann die Kopie danach nicht fertig einrichten (dann meldet es das)."
    )]
    have_pool = await pool_rights(connector)
    if have_pool is None:
        return unreadable
    problem = pool_problem(have_pool, kind, version, getattr(connector, "token_id", ""))
    if problem:
        raise RestoreError(problem)
    needed: list[tuple[str, list[str], str]] = [
        (f"/storage/{inp.storage}", ["Datastore.AllocateSpace"], f"auf dem Zielspeicher „{inp.storage}“"),
    ]
    if kind == "lxc" and info.privileged:
        needed.append(("/", ["Sys.Modify"], "auf „/“ (privilegierter Container)"))
    needed.extend((path, ["Mapping.Use"], f"auf {path} (durchgereichtes Gerät)") for path in info.mappings)
    hints: list[str] = []
    if version is not None and version[0] >= 8:  # `SDN.Use` gibt es erst ab Proxmox VE 8
        for net in info.nets:
            bridge, tag = net.get("bridge"), net.get("tag")
            if not bridge:
                continue
            if not _acl_name_ok(bridge) or (tag and _TAG_RE.fullmatch(str(tag)) is None):
                if not hints:
                    hints.append(
                        "Eine Bridge oder ein VLAN-Tag in der Sicherung hat einen ungültigen Namen. Ob der Zugang sie nutzen "
                        "darf (SDN.Use), ließ sich nicht prüfen; Proxmox prüft das beim Anlegen selbst."
                    )
                continue
            path = f"/sdn/zones/localnetwork/{bridge}" + (f"/{tag}" if tag else "")
            needed.append((path, ["SDN.Use"], f"für die Bridge {bridge}"))
    lacking_pool = [r for r in vm_rights if r not in have_pool]
    missing = [f"{', '.join(lacking_pool)} auf dem Pool „{COPY_POOL}“"] if lacking_pool else []
    try:
        for path, rights, where in needed:
            have = await connector.permissions(path)
            lacking = [r for r in rights if r not in have]
            if lacking:
                missing.append(f"{', '.join(lacking)} {where}")
    except ProxmoxBackupApiError:
        if missing:  # was schon sicher fehlt, gilt auch, wenn sich ein weiteres Recht nicht lesen lässt
            raise _missing_rights_error(missing) from None
        return [*unreadable, *hints]
    if missing:
        raise _missing_rights_error(missing)
    return hints


async def read_backup_config(connector: ProxmoxBackupConnector, node: str, volid: str) -> dict[str, str] | None:
    # Die Einstellungen des Originals kommen aus der Sicherung (`extractconfig`, nur lesend); auf die Nummer des
    # Originals geht kein einziger Aufruf.
    try:
        text = await connector.backup_config(node, volid)
    except ProxmoxBackupApiError:
        return None
    return parse_backup_config(text) if text else None


def _backup_kind(row: dict[str, Any], volid: str) -> str:
    kind = file_kind(row)
    if kind is None:
        raise RestoreError("Ob die Sicherung eine VM oder ein Container ist, ließ sich nicht erkennen.")
    if kind == "qemu" and _TAR_RE.search(volid):
        raise RestoreError(
            "Diese VM-Sicherung hat das sehr alte tar-Format. So eine Sicherung bitte in Proxmox selbst wiederherstellen."
        )
    return kind


def format_backup_time(ctime: Any) -> str:  # noqa: ANN401 - Zahl aus der Proxmox-Antwort
    if not isinstance(ctime, (int, float)) or isinstance(ctime, bool) or ctime <= 0:
        return "unbekannt"
    return datetime.fromtimestamp(ctime, tz=UTC).strftime("%d.%m.%Y %H:%M UTC")


# --- Plan ------------------------------------------------------------------------------------------


def _required_text(rec: dict[str, Any], key: str) -> str:
    value = rec[key]
    if not isinstance(value, str):
        raise TypeError(key)
    return value


@dataclass(frozen=True)
class CopyTarget:
    """Was das Fertig-Einrichten über eine neue Kopie wissen muss, auch nach einem Neustart (pending.py)."""

    connection: str
    node: str
    kind: str
    vmid: int
    name: str
    link_down: bool
    note: str
    source_text: str
    created_at: float
    upid: str | None = None
    announced: str | None = None
    """Der Ausgang, zu dem die Abschluss-Meldung schon raus ist (`_conclude`); der Vermerk liegt dann nur noch, weil das
    Wegräumen nicht klappte."""

    @property
    def key(self) -> str:
        return f"{self.connection}/{self.node}/{self.vmid}"

    @property
    def label(self) -> str:
        return f"{KIND_LABEL[self.kind]} {self.vmid} „{self.name}“"

    def record(self) -> dict[str, Any]:
        return {
            "connection": self.connection, "node": self.node, "kind": self.kind, "vmid": self.vmid, "name": self.name,
            "link_down": self.link_down, "note": self.note, "source_text": self.source_text,
            "created_at": self.created_at, "upid": self.upid, "announced": self.announced,
        }

    @classmethod
    def from_record(cls, rec: dict[str, Any], *, now: float | None = None) -> CopyTarget | None:
        """Nur ein Vermerk, der genau so aussieht, wie Nodvard Deck ihn schreibt, sonst None (und er fällt weg)."""
        # Die Datei ist von außen veränderbar und bestimmt, welchen Gast die Nachbereitung anfasst: Die Aufgabe muss die
        # Wiederherstellung genau dieser Nummer auf diesem Knoten sein, Notiz und Herkunft werden gekürzt, und die Zeit
        # muss endlich sein und darf nicht in der Zukunft liegen (sonst liefe die Frist nie ab).
        try:
            created_at = rec["created_at"]
            if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
                return None
            target = cls(
                connection=_required_text(rec, "connection"), node=_required_text(rec, "node"), kind=_required_text(rec, "kind"),
                vmid=rec["vmid"], name=_required_text(rec, "name"), link_down=rec["link_down"],
                note=clean_text(rec["note"], NOTE_MAX) or "", source_text=clean_text(rec.get("source_text"), SOURCE_TEXT_MAX) or "",
                created_at=float(created_at), upid=rec.get("upid"), announced=rec.get("announced"),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        current = time.time() if now is None else now
        valid = (
            _CONNECTION_RE.fullmatch(target.connection) and _NODE_RE.fullmatch(target.node) and len(target.node) <= NAME_MAX
            and target.kind in KIND_LABEL and target.note
            and isinstance(target.vmid, int) and not isinstance(target.vmid, bool) and 100 <= target.vmid <= 999_999_999
            and len(target.name) <= NAME_MAX and _NAME_RE.fullmatch(target.name) and isinstance(target.link_down, bool)
            and math.isfinite(target.created_at) and target.created_at <= current + FUTURE_SLACK_S
            and (target.upid is None or upid_is_restore_of(target.upid, target.node, target.kind, target.vmid))
            and (target.announced is None or (isinstance(target.announced, str) and re.fullmatch(r"[a-z_]{1,30}", target.announced)))
        )
        return target if valid else None


@dataclass
class RestorePlan:
    connection: str
    inp: RestoreInput
    kind: str
    source_vmid: str
    source_name: str | None
    backup_at: int | None
    backup_size: int
    disk_bytes: int | None
    need_bytes: int
    avail_bytes: int
    storage_type: str | None
    warnings: list[str]

    @property
    def kind_label(self) -> str:
        return KIND_LABEL[self.kind]

    @property
    def source_text(self) -> str:
        return f"{self.kind_label} {self.source_vmid}" + (f" ({self.source_name})" if self.source_name else "")

    def payload(self) -> dict[str, Any]:
        """Was im Gate steht. `kind`/`source_vmid`/`backup_at` sind nur zum Abgleich: beim Ausführen wird alles
        frisch aus Proxmox gelesen, weichen sie ab, gilt die Freigabe nicht."""
        return {
            "connection": self.connection, "volid": self.inp.volid, "node": self.inp.node, "storage": self.inp.storage,
            "vmid": self.inp.vmid, "name": self.inp.name, "link_down": self.inp.link_down,
            "kind": self.kind, "source_vmid": self.source_vmid, "backup_at": self.backup_at,
        }

    def create_params(self) -> dict[str, Any]:
        """Genau das, was an Proxmox geht: immer im Pool `COPY_POOL`, kein `force`, kein `start` (auch
        connector.RESTORE_KEYS lässt sie nicht durch)."""
        # `unique=1` vergibt neue MAC-Adressen (VMs und Container kennen den Schalter). `onboot=0` gleich beim
        # Anlegen, weil die Sicherung sonst den Autostart des Originals mitbringt und die Kopie beim nächsten
        # Neustart des Servers loslief.
        common = {"vmid": self.inp.vmid, "storage": self.inp.storage, "unique": 1, "onboot": 0, "pool": COPY_POOL}
        if self.kind == "lxc":
            return {**common, "ostemplate": self.inp.volid, "restore": 1, "hostname": self.inp.name}
        return {**common, "archive": self.inp.volid, "name": self.inp.name}

    def note(self) -> str:
        text = (
            f"Kopie aus der Sicherung vom {format_backup_time(self.backup_at)} von {self.source_text}, "
            "wiederhergestellt mit Nodvard Deck."
        )
        if self.inp.link_down:
            text += " Netzwerkkarten getrennt angelegt: vor dem Verbinden die IP-Adresse prüfen."
        return f"{text} {POOL_KEEP_HINT}"

    def target(self, created_at: float) -> CopyTarget:
        return CopyTarget(
            connection=self.connection, node=self.inp.node, kind=self.kind, vmid=self.inp.vmid, name=self.inp.name,
            link_down=self.inp.link_down, note=self.note(), source_text=self.source_text, created_at=created_at,
        )

    def reason(self) -> str:
        return (
            f"Sicherung von {self.source_text} vom {format_backup_time(self.backup_at)} als Kopie wiederherstellen: "
            f"neue Nummer {self.inp.vmid}, Name „{self.inp.name}“, Knoten {self.inp.node}, Speicher {self.inp.storage} "
            f"({gb(self.need_bytes)} nötig, {gb(self.avail_bytes)} frei), im Pool {COPY_POOL}. Das Original bleibt unberührt, die Kopie "
            "startet nicht von selbst"
            + (", ihre Netzwerkkarten werden getrennt, sobald Proxmox fertig ist." if self.inp.link_down else ".")
        )


async def check_restore(connector: ProxmoxBackupConnector, connection: str, values: Any) -> RestorePlan:  # noqa: ANN401
    """Alle Prüfungen vor dem Anlegen, beim Vorschlag und beim Ausführen gleich; wirft `RestoreError`.

    Dem Payload aus dem Gate wird nichts geglaubt, was sich frisch nachprüfen lässt."""
    inp = parse_input(values)
    nodes = await online_nodes(connector)
    if inp.node not in nodes:
        raise RestoreError(f"Den Knoten {inp.node} gibt es nicht, oder er ist gerade nicht online.")
    row = await find_backup(connector, inp.node, inp.volid)
    kind = _backup_kind(row, inp.volid)
    version, version_text = await read_version(connector)
    version_hints = check_version(kind, version, version_text)
    source_vmid = str(row.get("vmid"))
    locations = await _backup_locations(connector, nodes)
    resources, hints = await check_vmid(connector, inp.vmid, source_vmid=source_vmid, locations=locations)
    hints = version_hints + hints
    status_row = await target_storage(connector, inp.node, inp.storage, kind)
    info = describe_config(kind, await read_backup_config(connector, inp.node, inp.volid))
    if info.refuse:
        raise RestoreError(info.refuse)
    size = row.get("size")
    backup_size = int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0 else 0
    avail = status_row.get("avail")
    if not isinstance(avail, (int, float)) or isinstance(avail, bool) or not backup_size:
        raise RestoreError(
            f"Wie viel Platz die Kopie braucht oder auf „{inp.storage}“ frei ist, meldet Proxmox nicht. Ohne diese "
            "Angabe stellt Nodvard Deck nichts wieder her."
        )
    storage_type = str(status_row.get("type") or "") or None
    need = needed_bytes(backup_size, info.disk_bytes, storage_type)
    if avail < need:
        raise RestoreError(
            f"Zu wenig Platz auf „{inp.storage}“: Die Kopie braucht mindestens {gb(need)} (Sicherung {gb(backup_size)} "
            f"plus Reserve), frei sind nur {gb(avail)}. Wähle einen anderen Speicher."
        )
    hints += await check_rights(connector, inp, kind, info, version)
    warnings = list(info.warnings) + hints
    soft = space_warning(info.disk_bytes, int(avail), storage_type)
    if soft:
        warnings.append(soft)
    source_name = info.name or next(
        (name for r in resources if str(r.get("vmid")) == source_vmid and (name := _short_name(r.get("name")))), None
    )
    ctime = row.get("ctime")
    return RestorePlan(
        connection=connection, inp=inp, kind=kind, source_vmid=source_vmid, source_name=source_name,
        backup_at=int(ctime) if isinstance(ctime, (int, float)) and not isinstance(ctime, bool) else None,
        backup_size=backup_size, disk_bytes=info.disk_bytes, need_bytes=need, avail_bytes=int(avail),
        storage_type=storage_type, warnings=warnings,
    )


# --- Auswahl für den Dialog (nur lesend) -----------------------------------------------------------


async def guest_backups(connector: ProxmoxBackupConnector, vmid: str, kind: str | None) -> dict[str, Any]:
    """Die Sicherungen eines Gastes (Nummer, optional Art), neueste zuerst, für die Auswahl im Dialog."""
    if not _VMID_RE.fullmatch(vmid):
        raise RestoreError("Unbekannter Gast.", INVALID)
    nodes = await online_nodes(connector)
    try:
        resources = await connector.guest_resources()
    except ProxmoxBackupApiError:
        resources = []
    guest = next((r for r in resources if str(r.get("vmid")) == vmid), None)
    wanted = kind if kind in KIND_LABEL else (str(guest.get("type")) if guest and guest.get("type") in KIND_LABEL else None)
    backups: list[dict[str, Any]] = []
    errors: list[str] = []
    for node, storage in await _backup_locations(connector, nodes):
        try:
            rows = await connector.guest_backup_files(node, storage, vmid)
        except ProxmoxBackupApiError as exc:
            errors.append(f"{storage}: {exc}")
            continue
        for row in rows:
            row_kind = file_kind(row)
            if str(row.get("vmid")) != vmid or row_kind is None or (wanted and row_kind != wanted):
                continue
            if any(b["volid"] == row.get("volid") for b in backups):
                continue
            backups.append({
                "volid": row.get("volid"), "storage": storage, "kind": row_kind, "kind_label": KIND_LABEL[row_kind],
                "ctime": row.get("ctime"), "size": row.get("size"),
                "notes": clean_text(row.get("notes"), 120), "protected": bool(row.get("protected")),
            })
    backups.sort(key=lambda b: b.get("ctime") or 0, reverse=True)
    return {
        "vmid": vmid, "name": (guest or {}).get("name"), "kind": wanted, "exists": guest is not None,
        "backups": backups, "errors": errors,
    }


async def restore_blocker(connector: ProxmoxBackupConnector, kind: str) -> tuple[str | None, list[str]]:
    """Was die Kopie schon vor jeder Eingabe verhindert: ein zu alter Proxmox oder fehlender Pool bzw. fehlende Rechte
    dort. -> (Satz oder None, Hinweise). Nur für den Dialog; verbindlich prüft `check_restore`."""
    version, version_text = await read_version(connector)
    try:
        notes = check_version(kind, version, version_text)
    except RestoreError as exc:
        return str(exc), []
    have = await pool_rights(connector)
    if have is None:
        return None, notes
    return pool_problem(have, kind, version, getattr(connector, "token_id", "")), notes


async def restore_options(connector: ProxmoxBackupConnector, connection: str, volid: str) -> dict[str, Any]:
    """Vorschläge für den Dialog: welche Knoten die Sicherung sehen, welche Speicher dort passen (mit Platz),
    freie Nummer, Name, Hinweise. Verbindlich prüft erst `check_restore` beim Vorschlagen und Ausführen."""
    check_volid(volid)
    nodes = await online_nodes(connector)
    storage_name = volid.split(":", 1)[0]
    row: dict[str, Any] | None = None
    candidates: list[str] = []
    for node in nodes:
        try:
            storages = await connector.backup_storages(node)
        except ProxmoxBackupApiError:
            continue
        entry = next((s for s in storages if s.get("storage") == storage_name and s.get("active", 1)), None)
        if entry is None:
            continue
        if entry.get("shared") and row is not None:
            candidates.append(node)
            continue
        try:
            found = await find_backup(connector, node, volid)
        except RestoreError:
            continue
        row = row or found
        candidates.append(node)
    if row is None or not candidates:
        raise RestoreError("Die Sicherung ist von keinem Knoten aus lesbar (gelöscht, oder der Backup-Speicher ist nicht erreichbar).")
    kind = _backup_kind(row, volid)
    source_vmid = str(row.get("vmid"))
    info = describe_config(kind, await read_backup_config(connector, candidates[0], volid))
    blocked, notes = await restore_blocker(connector, kind)
    try:
        resources = await connector.guest_resources()
    except ProxmoxBackupApiError:
        resources = []
    source = next((r for r in resources if str(r.get("vmid")) == source_vmid and r.get("type") == kind), None)
    source_name = info.name or (_short_name(source.get("name")) if source else None)
    locations = await _backup_locations(connector, nodes)
    size = row.get("size")
    backup_size = int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0 else 0
    content = "rootdir" if kind == "lxc" else "images"
    node_rows: list[dict[str, Any]] = []
    for node in candidates:
        try:
            storages = await connector.guest_storages(node, content)
        except ProxmoxBackupApiError:
            storages = []
        choices = []
        for s in storages:
            if not s.get("storage") or not s.get("active", 1):
                continue
            avail = s.get("avail") if isinstance(s.get("avail"), (int, float)) else None
            storage_type = str(s.get("type") or "") or None
            need = needed_bytes(backup_size, info.disk_bytes, storage_type) if backup_size else None
            fits = avail is not None and need is not None and avail >= need
            choices.append({
                "storage": s["storage"], "type": storage_type, "avail": avail, "total": s.get("total"),
                "need_bytes": need, "fits": fits,
                "warning": space_warning(info.disk_bytes, int(avail), storage_type) if fits and avail is not None else None,
            })
        choices.sort(key=lambda c: (not c["fits"], -(c["avail"] or 0)))
        node_rows.append({"node": node, "storages": choices})
    preferred = str(source.get("node")) if source and source.get("node") in candidates else candidates[0]
    default_storage = next(
        (c["storage"] for n in node_rows if n["node"] == preferred for c in n["storages"] if c["fits"]), None
    )
    ctime = row.get("ctime")
    return {
        "connection": connection, "volid": volid, "kind": kind, "kind_label": KIND_LABEL[kind],
        "source_vmid": source_vmid, "source_name": source_name, "source_exists": source is not None,
        "backup_at": ctime if isinstance(ctime, (int, float)) else None, "backup_size": backup_size,
        "disk_bytes": info.disk_bytes, "nets": info.nets, "warnings": [*notes, *info.warnings], "refuse": info.refuse,
        "blocked": blocked, "pool": COPY_POOL, "keep_hint": POOL_KEEP_HINT,
        "suggested_vmid": await suggest_vmid(connector, source_vmid=source_vmid, locations=locations),
        "suggested_name": suggest_name(source_name, source_vmid),
        "nodes": node_rows, "default_node": preferred, "default_storage": default_storage,
    }


# --- Ausführen --------------------------------------------------------------------------------------


@dataclass
class TaskOutcome:
    state: str  # "ok", "failed", "timeout", "lost"
    exitstatus: str | None = None
    """Bei "ok" der Ausgang von Proxmox; bei "failed" nur sein Grund, soweit er sich anzeigen lässt (`proxmox_reason`), sonst None."""


def task_finished_ok(exitstatus: str) -> bool:
    return exitstatus == "OK" or re.fullmatch(r"WARNINGS: \d+", exitstatus) is not None


async def wait_for_restore(connector: ProxmoxBackupConnector, node: str, upid: str) -> TaskOutcome:
    """Fragt nach, bis die Proxmox-Aufgabe fertig ist. `RESTORE_TIMEOUT_S`/`POLL_INTERVAL_S` werden bei jedem Aufruf
    gelesen. Fehler und Antworten ohne `status` zählen nicht als Ausgang ("vielleicht läuft sie noch"), erst nach
    `MAX_POLL_ERRORS` in Folge gibt die Aktion auf ("lost")."""
    deadline = time.monotonic() + RESTORE_TIMEOUT_S
    errors = 0
    while True:
        try:
            task = await connector.task_status(node, upid)
        except ProxmoxBackupApiError:
            task = {}
        state = task.get("status") if isinstance(task, dict) else None
        if state == "stopped":
            exitstatus = str(task.get("exitstatus") or "")
            if task_finished_ok(exitstatus):
                return TaskOutcome("ok", exitstatus)
            return TaskOutcome("failed", proxmox_reason(exitstatus))
        if state == "running":
            errors = 0
        else:
            errors += 1
            if errors >= MAX_POLL_ERRORS:
                return TaskOutcome("lost")
        if time.monotonic() >= deadline:
            return TaskOutcome("timeout")
        await asyncio.sleep(POLL_INTERVAL_S)


def upid_is_restore_of(upid: str, node: str, kind: str, vmid: int) -> bool:
    """Ob die Aufgaben-Kennung (`UPID:<knoten>:<pid>:<pstart>:<start>:<art>:<nummer>:<benutzer>:`) genau diese
    Wiederherstellung meint. Nur dann fasst die Nachbereitung die Nummer an: sie gehört dann sicher der Kopie."""
    if not isinstance(upid, str) or not UPID_RE.fullmatch(upid):
        return False
    parts = upid.split(":")
    return parts[1] == node and parts[5] == WORKER.get(kind) and parts[6] == str(vmid)


def with_link_down(value: str) -> str:
    """`virtio=BC:..,bridge=vmbr0` -> `virtio=BC:..,bridge=vmbr0,link_down=1` (vorhandenes `link_down` wird ersetzt)."""
    parts = [p for p in value.split(",") if p]
    out = ["link_down=1" if p.partition("=")[0].strip() == "link_down" else p for p in parts]
    if "link_down=1" not in out:
        out.append("link_down=1")
    return ",".join(out)


def _truthy(value: Any) -> bool:  # noqa: ANN401
    return str(value).strip().lower() in _TRUE


def finish_changes(target: CopyTarget, config: dict[str, Any]) -> dict[str, Any]:
    """Was nach dem Anlegen noch zu setzen ist: getrennte Netzwerkkarten, Autostart aus, Name, Notiz."""
    changes: dict[str, Any] = {}
    # Im Gast gilt noch die Netzwerk-Einstellung des Originals (feste IP, Rechnername); darum werden die
    # Netzwerkkarten getrennt, bis die Person sie in Proxmox wieder verbindet.
    if target.link_down:
        for key in sorted(k for k in config if _NET_RE.fullmatch(k)):
            new = with_link_down(str(config[key]))
            if new != str(config[key]):
                changes[key] = new
    if _truthy(config.get("onboot")):
        changes["onboot"] = 0
    name_key = "hostname" if target.kind == "lxc" else "name"
    if str(config.get(name_key) or "") != target.name:
        changes[name_key] = target.name
    old = str(config.get("description") or "")
    if not old.startswith(target.note):
        # Proxmox nimmt höchstens 8 KiB; gekürzt wird nach Bytes, nicht mitten in einem Umlaut.
        text = target.note + (f"\n\n{old}" if old else "")
        changes["description"] = text.encode()[:DESCRIPTION_MAX].decode(errors="ignore")
    if changes and config.get("digest"):
        changes["digest"] = config["digest"]
    return changes


def finish_problems(target: CopyTarget, config: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if target.link_down:
        open_nets = [k for k in sorted(config) if _NET_RE.fullmatch(k) and _properties(str(config[k])).get("link_down") != "1"]
        if open_nets:
            problems.append(f"Netzwerkkarten nicht getrennt ({', '.join(open_nets)})")
    if _truthy(config.get("onboot")):
        problems.append("Autostart ist an")
    return problems


async def finish_copy(connector: ProxmoxBackupConnector, target: CopyTarget) -> tuple[str | None, bool]:
    """Richtet die neue Kopie fertig ein (nur ihre Nummer!) und liest nach. -> (None oder was nicht klappte,
    ob ein weiterer Versuch sinnlos ist; 403: dem Zugang fehlt ein Recht)."""
    vmid = str(target.vmid)
    problem: str | None = None
    for attempt in range(FINISH_ATTEMPTS):
        if attempt:
            await asyncio.sleep(FINISH_RETRY_DELAY_S)
        try:
            config = await connector.guest_config(target.node, target.kind, vmid)
            changes = finish_changes(target, config)
            if changes:
                await connector.update_guest_config(target.node, target.kind, vmid, changes)
                config = await connector.guest_config(target.node, target.kind, vmid)
            problems = finish_problems(target, config)
        except ProxmoxBackupApiError as exc:
            problem = str(exc)
            if exc.status_code == 403:
                return problem, True
            continue
        if not problems:
            return None, False
        problem = "; ".join(problems)
    return problem or "unbekannter Fehler", False


def _network_sentence(link_down: bool) -> str:
    if link_down:
        return (
            "Ihre Netzwerkkarten sind getrennt: Im Gast gilt noch die Netzwerk-Einstellung des Originals (z. B. eine "
            f"feste IP-Adresse). {RECONNECT_HINT}"
        )
    return (
        "Achtung: Im Gast gilt noch die Netzwerk-Einstellung des Originals (z. B. eine feste IP-Adresse). Läuft das "
        "Original noch, vor dem Start der Kopie die IP ändern oder das Netz trennen."
    )


def _unclear(status_code: int | None) -> bool:
    """Ob ein Fehler beim Anlegen offen lässt, ob Proxmox angefangen hat: keine Antwort, oder ein Proxy bzw. Proxmox'
    eigener Weiterleiter meldet sich (502/503/504, 595/596). Ein 4xx oder 500 kommt von Proxmox selbst, vor dem Anlegen."""
    return status_code is None or status_code >= 502


class RestoreCopyExecutor:
    """Führt `backup.restore_copy` aus."""

    action_types = frozenset({ACTION_TYPE})

    def __init__(self, ctx: ExtensionContext, connectors_factory: ConnectorsFactory) -> None:
        self._ctx = ctx
        self._connectors_factory = connectors_factory

    async def execute(self, req: ActionRequest) -> ActionResult:
        result = await self._execute(req)
        await _audit(self._ctx, req.payload.get("connection"), req.payload.get("vmid"), result.success,
                     (result.output if result.success else result.error) or "", result.detail, req.correlation_id)
        return result

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        connection = str(req.payload.get("connection") or "")
        connector = (await self._connectors_factory()).get(connection)
        if connector is None:
            return DryRunReport(would_change=False, summary=f"Backup-Verbindung '{connection}' ist nicht (mehr) konfiguriert.")
        try:
            plan = await check_restore(connector, connection, req.payload)
        except RestoreError as exc:
            return DryRunReport(would_change=False, summary=str(exc))
        return DryRunReport(would_change=True, summary=plan.reason(), detail={"warnings": plan.warnings})

    async def _execute(self, req: ActionRequest) -> ActionResult:
        payload = req.payload
        connection = str(payload.get("connection") or "")
        nothing = "Es wurde nichts angelegt, das Original ist unberührt."
        if req.risk not in (Risk.HIGH, Risk.CRITICAL):
            return ActionResult(
                success=False, error=f"Diese Wiederherstellung wurde nicht mit hohem Risiko freigegeben. {nothing} Bitte neu vorschlagen.",
            )
        connector = (await self._connectors_factory()).get(connection)
        if connector is None:
            return ActionResult(success=False, error=f"Backup-Verbindung '{connection}' ist nicht (mehr) konfiguriert. {nothing}")
        try:
            plan = await check_restore(connector, connection, payload)
        except RestoreError as exc:
            return ActionResult(success=False, error=f"Nicht wiederhergestellt: {exc} {nothing}")
        for key, actual in (("kind", plan.kind), ("source_vmid", plan.source_vmid), ("backup_at", plan.backup_at)):
            if key in payload and str(payload[key]) != str(actual):
                return ActionResult(
                    success=False,
                    error=f"Die Sicherung passt nicht mehr zum Vorschlag (sie wurde ersetzt oder geändert). {nothing} Bitte neu vorschlagen.",
                )
        target = plan.target(time.time())
        if target.key in pending.ACTIVE:
            # Ein zweiter Lauf zur selben Nummer (gleichzeitig vorgeschlagen und freigegeben): der erste ist schon
            # dabei. Hier wird nichts angefasst, weder sein Vermerk noch sein Platz in `ACTIVE` (das `finally` unten
            # gehört dem Lauf, der sich eingetragen hat).
            return ActionResult(
                success=False,
                error=f"Nicht wiederhergestellt: Für diese Nummer läuft schon eine Wiederherstellung. {nothing}",
            )
        pending.ACTIVE.add(target.key)
        try:
            return await self._create_and_finish(connector, plan, target)
        finally:
            pending.ACTIVE.discard(target.key)

    async def _create_and_finish(self, connector: ProxmoxBackupConnector, plan: RestorePlan, target: CopyTarget) -> ActionResult:
        # Ablauf: Vermerk (pending.py) -> Anlegen -> Aufgabe abwarten (`RESTORE_TIMEOUT_S`) -> fertig einrichten
        # (`PUT .../config` mit `digest`, nur auf der neuen Nummer und nur bei nachweislich dieser Wiederherstellung) ->
        # nachlesen -> Meldung -> Vermerk weg. Endet die Aktion vorher, holt der Wächter das nach (`follow_up_open_copies`).
        nothing = "Es wurde nichts angelegt, das Original ist unberührt."
        detail: dict[str, Any] = {
            "connection": plan.connection, "node": plan.inp.node, "storage": plan.inp.storage, "vmid": plan.inp.vmid,
            "name": plan.inp.name, "kind": plan.kind, "source_vmid": plan.source_vmid, "volid": plan.inp.volid,
            "link_down": plan.inp.link_down, "pool": COPY_POOL, "created": False,
        }
        pending.ANNOUNCED.pop(target.key, None)
        try:
            # Vor dem Anlegen: endet die Aktion danach unerwartet, weiß der Wächter von der Kopie.
            pending.remember(self._ctx, target.key, target.record())
        except OSError:
            return ActionResult(
                success=False, detail=detail,
                error=(
                    "Nodvard Deck kann sich die Wiederherstellung nicht merken (sein Datenordner ist nicht beschreibbar) "
                    f"und könnte die Kopie nach einem Abbruch nicht fertig einrichten. {nothing}"
                ),
            )
        # Ohne `force` prüft Proxmox beim Anlegen selbst clusterweit, ob die Nummer frei ist (VMs:
        # `create_and_lock_config`, Container: `check_vmid_unused`); wurde sie seit unserer Prüfung vergeben, lehnt es ab.
        try:
            upid = await connector.restore_guest(plan.inp.node, plan.kind, plan.create_params())
        except ProxmoxBackupApiError as exc:
            if _unclear(exc.status_code):
                return ActionResult(
                    success=False, detail={**detail, "created": None},
                    error=(
                        f"Keine klare Antwort von Proxmox ({exc}). Ob die Wiederherstellung als {target.label} begonnen hat, "
                        f"ist unklar. {FOLLOW_UP_NOTE}"
                    ),
                )
            if exc.status_code == 403:
                error = f"{RESTORE_PERMISSION_HINT} ({exc})"
            else:
                error = f"Proxmox hat die Wiederherstellung abgelehnt: {exc} {nothing}"
            await _conclude(self._ctx, target, "rejected", f"Wiederherstellung abgelehnt: {target.label}", error, success=False)
            return ActionResult(success=False, error=error, detail=detail)
        upid = str(upid or "")
        detail["upid"] = upid
        if not upid_is_restore_of(upid, plan.inp.node, plan.kind, plan.inp.vmid):
            return ActionResult(
                success=False, detail={**detail, "created": None},
                error=f"Proxmox hat keine gültige Aufgaben-Kennung geliefert. Ob {target.label} angelegt wird, ist unklar. {FOLLOW_UP_NOTE}",
            )
        target = replace(target, upid=upid)
        try:
            pending.remember(self._ctx, target.key, target.record())
        except OSError:
            log.warning("restore_pending_upid_not_saved key=%s", target.key)
        outcome = await wait_for_restore(connector, plan.inp.node, upid)
        detail["task_status"] = outcome.state
        if outcome.state == "timeout":
            return ActionResult(
                success=False, detail={**detail, "created": None},
                error=(
                    f"Die Wiederherstellung läuft nach {RESTORE_TIMEOUT_S / 60:g} Minuten noch; Proxmox macht weiter. "
                    f"{FOLLOW_UP_NOTE}"
                ),
            )
        if outcome.state == "lost":
            return ActionResult(
                success=False, detail={**detail, "created": None},
                error=f"Proxmox antwortet nicht mehr. Ob die Wiederherstellung fertig wurde, ist unklar. {FOLLOW_UP_NOTE}",
            )
        if outcome.state == "failed":
            title, body = failed_message(target, outcome.exitstatus)
            await _conclude(self._ctx, target, "failed", title, body, success=False)
            return ActionResult(
                success=False, detail={**detail, "created": None, "task_status": outcome.exitstatus or "unbekannter Ausgang"},
                error=body,
            )
        detail["created"] = True
        problem, permanent = await finish_copy(connector, target)
        if problem is not None:
            if permanent:
                title, body = unfinished_message(target, problem)
                await _conclude(self._ctx, target, "unfinished", title, body, success=False)
                retry = (
                    "Bevor du sie startest: in Proxmox „Beim Booten starten“ prüfen und die Netzwerkkarten trennen oder die "
                    "IP-Adresse ändern – oder die Kopie löschen."
                )
            else:
                retry = (
                    f"Nodvard Deck versucht es weiter ({FOLLOW_UP_INTERVAL_TEXT}) und meldet sich. Bis dahin die Kopie bitte "
                    "nicht starten."
                )
            return ActionResult(
                success=False, detail=detail,
                error=(
                    f"Die Kopie ist angelegt ({target.label} auf {plan.inp.node}, ausgeschaltet), aber Nodvard Deck konnte "
                    f"sie nicht fertig einrichten: {problem}. {retry} Das Original ist unberührt."
                ),
            )
        title, body = finished_message(target)
        await _conclude(self._ctx, target, "finished", title, body, success=True)
        warnings = f" Proxmox meldet Warnungen ({outcome.exitstatus}), Details im Aufgabenprotokoll." if outcome.exitstatus != "OK" else ""
        return ActionResult(
            success=True, detail=detail,
            output=(
                f"Kopie angelegt: {target.label} auf {plan.inp.node}, Speicher {plan.inp.storage}, im Pool {COPY_POOL}, aus der "
                f"Sicherung vom {format_backup_time(plan.backup_at)} von {plan.source_text}. Sie ist ausgeschaltet und startet "
                f"nicht von selbst, auch nicht beim Neustart des Servers, und hat neue MAC-Adressen. "
                f"{_network_sentence(plan.inp.link_down)} {POOL_KEEP_HINT} Das Original ist unberührt.{warnings}"
            ),
        )


async def _audit(
    ctx: ExtensionContext, connection: Any, vmid: Any, success: bool, text: str,  # noqa: ANN401
    detail: dict[str, Any] | None, correlation_id: str | None,
) -> None:
    detail = dict(detail or {})
    try:
        await ctx.audit.log(
            action=AUDIT_ACTION, outcome="success" if success else "failure", target_type="restore",
            target_id=f"{connection}/{vmid}", reason=text[:500],
            detail={
                k: detail.get(k)
                for k in ("node", "storage", "kind", "source_vmid", "volid", "link_down", "created", "task_status", "followup")
            },
            correlation_id=correlation_id,
        )
    except Exception:  # noqa: BLE001 - das Gate bzw. die Meldung hält das Ergebnis ohnehin fest
        return


# --- Abschluss und Nachbereiten offener Kopien (Backup-Wächter) --------------------------------------


def finished_message(target: CopyTarget) -> tuple[str, str]:
    return (
        f"Kopie fertig eingerichtet: {target.label}",
        f"Proxmox ist mit der Wiederherstellung fertig ({target.source_text}). Die Kopie auf {target.node} ist "
        f"ausgeschaltet und startet nicht von selbst. {_network_sentence(target.link_down)} {POOL_KEEP_HINT} "
        "Das Original ist unberührt.",
    )


def failed_message(target: CopyTarget, exitstatus: str | None) -> tuple[str, str]:
    """Der Ausgang der Aufgabe kommt von Proxmox: derselbe Filter und Rahmen wie bei den Gründen in Fehlerantworten
    (`proxmox_reason`, "Grund laut Proxmox ..."), nie HTML und nie eine Adresse mit Schema."""
    reason = proxmox_reason(exitstatus)
    why = (
        f"Grund laut Proxmox: {reason.rstrip('.')}."
        if reason else
        "Es nennt aber keinen Grund, den Nodvard Deck anzeigen kann; Näheres steht im Aufgabenprotokoll in Proxmox."
    )
    return (
        f"Wiederherstellung gescheitert: {target.label}",
        f"Proxmox konnte die Sicherung nicht wiederherstellen. {why} {leftover_note(target.vmid)}",
    )


def unfinished_message(target: CopyTarget, problem: str) -> tuple[str, str]:
    return (
        f"Kopie nicht fertig eingerichtet: {target.label}",
        f"Die Kopie ist da, aber Nodvard Deck konnte sie nicht fertig einrichten: {problem}. Bevor du sie startest: in "
        "Proxmox „Beim Booten starten“ prüfen und die Netzwerkkarten trennen oder die IP-Adresse ändern – oder die "
        "Kopie löschen. Das Original ist unberührt.",
    )


NOTIFY_GIVE_UP_S = FOLLOW_UP_GIVE_UP_S + 24 * 3600.0
"""Lässt sich die Abschluss-Meldung so lange nicht abschicken (der Vermerk liegt seit der Kopie), räumt der Wächter
den Vermerk trotzdem weg und schreibt es ins Protokoll, statt es für immer weiter zu versuchen."""


async def _conclude(
    ctx: ExtensionContext, target: CopyTarget, result: str, title: str, body: str, *, success: bool,
    followup: bool = False,
) -> bool:
    """Das Ende einer Kopie: genau eine Meldung, dann den Vermerk wegräumen. -> True, wenn die Meldung raus ist."""
    # Scheitert die Meldung, bleibt der Vermerk für die nächste Runde; scheitert nur das Wegräumen, verhindern `announced`
    # und der Speicher eine zweite Meldung. Der Wächter (`followup=True`) schreibt selbst ins Protokoll, die Aktion über ihr
    # Ergebnis. In Proxmox wird dabei nie etwas gelöscht.
    if target.announced is None and target.key not in pending.ANNOUNCED:
        try:
            await ctx.notify.send(Notification(
                title=title, body=body, severity=Severity.INFO if success else Severity.WARNING,
                correlation_id=f"backups-restore:{target.key}", payload={"path": PAGE_PATH},
            ))
        except Exception:  # noqa: BLE001 - dann bleibt der Vermerk, und die nächste Runde versucht es noch einmal
            log.exception("restore_conclude_notify_failed key=%s", target.key)
            if time.time() - target.created_at <= NOTIFY_GIVE_UP_S:
                return False
            log.error("restore_conclude_gave_up_notifying key=%s", target.key)
        else:
            pending.ANNOUNCED[target.key] = result
            try:
                pending.remember(ctx, target.key, replace(target, announced=result).record())
            except OSError:
                log.warning("restore_conclude_mark_not_saved key=%s", target.key)
        if followup:
            await _audit(
                ctx, target.connection, target.vmid, success, body,
                {"node": target.node, "kind": target.kind, "created": success or None, "task_status": result, "followup": True},
                f"restore:{target.connection}/{target.vmid}",
            )
    if pending.forget(ctx, target.key):
        pending.ANNOUNCED.pop(target.key, None)
    return True


async def _close(
    ctx: ExtensionContext, target: CopyTarget, result: str, title: str, body: str, *, success: bool,
) -> str:
    """Der Wächter beendet eine Kopie: Meldung, Protokoll, Vermerk weg. -> der Ausgang, oder "open", wenn die Meldung
    nicht abging (der Vermerk bleibt)."""
    return result if await _conclude(ctx, target, result, title, body, success=success, followup=True) else "open"


async def _find_restore_task(connector: ProxmoxBackupConnector, target: CopyTarget) -> str | None:
    """Die Aufgabe zu einer Kopie, deren Kennung nie ankam: Wiederherstellung genau dieser Nummer auf diesem Knoten,
    nicht älter als der Vermerk (zwei Minuten Spielraum für abweichende Uhren)."""
    rows = await connector.restore_tasks(target.node, target.vmid, WORKER[target.kind])
    found = [
        r for r in rows
        if isinstance(r, dict) and upid_is_restore_of(str(r.get("upid") or ""), target.node, target.kind, target.vmid)
        and isinstance(r.get("starttime"), (int, float)) and r["starttime"] >= target.created_at - 120
    ]
    found.sort(key=lambda r: r["starttime"], reverse=True)
    return str(found[0]["upid"]) if found else None


def _give_up_message(target: CopyTarget, reason: str) -> tuple[str, str]:
    return (
        f"Kopie nicht fertig eingerichtet: {target.label}",
        f"{reason} Nach 24 Stunden gibt Nodvard Deck auf. Bitte in Proxmox prüfen: Gibt es die Kopie, vor dem Start "
        "„Beim Booten starten“ prüfen und die Netzwerkkarten trennen oder die IP-Adresse ändern. Das Original ist unberührt.",
    )


async def _in_copy_pool(connector: ProxmoxBackupConnector, target: CopyTarget) -> bool:
    """Ob es unter der Nummer einen Gast dieser Art im Pool `COPY_POOL` gibt (`GET /cluster/resources`). Nur dort richtet
    die Nachbereitung etwas ein: jede Kopie entsteht im Pool, ein Gast außerhalb ist nie eine Kopie von Nodvard Deck."""
    rows = await connector.guest_resources()
    return any(
        isinstance(r, dict) and str(r.get("vmid")) == str(target.vmid) and r.get("type") == target.kind
        and r.get("pool") == COPY_POOL
        for r in rows
    )


async def _follow_up_one(
    ctx: ExtensionContext, connector: ProxmoxBackupConnector | None, target: CopyTarget, now: float,
) -> str:
    # Der Wächter fasst einen Gast nur an, wenn der Vermerk genau zu ihm passt (`CopyTarget.from_record`, Aufgaben-Kennung,
    # `_in_copy_pool`); sonst meldet er "Kopie nicht gefunden" und verändert nichts.
    age = now - target.created_at
    expired = age > FOLLOW_UP_GIVE_UP_S
    label = target.label
    done = target.announced or pending.ANNOUNCED.get(target.key)
    if done:  # die Meldung ist schon raus, nur das Wegräumen ging schief
        await _conclude(ctx, target, done, "", "", success=True, followup=True)
        return done

    async def give_up_or_wait(reason: str) -> str:
        if not expired:
            return "open"
        title, body = _give_up_message(target, reason)
        return await _close(ctx, target, "given_up", title, body, success=False)

    if connector is None:
        return await give_up_or_wait(f"Die Backup-Verbindung „{target.connection}“ ist nicht mehr eingerichtet.")
    try:
        upid = target.upid
        if not upid:
            upid = await _find_restore_task(connector, target)
            if upid:
                target = replace(target, upid=upid)
                try:
                    pending.remember(ctx, target.key, target.record())
                except OSError:  # die Kennung bleibt im Speicher; mit "Proxmox antwortet nicht" hat das nichts zu tun
                    log.warning("restore_pending_upid_not_saved key=%s", target.key)
        if not upid:
            if age < NO_TASK_GRACE_S:
                return "open"
            if any(str(r.get("vmid")) == str(target.vmid) for r in await connector.guest_resources()):
                return await _close(
                    ctx, target, "unclear", f"Wiederherstellung unklar: {label}",
                    f"Unter der Nummer {target.vmid} gibt es einen Gast, aber Nodvard Deck findet die zugehörige "
                    "Wiederherstellung nicht und richtet ihn deshalb nicht ein. Bitte in Proxmox prüfen (Netzwerkkarten, "
                    "„Beim Booten starten“), bevor du ihn startest. Das Original ist unberührt.",
                    success=False,
                )
            return await _close(
                ctx, target, "not_created", f"Wiederherstellung nicht begonnen: {label}",
                "Proxmox hat die Wiederherstellung nicht begonnen; es wurde nichts angelegt. Das Original ist unberührt.",
                success=False,
            )
        task = await connector.task_status(target.node, upid)
    except ProxmoxBackupApiError as exc:
        return await give_up_or_wait(f"Proxmox antwortet nicht ({exc}).")
    if not isinstance(task, dict) or task.get("status") != "stopped":
        return await give_up_or_wait("Die Wiederherstellung lief nach 24 Stunden noch oder ihr Stand war nicht lesbar.")
    exitstatus = str(task.get("exitstatus") or "")
    if not task_finished_ok(exitstatus):
        title, body = failed_message(target, exitstatus)
        return await _close(ctx, target, "failed", title, body, success=False)
    try:
        in_pool = await _in_copy_pool(connector, target)
    except ProxmoxBackupApiError as exc:
        return await give_up_or_wait(f"Proxmox antwortet nicht ({exc}).")
    if not in_pool:
        return await _close(
            ctx, target, "not_found", f"Kopie nicht gefunden: {label}",
            f"Unter der Nummer {target.vmid} gibt es im Pool {COPY_POOL} keinen Gast: Die Kopie wurde gelöscht oder aus dem "
            "Pool genommen, oder der Vermerk gehört nicht zu einer Kopie von Nodvard Deck. Nodvard Deck richtet nur Gäste in "
            "diesem Pool ein und hat deshalb nichts verändert. Gibt es die Kopie noch, vor dem Start in Proxmox „Beim Booten "
            "starten“ prüfen und die Netzwerkkarten trennen oder die IP-Adresse ändern. Das Original ist unberührt.",
            success=False,
        )
    problem, permanent = await finish_copy(connector, target)
    if problem is None:
        title, body = finished_message(target)
        return await _close(ctx, target, "finished", title, body, success=True)
    if permanent or expired:
        title, body = unfinished_message(target, problem)
        return await _close(ctx, target, "unfinished", title, body, success=False)
    return "open"


async def _follow_up_record(
    ctx: ExtensionContext, connectors: dict[str, ProxmoxBackupConnector], key: str, now: float | None,
) -> str | None:
    # Frisch lesen: Während der Wächter auf Proxmox wartete, kann die Aktion die Kopie abgeschlossen (Meldung, Vermerk weg)
    # oder eine neue begonnen haben. Mit dem Stand vom Anfang der Runde käme eine Meldung doppelt.
    record = pending.load(ctx).get(key)
    if record is None or key in pending.ACTIVE:
        return None
    current = time.time() if now is None else now
    target = CopyTarget.from_record(record, now=current)
    if target is None or target.key != key:
        log.warning("restore_pending_dropped key=%r", key)  # `%r`: der Schlüssel stammt aus einer veränderbaren Datei
        pending.forget(ctx, key)
        return None
    return await _follow_up_one(ctx, connectors.get(target.connection), target, current)


async def _expire_failing_record(ctx: ExtensionContext, key: str, now: float | None) -> None:
    """Ein Vermerk, den der Wächter in jeder Runde nicht auswerten kann (unerwarteter Fehler): nach `FOLLOW_UP_GIVE_UP_S`
    meldet der Wächter es einmal und räumt ihn weg, statt ihn für immer mitzuschleppen."""
    try:
        record = pending.load(ctx).get(key)
        current = time.time() if now is None else now
        target = CopyTarget.from_record(record, now=current) if record is not None else None
        if target is None or current - target.created_at <= FOLLOW_UP_GIVE_UP_S:
            return
        title, body = _give_up_message(
            target, "Nodvard Deck konnte den Stand dieser Wiederherstellung nicht auswerten (Näheres im Protokoll)."
        )
        await _close(ctx, target, "given_up", title, body, success=False)
    except Exception:  # noqa: BLE001 - der Wächter läuft weiter
        log.exception("restore_followup_expire_failed key=%r", key)


async def follow_up_open_copies(
    ctx: ExtensionContext, connectors_factory: ConnectorsFactory, *, now: float | None = None,
) -> dict[str, int]:
    """Für den Backup-Wächter: offene Kopien fertig einrichten, deren Aktion vorher endete. Ohne Vermerk kein
    einziger Aufruf an Proxmox. Wirft nie (der Wächter soll weiterlaufen). Jeder Vermerk für sich: ein kaputter blockiert
    die anderen nicht, und er läuft nach `FOLLOW_UP_GIVE_UP_S` ab."""
    counts: dict[str, int] = {}
    try:
        records = pending.load(ctx)
        if not records:
            return counts
        connectors = await connectors_factory()
    except Exception:  # noqa: BLE001 - der Wächter soll weiterlaufen; der Vermerk bleibt für die nächste Runde
        log.exception("restore_followup_failed")
        return counts
    for key in records:
        try:
            result = await _follow_up_record(ctx, connectors, key, now)
        except Exception:  # noqa: BLE001 - der Vermerk bleibt für die nächste Runde
            log.exception("restore_followup_failed key=%r", key)
            await _expire_failing_record(ctx, key, now)
            continue
        if result:
            counts[result] = counts.get(result, 0) + 1
    return counts


SPEC = ActionSpec(
    action_type=ACTION_TYPE,
    label="Sicherung als Kopie wiederherstellen",
    description=(
        "Legt aus einer Proxmox-Sicherung einen NEUEN Gast an (neue Nummer, neue MAC-Adressen, Autostart aus, "
        "Netzwerkkarten auf Wunsch getrennt). Das Original bleibt unberührt."
    ),
    default_risk=Risk.HIGH,
    permissions=["hosts.execute"],
    host_bound=False,
)
"""Hohes Risiko: Es entsteht ein ganzer Gast mit eigenen Platten, der Lauf belastet Speicher und Netz lange, und die
Kopie trägt dieselbe Einrichtung wie das Original (IP, Dienste, Zugangsdaten). Freigeben dürfen das nur Rollen mit
`actions.approve:high` (Admin, Owner), nicht der Operator."""


def register(ctx: ExtensionContext, connectors_factory: ConnectorsFactory) -> None:
    """Aktion, Ausführer und Routen, alle mit `hosts.execute`: auch die Auswahl zeigt feste Adressen und Geräte aus der
    Gast-Konfiguration und ist nur für die gedacht, die vorschlagen dürfen."""
    ctx.actions.register(SPEC)
    ctx.capabilities.provide(RestoreCopyExecutor(ctx, connectors_factory))

    async def _connector(connection: str) -> ProxmoxBackupConnector:
        connector = (await connectors_factory()).get(connection)
        if connector is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{connection}' unbekannt.")
        return connector

    router = APIRouter()

    @router.get("/restore/backups")
    async def restore_backups(
        connection: str = Query(...), vmid: str = Query(...), kind: str | None = Query(None),
    ) -> dict[str, Any]:
        """Sicherungen eines Gastes für "Als Kopie wiederherstellen", neueste zuerst."""
        try:
            return await guest_backups(await _connector(connection), vmid, kind)
        except RestoreError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

    @router.get("/restore/options")
    async def restore_choices(connection: str = Query(...), volid: str = Query(...)) -> dict[str, Any]:
        """Knoten, passende Speicher mit Platz, freie Nummer und Name als Vorschlag (nur lesend)."""
        try:
            return await restore_options(await _connector(connection), connection, volid)
        except RestoreError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None

    @router.post("/restore")
    async def propose_restore(
        values: dict[str, Any] = Body(...), actor: Actor = Depends(ctx.api.current_actor),
    ) -> dict[str, Any]:
        """Prüft alles (wie beim Ausführen) und schlägt die Wiederherstellung dem Gate vor (hohes Risiko).
        Passt etwas nicht (Nummer vergeben, zu wenig Platz, falscher Speicher, fehlendes Recht), entsteht gar kein
        Vorschlag."""
        connection = str(values.get("connection") or "")
        try:
            plan = await check_restore(await _connector(connection), connection, values)
        except RestoreError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
        request = ActionRequest(
            action_type=ACTION_TYPE, payload=plan.payload(), risk=Risk.HIGH, proposed_by=actor,
            reason=plan.reason(), correlation_id=f"restore:{connection}/{plan.inp.vmid}",
        )
        decision = await ctx.actions.propose(request, wait_s=REQUEST_WAIT_S)
        return {
            "action_id": decision.action_id, "status": decision.status.value, "risk": request.risk.value,
            "warnings": plan.warnings,
        }

    ctx.api.include_router(router, permission="hosts.execute")
