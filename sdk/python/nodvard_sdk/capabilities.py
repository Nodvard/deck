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
class ScopedTerminalTarget(Protocol):
    """Eine Shell IN etwas auf einem Host statt auf dem Host selbst, etwa in einem Container. `scope` sagt, worin
    (z. B. `"container"`), `ref` welches (z. B. der Name); was als `ref` gilt, prueft der Anbieter selbst, bevor er
    es irgendwo einsetzt. Der Kern oeffnet die Sitzung wie ein normales Terminal (`POST /terminal/sessions` mit
    `scope` und `ref`, gleiche Berechtigung, gleiches Protokoll) und kennt die Art nicht."""

    scope: str

    async def can_open_in(self, host: Host, ref: str) -> bool: ...
    async def open_in(self, host: Host, ref: str, *, cols: int, rows: int) -> TerminalSession: ...


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

    Optional: `required_permission: str | None` (Standard: fehlt = `None`). Nennt eine Quelle
    hier eine Berechtigung (z. B. `"hosts.execute"`), darf der Kern sie nur Nutzern zeigen und
    öffnen, die diese Berechtigung selbst haben -- `files.read`/`files.write` allein reichen dann
    nicht. Gedacht für Quellen, die mit fremden Zugangsdaten arbeiten (SSH als root): sonst
    liest jeder mit `files.read` alles, was der Zugang lesen darf. Bewusst KEIN Pflichtmitglied
    des Protokolls (`@runtime_checkable` würde sonst jede ältere Quelle ohne das Attribut
    abweisen); der Kern liest es mit `getattr(source, "required_permission", None)`.

    Optional: `async file_identity(path) -> dict | None`. Damit erkennt der Kern vor einem Transfer,
    ob Quelle und Ziel dieselbe Datei sind (Link, `..`-Umweg, zweiter Eintrag für denselben
    Rechner) -- sonst würde das Schreiben die Quelle selbst überschreiben. Rückgabe: `path` (der
    aufgelöste, absolute Pfad) und, soweit bekannt, `size`, `mtime`, `uid`, `gid`, `mode` sowie
    `machine` (eine feste Kennung des Rechners, etwa `/etc/machine-id`); `None`, wenn es die Datei
    nicht gibt oder die Quelle es nicht ermitteln kann. Innerhalb derselben Quelle entscheidet
    `path`. Über zwei Quellen hinweg sind zwei verschiedene `machine`-Kennungen immer zwei Dateien;
    bei gleicher oder unbekannter Kennung müssen außer `path` auch alle fünf Werte übereinstimmen
    (geklonte Rechner teilen oft dieselbe Kennung).
    Bewusst KEIN Pflichtmitglied; der Kern liest es mit `getattr(source, "file_identity", None)`.

    Optional: `host_id: str | None`. Steht die Quelle für einen Server (etwa SFTP auf einen Host), nennt sie
    hier seine Kennung; der Dateimanager zeigt dann Status und Betriebssystem des Servers neben der Quelle.
    Bewusst KEIN Pflichtmitglied; der Kern liest es mit `getattr(source, "host_id", None)`.
    Ebenso gilt für `open_write`: eine Quelle sollte das Ziel erst ersetzen, wenn alles
    angekommen ist, nie vorab abschneiden.
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
class FilePlaceProvider(Protocol):
    """Nennt wichtige Orte eines Servers, die der Dateimanager als Abkürzungen zeigt („Wichtige Orte“).

    `places_for_host(host_id)` liefert die Orte für genau diesen Server (Kennung wie `Host.id`) oder eine leere
    Liste, wenn die Erweiterung dort nichts weiß. Nur Wissen, das die Erweiterung schon hat: keine eigenen
    Befehle auf dem Server, nur um Orte zu raten. Der Kern fragt alle Anbieter gleichzeitig, gibt jedem eine
    Zeitgrenze, lässt Fehler eines Anbieters nur dessen Orte kosten, entdoppelt nach Pfad und begrenzt die Zahl
    (`GET /files/places`, docs/04-API.md). Der Kern zeigt Orte nur Nutzern, die zu diesem Server auch eine
    Dateiquelle öffnen dürfen."""

    async def places_for_host(self, host_id: str) -> list["FilePlace"]: ...


