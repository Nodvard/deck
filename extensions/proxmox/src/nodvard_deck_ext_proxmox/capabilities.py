"""Capability-Protokolle der Proxmox-Erweiterung: Hosts, Messwerte, Aktionen und Konsole."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from nodvard_sdk.actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from nodvard_sdk.types import DiscoveredHost, Host as SdkHost, HostStatus, Risk

from .config import ConnectionMissing, build_connectors
from .connector import ProxmoxApiError, ProxmoxConnector
from .guest_edit import ConfigEditError, build_update, describe, pending_keys
from .transport import task_outcome

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext

_log = logging.getLogger("nodvard_deck.ext.proxmox")

_VM_ACTION_SPECS = [
    ActionSpec(
        action_type="vm.start", label="Starten", icon="play",
        default_risk=Risk.LOW, host_bound=True,
    ),
    # Herunterfahren (ACPI bzw. Gast-Agent) ist die normale Wahl; `vm.stop` schaltet hart aus und kann Daten kosten,
    # darum getrennte Aktionen mit eigener Risikostufe.
    ActionSpec(
        action_type="vm.shutdown", label="Herunterfahren", icon="power",
        description="Fährt das Betriebssystem im Gast (VM oder Container) geordnet herunter (wie \"Herunterfahren\" im Startmenü).",
        default_risk=Risk.MEDIUM, host_bound=True,
        confirm_text="Das Betriebssystem im Gast fährt geordnet herunter. Fortfahren?",
    ),
    ActionSpec(
        action_type="vm.stop", label="Stoppen (hart)", icon="square",
        default_risk=Risk.HIGH, host_bound=True,
        confirm_text="Hartes Stoppen kann nicht gespeicherte Daten in der VM verlieren. Fortfahren?",
    ),
    ActionSpec(
        action_type="vm.reboot", label="Neustarten", icon="rotate-cw",
        default_risk=Risk.HIGH, host_bound=True,
        confirm_text="Die VM wird neugestartet. Fortfahren?",
    ),
    ActionSpec(
        action_type="vm.snapshot", label="Snapshot erstellen", icon="camera",
        default_risk=Risk.MEDIUM, host_bound=True,
    ),
    ActionSpec(
        action_type="vm.snapshot_rollback", label="Snapshot zurückrollen", icon="history",
        default_risk=Risk.HIGH, host_bound=True,
        params_schema={"type": "object", "properties": {"snapname": {"type": "string"}}, "required": ["snapname"]},
        confirm_text="Der Gast wird gestoppt und auf den Stand des Snapshots zurückgesetzt -- alles danach geht verloren. Fortfahren?",
    ),
    ActionSpec(
        action_type="vm.snapshot_delete", label="Snapshot löschen", icon="trash-2",
        default_risk=Risk.MEDIUM, host_bound=True,
        params_schema={"type": "object", "properties": {"snapname": {"type": "string"}}, "required": ["snapname"]},
    ),
    # Nur die Positivliste aus guest_edit.py; `changes` ist ein Objekt, daher ein eigenes Formular auf der Proxmox-Seite.
    ActionSpec(
        action_type="vm.config_set", label="Hardware ändern", icon="sliders-horizontal",
        default_risk=Risk.MEDIUM, host_bound=True,
        params_schema={"type": "object", "properties": {"changes": {"type": "object"}}, "required": ["changes"]},
    ),
]

# Proxmox' Regel für Snapshot-Namen (pve-configid, höchstens 40 Zeichen). Der Name landet im URL-Pfad, alles andere
# (z. B. "../") wird vorher abgewiesen.
SNAPNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,39}$")
_VM_ACTION_TYPES = frozenset(spec.action_type for spec in _VM_ACTION_SPECS)

# Pakete einspielen kann die REST-API nicht (nur per Shell): hier nur Paketlisten aktualisieren und neu starten.
_NODE_ACTION_SPECS = [
    ActionSpec(
        action_type="node.apt_refresh", label="Paketlisten aktualisieren", icon="refresh-cw",
        description="Lädt die Paketlisten des Knotens neu (apt update), damit die Update-Anzeige stimmt.",
        default_risk=Risk.LOW, host_bound=True,
    ),
    ActionSpec(
        action_type="node.reboot", label="Knoten neu starten", icon="power",
        description="Startet den Proxmox-Knoten neu -- alle Gäste darauf fahren herunter.",
        default_risk=Risk.CRITICAL, host_bound=True,
        confirm_text="Alle VMs und Container auf diesem Knoten werden heruntergefahren und sind bis nach dem Neustart weg. Wirklich neu starten?",
    ),
]
_NODE_ACTION_TYPES = frozenset(spec.action_type for spec in _NODE_ACTION_SPECS)


# Gäste werden über (Knoten, VMID) adressiert, nie über die VMID allein. Der Verbindungsname vorn hält die Kennung
# eindeutig, weil mehrere Proxmox-Instanzen dieselbe VMID vergeben können.
def _parse_ref(provider_ref: str) -> tuple[str, str, str, str | None]:
    """"<connection>/node/<node>" -> (connection, "node", node, None);
    "<connection>/qemu|lxc/<node>/<vmid>" -> (connection, kind, node, vmid)."""
    parts = provider_ref.split("/")
    connection, kind, node = parts[0], parts[1], parts[2]
    if kind == "node":
        return connection, "node", node, None
    return connection, kind, node, parts[3]


def _power_state(guest: dict[str, Any]) -> str | None:
    """`running`, `stopped` oder `paused` aus der Gästeliste; eine pausierte VM meldet `status: running` und
    `qmpstatus: paused`. Andere Werte (`unknown`, fehlend) bleiben unbekannt."""
    status = guest.get("status")
    if status == "running" and guest.get("qmpstatus") == "paused":
        return "paused"
    return status if status in ("running", "stopped") else None


# Schnittstellen, über die man den Gast im Heimnetz nicht erreicht (Docker-Brücken, VPNs); nur für den Fall ohne
# Subnetz-Treffer.
_NOT_THE_LAN = ("lo", "docker", "br-", "veth", "virbr", "tailscale", "cni", "flannel", "wg", "zt")


def pick_guest_ipv4(candidates: list[tuple[str | None, str | None]], node_host: str | None) -> str | None:
    """Wählt aus den gemeldeten Adressen die im Netz des Proxmox-Knotens: gleiches /24, sonst die erste Adresse
    außerhalb von Brücken und VPNs, sonst keine."""
    parsed: list[tuple[str, ipaddress.IPv4Address]] = []
    for iface, addr in candidates:
        if not addr:
            continue
        try:
            ip = ipaddress.IPv4Address(addr.split("/", 1)[0])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local:
            continue
        parsed.append((iface or "", ip))
    try:
        node_net = ipaddress.IPv4Network(f"{node_host}/24", strict=False) if node_host else None
    except ValueError:
        node_net = None  # Knoten per DNS-Name konfiguriert
    if node_net is not None:
        for _iface, ip in parsed:
            if ip in node_net:
                return str(ip)
    for iface, ip in parsed:
        if not iface.startswith(_NOT_THE_LAN):
            return str(ip)
    return None


async def _guest_address(connector: ProxmoxConnector, kind: str, node: str, vmid: str) -> str | None:
    try:
        if kind == "lxc":
            rows = await connector.lxc_interfaces(node, vmid)
            candidates = [(r.get("name"), r.get("inet")) for r in rows]
        else:
            ifaces = await connector.qemu_agent_interfaces(node, vmid)
            candidates = [
                (iface.get("name"), a.get("ip-address"))
                for iface in ifaces
                for a in (iface.get("ip-addresses") or [])
                if a.get("ip-address-type") == "ipv4"
            ]
    except ProxmoxApiError:
        return None  # z. B. kein Gast-Agent -> Adresse bleibt, wie sie ist
    return pick_guest_ipv4(candidates, connector.host)


def os_family_from_ostype(ostype: str | None) -> str | None:
    """Proxmox' `ostype` (win11, win10, w2k8, wxp, l26 ...) -> `os_family` in Nodvard Deck.
    Unbekanntes (solaris, other) -> None: dann meldet die Discovery nichts."""
    value = str(ostype or "")
    if value.startswith("w"):
        return "windows"
    if value in ("l24", "l26"):
        return "linux"
    return None


async def _guest_os_family(connector: ProxmoxConnector, kind: str, node: str, vmid: str) -> str | None:
    if kind == "lxc":
        return "linux"  # Container teilen den Linux-Kernel des Knotens
    try:
        config = await connector.guest_config(node, "qemu", vmid)
    except ProxmoxApiError:
        return None
    return os_family_from_ostype(config.get("ostype"))


async def _connector_for(ctx: "ExtensionContext", connection: str):
    connectors = await build_connectors(ctx)
    connector = connectors.get(connection)
    if connector is None:
        raise ConnectionMissing(f"Proxmox-Verbindung '{connection}' ist nicht (mehr) konfiguriert.")
    return connector


# So lange muss eine Verbindung am Stück schweigen, bevor ihr Knoten „nicht erreichbar“ heißt: Der nächste geplante
# Abgleich (alle 5 Minuten) liegt darüber, zwei von Hand kurz hintereinander angestoßene nicht.
_DOWN_AFTER_S = 240.0
# Verbindung -> Beginn des ersten Abgleichs ohne Antwort seit der letzten Antwort. Nur im Speicher: Nach einem Neustart
# beginnt die Frist neu, ein schon nicht erreichbarer Knoten bleibt es trotzdem.
_no_answer_since: dict[str, float] = {}
# Verbindung -> Beginn des jüngsten Abgleichs mit Antwort oder Ablehnung. Abgleiche können parallel laufen (von Hand,
# nach einer Aktion); ein älterer, der erst danach fertig wird, kennt nur einen überholten Stand.
_answered_at: dict[str, float] = {}


def _now() -> float:
    return time.monotonic()


def _no_answer(exc: ProxmoxApiError) -> bool:
    """Kam gar keine Antwort (Netz, Zeitüberschreitung) oder nur ein Serverfehler? Eine Ablehnung (Anmeldung, Rechte,
    Zertifikat, Weiterleitung) zeigt dagegen, dass die Maschine läuft."""
    return exc.code is None and (exc.status_code is None or exc.status_code >= 500)


async def _none() -> None:
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")


def node_host_name(connection: str, node: str) -> str:
    """Kennung für einen neu eingelesenen Knoten, klein geschrieben und ohne Doppelung bei gleichem Namen
    (`pve-pve1` statt `pve-pve1-pve1`). Bereits eingelesene Hosts behalten ihre Kennung, der Kern ändert sie nicht."""
    conn, name = _slug(connection), _slug(node)
    return (f"pve-{conn}" if conn == name else f"pve-{conn}-{name}")[:64]


def guest_host_name(connection: str, short: str, vmid: str) -> str:
    return f"proxmox-{_slug(connection)}-{short}-{_slug(vmid)}"[:64]


def _legacy_node_host_name(connection: str, node: str) -> str:
    return f"pve-{connection}-{node}"


_POWER_ACTIONS = frozenset({"start", "stop", "shutdown", "reboot", "reset", "suspend", "resume"})

# Normale Wartezeit auf den Ausgang eines Proxmox-Tasks (`_wait_and_report`).
_TASK_WAIT_S = 30.0
# Geordnetes Herunterfahren dauert oft deutlich länger (Datenbanken schreiben noch, Windows installiert Updates). Dieselbe
# Zeit geht als `timeout` an Proxmox: ohne ihn bricht Proxmox nach etwa 60 s mit "got timeout" ab.
_SHUTDOWN_WAIT_S = 180.0
# Puffer obendrauf, damit der Ausgang bei Ablauf des Proxmox-Limits noch bei uns ankommt.
_SHUTDOWN_GRACE_S = 10.0


class ProxmoxHostProvider:
    """Erfüllt `nodvard_sdk.capabilities.HostProvider`."""

    provider_id = "proxmox"

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx
        self.covered: list[str] = []
        """Kennungs-Anfänge, die der letzte `discover_hosts()` vollständig gelesen hat (`<verbindung>/node/` und je
        Knoten `<verbindung>/qemu|lxc/<knoten>/`). Was darunter fehlt, gibt es nicht mehr; eine nicht erreichbare
        Verbindung oder ein Knoten, dessen Gäste nicht lesbar waren, steht nicht darin."""
        self.unreachable: dict[str, bool] = {}
        """Nicht lesbare Verbindungen: Name -> ob gar keine Antwort kam (`_no_answer`)."""
        self.unread: list[str] = []
        """Kennungs-Anfänge der Gäste, deren Liste nicht lesbar war: je Verbindung oder je Knoten."""
        self.connections: set[str] = set()
        """Verbindungen, die der letzte `discover_hosts()` abgefragt hat; deaktivierte und unvollständige fehlen."""

    async def discover_hosts(self) -> list[DiscoveredHost]:
        discovered: list[DiscoveredHost] = []
        self.covered, self.unreachable, self.unread = [], {}, []
        # Kennungen sind im Kern eindeutig. Wer schon eingelesen ist, behält seine (der Kern übernimmt `name` nur
        # beim Anlegen); für neue wird die einheitliche Form nur genommen, wenn sie noch frei ist, sonst die bisherige.
        known = await self._ctx.hosts.list()
        taken = {h.name for h in known}
        name_by_ref = {
            h.provider_ref: h.name for h in known if h.provider_ref and h.provider_ext_id == self._ctx.ext_id
        }

        def free_name(provider_ref: str, wanted: str, fallback: str) -> str:
            if provider_ref in name_by_ref:
                return name_by_ref[provider_ref]
            chosen = wanted if wanted not in taken else fallback
            # Sind beide Formen schon vergeben (z. B. Verbindung „a“ + Knoten „b-c“ neben „a-b“ + „c“),
            # hängt eine Zahl an: ein doppelter Name würde sonst den ganzen Abgleich abbrechen.
            n = 2
            while chosen in taken:
                suffix = f"-{n}"
                chosen = fallback[: 64 - len(suffix)] + suffix
                n += 1
            taken.add(chosen)
            return chosen

        connectors = await build_connectors(self._ctx)
        self.connections = set(connectors)
        for conn_name, connector in connectors.items():
            # Eine nicht erreichbare Verbindung überspringen, damit die anderen weiter eingelesen werden. Ihre bekannten
            # Hosts bleiben stehen, `sync()` setzt nur ihren Zustand.
            try:
                nodes = await connector.list_nodes()
            except ProxmoxApiError as exc:
                _log.warning("Proxmox-Discovery: Verbindung '%s' nicht erreichbar: %s", conn_name, exc)
                self.unreachable[conn_name] = _no_answer(exc)
                self.unread += [f"{conn_name}/qemu/", f"{conn_name}/lxc/"]
                continue
            self.covered.append(f"{conn_name}/node/")
            for node in nodes:
                node_name = node["node"]
                node_ref = f"{conn_name}/node/{node_name}"
                discovered.append(
                    DiscoveredHost(
                        provider_ref=node_ref,
                        name=free_name(node_ref, node_host_name(conn_name, node_name), _legacy_node_host_name(conn_name, node_name)),
                        display_name=f"Proxmox-Knoten {node_name}",
                        address=connector.host,
                        kind="hypervisor",
                        status=HostStatus.UP if node.get("status") == "online" else HostStatus.DOWN,
                        tags=["proxmox", "node"],
                        metadata={
                            "connection": conn_name, "node": node_name,
                            "cpu": node.get("cpu"), "maxmem": node.get("maxmem"),
                        },
                    )
                )
                # LXC liefert dieselben Felder wie QEMU (vmid/name/status/cpu/maxmem). Ein offline Knoten im Cluster
                # antwortet mit 595: dann nur seine Gäste auslassen.
                try:
                    guests: list[tuple[str, dict[str, Any]]] = [
                        *(("qemu", vm) for vm in await connector.list_qemu(node_name)),
                        *(("lxc", ct) for ct in await connector.list_lxc(node_name)),
                    ]
                except ProxmoxApiError as exc:
                    _log.warning("Proxmox-Discovery: Gäste von Knoten '%s' (%s) nicht lesbar: %s", node_name, conn_name, exc)
                    self.unread += [f"{conn_name}/qemu/{node_name}/", f"{conn_name}/lxc/{node_name}/"]
                    continue
                self.covered += [f"{conn_name}/qemu/{node_name}/", f"{conn_name}/lxc/{node_name}/"]
                # Echte Gast-Adressen nur für laufende Gäste, parallel, je Abfrage höchstens 5 s. Ohne Antwort bleibt die
                # Knoten-Adresse als Platzhalter, der eine vorhandene Adresse nie überschreibt (siehe `address_verified`).
                addresses = await asyncio.gather(*(
                    _guest_address(connector, kind, node_name, str(g["vmid"]))
                    if g.get("status") == "running" else _none()
                    for kind, g in guests
                ))
                os_families = await asyncio.gather(*(
                    _guest_os_family(connector, kind, node_name, str(g["vmid"])) for kind, g in guests
                ))
                for (kind, guest), address, os_family in zip(guests, addresses, os_families, strict=True):
                    vmid = str(guest["vmid"])
                    short = "vm" if kind == "qemu" else "lxc"
                    guest_ref = f"{conn_name}/{kind}/{node_name}/{vmid}"
                    discovered.append(
                        DiscoveredHost(
                            provider_ref=guest_ref,
                            name=free_name(guest_ref, guest_host_name(conn_name, short, vmid), f"proxmox-{conn_name}-{short}-{vmid}"),
                            display_name=guest.get("name") or f"{'VM' if kind == 'qemu' else 'LXC'} {vmid}",
                            address=address or connector.host,
                            address_verified=address is not None,
                            kind=short,
                            # Nur gesetzt, wenn bekannt -- der Kern übernimmt es sonst nicht.
                            **({"os_family": os_family} if os_family else {}),
                            status=HostStatus.UP if guest.get("status") == "running" else HostStatus.DOWN,
                            # Damit die Oberfläche „LXC auf <Knoten>“ zeigt und einen gestoppten Gast grau statt rot.
                            parent_ref=node_ref,
                            parent_label=node_name,
                            power_state=_power_state(guest),
                            tags=["proxmox", short],
                            metadata={
                                "connection": conn_name, "node": node_name, "vmid": vmid,
                                "cpu": guest.get("cpu"), "maxmem": guest.get("maxmem"),
                                "address_source": ("guest-agent" if kind == "qemu" else "container") if address else "placeholder",
                            },
                        )
                    )
        return discovered

    async def sync(self) -> int:
        """Abgleich in einem Schritt: einlesen, übernehmen, in vollständig gelesenen Bereichen Fehlende als nicht mehr
        vorhanden markieren und Hosts, die gerade nicht lesbar sind, als unbekannt oder nicht erreichbar führen; gibt
        die Zahl der übernommenen Hosts zurück."""
        started = _now()
        discovered = await self.discover_hosts()
        hosts = await self._ctx.hosts.upsert_discovered(discovered)
        seen = {d.provider_ref for d in discovered}
        unseen = [
            h for h in await self._ctx.hosts.list()
            if h.provider_ext_id == self._ctx.ext_id and h.provider_ref and h.provider_ref not in seen
        ]
        gone = [h.provider_ref for h in unseen if h.provider_ref.startswith(tuple(self.covered))]
        if gone:
            await self._ctx.hosts.mark_gone(gone)
        # Sonst behielten Knoten und Gäste einer ausgefallenen Maschine für immer ihren letzten Zustand „läuft“.
        down: list[str] = []
        unknown = [
            h.provider_ref for h in unseen
            if h.provider_ref.startswith(tuple(self.unread)) or h.provider_ref.split("/", 1)[0] not in self.connections
        ]
        # Es zählt nur, was die Abgleiche selbst erlebt haben: Eine Antwort oder eine Ablehnung beendet die Folge, ein
        # Zustand „unbekannt“ aus einem anderen Grund beginnt keine.
        for conn_name in self.connections:
            if not self.unreachable.get(conn_name):
                _answered_at[conn_name] = max(_answered_at.get(conn_name, started), started)
        for conn_name in list(_no_answer_since):
            if conn_name not in self.connections or _no_answer_since[conn_name] < _answered_at.get(conn_name, -math.inf):
                del _no_answer_since[conn_name]
        for conn_name, no_answer in self.unreachable.items():
            if started < _answered_at.get(conn_name, -math.inf):  # ein neuerer Abgleich hat schon eine Antwort
                unknown = [ref for ref in unknown if not ref.startswith(f"{conn_name}/")]
                continue
            nodes = [
                h for h in unseen if h.provider_ref.startswith(f"{conn_name}/node/") and not h.metadata.get("gone_since")
            ]
            silent_for = started - _no_answer_since.setdefault(conn_name, started) if no_answer else 0.0
            # Hinter einem Cluster ist offen, welcher Knoten fehlt, und ein kurzer Aussetzer kann ein Neustart sein.
            if no_answer and len(nodes) == 1 and (nodes[0].status == HostStatus.DOWN or silent_for >= _DOWN_AFTER_S):
                down.append(nodes[0].provider_ref)
            else:
                unknown += [h.provider_ref for h in nodes]
        if down:
            await self._ctx.hosts.set_status(down, HostStatus.DOWN)
        if unknown:
            await self._ctx.hosts.set_status(unknown, HostStatus.UNKNOWN)
        return len(hosts)

    async def host_status(self, provider_ref: str) -> HostStatus:
        connection, kind, node, vmid = _parse_ref(provider_ref)
        connector = await _connector_for(self._ctx, connection)
        if kind == "node":
            status = await connector.node_status(node)
            return HostStatus.UP if status else HostStatus.DOWN
        status = (
            await connector.lxc_status(node, vmid)  # type: ignore[arg-type]
            if kind == "lxc"
            else await connector.qemu_status(node, vmid)  # type: ignore[arg-type]
        )
        return HostStatus.UP if status.get("status") == "running" else HostStatus.DOWN

    async def host_actions(self, provider_ref: str) -> list[ActionSpec]:
        _connection, kind, _node, _vmid = _parse_ref(provider_ref)
        if kind == "node":
            return list(_NODE_ACTION_SPECS)
        return list(_VM_ACTION_SPECS)


class ProxmoxMetricsProvider:
    """Erfüllt `nodvard_sdk.capabilities.MetricsProvider`: eine Momentaufnahme, `sample()` fragt Proxmox bei jedem
    Aufruf frisch ab."""

    _METRIC_NAMES = ["cpu_percent", "mem_used_bytes", "mem_total_bytes", "uptime_s"]

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def sample(self, host: SdkHost) -> dict[str, float]:
        if not host.provider_ref:
            return {}
        connection, kind, node, vmid = _parse_ref(host.provider_ref)
        connector = await _connector_for(self._ctx, connection)
        if kind == "node":
            data = await connector.node_status(node)
        elif kind == "lxc":
            data = await connector.lxc_status(node, vmid)  # type: ignore[arg-type]
        else:
            data = await connector.qemu_status(node, vmid)  # type: ignore[arg-type]
        # Knoten melden den RAM als memory.used/total, Gäste als mem/maxmem.
        memory = data.get("memory") if isinstance(data.get("memory"), dict) else {}
        return {
            "cpu_percent": round(float(data.get("cpu") or 0.0) * 100, 1),
            "mem_used_bytes": float(data.get("mem") or memory.get("used") or 0),
            "mem_total_bytes": float(data.get("maxmem") or memory.get("total") or 0),
            "uptime_s": float(data.get("uptime") or 0),
        }

    async def metric_names(self) -> list[str]:
        return list(self._METRIC_NAMES)

    async def history(self, host: SdkHost, range_name: str) -> dict[str, Any]:
        """Verlauf aus Proxmox' eigenen RRD-Daten (dieselben Kurven wie in der Proxmox-Oberfläche);
        nichts wird in Nodvard Deck zwischengespeichert."""
        if not host.provider_ref:
            raise ProxmoxApiError("Host ohne Proxmox-Bezug.")
        connection, kind, node, vmid = _parse_ref(host.provider_ref)
        connector = await _connector_for(self._ctx, connection)
        rows = await connector.rrddata(node, kind, vmid, RRD_TIMEFRAME[range_name])
        return rrd_to_series(rows, RANGE_SECONDS[range_name], now=int(datetime.now(UTC).timestamp()))


RANGE_SECONDS = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
RRD_TIMEFRAME = {"1h": "hour", "6h": "day", "24h": "day", "7d": "week", "30d": "month"}


def _num(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def rrd_to_series(rows: list[dict[str, Any]], range_s: int, *, now: int) -> dict[str, Any]:
    """RRD-Zeilen -> gemeinsame Zeitachse plus eine Werteliste je Metrik, mit denselben Schlüsseln wie der SSH-Anbieter.
    Fehlende Werte (NaN bei Proxmox, im JSON weggelassen) bleiben `None`: eine Lücke, keine erfundene Null."""
    since = now - range_s
    points = sorted((r for r in rows if isinstance(r.get("time"), (int, float)) and r["time"] >= since), key=lambda r: r["time"])
    timestamps = [int(r["time"]) for r in points]
    step = timestamps[1] - timestamps[0] if len(timestamps) > 1 else 60

    def pct(*keys: str):  # noqa: ANN202
        return [None if (v := _num(r, *keys)) is None else round(v * 100, 2) for r in points]

    def raw(*keys: str):  # noqa: ANN202
        return [_num(r, *keys) for r in points]

    series: dict[str, list[float | None]] = {
        "cpu_percent": pct("cpu"),
        "cpu_iowait_percent": pct("iowait"),
        "load_1": raw("loadavg"),
        # Knoten melden den RAM als memused/memtotal, Gäste als mem/maxmem.
        "mem_used_bytes": raw("memused", "mem"),
        "mem_total_bytes": raw("memtotal", "maxmem"),
        "swap_used_bytes": raw("swapused"),
        "swap_total_bytes": raw("swaptotal"),
        "net_in_bps": raw("netin"),
        "net_out_bps": raw("netout"),
        "disk_read_bps": raw("diskread"),
        "disk_write_bps": raw("diskwrite"),
        "root_used_bytes": raw("rootused"),
        "root_total_bytes": raw("roottotal"),
    }
    # Nur Metriken, die dieser Host überhaupt liefert (ein Gast hat kein iowait/load).
    series = {k: v for k, v in series.items() if any(x is not None for x in v)}
    return {"step_s": step, "timestamps": timestamps, "series": series, "max": {}}


class ProxmoxActionExecutor:
    """Erfüllt `nodvard_sdk.capabilities.ActionExecutor`; wird nur vom Gate aufgerufen (`core.gate.execute_action()`),
    nie direkt von dieser Erweiterung."""

    action_types = _VM_ACTION_TYPES | _NODE_ACTION_TYPES

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def execute(self, req: ActionRequest) -> ActionResult:
        if not req.host_ref:
            return ActionResult(success=False, error="Aktion ohne Host-Referenz.")
        host = await self._ctx.hosts.get(req.host_ref)
        if host is None or not host.provider_ref:
            return ActionResult(success=False, error="Host nicht gefunden oder nicht von proxmox verwaltet.")

        connection, kind, node, vmid = _parse_ref(host.provider_ref)
        if req.action_type in _NODE_ACTION_TYPES:
            if kind != "node":
                return ActionResult(success=False, error="Diese Aktion gibt es nur für Proxmox-Knoten.")
            try:
                connector = await _connector_for(self._ctx, connection)
                if req.action_type == "node.apt_refresh":
                    task_id = await connector.apt_refresh(node)
                    return await self._wait_and_report(connector, node, task_id, "Paketlisten aktualisieren")
                await connector.node_reboot(node)
                return ActionResult(success=True, output=f"Neustart von '{node}' angefordert -- der Knoten ist gleich für einige Minuten weg.")
            except (ProxmoxApiError, RuntimeError) as exc:
                return ActionResult(success=False, error=str(exc))
        if kind not in ("qemu", "lxc") or vmid is None:
            return ActionResult(success=False, error="Diese Aktion ist nur für VMs/Container verfügbar, nicht für Knoten.")

        action = req.action_type.split(".", 1)[1]
        if action == "config_set":
            return await self._config_set(connection, kind, node, vmid, req.payload.get("changes"))
        if action in ("snapshot_rollback", "snapshot_delete"):
            snapname = str(req.payload.get("snapname") or "")
            if not SNAPNAME_RE.match(snapname):
                return ActionResult(success=False, error=f"Ungültiger Snapshot-Name: {snapname!r}")
        try:
            connector = await _connector_for(self._ctx, connection)
            if action == "snapshot_rollback":
                task_id = await connector.snapshot_rollback(node, kind, vmid, snapname)
                return await self._wait_and_report(connector, node, task_id, f"Zurückrollen auf '{snapname}'")
            if action == "snapshot_delete":
                task_id = await connector.snapshot_delete(node, kind, vmid, snapname)
                return await self._wait_and_report(connector, node, task_id, f"Löschen von Snapshot '{snapname}'")
            action_fn = connector.lxc_action if kind == "lxc" else connector.qemu_action
            snapshot_fn = connector.lxc_snapshot if kind == "lxc" else connector.qemu_snapshot
            if action == "snapshot":
                snapname = req.payload.get("snapname") or f"nodvard-{datetime.now(UTC):%Y%m%d-%H%M%S}"
                task_id = await snapshot_fn(node, vmid, str(snapname))
                label = f"Snapshot '{snapname}'"
            else:
                params = {"timeout": int(_SHUTDOWN_WAIT_S)} if action == "shutdown" else None
                task_id = await action_fn(node, vmid, action, params)
                label = f"'{action}'"
            if action == "shutdown":
                result = await self._wait_and_report(
                    connector, node, task_id, "Herunterfahren", timeout_s=_SHUTDOWN_WAIT_S + _SHUTDOWN_GRACE_S,
                    running_note="der Gast ist noch nicht aus",
                )
                if not result.success and "got timeout" in (result.error or ""):
                    # Proxmox hat das Zeitlimit erreicht: der Gast reagiert nicht (mehr) auf das Signal.
                    result = ActionResult(
                        success=False,
                        error=f"Herunterfahren fehlgeschlagen: der Gast ist nach {_SHUTDOWN_WAIT_S:g}s noch nicht aus "
                        f"(Proxmox: {result.error.split(': ', 1)[-1]}). Notfalls hart ausschalten.",
                        detail=result.detail,
                    )
            else:
                result = await self._wait_and_report(connector, node, task_id, label)
            if action in _POWER_ACTIONS:
                # Sonst zeigt die Liste bis zur nächsten Discovery (alle 5 min) den alten Zustand.
                await self._refresh_hosts()
            return result
        except (ProxmoxApiError, RuntimeError) as exc:
            return ActionResult(success=False, error=str(exc))

    async def _refresh_hosts(self) -> None:
        try:
            await ProxmoxHostProvider(self._ctx).sync()
        except Exception:  # noqa: BLE001 - die Aktion selbst war erfolgreich; der Status holt die Discovery nach
            _log.warning("Proxmox: Host-Status nach der Aktion nicht aufgefrischt", exc_info=True)

    async def _config_set(self, connection: str, kind: str, node: str, vmid: str, changes: Any) -> ActionResult:
        """Liest die Konfiguration (für `digest` und Ausgangswerte), prüft gegen die Positivliste, schreibt und meldet,
        was erst nach einem Neustart greift."""
        try:
            connector = await _connector_for(self._ctx, connection)
            config = await connector.guest_config(node, kind, vmid)
            params, diff = build_update(kind, config, changes)
            await connector.update_guest_config(node, kind, vmid, params)
            try:
                pending = pending_keys(await connector.guest_pending(node, kind, vmid))
            except ProxmoxApiError:
                pending = []  # Änderung ist durch, nur die Neustart-Auskunft fehlt
        except ConfigEditError as exc:
            return ActionResult(success=False, error=str(exc))
        except (ProxmoxApiError, RuntimeError) as exc:
            return ActionResult(success=False, error=str(exc))
        return ActionResult(success=True, output=describe(diff, pending), detail={"changes": diff, "pending": pending})

    async def _wait_and_report(
        self, connector: ProxmoxConnector, node: str, task_id: str, label: str,
        *, timeout_s: float = _TASK_WAIT_S, running_note: str | None = None,
    ) -> ActionResult:
        """Wartet auf den Ausgang des Tasks, nicht nur auf die Annahme (UPID). Länger laufende Tasks melden sich als
        "noch laufend", nicht als Fehlschlag, und werden nicht abgebrochen. `running_note` sagt, was dann noch offen ist."""
        status = await connector.wait_for_task(node, task_id, timeout_s=timeout_s)
        if status.get("status") == "running":
            note = f" ({running_note})" if running_note else ""
            return ActionResult(
                success=True, output=f"{label} angefordert, läuft nach {timeout_s:g}s noch{note}.",
                detail={"task_id": task_id, "task_status": "running"},
            )
        exitstatus = status.get("exitstatus")
        if exitstatus == "OK":
            return ActionResult(
                success=True, output=f"{label} abgeschlossen.",
                detail={"task_id": task_id, "task_status": "OK"},
            )
        outcome = task_outcome(exitstatus)
        return ActionResult(
            success=False, error=f"{label} fehlgeschlagen: {outcome or 'unbekannter Ausgang'}",
            detail={"task_id": task_id, "task_status": outcome},
        )

    async def dry_run(self, req: ActionRequest) -> DryRunReport | None:
        return None


class ProxmoxConsoleTarget:
    """Erfüllt `nodvard_sdk.capabilities.ConsoleTarget`: Bildschirm einer VM oder eines Containers über Proxmox'
    vncproxy/vncwebsocket. Der Kern prüft den Nutzer selbst, der Browser braucht weder Proxmox-Login noch API-Token.
    Knoten haben keine Konsole: ein Hypervisor-Bildschirm reicht weiter als eine einzelne VM."""

    def __init__(self, ctx: "ExtensionContext") -> None:
        self._ctx = ctx

    async def console_available(self, host: SdkHost) -> bool:
        if host.provider_ext_id != self._ctx.ext_id or not host.provider_ref:
            return False
        try:
            _connection, kind, _node, vmid = _parse_ref(host.provider_ref)
        except IndexError:
            return False
        return kind in ("qemu", "lxc") and vmid is not None

    async def open_console(self, host: SdkHost):  # noqa: ANN201 - ProxmoxVncSession
        if not await self.console_available(host):
            raise RuntimeError("Konsole gibt es nur für VMs und Container, nicht für Knoten.")
        connection, kind, node, vmid = _parse_ref(host.provider_ref)  # type: ignore[arg-type]
        connector = await _connector_for(self._ctx, connection)
        return await connector.open_vnc(node, kind, vmid)  # type: ignore[arg-type]
