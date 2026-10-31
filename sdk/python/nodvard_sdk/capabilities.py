"""Capability-Protokolle: Der Kern kennt keine Extension, nur diese Protokolle, und ruft sie auf.

Eine Extension meldet eine Implementierung an: `ctx.capabilities.provide(NextcloudSource(ctx, connector))`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any, Protocol, runtime_checkable

from pydantic import SecretStr

from .actions import ActionRequest, ActionResult, ActionSpec, DryRunReport
from .types import (
    AppSuggestion,
    ConnectorHealth,
    DiscoveredHost,
    ExecResult,
    FileEntry,
    FilePlace,
    FileSourceCaps,
    Host,
    HostStatus,
    Notification,
    Page,
    SourceInfo,
)


@runtime_checkable
class HostProvider(Protocol):
    """Entdeckt Hosts; die Host-Identität gehört trotzdem dem Kern.

    Der Kern gleicht die Rückgabe gegen seine `hosts`-Tabelle ab (Schlüssel: provider_ext_id + provider_ref).
    """

    provider_id: str

    async def discover_hosts(self) -> list[DiscoveredHost]: ...
    async def host_status(self, provider_ref: str) -> HostStatus: ...
    async def host_actions(self, provider_ref: str) -> list[ActionSpec]: ...


@runtime_checkable
class MetricsProvider(Protocol):
    """Momentaufnahmen für Kacheln und Kurven."""

    async def sample(self, host: Host) -> dict[str, float]: ...
    async def metric_names(self) -> list[str]: ...

    # Optional: `async def history(self, host: Host, range_name: str) -> dict`, wenn die Quelle selbst Historie hat
    # (Proxmox-RRD). `range_name` ist "1h", "6h", "24h", "7d" oder "30d". Rückgabe: `{"step_s", "timestamps", "series",
    # "max"}` mit einer Werteliste je Metrik (`None` = Lücke). Ohne `history` sammelt der Kern selbst über `sample()`
    # in einen eigenen Speicher (nicht die relationale DB).

    # Optional: `async def supports(self, host: Host) -> bool`. Hat ein Host keinen
    # eigenen Anbieter (kein provider_ext_id, z. B. ein Raspberry Pi), fragt der Kern
    # die Anbieter, die das anbieten -- etwa einen, der per SSH misst.


@runtime_checkable
class ActionExecutor(Protocol):
    """Führt aus, was das Gate freigegeben hat — und nur das.

    Extensions rufen das nie selbst auf. Sie gehen über ctx.actions.propose().
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
class ScopedTerminalTarget(Protocol):
    """Eine Shell IN etwas auf einem Host (etwa einem Container) statt auf dem Host selbst; `scope` sagt, worin
    (z. B. `"container"`), `ref` welches (z. B. der Name). Was als `ref` gilt, prüft der Anbieter selbst, bevor er es
    irgendwo einsetzt. Der Kern öffnet die Sitzung wie ein normales Terminal (`POST /terminal/sessions`)."""

    scope: str

    async def can_open_in(self, host: Host, ref: str) -> bool: ...
    async def open_in(self, host: Host, ref: str, *, cols: int, rows: int) -> TerminalSession: ...


# Der Kern pumpt nur Bytes zwischen Browser und Sitzung und kennt das Protokoll nicht. Das dauerhafte Zugangsdatum
# bleibt im Vault.
@runtime_checkable
class ConsoleSession(Protocol):
    """Byte-Kanal zum BILDSCHIRM eines Hosts (VM-/Container-Konsole), anders als `TerminalSession` ein Grafikprotokoll;
    `protocol` sagt dem Browser-Client, wie er die Bytes deutet (aktuell nur `"vnc"`, RFB für noVNC).
    `password`: Einmal-Kennwort für die RFB-VNC-Auth, nur für diese Sitzung gültig, kein dauerhaftes Zugangsdatum."""

    protocol: str
    password: str | None

    async def read(self) -> AsyncIterator[bytes]: ...
    async def write(self, data: bytes) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class ConsoleTarget(Protocol):
    """Wer den Bildschirm eines Hosts liefern kann (z. B. ein Hypervisor für seine VMs)."""

    # Die Namen weichen bewusst von `can_open`/`open` des `TerminalTarget` ab: `@runtime_checkable` prüft nur
    # Attributnamen, bei gleichen Namen würde jedes SSH-Terminal auch als Konsole gelten (und umgekehrt) und der Kern
    # lieferte eine Shell-Sitzung als Bildschirm-Strom aus.
    async def console_available(self, host: Host) -> bool: ...
    async def open_console(self, host: Host) -> ConsoleSession: ...


