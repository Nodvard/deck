"""Proxmox-Erweiterung: entdeckt Knoten und Gäste als Hosts, liefert Messwerte und führt Aktionen über das Gate aus."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
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
    SecretNotOwned,
    StatusGridView,
    WidgetSpec,
    same_target,
)
from pydantic import BaseModel, Field, field_validator

from .capabilities import (
    ProxmoxActionExecutor,
    ProxmoxConsoleTarget,
    ProxmoxHostProvider,
    ProxmoxMetricsProvider,
    _parse_ref,
)
from .certificate import FIELD as FINGERPRINT_FIELD
from .certificate import MAX_INPUT as MAX_FINGERPRINT_INPUT
from .certificate import build_certificate_router, connection_pin, normalize_fingerprint
from .config import TOKEN_LABEL, build_connector, build_connectors, foreign_secret_text
from .connector import ProxmoxApiError
from .guest_config import collect_guest_details
from .node_health import collect_disks, collect_node_health, temperature_setting
from .password_connect import build_router as build_password_connect_router
from .places import ProxmoxFilePlaces
from .storage import collect_storage, format_bytes, percent_badge, shared_label
from .tasks import collect_tasks
from .updates import collect_updates
from .watch import run_watch


# "hypervisor" ist ein interner Wert; die Proxmox-Seite nennt ihn "Knoten".
_KIND_LABEL = {"vm": "VM", "lxc": "LXC", "hypervisor": "Knoten"}


GATEWAY_FALLBACK = "Proxmox hat die Anfrage nicht beantwortet. Einzelheiten stehen im Protokoll des Dashboards."
# Die Kachel nennt nur feste Sätze; den Grund zeigen der Verbindungstest und die Seite des Knotens.
DISKS_CONNECTION_FAILED = "Verbindung gestört. Den Grund zeigt „Verbindung testen“ in den Einstellungen des Moduls."
DISKS_NODE_FAILED = "Datenträger nicht abrufbar. Den Grund zeigt die Seite des Knotens."


def bad_gateway(exc: BaseException, logger: Any) -> HTTPException:
    """502 für die Oberfläche; den Text der Ausnahme sieht der Client nur bei `readable` (etwa `ProxmoxApiError`)."""
    text = str(exc).strip()
    if getattr(exc, "readable", False) and text:
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=text)
    # Andere Ausnahmen (etwa ein RuntimeError aus einer Bibliothek) können interne Einzelheiten enthalten: nur ins Protokoll.
    logger.warning("Proxmox: Anfrage fehlgeschlagen (%s)", type(exc).__name__, exc_info=exc)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=GATEWAY_FALLBACK)


class _DiscoveryJobSpec:
    """Erfüllt `JobSpec` strukturell; der Scheduler ruft `handler` bei Fälligkeit und per `POST /jobs/{id}/run` auf."""

    id = "discovery"
    name = "Proxmox-Abgleich"
    schedule = "*/5 * * * *"
    params: dict[str, Any] = {}
    enabled = True

    def __init__(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

    async def handler(self, **_: Any) -> dict[str, int]:
        return {"discovered": await ProxmoxHostProvider(self._ctx).sync()}


class _WatchJobSpec:
    """Zustandswächter (watch.py): meldet nur Zustandswechsel."""

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
    """Wie das Schema von `settings.connections`, aber ohne Token: das liegt nur im Tresor (`POST .../{name}/token`)."""

    name: str
    base_url: str
    token_id: str
    tls_insecure_skip_verify: bool = False
    tls_fingerprint_sha256: str | None = Field(default=None, max_length=MAX_FINGERPRINT_INPUT)
    enabled: bool = True

    @field_validator("tls_fingerprint_sha256")
    @classmethod
    def _fingerprint(cls, value: str | None) -> str | None:
        return normalize_fingerprint(value)


class _ConnectionPatch(BaseModel):
    """Alle Felder optional: ein `PUT` ändert nur, was mitgeschickt wird (z. B. `enabled` allein)."""

    base_url: str | None = None
    token_id: str | None = None
    tls_insecure_skip_verify: bool | None = None
    tls_fingerprint_sha256: str | None = Field(default=None, max_length=MAX_FINGERPRINT_INPUT)
    """Leer oder `null` nimmt den gemerkten Fingerabdruck weg."""
    enabled: bool | None = None

    @field_validator("tls_fingerprint_sha256")
    @classmethod
    def _fingerprint(cls, value: str | None) -> str | None:
        return normalize_fingerprint(value)


class _ConnectionOut(BaseModel):
    name: str
    base_url: str
    token_id: str
    tls_insecure_skip_verify: bool
    enabled: bool
    has_token: bool
    tls_fingerprint_sha256: str | None = None
    """Der gemerkte Fingerabdruck (`AB:CD:...`), sonst `None`."""
    tls_fingerprint_invalid: bool = False
    """Im Feld steht etwas, das kein Fingerabdruck ist -- die Verbindung ist dann gesperrt, bis es stimmt."""


class Extension(NodvardExtension):
    async def setup(self, ctx: ExtensionContext) -> None:
        self._ctx = ctx

        ctx.capabilities.provide(ProxmoxHostProvider(ctx))
        ctx.capabilities.provide(ProxmoxMetricsProvider(ctx))
        ctx.capabilities.provide(ProxmoxActionExecutor(ctx))
        ctx.capabilities.provide(ProxmoxConsoleTarget(ctx))
        ctx.capabilities.provide(ProxmoxFilePlaces(ctx))

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
                                "tls_fingerprint_sha256": {"type": ["string", "null"]},
                                "enabled": {"type": "boolean"},
                            },
                            "required": ["name", "base_url", "token_id"],
                        },
                    },
                    "storage_warn_percent": {"type": "integer", "minimum": 50, "maximum": 99, "default": 90},
                    "storage_skip": {"type": "array", "items": {"type": "string"}},
                    "disk_temp_warn_c": {"type": "integer", "minimum": 40, "maximum": 95},
                },
                "required": ["connections"],
            }
        )

        router = APIRouter()

        @router.post("/connections/{name}/token", status_code=status.HTTP_204_NO_CONTENT)
        async def set_token(name: str, payload: _TokenIn) -> None:
            """Legt das Token im Tresor ab, nie in den Einstellungen; ein Geheimnis je Verbindung."""
            # Nur Erstanlage. Ersetzen und Entfernen laufen über die Moduleinstellungen des Kerns, mit Passwort.
            label = f"{TOKEN_LABEL}:{name}"
            # Bei einem doppelten Namen gälte das Token für die zuletzt eingetragene Adresse, nicht unbedingt die gemeinte.
            connections = (await ctx.settings.get()).get("connections") or []
            if [str(c.get("name") or "").strip() for c in connections if isinstance(c, dict)].count(name) > 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Der Name „{name}“ kommt mehrfach vor. Benenne unter Einstellungen → Erweiterungen → Proxmox VE "
                        "eine der Verbindungen um und speichere; danach kannst du das Token hinterlegen."
                    ),
                )
            if await ctx.secrets.exists(label):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Ein Token für '{name}' existiert bereits. Ersetzen oder entfernen unter Einstellungen → "
                        f"Erweiterungen → Proxmox VE (API: PUT oder DELETE /api/v1/extensions/proxmox/secrets, "
                        f"Bezeichnung {label})."
                    ),
                )
            await ctx.secrets.create(label=label, kind="generic", value=payload.value)

        async def _drop_secret_or_refuse(label: str) -> None:
            """Löscht das Geheimnis einer Verbindung; ist es nicht von dieser Erweiterung (etwa von Hand angelegt),
            bleibt es, und die Anfrage endet mit 409."""
            foreign = False
            try:
                await ctx.secrets.delete(label)
            except SecretNotOwned:
                foreign = True
            if foreign:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=foreign_secret_text(label))

        async def _connection_out(conn: dict[str, Any]) -> _ConnectionOut:
            name = str(conn.get("name") or "")
            pin, pin_invalid = connection_pin(conn)
            return _ConnectionOut(
                name=name,
                base_url=str(conn.get("base_url") or ""),
                token_id=str(conn.get("token_id") or ""),
                tls_insecure_skip_verify=bool(conn.get("tls_insecure_skip_verify") or False),
                enabled=conn.get("enabled") is not False,
                has_token=await ctx.secrets.exists(f"{TOKEN_LABEL}:{name}") if name else False,
                tls_fingerprint_sha256=pin,
                tls_fingerprint_invalid=pin_invalid,
            )

        @router.get("/connections")
        async def list_connections() -> list[_ConnectionOut]:
            """Verbindungen für die Oberfläche; nie der Token-Wert, nur ob eines gesetzt ist."""
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
            # Eine neue Verbindung startet ohne Token: ein altes unter demselben Namen gehörte zu einer anderen Adresse.
            # Ein fremdes Geheimnis (etwa von Hand im Tresor) bleibt, dann wird nichts angelegt.
            await _drop_secret_or_refuse(f"{TOKEN_LABEL}:{payload.name}")
            entry = payload.model_dump()
            if entry.get(FINGERPRINT_FIELD) is None:
                entry.pop(FINGERPRINT_FIELD, None)
            else:
                entry["tls_insecure_skip_verify"] = False  # der Fingerabdruck ersetzt den Schalter
            connections.append(entry)
            settings["connections"] = connections
            await ctx.settings.set(settings)
            return await _connection_out(entry)

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
                if FINGERPRINT_FIELD in updates:
                    if updates[FINGERPRINT_FIELD] is None:
                        conn.pop(FINGERPRINT_FIELD, None)
                    elif "tls_insecure_skip_verify" not in updates:
                        conn["tls_insecure_skip_verify"] = False  # der Fingerabdruck ersetzt den Schalter
                if "base_url" in updates and not same_target(old_url, conn.get("base_url")):
                    # Das Token gehört zur alten Adresse, sonst ginge es an die neue. Ein fremdes Geheimnis lässt die
                    # Adresse unverändert.
                    await _drop_secret_or_refuse(f"{TOKEN_LABEL}:{name}")
                    if FINGERPRINT_FIELD not in updates:
                        # Der gemerkte Fingerabdruck gehört zum alten Server.
                        conn.pop(FINGERPRINT_FIELD, None)
                settings["connections"] = connections
                await ctx.settings.set(settings)
                return await _connection_out(conn)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{name}' existiert nicht.")

        @router.delete("/connections/{name}", status_code=status.HTTP_204_NO_CONTENT)
        async def remove_connection(name: str) -> None:
            """Entfernt die Verbindung samt Token im Tresor, damit eine neue gleichnamige Verbindung es nicht erbt."""
            settings = await ctx.settings.get()
            connections: list[dict[str, Any]] = settings.get("connections") or []
            filtered = [c for c in connections if c.get("name") != name]
            if len(filtered) == len(connections):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Verbindung '{name}' existiert nicht.")
            settings["connections"] = filtered
            await ctx.settings.set(settings)
            try:
                await ctx.secrets.delete(f"{TOKEN_LABEL}:{name}")
            except SecretNotOwned:
                # Ein fremdes Geheimnis (etwa von Hand im Tresor) bleibt unberührt.
                ctx.logger.warning("Proxmox: Verbindung %s entfernt, fremdes Geheimnis im Tresor bleibt", name)

        ctx.api.include_router(router, permission="secrets.write")
        # "Mit Passwort verbinden" (password_connect.py) legt Verbindung und Token-Geheimnis an wie die Routen oben,
        # daher dieselbe Berechtigung.
        ctx.api.include_router(build_password_connect_router(ctx), permission="secrets.write")
        # Fingerabdruck des Zertifikats ansehen und bestätigen (certificate.py): ändert die Verbindung wie PUT oben.
        ctx.api.include_router(build_certificate_router(ctx), permission="secrets.write")

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
                        # Verbindungsname aus host_metadata, damit gleiche Knoten mehrerer Instanzen unterscheidbar sind.
                        "connection": (h.metadata or {}).get("connection", ""),
                    }
                    for h in hosts
                ],
                "meta": {},
            }

        @data_router.get("/guests/{host_id}/snapshots")
        async def guest_snapshots(host_id: str) -> list[dict]:
            """Snapshots eines Gasts, neueste zuerst, ohne Proxmox' Pseudo-Eintrag "current" (der laufende Zustand).
            Zurückrollen und Löschen laufen als Aktionen über das Gate, nicht hier."""
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
                raise bad_gateway(exc, ctx.logger) from exc
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
            """Kerne, RAM, Disks, Netzwerk und Gast-Agent (guest_config.py), nur Felder der Positivliste."""
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
                raise bad_gateway(exc, ctx.logger) from exc

        @data_router.get("/nodes/{host_id}/health")
        async def node_health(host_id: str) -> dict:
            """Prozessor, Last, Speicher, Systemplatte und Datenträger mit SMART-Zustand eines Knotens (node_health.py)."""
            host = await ctx.hosts.get(host_id)
            if host is None or host.provider_ext_id != ctx.ext_id or not host.provider_ref:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Knoten.")
            connection, kind, node, _vmid = _parse_ref(host.provider_ref)
            if kind != "node":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nur fuer Proxmox-Knoten.")
            try:
                connector = await build_connector(ctx, connection)
                temp_setting = temperature_setting((await ctx.settings.get()).get("disk_temp_warn_c"))
                return await collect_node_health(connector, connection, node, temp_setting)
            except (RuntimeError, ProxmoxApiError) as exc:
                raise bad_gateway(exc, ctx.logger) from exc

        @data_router.get("/widgets/disks")
        async def disks_widget_data() -> dict:
            """Datenträger aller Knoten, schlechtester Zustand zuerst; ein unerreichbarer Knoten wird eine eigene Zeile."""
            rows: list[dict] = []
            temp_setting = temperature_setting((await ctx.settings.get()).get("disk_temp_warn_c"))
            for conn_name, connector in (await build_connectors(ctx)).items():
                try:
                    nodes = await connector.list_nodes()
                except ProxmoxApiError:
                    rows.append({"node": conn_name, "model": "Verbindung", "summary": DISKS_CONNECTION_FAILED, "badge": "nicht erreichbar", "tone": "danger"})
                    continue
                for entry in nodes:
                    node_name = entry.get("node")
                    if not node_name:
                        continue
                    if entry.get("status") not in (None, "online"):
                        rows.append({"node": node_name, "model": "Knoten", "summary": "offline", "badge": "offline", "tone": "danger"})
                        continue
                    try:
                        rows.extend(await collect_disks(connector, conn_name, node_name, temp_setting))
                    except ProxmoxApiError:
                        rows.append({"node": node_name, "model": "Datenträger", "summary": DISKS_NODE_FAILED, "badge": "nicht abrufbar", "tone": "neutral"})
            order = {"danger": 0, "warn": 1, "neutral": 2, "good": 3}
            rows.sort(key=lambda r: (order.get(r["tone"], 2), r["node"]))
            return {"data": rows, "meta": {}}

        @data_router.get("/tasks")
        async def task_history(limit: int = Query(50, ge=1, le=1000), include_console: bool = False) -> dict:
            """Aufgabenverlauf aller Knoten (tasks.py); Konsolen-Öffnungen sind standardmäßig ausgeblendet."""
            return await collect_tasks(ctx, limit=max(1, min(limit, 200)), include_console=include_console)

        @data_router.get("/tasks/{connection}/{node}/log")
        async def task_log(connection: str, node: str, upid: str) -> dict:
            """Protokoll eines Tasks. `upid` kommt als Query-Parameter (enthält Doppelpunkte) und muss zu `node` passen:
            Proxmox kodiert den Knoten in der UPID selbst (`UPID:<node>:...`)."""
            if not upid.startswith(f"UPID:{node}:") or "/" in upid:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="UPID passt nicht zum Knoten.")
            try:
                connector = await build_connector(ctx, connection)
                rows = await connector.task_log(node, upid)
            except (RuntimeError, ProxmoxApiError) as exc:
                raise bad_gateway(exc, ctx.logger) from exc
            return {"lines": [str(r.get("t", "")) for r in sorted(rows, key=lambda r: r.get("n", 0))]}

        @data_router.get("/updates")
        async def updates_overview() -> dict:
            """Wartende Paket-Updates je Knoten mit Paketliste (updates.py)."""
            return await collect_updates(ctx, details=True)

        @data_router.get("/widgets/updates")
        async def updates_widget_data() -> dict:
            """Eine nicht erreichbare Verbindung erscheint als eigene rote Zeile, damit sie nicht still fehlt."""
            overview = await collect_updates(ctx, details=False)
            rows = [
                {"connection": e["connection"], "node": e["connection"], "summary": e["error"], "badge": "nicht erreichbar", "tone": "danger"}
                for e in overview["errors"]
            ]
            return {"data": rows + overview["nodes"], "meta": {"errors": overview["errors"]}}

        @data_router.get("/storage")
        async def storage_overview() -> dict:
            """Belegung je Pool und die Gast-Disks darauf (storage.py)."""
            return await collect_storage(ctx, details=True)

        @data_router.get("/widgets/storage")
        async def storage_widget_data() -> dict:
            """Nur die Belegung (eine Abfrage je Knoten); der Pool-Inhalt wäre für das Neuladen alle 60 s zu teuer."""
            overview = await collect_storage(ctx, details=False, merge_connections=True)
            # Fehler als eigene rote Zeile mit fertigem Text aus dem Backend, damit sie keine leeren Zahlen zeigt.
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
            """Durchschnitt der CPU-Auslastung über alle bekannten Knoten; ein nicht erreichbarer Knoten wird einzeln
            übersprungen, solange mindestens einer antwortet."""
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

        # Ohne `permission` wäre der Router ohne Login erreichbar.
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
                # Die Gauge-Ansicht zeigt ein 80x80-px-SVG neben dem Text; in 1x1 Feldern wirkt die Karte abgeschnitten.
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
                # Proxmox prüft selbst nur einmal täglich, öfter nachzufragen bringt nichts.
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
                # SMART ändert sich langsam; alle 10 Minuten reicht.
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

        # Server-Seite: was diese Erweiterung für ihre eigenen Hosts anbietet.
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
        """Gesund nur, wenn alle Verbindungen erreichbar sind: `details` und `message` nennen jede einzeln, damit der
        Ausfall einer Instanz nicht von einer funktionierenden verdeckt wird."""
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
                code = getattr(exc, "code", None)
                if code:
                    details[name]["code"] = code  # z. B. "certificate_changed": die Oberfläche bietet das Bestätigen an
                summaries.append(f"{name}: FEHLER: {exc}")
                all_healthy = False

        return HealthReport(healthy=all_healthy, message="; ".join(summaries), details=details)
