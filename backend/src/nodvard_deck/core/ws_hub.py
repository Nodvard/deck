"""Der WebSocket-Hub: eine Verbindung, viele Kanäle, Zustellung gefiltert nach Berechtigung."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .rbac import has_permission

logger = logging.getLogger("nodvard_deck.ws")

# Ein Client, der nicht mehr liest, darf die Zustellung an die anderen nicht aufhalten.
SEND_TIMEOUT_S = 5.0
MAX_CONNECTIONS_PER_USER = 20
"""Mehr gleichzeitige Live-Verbindungen eines Kontos lehnt der Hub ab (jeder offene Tab braucht eine)."""


class ConnectionLimitReached(Exception):
    """Das Konto hat schon `MAX_CONNECTIONS_PER_USER` offene Verbindungen."""


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
        if sum(1 for c in self._connections.values() if c.user_id == user_id) >= MAX_CONNECTIONS_PER_USER:
            raise ConnectionLimitReached(user_id)
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
        audience: Callable[[str, list[str]], bool] | None = None,
    ) -> None:
        """Verteilt `payload` an alle Verbindungen, die den Kanal abonniert haben und `required_permission` erfüllen.
        Ohne die Berechtigung bleibt ein Client abonniert, bekommt aber diese Nachricht nicht (ohne `full_permission` die
        gekürzte `reduced_payload`); `audience(user_id, permissions)` grenzt die Empfänger weiter ein. Ein toter Socket
        stoppt weder andere Empfänger noch den Aufrufer."""
        full = {"type": "event", "channel": channel, "payload": payload}
        reduced = (
            {"type": "event", "channel": channel, "payload": reduced_payload}
            if reduced_payload is not None and full_permission is not None
            else full
        )

        def message_for(conn: _Connection) -> dict[str, Any] | None:
            if channel not in conn.channels:
                return None
            if required_permission is not None and not has_permission(conn.permissions, required_permission):
                return None
            if audience is not None and not audience(conn.user_id, conn.permissions):
                return None
            if reduced is not full and not has_permission(conn.permissions, full_permission):  # type: ignore[arg-type]
                return reduced
            return full

        deliveries = [
            self._deliver(conn_id, channel, message_for)
            for conn_id, conn in list(self._connections.items())
            if channel in conn.channels
        ]
        # Gleichzeitig, damit eine hängende Verbindung die anderen nicht aufhält.
        await asyncio.gather(*deliveries)

    async def _deliver(
        self, conn_id: int, channel: str, message_for: Callable[[_Connection], dict[str, Any] | None]
    ) -> None:
        # Erst hier entscheiden: Bis die Zustellung läuft, können sich Rechte und Abonnements schon geändert haben.
        conn = self._connections.get(conn_id)
        if conn is None:
            return
        message = message_for(conn)
        if message is None:
            return
        try:
            async with asyncio.timeout(SEND_TIMEOUT_S):
                await conn.socket.send_json(message)
        except TimeoutError:
            logger.warning("ws_hub_send_failed conn_id=%s channel=%s reason=timeout", conn_id, channel)
            self.disconnect(conn_id)
            await self._close_quietly(conn.socket)
        except Exception:  # noqa: BLE001
            logger.warning("ws_hub_send_failed conn_id=%s channel=%s", conn_id, channel)

    @staticmethod
    async def _close_quietly(socket: _SendsJson) -> None:
        close = getattr(socket, "close", None)
        if close is None:
            return
        try:
            async with asyncio.timeout(SEND_TIMEOUT_S):
                await close()
        except Exception:  # noqa: BLE001
            pass


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
