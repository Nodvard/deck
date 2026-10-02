"""In-Process-Event-Bus mit Pattern-Abo (docs/02-EXTENSION-API.md `ctx.events`).

Nur Prozessspeicher -- kein Redis/RabbitMQ, D-09 gilt sinngemaess: keine zusaetzliche
Pflicht-Komponente fuer etwas, das ein Dict und `fnmatch` genauso zuverlaessig loesen.

Der kanonische `Event`-Typ ist `nodvard_sdk.types.Event` -- derselbe Vertrag, den
Extensions ab WP-3 ueber `ctx.events` sehen werden, nicht eine zweite, kernseitige
Definition.
"""

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
        """`fnmatchcase` statt `fnmatch`: Letzteres normalisiert Gross-/Kleinschreibung
        ueber `os.path.normcase` und wuerde sich unter Windows (Entwicklung) anders
        verhalten als unter Linux (Produktion, D-10) -- ein Dialektunterschied, den
        D-02 fuer die Datenbank bewusst ausschliesst und der hier genauso wenig
        hineingehoert."""
        return [h for pattern, h in self._subscribers if fnmatch.fnmatchcase(name, pattern)]

    async def publish(self, event: Event) -> None:
        """Ruft jeden passenden Handler auf. Ein einzelner fehlerhafter Handler darf
        weder die anderen Handler noch den Aufrufer abbrechen -- sonst koennte eine
        einzelne kaputte Extension (ab WP-3) den gesamten Bus fuer alle lahmlegen.

        Seit WP-6 zusaetzlich ein Fan-out auf den WS-Kanal `events` (docs/04 §4:
        "alles, was der Event-Bus publiziert, gefiltert nach RBAC") -- der Bus selbst
        weiss dabei nichts von WebSockets, er ruft nur denselben Hub auf, den auch
        `ctx.ws.broadcast()` und die Job-/Notification-Pipelines benutzen. Die
        RBAC-Filterung haengt am Event-NAMEN (`core.rbac.permission_for_event()`),
        nicht am Kanal -- `events` bleibt EIN Kanal fuer alle, nur einzelne
        Nachrichten darin sind je nach Berechtigung unterschiedlich sichtbar."""
        for handler in self.matching_handlers(event.name):
            try:
                await handler(event)
            except Exception:
                logger.exception("event_handler_failed name=%s", event.name)

        from . import rbac
        from .action_output import OUTPUT_PERMISSION, redact_action_event
        from .ws_hub import get_ws_hub

        message = {"name": event.name, "data": event.payload, "correlation_id": event.correlation_id}
        # `action.*` tragen den Befehl der Aktion (Handler im Prozess bekommen ihn, z. B. die
        # Skripte-Erweiterung). Ueber WebSocket sehen ihn nur Nutzer mit Server-Recht;
        # alle anderen bekommen die Aktion ohne Befehl (`core/action_output.py`).
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
    """Nur fuer Tests: erzwingt beim naechsten `get_event_bus()` eine frische, leere
    Instanz -- Pendant zu `db.session.reset_engine_cache()`."""
    global _bus
    _bus = None
