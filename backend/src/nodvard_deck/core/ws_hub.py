"""Der generische WS-Multiplex-Hub -- docs/04-API.md §4.

Eine Verbindung, viele Kanaele (docs/04 §4: "jede zusaetzliche Verbindung ist
Akkulaufzeit"). Anders als der Event-Bus (`core/events.py`, Muster-Abo mit
`fnmatch`) braucht dieser Hub kein Pattern-Matching: ein Client abonniert immer einen
KONKRETEN Kanalnamen (`jobs.<echte-id>`, nicht `jobs.*`) -- er kennt die ID schon aus
der REST-Antwort, die den Kanal ueberhaupt erst bekannt gemacht hat.

Bewusst getrennt vom Terminal-Socket (`api/v1/terminal.py`): binaere, hochvolumige
PTY-Frames wuerden den Steuerkanal hier verstopfen (docs/04 §4).

Keine Nachrichten-Wiederholung nach Verbindungsabbruch (docs/04 §4, D-09: keine
Message-Queue) -- ein Reconnect abonniert neu und holt den Stand per REST nach. Dieser
Hub haelt deshalb ausschliesslich fluechtigen Prozessspeicher, keine Zustellhistorie.

**RBAC-Filterung (Nachtrag, live als Luecke gemeldet):** docs/04 §4 verlangt fuer den
`events`-Kanal "gefiltert nach RBAC". Jede Verbindung traegt einen Schnappschuss der
Berechtigungen ihres Nutzers, beim Auth-Handshake ueber `services.auth.
user_permissions()` ermittelt (`api/v1/ws.py`) -- bewusst NICHT aus dem JWT-`perms`-
Claim gelesen, obwohl der existiert (`services.auth._issue_tokens()`): HTTP-Requests
leiten ihre Berechtigung ueber `user_has_permission()` bei JEDEM Request frisch aus
der DB ab, nie aus dem Claim, der SDK-Vertrag fuer WS soll keine zweite,
moeglicherweise abweichende Quelle der Wahrheit eroeffnen. Der Schnappschuss wird
nicht pro Nachricht, sondern bei der regelmaessigen Pruefung der offenen Verbindung
(`api/v1/ws.py`, alle 30 s) aufgefrischt (`set_permissions`): eine geaenderte Rolle
wirkt so nach hoechstens einer halben Minute, ein deaktiviertes oder abgemeldetes
Konto verliert die Verbindung ganz.
`publish(..., required_permission=...)` liefert eine Nachricht nur an Verbindungen,
deren Berechtigungen das erfuellen (`core.rbac.has_permission()`); ohne
`required_permission` (Default `None`) sieht sie jede abonnierte, authentifizierte
Verbindung -- die Voreinstellung fuer Kanaele ohne sensible Domaenen-Zuordnung
(`ext.*`, und Event-Praefixe ausserhalb von `core.rbac.EVENT_PREFIX_PERMISSIONS`).
"""

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
    """Reiner Verteiler: kennt FastAPIs `WebSocket` nicht direkt (nur die
    `send_json`-Form, die es erfuellt) -- so bleibt der Hub isoliert testbar mit
    einem einfachen Test-Double statt eines echten Sockets."""

    def __init__(self) -> None:
        self._connections: dict[int, _Connection] = {}
        self._next_id = 0

    def connect(self, socket: _SendsJson, *, user_id: str, permissions: list[str] | None = None) -> int:
        conn_id = self._next_id
        self._next_id += 1
        self._connections[conn_id] = _Connection(socket=socket, user_id=user_id, permissions=list(permissions or []))
        return conn_id

    def disconnect(self, conn_id: int) -> None:
        self._connections.pop(conn_id, None)

    def set_permissions(self, conn_id: int, permissions: list[str]) -> None:
        """Ersetzt den Berechtigungs-Schnappschuss einer offenen Verbindung (die Rolle des
        Nutzers hat sich geaendert)."""
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
        """Ein einzelner tot/kaputter Socket darf weder andere Empfaenger noch den
        Aufrufer stoppen -- dasselbe Prinzip wie `EventBus.publish()`. `required_
        permission`, falls gesetzt, filtert die Zustellung zusaetzlich zum
        Abonnement -- ein Client OHNE die Berechtigung bleibt abonniert (kein Fehler),
        bekommt aber genau DIESE Nachricht nicht.

        `reduced_payload` und `full_permission` zusammen: Verbindungen ohne `full_permission`
        bekommen statt `payload` die gekuerzte Fassung (z. B. ohne Befehl einer Aktion) --
        dieselbe Nachricht, pro Empfaenger in der passenden Tiefe."""
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
    """Nur fuer Tests."""
    global _hub
    _hub = None
