"""Sammel-Vorschlag der Update-Zentrale und die alte Kennung `nexus-soc` (Alias-Tests, siehe `test_ext_shield_updates_propose_all`).

Offene Vorschlaege aus der Zeit vor der Umbenennung tragen die alte Kennung in der Datenbank; die alte Adresse der Route
antwortet wie die neue, mit demselben Recht und im Schema als veraltet markiert."""

from __future__ import annotations

from datetime import timedelta

import pytest
from test_ext_shield_updates_propose_all import (  # noqa: F401 -- shield_tables ist ein Fixture
    NOW,
    _real_app,
    _upgrade_actions,
    shield_tables,
    up,
)

NEW_URL = "/api/v1/ext/shield/defender/updates/propose-all"
OLD_URL = "/api/v1/ext/nexus-soc/defender/updates/propose-all"


@pytest.mark.asyncio
async def test_an_open_proposal_from_before_the_rename_still_counts(client, db_session, test_settings, shield_tables, monkeypatch):  # noqa: F811
    from nodvard_deck.models import Action

    app = await _real_app(client, db_session, test_settings, shield_tables, monkeypatch)
    # Ein Vorschlag aus 0.6, als die Erweiterung noch `nexus-soc` hiess (die Zeile in der Datenbank behaelt die alte Kennung).
    db_session.add(Action(
        ext_id="nexus-soc", action_type="nexus_soc.upgrade", host_id=app.ids["pi"], risk="medium", status="proposed",
        payload={"mode": "all", "manager": "apt", "packages": [], "command": up.upgrade_command("apt", "all")},
        proposed_by_type="user", proposed_by_id="x", reason="Alle Updates auf pi", gate_decision={"rule": "autonomy:propose"},
        expires_at=NOW + timedelta(hours=3),
    ))
    # Ein offener Neustart derselben Erweiterung auf web zaehlt nicht als Update-Vorschlag.
    db_session.add(Action(
        ext_id="shield", action_type="nexus_soc.reboot", host_id=app.ids["web"], risk="high", status="proposed",
        payload={"command": up.REBOOT_COMMAND}, proposed_by_type="user", proposed_by_id="x", reason="Neustart von web",
        gate_decision={"rule": "autonomy:propose"}, expires_at=NOW + timedelta(hours=3),
    ))
    await db_session.commit()

    r = await client.post(NEW_URL, json={"mode": "security"}, headers=app.owner)
    assert r.status_code == 200, r.text
    got = {x["host_id"]: x for x in r.json()["results"]}
    assert got[app.ids["pi"]]["result"] == "skipped" and "wartet schon auf Freigabe" in got[app.ids["pi"]]["reason"]
    assert got[app.ids["web"]]["result"] == "proposed"
    assert len(await _upgrade_actions(db_session)) == 2  # der alte Vorschlag und der neue fuer web


@pytest.mark.asyncio
async def test_the_old_address_answers_like_the_new_one(client, db_session, test_settings, shield_tables, monkeypatch):  # noqa: F811
    app = await _real_app(client, db_session, test_settings, shield_tables, monkeypatch)

    assert (await client.post(OLD_URL, json={"mode": "all"})).status_code == 401
    denied = await client.post(OLD_URL, json={"mode": "all"}, headers=app.viewer)
    assert denied.status_code == 403, denied.text
    assert await _upgrade_actions(db_session) == []

    old = await client.post(OLD_URL, json={"mode": "security"}, headers=app.owner)
    assert old.status_code == 200, old.text
    assert old.json()["counts"]["proposed"] == 2
    # Beide Adressen sehen dieselben offenen Vorschlaege.
    assert (await client.post(NEW_URL, json={"mode": "security"}, headers=app.owner)).json()["counts"]["skipped"] == 2

    from nodvard_deck.main import app as fastapi_app

    fastapi_app.openapi_schema = None
    try:
        paths = fastapi_app.openapi()["paths"]
    finally:
        fastapi_app.openapi_schema = None
    assert paths[OLD_URL]["post"].get("deprecated") is True
    assert not paths[NEW_URL]["post"].get("deprecated")
