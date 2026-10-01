"""GET /ws -- Ende-zu-Ende gegen eine echte laufende App (`running_app`-Fixture,
siehe conftest.py und test_terminal_api.py fuer dasselbe Muster), docs/04-API.md §4."""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import AsyncClient


async def _bootstrap_owner(base_url: str) -> str:
    async with AsyncClient(base_url=base_url) as ac:
        await ac.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
        login = await ac.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]


@pytest.mark.asyncio
async def test_auth_handshake_then_subscribe_and_receive_event(running_app):
    import websockets

    http_base, ws_base = running_app
    token = await _bootstrap_owner(http_base)

    async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        auth_reply = json.loads(await ws.recv())
        assert auth_reply == {"type": "auth_ok"}

        await ws.send(json.dumps({"type": "subscribe", "channel": "events"}))
        sub_reply = json.loads(await ws.recv())
        assert sub_reply == {"type": "subscribed", "channel": "events"}

        from nodvard_deck.core.events import get_event_bus
        from nodvard_sdk import Event

        await get_event_bus().publish(Event(name="test.thing", payload={"x": 1}))

        event_msg = json.loads(await ws.recv())
        assert event_msg["type"] == "event"
        assert event_msg["channel"] == "events"
        assert event_msg["payload"]["name"] == "test.thing"
        assert event_msg["payload"]["data"] == {"x": 1}


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery(running_app):
    import websockets

    http_base, ws_base = running_app
    token = await _bootstrap_owner(http_base)

    async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        await ws.recv()
        await ws.send(json.dumps({"type": "subscribe", "channel": "events"}))
        await ws.recv()
        await ws.send(json.dumps({"type": "unsubscribe", "channel": "events"}))
        unsub_reply = json.loads(await ws.recv())
        assert unsub_reply == {"type": "subscribed", "channel": "events", "payload": {"subscribed": False}}

        from nodvard_deck.core.events import get_event_bus
        from nodvard_sdk import Event

        await get_event_bus().publish(Event(name="test.thing2"))

        # Kein Event mehr faellig -- ein Ping darf noch kommen, aber kein "event".
        await ws.send(json.dumps({"type": "ping"}))
        reply = json.loads(await ws.recv())
        assert reply == {"type": "pong"}


@pytest.mark.asyncio
async def test_missing_auth_within_timeout_closes_connection(running_app, monkeypatch):
    import websockets
    from websockets.exceptions import ConnectionClosed

    from nodvard_deck.api.v1 import ws as ws_module

    monkeypatch.setattr(ws_module, "_AUTH_TIMEOUT_S", 0.2)
    _, ws_base = running_app

    async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
        with pytest.raises(ConnectionClosed) as exc_info:
            await ws.recv()
        assert exc_info.value.rcvd.code == 4401


@pytest.mark.asyncio
async def test_client_disconnect_during_auth_window_does_not_raise_server_side(
    running_app, monkeypatch, caplog
):
    """Live beim Boot-Test gefunden: ein Extension-Seitenwechsel im Frontend
    oeffnet/schliesst WS-Verbindungen schneller, als es ein manueller Test je getan
    haette -- schliesst ein Client VOR der ersten Auth-Nachricht, wirft
    `await websocket.close(...)` im Timeout-Zweig selbst nochmal `WebSocketDisconnect`
    (man kann keinen bereits getrennten Socket schliessen). Regressionstest fuer den
    Fix in api/v1/ws.py: `WebSocketDisconnect` bekommt jetzt einen eigenen Zweig ohne
    `close()`-Aufruf."""
    import logging

    import websockets

    from nodvard_deck.api.v1 import ws as ws_module

    monkeypatch.setattr(ws_module, "_AUTH_TIMEOUT_S", 0.2)
    _, ws_base = running_app

    with caplog.at_level(logging.ERROR):
        ws = await websockets.connect(f"{ws_base}/api/v1/ws")
        # `ws.close()` waere eine GEORDNETE Schliess-Verhandlung (Client- und Server-
        # Close-Frame) -- das reproduziert den Fehler NICHT, weil Starlettes `send()`
        # nur bei einem echten OSError auf dem Transport wirft (websockets.py:85-89),
        # nicht bei einer bereits sauber abgeschlossenen Verhandlung. `transport.abort()`
        # kappt die TCP-Verbindung sofort ohne Handshake -- das simuliert den
        # abgebrochenen Tab/die Navigation aus dem echten Boot-Test.
        ws.transport.abort()
        # Dem Server Zeit geben, das 10s(hier 0.2s)-Auth-Fenster ablaufen zu lassen
        # UND den (vor dem Fix) fehlerhaften zweiten close()-Versuch auszufuehren.
        await asyncio.sleep(0.5)

    for record in caplog.records:
        assert "WebSocketDisconnect" not in record.getMessage()
        if record.exc_info is not None:
            assert record.exc_info[0] is not None
            assert "WebSocketDisconnect" not in record.exc_info[0].__name__