@runtime_checkable
class FileSource(Protocol):
    """Eine Quelle in der Seitenleiste des Dateimanagers; der Kern baut daraus Explorer, Suche, serverseitiges
    Drag & Drop (`open_read(A)` -> `open_write(B)`) und das Info-Panel, ohne zu wissen, was dahinter steckt.
    `open_write` ersetzt das Ziel erst, wenn alles angekommen ist, und schneidet es nie vorab ab."""

    # Optional: `required_permission: str | None`. Der Kern zeigt und öffnet eine Quelle nur Nutzern, die diese
    # Berechtigung selbst haben (SSH: `"hosts.execute"`, Nextcloud: `"files.nextcloud"`); `files.read` und `files.write`
    # allein reichen nicht. Fehlt die Angabe (oder ist sie `None`), gilt die Quelle als geschützt: nur Owner und admin.
    # Eine Quelle ohne fremde Zugangsdaten, die jeder mit Dateizugriff benutzen darf, nennt `"files.read"`. Kein
    # Pflichtmitglied, weil `@runtime_checkable` sonst jede ältere Quelle ohne das Attribut abwiese; der Kern liest es
    # per `getattr(source, "required_permission", None)`.
    #
    # Optional: `async file_identity(path) -> dict | None`. Damit erkennt der Kern vor einem Transfer, ob Quelle und
    # Ziel dieselbe Datei sind (Link, `..`-Umweg, zweiter Eintrag für denselben Rechner); sonst würde das Schreiben die
    # Quelle selbst überschreiben. Rückgabe: `path` (aufgelöster, absoluter Pfad) und, soweit bekannt, `size`, `mtime`,
    # `uid`, `gid`, `mode` sowie `machine` (feste Kennung des Rechners, etwa `/etc/machine-id`); `None`, wenn es die
    # Datei nicht gibt oder die Quelle es nicht ermitteln kann. Innerhalb derselben Quelle entscheidet `path`. Über zwei
    # Quellen hinweg sind zwei verschiedene `machine`-Kennungen immer zwei Dateien; bei gleicher oder unbekannter
    # Kennung müssen außer `path` auch alle fünf Werte übereinstimmen (geklonte Rechner teilen oft dieselbe Kennung).
    # Kein Pflichtmitglied; der Kern liest es mit `getattr(source, "file_identity", None)`.
    #
    # Optional: `async free_bytes(path) -> int | None`. Freier Platz (in Bytes) dort, wohin `path` geschrieben würde. Damit
    # lehnt der Kern einen Upload mit bekannter Größe vorab ab (507), statt das Ziel halb vollzuschreiben; `None`, wenn die
    # Quelle es nicht weiß. Kein Pflichtmitglied; der Kern liest es mit `getattr(source, "free_bytes", None)`.
    #
    # Optional: `host_id: str | None`. Steht die Quelle für einen Server (etwa SFTP auf einen Host), nennt sie hier
    # seine Kennung; der Dateimanager zeigt dann Status und Betriebssystem des Servers neben der Quelle. Kein
    # Pflichtmitglied; der Kern liest es mit `getattr(source, "host_id", None)`.

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


# `ctx.capabilities.provide(FileSource)` direkt reicht für eine Extension mit genau einer, zur Setup-Zeit feststehenden
# Quelle (z. B. ein konfigurierter Nextcloud-Server). Ändert sich die Menge der Quellen zur Laufzeit, ohne dass die
# Extension neu lädt, braucht sie diesen Anbieter; der Leitfall ist SSH: Jeder Host mit Zugangsdaten ist eine eigene
# Quelle, und Hosts werden jederzeit angelegt oder entfernt.
@runtime_checkable
class FileSourceProvider(Protocol):
    """Liefert die aktuell verfügbaren `FileSource`-Instanzen dieser Extension.

    Der Kern ruft `file_sources()` bei jeder `GET /files/sources`-Anfrage frisch auf, nicht nur bei `setup()`.
    """

    async def file_sources(self) -> list["FileSource"]: ...


