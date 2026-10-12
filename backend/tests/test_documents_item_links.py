"""Dokumente mit Gegenständen im Inventar verknüpfen (Rechnung, Handbuch): verknüpfen, lösen, filtern, gleich beim
Hochladen verknüpfen -- und das Aufräumen, wenn das Inventar einen Gegenstand löscht."""

from __future__ import annotations

import pytest
from test_ext_documents import (  # noqa: F401
    _auth_header,
    _cleanup_sys_path,
    _create_documents_tables,
    _enable_documents,
    _png_bytes,
)
from test_ext_inventory import _create_inventory_tables  # noqa: F401

API = "/api/v1/ext/documents"


async def _upload(client, headers, name: str, item_id: str | None = None) -> dict:
    query = f"filename={name}" + (f"&item_id={item_id}" if item_id else "")
    res = await client.post(f"{API}/documents?{query}", content=_png_bytes("X"), headers={**headers, "Content-Type": "image/png"})
    assert res.status_code == 201, res.text
    return res.json()


@pytest.mark.asyncio
async def test_link_unlink_and_filter_by_item(client, db_session, test_settings):
    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    invoice = await _upload(client, headers, "rechnung.png")
    manual = await _upload(client, headers, "handbuch.png", item_id="item-nas")
    assert invoice["item_ids"] == [] and manual["item_ids"] == ["item-nas"], "gleich beim Hochladen verknüpft"

    assert (await client.post(f"{API}/documents/{invoice['id']}/items/item-nas", headers=headers)).status_code == 204
    # Doppelt verknüpfen schadet nicht.
    assert (await client.post(f"{API}/documents/{invoice['id']}/items/item-nas", headers=headers)).status_code == 204
    assert (await client.post(f"{API}/documents/{invoice['id']}/items/item-switch", headers=headers)).status_code == 204
    doc = (await client.get(f"{API}/documents/{invoice['id']}", headers=headers)).json()
    assert doc["item_ids"] == ["item-nas", "item-switch"]

    for_nas = (await client.get(f"{API}/documents?item_id=item-nas", headers=headers)).json()
    assert sorted(d["original_filename"] for d in for_nas) == ["handbuch.png", "rechnung.png"]
    for_switch = (await client.get(f"{API}/documents?item_id=item-switch", headers=headers)).json()
    assert [d["original_filename"] for d in for_switch] == ["rechnung.png"]

    assert (await client.delete(f"{API}/documents/{invoice['id']}/items/item-nas", headers=headers)).status_code == 204
    assert (await client.get(f"{API}/documents/{invoice['id']}", headers=headers)).json()["item_ids"] == ["item-switch"]


@pytest.mark.asyncio
async def test_bad_item_ids_and_unknown_documents(client, db_session, test_settings):
    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    doc = await _upload(client, headers, "x.png")
    assert (await client.post(f"{API}/documents/{doc['id']}/items/{'a' * 65}", headers=headers)).status_code == 422
    assert (await client.post(f"{API}/documents/{doc['id']}/items/a%20b", headers=headers)).status_code == 422
    assert (await client.post(f"{API}/documents/gibt-es-nicht/items/item-1", headers=headers)).status_code == 404
    res = await client.post(
        f"{API}/documents?filename=y.png&item_id=a;b", content=_png_bytes("Y"), headers={**headers, "Content-Type": "image/png"},
    )
    assert res.status_code == 422


async def _link_rows(db_session) -> list[tuple[str, str]]:
    from nodvard_deck_ext_documents.models import DocumentItemLink
    from sqlalchemy import select

    rows = await db_session.execute(select(DocumentItemLink.document_id, DocumentItemLink.item_id))
    return [tuple(r) for r in rows.all()]


@pytest.mark.asyncio
async def test_deleting_a_document_removes_its_links(client, db_session, test_settings):
    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    doc = await _upload(client, headers, "x.png", item_id="item-1")
    assert await _link_rows(db_session) == [(doc["id"], "item-1")]
    assert (await client.delete(f"{API}/documents/{doc['id']}", headers=headers)).status_code == 204
    # Direkt in der Tabelle gezählt: die Abfrage über die Dokumente sähe eine verwaiste Zeile nicht.
    assert await _link_rows(db_session) == []


