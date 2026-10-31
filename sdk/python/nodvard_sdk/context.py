"""Der ExtensionContext, das einzige Objekt, das eine Extension vom Kern bekommt (hier nur das Protokoll).

Jeder Handle ist permission-geprüft und auditiert. Nicht verfügbar: Kern-Tabellen, asyncio-Loop, andere Extensions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from contextvars import ContextVar
from pathlib import PurePosixPath
from typing import Any, AsyncContextManager, Protocol, runtime_checkable

from .actions import ActionRequest, ActionSpec, GateDecision
from .types import (
    Actor,
    ConnectorHealth,
    DiscoveredHost,
    Event,
    ExecResult,
    Host,
    HostStatus,
    Notification,
    NotifyResult,
    PeerCertificate,
    Risk,
    Severity,
)
from .widgets import HostRequirementSpec, HostToolSpec, PageSpec, WidgetSpec

# ---------------------------------------------------------------------------
# Teil-Handles
# ---------------------------------------------------------------------------


class ApiHandle(Protocol):
    current_actor: Any
    """FastAPI-Dependency `Depends(ctx.api.current_actor)`: `Actor.user(...)` des Angemeldeten für `proposed_by` und das
    Audit-Log, gültig nur in dieser Anfrage und in daraus mit `asyncio.create_task` gestarteten Tasks (alle Ausnahmen in
    docs/02 §3, „Wer vorschlägt“)."""

    # Mit `permission` braucht die Extension keine eigene Auth-Abhängigkeit. Ohne `permission` verlangt der Kern
    # mindestens eine gültige Anmeldung (401 ohne Token, keine bestimmte Berechtigung nötig). Anmeldung ist keine
    # Berechtigung: Routen, die Daten zeigen oder etwas ändern, setzen `permission=` oder prüfen selbst.
    #
    # `public=True` ist die ausdrückliche Ausnahme für Routen, die bewusst für jeden erreichbar sein sollen (z. B. ein
    # Webhook): dann prüft der Kern nichts. Dafür braucht die Erweiterung die Manifest-Berechtigung `api.public` (sonst
    # `PermissionDenied`); der Host schreibt die öffentlichen Präfixe beim Einschalten ins Protokoll. `public=True` und
    # `permission` zusammen sind ein Fehler (`InvalidRegistration`).
    #
    # Die Anmeldeprüfung greift nur bei normalen FastAPI-Routen (`@router.get` usw.). WebSocket-Routen,
    # `router.add_route()` und `router.mount()` (z. B. StaticFiles) lehnt der Kern ohne `public=True` mit
    # `InvalidRegistration` ab; mit `public=True` muss die Erweiterung sie selbst absichern (z. B. Einmal-Ticket wie das
    # Terminal).
    #
    # Der Host prüft die Router nach `setup()` und nach `on_start()`. Was eine Erweiterung erst später zur Laufzeit an
    # einen schon montierten Router hängt (etwa aus einem Hintergrundtask), sieht er nicht mehr: solche Routen müssen
    # sich selbst absichern.
    def include_router(
        self,
        router: Any,
        *,
        prefix: str = "",
        tags: list[str] | None = None,
        permission: str | None = None,
        public: bool = False,
    ) -> None:
        """Montiert einen FastAPI-Router unter /api/v1/ext/<ext_id><prefix>. Mit `permission` übernimmt der Kern
        Bearer-Anmeldung und RBAC-Prüfung dieser Berechtigung für ALLE Routen des Routers, ohne sie verlangt er
        mindestens eine gültige Anmeldung. `public=True` nimmt Routen von der Prüfung aus und braucht `api.public`."""
        ...


class UiHandle(Protocol):
    def register_page(self, spec: PageSpec) -> None: ...
    def register_widget(self, spec: WidgetSpec) -> None:
        """Meldet eine Kachel an; dieselbe `id` erneut registriert ersetzt sie (z. B. in `on_settings_changed`)."""
        ...

    def unregister_widget(self, widget_id: str) -> bool:
        """Nimmt eine eigene Kachel wieder aus dem Katalog; `False`, wenn es keine mit dieser `id` gab."""
        ...

    def register_nav(self, *, section: str, order: int = 100) -> None: ...
    def register_host_tool(self, spec: HostToolSpec) -> None:
        """Werkzeug-Kachel auf der Server-Seite passender Hosts (siehe HostToolSpec)."""
        ...

    def register_host_requirement(self, spec: HostRequirementSpec) -> None:
        """Meldet, was diese Extension auf passenden Servern braucht (Gruppe, root ohne
        Passwort, ein lesender Prüfbefehl) -- siehe HostRequirementSpec. Dieselbe `id`
        erneut registrieren ersetzt die Meldung (z. B. in `on_settings_changed`)."""
        ...


@runtime_checkable
class ConnectorType(Protocol):
    """Ein Verbindungs-*Typ*. Instanzen legt der Nutzer in der UI an."""

    id: str
    label: str
    icon: str
    schema: dict[str, Any]
    secret_fields: list[str]

    async def test(self, config: dict[str, Any]) -> ConnectorHealth: ...
    async def build(self, config: dict[str, Any]) -> Any: ...


class ConnectorsHandle(Protocol):
    def register_type(self, connector_type: ConnectorType) -> None: ...
    async def instances(self, type_id: str | None = None) -> list[Any]: ...
    async def get_client(self, instance_id: str) -> Any: ...


class CapabilitiesHandle(Protocol):
    def provide(self, implementation: Any) -> None:
        """Meldet eine Capability an. Der Kern erkennt am Protokoll, welche."""
        ...

    async def query(self, protocol: type) -> list[Any]:
        """Fragt Implementierungen *anderer* Extensions ab — nur für in `requires`
        deklarierte Abhängigkeiten."""
        ...


class ActionsHandle(Protocol):
    def register(self, spec: ActionSpec) -> None: ...

    # Die Extension führt nie selbst aus. Eine sofort freigegebene Aktion (Modus `full`) läuft im Hintergrund:
    # HTTP-Routen übergeben `REQUEST_WAIT_S` und bekommen danach ggf. `executing`, Hintergrund-Jobs mit Bedarf am
    # Ergebnis `None`.
    async def propose(self, request: ActionRequest, *, wait_s: float | None = None) -> GateDecision:
        """Der einzige Weg zu einer zustandsverändernden Aktion (`proposed`, `approved` oder `denied`); als
        `proposed_by` gilt ein Mensch nur, wenn der Kern ihn gerade kennt, eine Erweiterung nur mit eigener Kennung, KI
        und Zeitplan frei, sonst `PermissionDenied`."""
        ...

    async def result(self, action_id: str) -> Any: ...

    async def check_standing_approval(self, granted_by_user_id: str, *, risk: Risk) -> str | None:
        """Gälte eine Dauerfreigabe (`StandingApproval`) von `granted_by_user_id` heute für Aktionen der Stufe `risk`?
        `None` = ja, sonst der Grund in einfachen Worten (Person gesperrt, kein Owner/Admin mehr ...). Dieselbe Prüfung
        wie im Gate, nur lesend; braucht die Berechtigung `actions.standing_approval`."""
        ...

    async def list(self, *, correlation_id: str | None = None, limit: int = 20) -> list[Any]:
        """Jüngste eigene Aktionen (Zeilen mit status/result/proposed_by_*/created_at),
        optional nur zu einer correlation_id -- z. B. die Läufe eines Skripts."""
        ...

    async def waiting_count(self) -> int:
        """Wie viele Aktionen (aller Erweiterungen und des Kerns) gerade auf eine Freigabe warten -- dieselbe Zahl wie
        die Kachel "wartet auf Freigabe" der Übersicht. Nur die Zahl, nichts über fremde Aktionen (etwa für einen
        Lagebericht: vergessene Freigaben fallen so auf). Braucht die Berechtigung `hosts.read`."""
        ...

    # Ob eine Zeile zur eigenen Extension gehört, entscheidet die Datenbank, nicht die übergebene Zeile.
    async def proposer_labels(self, rows: list[Any]) -> dict[str, str]:
        """Lesbare Namen der Vorschlagenden zu Zeilen aus `list()`/`result()`: `{action_id: "admin" | "Skripte" | ...}`.
        Nicht auflösbar (gelöschter Nutzer) und fremde Zeilen bekommen den rohen Wert `art/kennung`. Die Namen gehen an
        jeden, den die Route der Extension durchlässt, die Route also absichern (z. B. `hosts.execute`)."""
        ...


class HostsHandle(Protocol):
    async def list(
        self, *, tag: str | None = None, group: str | None = None
    ) -> list[Host]: ...
    async def get(self, host_id: str) -> Host | None: ...
    async def by_name(self, name: str) -> Host | None: ...
    async def upsert_discovered(self, hosts: list[DiscoveredHost]) -> list[Host]:
        """Gleicht entdeckte Hosts gegen die Kern-Tabelle ab (Schlüssel: ext + ref)."""
        ...
    async def mark_gone(self, provider_refs: list[str]) -> int:
        """Markiert eigene Hosts (Schlüssel: ext + ref) als beim Anbieter nicht mehr vorhanden, etwa einen in Proxmox
        gelöschten Gast; die Oberfläche bietet dann an, ihn zu entfernen. Nur für Bereiche aufrufen, die gerade
        vollständig gelesen wurden, nie für eine nicht erreichbare Verbindung. Ein späteres `upsert_discovered` mit
        derselben Kennung hebt die Markierung auf. Gibt zurück, wie viele Hosts neu markiert wurden."""
        ...
    async def set_status(self, provider_refs: list[str], status: HostStatus) -> int:
        """Setzt nur den Zustand eigener Hosts (Schlüssel: ext + ref) und gibt die Zahl der geänderten zurück; Hosts in
        Wartung oder mit `gone_since` bleiben stehen, `MAINTENANCE` als Ziel lehnt die Methode mit `ValueError` ab."""
        ...


class ExecHandle(Protocol):
    # `log_command` ist nur relevant, wenn die Sperrliste greift.
    async def run(
        self, host: Host, command: str, *, timeout_s: int = 60, user: str | None = None,
        log_command: str | None = None,
    ) -> ExecResult:
        """Nur für *lesende* Kommandos (Status, Logs, Inventar), Veränderndes geht über `ctx.actions.propose()`;
        `log_command` maskiert die Audit-Zeile, und über 8000 Byte geht `command` (außer an Windows-Server) über die
        Standardeingabe der POSIX-Login-Shell."""
        ...

    def stream(self, host: Host, command: str) -> AsyncContextManager[AsyncIterator[bytes]]:
        """Wie `run()` für *lesende* Kommandos mit laufender Ausgabe, die nicht von selbst enden (Live-Logs), beim
        Verlassen endet der entfernte Prozess; über 8000 Byte (außer an Windows-Server) scheitert `command` vor dem
        Senden mit lesbarem Text."""
        ...

    async def run_argv(
        self, host: Host, argv: Sequence[str], *, stdin: str | bytes | None = None, timeout_s: int = 60,
        log_command: str | None = None,
    ) -> ExecResult:
        """Wie `run()`, aber der Kern setzt die Kommandozeile mit POSIX-Quoting aus `argv` zusammen (Eingaben mit `--`
        abtrennen, ungültige Wörter geben vor jeder Verbindung `TypeError`/`ValueError`), und `stdin` geht samt EOF
        nur auf die Standardeingabe, dann darf die Kommandozeile höchstens 8000 Byte haben (außer an Windows-Server)."""
        ...

    def stream_argv(self, host: Host, argv: Sequence[str]) -> AsyncContextManager[AsyncIterator[bytes]]:
        """Wie `stream()`, mit `argv` wie bei `run_argv()` (ohne Standardeingabe)."""
        ...

    def open_shell(
        self, host: Host, *, cols: int, rows: int, command: str | None = None
    ) -> AsyncContextManager[Any]:
        """Interaktive Sitzung mit PTY, ohne `command` mit der Login-Shell des Hosts, sonst mit genau diesem Kommando
        (z. B. eine Shell in einem Container), geprüft gegen die Sperrliste wie bei `run()` und mit höchstens 8000 Byte
        wie bei `stream()`."""
        ...
    def sftp(self, host: Host) -> AsyncContextManager[Any]: ...


@runtime_checkable
class SecretHandleRef(Protocol):
    """Eine Referenz, kein Wert. Der Klartext existiert nur in ctx.vault_use()."""

    id: str
    label: str
    kind: str


class SecretsHandle(Protocol):
    async def get_handle(self, label: str) -> SecretHandleRef:
        """Verweis auf das Geheimnis mit diesem Label (Recht `secrets.read:<label>`). Ein Geheimnis der
        Zwei-Faktor-Anmeldung eines Kontos bekommt keine Erweiterung (`SecretNotOwned`)."""
        ...

    async def create(
        self, *, label: str, kind: str, value: str, description: str | None = None
    ) -> SecretHandleRef: ...
    async def exists(self, label: str) -> bool:
        """Ob es ein Geheimnis mit diesem Label gibt (Recht `secrets.read:<label>`, ältere Kerne prüfen es nicht)."""
        ...

    # Anders als Löschen und neu Anlegen steht das Geheimnis nie zwischendurch leer da.
    async def replace(self, *, label: str, value: str) -> SecretHandleRef:
        """Ersetzt den Wert eines vorhandenen Geheimnisses in einem Schritt (Recht `secrets.read:<label>`, wie bei
        `create`); scheitert es, bleibt der alte Wert. `SecretUnavailable`, wenn es unter dem Label keines gibt;
        `SecretNotOwned`, wenn es nicht dieser Erweiterung gehört (unverändert). Ältere Kerne haben sie nicht."""
        ...

    # Gedacht für Zugangsdaten, die zu einem Ziel gehören, das die Extension gerade entfernt oder umstellt.
    async def delete(self, label: str) -> bool:
        """Löscht das Geheimnis mit diesem Label (Recht `secrets.read:<label>`, wie `get_handle`): `True`, wenn es eines
        gab, sonst `False` (kein Fehler). Nur eigene Geheimnisse, sonst `SecretNotOwned` und es bleibt stehen.
        Ältere Kerne haben die Methode nicht."""
        ...


class SettingsHandle(Protocol):
    def declare(self, schema: dict[str, Any]) -> None: ...
    async def get(self) -> dict[str, Any]: ...
    async def set(self, values: dict[str, Any]) -> None: ...
    async def core(self, key: str) -> Any:
        """Liest eine freigegebene Kern-Einstellung, `system.timezone` oder `maintenance.windows`; jede andere wirft
        `PermissionDenied` (ältere Kerne lieferten jede)."""
        ...


_JOB_TRIGGER: ContextVar[str | None] = ContextVar("nodvard_job_trigger", default=None)
"""Setzt der Kern-Scheduler für die Dauer eines Job-Laufs (siehe `current_job_trigger`)."""


def current_job_trigger() -> str | None:
    """Wie der laufende Job gestartet wurde: "schedule" (nach Zeitplan), "manual" ("Jetzt ausführen") oder `None`
    außerhalb eines Job-Laufs. Damit kann ein `JobSpec.handler` z. B. eine Dauerfreigabe nur für echte
    Zeitplan-Läufe nutzen."""
    return _JOB_TRIGGER.get()


@runtime_checkable
class JobSpec(Protocol):
    id: str
    name: str
    schedule: str  # Crontab, 5 Felder; Wochentag: 0 und 7 = Sonntag, 1 = Montag
    handler: Callable[..., Awaitable[Any]]
    params: dict[str, Any]
    enabled: bool


class SchedulerHandle(Protocol):
    def register_job(self, spec: JobSpec) -> None:
        """Ein Scheduler für alles: keine Cron-Einträge auf Hosts, sondern Jobs im Kern."""
        ...

    def validate_schedule(self, schedule: str) -> None:
        """Prüft einen Cron-Ausdruck (5 Felder) genau so, wie `register_job()` ihn braucht, ohne etwas anzumelden:
        `ValueError` mit deutscher Meldung, wenn er sich nicht planen lässt. Für Zeitpläne, die eine Erweiterung selbst
        speichert: erst prüfen, dann speichern, sonst scheitert `register_job()` beim nächsten Start."""
        ...

    async def trigger(self, job_id: str, **params: Any) -> str: ...


class EventsHandle(Protocol):
    async def publish(self, event: Event) -> None: ...
    def subscribe(
        self, pattern: str, handler: Callable[[Event], Awaitable[None]]
    ) -> None:
        """Pattern mit Stern als Platzhalter, z. B. action.* oder host.down; `action.executed` trägt den `payload`
        der Aktion nur für das vorschlagende Modul oder mit `hosts.execute`."""
        ...


class NotifyHandle(Protocol):
    # `None` als Rückgabe (ältere Kerne, Test-Doubles) wie `suppressed=False` und `delivered=None` (unbekannt)
    # behandeln; die Felder von `NotifyResult` stehen dort.
    async def send(self, notification: Notification, *, raise_on_failure: bool = False) -> NotifyResult | None:
        """Legt die Benachrichtigung an und stellt sie über die Kanäle zu; ein ausgefallener Kanal stoppt den Aufrufer
        nicht. `raise_on_failure=True` wirft `NotificationNotDelivered`, wenn es Kanäle gab, aber keiner zugestellt hat
        (kein Kanal oder Wartungsfenster: kein Fehler). Liefert `NotifyResult`, ältere Kerne und Test-Doubles `None`."""
        ...

    # Für Wächter, die eine im Fenster stumme Meldung nach dem Fenster einmal hörbar nachholen wollen, ohne bei jeder
    # Prüfung einen Verlaufseintrag zu erzeugen.
    async def would_suppress(self, *, host_id: str | None = None, host_ids: Sequence[str] | None = None) -> bool:
        """Würde eine Meldung mit diesem Host-Bezug (`payload["host_id"]` bzw. `payload["host_ids"]`) JETZT still
        geschaltet? Dieselbe Regel wie `send()`, legt aber nichts an; ohne Host-Bezug immer `False`."""
        ...


class AuditHandle(Protocol):
    """Aktionen, die mit `mfa.`, `auth.`, `login.` oder `system.` beginnen, schreibt nur der Kern (er liest
    sie wieder, etwa für die Sperre nach falschen Zwei-Faktor-Codes): `log()` wirft dafür `ValueError` und
    schreibt nichts. Eine Erweiterung nimmt ihre eigene Kennung als Anfang (`<kennung>.<ereignis>`)."""

    async def log(
        self,
        *,
        action: str,
        outcome: str,
        target_type: str | None = None,
        target_id: str | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        actor: Actor | None = None,
    ) -> None:
        """Schreibt einen Eintrag unter der Erweiterung; `actor` gilt nur, wenn der Kern den Menschen gerade kennt
        (Nutzer der Anfrage, Vorschlagende oder Freigebende der laufenden Aktion), sonst `detail.on_behalf_of`."""
        ...


class WsHandle(Protocol):
    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        """Kanal wird automatisch zu ext.<ext_id>.<channel> erweitert. Nach einer Umbenennung (`legacy_ids`)
        geht dieselbe Nachricht zusätzlich an die Kanäle der alten Kennungen."""
        ...


class DbHandle(Protocol):
    def session(self) -> AsyncContextManager[Any]:
        """AsyncSession nur innerhalb von `async with` und nur für Tabellen mit dem Präfix der Extension; andere Tabellen,
        PRAGMA, ATTACH und Schemaänderungen lehnt SQLite ab (`sqlalchemy.exc.DatabaseError`)."""
        ...


# Proxys aus der Umgebung (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) werden nie benutzt: jede Anfrage geht direkt an die
# geprüfte Adresse, auch mit `insecure_tls=True`. `SSL_CERT_FILE` und `SSL_CERT_DIR` gelten weiter.
#
# Weiterleitungen (3xx) werden nie verfolgt: Eine Antwort mit Status 3xx kommt unverändert zurück, die Erweiterung fragt
# die neue Adresse selbst ab. `follow_redirects=True` wirft `ValueError` (die Zielprüfung kennt nur die Adresse des
# Aufrufs, nicht das Ziel einer Weiterleitung), auch mit `insecure_tls` und bei `stream()`; `False` ist erlaubt.
#
# `pinned_sha256` (ein SHA-256-Fingerabdruck, Schreibweise wie `sha256_fingerprint()` sie annimmt) bindet den Aufruf an
# GENAU dieses eine Zertifikat: Der Kern vergleicht den Fingerabdruck des Zertifikats, das der Server im TLS-Handshake
# zeigt, und bricht mit `CertificateMismatch` ab, bevor ein Byte der Anfrage gesendet ist. Die Prüfung über
# Zertifizierungsstellen und Rechnernamen entfällt dabei (das gepinnte Zertifikat ersetzt sie), deshalb braucht auch das
# die Berechtigung `net.outbound.insecure_tls`. Für `http://`-Adressen wirft es `ValueError` (dort gibt es kein
# Zertifikat), zusammen mit `insecure_tls=True` ebenfalls. Woher der Fingerabdruck kommt (z. B. ein Mensch hat ihn mit
# dem auf dem Server verglichen), entscheidet die Erweiterung; `peer_certificate()` liefert ihn, ohne etwas zu senden.
# Aufrufe mit `pinned_sha256` merken sich keine Cookies des Servers (ein Cookie geht nur mit, wenn der Aufruf ihn selbst
# setzt). Ältere Kerne kennen den Parameter nicht und lehnen den Aufruf ab (`TypeError`), statt ungeprüft zu senden.
class HttpHandle(Protocol):
    """Ausgehende HTTP-Aufrufe mit `net.outbound`; mit nur `net.outbound:<cidr>` gehen nur IP-Adressen aus diesen
    Bereichen, ein DNS-Name braucht das freie Recht, und `insecure_tls=True` (je Aufruf) braucht zusätzlich
    `net.outbound.insecure_tls`, sonst `PermissionDenied`."""

    async def get(
        self, url: str, *, insecure_tls: bool = False, pinned_sha256: str | None = None, **kwargs: Any
    ) -> Any: ...
    async def post(
        self, url: str, *, insecure_tls: bool = False, pinned_sha256: str | None = None, **kwargs: Any
    ) -> Any: ...
    async def request(
        self, method: str, url: str, *, insecure_tls: bool = False, pinned_sha256: str | None = None, **kwargs: Any
    ) -> Any: ...

    # Erst mit der üblichen Prüfung (Zertifizierungsstellen wie bei jedem `ctx.http`-Aufruf, Rechnername); scheitert
    # sie, noch einmal ohne Prüfung, nur um das Zertifikat zu lesen (`trusted=False`). Dieselbe Zielprüfung
    # (`net.outbound`) wie jeder Aufruf, kein Proxy aus der Umgebung.
    async def peer_certificate(self, url: str, *, timeout_s: float = 10.0) -> PeerCertificate:
        """Holt das Zertifikat, das der Server unter `url` (`https://` oder `wss://`) zeigt: nur der TLS-Handshake, ohne
        Anfrage, Kopfzeilen oder Zugangsdaten. Nicht erreichbar oder kein TLS: `OSError` bzw. `TimeoutError`;
        eine andere Adresse als `https://`/`wss://`: `ValueError`."""
        ...

    # Proxys aus der Umgebung (`HTTP_PROXY`, `HTTPS_PROXY`, `WS_PROXY`, `WSS_PROXY`, `SOCKS_PROXY`) werden nie benutzt.
    # Die Zielprüfung kennt nur die Start-Adresse, deshalb entsteht nach einer Weiterleitung keine zweite Verbindung.
    def websocket(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        subprotocols: list[str] | None = None,
        insecure_tls: bool = False,
        open_timeout_s: float = 10.0,
        pinned_sha256: str | None = None,
    ) -> AsyncContextManager["WebSocketConnection"]:
        """Ausgehende WebSocket-Verbindung (`ws://`/`wss://`) mit denselben `net.outbound`-/`insecure_tls`-Prüfungen wie
        die HTTP-Methoden, immer direkt an die geprüfte Adresse. Eine Weiterleitung (3xx) beim Verbindungsaufbau wird
        nie verfolgt: `async with` wirft ein `ConnectionError` mit fertigem deutschem Text."""
        ...


class WebSocketConnection(Protocol):
    async def send(self, data: bytes | str) -> None: ...
    async def recv(self) -> bytes | str: ...
    async def close(self) -> None: ...
    def __aiter__(self) -> Any: ...


# ---------------------------------------------------------------------------
# Der Kontext
# ---------------------------------------------------------------------------


# Andere Extensions erreicht eine Extension nur über `ctx.events` und deklarierte `requires`. Die Implementierung des
# Protokolls liegt im Kern unter `nodvard_deck/ext/context.py`.
@runtime_checkable
class ExtensionContext(Protocol):
    ext_id: str
    version: str
    data_dir: PurePosixPath
    """Beschreibbares Verzeichnis unter /data/ext/<ext_id>/ — z. B. für das Git-Repo
    des Script-Repositories."""

    api: ApiHandle
    ui: UiHandle
    connectors: ConnectorsHandle
    capabilities: CapabilitiesHandle
    actions: ActionsHandle
    hosts: HostsHandle
    exec: ExecHandle
    secrets: SecretsHandle
    settings: SettingsHandle
    scheduler: SchedulerHandle
    events: EventsHandle
    notify: NotifyHandle
    audit: AuditHandle
    ws: WsHandle
    db: DbHandle
    http: HttpHandle
    logger: Any

    def vault_use(self, handle: SecretHandleRef) -> AsyncContextManager[str]:
        """Materialisiert einen Secret-Wert nur innerhalb des Blocks (`async with ctx.vault_use(h) as key:`), mit dem
        Recht `secrets.read:<label>` für das gespeicherte Geheimnis zu `h.id`. Die Nutzung wird protokolliert (ohne den
        Wert), je Secret und Modul höchstens einmal pro Stunde."""
        ...

    def spawn(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str,
        restart: bool = True,
        restart_delay_s: float = 5.0,
    ) -> None:
        """Überwachter Hintergrundtask statt `asyncio.create_task()`: Der Kern bricht ihn beim Stop sauber ab, startet
        ihn nach einem Absturz neu und meldet wiederholte Abstürze als Extension-Gesundheitsproblem."""
        ...

    def notify_sync(
        self, title: str, body: str, severity: Severity = Severity.INFO
    ) -> None: ...
