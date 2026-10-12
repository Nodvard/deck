"""In-Process-Event-Bus mit Pattern-Abo (`ctx.events`); nur Prozessspeicher, kein Redis oder RabbitMQ."""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Awaitable, Callable

from nodvard_sdk.types import Event

logger = logging.getLogger("nodvard_deck.events")

Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[tuple[str, Handler]] = []

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subscribers.append((pattern, handler))

    def unsubscribe(self, pattern: str, handler: Handler) -> None:
        try:
            self._subscribers.remove((pattern, handler))
        except ValueError:
            pass

    def matching_handlers(self, name: str) -> list[Handler]:
        """`fnmatchcase` statt `fnmatch`: Letzteres normalisiert die Schreibweise über `os.path.normcase` und verhielte
        sich unter Windows anders als unter Linux."""
        return [h for pattern, h in self._subscribers if fnmatch.fnmatchcase(name, pattern)]

    async def publish(self, event: Event) -> None:
        """Ruft jeden passenden Handler auf und verteilt das Event an den WebSocket-Kanal `events`. Ein fehlerhafter
        Handler darf weder die anderen noch den Aufrufer abbrechen, sonst legte eine kaputte Extension den Bus lahm."""
        for handler in self.matching_handlers(event.name):
            try:
                await handler(event)
            except Exception:
                logger.exception("event_handler_failed name=%s", event.name)

        from . import rbac
        from .action_output import OUTPUT_PERMISSION, redact_action_event
        from .ws_hub import get_ws_hub

        # Die RBAC-Filterung hängt am Event-Namen (`core.rbac.permission_for_event()`), nicht am Kanal: `events` bleibt
        # ein Kanal, einzelne Nachrichten sind je nach Berechtigung sichtbar.
        message = {"name": event.name, "data": event.payload, "correlation_id": event.correlation_id}
        # `action.*` trägt den Befehl der Aktion (Handler im Prozess bekommen ihn). Über WebSocket sehen ihn nur Nutzer
        # mit Server-Recht, alle anderen bekommen die Aktion ohne Befehl (`core/action_output.py`).
        reduced = None
        if event.name.split(".", 1)[0] == "action":
            reduced = {**message, "data": redact_action_event(event.payload)}
        await get_ws_hub().publish(
            "events",
            message,
            required_permission=rbac.permission_for_event(event.name),
            reduced_payload=reduced,
            full_permission=OUTPUT_PERMISSION if reduced is not None else None,
        )


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def reset_event_bus() -> None:
    """Nur für Tests: der nächste `get_event_bus()` liefert eine frische, leere Instanz."""
    global _bus
    _bus = None
