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


# ---------------------------------------------------------------------------
# Offene Verbindungen: Konto und Anmeldung werden bei jedem Ping erneut geprueft
# ---------------------------------------------------------------------------

_PASSWORD = "correct-horse-battery"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_viewer(http_base: str, owner_token: str, name: str = "zuschauer") -> str:
    """Legt einen Nutzer mit der Rolle `viewer` an (hat `hosts.read`); gibt seine ID zurueck."""
    async with AsyncClient(base_url=http_base) as ac:
        roles = {r["name"]: r["id"] for r in (await ac.get("/api/v1/roles", headers=_bearer(owner_token))).json()}
        r = await ac.post(
            "/api/v1/users", json={"username": name, "password": _PASSWORD, "role_ids": [roles["viewer"]]},
            headers=_bearer(owner_token),
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]


async def _login_cli(http_base: str, username: str) -> dict:
    async with AsyncClient(base_url=http_base) as ac:
        r = await ac.post(
            "/api/v1/auth/login", json={"username": username, "password": _PASSWORD, "client_type": "cli"}
        )
        assert r.status_code == 200, r.text
        return r.json()


async def _connect_and_subscribe(ws, token: str) -> None:
    await ws.send(json.dumps({"type": "auth", "token": token}))
    assert json.loads(await ws.recv()) == {"type": "auth_ok"}
    await ws.send(json.dumps({"type": "subscribe", "channel": "events"}))
    assert json.loads(await ws.recv()) == {"type": "subscribed", "channel": "events"}


async def _expect_end(ws, *, within: float = 8.0) -> tuple[int, str]:
    """Liest bis zum Ende der Verbindung; gibt (Close-Code, letzte Fehlermeldung) zurueck."""
    from websockets.exceptions import ConnectionClosed

    message = ""
    try:
        async with asyncio.timeout(within):
            while True:
                data = json.loads(await ws.recv())
                if data.get("type") == "error":
                    message = data["payload"]["title"]
    except ConnectionClosed as exc:
        return (exc.rcvd.code if exc.rcvd else -1), message
    raise AssertionError("Verbindung wurde nicht beendet")


async def _events_until_pong(ws) -> list[dict]:
    """Schickt einen Ping und gibt alle bis zur Antwort eingetroffenen Ereignisse zurueck (Pings
    des Servers werden uebersprungen)."""
    await ws.send(json.dumps({"type": "ping"}))
    events: list[dict] = []
    async with asyncio.timeout(5):
        while True:
            data = json.loads(await ws.recv())
            if data["type"] == "pong":
                return events
            if data["type"] == "event":
                events.append(data)


async def _run_change_case(running_app, db_session, pause_session_guard, monkeypatch, *, change):
    """Verbindet sich als `zuschauer` (Rolle viewer), fuehrt `change` aus und gibt zurueck, wie die
    Verbindung endet."""
    import websockets
    from nodvard_deck.api.v1 import ws as ws_module
    from nodvard_deck.core.ws_hub import get_ws_hub

    monkeypatch.setattr(ws_module, "_PING_INTERVAL_S", 0.2)
    http_base, ws_base = running_app
    owner_token = await _bootstrap_owner(http_base)
    user_id = await _create_viewer(http_base, owner_token)
    login = await _login_cli(http_base, "zuschauer")

    with pause_session_guard(ws_module) as lock:
        async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
            await _connect_and_subscribe(ws, login["access_token"])
            assert get_ws_hub().subscriber_count("events") == 1
            async with lock:  # siehe conftest.py `pause_session_guard`
                await change(http_base, owner_token, login, user_id)
                await db_session.commit()
            result = await _expect_end(ws)
    assert get_ws_hub().subscriber_count("events") == 0
    return result


@pytest.mark.asyncio
async def test_open_connection_ends_when_the_account_is_deactivated(running_app, db_session, pause_session_guard, monkeypatch):
    async def change(http_base, owner_token, login, user_id):
        async with AsyncClient(base_url=http_base) as ac:
            r = await ac.patch(f"/api/v1/users/{user_id}", json={"is_active": False}, headers=_bearer(owner_token))
            assert r.status_code == 200, r.text

    code, message = await _run_change_case(running_app, db_session, pause_session_guard, monkeypatch, change=change)
    assert code == 4401
    assert "nicht mehr aktiv" in message


