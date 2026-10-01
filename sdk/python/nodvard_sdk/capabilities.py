"""Capability-Protokolle — die eigentliche Entkopplung.

Der Kern kennt keine Extension. Er kennt diese Protokolle und ruft sie auf, ohne zu
wissen, wer sie erfuellt. Eine Extension meldet eine Implementierung an:

    ctx.capabilities.provide(NextcloudSource(ctx, connector))

Daraus baut der Kern generische Funktionen — den Dateimanager, das Terminal, die
Host-Liste, den App-Launcher — ohne eine Zeile "Proxmox" oder "Nextcloud".

Siehe docs/02-EXTENSION-API.md §3.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any, Protocol, runtime_checkable

from .actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from .types import (
    ConnectorHealth,
    DiscoveredHost,
    ExecResult,
    FileEntry,
    FileSourceCaps,
    Host,
    HostStatus,
    Notification,
    Page,
    SourceInfo,
)


@runtime_checkable
class HostProvider(Protocol):
    """Entdeckt Hosts. Die Host-*Identitaet* gehoert trotzdem dem Kern.

    Der Kern gleicht die Rueckgabe gegen seine `hosts`-Tabelle ab (Schluessel:
    provider_ext_id + provider_ref). Ohne diese Regel haette jede Extension ihre eigene
    Host-Liste — und man waere wieder bei vier auseinanderdriftenden Wahrheiten.
    """

    provider_id: str

    async def discover_hosts(self) -> list[DiscoveredHost]: ...
    async def host_status(self, provider_ref: str) -> HostStatus: ...
    async def host_actions(self, provider_ref: str) -> list[ActionSpec]: ...


@runtime_checkable
class MetricsProvider(Protocol):
    """Momentaufnahmen fuer Kacheln und Kurven.

    Verlauf (docs/00-DECISIONS.md D-02-Nachtrag): hat die Quelle selbst Historie
    (Proxmox-RRD), bietet der Anbieter optional
    `async def history(self, host: Host, range_name: str) -> dict` an -- range_name aus
    "1h"/"6h"/"24h"/"7d"/"30d", Rueckgabe `{"step_s", "timestamps", "series", "max"}`
    (eine Werteliste je Metrik, `None` = Luecke). Ohne `history` sammelt der Kern
    selbst ueber `sample()` in einen eigenen Speicher (nicht die relationale DB).
    """

    async def sample(self, host: Host) -> dict[str, float]: ...
    async def metric_names(self) -> list[str]: ...

    # Optional: `async def supports(self, host: Host) -> bool`. Hat ein Host keinen
    # eigenen Anbieter (kein provider_ext_id, z. B. ein Raspberry Pi), fragt der Kern
    # die Anbieter, die das anbieten -- etwa einen, der per SSH misst.


@runtime_checkable
class ActionExecutor(Protocol):
    """Fuehrt aus, was das Gate freigegeben hat — und nur das.

    Extensions rufen das nie selbst auf. Sie gehen ueber ctx.actions.propose().
    """

    action_types: frozenset[str]

    async def execute(self, req: ActionRequest) -> ActionResult: ...
    async def dry_run(self, req: ActionRequest) -> DryRunReport | None: ...


@runtime_checkable
class TerminalSession(Protocol):
    async def read(self) -> AsyncIterator[bytes]: ...
    async def write(self, data: bytes) -> None: ...
    async def resize(self, cols: int, rows: int) -> None: ...
    async def close(self) -> None: ...

    @property
    def exit_code(self) -> int | None: ...


@runtime_checkable
class TerminalTarget(Protocol):
    async def can_open(self, host: Host) -> bool: ...
    async def open(
        self, host: Host, *, user: str | None, cols: int, rows: int
    ) -> TerminalSession: ...


@runtime_checkable
class ConsoleSession(Protocol):
    """Ein offener Byte-Kanal zum BILDSCHIRM eines Hosts (VM-/Container-Konsole) --
    anders als `TerminalSession` keine Zeichen-Shell, sondern ein Grafikprotokoll.
    Der Kern pumpt nur Bytes zwischen Browser und dieser Sitzung und kennt das
    Protokoll selbst nicht; `protocol` sagt dem Browser-Client, wie er sie deuten muss
    (aktuell nur `"vnc"`, also RFB fuer noVNC).

    `password`: Einmal-Kennwort fuer die protokolleigene Authentifizierung (RFB-VNC-
    Auth), falls der Gegenueber eines verlangt -- kurzlebig und nur fuer genau diese
    eine Sitzung gueltig, KEIN dauerhaftes Zugangsdatum (das bleibt im Vault)."""

    protocol: str
    password: str | None

    async def read(self) -> AsyncIterator[bytes]: ...
    async def write(self, data: bytes) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class ConsoleTarget(Protocol):
    """Wer den Bildschirm eines Hosts liefern kann (z. B. ein Hypervisor fuer seine
    VMs). Methodennamen bewusst NICHT `can_open`/`open` wie bei `TerminalTarget`:
    `@runtime_checkable` prueft nur Attributnamen -- bei gleichen Namen haette jede
    SSH-Terminal-Implementierung auch als Konsole gegolten (und umgekehrt), und der
    Kern haette eine Shell-Sitzung als Bildschirm-Strom ausgeliefert."""

    async def console_available(self, host: Host) -> bool: ...
    async def open_console(self, host: Host) -> ConsoleSession: ...


@runtime_checkable
class FileSource(Protocol):
    """Eine Quelle in der Seitenleiste des Dateimanagers.

    Der Kern baut daraus Explorer, quellenuebergreifende Suche, Drag & Drop zwischen
    Quellen (open_read(A) -> open_write(B), serverseitig) und das Info-Panel — ohne zu
    wissen, ob dahinter SFTP, WebDAV, TrueNAS oder Syncthing steckt.
    """

    source_id: str
    label: str
    icon: str
    caps: FileSourceCaps

    async def stat(self, path: PurePosixPath) -> FileEntry: ...
    async def list_dir(
        self, path: PurePosixPath, *, cursor: str | None = None
    ) -> Page[FileEntry]: ...
    async def open_read(
        self, path: PurePosixPath, *, offset: int = 0
    ) -> AsyncIterator[bytes]: ...
    async def open_write(
        self, path: PurePosixPath, stream: AsyncIterator[bytes], *, size: int | None
    ) -> FileEntry: ...
    async def mkdir(self, path: PurePosixPath) -> FileEntry: ...
    async def remove(self, path: PurePosixPath, *, recursive: bool = False) -> None: ...
    async def rename(self, src: PurePosixPath, dst: PurePosixPath) -> FileEntry: ...
    async def search(
        self, query: str, *, root: PurePosixPath
    ) -> AsyncIterator[FileEntry]: ...
    async def info(self) -> SourceInfo: ...


@runtime_checkable
class FileSourceProvider(Protocol):
    """Liefert die aktuell verfuegbaren `FileSource`-Instanzen dieser Extension.

    `ctx.capabilities.provide(FileSource)` direkt (siehe Modul-Docstring-Beispiel)
    reicht fuer eine Extension mit GENAU EINER, zur Setup-Zeit feststehenden Quelle
    (z. B. ein einzelner konfigurierter Nextcloud-Server). Es reicht NICHT, wenn die
    Menge der Quellen sich waehrend der Laufzeit aendert, ohne dass die Extension neu
    laedt — der Leitfall ist SSH: jeder Host mit Zugangsdaten ist eine eigene Quelle,
    und Hosts werden jederzeit angelegt/entfernt (docs/02 §4/§7). Der Kern-
    Dateimanager ruft `file_sources()` bei JEDER `GET /files/sources`-Anfrage frisch
    auf, statt sich auf eine bei `setup()` eingefrorene Liste zu verlassen."""

    async def file_sources(self) -> list["FileSource"]: ...


@runtime_checkable
class AIProvider(Protocol):
    provider_id: str
    model: str

    async def complete(
        self, prompt: str, *, system: str | None = None, **kwargs: Any
    ) -> str: ...
    async def stream(
        self, prompt: str, *, system: str | None = None, **kwargs: Any
    ) -> AsyncIterator[str]: ...
    async def health(self) -> ConnectorHealth: ...


@runtime_checkable
class NotificationChannel(Protocol):
    channel_id: str
    label: str

    async def send(self, notification: Notification) -> None: ...
    async def test(self) -> ConnectorHealth: ...


@runtime_checkable
class ServiceCatalog(Protocol):
    """Speist den App-Launcher.

    Ersetzt die von Hand gepflegten Link-Listen, die unbemerkt veralten: Dienste werden
    entdeckt, nicht eingetragen.
    """

    async def list_services(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class BackupProvider(Protocol):
    async def list_jobs(self) -> list[dict[str, Any]]:
        """Je Job (und Gast) eine Zeile mit `last_status` (`ok`, `failed`, `running`,
        `unknown`). Eine Verbindung, die nicht antwortet, kommt als eigene Zeile mit
        `last_status="unreachable"` (Feld `connection` = Name, `job_ref` leer) -- keine
        Ausnahme, damit die anderen Verbindungen sichtbar bleiben. Leser behandeln sie als
        Warnung, nicht als Job."""
        ...
    async def job_history(self, job_ref: str) -> list[dict[str, Any]]: ...
    async def retry(self, job_ref: str) -> ActionRequest:
        """Gibt einen *Vorschlag* zurueck, keine Ausfuehrung — auch Retries gehen durchs Gate."""
        ...


@runtime_checkable
class SearchProvider(Protocol):
    async def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]: ...


@runtime_checkable
class CommandRunner(Protocol):
    """Der gemeinsame Ausfuehrungs-Layer (ctx.exec).

    Grundsatz: kein zweiter, separater Ausfuehrungsweg mit eigenen Edge-Cases.
    Web-Terminal, Skript-Ausfuehrung und KI-Remediation benutzen denselben.
    """

    async def run(
        self, host: Host, command: str, *, timeout_s: int = 60, user: str | None = None
    ) -> ExecResult: ...


ALL_CAPABILITIES: tuple[type, ...] = (
    HostProvider,
    MetricsProvider,
    ActionExecutor,
    TerminalTarget,
    ConsoleTarget,
    FileSource,
    FileSourceProvider,
    AIProvider,
    NotificationChannel,
    ServiceCatalog,
    BackupProvider,
    SearchProvider,
    CommandRunner,
)
"""Der Kern iteriert hierueber, um zu erkennen, welche Capability eine Registrierung
erfuellt. Neue Protokolle muessen hier eingetragen werden, sonst sieht der Loader sie nicht."""
