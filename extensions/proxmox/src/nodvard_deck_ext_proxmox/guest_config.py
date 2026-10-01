"""Gast-Details: Kerne, RAM, Disks mit Speicherort, Netzwerk,
Gast-Agent -- das, wofuer man in Proxmox die "Hardware"-/"Ressourcen"-Reiter aufmacht.

Rein lesend. Bewusst eine WHITELIST: aus der Rohkonfiguration werden nur die hier
benannten Felder uebernommen. Cloud-Init-Passwort (`cipassword`), SSH-Schluessel
(`sshkeys`) und die frei beschreibbaren Notizen (`description`) verlassen Proxmox so
nie ueber Nodvard Deck.

Wo eine Gast-Disk liegt (z. B. unerwartet auf `local` statt `local-lvm`, ebenso EFI- und
Cloud-Init-Laufwerke), steht hier direkt am Gast statt nur in der Speicher-Uebersicht;
durchgereichte PCI-Geraete ebenfalls.
"""

from __future__ import annotations

import re
from typing import Any

from .connector import ProxmoxApiError

QEMU_DISK_RE = re.compile(r"^(scsi|virtio|sata|ide|efidisk|tpmstate)\d+$")
LXC_DISK_RE = re.compile(r"^(rootfs|mp\d+)$")
UNUSED_RE = re.compile(r"^unused\d+$")
NET_RE = re.compile(r"^net\d+$")
PASSTHROUGH_RE = re.compile(r"^(hostpci|usb)\d+$")
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")

OS_LABEL = {
    "l26": "Linux", "l24": "Linux (2.4)", "win11": "Windows 11", "win10": "Windows 10",
    "win8": "Windows 8", "win7": "Windows 7", "w2k8": "Windows Server 2008", "wxp": "Windows XP",
    "other": "Andere", "solaris": "Solaris",
}


def split_property(value: str) -> tuple[str | None, dict[str, str]]:
    """Proxmox-Eigenschaftsstring -> (fuehrender Wert ohne "=", Schluessel/Wert).
    "local-lvm:vm-100-disk-0,iothread=1,size=540G" -> ("local-lvm:vm-100-disk-0", {...})."""
    head: str | None = None
    options: dict[str, str] = {}
    for index, part in enumerate(str(value).split(",")):
        if "=" in part:
            key, _, val = part.partition("=")
            options[key.strip()] = val.strip()
        elif index == 0:
            head = part.strip()
    return head, options


def parse_disk(slot: str, value: str) -> dict[str, Any]:
    head, opts = split_property(value)
    volume = head or opts.get("file") or opts.get("volume") or ""
    storage, _, name = volume.partition(":") if ":" in volume else ("", "", volume)
    media = opts.get("media")
    kind = "disk"
    if media == "cdrom":
        kind = "cloudinit" if "cloudinit" in name else "cdrom"
    elif volume.startswith("/"):
        kind = "bind"  # LXC-Bind-Mount eines Host-Pfads, kein Proxmox-Speicher
    return {
        "slot": slot,
        "kind": kind,
        "storage": storage or None,
        "volume": name or volume or None,
        "size": opts.get("size"),
        "mountpoint": opts.get("mp"),
    }


def parse_net(slot: str, value: str, *, kind: str) -> dict[str, Any]:
    _, opts = split_property(value)
    if kind == "qemu":
        model, mac = next(((k, v) for k, v in opts.items() if MAC_RE.match(v)), (None, None))
        ip = None
    else:
        model, mac = opts.get("type"), opts.get("hwaddr")
        ip = opts.get("ip")
    return {
        "slot": slot,
        "name": opts.get("name"),
        "model": model,
        "mac": mac.upper() if mac else None,
        "bridge": opts.get("bridge"),
        "vlan": opts.get("tag"),
        "firewall": opts.get("firewall") == "1",
        "configured_ip": ip,
        "gateway": opts.get("gw"),
        "link_down": opts.get("link_down") == "1",
        "ips": [],
    }


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def agent_enabled(value: Any) -> bool:
    """`agent` ist "1", "0" oder "enabled=1,fstrim_cloned_disks=1" -- live beide
    Schreibweisen gesehen ("1" und "enabled=1")."""
    if value is None:
        return False
    head, opts = split_property(str(value))
    return head == "1" or opts.get("enabled") == "1"


