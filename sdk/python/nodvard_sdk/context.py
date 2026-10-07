"""Der ExtensionContext — das einzige Objekt, das eine Extension vom Kern bekommt.

Jeder Handle ist permission-geprueft und auditiert. Was hier nicht steht, ist bewusst
nicht verfuegbar: direkter Zugriff auf Kern-Tabellen, das asyncio-Loop-Objekt, andere
Extensions (nur ueber ctx.events und deklarierte `requires`).

Diese Datei ist eine Protokoll-Definition — die Implementierung liegt im Kern unter
`nodvard_deck/ext/context.py`. Extensions importieren nur von hier.

Siehe docs/02-EXTENSION-API.md §2.
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
    Notification,
    NotifyResult,
    Risk,
    Severity,
)
from .widgets import HostRequirementSpec, HostToolSpec, PageSpec, WidgetSpec

# ---------------------------------------------------------------------------
# Teil-Handles
# ---------------------------------------------------------------------------


class ApiHandle(Protocol):
    current_actor: Any
    """FastAPI-Dependency (D-15): `actor: Actor = Depends(ctx.api.current_actor)` in einer
    Extension-Route liefert `Actor.user(...)` des angemeldeten Nutzers -- fuer
    `proposed_by` und das Audit-Log, damit dort der klickende Mensch steht statt der
    Extension."""

    def include_router(
        self,
        router: Any,
        *,
        prefix: str = "",
        tags: list[str] | None = None,
        permission: str | None = None,
        public: bool = False,
    ) -> None:
        """Montiert einen FastAPI-Router unter /api/v1/ext/<ext_id><prefix>.

        `permission`, falls gesetzt, macht die Bearer-Authentifizierung UND die
        RBAC-Pruefung dieser einen Berechtigung fuer ALLE Routen des Routers zur
        Kern-Aufgabe -- die Extension definiert dafuer keine eigene Auth-Abhaengigkeit.

        **Ohne `permission` verlangt der Kern mindestens eine gueltige Anmeldung**
        (401 ohne Token, keine bestimmte Berechtigung noetig). Frueher blieb so eine
        Route ganz ohne Pruefung und war ohne Anmeldung erreichbar; wer das Argument
        vergass, veroeffentlichte den Endpunkt fuer alle. Der Standard ist deshalb
        umgedreht (sichere Richtung): bestehende Aufrufe laufen weiter, nur ohne
        Anmeldung kommt nichts mehr durch. **Anmeldung ist keine Berechtigung:** Routen,
        die Daten zeigen oder etwas aendern, setzen `permission=` oder pruefen selbst.

        `public=True` ist die ausdrueckliche Ausnahme fuer Routen, die bewusst fuer
        jeden erreichbar sein sollen (z. B. ein Webhook): dann prueft der Kern nichts.
        Dafuer braucht die Erweiterung die Manifest-Berechtigung `api.public` (sonst
        `PermissionDenied`), und der Host schreibt die oeffentlichen Praefixe beim
        Einschalten ins Protokoll. `public=True` und `permission` zusammen sind ein
        Fehler (`InvalidRegistration`).

        Die Anmeldepruefung greift nur bei normalen FastAPI-Routen (`@router.get` usw.).
        WebSocket-Routen, `router.add_route()` und `router.mount()` (z. B. StaticFiles)
        lehnt der Kern ohne `public=True` mit `InvalidRegistration` ab; mit `public=True`
        muss die Erweiterung sie selbst absichern (z. B. Einmal-Ticket wie das Terminal).

        Der Host prueft die Router nach `setup()` und nach `on_start()`. Was eine
        Erweiterung erst spaeter zur Laufzeit an einen schon montierten Router haengt,
        etwa aus einem Hintergrundtask, sieht der Host nicht mehr: solche Routen muessen
        sich selbst absichern.
        """
        ...


class UiHandle(Protocol):
    def register_page(self, spec: PageSpec) -> None: ...
    def register_widget(self, spec: WidgetSpec) -> None: ...
    def register_nav(self, *, section: str, order: int = 100) -> None: ...
    def register_host_tool(self, spec: HostToolSpec) -> None:
        """Werkzeug-Kachel auf der Server-Seite passender Hosts (siehe HostToolSpec)."""
        ...

    def register_host_requirement(self, spec: HostRequirementSpec) -> None:
        """Meldet, was diese Extension auf passenden Servern braucht (Gruppe, root ohne
        Passwort, ein lesender Pruefbefehl) -- siehe HostRequirementSpec. Dieselbe `id`
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
        """Fragt Implementierungen *anderer* Extensions ab — nur fuer in `requires`
        deklarierte Abhaengigkeiten."""
        ...