@pytest.mark.asyncio
async def test_invalid_token_is_rejected(running_app):
    import websockets
    from websockets.exceptions import ConnectionClosed

    _, ws_base = running_app
    async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
        await ws.send(json.dumps({"type": "auth", "token": "not-a-real-token"}))
        error_msg = json.loads(await ws.recv())
        assert error_msg == {"type": "auth_error"}
        with pytest.raises(ConnectionClosed) as exc_info:
            await ws.recv()
        assert exc_info.value.rcvd.code == 4401


async def _login(base_url: str, username: str, password: str) -> str:
    async with AsyncClient(base_url=base_url) as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]


@pytest.mark.asyncio
async def test_events_channel_filters_by_rbac_per_event(running_app, db_session):
    """docs/04-API.md §4: der `events`-Kanal ist "gefiltert nach RBAC" -- ein Nutzer
    ohne `hosts.read` sieht `host.down` nicht, ein Nutzer MIT `hosts.read` schon,
    beide auf demselben Kanal, derselben Verbindung, ohne dass das Abonnieren je
    fehlschlaegt."""
    import websockets
    from nodvard_deck.core import security
    from nodvard_deck.core.events import get_event_bus
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service
    from nodvard_sdk import Event

    http_base, ws_base = running_app

    roles = await auth_service.ensure_builtin_roles(db_session)
    no_role_user = User(username="noperm", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer_user = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer_user.roles.append(roles["viewer"])  # viewer hat hosts.read
    db_session.add_all([no_role_user, viewer_user])
    await db_session.flush()
    await db_session.commit()

    no_perm_token = await _login(http_base, "noperm", "whatever123")
    viewer_token = await _login(http_base, "viewer1", "whatever123")

    async with websockets.connect(f"{ws_base}/api/v1/ws") as ws_no_perm, \
            websockets.connect(f"{ws_base}/api/v1/ws") as ws_viewer:
        await ws_no_perm.send(json.dumps({"type": "auth", "token": no_perm_token}))
        assert json.loads(await ws_no_perm.recv()) == {"type": "auth_ok"}
        await ws_no_perm.send(json.dumps({"type": "subscribe", "channel": "events"}))
        assert json.loads(await ws_no_perm.recv()) == {"type": "subscribed", "channel": "events"}

        await ws_viewer.send(json.dumps({"type": "auth", "token": viewer_token}))
        assert json.loads(await ws_viewer.recv()) == {"type": "auth_ok"}
        await ws_viewer.send(json.dumps({"type": "subscribe", "channel": "events"}))
        assert json.loads(await ws_viewer.recv()) == {"type": "subscribed", "channel": "events"}

        await get_event_bus().publish(Event(name="host.down", payload={"host_id": "h1"}))

        # Der Viewer bekommt es -- hosts.read reicht.
        viewer_msg = json.loads(await ws_viewer.recv())
        assert viewer_msg["payload"]["name"] == "host.down"

        # Der berechtigungslose Client bekommt es NICHT -- stattdessen kommt als
        # naechstes garantiert Zustellbares ein Ping, das beweist, dass die
        # Verbindung selbst gesund ist und nur dieses eine Event fehlt.
        await ws_no_perm.send(json.dumps({"type": "ping"}))
        no_perm_msg = json.loads(await ws_no_perm.recv())
        assert no_perm_msg == {"type": "pong"}


@pytest.mark.asyncio
async def test_unknown_message_type_gets_error_reply(running_app):
    import websockets

    http_base, ws_base = running_app
    token = await _bootstrap_owner(http_base)

    async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        await ws.recv()
        await ws.send(json.dumps({"type": "not-a-real-type"}))
        reply = json.loads(await ws.recv())
        assert reply["type"] == "error"