@pytest.mark.asyncio
async def test_open_connection_ends_after_logout(running_app, db_session, pause_session_guard, monkeypatch):
    async def change(http_base, owner_token, login, user_id):
        async with AsyncClient(base_url=http_base) as ac:
            r = await ac.post("/api/v1/auth/logout", json={"refresh_token": login["refresh_token"]})
            assert r.status_code in (200, 204), r.text

    code, message = await _run_change_case(running_app, db_session, pause_session_guard, monkeypatch, change=change)
    assert code == 4401
    assert "abgemeldet" in message


@pytest.mark.asyncio
async def test_open_connection_ends_on_logout_even_with_another_login(running_app, db_session, pause_session_guard, monkeypatch):
    """Die Verbindung haengt an der Anmeldung, aus der sie aufgebaut wurde: eine zweite Anmeldung
    desselben Kontos (z. B. das Handy) haelt sie nach dem Abmelden nicht am Leben."""

    async def change(http_base, owner_token, login, user_id):
        await _login_cli(http_base, "zuschauer")
        async with AsyncClient(base_url=http_base) as ac:
            r = await ac.post("/api/v1/auth/logout", json={"refresh_token": login["refresh_token"]})
            assert r.status_code in (200, 204), r.text

    code, message = await _run_change_case(running_app, db_session, pause_session_guard, monkeypatch, change=change)
    assert code == 4401
    assert "abgemeldet" in message


@pytest.mark.asyncio
async def test_open_connection_ends_when_the_password_changes(running_app, db_session, pause_session_guard, monkeypatch):
    """Auch bei dem Geraet, das das Passwort aendert und angemeldet bleibt, wird die Verbindung
    beendet; die Seite baut sie danach mit dem aktuellen Stand selbst neu auf."""

    async def change(http_base, owner_token, login, user_id):
        async with AsyncClient(base_url=http_base) as ac:
            r = await ac.post(
                "/api/v1/me/password",
                json={"current_password": _PASSWORD, "new_password": "ganz-neues-passwort"},
                headers=_bearer(login["access_token"]),
            )
            assert r.status_code == 204, r.text

    code, message = await _run_change_case(running_app, db_session, pause_session_guard, monkeypatch, change=change)
    assert code == 4401
    assert "Passwort" in message


@pytest.mark.asyncio
async def test_open_connection_follows_a_role_change(running_app, db_session, pause_session_guard, monkeypatch):
    """Verliert der Nutzer seine Rolle, bleibt die Verbindung bestehen, bekommt aber die Ereignisse
    nicht mehr, fuer die sie die Berechtigung brauchte (`host.down` verlangt `hosts.read`)."""
    import websockets
    from nodvard_deck.api.v1 import ws as ws_module
    from nodvard_deck.core.events import get_event_bus
    from nodvard_sdk import Event

    monkeypatch.setattr(ws_module, "_PING_INTERVAL_S", 0.2)
    http_base, ws_base = running_app
    owner_token = await _bootstrap_owner(http_base)
    user_id = await _create_viewer(http_base, owner_token)
    login = await _login_cli(http_base, "zuschauer")

    with pause_session_guard(ws_module) as lock:
        async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
            await _connect_and_subscribe(ws, login["access_token"])
            await get_event_bus().publish(Event(name="host.down", payload={"host_id": "h1"}))
            assert [e["payload"]["name"] for e in await _events_until_pong(ws)] == ["host.down"]

            async with lock:
                async with AsyncClient(base_url=http_base) as ac:
                    r = await ac.patch(f"/api/v1/users/{user_id}", json={"role_ids": []}, headers=_bearer(owner_token))
                    assert r.status_code == 200, r.text
                await db_session.commit()

            # Die Pruefung laeuft alle 0,2 s: nach kurzer Zeit kommt `host.down` nicht mehr an.
            async with asyncio.timeout(8):
                while True:
                    await get_event_bus().publish(Event(name="host.down", payload={"host_id": "h2"}))
                    if not await _events_until_pong(ws):
                        break
                    await asyncio.sleep(0.2)
            # Die Verbindung selbst lebt weiter.
            await ws.send(json.dumps({"type": "ping"}))


