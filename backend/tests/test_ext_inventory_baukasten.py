"""Inventar als Homelab-Baukasten: Hersteller/Modell/Seriennummer/Produktlink, Server-Bezug, Bauprojekte und Racks.

Wie `test_ext_inventory.py` gegen die In-Memory-DB; die Migration selbst (Spalten, Indizes, Rueckweg ohne
Datenverlust bei den Bildern) prueft `test_inventory_baukasten_migration.py`.
"""

from __future__ import annotations

import pytest

# Die autouse-Fixtures (Tabellen anlegen, sys.path aufraeumen) gelten nur, wenn sie hier importiert sind.
from test_ext_inventory import _auth_header, _cleanup_sys_path, _create_inventory_tables, _enable_inventory  # noqa: F401

pytestmark = pytest.mark.asyncio

API = "/api/v1/ext/inventory"


async def _item(client, headers, **fields):
    res = await client.post(f"{API}/items", json={"name": "Teil", **fields}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


async def test_new_fields_round_trip_and_search(client, db_session, test_settings):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    item = await _item(
        client, headers, name="Festplatte", manufacturer="WD", model="Red Plus 4TB", serial_number="WX12345",
        product_url="https://example.com/wd-red", host_id="h-nas",
    )
    assert (item["manufacturer"], item["model"], item["serial_number"], item["host_id"]) == ("WD", "Red Plus 4TB", "WX12345", "h-nas")

    # Die Suche findet ein Teil auch ueber Modell und Seriennummer, der Filter ueber den Server.
    for q in ("red plus", "WX123"):
        found = await client.get(f"{API}/items", params={"q": q}, headers=headers)
        assert [i["name"] for i in found.json()] == ["Festplatte"], q
    assert [i["id"] for i in (await client.get(f"{API}/items", params={"host_id": "h-nas"}, headers=headers)).json()] == [item["id"]]
    assert (await client.get(f"{API}/items", params={"host_id": "h-other"}, headers=headers)).json() == []


async def test_put_without_the_new_fields_keeps_them(client, db_session, test_settings):
    """Ein aelterer Aufrufer (kennt die neuen Felder nicht) darf sie beim Aendern nicht loeschen."""
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    item = await _item(client, headers, manufacturer="Intel", model="NUC 12", host_id="h-1")

    res = await client.put(f"{API}/items/{item['id']}", json={"name": "Mini-PC", "quantity": 2}, headers=headers)
    assert res.status_code == 200, res.text
    assert (res.json()["name"], res.json()["quantity"], res.json()["manufacturer"], res.json()["model"], res.json()["host_id"]) == (
        "Mini-PC", 2, "Intel", "NUC 12", "h-1",
    )
    # Mitgeschickt (auch als null) gilt es dagegen.
    res = await client.put(f"{API}/items/{item['id']}", json={"name": "Mini-PC", "host_id": None}, headers=headers)
    assert res.json()["host_id"] is None
    assert res.json()["manufacturer"] == "Intel"


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://example.com/x", "example.com"])
async def test_product_link_must_be_a_web_address(client, db_session, test_settings, url):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    res = await client.post(f"{API}/items", json={"name": "Teil", "product_url": url}, headers=headers)
    assert res.status_code == 422
    assert "http://" in res.text


async def test_projects_sum_up_their_parts_and_leave_them_when_deleted(client, db_session, test_settings):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    created = await client.post(f"{API}/projects", json={"name": "NAS bauen", "budget_cents": 60000}, headers=headers)
    assert created.status_code == 201, created.text
    project = created.json()
    assert (project["status"], project["item_count"], project["spent_cents"]) == ("planned", 0, 0)

    await _item(client, headers, name="Gehäuse", project_id=project["id"], purchase_price_cents=8999)
    disk = await _item(client, headers, name="Platte", project_id=project["id"], purchase_price_cents=11000, quantity=2)
    await _item(client, headers, name="Kabel", project_id=project["id"])  # ohne Preis
    await _item(client, headers, name="Anderes")

    (listed,) = (await client.get(f"{API}/projects", headers=headers)).json()
    assert (listed["item_count"], listed["spent_cents"]) == (3, 8999 + 2 * 11000)
    assert {i["name"] for i in (await client.get(f"{API}/items", params={"project_id": project["id"]}, headers=headers)).json()} == {
        "Gehäuse", "Platte", "Kabel",
    }

    updated = await client.put(f"{API}/projects/{project['id']}", json={"name": "NAS", "status": "building"}, headers=headers)
    assert (updated.json()["name"], updated.json()["status"], updated.json()["budget_cents"]) == ("NAS", "building", None)
    bad = await client.put(f"{API}/projects/{project['id']}", json={"name": "NAS", "status": "kaputt"}, headers=headers)
    assert bad.status_code == 422

    assert (await client.delete(f"{API}/projects/{project['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"{API}/projects", headers=headers)).json() == []
    still = (await client.get(f"{API}/items/{disk['id']}", headers=headers)).json()
    assert still["project_id"] is None


async def test_unknown_project_or_rack_is_rejected(client, db_session, test_settings):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    for field in ("project_id", "rack_id"):
        res = await client.post(f"{API}/items", json={"name": "Teil", field: "gibt-es-nicht"}, headers=headers)
        assert res.status_code == 404, field


async def test_rack_places_devices_without_overlap(client, db_session, test_settings):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    rack = (await client.post(f"{API}/racks", json={"name": "Keller", "height_units": 12}, headers=headers)).json()
    assert rack["height_units"] == 12

    switch = await _item(client, headers, name="Switch", rack_id=rack["id"], rack_position=1, rack_units=1)
    server = await _item(client, headers, name="Server", rack_id=rack["id"], rack_position=2, rack_units=2)

    clash = await client.post(
        f"{API}/items", json={"name": "NAS", "rack_id": rack["id"], "rack_position": 3, "rack_units": 2}, headers=headers,
    )
    assert clash.status_code == 409
    assert "„Server“" in clash.json()["detail"] and "HE 2–3" in clash.json()["detail"]

    too_high = await client.post(
        f"{API}/items", json={"name": "USV", "rack_id": rack["id"], "rack_position": 12, "rack_units": 2}, headers=headers,
    )
    assert too_high.status_code == 422
    assert "12 HE" in too_high.json()["detail"]

    # Das eigene Geraet stoert beim Verschieben nicht.
    moved = await client.put(
        f"{API}/items/{server['id']}", json={"name": "Server", "rack_id": rack["id"], "rack_position": 3, "rack_units": 2},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text

    # Verkleinern unter ein Geraet geht nicht, sonst schon.
    smaller = await client.put(f"{API}/racks/{rack['id']}", json={"name": "Keller", "height_units": 3}, headers=headers)
    assert smaller.status_code == 409
    assert (await client.put(f"{API}/racks/{rack['id']}", json={"name": "Keller", "height_units": 4}, headers=headers)).status_code == 200

    assert (await client.delete(f"{API}/racks/{rack['id']}", headers=headers)).status_code == 204
    after = (await client.get(f"{API}/items/{switch['id']}", headers=headers)).json()
    assert (after["rack_id"], after["rack_position"], after["rack_units"]) == (None, None, None)


async def test_new_routes_need_the_inventory_permissions(client, db_session, test_settings):
    await _enable_inventory(client, db_session, test_settings)
    for path in ("/projects", "/racks"):
        assert (await client.get(f"{API}{path}")).status_code == 401
        assert (await client.post(f"{API}{path}", json={"name": "x"})).status_code == 401


async def test_rack_edges_fit_exactly_and_neighbours_do_not_clash(client, db_session, test_settings):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    rack = (await client.post(f"{API}/racks", json={"name": "Klein", "height_units": 4}, headers=headers)).json()
    top = await client.post(f"{API}/items", json={"name": "Oben", "rack_id": rack["id"], "rack_position": 3, "rack_units": 2}, headers=headers)
    assert top.status_code == 201, "passt genau bis HE 4"
    below = await client.post(f"{API}/items", json={"name": "Darunter", "rack_id": rack["id"], "rack_position": 2}, headers=headers)
    assert below.status_code == 201, "Nachbar-HE ohne Überlappung; ohne Höhe zählt 1 HE"
    clash = await client.post(f"{API}/items", json={"name": "Doppelt", "rack_id": rack["id"], "rack_position": 2}, headers=headers)
    assert clash.status_code == 409


async def test_place_without_rack_is_cleared_and_blank_ids_are_none(client, db_session, test_settings):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    item = await _item(client, headers, rack_position=5, rack_units=2, project_id="", host_id="  ", manufacturer="")
    assert (item["rack_position"], item["rack_units"], item["project_id"], item["host_id"], item["manufacturer"]) == (None, None, None, None, None)

    rack = (await client.post(f"{API}/racks", json={"name": "R"}, headers=headers)).json()
    placed = await client.put(f"{API}/items/{item['id']}", json={"name": "Teil", "rack_id": rack["id"], "rack_position": 1}, headers=headers)
    assert placed.json()["rack_position"] == 1
    # Nur das Rack entfernt: der Platz darin gilt nicht weiter.
    out = await client.put(f"{API}/items/{item['id']}", json={"name": "Teil", "rack_id": None}, headers=headers)
    assert (out.json()["rack_id"], out.json()["rack_position"], out.json()["rack_units"]) == (None, None, None)


@pytest.mark.parametrize("url", [
    "http://", "https:///pfad", "https://x\nhttps://y", "https://a b.de",
    # Sieht nach dem Shop aus, führt aber zu boese.example; Zugangsdaten gehören nicht in einen sichtbaren Link.
    "https://shop.example@boese.example/", "https://nutzer:" + "geheim@example.com/", "https://@example.com/",
])
async def test_broken_product_links_are_rejected(client, db_session, test_settings, url):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    assert (await client.post(f"{API}/items", json={"name": "Teil", "product_url": url}, headers=headers)).status_code == 422


async def test_names_of_only_spaces_are_rejected_and_unpriced_parts_are_counted(client, db_session, test_settings):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    assert (await client.post(f"{API}/racks", json={"name": "   "}, headers=headers)).status_code == 422
    assert (await client.post(f"{API}/projects", json={"name": "  "}, headers=headers)).status_code == 422
    project = (await client.post(f"{API}/projects", json={"name": "  NAS  "}, headers=headers)).json()
    assert project["name"] == "NAS"
    await _item(client, headers, project_id=project["id"], purchase_price_cents=1000)
    await _item(client, headers, project_id=project["id"])
    (listed,) = (await client.get(f"{API}/projects", headers=headers)).json()
    assert (listed["item_count"], listed["spent_cents"], listed["unpriced_count"]) == (2, 1000, 1)


async def test_numbers_have_upper_bounds_so_the_project_sum_cannot_overflow(client, db_session, test_settings):
    """Zwei riesige Werte ließen die Summe je Projekt (Preis mal Menge) in SQLite überlaufen: GET /projects endete mit 500."""
    from nodvard_deck_ext_inventory import MAX_CENTS, MAX_QUANTITY

    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    project = (await client.post(f"{API}/projects", json={"name": "Server"}, headers=headers)).json()
    for fields in (
        {"quantity": MAX_QUANTITY + 1}, {"quantity": -1},
        {"purchase_price_cents": MAX_CENTS + 1}, {"purchase_price_cents": -5},
    ):
        res = await client.post(f"{API}/items", json={"name": "Teil", "project_id": project["id"], **fields}, headers=headers)
        assert res.status_code == 422, fields
    big = await client.post(f"{API}/projects", json={"name": "Groß", "budget_cents": MAX_CENTS + 1}, headers=headers)
    assert big.status_code == 422

    # Die Grenzwerte selbst gehen, auch mehrfach: die Summe bleibt im Zahlenbereich.
    for _ in range(3):
        await _item(client, headers, project_id=project["id"], quantity=MAX_QUANTITY, purchase_price_cents=MAX_CENTS)
    res = await client.get(f"{API}/projects", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()[0]["spent_cents"] == 3 * MAX_QUANTITY * MAX_CENTS


async def test_old_values_beyond_the_limits_do_not_break_the_project_list(client, db_session, test_settings):
    """Altdaten von vor den Obergrenzen: die Summe rechnet Python, SQLite liefe hier über."""
    from nodvard_deck_ext_inventory.models import Item

    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    project = (await client.post(f"{API}/projects", json={"name": "Alt"}, headers=headers)).json()
    for name in ("A", "B"):
        db_session.add(Item(name=name, quantity=1_000_000, purchase_price_cents=9_000_000_000_000, project_id=project["id"]))
    await db_session.commit()
    res = await client.get(f"{API}/projects", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()[0]["spent_cents"] == 2 * 1_000_000 * 9_000_000_000_000
    assert res.json()[0]["item_count"] == 2


async def test_limit_messages_are_plain_german(client, db_session, test_settings):
    headers = _auth_header(await _enable_inventory(client, db_session, test_settings))
    res = await client.post(f"{API}/items", json={"name": "Teil", "quantity": 100_001}, headers=headers)
    assert res.status_code == 422 and "Die Menge muss zwischen 0 und 100.000 liegen." in res.text
    res = await client.post(f"{API}/projects", json={"name": "P", "budget_cents": 10**12}, headers=headers)
    assert res.status_code == 422 and "höchstens 10 Millionen €" in res.text
