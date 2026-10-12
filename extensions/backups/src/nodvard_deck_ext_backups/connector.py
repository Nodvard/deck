"""Dünner Client für die Proxmox-VE-Backup-API, nur über `ctx.http`, nie mit einem eigenen `httpx`-Client."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from .certificate import CHANGED_CODE, INVALID_CODE, INVALID_TEXT, normalize_fingerprint
from .job_edit import VZDUMP_JOB_KEYS, VZDUMP_RETENTION_KEYS
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

# Die Erweiterung führt eine eigene Proxmox-Verbindung: Erweiterungen importieren einander nicht, und die
# Proxmox-Erweiterung bietet keine Capability für Backup-Jobs und Tasks. Wer beide nutzt, trägt dieselbe Instanz
# zweimal ein.

NODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}")
"""Name eines Proxmox-Knotens, so wie er im Pfad einer Anfrage landet."""
TASK_LOG_LINES = 5000
"""Ein Sammel-Job mit vielen Gästen schreibt je Gast gut hundert Zeilen; mehr liest Nodvard Deck nicht."""


class ProxmoxBackupApiError(Exception):
    """`status_code`: HTTP-Status der Antwort, falls es eine gab. `code`: fester Grund (`"certificate_changed"`,
    `"certificate_invalid"`), sonst `None`."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ProxmoxBackupConnector:
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
        self._token_id = token_id
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
        """TLS-Angaben für `ctx.http`: der Fingerabdruck, wenn einer gemerkt ist, sonst der alte Schalter."""
        # Ein ungültiger Eintrag fällt nie auf den Schalter zurück (nie still „nicht prüfen“), sondern sperrt die
        # Verbindung; ein anderes Zertifikat ergibt später den Code "certificate_changed".
        if self._pin_invalid:
            raise ProxmoxBackupApiError(f"TLS -> {INVALID_TEXT}", code=INVALID_CODE)
        if self._pin is not None:
            return {"pinned_sha256": self._pin}
        return {"insecure_tls": self._tls_insecure_skip_verify}

    @property
    def host(self) -> str:
        return urlsplit(self._base_url).hostname or self._base_url

    @property
    def token_id(self) -> str:
        """Die Token-ID (`benutzer@bereich!name`), nie das Geheimnis; für Anleitungen mit fertigem `pveum`-Befehl."""
        return self._token_id

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}/api2/json{path}"
        headers = {**self._auth_header, **kwargs.pop("headers", {})}
        # Weder Ausnahmetext noch Antwort kommen in die Meldung (transport.py). Geworfen wird erst außerhalb des
        # `except`-Blocks, sonst hinge die httpx-Ausnahme (mit Teilen der fremden Antwort) als `__context__` dran.
        if self._pin_invalid:
            raise ProxmoxBackupApiError(f"{method} {path} -> {INVALID_TEXT}", code=INVALID_CODE)
        problem: str | None = None
        code: str | None = None
        cause: BaseException | None = None
        response: Any = None
        try:
            response = await self._ctx.http.request(method, url, headers=headers, **self._tls(), **kwargs)
        except ProxmoxBackupApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - jeder Fehler wird hier vereinheitlicht
            problem = transport_text(exc, url)
            code = CHANGED_CODE if certificate_changed(exc) else None
            cause = None if carries_foreign_text(exc) else exc
        if problem is not None:
            raise ProxmoxBackupApiError(f"{method} {path} -> {problem}", code=code) from cause
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
        """`GET /cluster/backup`: die konfigurierten VZDump-Zeitpläne (entspricht `/etc/pve/jobs.cfg`)."""
        return await self._request("GET", "/cluster/backup") or []

    async def not_backed_up(self) -> list[dict[str, Any]]:
        """`GET /cluster/backup-info/not-backed-up`: Proxmox' eigene Auswertung, welche Gäste kein Job erfasst.

        Sie berücksichtigt `all`, `exclude` und `pool`. Einträge: `vmid`, `name`, `type` ("qemu"/"lxc")."""
        return await self._request("GET", "/cluster/backup-info/not-backed-up") or []

    async def list_nodes(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/nodes") or []

    async def backup_storages(self, node: str) -> list[dict[str, Any]]:
        """Speicher des Knotens, die Backups aufnehmen können (`storage`, `shared`, `active`)."""
        return await self._request("GET", f"/nodes/{node}/storage", params={"content": "backup"}) or []

    async def backup_files(self, node: str, storage: str) -> list[dict[str, Any]]:
        """Backup-Dateien auf einem Speicher (`volid`, `vmid`, `ctime`, `size`)."""
        return await self._request(
            "GET", f"/nodes/{node}/storage/{storage}/content", params={"content": "backup"}, timeout=30.0
        ) or []

    async def guest_resources(self) -> list[dict[str, Any]]:
        """Alle Gäste der Verbindung (`vmid`, `node`, `pool`, `type`), nötig für Jobs ohne VMID-Liste („alle Gäste“, Pool)."""
        return await self._request("GET", "/cluster/resources", params={"type": "vm"}) or []

    async def guest_config(self, node: str, guest_type: str, vmid: str) -> dict[str, Any]:
        """`GET /nodes/{node}/{qemu|lxc}/{vmid}/config`: Plattengrößen für die Platzprüfung (space.py).

        Braucht `VM.Audit`; fehlt das, fällt space.py auf `maxdisk` aus `/cluster/resources` zurück."""
        kind = "lxc" if guest_type == "lxc" else "qemu"
        return await self._request("GET", f"/nodes/{node}/{kind}/{vmid}/config") or {}

    async def list_tasks(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Backup-Tasks (`vzdump`) aller Knoten; ein nicht erreichbarer Knoten wird übersprungen."""
        # Nicht `/cluster/tasks`: das ist ein Ringpuffer der letzten ~25 Tasks, in dem Konsolen (vncproxy) und
        # VM-Starts die vzdump-Einträge verdrängen, ein gesundes Backup stünde dann auf „unbekannt“. Der
        # Knoten-Endpunkt liest den vollen Index und filtert serverseitig; `source=all`, sonst fehlen laufende
        # Backups (Standard ist nur das Archiv beendeter Tasks).
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
        """`POST /nodes/{node}/vzdump`: startet sofort einen Lauf für eine VMID und gibt die UPID zurück.

        Wartet nicht auf das Ende: Erfolg heißt nur „Proxmox hat den Auftrag angenommen“."""
        # `options` sind die Einstellungen des Jobs, es gehen nur Schlüssel aus job_edit mit.
        payload: dict[str, Any] = {"vmid": vmid}
        allowed = (*VZDUMP_JOB_KEYS, *VZDUMP_RETENTION_KEYS)
        for key, value in (options or {}).items():
            if key in allowed:
                payload[key] = value
        if storage and "storage" not in payload:
            payload["storage"] = storage
        # Die Aufbewahrung geht immer mit, fehlt sie, gilt `keep-all=1`. Sonst räumt Proxmox nach der Regel des
        # Speichers auf und kann Sicherungen löschen, die der Job behalten hätte.
        if "prune-backups" not in payload and "maxfiles" not in payload:
            payload["prune-backups"] = "keep-all=1"
        return await self._request("POST", f"/nodes/{node}/vzdump", data=payload)

    # --- Wiederherstellen als Kopie (restore.py) -------------------------------------------------

    async def guest_backup_files(self, node: str, storage: str, vmid: str) -> list[dict[str, Any]]:
        """Nur die Sicherungen eines Gastes auf einem Speicher (Proxmox filtert nach `vmid`)."""
        return await self._request(
            "GET", f"/nodes/{node}/storage/{storage}/content",
            params={"content": "backup", "vmid": vmid}, timeout=30.0,
        ) or []

    async def guest_storages(self, node: str, content: str) -> list[dict[str, Any]]:
        """Speicher des Knotens, die `content` (`images` für VMs, `rootdir` für Container) aufnehmen."""
        return await self._request("GET", f"/nodes/{node}/storage", params={"content": content, "enabled": 1}) or []

    async def storage_status(self, node: str, storage: str) -> dict[str, Any]:
        """`type`, `content`, `active`, `enabled`, `avail`, `total` eines Speichers auf dem Knoten."""
        return await self._request("GET", f"/nodes/{node}/storage/{storage}/status") or {}

    async def next_vmid(self) -> int | None:
        value = await self._request("GET", "/cluster/nextid")
        return int(value) if str(value).isdigit() else None

    async def vmid_is_free(self, vmid: int) -> bool:
        """`GET /cluster/nextid?vmid=N`: Proxmox antwortet mit N, wenn die Nummer im ganzen Cluster frei ist, sonst
        mit 400. Sieht auch Gäste, die der Token nicht lesen darf."""
        try:
            value = await self._request("GET", "/cluster/nextid", params={"vmid": vmid})
        except ProxmoxBackupApiError as exc:
            if exc.status_code == 400:
                return False
            raise
        return str(value) == str(vmid)

    async def backup_config(self, node: str, volid: str) -> str:
        """Die Gast-Einstellungen in der Sicherung, als Text (`GET /nodes/{n}/vzdump/extractconfig`, nur lesend)."""
        value = await self._request("GET", f"/nodes/{node}/vzdump/extractconfig", params={"volume": volid}, timeout=60.0)
        return value if isinstance(value, str) else ""

    async def restore_guest(self, node: str, kind: str, params: dict[str, Any]) -> str:
        """`POST /nodes/{n}/qemu` bzw. `/lxc` mit Sicherung, gibt die UPID zurück.

        Nur Schlüssel aus `RESTORE_KEYS` (nie `force`, `start`, `live-restore`); der Pool ist immer `COPY_POOL`."""
        allowed = RESTORE_KEYS["lxc" if kind == "lxc" else "qemu"]
        payload = {key: value for key, value in params.items() if key in allowed}
        payload["pool"] = COPY_POOL
        path = f"/nodes/{node}/{'lxc' if kind == 'lxc' else 'qemu'}"
        return await self._request("POST", path, data=payload)

    async def task_status(self, node: str, upid: str) -> dict[str, Any]:
        # Die UPID landet im Pfad, daher vorher gegen das feste Muster prüfen.
        if not UPID_RE.fullmatch(upid):
            raise ProxmoxBackupApiError("Ungültige Aufgaben-Kennung von Proxmox.")
        return await self._request("GET", f"/nodes/{node}/tasks/{upid}/status") or {}

    async def task_log(self, node: str, upid: str, *, limit: int = TASK_LOG_LINES) -> list[str]:
        """Das Protokoll einer Aufgabe als Zeilen (`GET /nodes/{node}/tasks/{upid}/log`, höchstens `limit`).

        Für fremde Aufgaben wie die nächtlichen Backups braucht der Zugang `Sys.Audit` auf dem Knoten."""
        # UPID und Knotenname landen im Pfad, daher vorher gegen feste Muster prüfen.
        if not UPID_RE.fullmatch(upid) or not NODE_RE.fullmatch(node):
            raise ProxmoxBackupApiError("Ungültige Aufgaben-Kennung von Proxmox.")
        rows = await self._request("GET", f"/nodes/{node}/tasks/{upid}/log", params={"start": 0, "limit": limit}) or []
        return [str(row.get("t") or "") for row in rows if isinstance(row, dict)]

    async def restore_tasks(self, node: str, vmid: int, worker: str) -> list[dict[str, Any]]:
        """Wiederherstellungs-Aufgaben (`qmrestore`/`vzrestore`) zu einer Nummer, um eine Kopie wiederzufinden,
        deren Kennung nie ankam (Verbindung beim Anlegen abgerissen)."""
        return await self._request(
            "GET", f"/nodes/{node}/tasks", params={"typefilter": worker, "vmid": vmid, "source": "all", "limit": 50},
        ) or []

    async def update_guest_config(self, node: str, kind: str, vmid: str, params: dict[str, Any]) -> Any:
        """`PUT .../config` nur für die neu angelegte Kopie (restore.py prüft die Nummer vorher) und nur mit den
        Schlüsseln aus `FINISH_KEYS` und `net<N>`."""
        payload = {k: v for k, v in params.items() if k in FINISH_KEYS or NET_KEY_RE.fullmatch(k)}
        return await self._request("PUT", f"/nodes/{node}/{'lxc' if kind == 'lxc' else 'qemu'}/{vmid}/config", data=payload)

    async def permissions(self, path: str) -> dict[str, Any]:
        """Die wirksamen Rechte des Tokens auf `path` (`GET /access/permissions`, jeder darf das für sich selbst).

        Ergebnis wie `{"VM.Allocate": 1, ...}`, leer ohne Rechte."""
        data = await self._request("GET", "/access/permissions", params={"path": path}) or {}
        rights = data.get(path) if isinstance(data, dict) else None
        return rights if isinstance(rights, dict) else {}

    async def version(self) -> str:
        data = await self._request("GET", "/version") or {}
        return str(data.get("version") or "") if isinstance(data, dict) else ""

    async def ha_resources(self) -> list[dict[str, Any]]:
        """`GET /cluster/ha/resources` (`Sys.Audit` auf `/`): HA-Regeln, die einen Gast von selbst starten können."""
        return await self._request("GET", "/cluster/ha/resources") or []


UPID_RE = re.compile(
    r"UPID:[A-Za-z0-9-]+:[0-9A-Fa-f]{8,16}:[0-9A-Fa-f]{8,16}:[0-9A-Fa-f]{8,16}:[A-Za-z0-9_-]+:[A-Za-z0-9_.-]*:[^\s:/?#%]+:"
)
"""Aufgaben-Kennung von Proxmox (`UPID:<knoten>:<pid>:<pstart>:<start>:<art>:<nummer>:<benutzer>:`). Sie landet im Pfad
einer Anfrage, darum nichts, was den Pfad verlassen oder eine Abfrage anhängen könnte (`/`, `?`, `#`, `%`)."""
NET_KEY_RE = re.compile(r"net\d+")
FINISH_KEYS = frozenset({"onboot", "name", "hostname", "description", "digest"})

COPY_POOL = "nodvard-kopien"
"""Der Pool, in dem jede Kopie entsteht. Fest, nie aus einer Anfrage oder dem Payload der Aktion: der Zugang hat
`VM.Allocate` nur auf diesem Pool, so kann Nodvard Deck nirgends sonst einen Gast anlegen. Die Proxmox-Erweiterung
legt Pool und Rechte an und trägt denselben Namen selbst ein (kein Import zwischen Erweiterungen)."""

RESTORE_KEYS: dict[str, frozenset[str]] = {
    "qemu": frozenset({"vmid", "archive", "storage", "unique", "name", "onboot", "pool"}),
    "lxc": frozenset({"vmid", "ostemplate", "restore", "storage", "unique", "hostname", "onboot", "pool"}),
}
"""Was beim Wiederherstellen als Kopie an Proxmox geht; `pool` setzt `restore_guest` selbst auf `COPY_POOL`.
Bewusst nicht dabei: `force` (überschriebe einen vorhandenen Gast mit derselben Nummer), `start`, `live-restore`,
`ha-managed`."""
