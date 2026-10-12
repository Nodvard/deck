"""Gemeinsame Datentypen des SDK, alle serialisierbar.

Über die Kern/Extension-Grenze wandern nur Daten, damit Out-of-Process-Extensions später ohne Änderung laufen.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Risk(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Severity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class HostStatus(str, enum.Enum):
    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"
    MAINTENANCE = "maintenance"


class ActorType(str, enum.Enum):
    USER = "user"
    EXTENSION = "extension"
    AI = "ai"
    SCHEDULER = "scheduler"
    SYSTEM = "system"


class Actor(BaseModel):
    """Wer etwas veranlasst hat. Landet unverändert im Audit-Log."""

    type: ActorType
    id: str
    label: str | None = None

    @classmethod
    def ai(cls, model: str) -> "Actor":
        return cls(type=ActorType.AI, id=model, label=f"KI ({model})")

    @classmethod
    def user(cls, user_id: str, username: str | None = None) -> "Actor":
        return cls(type=ActorType.USER, id=user_id, label=username)

    @classmethod
    def extension(cls, ext_id: str) -> "Actor":
        return cls(type=ActorType.EXTENSION, id=ext_id, label=ext_id)

    @classmethod
    def scheduler(cls, job_id: str) -> "Actor":
        return cls(type=ActorType.SCHEDULER, id=job_id)


class Host(BaseModel):
    """Die Host-Identität des Kerns. Extensions entdecken Hosts, besitzen sie aber nicht."""

    id: str
    name: str
    display_name: str
    address: str
    os_family: str = "linux"
    kind: str | None = None
    status: HostStatus = HostStatus.UNKNOWN
    tags: list[str] = Field(default_factory=list)
    provider_ext_id: str | None = None
    provider_ref: str | None = None
    is_managed: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    has_credential: bool = True
    """Ob ein Standard-Zugangsdatensatz existiert, nie der Wert selbst. Vorgabe `True` aus Rückwärtskompatibilität
    für Code, der die Klasse ohne das Feld konstruiert; der Kern setzt es für echte Hosts immer explizit."""
    credential_username: str | None = None
    """Unter welchem Konto `ctx.exec.run()` auf diesem Host anmeldet (Benutzername des
    Standard-Zugangs, z. B. "root"); None ohne Zugang oder bei älteren Kernen. Nur der
    Name, nie das Geheimnis."""
    credential_port: int | None = None
    """SSH-Port, über den `ctx.exec.run()` auf diesem Host anmeldet (Port des Standard-Zugangs,
    meist 22); None ohne Zugang oder bei älteren Kernen."""


class DiscoveredHost(BaseModel):
    """Was ein HostProvider zurückgibt. Der Kern gleicht darüber `hosts` ab."""

    provider_ref: str
    name: str
    display_name: str | None = None
    address: str
    os_family: str = "linux"
    kind: str | None = None
    status: HostStatus = HostStatus.UNKNOWN
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    address_verified: bool = False
    """`True`, wenn der Anbieter die Adresse vom Gast selbst kennt (Gast-Agent, Container-Schnittstellen): Dann
    übernimmt der Kern sie auch für einen schon bekannten VM-/Container-Host. `False` (Vorgabe): möglicherweise nur
    ein Platzhalter, eine vorhandene, ggf. von Hand korrigierte Adresse bleibt stehen."""


class FileEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int | None = None
    modified_at: datetime | None = None
    mime: str | None = None
    sync_status: str | None = None  # für Quellen mit caps.sync_status
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileSourceCaps(BaseModel):
    write: bool = False
    rename: bool = False
    remove: bool = False
    mkdir: bool = False
    search: bool = False
    range_read: bool = False
    sync_status: bool = False
    quota: bool = False
    trash: bool = False


class SourceInfo(BaseModel):
    """Speist das Info-Panel des Dateimanagers (Quota, Health, Deep-Link)."""

    healthy: bool = True
    message: str | None = None
    used_bytes: int | None = None
    total_bytes: int | None = None
    deep_link: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class FilePlace(BaseModel):
    """Ein wichtiger Ort auf einem Server, den der Dateimanager als Abkürzung anbietet (Projektordner,
    Protokolle, Sicherungen). `path` ist ein absoluter Pfad mit `/` am Anfang, `label` eine kurze deutsche
    Beschriftung, `hint` ein Satz dazu. `icon` ist ein Symbolname wie bei `PageSpec.icon`."""

    label: str
    path: str
    icon: str = "folder"
    hint: str | None = None


class AppSuggestion(BaseModel):
    """Ein Vorschlag für eine App-Kachel (`AppSuggestionProvider`): Name und Adresse, die eine Erweiterung schon kennt.
    `source` nennt kurz, woher er stammt ("Nginx Proxy Manager"), `host` optional den Server. Der Kern prüft und kürzt
    alles wie bei einer von Hand angelegten App und zeigt nur Adressen mit `http://` oder `https://`."""

    name: str
    url: str
    source: str
    host: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class Notification(BaseModel):
    title: str
    body: str
    severity: Severity = Severity.INFO
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class NotifyResult(BaseModel):
    """Was `ctx.notify.send()` zurückgibt: die ID des Verlaufseintrags (er entsteht immer) und ob ein Wartungsfenster
    den Versand an die Kanäle (Push) unterdrückt hat. Ältere Kerne und Test-Doubles liefern `None`; wer den Wert
    auswertet, behandelt `None` wie `suppressed=False`."""

    notification_id: str
    suppressed: bool = False
    # `delivered`: `True`, wenn mindestens ein Kanal die Meldung wirklich zugestellt hat. `False`, wenn keiner es getan
    # hat: kein Kanal, alle ausgefallen oder noch nicht eingerichtet, oder ein Wartungsfenster hat den Push unterdrückt
    # (`suppressed`). `None`: unbekannt (ältere Kerne). So kann eine Seite ehrlich sagen, ob etwas auf dem Handy
    # ankommt.
    delivered: bool | None = None
    skipped: bool | None = None
    """`True`, wenn kein Kanal zugestellt hat, mindestens einer die Meldung bewusst übersprungen hat
    (`NotificationSkipped`, z. B. unter der Mindest-Stufe von ntfy) und keiner ausgefallen ist. Ist daneben ein
    Kanal ausgefallen, ist es `False` (der Ausfall geht vor). `None`: unbekannt (ältere Kerne)."""


class Event(BaseModel):
    """Nachricht auf dem Kern-Event-Bus. `name` ist punktgetrennt und abonnierbar."""

    name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class HealthReport(BaseModel):
    healthy: bool
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ConnectorHealth(BaseModel):
    ok: bool
    message: str | None = None
    latency_ms: int | None = None


class ExecResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False


class PeerCertificate(BaseModel):
    """Das Zertifikat, das ein Server beim Verbindungsaufbau zeigt (`ctx.http.peer_certificate()`).
    `sha256`: Fingerabdruck über das ganze Zertifikat (Großbuchstaben, Paare durch Doppelpunkte getrennt).
    `trusted`: gilt nach den Zertifizierungsstellen eines normalen `ctx.http`-Aufrufs und passt zum Rechnernamen."""

    # `sha256` ist derselbe Wert wie bei `openssl x509 -fingerprint -sha256` und in vielen Oberflächen (z. B. Proxmox VE
    # unter Knoten -> System -> Zertifikate). Bei `trusted=True` klappt ein Aufruf ohne `insecure_tls` und ohne
    # `pinned_sha256`. Die übrigen Felder sind nur zum Anzeigen (vom Server, ungeprüft, gekürzt): Inhaber, Aussteller,
    # Ablauf (ISO 8601, UTC) und weitere Namen.

    model_config = {"frozen": True}

    sha256: str
    trusted: bool
    subject: str | None = None
    issuer: str | None = None
    not_after: str | None = None
    alt_names: tuple[str, ...] = ()
