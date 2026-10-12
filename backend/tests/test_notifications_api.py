"""GET /notifications, POST /notifications/read -- docs/04-API.md."""

from __future__ import annotations

import pytest

from nodvard_deck.services import notifications as notifications_service


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    assert (await client.get("/api/v1/notifications")).status_code == 401


@pytest.mark.asyncio
async def test_list_get_and_mark_read(client, db_session):
    token = await _bootstrap_owner(client)
    a = await notifications_service.send(db_session, title="A", body="x")
    b = await notifications_service.send(db_session, title="B", body="y", severity="critical")

    listed = await client.get("/api/v1/notifications", headers=_auth_header(token))
    assert listed.status_code == 200
    assert {n["id"] for n in listed.json()} == {a.id, b.id}

    unread_only = await client.get("/api/v1/notifications", params={"unread": True}, headers=_auth_header(token))
    assert {n["id"] for n in unread_only.json()} == {a.id, b.id}

    got = await client.get(f"/api/v1/notifications/{a.id}", headers=_auth_header(token))
    assert got.status_code == 200
    assert got.json()["severity"] == "info"

    missing = await client.get("/api/v1/notifications/does-not-exist", headers=_auth_header(token))
    assert missing.status_code == 404

    marked = await client.post("/api/v1/notifications/read", json={"ids": [a.id]}, headers=_auth_header(token))
    assert marked.status_code == 200
    assert marked.json() == {"marked": 1}

    still_unread = await client.get("/api/v1/notifications", params={"unread": True}, headers=_auth_header(token))
    assert {n["id"] for n in still_unread.json()} == {b.id}


