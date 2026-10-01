"""`GET /ws` -- der generische WS-Multiplex-Hub (docs/04-API.md §4).

Authentifizierung laeuft NICHT wie bei jedem anderen Endpunkt ueber
`Authorization: Bearer` (WebSockets kennen keine benutzerdefinierten Header vom
Browser aus) -- die erste Nachricht nach dem Verbinden traegt den Access-Token
(docs/04 §4: "keine Tokens in der URL, sie landen in Proxy-Logs").

RBAC-Filterung des `events`-Kanals (docs/04 §4): siehe `core/ws_hub.py`
Modul-Docstring fuer die volle Begruendung -- kurz: jede Verbindung bekommt beim
Auth-Handshake einen Berechtigungs-Schnappschuss (`services.auth.
user_permissions()`), `core.rbac.permission_for_event()`/`EVENT_PREFIX_PERMISSIONS`
ordnet jedem Event-Namens-Praefix eine Berechtigung zu, `WsHub.publish(...,
required_permission=...)` liefert nur an Verbindungen, die sie erfuellen."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ...config import Settings, get_settings
from ...core import security
from ...core.ws_hub import get_ws_hub
from ...db.session import session_scope
from ...models import User

router = APIRouter(tags=["ws"])
logger = logging.getLogger("nodvard_deck.ws")

_AUTH_TIMEOUT_S = 10.0
_PING_INTERVAL_S = 30.0

# Private WS-Fehlercodes (RFC 6455 erlaubt 4000-4999 fuer Anwendungen) -- analog zu
# api/v1/terminal.py, das dasselbe Schema fuer denselben Zweck nutzt.
_WS_AUTH_TIMEOUT = 4401
_WS_AUTH_FAILED = 4401


async def _authenticate(raw_message: str, settings: Settings) -> tuple[User, list[str]] | None:
    try:
        envelope = json.loads(raw_message)
    except ValueError:
        return None
    if envelope.get("type") != "auth":
        return None
    token = envelope.get("token")
    if not isinstance(token, str):
        return None

    try:
        payload = security.decode_jwt(
            token, secret=settings.get_or_create_jwt_secret(), expected_type="access"
        )
    except security.TokenError:
        return None

    async with session_scope() as session:
        user = await session.get(User, payload["sub"])
        if user is None or not user.is_active:
            return None
        # `user_permissions()` liest `user.roles`/`role.permissions`, beides ueber
        # `lazy="selectin"` bereits eager-geladen (das Objekt kam aus session.get(),
        # nicht aus session.add() -- D-12 betrifft das hier nicht). Losgeloest von der
        # Session zurueckgeben, die hier gleich schliesst -- `expire_on_commit=False`
        # (db/session.py) haelt die bereits geladenen Attribute verwendbar.
        from ...services.auth import user_permissions

        return user, user_permissions(user)


@router.websocket("/ws")
async def ws_multiplex(websocket: WebSocket, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    # `accept()` zuerst, immer -- siehe api/v1/terminal.py fuer die volle Begruendung
    # (ASGI konsumiert das ausstehende "websocket.connect" sonst nie).
    await websocket.accept()

    try:
        first_message = await asyncio.wait_for(websocket.receive_text(), timeout=_AUTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        await websocket.close(code=_WS_AUTH_TIMEOUT)
        return
    except WebSocketDisconnect:
        # Der Client ist schon weg (z.B. Tab/Navigation waehrend des 10s-Auth-Fensters
        # geschlossen) -- ein `close()` auf einem bereits getrennten Socket wirft selbst
        # wieder WebSocketDisconnect (live gefunden beim WP-7-Boot-Test: ein
        # nachladender Extension-Loader oeffnet/schliesst WS-Verbindungen schneller, als
        # es vorher ein manueller Test je getan haette). Nichts mehr zu tun.
        return

    authenticated = await _authenticate(first_message, settings)
    if authenticated is None:
        try:
            await websocket.send_json({"type": "auth_error"})
        except Exception:  # noqa: BLE001
            pass
        await websocket.close(code=_WS_AUTH_FAILED)
        return
    user, permissions = authenticated

    await websocket.send_json({"type": "auth_ok"})

    hub = get_ws_hub()
    conn_id = hub.connect(websocket, user_id=user.id, permissions=permissions)

    async def _pinger() -> None:
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL_S)
                await websocket.send_json({"type": "ping"})
        except Exception:  # noqa: BLE001 - Verbindungsende ist kein Serverfehler
            pass

    ping_task = asyncio.ensure_future(_pinger())
    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break

            try:
                envelope = json.loads(raw)
            except ValueError:
                await websocket.send_json({"type": "error", "payload": {"title": "Ungültiges JSON."}})
                continue

            msg_type = envelope.get("type")
            channel = envelope.get("channel")

            if msg_type == "subscribe" and isinstance(channel, str):
                hub.subscribe(conn_id, channel)
                await websocket.send_json({"type": "subscribed", "channel": channel})
            elif msg_type == "unsubscribe" and isinstance(channel, str):
                hub.unsubscribe(conn_id, channel)
                await websocket.send_json({"type": "subscribed", "channel": channel, "payload": {"subscribed": False}})
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "pong":
                continue
            else:
                await websocket.send_json(
                    {"type": "error", "payload": {"title": f"Unbekannter Nachrichtentyp '{msg_type}'."}}
                )
    finally:
        ping_task.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(ping_task, return_exceptions=True), timeout=5.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        hub.disconnect(conn_id)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001 - moeglicherweise schon zu
            pass