def summarize_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    disk_re = QEMU_DISK_RE if kind == "qemu" else LXC_DISK_RE
    disks = [parse_disk(k, v) for k, v in config.items() if disk_re.match(k)]
    unused = [parse_disk(k, v) | {"kind": "unused"} for k, v in config.items() if UNUSED_RE.match(k)]
    nets = [parse_net(k, v, kind=kind) for k, v in config.items() if NET_RE.match(k)]
    slot_order = lambda d: (re.sub(r"\d+$", "", d["slot"]), _int(re.sub(r"^\D+", "", d["slot"])) or 0)  # noqa: E731
    startup = split_property(str(config.get("startup") or ""))[1]
    summary: dict[str, Any] = {
        "kind": kind,
        "name": config.get("name") or config.get("hostname"),
        "cores": _int(config.get("cores")) or 1,
        "sockets": _int(config.get("sockets")) or 1 if kind == "qemu" else None,
        "cpu_type": config.get("cpu") if kind == "qemu" else None,
        "memory_mb": _int(config.get("memory")),
        # qemu: Ballooning-Minimum (0 = aus); lxc: Swap.
        "balloon_mb": _int(config.get("balloon")) if kind == "qemu" else None,
        "swap_mb": _int(config.get("swap")) if kind == "lxc" else None,
        "os": OS_LABEL.get(str(config.get("ostype")), config.get("ostype")),
        "onboot": str(config.get("onboot")) == "1",
        "startup_order": _int(startup.get("order")),
        "disks": sorted(disks, key=slot_order) + sorted(unused, key=slot_order),
        "networks": sorted(nets, key=slot_order),
        "passthrough": [f"{k}: {split_property(v)[0] or v}" for k, v in sorted(config.items()) if PASSTHROUGH_RE.match(k)],
        "tags": [t for t in re.split(r"[;, ]+", str(config.get("tags") or "")) if t],
    }
    if kind == "qemu":
        summary.update({
            "agent_enabled": agent_enabled(config.get("agent")),
            "bios": "UEFI" if config.get("bios") == "ovmf" else "SeaBIOS",
            "machine": config.get("machine") or "i440fx",
        })
    else:
        summary.update({
            "unprivileged": str(config.get("unprivileged")) == "1",
            "features": [f for f, v in split_property(str(config.get("features") or ""))[1].items() if v == "1"],
        })
    return summary


def _attach_ips(networks: list[dict[str, Any]], interfaces: list[tuple[str | None, str | None, list[str]]]) -> list[str]:
    """Adressen den konfigurierten Netzwerkkarten ueber die MAC zuordnen. Rueckgabe:
    Adressen, die keiner Karte zuzuordnen waren (Docker-Bruecken im Gast o. ae.)."""
    by_mac = {n["mac"]: n for n in networks if n.get("mac")}
    leftover: list[str] = []
    for name, mac, ips in interfaces:
        target = by_mac.get((mac or "").upper())
        if target is not None:
            target["ips"].extend(ips)
        elif name not in (None, "lo"):
            leftover.extend(f"{ip} ({name})" for ip in ips)
    return leftover


async def collect_guest_details(connector: Any, kind: str, node: str, vmid: str) -> dict[str, Any]:
    config = await connector.guest_config(node, kind, vmid)
    details = summarize_config(kind, config)
    status = await (connector.qemu_status(node, vmid) if kind == "qemu" else connector.lxc_status(node, vmid))
    running = status.get("status") == "running"
    details["running"] = running
    details["other_ips"] = []
    interfaces: list[tuple[str | None, str | None, list[str]]] = []
    if kind == "qemu":
        details["agent_responding"] = None
        if running and details["agent_enabled"]:
            try:
                rows = await connector.qemu_agent_interfaces(node, vmid)
                details["agent_responding"] = True
                interfaces = [
                    (
                        r.get("name"),
                        r.get("hardware-address"),
                        [
                            str(a.get("ip-address")) + (f"/{a['prefix']}" if a.get("prefix") is not None else "")
                            for a in r.get("ip-addresses") or []
                            if a.get("ip-address-type") == "ipv4"
                        ],
                    )
                    for r in rows
                ]
            except ProxmoxApiError:
                details["agent_responding"] = False
    elif running:
        try:
            rows = await connector.lxc_interfaces(node, vmid)
            interfaces = [(r.get("name"), r.get("hwaddr"), [r["inet"]] if r.get("inet") else []) for r in rows]
        except ProxmoxApiError:
            pass
    details["other_ips"] = _attach_ips(details["networks"], interfaces)
    return details