@pytest.mark.asyncio
async def test_viewer_can_read_notifications(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.flush()

    login = await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
    token = login.json()["access_token"]

    r = await client.get("/api/v1/notifications", headers=_auth_header(token))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_unread_count_and_mark_all_read(client, db_session):
    """Benachrichtigungs-Center: Zaehler fuer das Menue und "Alle gelesen"."""
    token = await _bootstrap_owner(client)
    for i in range(3):
        await notifications_service.send(db_session, title=f"N{i}", body="x")

    count = await client.get("/api/v1/notifications/unread-count", headers=_auth_header(token))
    assert count.status_code == 200, count.text
    assert count.json() == {"unread": 3}

    marked = await client.post("/api/v1/notifications/read-all", headers=_auth_header(token))
    assert marked.json() == {"marked": 3}
    assert (await client.get("/api/v1/notifications/unread-count", headers=_auth_header(token))).json() == {"unread": 0}
    assert (await client.post("/api/v1/notifications/read-all", headers=_auth_header(token))).json() == {"marked": 0}
    assert (await client.get("/api/v1/notifications/unread-count")).status_code == 401


async def _role_header(client, db_session, role: str) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=f"u-{role}", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": user.username, "password": "whatever123"})
    assert login.status_code == 200, login.text
    return _auth_header(login.json()["access_token"])


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_mark_as_read(client, db_session):
    """Der Lesestatus gilt fuer alle: wer nur lesen darf, blendet keine Meldung fuer andere aus."""
    owner = await _bootstrap_owner(client)
    note = await notifications_service.send(db_session, title="Neue Login-IP", body="x", severity="critical")
    viewer = await _role_header(client, db_session, "viewer")

    assert (await client.get("/api/v1/notifications", headers=viewer)).status_code == 200
    assert (await client.get("/api/v1/notifications/unread-count", headers=viewer)).json() == {"unread": 1}

    all_ = await client.post("/api/v1/notifications/read-all", headers=viewer)
    assert all_.status_code == 403
    assert "notifications.write" in all_.json()["detail"]
    one = await client.post("/api/v1/notifications/read", json={"ids": [note.id]}, headers=viewer)
    assert one.status_code == 403

    # Beim Owner bleibt die Meldung ungelesen.
    assert (await client.get("/api/v1/notifications/unread-count", headers=_auth_header(owner))).json() == {"unread": 1}


@pytest.mark.asyncio
async def test_operator_can_mark_as_read(client, db_session):
    note = await notifications_service.send(db_session, title="A", body="x")
    operator = await _role_header(client, db_session, "operator")

    assert (await client.post("/api/v1/notifications/read", json={"ids": [note.id]}, headers=operator)).json() == {"marked": 1}
    assert (await client.post("/api/v1/notifications/read-all", headers=operator)).json() == {"marked": 0}


async def _deleted_entries(client, token: str) -> list[dict]:
    return (await client.get("/api/v1/audit?action=notifications.deleted", headers=_auth_header(token))).json()


@pytest.mark.asyncio
async def test_delete_one_notification_returns_204_then_404_and_removes_its_deliveries(client, db_session):
    from nodvard_sdk.capabilities import NotificationChannel
    from sqlalchemy import select

    from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
    from nodvard_deck.models import NotificationDelivery

    class _Channel:
        channel_id = "kanal"
        label = "Kanal"

        async def send(self, notification) -> None:
            return None

        async def test(self):
            raise NotImplementedError

    reset_extension_runtime()
    get_extension_runtime().capabilities.provide("chan-ext", NotificationChannel, _Channel())
    token = await _bootstrap_owner(client)
    keep = await notifications_service.send(db_session, title="Bleibt", body="x")
    gone = await notifications_service.send(db_session, title="Lagebericht vom Montag", body="y")
    assert len((await db_session.execute(select(NotificationDelivery))).scalars().all()) == 2

    first = await client.delete(f"/api/v1/notifications/{gone.id}", headers=_auth_header(token))
    assert first.status_code == 204, first.text
    assert first.content == b""
    again = await client.delete(f"/api/v1/notifications/{gone.id}", headers=_auth_header(token))
    assert again.status_code == 404
    assert (await client.delete("/api/v1/notifications/gibt-es-nicht", headers=_auth_header(token))).status_code == 404
    assert (await client.get(f"/api/v1/notifications/{gone.id}", headers=_auth_header(token))).status_code == 404

    listed = await client.get("/api/v1/notifications", headers=_auth_header(token))
    assert [n["id"] for n in listed.json()] == [keep.id]
    assert (await client.get("/api/v1/notifications/unread-count", headers=_auth_header(token))).json() == {"unread": 1}
    left = (await db_session.execute(select(NotificationDelivery.notification_id))).scalars().all()
    assert left == [keep.id]
    reset_extension_runtime()


@pytest.mark.asyncio
async def test_deleting_one_notification_is_written_to_the_audit_log_with_id_and_title(client, db_session):
    token = await _bootstrap_owner(client)
    note = await notifications_service.send(db_session, title="Neue Login-IP auf pve1", body="x", severity="critical")

    assert (await client.delete(f"/api/v1/notifications/{note.id}", headers=_auth_header(token))).status_code == 204
    assert (await client.delete(f"/api/v1/notifications/{note.id}", headers=_auth_header(token))).status_code == 404

    entries = await _deleted_entries(client, token)
    assert len(entries) == 1  # der Fehlversuch (404) hinterlaesst nichts
    entry = entries[0]
    assert entry["target_type"] == "notification" and entry["target_id"] == note.id
    assert entry["outcome"] == "success" and entry["actor_type"] == "user"
    assert entry["detail"] == {"title": "Neue Login-IP auf pve1"}


@pytest.mark.asyncio
async def test_delete_read_removes_only_read_notifications_and_counts_them(client, db_session):
    token = await _bootstrap_owner(client)
    a = await notifications_service.send(db_session, title="A", body="x")
    b = await notifications_service.send(db_session, title="B", body="y")
    c = await notifications_service.send(db_session, title="C", body="z")
    d = await notifications_service.send(db_session, title="D", body="w")
    await client.post("/api/v1/notifications/read", json={"ids": [a.id, b.id]}, headers=_auth_header(token))

    response = await client.post("/api/v1/notifications/delete-read", headers=_auth_header(token))
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 2}

    listed = await client.get("/api/v1/notifications", headers=_auth_header(token))
    assert {n["id"] for n in listed.json()} == {c.id, d.id}  # ungelesene bleiben
    assert (await client.get("/api/v1/notifications/unread-count", headers=_auth_header(token))).json() == {"unread": 2}
    assert (await client.post("/api/v1/notifications/delete-read", headers=_auth_header(token))).json() == {"deleted": 0}


@pytest.mark.asyncio
async def test_delete_read_is_written_to_the_audit_log_with_the_count_but_not_when_nothing_went(client, db_session):
    token = await _bootstrap_owner(client)
    for i in range(3):
        await notifications_service.send(db_session, title=f"N{i}", body="x")
    await client.post("/api/v1/notifications/read-all", headers=_auth_header(token))

    assert (await client.post("/api/v1/notifications/delete-read", headers=_auth_header(token))).json() == {"deleted": 3}
    assert (await client.post("/api/v1/notifications/delete-read", headers=_auth_header(token))).json() == {"deleted": 0}

    entries = await _deleted_entries(client, token)
    assert len(entries) == 1
    assert entries[0]["target_type"] == "notification" and entries[0]["target_id"] is None
    assert entries[0]["detail"] == {"count": 3}


