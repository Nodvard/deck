"""„Vorgeschlagen von“ nennt den Namen einer umbenannten Erweiterung, egal unter welcher Kennung der Vorschlag steht.

Gespeicherte Vorschlaege tragen die Kennung, die zur Zeit des Vorschlags galt (`nexus-soc`), neue die heutige
(`shield`). Die Registry-Zeile mit dem Namen heisst auf einer bestehenden Installation `nexus-soc` (Speicher-Kennung),
auf einer neuen `shield`. In beiden Faellen muss jede der beiden Kennungen den Namen aus dem Manifest finden --
in EINER Abfrage fuer alle Akteure einer Liste (`services.extensions.get_records`).
"""

from __future__ import annotations

import pytest
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.models import Action, ExtensionRecord
from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services.actor_labels import load_actor_labels
from sqlalchemy import event


@pytest.fixture(autouse=True)
def _clean_runtime():
    reset_extension_runtime()
    yield
    reset_extension_runtime()


def _record(ext_id: str, name: str) -> ExtensionRecord:
    return ExtensionRecord(
        id=ext_id, version="0.1.0", api_version="0.1", state="enabled", manifest={"id": "shield", "name": name},
    )


def _proposal(proposed_by_id: str, *, proposed_by_type: str = "extension") -> Action:
    return Action(
        ext_id="shield", action_type="nexus_soc.upgrade", risk="medium", status="proposed", payload={},
        proposed_by_type=proposed_by_type, proposed_by_id=proposed_by_id, reason="Test", gate_decision={},
    )


def _renamed(store_id: str) -> None:
    """Der Stand nach dem Entdecken: `shield` hiess frueher `nexus-soc`; seine Zeile liegt unter `store_id`."""
    runtime = get_extension_runtime()
    runtime.legacy_owner = {"nexus-soc": "shield"}
    runtime.store_ids = {"shield": store_id}


async def _names(session, *ids: str) -> list[str]:
    actions = [_proposal(i) for i in ids]
    labels = await load_actor_labels(session, actions, viewer=None)
    return [labels.proposed_by(a) for a in actions]


@pytest.mark.asyncio
@pytest.mark.parametrize("row_id", ["nexus-soc", "shield"], ids=["bestand-zeile-nexus-soc", "neuinstallation-zeile-shield"])
async def test_the_current_and_the_old_id_both_find_the_name(db_session, row_id):
    db_session.add(_record(row_id, "Nodvard Shield"))
    await db_session.flush()
    _renamed(row_id)

    assert await _names(db_session, "nexus-soc", "shield") == ["Nodvard Shield", "Nodvard Shield"]


@pytest.mark.asyncio
async def test_an_unknown_actor_still_falls_back_to_the_raw_value(db_session):
    db_session.add(_record("nexus-soc", "Nodvard Shield"))
    await db_session.flush()
    _renamed("nexus-soc")

    names = await _names(db_session, "entfernt", "nexus-soc")
    assert names == ["extension/entfernt", "Nodvard Shield"]
    # Ein Akteur, der keine Erweiterung ist, wird nicht ueber die Registry aufgeloest.
    actions = [_proposal("nexus-soc", proposed_by_type="user")]
    labels = await load_actor_labels(db_session, actions, viewer=None)
    assert labels.proposed_by(actions[0]) == "user/nexus-soc"


@pytest.mark.asyncio
async def test_a_row_without_a_name_falls_back_to_the_raw_value(db_session):
    db_session.add(ExtensionRecord(id="nexus-soc", version="0.1.0", api_version="0.1", state="enabled", manifest={}))
    await db_session.flush()
    _renamed("nexus-soc")

    assert await _names(db_session, "shield", "nexus-soc") == ["extension/shield", "extension/nexus-soc"]


@pytest.mark.asyncio
async def test_only_the_row_the_extension_really_uses_gives_the_name(db_session):
    """Liegen eine Zeile unter der alten und eine unter der neuen Kennung (verwaister Zwilling nach einem Rueckweg),
    gilt fuer beide Kennungen die Zeile, die die Erweiterung nutzt."""
    db_session.add(_record("nexus-soc", "Nodvard Shield"))
    db_session.add(_record("shield", "Zwilling"))
    await db_session.flush()
    _renamed("nexus-soc")

    assert await _names(db_session, "nexus-soc", "shield") == ["Nodvard Shield", "Nodvard Shield"]


@pytest.mark.asyncio
async def test_without_a_rename_only_the_row_with_exactly_that_id_counts(db_session):
    db_session.add(_record("proxmox", "Proxmox VE"))
    await db_session.flush()

    assert await _names(db_session, "proxmox", "shield") == ["Proxmox VE", "extension/shield"]


@pytest.mark.asyncio
async def test_all_extension_actors_cost_one_query(db_session):
    db_session.add(_record("nexus-soc", "Nodvard Shield"))
    db_session.add(ExtensionRecord(
        id="proxmox", version="0.1.0", api_version="0.1", state="enabled", manifest={"name": "Proxmox VE"},
    ))
    await db_session.flush()
    _renamed("nexus-soc")
    statements: list[str] = []

    def _record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", _record_statement)
    try:
        names = await _names(db_session, "nexus-soc", "shield", "proxmox", "entfernt")
    finally:
        event.remove(engine, "before_cursor_execute", _record_statement)

    assert names == ["Nodvard Shield", "Nodvard Shield", "Proxmox VE", "extension/entfernt"]
    assert len([s for s in statements if "FROM extensions" in s]) == 1, statements


@pytest.mark.asyncio
async def test_get_records_maps_every_given_id_to_its_row(db_session):
    db_session.add(_record("nexus-soc", "Nodvard Shield"))
    await db_session.flush()
    _renamed("nexus-soc")

    found = await extensions_service.get_records(db_session, ["shield", "nexus-soc", "gibt-es-nicht"])

    assert sorted(found) == ["nexus-soc", "shield"]
    assert found["shield"] is found["nexus-soc"] and found["shield"].id == "nexus-soc"
    assert await extensions_service.get_records(db_session, []) == {}