@pytest.mark.asyncio
async def test_open_connection_survives_while_nothing_changed_and_after_renewals(running_app, db_session, pause_session_guard, monkeypatch):
    """Die regelmaessige Pruefung beendet eine gesunde Verbindung nicht, auch nicht nach dem
    Erneuern der Anmeldung (die Pruefung folgt der Kette). Wird die erneuerte Anmeldung
    abgemeldet, endet sie."""
    import websockets
    from nodvard_deck.api.v1 import ws as ws_module

    monkeypatch.setattr(ws_module, "_PING_INTERVAL_S", 0.1)
    http_base, ws_base = running_app
    owner_token = await _bootstrap_owner(http_base)
    await _create_viewer(http_base, owner_token)
    login = await _login_cli(http_base, "zuschauer")

    with pause_session_guard(ws_module) as lock:
        async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
            await _connect_and_subscribe(ws, login["access_token"])
            refresh_token = login["refresh_token"]
            for _ in range(2):
                async with lock:
                    async with AsyncClient(base_url=http_base) as ac:
                        r = await ac.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
                        assert r.status_code == 200, r.text
                        refresh_token = r.json()["refresh_token"]
                    await db_session.commit()
                await asyncio.sleep(0.5)
                assert await _events_until_pong(ws) == []

            async with lock:
                async with AsyncClient(base_url=http_base) as ac:
                    r = await ac.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
                    assert r.status_code in (200, 204), r.text
                await db_session.commit()
            code, message = await _expect_end(ws)
    assert code == 4401
    assert "abgemeldet" in message


@pytest.mark.asyncio
async def test_a_logged_out_login_cannot_connect_with_its_old_access_token(running_app):
    """Das Zugangs-Token gilt noch bis zu 15 Minuten; die Anmeldung dazu ist aber beendet. Damit
    baut sich keine neue Live-Verbindung mehr auf."""
    import websockets
    from websockets.exceptions import ConnectionClosed

    http_base, ws_base = running_app
    owner_token = await _bootstrap_owner(http_base)
    await _create_viewer(http_base, owner_token)
    login = await _login_cli(http_base, "zuschauer")
    async with AsyncClient(base_url=http_base) as ac:
        r = await ac.post("/api/v1/auth/logout", json={"refresh_token": login["refresh_token"]})
        assert r.status_code in (200, 204), r.text

    async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
        await ws.send(json.dumps({"type": "auth", "token": login["access_token"]}))
        assert json.loads(await ws.recv()) == {"type": "auth_error"}
        with pytest.raises(ConnectionClosed) as exc_info:
            await ws.recv()
        assert exc_info.value.rcvd.code == 4401


# ---------------------------------------------------------------------------
# Aenderungen direkt in der Datenbank (z. B. Notfall-Befehl auf dem Server), Ablauf des
# Zugangs-Tokens und der Hub waehrend des Schliessens
# ---------------------------------------------------------------------------


async def _make_viewer(db_session, username: str):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])  # hosts.read, notifications.read
    db_session.add(user)
    await db_session.flush()
    await db_session.commit()
    return user


async def _open_and_subscribe(websockets, ws_base: str, token: str, channel: str):
    ws = await websockets.connect(f"{ws_base}/api/v1/ws")
    async with asyncio.timeout(10):
        await ws.send(json.dumps({"type": "auth", "token": token}))
        assert json.loads(await ws.recv()) == {"type": "auth_ok"}
        await ws.send(json.dumps({"type": "subscribe", "channel": channel}))
        assert json.loads(await ws.recv())["type"] == "subscribed"
    return ws


@pytest.mark.asyncio
async def test_deactivated_user_is_disconnected_on_recheck(running_app, db_session, pause_session_guard, monkeypatch):
    """Eine offene Verbindung prueft das Konto regelmaessig neu: wer deaktiviert wurde,
    wird mit 4401 getrennt und bekommt nichts mehr."""
    import websockets

    from nodvard_deck.api.v1 import ws as ws_module

    monkeypatch.setattr(ws_module, "_PING_INTERVAL_S", 0.05)
    http_base, ws_base = running_app
    user = await _make_viewer(db_session, "mitbewohner")
    token = await _login(http_base, "mitbewohner", "whatever123")

    with pause_session_guard(ws_module) as lock:
        ws = await _open_and_subscribe(websockets, ws_base, token, "notifications")
        try:
            async with lock:
                user.is_active = False
                await db_session.commit()
            code, _message = await _expect_end(ws, within=10)
            assert code == 4401
        finally:
            await ws.close()


