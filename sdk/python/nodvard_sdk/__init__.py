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
)
from .capabilities import (
    ALL_CAPABILITIES,
    ActionExecutor,
    AIProvider,
    BackupProvider,
    CommandRunner,
    ConsoleSession,
    ConsoleTarget,
    FileSource,
    FileSourceProvider,
    HostProvider,
    MetricsProvider,
    NotificationChannel,
    SearchProvider,
    ServiceCatalog,
    TerminalSession,
    TerminalTarget,
)
from .context import ConnectorType, ExtensionContext, JobSpec, SecretHandleRef
from .errors import (
    ActionBlocked,
    HostUnreachable,
    IncompatibleExtension,
    InvalidRegistration,
    NodvardError,
    NotificationNotDelivered,
    PermissionDenied,
    SecretUnavailable,
)
from .extension import NodvardExtension
from .manifest import KNOWN_PERMISSIONS, ExtensionManifest, load_manifest
from .types import (
    Actor,
    ActorType,
    ConnectorHealth,
    DiscoveredHost,
    Event,
    ExecResult,
    FileEntry,
    FileSourceCaps,
    HealthReport,
    Host,
    HostStatus,
    Notification,
    NotifyResult,
    Page,
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
    "KNOWN_PERMISSIONS",
    "ConnectorType",
    "JobSpec",
    "SecretHandleRef",
    # Aktionen / Gate
    "ActionRequest",
    "ActionResult",
    "ActionSpec",
    "ActionStatus",
    "GateDecision",
    "GateOutcome",
    "REQUEST_WAIT_S",
    "DryRunReport",
    # Capabilities
    "ALL_CAPABILITIES",
    "HostProvider",
    "MetricsProvider",
    "ActionExecutor",
    "TerminalTarget",
    "TerminalSession",
    "ConsoleTarget",
    "ConsoleSession",
    "FileSource",
    "FileSourceProvider",
    "AIProvider",
    "NotificationChannel",
    "ServiceCatalog",
    "BackupProvider",
    "SearchProvider",
    "CommandRunner",
    # Typen
    "Actor",
    "ActorType",
    "Risk",
    "Severity",
    "Host",
    "HostStatus",
    "DiscoveredHost",
    "FileEntry",
    "FileSourceCaps",
    "SourceInfo",
    "Page",
    "Event",
    "Notification",
    "NotifyResult",
    "HealthReport",
    "ConnectorHealth",
    "ExecResult",
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
    "HostUnreachable",
    "NotificationNotDelivered",
    "IncompatibleExtension",
    "InvalidRegistration",
]

# Alte Namen vor der Umbenennung: dieselben Objekte, mit DeprecationWarning (siehe _legacy.py).
_LEGACY_NAMES = {"LatticeExtension": "NodvardExtension", "LatticeError": "NodvardError"}
__getattr__ = legacy_getattr(__name__, _LEGACY_NAMES, globals())
if typing.TYPE_CHECKING:
    LatticeExtension = NodvardExtension
    LatticeError = NodvardError
