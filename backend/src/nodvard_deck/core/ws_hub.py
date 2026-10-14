"""Der WebSocket-Hub: eine Verbindung, viele Kanäle, Zustellung gefiltert nach Berechtigung."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from .rbac import has_permission

logger = logging.getLogger("nodvard_deck.ws")


class _SendsJson(Protocol):
    async def send_json(self, data: Any) -> None: ...


@dataclass
class _Connection:
    socket: _SendsJson
    user_id: str
    permissions: list[str] = field(default_factory=list)
    channels: set[str] = field(default_factory=set)


class WsHub:
    """Reiner Verteiler über die `send_json`-Form des Sockets (so mit einem Test-Double testbar). Kanäle sind konkrete
    Namen (`jobs.<id>`), anders als im Event-Bus kein Muster."""

    def __init__(self) -> None:
        # Nur flüchtiger Prozessspeicher: Nach einem Verbindungsabbruch gibt es keine Wiederholung, ein Reconnect
        # abonniert neu und holt den Stand per REST nach.
        self._connections: dict[int, _Connection] = {}
        self._next_id = 0

    def connect(self, socket: _SendsJson, *, user_id: str, permissions: list[str] | None = None) -> int:
        # `permissions` ist ein Schnappschuss aus `services.auth.user_permissions()`, bewusst nicht aus dem JWT-Claim:
        # HTTP leitet Rechte bei jedem Request frisch aus der Datenbank ab, WS soll keine zweite Quelle der Wahrheit
        # haben. `api/v1/ws.py` frischt ihn alle 30 s auf (`set_permissions`); deaktivierte oder abgemeldete Konten
        # verlieren die Verbindung.
        conn_id = self._next_id
        self._next_id += 1
        self._connections[conn_id] = _Connection(socket=socket, user_id=user_id, permissions=list(permissions or []))
        return conn_id

    def disconnect(self, conn_id: int) -> None:
        self._connections.pop(conn_id, None)

    def set_permissions(self, conn_id: int, permissions: list[str]) -> None:
        """Ersetzt den Berechtigungs-Schnappschuss einer offenen Verbindung (die Rolle des Nutzers änderte sich)."""
        conn = self._connections.get(conn_id)
        if conn is not None:
            conn.permissions = list(permissions)

    def subscribe(self, conn_id: int, channel: str) -> None:
        conn = self._connections.get(conn_id)
        if conn is not None:
            conn.channels.add(channel)

    def unsubscribe(self, conn_id: int, channel: str) -> None:
        conn = self._connections.get(conn_id)
        if conn is not None:
            conn.channels.discard(channel)

    def subscriber_count(self, channel: str) -> int:
        return sum(1 for c in self._connections.values() if channel in c.channels)

    async def publish(
        self,
        channel: str,
        payload: dict[str, Any],
        *,
        required_permission: str | None = None,
        reduced_payload: dict[str, Any] | None = None,
        full_permission: str | None = None,
    ) -> None:
        """Verteilt `payload` an alle Verbindungen, die den Kanal abonniert haben und `required_permission` erfüllen.
        Ohne die Berechtigung bleibt ein Client abonniert, bekommt aber diese Nachricht nicht (ohne `full_permission` die
        gekürzte `reduced_payload`); ein toter Socket stoppt weder andere Empfänger noch den Aufrufer."""
        full = {"type": "event", "channel": channel, "payload": payload}
        reduced = (
            {"type": "event", "channel": channel, "payload": reduced_payload}
            if reduced_payload is not None and full_permission is not None
            else full
        )
        for conn_id, conn in list(self._connections.items()):
            if channel not in conn.channels:
                continue
            if required_permission is not None and not has_permission(conn.permissions, required_permission):
                continue
            message = full
            if reduced is not full and not has_permission(conn.permissions, full_permission):  # type: ignore[arg-type]
                message = reduced
            try:
                await conn.socket.send_json(message)
            except Exception:  # noqa: BLE001
                logger.warning("ws_hub_send_failed conn_id=%s channel=%s", conn_id, channel)


_hub: WsHub | None = None


def get_ws_hub() -> WsHub:
    global _hub
    if _hub is None:
        _hub = WsHub()
    return _hub


def reset_ws_hub() -> None:
    """Nur für Tests."""
    global _hub
    _hub = None
