"""nodvard_sdk — der stabile Vertrag zwischen Kern und Extensions.

Eine Extension importiert ausschliesslich von hier. Der Kern importiert nie eine
Extension. Das ist die eine Regel, aus der die gesamte Architektur folgt
(docs/01-ARCHITECTURE.md §1).

**Alter Name:** Bis zur Umbenennung hiess das Paket `lattice_sdk`, die Basisklassen `LatticeExtension` und
`LatticeError`. Alle drei Namen funktionieren weiter, mit DeprecationWarning und als **dieselben Objekte**
(`lattice_sdk` ist ein Alias dieses Pakets, siehe `sdk/python/lattice_sdk/__init__.py`; `LatticeExtension is
NodvardExtension`). Entfernt wird davon nichts ohne angekuendigte Major-Version (docs/04-API.md).
"""

from __future__ import annotations

import typing

from ._legacy import legacy_getattr
from .actions import (
    REQUEST_WAIT_S,
    ActionRequest,
    ActionResult,
    ActionSpec,
    ActionStatus,
    DryRunReport,
    GateDecision,
    GateOutcome,
    StandingApproval,
)
from .addresses import same_target
from .capabilities import (
    ALL_CAPABILITIES,
    ActionExecutor,
    AIProvider,
    AppSuggestionProvider,
    BackupProvider,
    CommandRunner,
    ConnectionReceiver,
    ConsoleSession,
    ConsoleTarget,
    FilePlaceProvider,
    FileSource,
    FileSourceProvider,
    HostProvider,
    MetricsProvider,
    NotificationChannel,
    ScopedTerminalTarget,
    SearchProvider,
    ServiceCatalog,
    TerminalSession,
    TerminalTarget,
)
from .context import ConnectorType, ExtensionContext, JobSpec, SecretHandleRef, current_job_trigger
from .errors import (
    ActionBlocked,
    CertificateMismatch,
    HostUnreachable,
    IncompatibleExtension,
    InvalidRegistration,
    NodvardError,
    NotificationNotDelivered,
    NotificationSkipped,
    PermissionDenied,
    SecretNotOwned,
    SecretUnavailable,
)
from .extension import NodvardExtension
from .http import max_body_bytes, sha256_fingerprint
from .manifest import KNOWN_PERMISSIONS, ExtensionManifest, load_manifest
from .types import (
    Actor,
    ActorType,
    AppSuggestion,
    ConnectorHealth,
    DiscoveredHost,
    Event,
    ExecResult,
    FileEntry,
    FilePlace,
    FileSourceCaps,
    HealthReport,
    Host,
    HostStatus,
    Notification,
    NotifyResult,
    Page,
    PeerCertificate,
    Risk,
    Severity,
    SourceInfo,
)
from .version import API_VERSION, is_compatible
from .widgets import (
    ActionsView,
    Badge,
    ChartView,
    Column,
    GaugeView,
    GridSize,
    ListItem,
    ListView,
    LogView,
    MarkdownView,
    MobileFallback,
    HostRequirementSpec,
    HostToolSpec,
    PageSpec,
    Refresh,
    Series,
    StatusGridView,
    StatView,
    TableView,
    Tone,
    WidgetAction,
    WidgetSpec,
)

__all__ = [
    "API_VERSION",
    "is_compatible",
    "NodvardExtension",
    "LatticeExtension",  # alter Name, dasselbe Objekt (Alias mit DeprecationWarning)
    "ExtensionContext",
    "ExtensionManifest",
    "load_manifest",
    "same_target",
    "KNOWN_PERMISSIONS",
    "max_body_bytes",
    "sha256_fingerprint",
    "ConnectorType",
    "JobSpec",
    "SecretHandleRef",
    "current_job_trigger",
    # Aktionen / Gate
    "ActionRequest",
    "ActionResult",
    "ActionSpec",
    "ActionStatus",
    "GateDecision",
    "GateOutcome",
    "REQUEST_WAIT_S",
    "DryRunReport",
    "StandingApproval",
    # Capabilities
    "ALL_CAPABILITIES",
    "HostProvider",
    "MetricsProvider",
    "ActionExecutor",
    "TerminalTarget",
    "TerminalSession",
    "ScopedTerminalTarget",
    "ConsoleTarget",
    "ConsoleSession",
    "FileSource",
    "FileSourceProvider",
    "FilePlaceProvider",
    "AIProvider",
    "NotificationChannel",
    "ServiceCatalog",
    "BackupProvider",
    "SearchProvider",
    "CommandRunner",
    "ConnectionReceiver",
    # Typen
    "Actor",
    "ActorType",
    "Risk",
    "Severity",
    "Host",
    "HostStatus",
    "DiscoveredHost",
    "FileEntry",
    "FilePlace",
    "AppSuggestion",
    "AppSuggestionProvider",
    "FileSourceCaps",
    "SourceInfo",
    "Page",
    "Event",
    "Notification",
    "NotifyResult",
    "HealthReport",
    "ConnectorHealth",
    "ExecResult",
    "PeerCertificate",
    # Widgets / Seiten
    "WidgetSpec",
    "HostToolSpec",
    "HostRequirementSpec",
    "PageSpec",
    "MobileFallback",
    "GridSize",
    "Refresh",
    "WidgetAction",
    "Badge",
    "Tone",
    "StatView",
    "ListView",
    "ListItem",
    "TableView",
    "Column",
    "ChartView",
    "Series",
    "StatusGridView",
    "GaugeView",
    "MarkdownView",
    "ActionsView",
    "LogView",
    # Fehler
    "NodvardError",
    "LatticeError",  # alter Name, dasselbe Objekt (Alias mit DeprecationWarning)
    "PermissionDenied",
    "ActionBlocked",
    "SecretUnavailable",
    "SecretNotOwned",
    "HostUnreachable",
    "NotificationNotDelivered",
    "NotificationSkipped",
    "IncompatibleExtension",
    "InvalidRegistration",
    "CertificateMismatch",
]

# Alte Namen vor der Umbenennung: dieselben Objekte, mit DeprecationWarning (siehe _legacy.py).
_LEGACY_NAMES = {"LatticeExtension": "NodvardExtension", "LatticeError": "NodvardError"}
__getattr__ = legacy_getattr(__name__, _LEGACY_NAMES, globals())
if typing.TYPE_CHECKING:
    LatticeExtension = NodvardExtension
    LatticeError = NodvardError