@pytest.mark.asyncio
async def test_the_cleanup_ignores_broken_events_and_subscribes_once(client, db_session, test_settings):
    from nodvard_deck.core.events import get_event_bus
    from nodvard_sdk import Event

    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    doc = await _upload(client, headers, "x.png", item_id="item-1")
    bus = get_event_bus()
    for payload in ({}, {"item_id": None}, {"item_id": "a;b"}, {"item_id": "x" * 65}):
        await bus.publish(Event(name="inventory.item_deleted", payload=payload))
    assert (await client.get(f"{API}/documents/{doc['id']}", headers=headers)).json()["item_ids"] == ["item-1"]

    def handlers() -> int:
        return sum(1 for pattern, _ in bus._subscribers if pattern == "inventory.item_deleted")

    # Modul aus und wieder an: ein Abo weg, ein Abo dazu -- kein doppeltes Aufräumen. (Relativ gezählt: der Bus ist
    # prozessweit, frühere Tests in diesem Lauf haben eigene Abos hinterlassen.)
    before = handlers()
    assert before >= 1
    assert (await client.post("/api/v1/extensions/documents/disable", headers=headers)).status_code == 200
    assert handlers() == before - 1
    assert (await client.post("/api/v1/extensions/documents/enable", headers=headers)).status_code == 200
    assert handlers() == before


@pytest.mark.asyncio
async def test_deleting_the_item_in_the_inventory_removes_the_link_but_keeps_the_document(client, db_session, test_settings):
    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    enabled = await client.post("/api/v1/extensions/inventory/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    item = await client.post("/api/v1/ext/inventory/items", json={"name": "NAS"}, headers=headers)
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]
    doc = await _upload(client, headers, "rechnung.png", item_id=item_id)

    assert (await client.delete(f"/api/v1/ext/inventory/items/{item_id}", headers=headers)).status_code == 204
    after = (await client.get(f"{API}/documents/{doc['id']}", headers=headers)).json()
    assert after["item_ids"] == [], "die Verknüpfung ist weg"
    assert after["original_filename"] == "rechnung.png", "das Dokument bleibt"


