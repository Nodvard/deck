"""Duenner Client fuer die Proxmox-VE-Backup-API, analog zu
`nodvard_deck_ext_proxmox.connector.ProxmoxConnector` -- nutzt ausschliesslich `ctx.http`,
niemals einen eigenen `httpx`-Client.

**Bewusst eine EIGENE, unabhaengige Proxmox-Verbindung statt Wiederverwendung der
proxmox-Extension:** Extensions duerfen sich laut docs/02-EXTENSION-API.md Paragraph 2
nur ueber `ctx.events`/deklarierte `requires` + typisierte Capabilities sehen, nicht
sich gegenseitig direkt importieren -- und proxmox bietet keine Capability, die
Backup-Job-/Task-Daten liefert (nur `HostProvider`/`MetricsProvider`/`ActionExecutor`).
Diese Extension baut deshalb ihren eigenen, kleinen REST-Client -- derselbe
Kompromiss wie bei nextclouds eigenem WebDAV-Client trotz proxmoxs bereits
vorhandenem `ctx.http`-Nutzungsmuster. Konsequenz: wer beide Extensions nutzt,
konfiguriert dieselbe Proxmox-Instanz zweimal.

**`tls_insecure_skip_verify` (Nachtrag, live gegen den echten pve1 gefunden):**
dieselbe eigenstaendige Verbindung heisst auch, dass der WP-8-TLS-Fix
(`net.outbound.insecure_tls`, siehe `nodvard_deck_ext_proxmox.connector`) diese
Extension NICHT automatisch mitdeckt -- eine unabhaengige, aber strukturell
identische Ergaenzung hier."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from .job_edit import VZDUMP_JOB_KEYS, VZDUMP_RETENTION_KEYS
from .transport import (
    carries_foreign_text,
    http_error_text,
    json_body,
    redirect_text,
    transport_text,
    unexpected_format_text,
)

if TYPE_CHECKING:
    from nodvard_sdk import ExtensionContext


class ProxmoxBackupApiError(Exception):
    """`status_code`: der HTTP-Status der Antwort, falls es eine gab (sonst `None`)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProxmoxBackupConnector:
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
        return urlsplit(self._base_url).hostname or self._base_url

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}/api2/json{path}"
        headers = {**self._auth_header, **kwargs.pop("headers", {})}
        # Nie der Text der Ausnahme oder der Antwort in der Meldung (siehe transport.py), und der Fehler wird erst
        # ausserhalb des `except`-Blocks geworfen: sonst hinge die Ausnahme von httpx (mit Teilen der fremden
        # Antwort im Text) als `__context__` an der Meldung.
        problem: str | None = None
        cause: BaseException | None = None
        response: Any = None
        try:
            response = await self._ctx.http.request(
                method, url, headers=headers, insecure_tls=self._tls_insecure_skip_verify, **kwargs
            )
        except ProxmoxBackupApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - jeder Fehler wird hier vereinheitlicht (wie ProxmoxConnector)
            problem = transport_text(exc, url)
            cause = None if carries_foreign_text(exc) else exc
        if problem is not None:
            raise ProxmoxBackupApiError(f"{method} {path} -> {problem}") from cause
        if response.status_code >= 400:
            raise ProxmoxBackupApiError(http_error_text(method, path, response), status_code=response.status_code)
        if 300 <= response.status_code < 400:
            # `ctx.http` folgt keinen Weiterleitungen: die 3xx-Antwort kommt hier an. Die Adresse
            # steht bewusst nicht im Text (sie stammt vom Server).
            raise ProxmoxBackupApiError(redirect_text(response.status_code), status_code=response.status_code)
        body = json_body(response)
        if not isinstance(body, dict):
            raise ProxmoxBackupApiError(unexpected_format_text(response.status_code))
        return body.get("data")

    async def list_jobs(self) -> list[dict[str, Any]]:
        """`GET /cluster/backup` -- die konfigurierten VZDump-Zeitplaene
        (Proxmox-Aequivalent von `/etc/pve/jobs.cfg`)."""
        return await self._request("GET", "/cluster/backup") or []

    async def not_backed_up(self) -> list[dict[str, Any]]:
        """`GET /cluster/backup-info/not-backed-up` -- Proxmox' EIGENE Auswertung, welche
        Gaeste von keinem Backup-Job erfasst sind (beruecksichtigt `all`/`exclude`/
        `pool`, die hier sonst nachgebaut werden muessten). Eintraege: `vmid`, `name`,
        `type` ("qemu"/"lxc")."""
        return await self._request("GET", "/cluster/backup-info/not-backed-up") or []

    async def list_nodes(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/nodes") or []

    async def backup_storages(self, node: str) -> list[dict[str, Any]]:
        """Speicher des Knotens, die Backups aufnehmen koennen (`storage`, `shared`, `active`)."""
        return await self._request("GET", f"/nodes/{node}/storage", params={"content": "backup"}) or []

    async def backup_files(self, node: str, storage: str) -> list[dict[str, Any]]:
        """Backup-Dateien auf einem Speicher (`volid`, `vmid`, `ctime`, `size`)."""
        return await self._request(
            "GET", f"/nodes/{node}/storage/{storage}/content", params={"content": "backup"}, timeout=30.0
        ) or []

    async def guest_resources(self) -> list[dict[str, Any]]:
        """Alle Gaeste der Verbindung (`vmid`, `node`, `pool`, `type`) -- nur noetig,
        um Jobs ohne explizite VMID-Liste ("alle Gaeste", Pool) aufzuloesen."""
        return await self._request("GET", "/cluster/resources", params={"type": "vm"}) or []

    async def guest_config(self, node: str, guest_type: str, vmid: str) -> dict[str, Any]:
        """`GET /nodes/{node}/{qemu|lxc}/{vmid}/config` -- Plattengroessen fuer die
        Platz-Pruefung (space.py). Braucht `VM.Audit`; fehlt das, faellt space.py auf
        `maxdisk` aus `/cluster/resources` zurueck."""
        kind = "lxc" if guest_type == "lxc" else "qemu"
        return await self._request("GET", f"/nodes/{node}/{kind}/{vmid}/config") or {}

    async def list_tasks(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Backup-Tasks (`vzdump`) aus dem VOLLSTAENDIGEN Task-Verlauf jedes Knotens
        (`GET /nodes/{node}/tasks?typefilter=vzdump&source=all`).

        **Live gefunden (ein laufendes Backup stand auf "unbekannt"):** vorher
        `GET /cluster/tasks` -- das ist ein RINGPUFFER der letzten
        ~25 Tasks des ganzen Knotens. Jede Konsolen-Oeffnung (vncproxy), jeder
        VM-Start legt dort einen Eintrag an; auf pve1 waren 17 von 25 Eintraegen
        vncproxy, kein einziger vzdump mehr -- ein gesundes Backup wurde als
        "unbekannt" gemeldet, abhaengig davon, wie viel sonst auf dem Knoten los war.
        Der Knoten-Endpunkt liest den vollen Task-Index und filtert serverseitig.
        `source=all`: sonst fehlen gerade LAUFENDE Backups (Default ist nur das
        Archiv abgeschlossener Tasks).

        Ein nicht erreichbarer Knoten wird uebersprungen, nicht zum Fehler fuer alle
        anderen."""
        nodes = await self._request("GET", "/nodes") or []
        tasks: list[dict[str, Any]] = []
        for entry in nodes:
            node = entry.get("node")
            if not node:
                continue
            try:
                rows = await self._request(
                    "GET", f"/nodes/{node}/tasks",
                    params={"typefilter": "vzdump", "source": "all", "limit": limit},
                ) or []
            except ProxmoxBackupApiError:
                continue
            for row in rows:
                row.setdefault("node", node)
            tasks.extend(rows)
        return tasks

    async def get_job(self, job_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/cluster/backup/{job_id}") or {}

    async def create_job(self, params: dict[str, Any]) -> Any:
        """Nur mit Parametern aus job_edit.build_job_create."""
        return await self._request("POST", "/cluster/backup", json=params)

    async def update_job(self, job_id: str, params: dict[str, Any]) -> Any:
        """Nur mit Parametern aus job_edit.build_job_update -- nie mit Nutzereingaben direkt."""
        return await self._request("PUT", f"/cluster/backup/{job_id}", json=params)

    async def run_vzdump(
        self, node: str, vmid: str, *, storage: str | None = None, options: dict[str, Any] | None = None
    ) -> str:
        """`POST /nodes/{node}/vzdump` -- startet einen sofortigen Backup-Lauf fuer
        EINE VMID. Gibt die UPID (Proxmox-Task-Kennung) zurueck. **Ehrlich
        abgegrenzt** (wie proxmoxs `qemu_action()`): wartet NICHT auf Abschluss des
        Tasks -- ein Erfolg hier heisst "Proxmox hat den Auftrag angenommen", nicht
        "das Backup ist fertig".

        `options` sind die Einstellungen des Jobs (nur die Schluessel aus job_edit). Eine
        Aufbewahrung wird immer mitgeschickt: fehlt sie, gilt `keep-all=1`. Ohne sie raeumt
        Proxmox nach der Regel des Speichers auf und kann Sicherungen loeschen, die der Job
        behalten haette."""
        payload: dict[str, Any] = {"vmid": vmid}
        allowed = (*VZDUMP_JOB_KEYS, *VZDUMP_RETENTION_KEYS)
        for key, value in (options or {}).items():
            if key in allowed:
                payload[key] = value
        if storage and "storage" not in payload:
            payload["storage"] = storage
        if "prune-backups" not in payload and "maxfiles" not in payload:
            payload["prune-backups"] = "keep-all=1"
        return await self._request("POST", f"/nodes/{node}/vzdump", data=payload)