# Der Kern fragt alle Anbieter gleichzeitig (`GET /files/places`), gibt jedem eine Zeitgrenze, lässt Fehler eines
# Anbieters nur dessen Orte kosten, entdoppelt nach Pfad und begrenzt die Zahl. Er zeigt Orte nur Nutzern, die zu diesem
# Server auch eine Dateiquelle öffnen dürfen.
@runtime_checkable
class FilePlaceProvider(Protocol):
    """Nennt wichtige Orte eines Servers, die der Dateimanager als Abkürzungen zeigt („Wichtige Orte“).
    `places_for_host(host_id)` liefert sie für genau diesen Server (Kennung wie `Host.id`), sonst eine leere Liste.
    Nur schon vorhandenes Wissen: keine eigenen Befehle auf dem Server, nur um Orte zu raten."""

    async def places_for_host(self, host_id: str) -> list["FilePlace"]: ...


# Der Kern fragt alle Anbieter gleichzeitig (`GET /apps/suggestions`) mit einer Zeitgrenze, lässt Fehler eines Anbieters
# nur dessen Vorschläge kosten, prüft jede Adresse wie bei einer von Hand angelegten App, lässt schon angelegte weg,
# entdoppelt und begrenzt die Zahl.
@runtime_checkable
class AppSuggestionProvider(Protocol):
    """Schlägt App-Kacheln vor: Adressen, die die Erweiterung schon kennt (z. B. Proxy-Hosts), ohne Suche im Netz.
    Ein Vorschlag wird erst zur App, wenn ein Nutzer mit `apps.write` ihn speichert."""

    async def app_suggestions(self) -> list["AppSuggestion"]: ...


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
    """Speist den App-Launcher: Dienste werden entdeckt, nicht von Hand eingetragen."""

    async def list_services(self) -> list[dict[str, Any]]:
        """Je Dienst eine Zeile (`id`, `name`, `host`, `state`, `tone`, `url`, ...). Ein Server, dessen Dienste nicht
        gelesen werden konnten, kommt als Platzhalterzeile mit `unreachable=True` (`host` = Servername) statt als
        Ausnahme, damit die anderen sichtbar bleiben; Leser zählen sie als Warnung, nicht als Dienst."""
        ...


@runtime_checkable
class BackupProvider(Protocol):
    async def list_jobs(self) -> list[dict[str, Any]]:
        """Je Job (und Gast) eine Zeile mit `last_status` (`ok`, `failed`, `running`, `unknown`). Eine Verbindung, die
        nicht antwortet, kommt als Zeile mit `last_status="unreachable"` (`connection` = Name, `job_ref` leer) statt als
        Ausnahme, damit die anderen sichtbar bleiben; Leser behandeln sie als Warnung, nicht als Job."""
        ...
    async def job_history(self, job_ref: str) -> list[dict[str, Any]]: ...
    async def retry(self, job_ref: str) -> ActionRequest:
        """Gibt einen *Vorschlag* zurück, keine Ausführung; vor `ctx.actions.propose()` setzt der Aufrufer `proposed_by`
        selbst (eigene Kennung oder Nutzer der Anfrage), sonst `PermissionDenied`."""
        ...


@runtime_checkable
class SearchProvider(Protocol):
    async def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]: ...


@runtime_checkable
class CommandRunner(Protocol):
    """Der gemeinsame Ausführungs-Layer (`ctx.exec`): Web-Terminal, Skript-Ausführung und KI-Remediation nutzen ihn."""

    async def run(
        self, host: Host, command: str, *, timeout_s: int = 60, user: str | None = None
    ) -> ExecResult: ...