@runtime_checkable
class AppSuggestionProvider(Protocol):
    """Schlaegt App-Kacheln vor: Adressen, die die Erweiterung schon kennt (z. B. die Proxy-Hosts eines Reverse
    Proxys). Nur vorhandenes Wissen, keine Suche im Netz. Der Kern fragt alle Anbieter gleichzeitig mit einer
    Zeitgrenze, laesst Fehler eines Anbieters nur dessen Vorschlaege kosten, prueft jede Adresse wie bei einer von Hand
    angelegten App, laesst schon angelegte weg, entdoppelt und begrenzt die Zahl (`GET /apps/suggestions`,
    docs/04-API.md). Ein Vorschlag wird erst zur App, wenn ein Nutzer mit `apps.write` ihn speichert."""

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
    """Speist den App-Launcher.

    Ersetzt die von Hand gepflegten Link-Listen, die unbemerkt veralten: Dienste werden
    entdeckt, nicht eingetragen.
    """

    async def list_services(self) -> list[dict[str, Any]]:
        """Je Dienst eine Zeile (`id`, `name`, `host`, `state`, `tone`, `url`, ...). Ein Server, dessen
        Dienste nicht gelesen werden konnten, kommt als eigene Platzhalterzeile mit `unreachable=True`
        (und `host` = Name des Servers) -- keine Ausnahme, damit die anderen Server sichtbar bleiben.
        Leser zaehlen sie nicht als Dienst, sondern als Warnung."""
        ...


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


@runtime_checkable
class ConnectionReceiver(Protocol):
    """Uebernimmt eine fertige Verbindung (Adresse und API-Token), die eine ANDERE Erweiterung fuer sie angelegt hat --
    etwa wenn beim Verbinden eines Servers mit Passwort gleich ein eigener Zugang fuer ein zweites Modul entsteht.

    Erweiterungen importieren einander nicht, und Geheimnisse gehen nie ueber den Ereignis-Bus. Der Anbieter (wer den
    Zugang anlegt) findet Empfaenger ueber `ctx.capabilities.query(ConnectionReceiver)`, also nur bei Erweiterungen,
    die er in `requires` nennt; ist der Empfaenger ausgeschaltet, ist die Liste leer, und der Anbieter laesst diesen
    Teil weg. `requires` macht den Empfaenger nicht zur Pflicht. Das Geheimnis geht als Argument in genau einen Aufruf:
    Der Empfaenger legt es in seinen eigenen Tresor-Eintrag (`ctx.secrets`, wie sein Einstellungsformular) und gibt es
    nie zurueck, nie in eine Meldung und nie weiter.

    Der Empfaenger prueft nicht, wer ihn aufruft. Der Anbieter sichert seinen eigenen Weg dorthin ab (etwa eine Route
    nur fuer Admins mit Protokoll-Eintrag) und fragt nur Empfaenger der Art ab, die er kennt (`connection_kind`)."""

    connection_kind: str
    """Welche Art Verbindung angenommen wird, zwischen Anbieter und Empfaenger vereinbart (z. B. `"proxmox"`)."""

    async def connection_conflict(self, *, name: str, base_url: str) -> str | None:
        """`None`, wenn eine Verbindung `name` zu `base_url` angenommen werden kann; sonst ein deutscher Satz, warum
        nicht (etwa: der Name gehoert schon zu einem anderen Server). Aendert nichts -- der Anbieter fragt, bevor er
        beim Server etwas anlegt."""
        ...

    async def receive_connection(
        self, *, name: str, base_url: str, token_id: str, token_secret: SecretStr, tls_insecure_skip_verify: bool,
        pinned_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Traegt die Verbindung ein. Gibt es zu dieser Adresse schon eine, bekommt sie den neuen Token (ihr Geheimnis
        wird in einem Schritt ersetzt, ihr Name bleibt), sonst entsteht eine neue unter `name`. Rueckgabe:
        `{"name": <Name der Verbindung>, "replaced_token_id": <bisherige Token-ID oder None>}`, dazu wahlweise
        `tls_insecure_skip_verify`, `tls_fingerprint_sha256` und `enabled`: was danach tatsaechlich gilt. Ein Empfaenger
        schwaecht eine vorhandene Verbindung nie still ab (prueft sie das Zertifikat oder hat sie einen gemerkten
        Fingerabdruck, bleibt das so; ist sie aus, bleibt sie aus) und nimmt nur, was auch von Hand eingetragen werden
        koennte; sonst lehnt er ab.

        `pinned_sha256` (wahlweise, wie bei `ctx.http`): der Fingerabdruck des Zertifikats, den die Person beim Anbieter
        ausdruecklich bestaetigt hat. Der Empfaenger merkt ihn sich (die Verbindung spricht dann nur mit genau diesem
        Zertifikat) und ersetzt dabei einen alten; "Selbstsigniertes Zertifikat erlauben" gilt dann nicht. Ein
        Anbieter, der ihn nicht kennt, laesst ihn weg. Scheitert etwas, bleibt der alte Stand, und es
        kommt eine Ausnahme -- deren Text gibt der Anbieter nicht an Nutzer weiter (`SecretNotOwned`: unter dem Namen
        liegt ein Geheimnis, das nicht dem Empfaenger gehoert; es bleibt unveraendert)."""
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
"""Der Kern iteriert hierueber, um zu erkennen, welche Capability eine Registrierung
erfuellt. Neue Protokolle muessen hier eingetragen werden, sonst sieht der Loader sie nicht."""