class ActionsHandle(Protocol):
    def register(self, spec: ActionSpec) -> None: ...

    async def propose(self, request: ActionRequest, *, wait_s: float | None = None) -> GateDecision:
        """Der einzige Weg zu einer zustandsveraendernden Aktion.

        Gibt je nach Autonomie-Modus, Risiko und Regelwerk `proposed`, `approved` oder
        `denied` zurueck. Die Extension fuehrt nie selbst aus.

        Wird die Aktion sofort freigegeben (Modus `full`), laeuft sie im Hintergrund.
        `wait_s=None` wartet bis zum Ende (Hintergrund-Jobs, die das Ergebnis brauchen);
        HTTP-Routen geben `wait_s=REQUEST_WAIT_S` mit und bekommen danach ggf.
        `executing` zurueck.
        """
        ...

    async def result(self, action_id: str) -> Any: ...

    async def check_standing_approval(self, granted_by_user_id: str, *, risk: Risk) -> str | None:
        """Gaelte eine Dauerfreigabe (`StandingApproval`) von `granted_by_user_id` heute fuer
        Aktionen der Stufe `risk`? None = ja, sonst der Grund in einfachen Worten (Person
        gesperrt, kein Owner/Admin mehr ...). Dieselbe Pruefung wie im Gate, nur lesend --
        damit eine Seite nie "gilt" anzeigt, wo das Gate ablehnen wuerde. Braucht die
        Berechtigung `actions.standing_approval`."""
        ...

    async def list(self, *, correlation_id: str | None = None, limit: int = 20) -> list[Any]:
        """Juengste eigene Aktionen (Zeilen mit status/result/proposed_by_*/created_at),
        optional nur zu einer correlation_id -- z. B. die Laeufe eines Skripts."""
        ...

    async def proposer_labels(self, rows: list[Any]) -> dict[str, str]:
        """Lesbare Namen der Vorschlagenden zu Zeilen aus `list()`/`result()`:
        `{action_id: "admin" | "Skripte" | "KI" | ...}` -- statt `user/<uuid>` anzuzeigen.
        Nicht aufloesbar (geloeschter Nutzer) -> der rohe Wert `art/kennung`. Nur Zeilen der
        eigenen Extension werden aufgeloest (nach der Datenbank, nicht nach den uebergebenen
        Zeilen), fremde bekommen den rohen Wert. Die Namen gehen an jeden, den die Route der
        Extension durchlaesst -- die Route also absichern (z. B. `hosts.execute`)."""
        ...


class HostsHandle(Protocol):
    async def list(
        self, *, tag: str | None = None, group: str | None = None
    ) -> list[Host]: ...
    async def get(self, host_id: str) -> Host | None: ...
    async def by_name(self, name: str) -> Host | None: ...
    async def upsert_discovered(self, hosts: list[DiscoveredHost]) -> list[Host]:
        """Gleicht entdeckte Hosts gegen die Kern-Tabelle ab (Schluessel: ext + ref)."""
        ...


class ExecHandle(Protocol):
    async def run(
        self, host: Host, command: str, *, timeout_s: int = 60, user: str | None = None,
        log_command: str | None = None,
    ) -> ExecResult:
        """Nur fuer *lesende* Kommandos ohne Nebenwirkung (Status, Logs, Inventar).

        Alles Veraendernde geht ueber ctx.actions.propose() — das Gate prueft das anhand
        des ActionSpec, nicht anhand guter Absicht.

        `log_command`: maskierte Fassung des Befehls fuer die Audit-Zeile, falls `command`
        Geheimnisse enthaelt (nur relevant, wenn die Sperrliste greift).
        """
        ...

    def stream(self, host: Host, command: str) -> AsyncContextManager[AsyncIterator[bytes]]:
        """Wie `run()` nur fuer *lesende* Kommandos, deren Ausgabe laufend kommt und
        die nicht von selbst enden (Live-Logs). Beim Verlassen des Kontexts wird der
        entfernte Prozess beendet."""
        ...

    def open_shell(
        self, host: Host, *, cols: int, rows: int
    ) -> AsyncContextManager[Any]: ...
    def sftp(self, host: Host) -> AsyncContextManager[Any]: ...


@runtime_checkable
class SecretHandleRef(Protocol):
    """Eine Referenz, kein Wert. Der Klartext existiert nur in ctx.vault_use()."""

    id: str
    label: str
    kind: str


