"""Gemeinsame Datentypen des SDK.

Alles hier ist serialisierbar. Das ist Absicht: der ExtensionContext ist so geschnitten,
dass über die Kern/Extension-Grenze nur Daten wandern — damit ein späterer Wechsel auf
Out-of-Process-Extensions möglich bleibt, ohne dass eine Extension sich ändert
(siehe docs/01-ARCHITECTURE.md §2).
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
    """Ob ein Standard-
    Zugangsdatensatz existiert -- NIE der Wert selbst (docs/03 §3 Invariante 1 bleibt
    unangetastet). Default `True` aus Rueckwaertskompatibilitaet fuer Code, der diese
    Klasse ohne das Feld konstruiert (z. B. bestehende Tests) -- `host_to_sdk()`
    (services/hosts.py) setzt es fuer echte Hosts immer explizit."""


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
    """`True`, wenn der Anbieter die Adresse vom Gast SELBST kennt (Gast-Agent,
    Container-Schnittstellen) -- dann uebernimmt der Kern sie auch fuer einen schon
    bekannten VM-/Container-Host. `False` (Default): moeglicherweise nur ein
    Platzhalter; eine vorhandene, ggf. von Hand korrigierte Adresse bleibt stehen."""


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
    """Was `ctx.notify.send()` zurückgibt: die ID des Verlaufseintrags und ob ein
    Wartungsfenster den Versand an die Kanäle (Push) unterdrückt hat. Der Eintrag im
    Verlauf entsteht in beiden Fällen.

    Ältere Kerne und Test-Doubles liefern `None` -- wer den Wert auswertet, behandelt
    `None` wie `suppressed=False` (docs/02-EXTENSION-API.md §2)."""

    notification_id: str
    suppressed: bool = False


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
