"""Duenner Client fuer die Proxmox-VE-REST-API (`/api2/json/...`).

Nutzt ausschliesslich `ctx.http` (docs/02-EXTENSION-API.md §2) statt eines eigenen
httpx-Clients -- das ist die Stelle, an der `net.outbound`-Berechtigungen durchgesetzt
werden (siehe ext/context.py HttpHandle). Ein eigener Client wuerde diese Pruefung
umgehen.

Authentifizierung ueber einen API-Token (`Authorization: PVEAPIToken=user@realm!name=
secret`), nicht ueber Benutzername/Passwort + Ticket-Cookie -- kein Session-Refresh
noetig, das im Vorgaengersystem genutzte SSH+`pvesh`
funktioniert zwar auch, ist aber ein Nebenkanal mit eigenen Edge-Cases (siehe
docs/00-DECISIONS.md zu "kein zweiter Ausfuehrungsweg"); die echte REST-API ist hier
die robustere Wahl fuer eine Extension, die weder SSH-Zugangsdaten noch einen
SSH-Host-Key-Vertrauensanker fuer den Proxmox-Knoten braucht.

**Task-Ausgang abwarten:** Proxmox beantwortet
Start/Stop/Reboot/Snapshot mit einer Task-ID (`UPID:...`), nicht mit dem
Endergebnis -- `wait_for_task()` pollt jetzt `/nodes/{node}/tasks/{upid}/status`
bis der Task nicht mehr `running` ist oder ein Zeitlimit erreicht wird.
`ProxmoxActionExecutor.execute()` (capabilities.py) nutzt das, damit
`ActionResult.success` den TATSAECHLICHEN Ausgang meint (`exitstatus == "OK"`),
nicht mehr nur "Proxmox hat die Anfrage angenommen".

**`tls_insecure_skip_verify` (WP-8-Blocker-Nachtrag):** reicht `insecure_tls=True` an
JEDEN `ctx.http`-Aufruf durch, wenn diese Connector-Instanz dafuer konfiguriert ist
(Einstellung `tls_insecure_skip_verify`, siehe `config.py`) -- betrifft ausschliesslich
diese eine Proxmox-Instanz, nicht andere Ziele, die dieselbe Extension ueber
`ctx.http` erreicht (siehe `ext/context.py HttpHandle`-Docstring fuer die
Kern-Mechanik).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


class ProxmoxApiError(Exception):
    pass


class ProxmoxVncSession:
    """Erfuellt `nodvard_sdk.capabilities.ConsoleSession` -- ein offener
    `vncwebsocket` zu Proxmox. Reine Byte-Weitergabe (RFB), das Protokoll selbst
    sprechen erst noVNC im Browser und der VNC-Server auf dem Knoten."""

    protocol = "vnc"

    def __init__(self, ws: Any, stack: Any, *, password: str | None) -> None:
        self._ws = ws
        self._stack = stack
        self.password = password

    async def read(self):  # noqa: ANN201 - AsyncIterator[bytes], siehe Protokoll
        try:
            async for message in self._ws:
                yield message.encode() if isinstance(message, str) else message
        except Exception:  # noqa: BLE001 - Verbindungsende (VM aus, Proxmox trennt) beendet nur den Strom
            return

    async def write(self, data: bytes) -> None:
        await self._ws.send(data)

    async def close(self) -> None:
        await self._stack.aclose()


class ProxmoxConnector:
    def __init__(
        self,
        ctx: "ExtensionContext",
        *,
        base_url: str,
        token_id: str,
        token_secret: str,
        tls_insecure_skip_verify: bool = False,
    ) -> None:
        self._ctx = ctx
        self._base_url = base_url.rstrip("/")
        self._auth_header = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        self._tls_insecure_skip_verify = tls_insecure_skip_verify

    @property
    def host(self) -> str:
        """Nur fuer `Host.address` -- der Proxmox-API-Host selbst, ohne Schema/Port."""
        from urllib.parse import urlsplit

        return urlsplit(self._base_url).hostname or self._base_url

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}/api2/json{path}"
        headers = {**self._auth_header, **kwargs.pop("headers", {})}
        # Live gefunden (WP-8-Boot-Test, Nachtrag): ein NETZWERK-Fehler (Proxmox nicht
        # erreichbar -- Verbindung abgelehnt, Timeout, DNS) wurde bisher gar nicht
        # abgefangen, nur ein HTTP-Fehlerstatus. `node_load_widget_data()` (siehe
        # __init__.py) faengt ausschliesslich `ProxmoxApiError`/`RuntimeError` ab --
        # eine rohe `httpx`-Exception lief ungefangen bis zu FastAPI durch und ergab
        # einen 500 statt eines sauberen "Metrik nicht verfuegbar". Jetzt einheitlich
        # an EINER Stelle normalisiert, statt in jedem Aufrufer einzeln nachzuruesten.
        try:
            response = await self._ctx.http.request(
                method, url, headers=headers, insecure_tls=self._tls_insecure_skip_verify, **kwargs
            )
        except ProxmoxApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - jeder Netzwerkfehler wird hier vereinheitlicht
            # Live gefunden: httpx' Timeouts haben einen LEEREN Text -- die Meldung
            # lautete woertlich "POST /nodes/pve1/qemu/<vmid>/vncproxy -> ".
            raise ProxmoxApiError(f"{method} {path} -> {str(exc) or type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ProxmoxApiError(f"{method} {path} -> HTTP {response.status_code}: {response.text[:200]}")
        body = response.json()
        return body.get("data")

    async def version(self) -> dict[str, Any]:
        return await self._request("GET", "/version")

    async def list_nodes(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/nodes") or []

    async def node_status(self, node: str) -> dict[str, Any]:
        return await self._request("GET", f"/nodes/{node}/status") or {}

    async def list_qemu(self, node: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/nodes/{node}/qemu") or []

    async def qemu_status(self, node: str, vmid: str) -> dict[str, Any]:
        return await self._request("GET", f"/nodes/{node}/qemu/{vmid}/status/current") or {}

    async def qemu_action(self, node: str, vmid: str, action: str, params: dict[str, Any] | None = None) -> str:
        """`action` in {"start", "shutdown", "stop", "reboot"} -- Proxmox-eigene Endpunktnamen
        (`shutdown` = geordnet herunterfahren, `stop` = hart ausschalten). `params` geht als
        JSON-Body mit, z. B. `{"timeout": 180}` beim Herunterfahren (sonst bricht Proxmox
        den Task schon nach etwa 60 s ab)."""
        kwargs: dict[str, Any] = {"json": params} if params else {}
        return await self._request("POST", f"/nodes/{node}/qemu/{vmid}/status/{action}", **kwargs)

    async def qemu_snapshot(self, node: str, vmid: str, snapname: str) -> str:
        return await self._request("POST", f"/nodes/{node}/qemu/{vmid}/snapshot", json={"snapname": snapname})

    # -- LXC (WP-8-Blocker-Nachtrag: strukturell fast identisch zu /qemu/... -- ohne
    # diese vier Methoden sah die Extension auf einem reinen LXC-Knoten schlicht
    # nichts) --------------------------------------------------------------------

    async def list_lxc(self, node: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/nodes/{node}/lxc") or []

    async def lxc_status(self, node: str, vmid: str) -> dict[str, Any]:
        return await self._request("GET", f"/nodes/{node}/lxc/{vmid}/status/current") or {}

    async def lxc_action(self, node: str, vmid: str, action: str, params: dict[str, Any] | None = None) -> str:
        """`action` in {"start", "shutdown", "stop", "reboot"} -- dieselben Proxmox-Endpunktnamen
        wie bei QEMU-VMs; `params` wie dort."""
        kwargs: dict[str, Any] = {"json": params} if params else {}
        return await self._request("POST", f"/nodes/{node}/lxc/{vmid}/status/{action}", **kwargs)

    async def lxc_snapshot(self, node: str, vmid: str, snapname: str) -> str:
        return await self._request("POST", f"/nodes/{node}/lxc/{vmid}/snapshot", json={"snapname": snapname})

    # -- Snapshots (frueher konnte Nodvard Deck sie nur ANLEGEN) ------------------------

    async def guest_config(self, node: str, kind: str, vmid: str) -> dict[str, Any]:
        """Rohkonfiguration eines Gasts. Enthaelt ggf. `cipassword`/`sshkeys` --
        NIE ungefiltert weitergeben, siehe guest_config.summarize_config (Whitelist)."""
        return await self._request("GET", f"/nodes/{node}/{kind}/{vmid}/config") or {}

    async def rrddata(self, node: str, kind: str, vmid: str | None, timeframe: str) -> list[dict[str, Any]]:
        """Proxmox' eigene Messreihen (RRD): `timeframe` hour|day|week|month|year,
        Mittelwerte je Stuetzstelle. Knoten: `/nodes/{node}/rrddata`, Gaeste zusaetzlich
        `/{qemu|lxc}/{vmid}`."""
        path = f"/nodes/{node}/rrddata" if kind == "node" else f"/nodes/{node}/{kind}/{vmid}/rrddata"
        return await self._request("GET", path, params={"timeframe": timeframe, "cf": "AVERAGE"}) or []

    async def apt_refresh(self, node: str) -> str:
        """`apt update` des Knotens (Paketlisten neu laden) -- liefert eine UPID."""
        return await self._request("POST", f"/nodes/{node}/apt/update")

    async def node_reboot(self, node: str) -> Any:
        return await self._request("POST", f"/nodes/{node}/status", json={"command": "reboot"})

    async def update_guest_config(self, node: str, kind: str, vmid: str, params: dict[str, Any]) -> Any:
        """PUT = synchron (fuer QEMU gibt es auch POST = asynchron mit Task). Nur mit
        Parametern aus guest_edit.build_update -- nie mit Nutzereingaben direkt."""
        return await self._request("PUT", f"/nodes/{node}/{kind}/{vmid}/config", json=params)

    async def guest_pending(self, node: str, kind: str, vmid: str) -> list[dict[str, Any]]:
        """Aktuelle vs. vorgemerkte Werte -- was erst nach einem Neustart greift."""
        return await self._request("GET", f"/nodes/{node}/{kind}/{vmid}/pending") or []

    async def list_snapshots(self, node: str, kind: str, vmid: str) -> list[dict[str, Any]]:
        """`kind` in {"qemu", "lxc"}. Enthaelt immer den Pseudo-Eintrag "current"
        (der laufende Zustand) -- der Aufrufer filtert ihn."""
        return await self._request("GET", f"/nodes/{node}/{kind}/{vmid}/snapshot") or []

    async def snapshot_rollback(self, node: str, kind: str, vmid: str, snapname: str) -> str:
        return await self._request("POST", f"/nodes/{node}/{kind}/{vmid}/snapshot/{snapname}/rollback")

    async def snapshot_delete(self, node: str, kind: str, vmid: str, snapname: str) -> str:
        return await self._request("DELETE", f"/nodes/{node}/{kind}/{vmid}/snapshot/{snapname}")

    # -- Aufgabenverlauf ------------------------------------------------------------------

    async def node_tasks(self, node: str, *, limit: int = 50, typefilter: str | None = None) -> list[dict[str, Any]]:
        """Voller Task-Index des Knotens (neueste zuerst), inkl. laufender Tasks."""
        params: dict[str, Any] = {"limit": limit, "source": "all"}
        if typefilter:
            params["typefilter"] = typefilter
        return await self._request("GET", f"/nodes/{node}/tasks", params=params) or []

    async def task_log(self, node: str, upid: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """Protokollzeilen eines Tasks: `[{"n": Zeilennummer, "t": Text}, ...]`."""
        return await self._request("GET", f"/nodes/{node}/tasks/{upid}/log", params={"limit": limit}) or []

    # -- Datentraeger (Knoten-Gesundheit, rein lesend) -----------------------------------

    async def node_disks(self, node: str) -> list[dict[str, Any]]:
        """Physische Datentraeger des Knotens mit SMART-Kurzbefund (`health`) und
        Abnutzung (`wearout` = Restlebensdauer in %, bei manchen SSDs "N/A")."""
        return await self._request("GET", f"/nodes/{node}/disks/list", timeout=30.0) or []

    async def disk_smart(self, node: str, devpath: str) -> dict[str, Any]:
        """SMART-Details eines Datentraegers: NVMe als Freitext, SATA als Attributliste."""
        return await self._request("GET", f"/nodes/{node}/disks/smart", params={"disk": devpath}, timeout=30.0) or {}

    # -- Paket-Updates (rein lesend) -----------------------------------------------------

    async def apt_updates(self, node: str) -> list[dict[str, Any]]:
        """Wartende Pakete laut Proxmox' letzter Pruefung. Liest nur -- `POST` auf
        denselben Pfad wuerde die Paketlisten neu laden, das tut Nodvard Deck nicht.
        Live gefunden: pve2 lief hier einmal in den Standard-Timeout, beim naechsten
        Aufruf 0,0 s -- deshalb grosszuegiger Timeout statt eines leeren Widgets."""
        return await self._request("GET", f"/nodes/{node}/apt/update", timeout=30.0) or []

    async def apt_versions(self, node: str) -> list[dict[str, Any]]:
        """Installierte Proxmox-Pakete inkl. aller Kernel-Images (fuer "Neustart ausstehend")."""
        return await self._request("GET", f"/nodes/{node}/apt/versions", timeout=30.0) or []

    # -- Speicher (Speicher-Uebersicht) --------------------------------------------------

    async def node_storage(self, node: str) -> list[dict[str, Any]]:
        """Alle Speicher-Pools, die der Knoten sieht, mit `total`/`used`/`avail` in Byte."""
        return await self._request("GET", f"/nodes/{node}/storage") or []

    async def storage_content(self, node: str, storage: str) -> list[dict[str, Any]]:
        """Inhalt eines Pools: `volid`, `content` (images/rootdir/backup/iso ...),
        `vmid` (bei Gast-Disks und Backups), `size`."""
        return await self._request("GET", f"/nodes/{node}/storage/{storage}/content") or []

    # -- Gast-Adressen (sonst stehen Platzhalter-IPs in der Terminal-Liste) -------------

    async def lxc_interfaces(self, node: str, vmid: str) -> list[dict[str, Any]]:
        """Netzwerkschnittstellen eines LAUFENDEN Containers (`name`, `inet` als
        "a.b.c.d/nn") -- ohne Agent, Proxmox liest sie direkt aus dem Container."""
        return await self._request("GET", f"/nodes/{node}/lxc/{vmid}/interfaces", timeout=5.0) or []

    async def qemu_agent_interfaces(self, node: str, vmid: str) -> list[dict[str, Any]]:
        """Schnittstellen einer VM ueber den qemu-guest-agent. Ohne laufenden Agenten
        antwortet Proxmox mit HTTP 500 ("QEMU guest agent is not running") -- das
        wird zu `ProxmoxApiError`, der Aufrufer behandelt es als "unbekannt"."""
        data = await self._request("GET", f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", timeout=5.0) or {}
        return list(data.get("result") or [])

    # -- Konsole (Bildschirm einer VM/eines Containers in Nodvard Deck) ----------------

    async def vncproxy(self, node: str, kind: str, vmid: str) -> dict[str, Any]:
        """`kind` in {"qemu", "lxc"}. Startet auf dem Knoten einen kurzlebigen
        VNC-Proxy (wartet ~10s auf die Gegenverbindung) und liefert `port` + `ticket`.
        `websocket=1`: der Proxy wird ueber `.../vncwebsocket` erreicht, nicht ueber
        einen rohen TCP-Port -- genau der Weg, den Proxmox' eigene Oberflaeche nimmt.
        `generate-password=1` nur fuer QEMU: dort ist das VNC-Kennwort sonst das
        Ticket selbst, QEMUs VNC-Auth nutzt davon aber nur die ersten 8 Zeichen;
        Proxmox erzeugt dafuer ein eigenes Einmal-Kennwort (`password`). Der
        LXC-Endpunkt bekommt den Parameter nicht (live gegen pve2: liefert trotzdem
        ein eigenes `password`); fehlt es, bleibt das Ticket das Kennwort."""
        body: dict[str, Any] = {"websocket": 1}
        if kind == "qemu":
            body["generate-password"] = 1
        # Live gefunden: Proxmox startet dafuer auf dem Knoten einen eigenen Prozess --
        # bei einer GPU-VM dauerte das einmal laenger als httpx' 5s-Default
        # (danach wieder schnell). 20s statt eines sporadischen Fehlschlags.
        return await self._request(
            "POST", f"/nodes/{node}/{kind}/{vmid}/vncproxy", json=body, timeout=20.0
        ) or {}

    def vncwebsocket_url(self, node: str, kind: str, vmid: str, *, port: Any, ticket: str) -> str:
        """Das Ticket enthaelt `+`, `/` und `=` -- ohne vollstaendiges Kodieren kommt
        es bei Proxmox veraendert an ("PVEVNC Ticket invalid", im Proxmox-Forum
        mehrfach als Stolperstein dokumentiert)."""
        from urllib.parse import quote

        if self._base_url.startswith("https://"):
            ws_base = "wss://" + self._base_url[len("https://"):]
        elif self._base_url.startswith("http://"):
            ws_base = "ws://" + self._base_url[len("http://"):]
        else:
            ws_base = self._base_url
        return (
            f"{ws_base}/api2/json/nodes/{node}/{kind}/{vmid}/vncwebsocket"
            f"?port={quote(str(port), safe='')}&vncticket={quote(ticket, safe='')}"
        )

    async def open_vnc(self, node: str, kind: str, vmid: str) -> "ProxmoxVncSession":
        """vncproxy + vncwebsocket in einem Zug. Der WebSocket traegt denselben
        `PVEAPIToken`-Header wie jeder REST-Aufruf -- der Browser sieht weder Token
        noch Proxmox, er spricht nur mit Nodvard Deck (`api/v1/console.py` im Kern)."""
        from contextlib import AsyncExitStack

        data = await self.vncproxy(node, kind, vmid)
        ticket = data.get("ticket")
        port = data.get("port")
        if not ticket or port is None:
            raise ProxmoxApiError("vncproxy lieferte kein Ticket/keinen Port.")

        url = self.vncwebsocket_url(node, kind, vmid, port=port, ticket=str(ticket))
        stack = AsyncExitStack()
        try:
            ws = await stack.enter_async_context(
                self._ctx.http.websocket(
                    url,
                    headers=dict(self._auth_header),
                    subprotocols=["binary"],
                    insecure_tls=self._tls_insecure_skip_verify,
                )
            )
        except Exception as exc:  # noqa: BLE001 - wie `_request()`: einheitlich als ProxmoxApiError
            await stack.aclose()
            raise ProxmoxApiError(f"vncwebsocket -> {exc}") from exc
        return ProxmoxVncSession(ws, stack, password=str(data.get("password") or ticket))

    async def task_status(self, node: str, upid: str) -> dict[str, Any]:
        return await self._request("GET", f"/nodes/{node}/tasks/{upid}/status") or {}

    async def wait_for_task(
        self, node: str, upid: str, *, timeout_s: float = 30.0, poll_interval_s: float = 1.0
    ) -> dict[str, Any]:
        """Pollt bis `status` nicht mehr `"running"` ist oder `timeout_s` erreicht ist
        -- gibt in BEIDEN Faellen den letzten bekannten Status zurueck (kein Exception
        bei Timeout, der Aufrufer entscheidet, was "immer noch laufend" bedeutet).
        `exitstatus` ist `"OK"` bei Erfolg, sonst ein Proxmox-Fehlertext."""
        deadline = time.monotonic() + timeout_s
        status = await self.task_status(node, upid)
        while status.get("status") == "running" and time.monotonic() < deadline:
            await asyncio.sleep(poll_interval_s)
            status = await self.task_status(node, upid)
        return status