class SecretsHandle(Protocol):
    async def get_handle(self, label: str) -> SecretHandleRef: ...
    async def create(
        self, *, label: str, kind: str, value: str, description: str | None = None
    ) -> SecretHandleRef: ...
    async def exists(self, label: str) -> bool: ...
    async def delete(self, label: str) -> bool:
        """Loescht das Geheimnis mit diesem Label (gleiches Recht wie `get_handle`:
        `secrets.read:<label>`). Liefert `True`, wenn es eines gab, sonst `False`
        (kein Fehler). Gedacht fuer Zugangsdaten, die zu einem Ziel gehoeren, das die
        Extension gerade entfernt oder umstellt. Aeltere Kerne haben
        die Methode nicht."""
        ...


class SettingsHandle(Protocol):
    def declare(self, schema: dict[str, Any]) -> None: ...
    async def get(self) -> dict[str, Any]: ...
    async def set(self, values: dict[str, Any]) -> None: ...
    async def core(self, key: str) -> Any:
        """Lesender Zugriff auf freigegebene Kern-Einstellungen (z. B. maintenance.windows)."""
        ...


_JOB_TRIGGER: ContextVar[str | None] = ContextVar("nodvard_job_trigger", default=None)
"""Setzt der Kern-Scheduler fuer die Dauer eines Job-Laufs (siehe `current_job_trigger`)."""


def current_job_trigger() -> str | None:
    """Wie der gerade laufende Job gestartet wurde: "schedule" (nach Zeitplan), "manual"
    ("Jetzt ausfuehren" ueber die Jobs-Schnittstelle) oder None ausserhalb eines Job-Laufs.
    Damit kann ein `JobSpec.handler` z. B. eine Dauerfreigabe nur fuer echte Zeitplan-Laeufe
    nutzen."""
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
        """Ein Scheduler fuer alles.

        Keine Cron-Eintraege auf Hosts. Genau die haben den doppelten, ins Leere
        laufenden Eintrag produziert, den niemand bemerkt hat.
        """
        ...

    def validate_schedule(self, schedule: str) -> None:
        """Prueft einen Cron-Ausdruck (5 Felder) genau so, wie `register_job()` ihn braucht, ohne etwas
        anzumelden: `ValueError` mit deutscher Meldung, wenn er sich nicht planen laesst. Fuer Zeitplaene,
        die eine Erweiterung selbst speichert (z. B. den eines Skripts): erst pruefen, dann speichern --
        ein gespeicherter kaputter Zeitplan liesse `register_job()` beim naechsten Start scheitern."""
        ...

    async def trigger(self, job_id: str, **params: Any) -> str: ...


class EventsHandle(Protocol):
    async def publish(self, event: Event) -> None: ...
    def subscribe(
        self, pattern: str, handler: Callable[[Event], Awaitable[None]]
    ) -> None:
        """Pattern mit Stern als Platzhalter, z. B. action.* oder host.down."""
        ...


class NotifyHandle(Protocol):
    async def send(self, notification: Notification, *, raise_on_failure: bool = False) -> NotifyResult | None:
        """Legt die Benachrichtigung an und stellt sie über die Kanäle zu. Ein ausgefallener
        Kanal stoppt den Aufrufer nicht. Wer bei totalem Ausfall erneut versuchen will,
        übergibt `raise_on_failure=True`: dann wirft `NotificationNotDelivered`, wenn es
        Kanäle gab, aber keiner zugestellt hat (kein Kanal oder Wartungsfenster: kein Fehler).

        Liefert `NotifyResult` (Verlaufs-ID; `suppressed`: ein Wartungsfenster hat den Push
        unterdrückt; `delivered`: `True`, wenn mindestens ein Kanal die Meldung zugestellt hat,
        `False`, wenn keiner es getan hat -- kein Kanal eingerichtet, alle ausgefallen oder
        unterdrückt -- und `None`, wenn der Kern es nicht weiß). Ältere Kerne und Test-Doubles
        liefern `None` -- wie `suppressed=False` und `delivered=None` (unbekannt) behandeln."""
        ...

    async def would_suppress(self, *, host_id: str | None = None, host_ids: Sequence[str] | None = None) -> bool:
        """Würde eine Meldung mit diesem Host-Bezug (`payload["host_id"]` bzw.
        `payload["host_ids"]`) JETZT still geschaltet? Dieselbe Regel wie `send()`, legt aber
        nichts an -- für Wächter, die eine im Fenster stumme Meldung nach dem Fenster einmal
        hörbar nachholen wollen, ohne bei jeder Prüfung einen Verlaufseintrag zu erzeugen.
        Ohne Host-Bezug immer `False`."""
        ...


