"""Die Basisklasse, von der jede Extension erbt."""

from __future__ import annotations

from typing import Any

from .context import ExtensionContext
from .types import HealthReport


class NodvardExtension:
    """Einstiegspunkt einer Extension, im Manifest über `entrypoint` referenziert."""

    # `setup` und `on_start` sind getrennt, weil der Kern erst alle Registrierungen einsammelt (und Router, Navigation
    # und Widget-Katalog in einem Rutsch baut), bevor ein Hintergrundtask läuft und Ereignisse in einen halbfertigen
    # Bus schiebt.
    async def setup(self, ctx: ExtensionContext) -> None:
        """Nur registrieren: Routen, Seiten, Widgets, Connector-Typen, Capabilities, Aktionen, Jobs, Einstellungen.
        Kein I/O, kein Netzwerk, kein DB-Schreiben. Wirft sie, wird die Extension als `state=error` markiert und
        übersprungen, der Kern startet trotzdem."""
        raise NotImplementedError

    async def on_start(self, ctx: ExtensionContext) -> None:
        """Hintergrundarbeit anlaufen lassen; Tasks über `ctx.spawn()`, nie über `asyncio.create_task()`."""

    async def on_stop(self, ctx: ExtensionContext) -> None:
        """Aufräumen. Über `ctx.spawn` gestartete Tasks beendet der Kern selbst."""

    async def health(self, ctx: ExtensionContext) -> HealthReport:
        """Speist Registry-UI und Notification-Center."""
        return HealthReport(healthy=True)

    async def on_settings_changed(
        self, ctx: ExtensionContext, values: dict[str, Any]
    ) -> None:
        """Optional. Wird nach einer Änderung der Extension-Einstellungen aufgerufen."""

    async def on_secret_changed(self, ctx: ExtensionContext, label: str) -> None:
        """Optional. Ein lesbares Geheimnis wurde gespeichert, ersetzt oder gelöscht; zwischengespeicherte Anmeldungen
        hier verwerfen."""

    async def drop_data(self, ctx: ExtensionContext) -> None:
        """Optional. Bei der Deinstallation, wenn der Admin "Daten entfernen" wählt."""