# Erweiterungen importieren einander nicht, und Geheimnisse gehen nie über den Ereignis-Bus. Der Anbieter (wer den
# Zugang anlegt) findet Empfänger über `ctx.capabilities.query(ConnectionReceiver)`, also nur bei Erweiterungen, die er
# in `requires` nennt; ist der Empfänger ausgeschaltet, ist die Liste leer, und der Anbieter lässt diesen Teil weg.
# `requires` macht den Empfänger nicht zur Pflicht. Der Empfänger legt das Geheimnis in seinen eigenen Tresor-Eintrag
# (`ctx.secrets`, wie sein Einstellungsformular) und gibt es nie zurück, nie in eine Meldung und nie weiter.
#
# Der Empfänger prüft nicht, wer ihn aufruft: Der Anbieter sichert seinen eigenen Weg dorthin ab (etwa eine Route nur
# für Admins mit Protokoll-Eintrag) und fragt nur Empfänger der Art ab, die er kennt (`connection_kind`).
@runtime_checkable
class ConnectionReceiver(Protocol):
    """Übernimmt eine fertige Verbindung (Adresse und API-Token), die eine ANDERE Erweiterung für sie angelegt hat, etwa
    ein eigener Zugang für ein zweites Modul beim Verbinden eines Servers mit Passwort. Das Geheimnis geht als Argument
    in genau einen Aufruf und wird nie zurückgegeben oder weitergereicht."""

    connection_kind: str
    """Welche Art Verbindung angenommen wird, zwischen Anbieter und Empfänger vereinbart (z. B. `"proxmox"`)."""

    async def connection_conflict(self, *, name: str, base_url: str) -> str | None:
        """`None`, wenn eine Verbindung `name` zu `base_url` angenommen werden kann; sonst ein deutscher Satz, warum
        nicht (etwa: der Name gehört schon zu einem anderen Server). Ändert nichts -- der Anbieter fragt, bevor er
        beim Server etwas anlegt."""
        ...

    # Gibt es zu der Adresse schon eine Verbindung, wird ihr Geheimnis in einem Schritt ersetzt und ihr Name bleibt. Ein
    # Empfänger schwächt eine vorhandene Verbindung nie still ab (prüft sie das Zertifikat oder hat sie einen gemerkten
    # Fingerabdruck, bleibt das so; ist sie aus, bleibt sie aus) und nimmt nur, was auch von Hand eingetragen werden
    # könnte; sonst lehnt er ab.
    #
    # `pinned_sha256` (wahlweise, wie bei `ctx.http`): der Fingerabdruck des Zertifikats, den die Person beim Anbieter
    # ausdrücklich bestätigt hat. Der Empfänger merkt ihn sich (die Verbindung spricht dann nur mit genau diesem
    # Zertifikat) und ersetzt dabei einen alten; "Selbstsigniertes Zertifikat erlauben" gilt dann nicht. Ein Anbieter,
    # der ihn nicht kennt, lässt ihn weg.
    #
    # Scheitert etwas, bleibt der alte Stand, und es kommt eine Ausnahme, deren Text der Anbieter nicht an Nutzer
    # weitergibt (`SecretNotOwned`: unter dem Namen liegt ein Geheimnis, das nicht dem Empfänger gehört; es bleibt
    # unverändert).
    async def receive_connection(
        self, *, name: str, base_url: str, token_id: str, token_secret: SecretStr, tls_insecure_skip_verify: bool,
        pinned_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Trägt die Verbindung ein (zu dieser Adresse schon eine: neuer Token, Name bleibt; sonst neue unter `name`).
        Rückgabe: `{"name": <Name der Verbindung>, "replaced_token_id": <bisherige Token-ID oder None>}`, wahlweise dazu
        `tls_insecure_skip_verify`, `tls_fingerprint_sha256` und `enabled` (was danach tatsächlich gilt)."""
        ...

    async def received_token_ids(self) -> list[str]:
        """Die Token-IDs aller eingetragenen Verbindungen (nie ein Geheimnis): woran der Anbieter erkennt, welche der
        Tokens, die er beim Server angelegt hat, noch benutzt werden."""
        ...


ALL_CAPABILITIES: tuple[type, ...] = (
    HostProvider,
    MetricsProvider,
    ActionExecutor,
    TerminalTarget,
    ScopedTerminalTarget,
    ConsoleTarget,
    FileSource,
    FileSourceProvider,
    FilePlaceProvider,
    AppSuggestionProvider,
    AIProvider,
    NotificationChannel,
    ServiceCatalog,
    BackupProvider,
    SearchProvider,
    CommandRunner,
    ConnectionReceiver,
)
"""Der Kern iteriert hierüber, um zu erkennen, welche Capability eine Registrierung
erfüllt. Neue Protokolle müssen hier eingetragen werden, sonst sieht der Loader sie nicht."""
