"""Der WS-Multiplex-Hub als reiner Verteiler (docs/04-API.md §4)
-- ohne echten Socket, siehe test_ws_api.py fuer
die Ende-zu-Ende-Variante gegen eine echte laufende App."""

from __future__ import annotations

import asyncio

import pytest

from nodvard_deck.core import ws_hub
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


class _HangingSocket:
    def __init__(self) -> None:
        self.closed = False

    async def send_json(self, data: dict) -> None:
        await asyncio.Event().wait()

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_a_hanging_socket_is_dropped_and_others_still_receive(monkeypatch):
    monkeypatch.setattr(ws_hub, "SEND_TIMEOUT_S", 0.05)
    hub = WsHub()
    hanging = _HangingSocket()
    healthy = _FakeSocket()
    conn_h = hub.connect(hanging, user_id="u1")
    conn_ok = hub.connect(healthy, user_id="u2")
    hub.subscribe(conn_h, "jobs.j1")
    hub.subscribe(conn_ok, "jobs.j1")

    await asyncio.wait_for(hub.publish("jobs.j1", {"n": 1}), timeout=2)

    assert healthy.sent == [{"type": "event", "channel": "jobs.j1", "payload": {"n": 1}}]
    assert hanging.closed
    assert hub.subscriber_count("jobs.j1") == 1


@pytest.mark.asyncio
async def test_healthy_socket_receives_while_another_one_hangs(monkeypatch):
    monkeypatch.setattr(ws_hub, "SEND_TIMEOUT_S", 3.0)
    hub = WsHub()
    hanging = _HangingSocket()
    healthy = _FakeSocket()
    hub.subscribe(hub.connect(hanging, user_id="u1"), "jobs.j1")
    hub.subscribe(hub.connect(healthy, user_id="u2"), "jobs.j1")

    publishing = asyncio.ensure_future(hub.publish("jobs.j1", {"n": 1}))
    try:
        async with asyncio.timeout(0.5):
            while not healthy.sent:
                await asyncio.sleep(0.01)
    finally:
        await publishing

    assert healthy.sent == [{"type": "event", "channel": "jobs.j1", "payload": {"n": 1}}]


@pytest.mark.asyncio
async def test_many_hanging_sockets_cost_one_timeout_not_one_each(monkeypatch):
    monkeypatch.setattr(ws_hub, "SEND_TIMEOUT_S", 0.2)
    hub = WsHub()
    sockets = [_HangingSocket() for _ in range(6)]
    for i, sock in enumerate(sockets):
        hub.subscribe(hub.connect(sock, user_id=f"u{i}"), "events")

    loop = asyncio.get_running_loop()
    started = loop.time()
    await hub.publish("events", {"n": 1})

    assert loop.time() - started < 0.6
    assert all(sock.closed for sock in sockets)
    assert hub.subscriber_count("events") == 0


def test_connections_per_user_are_capped():
    hub = WsHub()
    ids = [hub.connect(_FakeSocket(), user_id="u1") for _ in range(ws_hub.MAX_CONNECTIONS_PER_USER)]

    with pytest.raises(ws_hub.ConnectionLimitReached):
        hub.connect(_FakeSocket(), user_id="u1")
    hub.connect(_FakeSocket(), user_id="u2")

    hub.disconnect(ids[0])
    hub.connect(_FakeSocket(), user_id="u1")


@pytest.mark.asyncio
async def test_rejected_connection_is_not_registered():
    hub = WsHub()
    for _ in range(ws_hub.MAX_CONNECTIONS_PER_USER):
        hub.subscribe(hub.connect(_FakeSocket(), user_id="u1"), "events")
    extra = _FakeSocket()
    with pytest.raises(ws_hub.ConnectionLimitReached):
        hub.connect(extra, user_id="u1")

    await hub.publish("events", {"n": 1})

    assert extra.sent == []
    assert hub.subscriber_count("events") == ws_hub.MAX_CONNECTIONS_PER_USER


async def _publish_with_change_before_delivery(hub: WsHub, change, *args, **kwargs) -> None:
    """Startet `publish`, ändert den Hub, nachdem die Empfänger geplant sind, und wartet die Zustellung ab."""
    task = asyncio.create_task(hub.publish(*args, **kwargs))
    await asyncio.sleep(0)
    assert not task.done()
    change()
    await task


_ACTION = {"id": "a1", "output": "geheim"}
_ACTION_REDUCED = {"id": "a1"}


@pytest.mark.asyncio
async def test_permission_lost_before_delivery_sends_reduced_payload():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1", permissions=["actions.read", "hosts.execute"])
    hub.subscribe(conn, "actions")

    await _publish_with_change_before_delivery(
        hub,
        lambda: hub.set_permissions(conn, ["actions.read"]),
        "actions",
        _ACTION,
        required_permission="actions.read",
        reduced_payload=_ACTION_REDUCED,
        full_permission="hosts.execute",
    )

    assert [m["payload"] for m in sock.sent] == [_ACTION_REDUCED]


@pytest.mark.asyncio
async def test_required_permission_lost_before_delivery_sends_nothing():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1", permissions=["actions.read"])
    hub.subscribe(conn, "actions")

    await _publish_with_change_before_delivery(
        hub, lambda: hub.set_permissions(conn, []), "actions", _ACTION, required_permission="actions.read"
    )

    assert sock.sent == []


@pytest.mark.asyncio
async def test_audience_lost_before_delivery_sends_nothing():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1", permissions=["soc.read"])
    hub.subscribe(conn, "ext.shield")

    await _publish_with_change_before_delivery(
        hub,
        lambda: hub.set_permissions(conn, []),
        "ext.shield",
        {"n": 1},
        audience=lambda _user_id, permissions: "soc.read" in permissions,
    )

    assert sock.sent == []


@pytest.mark.asyncio
async def test_unsubscribe_before_delivery_sends_nothing():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1")
    hub.subscribe(conn, "hosts")

    await _publish_with_change_before_delivery(hub, lambda: hub.unsubscribe(conn, "hosts"), "hosts", {"n": 1})

    assert sock.sent == []


@pytest.mark.asyncio
async def test_disconnect_before_delivery_sends_nothing():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1")
    hub.subscribe(conn, "hosts")

    await _publish_with_change_before_delivery(hub, lambda: hub.disconnect(conn), "hosts", {"n": 1})

    assert sock.sent == []


@pytest.mark.asyncio
async def test_permission_gained_before_delivery_sends_full_payload():
    hub = WsHub()
    sock = _FakeSocket()
    conn = hub.connect(sock, user_id="u1", permissions=["actions.read"])
    hub.subscribe(conn, "actions")

    await _publish_with_change_before_delivery(
        hub,
        lambda: hub.set_permissions(conn, ["actions.read", "hosts.execute"]),
        "actions",
        _ACTION,
        required_permission="actions.read",
        reduced_payload=_ACTION_REDUCED,
        full_permission="hosts.execute",
    )

    assert [m["payload"] for m in sock.sent] == [_ACTION]
