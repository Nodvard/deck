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
identische Ergaenzung hier.

**Gemerkter Fingerabdruck (`tls_fingerprint_sha256`, certificate.py):** wie in "Proxmox VE": ist einer gemerkt, geht
er als `pinned_sha256` in jeden Aufruf, und der veraltete Schalter gilt nicht. Ein anderes Zertifikat ergibt
`ProxmoxBackupApiError` mit `code == "certificate_changed"`, ein ungueltiger Eintrag sperrt die Verbindung
(`code == "certificate_invalid"`), nie still "nicht pruefen"."""

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


NODE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}")
"""Name eines Proxmox-Knotens, so wie er im Pfad einer Anfrage landet."""
TASK_LOG_LINES = 5000
"""Ein Sammel-Job mit vielen Gaesten schreibt je Gast gut hundert Zeilen; mehr liest Nodvard Deck nicht."""


class ProxmoxBackupApiError(Exception):
    """`status_code`: der HTTP-Status der Antwort, falls es eine gab (sonst `None`). `code`: fester Grund, wo es einen
    gibt (`"certificate_changed"`, `"certificate_invalid"`), sonst `None`."""

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
        """Die TLS-Angaben fuer `ctx.http`: der Fingerabdruck, wenn einer gemerkt ist, sonst der alte Schalter. Bei
        einem ungueltigen Eintrag nie der Schalter: dann `ProxmoxBackupApiError` (auch fuer kuenftige Aufrufer)."""
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
        """Die Token-ID (`benutzer@bereich!name`), nie das Geheimnis -- fuer Anleitungen mit einem fertigen `pveum`-Befehl."""
        return self._token_id

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}/api2/json{path}"
        headers = {**self._auth_header, **kwargs.pop("headers", {})}
        # Nie der Text der Ausnahme oder der Antwort in der Meldung (siehe transport.py), und der Fehler wird erst
        # ausserhalb des `except`-Blocks geworfen: sonst hinge die Ausnahme von httpx (mit Teilen der fremden
        # Antwort im Text) als `__context__` an der Meldung.
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
        except Exception as exc:  # noqa: BLE001 - jeder Fehler wird hier vereinheitlicht (wie ProxmoxConnector)
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

    # --- Wiederherstellen als Kopie (restore.py) -------------------------------------------------

    async def guest_backup_files(self, node: str, storage: str, vmid: str) -> list[dict[str, Any]]:
        """Nur die Sicherungen EINES Gastes auf einem Speicher (Proxmox filtert selbst nach `vmid`)."""
        return await self._request(
            "GET", f"/nodes/{node}/storage/{storage}/content",
            params={"content": "backup", "vmid": vmid}, timeout=30.0,
        ) or []

    async def guest_storages(self, node: str, content: str) -> list[dict[str, Any]]:
        """Speicher des Knotens, die `content` (`images` fuer VMs, `rootdir` fuer Container) aufnehmen."""
        return await self._request("GET", f"/nodes/{node}/storage", params={"content": content, "enabled": 1}) or []

    async def storage_status(self, node: str, storage: str) -> dict[str, Any]:
        """`type`, `content`, `active`, `enabled`, `avail`, `total` eines Speichers auf dem Knoten."""
        return await self._request("GET", f"/nodes/{node}/storage/{storage}/status") or {}

    async def next_vmid(self) -> int | None:
        value = await self._request("GET", "/cluster/nextid")
        return int(value) if str(value).isdigit() else None

    async def vmid_is_free(self, vmid: int) -> bool:
        """`GET /cluster/nextid?vmid=N`: Proxmox antwortet mit N, wenn die Nummer im ganzen Cluster frei
        ist, sonst mit 400 ("VM N already exists"). Sieht auch Gaeste, die der Token nicht lesen darf."""
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
        """`POST /nodes/{n}/qemu` bzw. `/lxc` mit Sicherung -> UPID. Nur die Schluessel aus `RESTORE_KEYS`; `force`
        (ueberschreibt einen vorhandenen Gast), `start` und `live-restore` gehen hier nie mit, egal, was ankommt.
        Der Pool ist immer `COPY_POOL`, auch wenn `params` einen anderen nennt."""
        allowed = RESTORE_KEYS["lxc" if kind == "lxc" else "qemu"]
        payload = {key: value for key, value in params.items() if key in allowed}
        payload["pool"] = COPY_POOL
        path = f"/nodes/{node}/{'lxc' if kind == 'lxc' else 'qemu'}"
        return await self._request("POST", path, data=payload)

    async def task_status(self, node: str, upid: str) -> dict[str, Any]:
        if not UPID_RE.fullmatch(upid):
            raise ProxmoxBackupApiError("Ungültige Aufgaben-Kennung von Proxmox.")
        return await self._request("GET", f"/nodes/{node}/tasks/{upid}/status") or {}

    async def task_log(self, node: str, upid: str, *, limit: int = TASK_LOG_LINES) -> list[str]:
        """Das Protokoll einer Aufgabe als Zeilen (`GET /nodes/{node}/tasks/{upid}/log`, hoechstens `limit` Zeilen).
        Fuer fremde Aufgaben wie die naechtlichen Backups braucht der Zugang `Sys.Audit` auf dem Knoten."""
        if not UPID_RE.fullmatch(upid) or not NODE_RE.fullmatch(node):
            raise ProxmoxBackupApiError("Ungültige Aufgaben-Kennung von Proxmox.")
        rows = await self._request("GET", f"/nodes/{node}/tasks/{upid}/log", params={"start": 0, "limit": limit}) or []
        return [str(row.get("t") or "") for row in rows if isinstance(row, dict)]

    async def restore_tasks(self, node: str, vmid: int, worker: str) -> list[dict[str, Any]]:
        """Wiederherstellungs-Aufgaben (`qmrestore`/`vzrestore`) fuer eine Nummer -- um eine Kopie wiederzufinden,
        deren Aufgaben-Kennung nie ankam (Verbindung beim Anlegen abgerissen)."""
        return await self._request(
            "GET", f"/nodes/{node}/tasks", params={"typefilter": worker, "vmid": vmid, "source": "all", "limit": 50},
        ) or []

    async def update_guest_config(self, node: str, kind: str, vmid: str, params: dict[str, Any]) -> Any:
        """`PUT .../config` -- nur fuer die neu angelegte Kopie (restore.py prueft die Nummer vorher) und nur die
        Schluessel, die das Fertig-Einrichten braucht (`FINISH_KEYS`, Netzwerkkarten `net<N>`)."""
        payload = {k: v for k, v in params.items() if k in FINISH_KEYS or NET_KEY_RE.fullmatch(k)}
        return await self._request("PUT", f"/nodes/{node}/{'lxc' if kind == 'lxc' else 'qemu'}/{vmid}/config", data=payload)

    async def permissions(self, path: str) -> dict[str, Any]:
        """Die eigenen, wirksamen Rechte des Tokens auf `path` (`GET /access/permissions`, darf jeder fuer sich selbst):
        `{"VM.Allocate": 1, ...}`; leer, wenn er dort keine hat."""
        data = await self._request("GET", "/access/permissions", params={"path": path}) or {}
        rights = data.get(path) if isinstance(data, dict) else None
        return rights if isinstance(rights, dict) else {}

    async def version(self) -> str:
        data = await self._request("GET", "/version") or {}
        return str(data.get("version") or "") if isinstance(data, dict) else ""

    async def ha_resources(self) -> list[dict[str, Any]]:
        """`GET /cluster/ha/resources` (`Sys.Audit` auf `/`): HA-Regeln, die einen Gast von selbst starten koennen."""
        return await self._request("GET", "/cluster/ha/resources") or []


UPID_RE = re.compile(
    r"UPID:[A-Za-z0-9-]+:[0-9A-Fa-f]{8,16}:[0-9A-Fa-f]{8,16}:[0-9A-Fa-f]{8,16}:[A-Za-z0-9_-]+:[A-Za-z0-9_.-]*:[^\s:/?#%]+:"
)
"""Aufgaben-Kennung von Proxmox (`UPID:<knoten>:<pid>:<pstart>:<start>:<art>:<nummer>:<benutzer>:`). Sie landet im Pfad
einer Anfrage -- nichts, was den Pfad verlassen oder eine Abfrage anhaengen koennte (`/`, `?`, `#`, `%`)."""
NET_KEY_RE = re.compile(r"net\d+")
FINISH_KEYS = frozenset({"onboot", "name", "hostname", "description", "digest"})

COPY_POOL = "nodvard-kopien"
"""Der Pool, in dem jede Kopie entsteht. Fest, nie aus einer Anfrage oder aus dem Payload der Aktion: Dem Zugang
gehoert `VM.Allocate` nur auf diesem Pool, so kann Nodvard Deck nirgends sonst einen Gast anlegen. Dieselbe
Bezeichnung nutzt die Einrichtung "Proxmox mit Passwort verbinden" in der proxmox-Erweiterung, die den Pool und
die Rechte anlegt -- bewusst kein Import zwischen den Erweiterungen, beide tragen den Namen selbst ein."""

RESTORE_KEYS: dict[str, frozenset[str]] = {
    "qemu": frozenset({"vmid", "archive", "storage", "unique", "name", "onboot", "pool"}),
    "lxc": frozenset({"vmid", "ostemplate", "restore", "storage", "unique", "hostname", "onboot", "pool"}),
}
"""Was beim Wiederherstellen als Kopie an Proxmox geht. `pool` ist immer `COPY_POOL` (`restore_guest` setzt es selbst).
Bewusst NICHT dabei: `force` (wuerde einen vorhandenen Gast mit derselben Nummer ueberschreiben), `start`,
`live-restore`, `ha-managed`."""
