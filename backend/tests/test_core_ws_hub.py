"""Der WS-Multiplex-Hub als reiner Verteiler (docs/04-API.md §4)
-- ohne echten Socket, siehe test_ws_api.py fuer
die Ende-zu-Ende-Variante gegen eine echte laufende App."""

from __future__ import annotations

import pytest

from nodvard_deck.core.ws_hub import WsHub


class _FakeSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        if self.fail:
            raise RuntimeError("Verbindung tot")
        self.sent.append(data)


@pytest.mark.asyncio
async def test_publish_only_reaches_subscribed_connections():
    hub = WsHub()
    a = _FakeSocket()
    b = _FakeSocket()
    conn_a = hub.connect(a, user_id="u1")
    conn_b = hub.connect(b, user_id="u2")
    hub.subscribe(conn_a, "jobs.j1")

    await hub.publish("jobs.j1", {"status": "running"})

    assert a.sent == [{"type": "event", "channel": "jobs.j1", "payload": {"status": "running"}}]
    assert b.sent == []


@pytest.mark.asyncio
async def test_unsubscribe_stops_further_delivery():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1")
    hub.subscribe(conn, "actions")
    await hub.publish("actions", {"n": 1})
    hub.unsubscribe(conn, "actions")
    await hub.publish("actions", {"n": 2})

    assert len(sock.sent) == 1
    assert sock.sent[0]["payload"] == {"n": 1}


@pytest.mark.asyncio
async def test_disconnect_removes_connection_entirely():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1")
    hub.subscribe(conn, "hosts")
    hub.disconnect(conn)

    await hub.publish("hosts", {"x": 1})
    assert sock.sent == []
    assert hub.subscriber_count("hosts") == 0


@pytest.mark.asyncio
async def test_a_failing_socket_does_not_block_delivery_to_others():
    hub = WsHub()
    broken = _FakeSocket(fail=True)
    healthy = _FakeSocket()
    hub.subscribe(hub.connect(broken, user_id="u1"), "notifications")
    hub.subscribe(hub.connect(healthy, user_id="u2"), "notifications")

    await hub.publish("notifications", {"title": "x"})

    assert healthy.sent  # kam trotz des kaputten Sockets an


@pytest.mark.asyncio
async def test_subscriber_count():
    hub = WsHub()
    hub.subscribe(hub.connect(_FakeSocket(), user_id="u1"), "events")
    hub.subscribe(hub.connect(_FakeSocket(), user_id="u2"), "events")
    assert hub.subscriber_count("events") == 2
    assert hub.subscriber_count("other") == 0


@pytest.mark.asyncio
async def test_required_permission_filters_delivery_not_subscription():
    """docs/04-API.md §4: der `events`-Kanal ist "gefiltert nach RBAC" -- gefiltert
    wird die ZUSTELLUNG, das Abonnement selbst schlaegt nie fehl (der Client weiss ja
    vorher nicht, welche Berechtigung ein kuenftiges Event brauchen wird)."""
    hub = WsHub()
    viewer = _FakeSocket()
    operator = _FakeSocket()
    conn_viewer = hub.connect(viewer, user_id="u1", permissions=["hosts.read"])
    conn_operator = hub.connect(operator, user_id="u2", permissions=["hosts.read", "jobs.read"])
    hub.subscribe(conn_viewer, "jobs.j1")
    hub.subscribe(conn_operator, "jobs.j1")

    await hub.publish("jobs.j1", {"status": "running"}, required_permission="jobs.read")

    assert viewer.sent == []  # abonniert, aber ohne jobs.read nichts zugestellt bekommen
    assert len(operator.sent) == 1


@pytest.mark.asyncio
async def test_required_permission_none_delivers_to_every_subscriber():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1", permissions=[])
    hub.subscribe(conn, "ext.ntfy.custom")

    await hub.publish("ext.ntfy.custom", {"x": 1}, required_permission=None)

    assert len(sock.sent) == 1


@pytest.mark.asyncio
async def test_required_permission_owner_wildcard_always_matches():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="owner", permissions=["*"])
    hub.subscribe(conn, "notifications")

    await hub.publish("notifications", {"x": 1}, required_permission="notifications.read")

    assert len(sock.sent) == 1


@pytest.mark.asyncio
async def test_connect_without_permissions_defaults_to_empty_list():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1")  # kein permissions=-Argument
    hub.subscribe(conn, "jobs.j1")

    await hub.publish("jobs.j1", {"status": "running"}, required_permission="jobs.read")
    assert sock.sent == []