def _fail_on_chunk(monkeypatch, number: int, *, chunk_size: int = 2) -> None:
    """`delete_read` in kleinen Haeppchen; das `number`-te scheitert (der Rest geht normal)."""
    import functools

    real_delete = notifications_service.delete_notifications
    calls = 0

    async def flaky(session, ids):
        nonlocal calls
        calls += 1
        if calls == number:
            raise RuntimeError("Festplatte voll")
        return await real_delete(session, ids)

    monkeypatch.setattr(notifications_service, "delete_notifications", flaky)
    monkeypatch.setattr(
        notifications_service, "delete_read", functools.partial(notifications_service.delete_read, chunk_size=chunk_size, pause=0)
    )


@pytest.mark.asyncio
async def test_a_later_chunk_failing_still_writes_what_was_deleted_to_the_audit_log(client, db_session, monkeypatch):
    token = await _bootstrap_owner(client)
    for i in range(5):
        await notifications_service.send(db_session, title=f"N{i}", body="x")
    await client.post("/api/v1/notifications/read-all", headers=_auth_header(token))
    await db_session.commit()  # wie im Betrieb: das Lesen ist schon festgeschrieben, bevor es ans Loeschen geht
    with monkeypatch.context() as faulty:
        _fail_on_chunk(faulty, 2)
        failed = await client.post("/api/v1/notifications/delete-read", headers=_auth_header(token))
    assert failed.status_code == 500, "der Fehler geht weiter"

    assert len((await client.get("/api/v1/notifications", headers=_auth_header(token))).json()) == 3, "ein Haeppchen war weg"
    entries = await _deleted_entries(client, token)
    assert len(entries) == 1, "die schon geloeschten stehen im Protokoll, auch wenn ein spaeteres Haeppchen scheiterte"
    assert entries[0]["target_type"] == "notification" and entries[0]["target_id"] is None
    assert entries[0]["outcome"] == "failure" and entries[0]["actor_type"] == "user"
    assert entries[0]["detail"] == {"count": 2, "interrupted": True}

    # Ein neuer Versuch (jetzt ohne Fehler) loescht den Rest und schreibt den ueblichen Eintrag.
    assert (await client.post("/api/v1/notifications/delete-read", headers=_auth_header(token))).json() == {"deleted": 3}
    entries = await _deleted_entries(client, token)
    assert sorted((e["outcome"], e["detail"].get("count"), e["detail"].get("interrupted")) for e in entries) == [
        ("failure", 2, True), ("success", 3, None),
    ]


@pytest.mark.asyncio
async def test_the_first_chunk_failing_deletes_nothing_and_writes_nothing(client, db_session, monkeypatch):
    token = await _bootstrap_owner(client)
    for i in range(3):
        await notifications_service.send(db_session, title=f"N{i}", body="x")
    await client.post("/api/v1/notifications/read-all", headers=_auth_header(token))
    await db_session.commit()
    with monkeypatch.context() as faulty:
        _fail_on_chunk(faulty, 1)
        assert (await client.post("/api/v1/notifications/delete-read", headers=_auth_header(token))).status_code == 500

    assert len((await client.get("/api/v1/notifications", headers=_auth_header(token))).json()) == 3
    assert await _deleted_entries(client, token) == []


@pytest.mark.asyncio
async def test_viewer_cannot_delete_notifications_but_operator_can(client, db_session):
    owner = await _bootstrap_owner(client)
    a = await notifications_service.send(db_session, title="A", body="x")
    b = await notifications_service.send(db_session, title="B", body="y")
    await client.post("/api/v1/notifications/read", json={"ids": [b.id]}, headers=_auth_header(owner))
    viewer = await _role_header(client, db_session, "viewer")

    one = await client.delete(f"/api/v1/notifications/{a.id}", headers=viewer)
    assert one.status_code == 403
    assert "notifications.write" in one.json()["detail"]
    many = await client.post("/api/v1/notifications/delete-read", headers=viewer)
    assert many.status_code == 403
    assert "notifications.write" in many.json()["detail"]
    # Ohne Anmeldung gar nicht.
    assert (await client.delete(f"/api/v1/notifications/{a.id}")).status_code == 401
    assert (await client.post("/api/v1/notifications/delete-read")).status_code == 401
    # Und es ist nichts verloren gegangen.
    assert {n["id"] for n in (await client.get("/api/v1/notifications", headers=_auth_header(owner))).json()} == {a.id, b.id}
    assert await _deleted_entries(client, owner) == []

    operator = await _role_header(client, db_session, "operator")
    assert (await client.delete(f"/api/v1/notifications/{a.id}", headers=operator)).status_code == 204
    assert (await client.post("/api/v1/notifications/delete-read", headers=operator)).json() == {"deleted": 1}
