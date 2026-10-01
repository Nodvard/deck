"""Einstellung `ssh_confirm_new_host_keys` (NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS, Standard aus):
ist sie an, merkt sich kein Hintergrundjob (Status, Terminal, Erweiterungen) mehr still einen neuen
Server-Schlüssel -- das geht nur noch über „Verbindung prüfen“ und Bestätigen.

Ist sie aus (Standard), bleibt alles wie bisher (TOFU), damit sich für bestehende Installationen
nichts ändert.
"""

from __future__ import annotations

import pytest
from nodvard_deck.config import Settings
from nodvard_deck.core import ssh
from nodvard_deck.models import Host, HostCredential, KnownHostKey
from nodvard_deck.services import host_check
from nodvard_deck.services import hosts as hosts_service
from sqlalchemy import select

CHECK = "/api/v1/hosts/{}/check"
PIN = "/api/v1/hosts/{}/known-hosts"


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _host_with_password(client, token, port) -> dict:
    r = await client.post("/api/v1/hosts", json={"name": "bastel-pi", "address": "127.0.0.1"}, headers=_auth(token))
    assert r.status_code == 201, r.text
    host = r.json()
    r = await client.post(
        f"/api/v1/hosts/{host['id']}/credentials",
        json={"kind": "ssh_password", "username": "testuser", "port": port, "secret_value": "test-password-123"},
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    return host


async def _known(db_session) -> list[KnownHostKey]:
    db_session.expire_all()
    return list((await db_session.execute(select(KnownHostKey))).scalars().all())


@pytest.fixture(autouse=True)
def _clean_state():
    host_check.reset_state()
    yield
    host_check.reset_state()


def test_setting_defaults_to_off_and_reads_both_env_names(monkeypatch):
    assert Settings().ssh_confirm_new_host_keys is False
    monkeypatch.setenv("NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS", "true")
    assert Settings().ssh_confirm_new_host_keys is True
    monkeypatch.delenv("NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS")
    monkeypatch.setenv("LATTICE_SSH_CONFIRM_NEW_HOST_KEYS", "1")
    assert Settings().ssh_confirm_new_host_keys is True, "der alte Name gilt weiter"


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [False, True])
async def test_resolve_connection_target_follows_the_setting(client, db_session, test_settings, confirm):
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, 22)
    test_settings.ssh_confirm_new_host_keys = confirm
    target = await hosts_service.resolve_connection_target(
        db_session, test_settings, await db_session.get(Host, host["id"]),
        (await db_session.execute(select(HostCredential))).scalars().one(),
    )
    assert target.allow_tofu is (not confirm)


@pytest.mark.asyncio
async def test_default_keeps_background_tofu(client, db_session, local_ssh_server, test_settings):
    """Standard: der Status-Abruf merkt den neuen Schlüssel still -- wie bisher."""
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    assert test_settings.ssh_confirm_new_host_keys is False

    r = await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))
    assert r.status_code == 200 and r.json()["status"] == "up", r.text
    [row] = await _known(db_session)
    assert row.host_id == host["id"] and row.accepted_by_user_id is None, "automatisch gemerkt"


@pytest.mark.asyncio
async def test_with_the_setting_on_status_reports_unknown_and_pins_nothing(
    client, db_session, local_ssh_server, test_settings, ssh_server_stats
):
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    test_settings.ssh_confirm_new_host_keys = True

    r = await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "unknown" and body["checked_live"] is True
    assert body["detail"] == (
        "Der Server-Schlüssel von diesem Server ist noch nicht bestätigt. "
        "Unter Einstellungen → Server & Zugänge „Verbindung prüfen“ drücken."
    )
    assert await _known(db_session) == [], "kein stilles Merken im Hintergrund"
    assert ssh_server_stats["begin_auth"] == 0 and ssh_server_stats["password_tries"] == 0, "nichts gesendet"
    db_session.expire_all()
    assert (await db_session.get(Host, host["id"])).status == "unknown"


@pytest.mark.asyncio
async def test_with_the_setting_on_the_pool_refuses_until_the_key_is_confirmed(
    client, db_session, local_ssh_server, test_settings, ssh_server_stats
):
    """Der Weg von Terminal und Erweiterungen (`SshPool.get`): erst nach „Verbindung prüfen“ und
    Bestätigen geht es, danach ohne weiteres."""
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    test_settings.ssh_confirm_new_host_keys = True
    row = await db_session.get(Host, host["id"])
    credential = (await db_session.execute(select(HostCredential))).scalars().one()

    async def _try():
        target = await hosts_service.resolve_connection_target(db_session, test_settings, row, credential)
        return await ssh.get_ssh_pool().get(db_session, target, credential_id=credential.id)

    with pytest.raises(ssh.HostKeyUnknown):
        await _try()
    assert await _known(db_session) == []
    assert ssh_server_stats["begin_auth"] == 0

    # Prüfen und bestätigen (der Nutzer vergleicht den Fingerabdruck).
    check = await client.post(CHECK.format(host["id"]), headers=_auth(token))
    assert [i["status"] for i in check.json()["items"]] == ["ok", "confirm"]
    key = check.json()["host_key"]
    pin = await client.post(PIN.format(host["id"]), json={"key_type": key["key_type"], "fingerprint": key["fingerprint"]}, headers=_auth(token))
    assert pin.status_code == 201, pin.text

    conn = await _try()
    exit_code, _, _ = await ssh.run(conn, "true")
    assert exit_code == 0
    status = await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))
    assert status.json()["status"] == "up"
    assert len(await _known(db_session)) == 1


@pytest.mark.asyncio
async def test_with_the_setting_on_an_already_pinned_key_is_unaffected(client, db_session, local_ssh_server, test_settings):
    """Bestehende Installationen haben ihre Schlüssel schon gemerkt: für sie ändert sich nichts."""
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    # Erst wie bisher (TOFU) verbinden und merken ...
    assert (await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))).json()["status"] == "up"
    await ssh.reset_ssh_pool()
    # ... dann die Einstellung einschalten.
    test_settings.ssh_confirm_new_host_keys = True
    r = await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))
    assert r.json()["status"] == "up" and r.json()["detail"] is None
    assert len(await _known(db_session)) == 1