@pytest.mark.asyncio
async def test_revoked_connection_leaves_the_hub_before_it_is_closed(running_app, db_session, pause_session_guard, monkeypatch):
    """Zwischen Erkennen und Schliessen darf keine Nachricht mehr an die Verbindung gehen: sie
    ist schon aus dem Hub, wenn `close()` laeuft."""
    import websockets
    from starlette.websockets import WebSocket

    from nodvard_deck.api.v1 import ws as ws_module
    from nodvard_deck.core.ws_hub import get_ws_hub

    monkeypatch.setattr(ws_module, "_PING_INTERVAL_S", 0.05)
    subscribers_at_close: list[int] = []
    real_close = WebSocket.close

    async def _close(self, *args, **kwargs):
        subscribers_at_close.append(get_ws_hub().subscriber_count("notifications"))
        return await real_close(self, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "close", _close)
    http_base, ws_base = running_app
    user = await _make_viewer(db_session, "auszug")
    token = await _login(http_base, "auszug", "whatever123")

    with pause_session_guard(ws_module) as lock:
        ws = await _open_and_subscribe(websockets, ws_base, token, "notifications")
        try:
            async with lock:
                user.is_active = False
                await db_session.commit()
            await _expect_end(ws, within=10)
            assert subscribers_at_close and set(subscribers_at_close) == {0}
        finally:
            await ws.close()


@pytest.mark.asyncio
async def test_deleted_user_is_disconnected_on_recheck(running_app, db_session, pause_session_guard, monkeypatch):
    import websockets

    from nodvard_deck.api.v1 import ws as ws_module

    monkeypatch.setattr(ws_module, "_PING_INTERVAL_S", 0.05)
    http_base, ws_base = running_app
    user = await _make_viewer(db_session, "ex-admin")
    token = await _login(http_base, "ex-admin", "whatever123")

    with pause_session_guard(ws_module) as lock:
        ws = await _open_and_subscribe(websockets, ws_base, token, "events")
        try:
            async with lock:
                await db_session.delete(user)
                await db_session.commit()
            code, _message = await _expect_end(ws, within=10)
            assert code == 4401
        finally:
            await ws.close()


@pytest.mark.asyncio
async def test_user_with_revoked_role_stops_receiving_events(running_app, db_session, pause_session_guard, monkeypatch):
    """Wird die Rolle direkt in der Datenbank entzogen, bleibt die Verbindung offen (das Konto gibt
    es noch), liefert aber nach der naechsten Pruefung keine Ereignisse mehr, fuer die das Recht fehlt."""
    import websockets
    from sqlalchemy import delete

    from nodvard_deck.api.v1 import ws as ws_module
    from nodvard_deck.core.events import get_event_bus
    from nodvard_deck.models import user_roles
    from nodvard_sdk import Event

    monkeypatch.setattr(ws_module, "_PING_INTERVAL_S", 0.05)
    http_base, ws_base = running_app
    user = await _make_viewer(db_session, "herabgestuft")
    token = await _login(http_base, "herabgestuft", "whatever123")

    with pause_session_guard(ws_module) as lock:
        ws = await _open_and_subscribe(websockets, ws_base, token, "events")
        try:
            await get_event_bus().publish(Event(name="host.down", payload={"host_id": "h1"}))
            assert [e["payload"]["name"] for e in await _events_until_pong(ws)] == ["host.down"]

            async with lock:
                await db_session.execute(delete(user_roles).where(user_roles.c.user_id == user.id))
                await db_session.commit()

            # Auf die naechste Pruefung warten, ohne Zeitmessung: so lange Ereignisse senden, bis
            # ein Ping/Pong-Austausch zeigt, dass keines mehr ankommt.
            for _ in range(200):
                await get_event_bus().publish(Event(name="host.down", payload={"host_id": "h2"}))
                if not await _events_until_pong(ws):
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Der Nutzer bekommt auch nach dem Rollenentzug noch Ereignisse.")
        finally:
            await ws.close()


async def _short_access_token(settings, http_base: str, username: str, *, ttl_seconds: int | None) -> str:
    """Meldet `username` richtig an und gibt ein Zugangs-Token fuer DIESE Anmeldung (`sid`) zurueck,
    das nach `ttl_seconds` ablaeuft; `None`: ganz ohne Ablauf (`exp`)."""
    import jwt

    from nodvard_deck.core import security

    secret = settings.get_or_create_jwt_secret()
    login_token = await _login(http_base, username, "whatever123")
    claims = security.decode_jwt(login_token, secret=secret, expected_type="access")
    sid, user_id = claims["sid"], claims["sub"]
    if ttl_seconds is None:
        return jwt.encode({"sub": user_id, "typ": "access", "sid": sid}, secret, algorithm="HS256")
    return security.create_jwt(
        subject=user_id, token_type="access", secret=secret, ttl_seconds=ttl_seconds, extra_claims={"sid": sid}
    )


