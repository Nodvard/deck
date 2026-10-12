"""`GET /ws`: der generische WebSocket-Hub. Der Access-Token kommt als erste Nachricht, nicht im Header (den
Browser-WebSockets nicht setzen können) und nicht in der URL (Proxy-Logs). Die Rechtefilterung der Kanäle steht
in `core/ws_hub.py`."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ...config import Settings, get_settings
from ...core import security
from ...core.ws_hub import get_ws_hub
from ...db.session import session_scope
from ...models import User
from ...services import session_guard
from ...services.auth import user_permissions

router = APIRouter(tags=["ws"])
logger = logging.getLogger("nodvard_deck.ws")

_AUTH_TIMEOUT_S = 10.0
_PING_INTERVAL_S = 30.0
"""So oft pingt eine offene Verbindung und liest dabei Konto, Anmeldung und Rechte ihres Nutzers neu
(`services/session_guard.py`). Zusätzlich endet sie, wenn das Zugangs-Token abläuft, mit dem sie sich anmeldete."""

# Private WS-Fehlercodes (RFC 6455 erlaubt 4000-4999 für Anwendungen), wie in api/v1/terminal.py.
_WS_AUTH_TIMEOUT = 4401
_WS_AUTH_FAILED = 4401
_WS_ACCESS_ENDED = 4401
"""Konto deaktiviert, Passwort geändert, abgemeldet oder Zugangs-Token abgelaufen (laufende Verbindung)."""

TOKEN_EXPIRED_MESSAGE = "Die Anmeldung der Live-Verbindung ist abgelaufen."
"""Die Seite verbindet sich danach mit ihrem erneuerten Zugangs-Token selbst neu (lib/ws.ts)."""


async def _authenticate(
    raw_message: str, settings: Settings
) -> tuple[User, list[str], session_guard.SessionWatch, float] | None:
    """Prüft die Anmeldenachricht und liefert Nutzer, Rechte, die Anmeldung, an der die Verbindung hängt, und den
    Ablaufzeitpunkt (`exp`, Unix-Sekunden) des Tokens. Ein Token ohne `exp` gibt es nicht (core/security.py)."""
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
    expires_at = payload.get("exp")
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
        return None

    async with session_scope() as session:
        user = await session.get(User, payload["sub"])
        if user is None or not user.is_active:
            return None
        # Woran die Verbindung hängt: die Anmeldung (`sid` im Token, bei älteren Tokens irgendeine gültige des
        # Kontos) und das Passwort von jetzt. Eine schon beendete Anmeldung (abgemeldet, widerrufen) darf sich
        # nicht neu verbinden, solange ihr Zugangs-Token noch läuft.
        sid = payload.get("sid")
        watch = session_guard.SessionWatch(
            user_id=user.id,
            stamp=session_guard.credential_stamp(user),
            login_id=sid if isinstance(sid, str) else None,
        )
        if await watch.ended_reason(session) is not None:
            return None
        # `user.roles` und `role.permissions` sind per `lazy="selectin"` schon geladen (das Objekt kam aus
        # session.get()) und bleiben dank `expire_on_commit=False` (db/session.py) nutzbar, wenn die Session schließt.
        return user, user_permissions(user), watch, float(expires_at)


@router.websocket("/ws")
async def ws_multiplex(websocket: WebSocket, settings: Annotated[Settings, Depends(get_settings)]) -> None:
    # `accept()` zuerst, immer (Begründung in api/v1/terminal.py).
    await websocket.accept()

    try:
        first_message = await asyncio.wait_for(websocket.receive_text(), timeout=_AUTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        await websocket.close(code=_WS_AUTH_TIMEOUT)
        return
    except WebSocketDisconnect:
        # Der Client ist schon weg (z. B. Tab im Auth-Fenster geschlossen); ein `close()` auf einem getrennten
        # Socket wirft selbst wieder WebSocketDisconnect. Nichts mehr zu tun.
        return

    authenticated = await _authenticate(first_message, settings)
    if authenticated is None:
        try:
            await websocket.send_json({"type": "auth_error"})
        except Exception:  # noqa: BLE001
            pass
        await websocket.close(code=_WS_AUTH_FAILED)
        return
    user, permissions, watch, token_expires_at = authenticated

    await websocket.send_json({"type": "auth_ok"})

    hub = get_ws_hub()
    conn_id = hub.connect(websocket, user_id=user.id, permissions=permissions)
    stop = asyncio.Event()

    async def _recheck() -> str | None:
        """Grund (Klartext), warum die Verbindung enden muss, sonst `None`. Gilt noch alles, werden dabei die
        Berechtigungen im Hub aufgefrischt: eine geänderte Rolle wirkt so ohne neue Verbindung."""
        try:
            async with session_scope() as db_session:
                reason = await watch.ended_reason(db_session)
                if reason is None:
                    current = await db_session.get(User, watch.user_id)
                    if current is not None:
                        hub.set_permissions(conn_id, user_permissions(current))
                return reason
        except Exception:  # ein Datenbank-Hänger beendet keine laufende Verbindung
            logger.warning("ws_recheck_failed conn_id=%s", conn_id, exc_info=True)
            return None

    async def _pinger() -> str | None:
        """Pingt alle `_PING_INTERVAL_S` Sekunden und prüft dabei, ob Konto und Anmeldung noch gelten; läuft vorher das
        Zugangs-Token ab, endet die Verbindung dann. Rückgabe: der Grund, wenn sie enden muss, sonst `None`."""
        # Wird nie mitten in der Datenbankabfrage abgebrochen, sondern über `stop` gestoppt (siehe api/v1/terminal.py).
        # Eine beendete Verbindung kommt sofort aus dem Hub: bis zum Schließen geht keine Nachricht mehr an sie,
        # auch nicht auf Kanälen ohne Rechteprüfung.
        try:
            while True:
                # Höchstens bis zum Ablauf des Tokens warten, nicht eine ganze Runde darüber hinaus.
                wait_s = max(0.0, min(_PING_INTERVAL_S, token_expires_at - time.time()))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=wait_s)
                    return None
                except asyncio.TimeoutError:
                    pass
                if time.time() >= token_expires_at:
                    hub.disconnect(conn_id)
                    return TOKEN_EXPIRED_MESSAGE
                await websocket.send_json({"type": "ping"})
                reason = await _recheck()
                if reason is not None:
                    hub.disconnect(conn_id)
                    return reason
        except Exception:  # noqa: BLE001 - Verbindungsende ist kein Serverfehler
            return None

    async def _serve() -> None:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                return

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

    serve_task = asyncio.ensure_future(_serve())
    ping_task = asyncio.ensure_future(_pinger())
    close_code = 1000
    ended_reason: str | None = None
    try:
        await asyncio.wait({serve_task, ping_task}, return_when=asyncio.FIRST_COMPLETED)
        if serve_task.done():
            serve_task.result()  # ein Fehler der Schleife geht nach oben
        elif not ping_task.cancelled() and ping_task.exception() is None:
            ended_reason = ping_task.result()
            if ended_reason is not None:
                close_code = _WS_ACCESS_ENDED
    finally:
        serve_task.cancel()
        stop.set()
        try:
            await asyncio.wait_for(asyncio.gather(serve_task, ping_task, return_exceptions=True), timeout=5.0)
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            pass
        hub.disconnect(conn_id)
        if ended_reason is not None:
            logger.info("ws_access_ended conn_id=%s user_id=%s", conn_id, watch.user_id)
            try:
                await websocket.send_json({"type": "error", "payload": {"title": ended_reason}})
            except Exception:  # noqa: BLE001 - der Client ist möglicherweise schon weg
                pass
        try:
            await websocket.close(code=close_code)
        except Exception:  # noqa: BLE001 - möglicherweise schon zu
            pass
