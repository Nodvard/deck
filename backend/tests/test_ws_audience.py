"""Live-Nachrichten gehen nur an Konten, die den Inhalt auch über die API sehen dürften."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from nodvard_deck.core.events import get_event_bus, reset_event_bus
from nodvard_deck.core.ws_hub import get_ws_hub, reset_ws_hub
from nodvard_deck.ext.context import WsHandle
from nodvard_sdk import WidgetSpec
from nodvard_sdk.types import Event
from nodvard_sdk.widgets import Refresh, StatView


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


@pytest.fixture
def hub():
    reset_ws_hub()
    reset_event_bus()
    yield get_ws_hub()
    reset_ws_hub()
    reset_event_bus()


def _listen(hub, channel: str, permissions: list[str], user_id: str = "u") -> _Socket:
    socket = _Socket()
    hub.subscribe(hub.connect(socket, user_id=user_id, permissions=permissions), channel)
    return socket


def _widget(channel: str, permissions: list[str], widget_id: str = "w") -> WidgetSpec:
    return WidgetSpec(
        id=widget_id, title="W", data_endpoint="x", view=StatView(value="1"),
        refresh=Refresh(ws_channel=channel), permissions=permissions,
    )


def _handle(widgets: list[WidgetSpec], legacy: list[str] | None = None) -> WsHandle:
    return WsHandle("shield", lambda: legacy or [], lambda: widgets)


@pytest.mark.asyncio
async def test_module_channel_needs_the_widget_permission(hub):
    handle = _handle([_widget("ext.shield.defender", ["soc.read"])])
    without = _listen(hub, "ext.shield.defender", ["hosts.read", "notifications.read"])
    with_read = _listen(hub, "ext.shield.defender", ["soc.read"])

    await handle.broadcast("defender", {"scan": "s1"})

    assert without.sent == []
    assert [m["payload"] for m in with_read.sent] == [{"scan": "s1"}]


@pytest.mark.asyncio
async def test_module_channel_under_an_old_id_uses_the_same_rule(hub):
    handle = _handle([_widget("ext.shield.defender", ["soc.read"])], legacy=["alt-modul"])
    without = _listen(hub, "ext.alt-modul.defender", ["hosts.read"])
    with_read = _listen(hub, "ext.alt-modul.defender", ["soc.read"])

    await handle.broadcast("defender", {"scan": "s1"})

    assert without.sent == []
    assert len(with_read.sent) == 1


@pytest.mark.asyncio
async def test_widget_needs_all_of_its_permissions(hub):
    handle = _handle([_widget("ext.shield.defender", ["soc.read", "hosts.read"])])
    half = _listen(hub, "ext.shield.defender", ["soc.read"])
    both = _listen(hub, "ext.shield.defender", ["soc.read", "hosts.read"])

    await handle.broadcast("defender", {})

    assert half.sent == []
    assert len(both.sent) == 1


@pytest.mark.asyncio
async def test_one_matching_widget_is_enough(hub):
    handle = _handle([
        _widget("ext.shield.defender", ["soc.manage"], "a"),
        _widget("ext.shield.defender", ["soc.read"], "b"),
    ])
    reader = _listen(hub, "ext.shield.defender", ["soc.read"])

    await handle.broadcast("defender", {})

    assert len(reader.sent) == 1


@pytest.mark.asyncio
async def test_widget_on_another_channel_opens_nothing(hub):
    handle = _handle([_widget("ext.shield.incidents", [])])
    anyone = _listen(hub, "ext.shield.defender", ["hosts.read"])

    await handle.broadcast("defender", {})

    assert anyone.sent == []


@pytest.mark.asyncio
async def test_channel_without_widget_reaches_only_full_rights(hub):
    handle = _handle([])
    viewer = _listen(hub, "ext.shield.defender", ["hosts.read", "jobs.read", "audit.read", "notifications.read"])
    owner = _listen(hub, "ext.shield.defender", ["*"])

    await handle.broadcast("defender", {})

    assert viewer.sent == []
    assert len(owner.sent) == 1


@pytest.mark.asyncio
async def test_widget_without_permissions_reaches_every_account(hub):
    handle = _handle([_widget("ext.shield.defender", [])])
    anyone = _listen(hub, "ext.shield.defender", [])

    await handle.broadcast("defender", {})

    assert len(anyone.sent) == 1


@pytest.mark.asyncio
async def test_context_takes_widgets_only_from_its_own_module(tmp_path, hub):
    from nodvard_deck.ext.runtime import ExtensionRuntime
    from test_ext_context import _build

    runtime = ExtensionRuntime()
    runtime.discovered["shield"] = SimpleNamespace(manifest=SimpleNamespace(legacy_ids=[]))
    runtime.ui.register_widget("other", _widget("ext.shield.defender", []))
    _, _, ctx = _build(tmp_path, ext_id="shield", runtime=runtime)
    anyone = _listen(hub, "ext.shield.defender", ["hosts.read"])

    await ctx.ws.broadcast("defender", {})
    assert anyone.sent == []

    runtime.ui.register_widget("shield", _widget("ext.shield.defender", ["soc.read"]))
    reader = _listen(hub, "ext.shield.defender", ["soc.read"])
    await ctx.ws.broadcast("defender", {})
    assert anyone.sent == []
    assert len(reader.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["hello.tick", "system.started", "inventory.item_deleted", "ext.ntfy.custom", "ohne-punkt"])
async def test_unassigned_events_are_not_sent(hub, name):
    viewer = _listen(hub, "events", ["hosts.read"])
    owner = _listen(hub, "events", ["*"])

    await get_event_bus().publish(Event(name=name, payload={}))

    assert viewer.sent == []
    assert owner.sent == []


@pytest.mark.asyncio
async def test_extension_events_still_reach_every_account(hub):
    anyone = _listen(hub, "events", [])

    await get_event_bus().publish(Event(name="extension.enabled", payload={"ext_id": "shield"}))

    assert [m["payload"]["name"] for m in anyone.sent] == ["extension.enabled"]


@pytest.mark.asyncio
async def test_mapped_events_keep_their_permission(hub):
    without = _listen(hub, "events", [])
    reader = _listen(hub, "events", ["hosts.read"])

    await get_event_bus().publish(Event(name="host.status_changed", payload={"host_id": "h1"}))

    assert without.sent == []
    assert len(reader.sent) == 1


async def _notify(db_session, kind: str | None, account_id: str | None = None) -> None:
    from nodvard_deck.services import notifications as notifications_service

    payload: dict = {}
    if kind is not None:
        payload["notification_type"] = kind
    if account_id is not None:
        payload["account_id"] = account_id
    await notifications_service.send(db_session, title="Zugang von „fremdkonto“ geändert", body="B", payload=payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["account.changed", "account.password_attempts", "account.recovery_used"])
async def test_foreign_account_notice_does_not_reach_a_viewer(hub, db_session, kind):
    viewer = _listen(hub, "notifications", ["notifications.read"], user_id="gast")
    own = _listen(hub, "notifications", ["notifications.read"], user_id="fremd")

    await _notify(db_session, kind, account_id="fremd")

    assert viewer.sent == []
    assert len(own.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("permissions", [["notifications.read", "users.read"], ["notifications.read", "actions.approve:low"], ["*"]])
async def test_foreign_account_notice_reaches_readers_of_other_accounts(hub, db_session, permissions):
    reader = _listen(hub, "notifications", permissions, user_id="admin")

    await _notify(db_session, "account.changed", account_id="fremd")

    assert len(reader.sent) == 1


@pytest.mark.asyncio
async def test_old_account_notice_without_account_counts_as_foreign(hub, db_session):
    viewer = _listen(hub, "notifications", ["notifications.read"], user_id="gast")

    await _notify(db_session, "account.changed")

    assert viewer.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [None, "backup"])
async def test_other_notices_still_reach_every_reader(hub, db_session, kind):
    viewer = _listen(hub, "notifications", ["notifications.read"], user_id="gast")

    await _notify(db_session, kind)

    assert len(viewer.sent) == 1


async def _login(client, username: str, password: str) -> str:
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_own_account_notice_hides_the_admin_name_from_a_viewer(hub, client):
    password, new_password = "correct-horse-battery", "new-correct-horse-battery"
    await client.post(
        "/api/v1/auth/bootstrap", json={"username": "inhaberin", "password": password, "setup_code": "TEST-CODE-2345"}
    )
    owner = {"Authorization": f"Bearer {await _login(client, 'inhaberin', password)}"}
    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=owner)).json()}
    created = await client.post(
        "/api/v1/users",
        json={"username": "gast", "password": password, "role_ids": [roles["viewer"]], "current_password": password},
        headers=owner,
    )
    gast_id = created.json()["id"]
    viewer = _listen(hub, "notifications", ["notifications.read"], user_id=gast_id)
    approver = _listen(hub, "notifications", ["notifications.read", "actions.approve:low"], user_id="pruefer")

    r = await client.patch(
        f"/api/v1/users/{gast_id}", json={"password": new_password, "current_password": password}, headers=owner
    )
    assert r.status_code == 200, r.text

    [seen] = [m["payload"] for m in viewer.sent]
    assert "inhaberin" not in seen["title"] + seen["body"]
    assert seen["body"].startswith("Ein Konto mit Verwaltungsrechten hat für „gast“ ein neues Passwort gesetzt.")
    [full] = [m["payload"] for m in approver.sent]
    assert "„inhaberin“" in full["body"]
    assert {k: v for k, v in full.items() if k != "body"} == {k: v for k, v in seen.items() if k != "body"}
    gast = {"Authorization": f"Bearer {await _login(client, 'gast', new_password)}"}
    rest = (await client.get("/api/v1/notifications", headers=gast)).json()
    assert [n["body"] for n in rest if n["id"] == seen["id"]] == [seen["body"]]


@pytest.mark.asyncio
async def test_notice_by_the_reader_itself_keeps_its_text(hub, db_session):
    from nodvard_deck.services import notifications as notifications_service

    actor = _listen(hub, "notifications", ["notifications.read"], user_id="selbst")
    other = _listen(hub, "notifications", ["notifications.read"], user_id="gast")

    await notifications_service.send(
        db_session, title="T", body="„selbst“ hat etwas geändert.",
        payload={"actor_id": "selbst", "neutral_body": "Jemand hat etwas geändert."},
    )

    assert [m["payload"]["body"] for m in actor.sent] == ["„selbst“ hat etwas geändert."]
    assert [m["payload"]["body"] for m in other.sent] == ["Jemand hat etwas geändert."]


@pytest.mark.asyncio
async def test_foreign_account_notice_with_actor_reaches_no_viewer(hub, db_session):
    from nodvard_deck.services import notifications as notifications_service

    viewer = _listen(hub, "notifications", ["notifications.read"], user_id="gast")
    own = _listen(hub, "notifications", ["notifications.read"], user_id="fremd")

    await notifications_service.send(
        db_session, title="T", body="„admin“ hat etwas geändert.",
        payload={"notification_type": "account.changed", "account_id": "fremd", "actor_id": "admin"},
    )

    assert viewer.sent == []
    assert [m["payload"]["body"] for m in own.sent] == [notifications_service.NEUTRAL_ACTOR_BODY]
