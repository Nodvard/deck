"""Erste Schritte: Das Modul Terminal schaltet sich ein, sobald ein Server einen SSH-Zugang
bekommt -- aber nur, solange niemand es je bewusst ein- oder ausgeschaltet hat. Der Kern kennt
die Erweiterung nicht beim Namen, sondern liest `enable_on` im Manifest."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from nodvard_deck.models import Setting
from nodvard_deck.services import extensions as extensions_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
GENERATE = "/api/v1/hosts/{}/credentials/generate-key"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


async def _owner(client, username="owner1", password="correct-horse-battery") -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _setup(client, db_session, test_settings) -> dict:
    headers = await _owner(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await db_session.commit()
    return headers


async def _host(client, headers, name="bastel-pi") -> str:
    r = await client.post("/api/v1/hosts", json={"name": name, "address": "10.0.0.5"}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _terminal_state(client, headers) -> str:
    r = await client.get("/api/v1/extensions/terminal", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["state"]


async def _password(client, headers, host_id, kind="ssh_password") -> int:
    r = await client.post(
        f"/api/v1/hosts/{host_id}/credentials", headers=headers,
        json={"kind": kind, "username": "pi", "secret_value": "geheim-123"},
    )
    return r.status_code


async def _audit(client, headers, action: str) -> list[dict]:
    r = await client.get("/api/v1/audit", params={"action": action}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_password_credential_enables_untouched_terminal(client, db_session, test_settings):
    headers = await _setup(client, db_session, test_settings)
    assert await _terminal_state(client, headers) == "disabled"
    host_id = await _host(client, headers)

    assert await _password(client, headers, host_id) == 201

    assert await _terminal_state(client, headers) == "enabled"
    entries = await _audit(client, headers, "extension.auto_enabled")
    assert len(entries) == 1
    assert entries[0]["target_id"] == "terminal"
    assert entries[0]["outcome"] == "success"
    assert entries[0]["detail"] == {"trigger": "host_credential"}
    assert entries[0]["actor_type"] == "user"


@pytest.mark.asyncio
async def test_generated_key_enables_untouched_terminal(client, db_session, test_settings):
    headers = await _setup(client, db_session, test_settings)
    host_id = await _host(client, headers)

    r = await client.post(GENERATE.format(host_id), json={"username": "lattice"}, headers=headers)
    assert r.status_code == 201, r.text

    assert await _terminal_state(client, headers) == "enabled"


@pytest.mark.asyncio
async def test_other_extensions_without_enable_on_stay_off(client, db_session, test_settings):
    headers = await _setup(client, db_session, test_settings)
    host_id = await _host(client, headers)
    await _password(client, headers, host_id)

    states = {e["id"]: e["state"] for e in (await client.get("/api/v1/extensions", headers=headers)).json()}
    assert states["terminal"] == "enabled"
    assert states["hello-world"] == "disabled"
    assert states["system"] == "disabled"


@pytest.mark.asyncio
async def test_stays_off_when_user_switched_it_off(client, db_session, test_settings):
    """Eingeschaltet und bewusst wieder ausgeschaltet: der naechste Zugang schaltet nichts ein."""
    headers = await _setup(client, db_session, test_settings)
    assert (await client.post("/api/v1/extensions/terminal/enable", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/extensions/terminal/disable", headers=headers)).status_code == 200
    host_id = await _host(client, headers)

    assert await _password(client, headers, host_id) == 201

    assert await _terminal_state(client, headers) == "disabled"
    assert await _audit(client, headers, "extension.auto_enabled") == []


@pytest.mark.asyncio
async def test_stays_off_when_user_switched_off_an_untouched_one(client, db_session, test_settings):
    """Schon das ausdrueckliche „ausschalten“ eines nie eingeschalteten Moduls ist eine Entscheidung."""
    headers = await _setup(client, db_session, test_settings)
    assert (await client.post("/api/v1/extensions/terminal/disable", headers=headers)).status_code == 200
    host_id = await _host(client, headers)

    await _password(client, headers, host_id)

    assert await _terminal_state(client, headers) == "disabled"


@pytest.mark.asyncio
async def test_installation_from_before_the_feature_is_left_alone(client, db_session, test_settings):
    """Ohne den Vermerk „unberuehrt“ (Erweiterung gab es schon vor dieser Funktion) bleibt sie aus,
    auch wenn niemand weiss, ob sie jemand ausgeschaltet hat."""
    headers = await _setup(client, db_session, test_settings)
    row = await db_session.get(Setting, (extensions_service.UNTOUCHED_KEY_PREFIX + "terminal", "global", ""))
    assert row is not None  # neu entdeckt -> unberuehrt
    await db_session.delete(row)
    await db_session.commit()
    host_id = await _host(client, headers)

    await _password(client, headers, host_id)

    assert await _terminal_state(client, headers) == "disabled"


@pytest.mark.asyncio
async def test_only_the_first_access_decides(client, db_session, test_settings):
    """Schaltet der Nutzer Terminal nach dem automatischen Einschalten aus, kommt es beim
    naechsten Zugang nicht zurueck."""
    headers = await _setup(client, db_session, test_settings)
    host_id = await _host(client, headers)
    await _password(client, headers, host_id)
    assert await _terminal_state(client, headers) == "enabled"

    assert (await client.post("/api/v1/extensions/terminal/disable", headers=headers)).status_code == 200
    other = await _host(client, headers, name="zweiter")
    await _password(client, headers, other)

    assert await _terminal_state(client, headers) == "disabled"
    assert len(await _audit(client, headers, "extension.auto_enabled")) == 1


@pytest.mark.asyncio
async def test_api_token_credential_does_not_enable(client, db_session, test_settings):
    """Nur SSH-Zugaenge (Schluessel, Passwort) machen ein Terminal moeglich."""
    headers = await _setup(client, db_session, test_settings)
    host_id = await _host(client, headers)

    assert await _password(client, headers, host_id, kind="api_token") == 201

    assert await _terminal_state(client, headers) == "disabled"


@pytest.mark.asyncio
async def test_manual_toggle_is_audited(client, db_session, test_settings):
    headers = await _setup(client, db_session, test_settings)
    await client.post("/api/v1/extensions/terminal/enable", headers=headers)
    await client.post("/api/v1/extensions/terminal/disable", headers=headers)

    enabled = await _audit(client, headers, "extension.enabled")
    disabled = await _audit(client, headers, "extension.disabled")
    assert [e["target_id"] for e in enabled] == ["terminal"]
    assert enabled[0]["detail"] == {"state": "enabled"}
    assert [e["target_id"] for e in disabled] == ["terminal"]
    assert disabled[0]["detail"] == {"state": "disabled"}


@pytest.mark.asyncio
async def test_untouched_marker_is_only_set_for_new_records(client, db_session, test_settings):
    await _setup(client, db_session, test_settings)
    keys = {
        row.key for row in (await db_session.execute(select(Setting).where(Setting.key.like("extension.untouched.%")))).scalars()
    }
    assert "extension.untouched.terminal" in keys

    # Ein zweiter Scan legt nichts neu an und stellt geloeschte Vermerke nicht wieder her.
    await db_session.delete(await db_session.get(Setting, ("extension.untouched.terminal", "global", "")))
    await db_session.commit()
    await extensions_service.discover_and_sync(db_session, test_settings)
    assert await db_session.get(Setting, ("extension.untouched.terminal", "global", "")) is None


def test_manifest_enable_on_is_validated():
    from pydantic import ValidationError

    from nodvard_sdk import ExtensionManifest, load_manifest

    base = {"id": "demo", "name": "Demo", "version": "1", "api_version": "0.1.0", "entrypoint": "x:Y"}
    assert ExtensionManifest(**base).enable_on == []
    assert ExtensionManifest(**base, enable_on=["host_credential"]).enable_on == ["host_credential"]
    with pytest.raises(ValidationError, match="Unbekannter Anlass"):
        ExtensionManifest(**base, enable_on=["immer"])

    # Das mitgelieferte Terminal-Manifest nutzt es.
    assert load_manifest(REPO_EXTENSIONS_DIR / "terminal" / "extension.toml").enable_on == ["host_credential"]
