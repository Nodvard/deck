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
required_permission=...)` liefert nur an Verbindungen, die sie erfuellen.

Offene Verbindungen: Konto und Anmeldung werden nicht nur beim Aufbau geprueft, sondern bei
jedem Ping (alle 30 s) erneut (`services/session_guard.py`, wie beim Terminal). Wer deaktiviert,
abgemeldet oder wessen Passwort geaendert wurde, verliert die Verbindung (Code 4401); geaenderte
Rollen frischen den Berechtigungs-Schnappschuss auf. Dazu endet die Verbindung, wenn das
Zugangs-Token ablaeuft, mit dem sie sich angemeldet hat (wie bei HTTP nach hoechstens 15 Minuten);
die Seite verbindet sich dann mit dem erneuerten Token selbst neu (lib/ws.ts)."""

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
"""So oft pingt eine offene Verbindung und liest dabei Konto, Anmeldung und Rechte ihres Nutzers
neu. Zusaetzlich endet sie, wenn das Zugangs-Token ablaeuft, mit dem sie sich angemeldet hat."""

# Private WS-Fehlercodes (RFC 6455 erlaubt 4000-4999 fuer Anwendungen) -- analog zu
# api/v1/terminal.py, das dasselbe Schema fuer denselben Zweck nutzt.
_WS_AUTH_TIMEOUT = 4401
_WS_AUTH_FAILED = 4401
_WS_ACCESS_ENDED = 4401
"""Konto deaktiviert, Passwort geaendert, abgemeldet oder Zugangs-Token abgelaufen (laufende Verbindung)."""

TOKEN_EXPIRED_MESSAGE = "Die Anmeldung der Live-Verbindung ist abgelaufen."
"""Die Seite verbindet sich danach mit ihrem erneuerten Zugangs-Token selbst neu (lib/ws.ts)."""


async def _authenticate(
    raw_message: str, settings: Settings
) -> tuple[User, list[str], session_guard.SessionWatch, float] | None:
    """Prueft die Anmeldenachricht. Liefert Nutzer, Rechte, die Anmeldung, an der die Verbindung
    haengt, und den Ablaufzeitpunkt (`exp`, Unix-Sekunden) des Tokens; ein Token ohne `exp` gibt
    es nicht (core/security.py setzt es immer)."""
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
        # Woran die Verbindung haengt: die Anmeldung (`sid` im Token, ohne `sid` bei aelteren
        # Tokens irgendeine gueltige des Kontos) und das Passwort von jetzt. Eine Anmeldung, die
        # schon beendet ist (abgemeldet, widerrufen), darf sich auch nicht neu verbinden, solange
        # ihr Zugangs-Token noch laeuft.
        sid = payload.get("sid")
        watch = session_guard.SessionWatch(
            user_id=user.id,
            stamp=session_guard.credential_stamp(user),
            login_id=sid if isinstance(sid, str) else None,
        )
        if await watch.ended_reason(session) is not None:
            return None
        # `user_permissions()` liest `user.roles`/`role.permissions`, beides ueber
        # `lazy="selectin"` bereits eager-geladen (das Objekt kam aus session.get(),
        # nicht aus session.add() -- D-12 betrifft das hier nicht). Losgeloest von der
        # Session zurueckgeben, die hier gleich schliesst -- `expire_on_commit=False`
        # (db/session.py) haelt die bereits geladenen Attribute verwendbar.
        return user, user_permissions(user), watch, float(expires_at)


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
    user, permissions, watch, token_expires_at = authenticated

    await websocket.send_json({"type": "auth_ok"})

    hub = get_ws_hub()
    conn_id = hub.connect(websocket, user_id=user.id, permissions=permissions)
    stop = asyncio.Event()

    async def _recheck() -> str | None:
        """Grund (Klartext), warum die Verbindung enden muss, sonst `None`. Gilt noch alles, werden
        dabei die Berechtigungen im Hub aufgefrischt (eine geaenderte Rolle wirkt so ohne neue Verbindung)."""
        try:
            async with session_scope() as db_session:
                reason = await watch.ended_reason(db_session)
                if reason is None:
                    current = await db_session.get(User, watch.user_id)
                    if current is not None:
                        hub.set_permissions(conn_id, user_permissions(current))
                return reason
        except Exception:  # ein Datenbank-Haenger beendet keine laufende Verbindung
            logger.warning("ws_recheck_failed conn_id=%s", conn_id, exc_info=True)
            return None

    async def _pinger() -> str | None:
        """Pingt alle `_PING_INTERVAL_S` Sekunden und prueft dabei, ob Konto und Anmeldung noch
        gelten; laeuft vorher das Zugangs-Token der Verbindung ab, endet sie zu diesem Zeitpunkt.
        Ein einziger Takt fuer beides. Rueckgabe: der Grund, wenn die Verbindung enden muss; sonst
        `None` (Verbindung weg oder Abbau). Eine beendete Verbindung kommt sofort aus dem Hub: bis
        sie geschlossen ist, geht keine Nachricht mehr an sie (auch nicht auf Kanaelen ohne
        Rechtepruefung). Wird nie mitten in der Datenbankabfrage abgebrochen, sondern ueber `stop`
        gestoppt (siehe api/v1/terminal.py)."""
        try:
            while True:
                # Hoechstens bis zum Ablauf des Tokens warten, nicht eine ganze Runde darueber hinaus.
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
            serve_task.result()  # ein Fehler der Schleife geht wie bisher nach oben
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
            except Exception:  # noqa: BLE001 - der Client ist moeglicherweise schon weg
                pass
        try:
            await websocket.close(code=close_code)
        except Exception:  # noqa: BLE001 - moeglicherweise schon zu
            pass