@pytest.mark.asyncio
async def test_connection_closes_when_its_access_token_expires(running_app, db_session, test_settings):
    """Konto und Anmeldung bleiben gueltig, aber das Token, mit dem sich die Verbindung angemeldet
    hat, laeuft ab: sie wird mit 4401 geschlossen, auch wenn der naechste Ping (30 s) noch weit weg
    ist -- wie bei HTTP. Die Seite verbindet sich dann mit dem erneuerten Token neu."""
    import websockets

    from nodvard_deck.api.v1 import ws as ws_module

    assert ws_module._PING_INTERVAL_S >= 30  # die Prüfrunde allein würde hier nicht rechtzeitig schliessen
    http_base, ws_base = running_app
    await _make_viewer(db_session, "kurzes-token")
    token = await _short_access_token(test_settings, http_base, "kurzes-token", ttl_seconds=2)

    ws = await _open_and_subscribe(websockets, ws_base, token, "notifications")
    try:
        await ws.send(json.dumps({"type": "ping"}))
        assert json.loads(await asyncio.wait_for(ws.recv(), timeout=10)) == {"type": "pong"}  # vor Ablauf offen
        code, message = await _expect_end(ws, within=10)
        assert code == 4401
        assert message == ws_module.TOKEN_EXPIRED_MESSAGE
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_expired_connection_leaves_the_hub_before_it_is_closed(running_app, db_session, test_settings, monkeypatch):
    """Auch beim Ablauf des Tokens geht zwischen Erkennen und Schliessen keine Nachricht mehr raus."""
    import websockets
    from starlette.websockets import WebSocket

    from nodvard_deck.core.ws_hub import get_ws_hub

    subscribers_at_close: list[int] = []
    real_close = WebSocket.close

    async def _close(self, *args, **kwargs):
        subscribers_at_close.append(get_ws_hub().subscriber_count("notifications"))
        return await real_close(self, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "close", _close)
    http_base, ws_base = running_app
    await _make_viewer(db_session, "abgelaufen")
    token = await _short_access_token(test_settings, http_base, "abgelaufen", ttl_seconds=2)

    ws = await _open_and_subscribe(websockets, ws_base, token, "notifications")
    try:
        assert get_ws_hub().subscriber_count("notifications") == 1
        await _expect_end(ws, within=10)
        assert subscribers_at_close and set(subscribers_at_close) == {0}
        assert get_ws_hub().subscriber_count("notifications") == 0
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_connection_with_a_valid_token_stays_open_across_rechecks(running_app, db_session, monkeypatch):
    import websockets

    from nodvard_deck.api.v1 import ws as ws_module

    monkeypatch.setattr(ws_module, "_PING_INTERVAL_S", 0.05)
    http_base, ws_base = running_app
    await _make_viewer(db_session, "dauergast")
    token = await _login(http_base, "dauergast", "whatever123")

    ws = await _open_and_subscribe(websockets, ws_base, token, "notifications")
    try:
        await asyncio.sleep(0.4)  # mehrere Prüfrunden
        assert await _events_until_pong(ws) == []
    finally:
        await ws.close()


@pytest.mark.asyncio
async def test_token_without_expiry_is_rejected(running_app, db_session, test_settings):
    """Auch mit gueltiger Anmeldung (`sid`): ein Zugangs-Token ohne Ablauf wird abgelehnt."""
    import websockets
    from websockets.exceptions import ConnectionClosed

    http_base, ws_base = running_app
    await _make_viewer(db_session, "ohne-ablauf")
    token = await _short_access_token(test_settings, http_base, "ohne-ablauf", ttl_seconds=None)

    async with websockets.connect(f"{ws_base}/api/v1/ws") as ws:
        await ws.send(json.dumps({"type": "auth", "token": token}))
        assert json.loads(await asyncio.wait_for(ws.recv(), timeout=10)) == {"type": "auth_error"}
        with pytest.raises(ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=10)
        assert exc_info.value.rcvd.code == 4401
