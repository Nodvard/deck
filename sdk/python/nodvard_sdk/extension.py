"""Die Basisklasse, von der jede Extension erbt."""

from __future__ import annotations

import typing
from typing import Any

from ._legacy import legacy_getattr
from .context import ExtensionContext
from .types import HealthReport


class NodvardExtension:
    """Einstiegspunkt einer Extension. Im Manifest ueber `entrypoint` referenziert.
    (Bis zur Umbenennung `LatticeExtension`: der alte Name bleibt als Alias, es ist dieselbe Klasse.)

    Die Trennung von `setup` und `on_start` ist keine Kosmetik: der Kern sammelt erst
    *alle* Registrierungen ein (und baut Router, Navigation und Widget-Katalog in einem
    Rutsch), bevor irgendein Hintergrundtask laeuft und Ereignisse in einen halbfertigen
    Bus schiebt.
    """

    async def setup(self, ctx: ExtensionContext) -> None:
        """Nur registrieren: Routen, Seiten, Widgets, Connector-Typen, Capabilities,
        Aktionen, Jobs, Einstellungen.

        Kein I/O, kein Netzwerk, kein DB-Schreiben. Wirft diese Methode, wird die
        Extension als `state=error` markiert und uebersprungen — der Kern startet
        trotzdem. Eine defekte Erweiterung darf nie das Dashboard lahmlegen.
        """
        raise NotImplementedError

    async def on_start(self, ctx: ExtensionContext) -> None:
        """Hintergrundarbeit anlaufen lassen. Tasks ueber ctx.spawn(), nie ueber
        asyncio.create_task()."""

    async def on_stop(self, ctx: ExtensionContext) -> None:
        """Aufraeumen. Ueber ctx.spawn gestartete Tasks beendet der Kern selbst."""

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        """Speist Registry-UI und Notification-Center."""
        return HealthReport(healthy=True)

    async def on_settings_changed(
        self, ctx: ExtensionContext, values: dict[str, Any]
    ) -> None:
        """Optional. Wird nach einer Aenderung der Extension-Einstellungen aufgerufen."""

    async def drop_data(self, ctx: ExtensionContext) -> None:
        """Optional. Bei der Deinstallation, wenn der Admin "Daten entfernen" waehlt."""


# Alter Name vor der Umbenennung: dasselbe Objekt, mit DeprecationWarning (siehe _legacy.py).
_LEGACY_NAMES = {"LatticeExtension": "NodvardExtension"}
__getattr__ = legacy_getattr(__name__, _LEGACY_NAMES, globals())
if typing.TYPE_CHECKING:
    LatticeExtension = NodvardExtension