class AuditHandle(Protocol):
    """Aktionen, die mit `mfa.`, `auth.`, `login.` oder `system.` beginnen, schreibt nur der Kern (er liest
    sie wieder, etwa fuer die Sperre nach falschen Zwei-Faktor-Codes): `log()` wirft dafuer `ValueError` und
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
    ) -> None: ...


class WsHandle(Protocol):
    async def broadcast(self, channel: str, payload: dict[str, Any]) -> None:
        """Kanal wird automatisch zu ext.<ext_id>.<channel> erweitert. Nach einer Umbenennung (`legacy_ids`)
        geht dieselbe Nachricht zusaetzlich an die Kanaele der alten Kennungen."""
        ...


class DbHandle(Protocol):
    def session(self) -> AsyncContextManager[Any]:
        """AsyncSession, beschraenkt auf Tabellen mit dem Praefix der Extension."""
        ...


class HttpHandle(Protocol):
    """`insecure_tls=True` (WP-8-Blocker-Nachtrag) schaltet die TLS-Zertifikatspruefung
    fuer genau diesen Aufruf ab -- fuer Ziele mit einem selbstsignierten Zertifikat
    (z. B. Proxmox VEs Standard-Auslieferung). Braucht zusaetzlich zu `net.outbound`
    die eigene Berechtigung `net.outbound.insecure_tls` im Manifest; ohne sie wirft
    der Aufruf `PermissionDenied`, auch wenn `net.outbound` bereits gewaehrt ist.

    Proxys aus der Umgebung (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) werden nie benutzt:
    jede Anfrage geht direkt an die gepruefte Adresse, auch mit `insecure_tls=True`.
    `SSL_CERT_FILE` und `SSL_CERT_DIR` gelten weiter.

    Weiterleitungen (3xx) werden nie verfolgt: eine Antwort mit Status 3xx kommt unveraendert zurueck, die
    Erweiterung fragt die neue Adresse selbst ab. `follow_redirects=True` wirft `ValueError` (die Zielpruefung
    kennt nur die Adresse des Aufrufs, nicht das Ziel einer Weiterleitung), auch mit `insecure_tls` und bei
    `stream()`; `follow_redirects=False` ist erlaubt."""

    async def get(self, url: str, *, insecure_tls: bool = False, **kwargs: Any) -> Any: ...
    async def post(self, url: str, *, insecure_tls: bool = False, **kwargs: Any) -> Any: ...
    async def request(self, method: str, url: str, *, insecure_tls: bool = False, **kwargs: Any) -> Any: ...

    def websocket(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        subprotocols: list[str] | None = None,
        insecure_tls: bool = False,
        open_timeout_s: float = 10.0,
    ) -> AsyncContextManager["WebSocketConnection"]:
        """Ausgehende WebSocket-Verbindung (`ws://`/`wss://`) -- dieselben
        `net.outbound`-/`insecure_tls`-Pruefungen wie die HTTP-Methoden. Erster Bedarf:
        eine Hypervisor-Konsole, die nur ueber einen WebSocket erreichbar ist.

        Weiterleitungen (3xx) beim Verbindungsaufbau werden nie verfolgt: `async with` wirft
        ein `ConnectionError` mit fertigem deutschem Text, eine zweite Verbindung entsteht
        nicht (die Zielpruefung kennt nur die Start-Adresse).

        Proxys aus der Umgebung (`HTTP_PROXY`, `HTTPS_PROXY`, `WS_PROXY`, `WSS_PROXY`,
        `SOCKS_PROXY`) werden nie benutzt: die Verbindung geht immer direkt an die
        gepruefte Adresse."""
        ...


class WebSocketConnection(Protocol):
    async def send(self, data: bytes | str) -> None: ...
    async def recv(self) -> bytes | str: ...
    async def close(self) -> None: ...
    def __aiter__(self) -> Any: ...


# ---------------------------------------------------------------------------
# Der Kontext
# ---------------------------------------------------------------------------


@runtime_checkable
class ExtensionContext(Protocol):
    ext_id: str
    version: str
    data_dir: PurePosixPath
    """Beschreibbares Verzeichnis unter /data/ext/<ext_id>/ — z. B. fuer das Git-Repo
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
        """Materialisiert einen Secret-Wert nur innerhalb des Blocks.

            async with ctx.vault_use(h) as key:
                ...

        Jede Materialisierung schreibt eine Audit-Zeile (ohne den Wert).
        """
        ...

    def spawn(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str,
        restart: bool = True,
        restart_delay_s: float = 5.0,
    ) -> None:
        """Ueberwachter Hintergrundtask.

        Statt asyncio.create_task(): der Kern bricht ihn beim Stop sauber ab, startet
        ihn nach einem Absturz neu und meldet wiederholte Abstuerze als
        Extension-Gesundheitsproblem, statt sie im Log verschwinden zu lassen.
        """
        ...

    def notify_sync(
        self, title: str, body: str, severity: Severity = Severity.INFO
    ) -> None: ...
