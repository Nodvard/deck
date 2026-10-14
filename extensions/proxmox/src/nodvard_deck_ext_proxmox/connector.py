"""Dünner Client für die Proxmox-VE-REST-API (`/api2/json/...`)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from .certificate import CHANGED_CODE, INVALID_CODE, INVALID_TEXT, normalize_fingerprint
from .transport import (
    carries_foreign_text,
    certificate_changed,
    http_error_text,
    json_body,
    redirect_text,
    transport_text,
    unexpected_format_text,
)

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


class ProxmoxApiError(Exception):
    """`status_code`: HTTP-Status der Antwort (falls es eine gab), `code`: fester Grund wie `"certificate_changed"`.
    `readable`: der Text ist ein Satz der Erweiterung (höchstens der Grund von Proxmox ist fremd, gekürzt in
    `transport.reason_from`); der Kern zeigt ihn unverändert, als 502 mit Grund statt 500."""

    readable = True

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ProxmoxVncSession:
    """Erfüllt `nodvard_sdk.capabilities.ConsoleSession`: ein offener `vncwebsocket`, reine Byte-Weitergabe (RFB);
    das Protokoll sprechen noVNC im Browser und der VNC-Server auf dem Knoten."""

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
    """Alle Aufrufe laufen über `ctx.http`, das die `net.outbound`-Berechtigungen durchsetzt; ein eigener Client würde sie
    umgehen. Authentifizierung per API-Token (`PVEAPIToken=user@realm!name=secret`), daher kein Session-Refresh."""

    def __init__(
        self,
        ctx: "ExtensionContext",
        *,
        base_url: str,
        token_id: str,
        token_secret: str,
        tls_insecure_skip_verify: bool = False,
        tls_fingerprint_sha256: str | None = None,
    ) -> None:
        self._ctx = ctx
        self._base_url = base_url.rstrip("/")
        self._auth_header = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        self._tls_insecure_skip_verify = tls_insecure_skip_verify
        self._pin: str | None = None
        self._pin_invalid = False
        try:
            self._pin = normalize_fingerprint(tls_fingerprint_sha256)
        except ValueError:
            self._pin_invalid = True

    @property
    def pinned(self) -> bool:
        """Ob die Verbindung an einen gemerkten Fingerabdruck gebunden ist."""
        return self._pin is not None

    def _tls(self) -> dict[str, Any]:
        """TLS-Angaben für `ctx.http`: der gemerkte Fingerabdruck (`pinned_sha256`, auch für den Konsolen-WebSocket),
        sonst der alte Schalter `insecure_tls` (gilt nur für diese Instanz). Ein ungültiger Eintrag fällt nie auf den
        Schalter zurück, sondern wirft `ProxmoxApiError`."""
        self._refuse_invalid_pin("TLS")
        if self._pin is not None:
            return {"pinned_sha256": self._pin}
        return {"insecure_tls": self._tls_insecure_skip_verify}

    def _refuse_invalid_pin(self, what: str) -> None:
        if self._pin_invalid:
            raise ProxmoxApiError(f"{what} -> {INVALID_TEXT}", code=INVALID_CODE)

    @property
    def host(self) -> str:
        """Nur für `Host.address`: der Proxmox-API-Host selbst, ohne Schema und Port."""
        from urllib.parse import urlsplit

        return urlsplit(self._base_url).hostname or self._base_url

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}/api2/json{path}"
        headers = {**self._auth_header, **kwargs.pop("headers", {})}
        # Netzwerkfehler werden einheitlich zu `ProxmoxApiError` (Aufrufer fangen nur diesen und `RuntimeError`, eine rohe
        # httpx-Ausnahme gäbe einen 500). Geworfen wird erst außerhalb des `except`, sonst hinge die httpx-Ausnahme samt
        # Teilen der fremden Antwort als `__context__` an der Meldung.
        self._refuse_invalid_pin(f"{method} {path}")
        problem: str | None = None
        code: str | None = None
        cause: BaseException | None = None
        response: Any = None
        try:
            response = await self._ctx.http.request(method, url, headers=headers, **self._tls(), **kwargs)
        except ProxmoxApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - jeder Netzwerkfehler wird hier vereinheitlicht
            problem = transport_text(exc, url)
            code = CHANGED_CODE if certificate_changed(exc) else None
            cause = None if carries_foreign_text(exc) else exc
        if problem is not None:
            raise ProxmoxApiError(f"{method} {path} -> {problem}", code=code) from cause
        if response.status_code >= 400:
            raise ProxmoxApiError(http_error_text(method, path, response), status_code=response.status_code)
        if 300 <= response.status_code < 400:
            # `ctx.http` folgt keinen Weiterleitungen: die 3xx-Antwort kommt hier an. Die Adresse
            # steht bewusst nicht im Text (sie stammt vom Server).
            raise ProxmoxApiError(redirect_text(response.status_code), status_code=response.status_code)
        body = json_body(response)
        if not isinstance(body, dict):
            raise ProxmoxApiError(unexpected_format_text(response.status_code))
        return body.get("data")

    async def version(self) -> dict[str, Any]:
        return await self._request("GET", "/version")

    async def user_tokens(self, userid: str) -> Any:
        """Die API-Tokens eines Benutzers aus `GET /access/users/{userid}` (`tokens`: ID -> privsep, Ablauf, Kommentar;
        nie ein Geheimnis). Braucht nur `Sys.Audit`; `GET .../token` verlangt von einem Token `User.Modify`, weil
        „der eigene Benutzer“ für eine Token-ID nicht gilt."""
        from urllib.parse import quote

        data = await self._request("GET", f"/access/users/{quote(userid, safe='@')}") or {}
        return data.get("tokens") if isinstance(data, dict) else None

    async def list_nodes(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/nodes") or []

    async def node_status(self, node: str) -> dict[str, Any]:
        return await self._request("GET", f"/nodes/{node}/status") or {}

    async def list_qemu(self, node: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/nodes/{node}/qemu") or []

    async def qemu_status(self, node: str, vmid: str) -> dict[str, Any]:
        return await self._request("GET", f"/nodes/{node}/qemu/{vmid}/status/current") or {}

    async def qemu_action(self, node: str, vmid: str, action: str, params: dict[str, Any] | None = None) -> str:
        """`action` in {"start", "shutdown", "stop", "reboot"} (Proxmox-Endpunktnamen; `shutdown` fährt geordnet herunter,
        `stop` schaltet hart aus). `params` geht als JSON-Body mit, z. B. `{"timeout": 180}` beim Herunterfahren, sonst
        bricht Proxmox den Task nach etwa 60 s ab."""
        kwargs: dict[str, Any] = {"json": params} if params else {}
        return await self._request("POST", f"/nodes/{node}/qemu/{vmid}/status/{action}", **kwargs)

    async def qemu_snapshot(self, node: str, vmid: str, snapname: str) -> str:
        return await self._request("POST", f"/nodes/{node}/qemu/{vmid}/snapshot", json={"snapname": snapname})

    # -- LXC (fast identisch zu /qemu/...) ------------------------------------------------------

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

    # -- Konfiguration, Messreihen, Snapshots ------------------------------------------------

    async def guest_config(self, node: str, kind: str, vmid: str) -> dict[str, Any]:
        """Rohkonfiguration eines Gasts. Enthält ggf. `cipassword`/`sshkeys`: nie ungefiltert weitergeben, siehe
        guest_config.summarize_config (Positivliste)."""
        return await self._request("GET", f"/nodes/{node}/{kind}/{vmid}/config") or {}

    async def rrddata(self, node: str, kind: str, vmid: str | None, timeframe: str) -> list[dict[str, Any]]:
        """Proxmox' eigene Messreihen (RRD): `timeframe` hour|day|week|month|year, Mittelwerte je Stützstelle. Knoten:
        `/nodes/{node}/rrddata`, Gäste zusätzlich `/{qemu|lxc}/{vmid}`."""
        path = f"/nodes/{node}/rrddata" if kind == "node" else f"/nodes/{node}/{kind}/{vmid}/rrddata"
        return await self._request("GET", path, params={"timeframe": timeframe, "cf": "AVERAGE"}) or []

    async def apt_refresh(self, node: str) -> str:
        """`apt update` des Knotens (Paketlisten neu laden), liefert eine UPID."""
        return await self._request("POST", f"/nodes/{node}/apt/update")

    async def node_reboot(self, node: str) -> Any:
        return await self._request("POST", f"/nodes/{node}/status", json={"command": "reboot"})

    async def update_guest_config(self, node: str, kind: str, vmid: str, params: dict[str, Any]) -> Any:
        """PUT ist synchron (für QEMU gäbe es auch POST, asynchron mit Task). Nur mit Parametern aus
        guest_edit.build_update, nie mit Nutzereingaben direkt."""
        return await self._request("PUT", f"/nodes/{node}/{kind}/{vmid}/config", json=params)

    async def guest_pending(self, node: str, kind: str, vmid: str) -> list[dict[str, Any]]:
        """Aktuelle und vorgemerkte Werte: was erst nach einem Neustart greift."""
        return await self._request("GET", f"/nodes/{node}/{kind}/{vmid}/pending") or []

    async def list_snapshots(self, node: str, kind: str, vmid: str) -> list[dict[str, Any]]:
        """`kind` in {"qemu", "lxc"}. Enthält immer den Pseudo-Eintrag "current" (der laufende Zustand), der Aufrufer
        filtert ihn."""
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

    # -- Datenträger (Knoten-Gesundheit, rein lesend) ---------------------------------------

    async def node_disks(self, node: str) -> list[dict[str, Any]]:
        """Physische Datenträger des Knotens mit SMART-Kurzbefund (`health`) und Abnutzung (`wearout` = Restlebensdauer
        in %, bei manchen SSDs "N/A")."""
        return await self._request("GET", f"/nodes/{node}/disks/list", timeout=30.0) or []

    async def node_zfs(self, node: str) -> list[dict[str, Any]]:
        """ZFS-Pools des Knotens mit `health` (ONLINE, DEGRADED, FAULTED ...), `size`, `alloc`, `free`, `frag`."""
        return await self._request("GET", f"/nodes/{node}/disks/zfs", timeout=30.0) or []

    async def disk_smart(self, node: str, devpath: str) -> dict[str, Any]:
        """SMART-Details eines Datenträgers: NVMe als Freitext, SATA als Attributliste."""
        return await self._request("GET", f"/nodes/{node}/disks/smart", params={"disk": devpath}, timeout=30.0) or {}

    # -- Paket-Updates (rein lesend) -----------------------------------------------------

    async def apt_updates(self, node: str) -> list[dict[str, Any]]:
        """Wartende Pakete laut Proxmox' letzter Prüfung. Liest nur: `POST` auf denselben Pfad würde die Paketlisten neu
        laden, das tut Nodvard Deck hier nicht. Der Timeout ist großzügig, weil Proxmox hier sporadisch länger braucht."""
        return await self._request("GET", f"/nodes/{node}/apt/update", timeout=30.0) or []

    async def apt_versions(self, node: str) -> list[dict[str, Any]]:
        """Installierte Proxmox-Pakete inkl. aller Kernel-Images (für "Neustart ausstehend")."""
        return await self._request("GET", f"/nodes/{node}/apt/versions", timeout=30.0) or []

    # -- Speicher (Speicher-Übersicht) ---------------------------------------------------------

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
        """Schnittstellen einer VM über den qemu-guest-agent. Ohne laufenden Agenten antwortet Proxmox mit HTTP 500
        ("QEMU guest agent is not running"); das wird zu `ProxmoxApiError`, der Aufrufer behandelt es als "unbekannt"."""
        data = await self._request("GET", f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces", timeout=5.0) or {}
        return list(data.get("result") or [])

    # -- Konsole (Bildschirm einer VM/eines Containers in Nodvard Deck) ----------------

    async def vncproxy(self, node: str, kind: str, vmid: str) -> dict[str, Any]:
        """`kind` in {"qemu", "lxc"}. Startet auf dem Knoten einen kurzlebigen VNC-Proxy (wartet ~10 s auf die
        Gegenverbindung) und liefert `port` und `ticket`; `websocket=1` wie in Proxmox' eigener Oberfläche."""
        body: dict[str, Any] = {"websocket": 1}
        if kind == "qemu":
            # Sonst ist das VNC-Kennwort das Ticket selbst, QEMUs VNC-Auth nutzt davon aber nur die ersten 8 Zeichen;
            # Proxmox erzeugt stattdessen ein Einmal-Kennwort (`password`). Der LXC-Endpunkt bekommt den Parameter nicht,
            # liefert aber ebenfalls ein eigenes `password`; fehlt es, bleibt das Ticket das Kennwort.
            body["generate-password"] = 1
        # Proxmox startet dafür auf dem Knoten einen eigenen Prozess, das kann länger als httpx' 5-s-Standard dauern
        # (einmal bei einer GPU-VM beobachtet), daher 20 s.
        return await self._request(
            "POST", f"/nodes/{node}/{kind}/{vmid}/vncproxy", json=body, timeout=20.0
        ) or {}

    def vncwebsocket_url(self, node: str, kind: str, vmid: str, *, port: Any, ticket: str) -> str:
        """Das Ticket enthält `+`, `/` und `=`: ohne vollständiges Kodieren kommt es bei Proxmox verändert an
        ("PVEVNC Ticket invalid")."""
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
        """vncproxy und vncwebsocket in einem Zug. Der WebSocket trägt denselben `PVEAPIToken`-Header wie jeder REST-Aufruf;
        der Browser sieht weder Token noch Proxmox, er spricht nur mit Nodvard Deck (`api/v1/console.py` im Kern)."""
        from contextlib import AsyncExitStack

        data = await self.vncproxy(node, kind, vmid)
        ticket = data.get("ticket")
        port = data.get("port")
        if not ticket or port is None:
            raise ProxmoxApiError("vncproxy lieferte kein Ticket/keinen Port.")

        url = self.vncwebsocket_url(node, kind, vmid, port=port, ticket=str(ticket))
        self._refuse_invalid_pin("vncwebsocket")
        stack = AsyncExitStack()
        problem: str | None = None
        code: str | None = None
        try:
            ws = await stack.enter_async_context(
                self._ctx.http.websocket(url, headers=dict(self._auth_header), subprotocols=["binary"], **self._tls())
            )
        except Exception as exc:  # noqa: BLE001 - wie `_request()`: einheitlich als ProxmoxApiError
            # Nie der Text der Ausnahme: `websockets` nennt in `InvalidURI` die ganze Adresse samt
            # `vncticket` und zitiert bei einer kaputten Antwort Kopfzeilen des Servers.
            problem = transport_text(exc, url)
            code = CHANGED_CODE if certificate_changed(exc) else None
        if problem is not None:
            await stack.aclose()
            raise ProxmoxApiError(f"vncwebsocket -> {problem}", code=code)
        return ProxmoxVncSession(ws, stack, password=str(data.get("password") or ticket))

    async def task_status(self, node: str, upid: str) -> dict[str, Any]:
        return await self._request("GET", f"/nodes/{node}/tasks/{upid}/status") or {}

    async def wait_for_task(
        self, node: str, upid: str, *, timeout_s: float = 30.0, poll_interval_s: float = 1.0
    ) -> dict[str, Any]:
        """Pollt den Task-Status, bis er nicht mehr `"running"` ist oder `timeout_s` abläuft (Proxmox beantwortet
        Aktionen nur mit einer UPID, nicht mit dem Ergebnis). Gibt in beiden Fällen den letzten Status zurück, bei
        Timeout ohne Fehler; `exitstatus` ist `"OK"` bei Erfolg, sonst ein Proxmox-Fehlertext."""
        deadline = time.monotonic() + timeout_s
        status = await self.task_status(node, upid)
        while status.get("status") == "running" and time.monotonic() < deadline:
            await asyncio.sleep(poll_interval_s)
            status = await self.task_status(node, upid)
        return status
