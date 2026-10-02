"""proxmox-Extension -- die erste echte Vendor-Extension.
Entdeckt Proxmox-VE-Knoten und ihre QEMU-VMs als Hosts, liefert Live-Metriken
und Start/Stop/Neustart/Snapshot ueber das Aktions-Gate.

**Die Nagelprobe fuer core_purity (siehe check_core_purity.py):** diese Datei ist die
EINZIGE Stelle im gesamten Repository ausserhalb von `extensions/proxmox/`, an der das
Wort "Proxmox" vorkommen darf. Alles, was der Kern sieht, sind die drei generischen
Capability-Protokolle aus `nodvard_sdk.capabilities` (HostProvider, MetricsProvider,
ActionExecutor) -- registriert wie jede andere Capability auch (siehe hello-world/
ntfy), nicht anders behandelt.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, status
from nodvard_sdk import (
    HostToolSpec,
    Badge,
    ExtensionContext,
    GaugeView,
    GridSize,
    HealthReport,
    NodvardExtension,
    ListItem,
    ListView,
    PageSpec,
    Refresh,
    StatusGridView,
    WidgetSpec,
    same_target,
)
from pydantic import BaseModel

from .capabilities import (
    ProxmoxActionExecutor,
    ProxmoxConsoleTarget,
    ProxmoxHostProvider,
    ProxmoxMetricsProvider,
    _parse_ref,
)
from .config import TOKEN_LABEL, build_connector, build_connectors
from .connector import ProxmoxApiError
from .guest_config import collect_guest_details
from .node_health import collect_disks, collect_node_health
from .storage import collect_storage, format_bytes, percent_badge, shared_label
from .tasks import collect_tasks
from .updates import collect_updates
from .watch import run_watch


_KIND_LABEL = {"vm": "VM", "lxc": "LXC", "hypervisor": "Knoten"}
"""Anzeige fuer den Host-Typ in der Proxmox-Uebersicht -- "hypervisor" ist ein
interner Wert, auf der Proxmox-Seite heisst dasselbe schon "Knoten"."""

class _DiscoveryJobSpec:
    """Erfuellt `nodvard_sdk.context.JobSpec` strukturell (wie hello-worlds
    `_HelloJobSpec`) -- der Kern-Scheduler fuehrt `handler` bei Faelligkeit UND per
    manuellem `POST /jobs/{id}/run` aus. `ctx.hosts.upsert_discovered()` ist der
    bereits im Kern bestehende Abgleichsweg (ext/context.py `HostsHandle`) -- diese
    Extension ruft ihn nur mit ihren eigenen Funden auf."""

    id = "discovery"
    name = "Proxmox-Abgleich"
    schedule = "*/5 * * * *"
    params: dict[str, Any] = {}
    enabled = True

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def handler(self, **_: Any) -> dict[str, int]:
        provider = ProxmoxHostProvider(self._ctx)
        discovered = await provider.discover_hosts()
        hosts = await self._ctx.hosts.upsert_discovered(discovered)
        return {"discovered": len(hosts)}


class _WatchJobSpec:
    """Zustandswaechter (watch.py): alle 5 Minuten, meldet nur Zustandswechsel."""

    id = "watch"
    name = "Proxmox-Zustandswächter"
    schedule = "*/5 * * * *"
    params: dict[str, Any] = {}
    enabled = True

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def handler(self, **_: Any) -> dict[str, int]:
        return await run_watch(self._ctx)


class _TokenIn(BaseModel):
    value: str


class _ConnectionIn(BaseModel):
    """Wie `settings.connections`s Schema (`ctx.settings.declare()` unten) -- ohne
    das Token selbst, das bleibt ausschliesslich ueber `POST .../{name}/token` im
    Vault, nie hier im Klartext."""

    name: str
    base_url: str
    token_id: str
    tls_insecure_skip_verify: bool = False
    enabled: bool = True


class _ConnectionPatch(BaseModel):
    """Alle Felder optional -- ein `PUT` aendert nur, was mitgeschickt wird
    (insbesondere `enabled` alleine fuer das unabhaengige Aktivieren/Deaktivieren)."""

    base_url: str | None = None
    token_id: str | None = None
    tls_insecure_skip_verify: bool | None = None
    enabled: bool | None = None


class _ConnectionOut(BaseModel):
    name: str
    base_url: str
    token_id: str
    tls_insecure_skip_verify: bool
    enabled: bool
    has_token: bool


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

        ctx.capabilities.provide(ProxmoxHostProvider(ctx))
        ctx.capabilities.provide(ProxmoxMetricsProvider(ctx))
        ctx.capabilities.provide(ProxmoxActionExecutor(ctx))
        ctx.capabilities.provide(ProxmoxConsoleTarget(ctx))

        ctx.settings.declare(
            {
                "type": "object",
                "properties": {
                    "connections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "base_url": {"type": "string"},
                                "token_id": {"type": "string"},
                                "tls_insecure_skip_verify": {"type": "boolean"},
                                "enabled": {"type": "boolean"},
                            },
                            "required": ["name", "base_url", "token_id"],
                        },
                    },
                },
                "required": ["connections"],
            }
        )

        router = APIRouter()

        @router.post("/connections/{name}/token", status_code=status.HTTP_204_NO_CONTENT)
        async def set_token(name: str, payload: _TokenIn) -> None:
            """Wie ntfys `/token` (WP-6): das Token-GEHEIMNIS liegt im Vault, nie in
            den (Klartext-)Einstellungen. Ein Label pro Verbindung (Multi-Instanz-
            Nachtrag), damit pve1 und pve2 unabhaengig eigene Tokens haben. Kein
            Rotations-Pfad in dieser Runde -- ein bestehendes Token muss erst ueber
            `DELETE /api/v1/secrets/{id}` entfernt werden."""
            label = f"{TOKEN_LABEL}:{name}"
            if await ctx.secrets.exists(label):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Ein Token für '{name}' existiert bereits -- zuerst über DELETE /api/v1/secrets/{{id}} entfernen.",
                )
            await ctx.secrets.create(label=label, kind="generic", value=payload.value)

        async def _connection_out(conn: dict[str, Any]) -> _ConnectionOut:
            name = str(conn.get("name") or "")
            return _ConnectionOut(
                name=name,
                base_url=str(conn.get("base_url") or ""),
                token_id=str(conn.get("token_id") or ""),
                tls_insecure_skip_verify=bool(conn.get("tls_insecure_skip_verify") or False),
                enabled=conn.get("enabled") is not False,
                has_token=await ctx.secrets.exists(f"{TOKEN_LABEL}:{name}") if name else False,
            )

        @router.get("/connections")
        async def list_connections() -> list[_ConnectionOut]:
            """Verbindungen fuer die Oberflaeche auflisten.
            Liefert nie den Token-WERT (der steht nie in `settings`, siehe oben) --
            nur, OB eines gesetzt ist."""
            settings = await ctx.settings.get()
            connections: list[dict[str, Any]] = settings.get("connections") or []
            return [await _connection_out(c) for c in connections]

        @router.post("/connections", status_code=status.HTTP_201_CREATED)
        async def add_connection(payload: _ConnectionIn) -> _ConnectionOut:
            settings = await ctx.settings.get()
            connections: list[dict[str, Any]] = settings.get("connections") or []
            if any(c.get("name") == payload.name for c in connections):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Verbindung '{payload.name}' existiert bereits.",
                )
            # Eine neue Verbindung startet ohne Token: ein altes unter demselben Namen (etwa von
            # einer frueher entfernten Verbindung) gehoerte zu einer anderen Adresse.
            await ctx.secrets.delete(f"proxmox-token:{payload.name}")
            connections.append(payload.model_dump())
            settings["connections"] = connections
            await ctx.settings.set(settings)
            return await _connection_out(payload.model_dump())

        @router.put("/connections/{name}")
        async def update_connection(name: str, payload: _ConnectionPatch) -> _ConnectionOut:
            settings = await ctx.settings.get()
            connections: list[dict[str, Any]] = settings.get("connections") or []
            for conn in connections:
                if conn.get("name") != name:
                    continue
                updates = payload.model_dump(exclude_unset=True)
                old_url = conn.get("base_url")
                conn.update(updates)
                if "base_url" in updates and not same_target(old_url, conn.get("base_url")):
                    # Das Token gehoert zur alten Adresse -- sonst ginge es an die neue.
                    await ctx.secrets.delete(f"proxmox-token:{name}")
                settings["connections"] = connections
                await ctx.settings.set(settings)
                return await _connection_out(conn)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{name}' existiert nicht.")

        @router.delete("/connections/{name}", status_code=status.HTTP_204_NO_CONTENT)
        async def remove_connection(name: str) -> None:
            """Entfernt die Verbindung samt ihrem Token im Tresor -- sonst bekaeme eine spaeter
            neu angelegte Verbindung mit demselben Namen das alte Token."""
            settings = await ctx.settings.get()
            connections: list[dict[str, Any]] = settings.get("connections") or []
            filtered = [c for c in connections if c.get("name") != name]
            if len(filtered) == len(connections):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{name}' existiert nicht.")
            settings["connections"] = filtered
            await ctx.settings.set(settings)
            await ctx.secrets.delete(f"proxmox-token:{name}")

        ctx.api.include_router(router, permission="secrets.write")

        data_router = APIRouter()

        @data_router.get("/widgets/overview")
        async def overview_widget_data() -> dict:
            hosts = await ctx.hosts.list(tag="proxmox")
            return {
                "data": [
                    {
                        "name": h.display_name,
                        "status": h.status.value,
                        "kind": h.kind or "",
                        "kind_label": _KIND_LABEL.get(h.kind or "", h.kind or ""),
                        # Multi-Instanz-Nachtrag: ohne dieses Feld waeren pve1 und
                        # pve2-Kacheln in derselben Liste nicht unterscheidbar --
                        # discover_hosts() schreibt den Verbindungsnamen mit in
                        # host_metadata["connection"] (siehe capabilities.py).
                        "connection": (h.metadata or {}).get("connection", ""),
                    }
                    for h in hosts
                ],
                "meta": {},
            }

        @data_router.get("/guests/{host_id}/snapshots")
        async def guest_snapshots(host_id: str) -> list[dict]:
            """Snapshots eines Gasts, neueste zuerst -- ohne Proxmox' Pseudo-Eintrag
            "current". Zurueckrollen/Loeschen laufen als Aktionen ueber das Gate
            (`vm.snapshot_rollback`/`vm.snapshot_delete`), nicht hier."""
            host = await ctx.hosts.get(host_id)
            if host is None or host.provider_ext_id != ctx.ext_id or not host.provider_ref:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Gast.")
            connection, kind, node, vmid = _parse_ref(host.provider_ref)
            if kind not in ("qemu", "lxc") or vmid is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knoten haben keine Snapshots.")
            try:
                connector = await build_connector(ctx, connection)
                rows = await connector.list_snapshots(node, kind, vmid)
            except (RuntimeError, ProxmoxApiError) as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc) or type(exc).__name__) from exc
            snaps = [
                {
                    "name": r.get("name"),
                    "description": (r.get("description") or "").strip(),
                    "snaptime": r.get("snaptime"),
                    "parent": r.get("parent"),
                    "with_ram": bool(r.get("vmstate")),
                }
                for r in rows
                if r.get("name") != "current"
            ]
            snaps.sort(key=lambda r: r.get("snaptime") or 0, reverse=True)
            return snaps

        @data_router.get("/guests/{host_id}/details")
        async def guest_details(host_id: str) -> dict:
            """Kerne, RAM, Disks mit Speicherort, Netzwerk (mit Adressen je Karte),
            Gast-Agent -- guest_config.py. Nur Whitelist-Felder, keine Rohkonfiguration."""
            host = await ctx.hosts.get(host_id)
            if host is None or host.provider_ext_id != ctx.ext_id or not host.provider_ref:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Gast.")
            connection, kind, node, vmid = _parse_ref(host.provider_ref)
            if kind not in ("qemu", "lxc") or vmid is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nur fuer VMs und Container.")
            try:
                connector = await build_connector(ctx, connection)
                return await collect_guest_details(connector, kind, node, vmid)
            except (RuntimeError, ProxmoxApiError) as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc) or type(exc).__name__) from exc

        @data_router.get("/nodes/{host_id}/health")
        async def node_health(host_id: str) -> dict:
            """Prozessor, Last, RAM/Swap, Systemplatte und Datentraeger mit SMART-Zustand
            eines Knotens (node_health.py). Rein lesend."""
            host = await ctx.hosts.get(host_id)
            if host is None or host.provider_ext_id != ctx.ext_id or not host.provider_ref:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Knoten.")
            connection, kind, node, _vmid = _parse_ref(host.provider_ref)
            if kind != "node":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nur fuer Proxmox-Knoten.")
            try:
                connector = await build_connector(ctx, connection)
                return await collect_node_health(connector, connection, node)
            except (RuntimeError, ProxmoxApiError) as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc) or type(exc).__name__) from exc

        @data_router.get("/widgets/disks")
        async def disks_widget_data() -> dict:
            """Alle Datentraeger aller Knoten, schlechtester Zustand zuerst. Ein nicht
            erreichbarer Knoten wird als eigene Zeile gemeldet, nicht verschwiegen."""
            rows: list[dict] = []
            for conn_name, connector in (await build_connectors(ctx)).items():
                try:
                    nodes = await connector.list_nodes()
                except ProxmoxApiError as exc:
                    rows.append({"node": conn_name, "model": "Verbindung", "summary": str(exc), "badge": "nicht erreichbar", "tone": "danger"})
                    continue
                for entry in nodes:
                    node_name = entry.get("node")
                    if not node_name:
                        continue
                    if entry.get("status") not in (None, "online"):
                        rows.append({"node": node_name, "model": "Knoten", "summary": "offline", "badge": "offline", "tone": "danger"})
                        continue
                    try:
                        rows.extend(await collect_disks(connector, conn_name, node_name))
                    except ProxmoxApiError as exc:
                        rows.append({"node": node_name, "model": "Datenträger", "summary": str(exc), "badge": "nicht abrufbar", "tone": "neutral"})
            order = {"danger": 0, "warn": 1, "neutral": 2, "good": 3}
            rows.sort(key=lambda r: (order.get(r["tone"], 2), r["node"]))
            return {"data": rows, "meta": {}}

        @data_router.get("/tasks")
        async def task_history(limit: int = 50, include_console: bool = False) -> dict:
            """Aufgabenverlauf aller Knoten (tasks.py) -- wer hat was wann getan, hat es
            geklappt. Konsolen-Oeffnungen standardmaessig ausgeblendet."""
            return await collect_tasks(ctx, limit=max(1, min(limit, 200)), include_console=include_console)

        @data_router.get("/tasks/{connection}/{node}/log")
        async def task_log(connection: str, node: str, upid: str) -> dict:
            """Protokoll eines einzelnen Tasks. `upid` als Query-Parameter (enthaelt
            Doppelpunkte); er muss zu `node` gehoeren -- Proxmox kodiert den Knoten in
            der UPID selbst ("UPID:<node>:...")."""
            if not upid.startswith(f"UPID:{node}:") or "/" in upid:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="UPID passt nicht zum Knoten.")
            try:
                connector = await build_connector(ctx, connection)
                rows = await connector.task_log(node, upid)
            except (RuntimeError, ProxmoxApiError) as exc:
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc) or type(exc).__name__) from exc
            return {"lines": [str(r.get("t", "")) for r in sorted(rows, key=lambda r: r.get("n", 0))]}

        @data_router.get("/updates")
        async def updates_overview() -> dict:
            """Wartende Paket-Updates je Knoten mit Paketliste (updates.py). Rein lesend."""
            return await collect_updates(ctx, details=True)

        @data_router.get("/widgets/updates")
        async def updates_widget_data() -> dict:
            """Eine nicht erreichbare Verbindung als eigene rote Zeile --
            vorher stand sie nur in `meta`, das keine Kachel liest; pve2 fehlte still."""
            overview = await collect_updates(ctx, details=False)
            rows = [
                {"connection": e["connection"], "node": e["connection"], "summary": e["error"], "badge": "nicht erreichbar", "tone": "danger"}
                for e in overview["errors"]
            ]
            return {"data": rows + overview["nodes"], "meta": {"errors": overview["errors"]}}

        @data_router.get("/storage")
        async def storage_overview() -> dict:
            """Speicher-Uebersicht fuer die Seite: Belegung je Pool UND welche Gast-Disks
            darauf liegen (storage.py)."""
            return await collect_storage(ctx, details=True)

        @data_router.get("/widgets/storage")
        async def storage_widget_data() -> dict:
            """Nur Belegung (eine Abfrage je Knoten) -- das Widget laedt alle 60s neu;
            der Pool-Inhalt waere dafuer zu teuer."""
            overview = await collect_storage(ctx, details=False, merge_connections=True)
            # Wie bei den Updates: Fehler als eigene rote Zeile. Texte kommen
            # fertig aus dem Backend, damit die Fehlerzeile keine leeren Zahlen zeigt.
            rows: list[dict] = [
                {
                    "connection": e["connection"],
                    "storage": e.get("node") or e["connection"],
                    "summary": f"{e['connection']} · {e['error']}" if e.get("node") else e["error"],
                    "badge": "nicht abrufbar" if e.get("node") else "nicht erreichbar",
                    "tone": "danger",
                }
                for e in overview["errors"]
            ]
            rows += [
                {
                    **pool,
                    "summary": (
                        f"{shared_label(pool['nodes'])} · " if pool["shared"] else f"{pool['connection']} · "
                    ) + f"{format_bytes(pool['used'])} von {format_bytes(pool['total'])}",
                    "badge": percent_badge(pool["used_percent"]),
                }
                for pool in overview["pools"]
            ]
            return {"data": rows, "meta": {"errors": overview["errors"]}}

        @data_router.get("/widgets/node-load")
        async def node_load_widget_data() -> dict:
            """Vorher `nodes[0]` -- bei mehreren Proxmox-Instanzen
            (pve1/pve2, siehe capabilities.py-Nachtrag) war das eine willkuerliche,
            als "erster Knoten" nur ehrlich gelabelte, aber nicht wirklich
            aussagekraeftige Einzelmessung. Jetzt: Durchschnitt ueber ALLE bekannten
            Knoten -- fehlerhafte/nicht erreichbare Knoten werden einzeln uebersprungen
            (`return_exceptions=True`), nicht die ganze Kachel leer geraeumt, solange
            mindestens ein Knoten antwortet."""
            nodes = await ctx.hosts.list(tag="node")
            if not nodes:
                return {"data": {"value": None, "node_count": 0}, "meta": {}}
            metrics = ProxmoxMetricsProvider(ctx)
            samples = await asyncio.gather(
                *(metrics.sample(node) for node in nodes), return_exceptions=True
            )
            cpu_values = [
                s.get("cpu_percent") for s in samples
                if not isinstance(s, BaseException) and s.get("cpu_percent") is not None
            ]
            if not cpu_values:
                return {"data": {"value": None, "node_count": len(nodes)}, "meta": {}}
            average = sum(cpu_values) / len(cpu_values)
            return {"data": {"value": round(average, 1), "node_count": len(cpu_values)}, "meta": {}}

        # Sicherheits-Nachtrag: dieser
        # Router hing OHNE Berechtigung (`include_router(router)`) -- laut ApiHandle-
        # Docstring heisst das: oeffentlich, ohne Login erreichbar. Lesen braucht jetzt
        # `hosts.read`, alles Ausloesende `hosts.execute`.
        ctx.api.include_router(data_router, permission="hosts.read")

        ctx.ui.register_page(
            PageSpec(
                id="nodes",
                path="/nodes",
                title="Proxmox",
                icon="server",
                nav_section="Infrastruktur",
                nav_order=10,
                component="ProxmoxNodePage",
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="overview",
                title="Proxmox-Übersicht",
                icon="server",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=30),
                data_endpoint="widgets/overview",
                view=StatusGridView(
                    tile_title="{{ name }}",
                    tile_subtitle="{{ kind_label }} · {{ connection }}",
                    tile_tone="{{ status | tone }}",
                ),
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="node-load",
                title="Proxmox-Knoten-Auslastung",
                icon="cpu",
                # Live gefunden (Dashboard-Boot-Test, echter Browser): GaugeView.tsx
                # rendert ein 80x80px-SVG PLUS Text nebeneinander (flex, gap-4) -- bei
                # GridSize(w=1, h=1) (~89x90px Karte minus Padding/Titel) blieb dafuer
                # nie genug Platz, die Karte wirkte leer/abgeschnitten. w=2/h=2 entspricht
                # der Groesse, die jedes andere Widget auf diesem Dashboard schon hat.
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=30),
                data_endpoint="widgets/node-load",
                view=GaugeView(value_field="value", max_value=100, label="CPU-Auslastung (Ø über {{ node_count }} Knoten)"),
            )
        )
        ctx.ui.register_widget(
            WidgetSpec(
                id="storage",
                title="Proxmox-Speicher",
                icon="hard-drive",
                size=GridSize(w=2, h=2),
                refresh=Refresh(interval_s=60),
                data_endpoint="widgets/storage",
                view=ListView(
                    item=ListItem(
                        title="{{ storage }}",
                        subtitle="{{ summary }}",
                        badge=Badge(text="{{ badge }}", tone="{{ tone }}"),
                    ),
                    empty_text="Keine Speicher-Pools gefunden",
                ),
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="updates",
                title="Proxmox-Updates",
                icon="package",
                size=GridSize(w=2, h=2),
                # Proxmox prueft selbst nur einmal taeglich -- oefter nachzufragen bringt nichts.
                refresh=Refresh(interval_s=900),
                data_endpoint="widgets/updates",
                view=ListView(
                    item=ListItem(
                        title="{{ node }}",
                        subtitle="{{ summary }}",
                        badge=Badge(text="{{ badge }}", tone="{{ tone }}"),
                    ),
                    empty_text="Keine Proxmox-Knoten gefunden",
                ),
            )
        )

        ctx.ui.register_widget(
            WidgetSpec(
                id="disks",
                title="Proxmox-Datenträger",
                icon="hard-drive",
                size=GridSize(w=2, h=2),
                # SMART aendert sich langsam; alle 10 Minuten reicht.
                refresh=Refresh(interval_s=600),
                data_endpoint="widgets/disks",
                view=ListView(
                    item=ListItem(
                        title="{{ node }} · {{ model }}",
                        subtitle="{{ summary }}",
                        badge=Badge(text="{{ badge }}", tone="{{ tone }}"),
                    ),
                    empty_text="Keine Datenträger gefunden",
                ),
            )
        )

        # Server-Seite (Plesk-Stil): was diese Extension fuer ihre eigenen Hosts anbietet.
        ctx.ui.register_host_tool(HostToolSpec(
            id="guest", title="Hardware, Netzwerk & Snapshots", icon="server", category="settings",
            description="Kerne, RAM, Disks mit Speicherort, Netzwerk, Snapshots", path="/nodes?host={host_id}",
            kinds=["vm", "lxc"], own_hosts_only=True,
        ))
        ctx.ui.register_host_tool(HostToolSpec(
            id="node", title="Hardware, Datenträger & Updates", icon="hard-drive", category="monitoring",
            description="Auslastung, SMART-Zustand der Platten, Paket-Updates", path="/nodes?host={host_id}",
            kinds=["hypervisor"], own_hosts_only=True,
        ))
        ctx.ui.register_host_tool(HostToolSpec(
            id="tasks", title="Aufgabenverlauf", icon="activity", category="monitoring",
            description="Wer hat wann was auf dem Knoten getan", path="/nodes?host={host_id}&tasks=1",
            own_hosts_only=True, order=200,
        ))

        await ctx.scheduler.register_job(_DiscoveryJobSpec(ctx))
        await ctx.scheduler.register_job(_WatchJobSpec(ctx))

    async def on_start(self, ctx: ExtensionContext) -> None:
        return None

    async def on_stop(self, ctx: ExtensionContext) -> None:
        return None

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        """**Multi-Instanz-Nachtrag, geschaerft:** die erste Fassung
        meldete `healthy=True`, sobald IRGENDEINE Verbindung erreichbar war -- ein
        Ausfall einer Instanz wurde von der anderen, funktionierenden verdeckt (pve2
        down, pve1 laeuft -> insgesamt "gesund", obwohl die Haelfte der Flotte
        unerreichbar ist). `healthy=True` gilt jetzt nur noch, wenn ALLE
        konfigurierten Verbindungen erreichbar sind. `details[name]["healthy"]` macht
        den Zustand JEDER einzelnen Verbindung explizit (nicht nur implizit ueber die
        An-/Abwesenheit eines "error"-Schluessels erschliessbar), `message` fasst
        IMMER alle Verbindungen zusammen (nicht nur die fehlgeschlagenen) -- sichtbar
        auch bei einem kurzen Blick, ohne `details` erst aufklappen zu muessen."""
        connectors = await build_connectors(ctx)
        if not connectors:
            return HealthReport(healthy=False, message="Keine Verbindung konfiguriert.")

        details: dict[str, Any] = {}
        summaries: list[str] = []
        all_healthy = True
        for name, connector in connectors.items():
            try:
                data = await connector.version()
                version = data.get("version") if isinstance(data, dict) else None
                details[name] = {"healthy": True, "version": version}
                summaries.append(f"{name}: ok")
            except (RuntimeError, ProxmoxApiError) as exc:
                details[name] = {"healthy": False, "error": str(exc)}
                summaries.append(f"{name}: FEHLER: {exc}")
                all_healthy = False

        return HealthReport(healthy=all_healthy, message="; ".join(summaries), details=details)