@pytest.mark.asyncio
async def test_linking_needs_write_permission(client, db_session, test_settings):
    from nodvard_deck.core import security
    from nodvard_deck.db.base import refresh_relationships
    from nodvard_deck.models import Role, RolePermission, User

    owner = _auth_header(await _enable_documents(client, db_session, test_settings))
    doc = await _upload(client, owner, "x.png")
    role = Role(name="nur-lesen", is_builtin=False, description="Testrolle")
    db_session.add(role)
    await db_session.flush()
    db_session.add(RolePermission(role_id=role.id, permission="documents.read"))
    await db_session.flush()
    await refresh_relationships(db_session, role, "permissions")
    reader = User(username="leser", password_hash=security.hash_password("correct-horse-battery"), is_active=True)
    reader.roles.append(role)
    db_session.add(reader)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": "leser", "password": "correct-horse-battery"})
    headers = _auth_header(login.json()["access_token"])

    assert (await client.get(f"{API}/documents?item_id=item-1", headers=headers)).status_code == 200
    assert (await client.post(f"{API}/documents/{doc['id']}/items/item-1", headers=headers)).status_code == 403
    assert (await client.delete(f"{API}/documents/{doc['id']}/items/item-1", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_links_to_items_deleted_while_documents_were_off_are_cleaned_up_on_start(client, db_session, test_settings):
    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    assert (await client.post("/api/v1/extensions/inventory/enable", headers=headers)).status_code == 200
    keep = (await client.post("/api/v1/ext/inventory/items", json={"name": "NAS"}, headers=headers)).json()
    gone = (await client.post("/api/v1/ext/inventory/items", json={"name": "Switch"}, headers=headers)).json()
    doc = await _upload(client, headers, "rechnung.png", item_id=keep["id"])
    assert (await client.post(f"{API}/documents/{doc['id']}/items/{gone['id']}", headers=headers)).status_code == 204

    # Dokumente aus, im Inventar löschen (die Meldung kommt nicht an), Dokumente wieder an: beim Start wird aufgeräumt.
    assert (await client.post("/api/v1/extensions/documents/disable", headers=headers)).status_code == 200
    assert (await client.delete(f"/api/v1/ext/inventory/items/{gone['id']}", headers=headers)).status_code == 204
    assert (await client.post("/api/v1/extensions/documents/enable", headers=headers)).status_code == 200
    assert (await client.get(f"{API}/documents/{doc['id']}", headers=headers)).json()["item_ids"] == [keep["id"]]


@pytest.mark.asyncio
async def test_the_check_also_runs_when_the_inventory_starts_later(client, db_session, test_settings):
    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    doc = await _upload(client, headers, "rechnung.png", item_id="item-gibt-es-nicht")
    # Das Inventar ist noch aus: niemand antwortet, die Zuordnung bleibt.
    assert (await client.get(f"{API}/documents/{doc['id']}", headers=headers)).json()["item_ids"] == ["item-gibt-es-nicht"]

    assert (await client.post("/api/v1/extensions/inventory/enable", headers=headers)).status_code == 200
    assert (await client.get(f"{API}/documents/{doc['id']}", headers=headers)).json()["item_ids"] == []


@pytest.mark.asyncio
async def test_the_inventory_answers_only_for_well_formed_missing_ids(client, db_session, test_settings):
    from nodvard_deck.core.events import get_event_bus
    from nodvard_sdk import Event

    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    assert (await client.post("/api/v1/extensions/inventory/enable", headers=headers)).status_code == 200
    there = (await client.post("/api/v1/ext/inventory/items", json={"name": "NAS"}, headers=headers)).json()
    bus = get_event_bus()
    deleted: list[str] = []

    async def _collect(event: Event) -> None:
        deleted.append(event.payload["item_id"])

    bus.subscribe("inventory.item_deleted", _collect)
    try:
        for payload in ({}, {"item_ids": "item-x"}, {"item_ids": [there["id"], "item-weg", "a;b", 7, "x" * 65]}):
            await bus.publish(Event(name="inventory.items_check_requested", payload=payload))
    finally:
        bus.unsubscribe("inventory.item_deleted", _collect)
    # Frühere Tests im selben Lauf haben eigene Inventar-Abos hinterlassen (der Bus ist prozessweit): die antworten
    # mit, aber nur für dieselbe Kennung.
    assert set(deleted) == {"item-weg"}


@pytest.mark.asyncio
async def test_a_failing_check_never_stops_either_module_from_starting(client, db_session, test_settings, monkeypatch, caplog):
    """Die Nachfrage ist nur Aufräumen: scheitert der Bus, starten Inventar und Dokumente trotzdem."""
    from nodvard_deck.ext.context import EventsHandle

    headers = _auth_header(await _enable_documents(client, db_session, test_settings))
    await _upload(client, headers, "rechnung.png", item_id="item-1")  # sonst gäbe es nichts nachzufragen
    original = EventsHandle.publish

    async def _broken(self, event):
        if event.name in ("inventory.started", "inventory.items_check_requested"):
            raise RuntimeError("Bus kaputt")
        await original(self, event)

    monkeypatch.setattr(EventsHandle, "publish", _broken)
    assert (await client.post("/api/v1/extensions/inventory/enable", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/extensions/documents/disable", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/extensions/documents/enable", headers=headers)).status_code == 200
    states = {e["id"]: e["state"] for e in (await client.get("/api/v1/extensions", headers=headers)).json()}
    assert states["inventory"] == "enabled" and states["documents"] == "enabled", states
    assert "Inventar gestartet" in caplog.text and "Nachfrage beim Inventar" in caplog.text
