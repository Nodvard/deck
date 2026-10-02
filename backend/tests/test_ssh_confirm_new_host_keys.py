"""Bestätigungspflicht für neue Server-Schlüssel: ist sie an, merkt sich kein Hintergrundjob (Status,
Terminal, Erweiterungen) mehr still einen neuen Server-Schlüssel -- das geht nur noch über „Verbindung
prüfen“ und Bestätigen.

Reihenfolge, die erste Antwort gilt: (1) Merker am Server nach „Schlüssel vergessen“, (2) Umgebungsvariable
NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS, (3) gespeicherte Einstellung `ssh.confirm_new_host_keys`
(neue Installationen: an, gesetzt vom Bootstrap des ersten Kontos), (4) sonst aus -- bestehende
Installationen behalten das alte Verhalten (TOFU).

Die Test-Einstellungen stellen die Umgebungsvariable auf `False` (siehe conftest); wer die Reihenfolge ab
Punkt 3 prüfen will, setzt `ssh_confirm_new_host_keys = None`.
"""

from __future__ import annotations

import pytest
from nodvard_deck.config import Settings
from nodvard_deck.core import ssh
from nodvard_deck.models import Host, HostCredential, KnownHostKey, Setting
from nodvard_deck.services import host_check
from nodvard_deck.services import hosts as hosts_service
from nodvard_deck.services import settings as settings_service
from sqlalchemy import delete, select

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


async def _make_existing_install(db_session, test_settings) -> None:
    """So sieht eine Installation aus, die vor der Bestätigungspflicht angelegt wurde: ohne den
    gespeicherten Wert und ohne Umgebungsvariable."""
    test_settings.ssh_confirm_new_host_keys = None
    await db_session.execute(delete(Setting).where(Setting.key == "ssh.confirm_new_host_keys"))
    await db_session.commit()


async def _known(db_session) -> list[KnownHostKey]:
    db_session.expire_all()
    return list((await db_session.execute(select(KnownHostKey))).scalars().all())


@pytest.fixture(autouse=True)
def _clean_state():
    host_check.reset_state()
    yield
    host_check.reset_state()


def test_setting_is_not_set_by_default_and_reads_both_env_names(monkeypatch):
    assert Settings().ssh_confirm_new_host_keys is None, "nicht gesetzt: die gespeicherte Einstellung gilt"
    monkeypatch.setenv("NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS", "")
    assert Settings().ssh_confirm_new_host_keys is None, "leer zählt als nicht gesetzt"
    monkeypatch.setenv("NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS", "false")
    assert Settings().ssh_confirm_new_host_keys is False
    monkeypatch.setenv("NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS", "true")
    assert Settings().ssh_confirm_new_host_keys is True
    monkeypatch.delenv("NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS")
    monkeypatch.setenv("LATTICE_SSH_CONFIRM_NEW_HOST_KEYS", "1")
    assert Settings().ssh_confirm_new_host_keys is True, "der alte Name gilt weiter"


@pytest.mark.asyncio
@pytest.mark.parametrize("confirm", [False, True])
async def test_resolve_connection_target_follows_the_variable(client, db_session, test_settings, confirm):
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
    """Bestehende Installation (kein gespeicherter Wert, keine Variable): der Status-Abruf merkt den
    neuen Schlüssel still -- wie bisher."""
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    await _make_existing_install(db_session, test_settings)

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


async def _stored(db_session) -> object:
    db_session.expire_all()
    row = await db_session.get(Setting, ("ssh.confirm_new_host_keys", "global", ""))
    return None if row is None else row.value.get("value")


async def _target_for(db_session, test_settings, host_id):
    host = await db_session.get(Host, host_id)
    credential = (await db_session.execute(select(HostCredential))).scalars().one()
    return host, credential, await hosts_service.resolve_connection_target(db_session, test_settings, host, credential)


@pytest.mark.asyncio
async def test_a_new_installation_requires_confirmation_by_default(
    client, db_session, local_ssh_server, test_settings, ssh_server_stats
):
    """Der Bootstrap des ersten Kontos schaltet die Pflicht ein: Status und Hintergrund merken nichts."""
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    test_settings.ssh_confirm_new_host_keys = None  # keine Übersteuerung
    assert await _stored(db_session) is True

    _, _, target = await _target_for(db_session, test_settings, host["id"])
    assert target.allow_tofu is False
    body = (await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))).json()
    assert body["status"] == "unknown" and "noch nicht bestätigt" in body["detail"]
    assert await _known(db_session) == []
    assert ssh_server_stats["begin_auth"] == 0 and ssh_server_stats["password_tries"] == 0


@pytest.mark.asyncio
async def test_an_existing_installation_keeps_the_old_behaviour(client, db_session, local_ssh_server, test_settings):
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    await _make_existing_install(db_session, test_settings)
    assert await _stored(db_session) is None

    _, _, target = await _target_for(db_session, test_settings, host["id"])
    assert target.allow_tofu is True
    assert (await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))).json()["status"] == "up"
    [row] = await _known(db_session)
    assert row.accepted_by_user_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stored,override,expect_confirm", [
    (True, None, True), (False, None, False), (None, None, False),
    (True, False, False), (False, True, True), (None, True, True),
])
async def test_order_of_the_settings(client, db_session, test_settings, stored, override, expect_confirm):
    """Umgebungsvariable (falls gesetzt) vor dem gespeicherten Wert, der vor dem Standard „aus“."""
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, 22)
    await _make_existing_install(db_session, test_settings)
    if stored is not None:
        await settings_service.set_global(db_session, "ssh.confirm_new_host_keys", stored)
        await db_session.commit()
    test_settings.ssh_confirm_new_host_keys = override
    _, _, target = await _target_for(db_session, test_settings, host["id"])
    assert target.allow_tofu is (not expect_confirm)


async def _pin_via_check(client, token, host_id):
    check = await client.post(CHECK.format(host_id), headers=_auth(token))
    key = check.json()["host_key"]
    pin = await client.post(
        PIN.format(host_id), json={"key_type": key["key_type"], "fingerprint": key["fingerprint"]}, headers=_auth(token)
    )
    assert pin.status_code == 201, pin.text
    return key


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [False, None])
async def test_forgetting_a_key_blocks_silent_pinning_until_confirmed(
    client, db_session, local_ssh_server, test_settings, ssh_server_stats, override
):
    """Auch bei „still merken“ (Variable false oder bestehende Installation): nach „vergessen“ geht die
    Verbindung über den Pool nur noch nach ausdrücklicher Bestätigung, und es wird nichts gesendet."""
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    await _make_existing_install(db_session, test_settings)
    test_settings.ssh_confirm_new_host_keys = override

    # Wie bisher: der erste Kontakt merkt den Schlüssel still.
    assert (await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))).json()["status"] == "up"
    [pinned] = await _known(db_session)
    auth_before = ssh_server_stats["begin_auth"]
    await ssh.reset_ssh_pool()

    forget = await client.delete(f"{PIN.format(host['id'])}/{pinned.key_type}", headers=_auth(token))
    assert forget.status_code == 204, forget.text
    assert await _known(db_session) == []

    async def _connect():
        db_session.expire_all()
        _, credential, target = await _target_for(db_session, test_settings, host["id"])
        return target, await ssh.get_ssh_pool().get(db_session, target, credential_id=credential.id)

    with pytest.raises(ssh.HostKeyUnknown):
        await _connect()
    # Status-Abruf (auch von einem Betrachter ausgelöst) und Hintergrund-Sammler gehen denselben Weg.
    body = (await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))).json()
    assert body["status"] == "unknown" and "noch nicht bestätigt" in body["detail"]
    assert await _known(db_session) == [], "kein neuer gemerkter Schlüssel"
    assert ssh_server_stats["begin_auth"] == auth_before, "kein Passwort an den unbestätigten Schlüssel"

    # Ausdrücklich bestätigen: danach geht es wieder, und der Merker ist weg.
    key = await _pin_via_check(client, token, host["id"])
    assert key["key_type"] == pinned.key_type
    db_session.expire_all()
    assert await db_session.get(Setting, (hosts_service.HOST_KEY_CONFIRM_REQUIRED, "host", host["id"])) is None
    target, conn = await _connect()
    assert target.allow_tofu is (override is not True) and (await ssh.run(conn, "true"))[0] == 0
    assert len(await _known(db_session)) == 1


@pytest.mark.asyncio
async def test_the_forget_marker_is_committed_with_the_delete_and_survives_a_provider_resync(
    client, db_session, local_ssh_server, test_settings
):
    """Löschen und Merker gehören in denselben Commit; der Merker liegt nicht in `Host.host_metadata`
    (das überschreibt der Abgleich der Anbieter-Erweiterungen)."""
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    await _make_existing_install(db_session, test_settings)
    marker = (hosts_service.HOST_KEY_CONFIRM_REQUIRED, "host", host["id"])
    db_session.add(KnownHostKey(host_id=host["id"], key_type="ssh-ed25519", fingerprint="SHA256:alt"))
    await db_session.commit()

    assert await hosts_service.clear_known_host_key(db_session, host["id"], "ssh-ed25519") is True
    await db_session.rollback()  # ohne Commit ist beides wieder weg ...
    assert len(await _known(db_session)) == 1 and await db_session.get(Setting, marker) is None
    assert await hosts_service.clear_known_host_key(db_session, host["id"], "ssh-ed25519") is True
    await db_session.commit()  # ... und mit Commit sind beide Änderungen da
    assert await _known(db_session) == [] and await db_session.get(Setting, marker) is not None

    (await db_session.get(Host, host["id"])).host_metadata = {"vom": "anbieter"}
    await db_session.commit()
    assert await hosts_service.host_key_confirmation_required(db_session, test_settings, host["id"]) is True


@pytest.mark.asyncio
async def test_deleting_the_host_removes_the_marker(client, db_session, local_ssh_server, test_settings):
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    await hosts_service.require_host_key_confirmation(db_session, host["id"])
    await db_session.commit()
    assert (await client.delete(f"/api/v1/hosts/{host['id']}", headers=_auth(token))).status_code == 204
    db_session.expire_all()
    assert await db_session.get(Setting, (hosts_service.HOST_KEY_CONFIRM_REQUIRED, "host", host["id"])) is None


@pytest.mark.asyncio
async def test_a_wrong_fingerprint_does_not_lift_the_marker(client, db_session, local_ssh_server, test_settings):
    """Nur der echte Bestätigen-Weg nimmt den Merker weg: ohne frische Prüfung gibt es 409."""
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    await hosts_service.require_host_key_confirmation(db_session, host["id"])
    await db_session.commit()
    r = await client.post(PIN.format(host["id"]), json={"key_type": "ssh-ed25519", "fingerprint": "SHA256:selbstgebaut"}, headers=_auth(token))
    assert r.status_code == 409
    assert await hosts_service.host_key_confirmation_required(db_session, test_settings, host["id"]) is True


@pytest.mark.asyncio
async def test_settings_api_shows_and_changes_the_switch(client, db_session, test_settings):
    token = await _bootstrap_owner(client)
    test_settings.ssh_confirm_new_host_keys = None

    def _value(listing):
        return next(r["value"] for r in listing if r["key"] == "ssh.confirm_new_host_keys")

    assert _value((await client.get("/api/v1/settings", headers=_auth(token))).json()) is True, "neue Installation: an"
    r = await client.put("/api/v1/settings/ssh.confirm_new_host_keys", json={"value": False}, headers=_auth(token))
    assert r.status_code == 200, r.text
    assert _value((await client.get("/api/v1/settings", headers=_auth(token))).json()) is False
    bad = await client.put("/api/v1/settings/ssh.confirm_new_host_keys", json={"value": "ja"}, headers=_auth(token))
    assert bad.status_code == 422

    # Ohne gespeicherten Wert (bestehende Installation) steht dort „aus“.
    await _make_existing_install(db_session, test_settings)
    assert _value((await client.get("/api/v1/settings", headers=_auth(token))).json()) is False


@pytest.mark.asyncio
async def test_settings_api_reports_the_variable_and_refuses_changes(client, test_settings):
    token = await _bootstrap_owner(client)
    test_settings.ssh_confirm_new_host_keys = False  # Variable gesetzt, obwohl der Bootstrap „an“ gespeichert hat
    listing = (await client.get("/api/v1/settings", headers=_auth(token))).json()
    assert next(r["value"] for r in listing if r["key"] == "ssh.confirm_new_host_keys") is False
    r = await client.put("/api/v1/settings/ssh.confirm_new_host_keys", json={"value": True}, headers=_auth(token))
    assert r.status_code == 409 and "NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_target_resolved_just_before_forgetting_does_not_pin_silently(
    client, db_session, local_ssh_server, test_settings, ssh_server_stats
):
    """Ein Hintergrunddienst hat sein Ziel kurz vor „vergessen“ geholt (damals noch ohne Merker, also mit
    stillem Merken) und verbindet erst danach: auch dann geht nichts an den Server, und nichts wird gemerkt."""
    _, port, *_ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = await _host_with_password(client, token, port)
    await _make_existing_install(db_session, test_settings)
    assert (await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth(token))).json()["status"] == "up"
    [pinned] = await _known(db_session)
    await ssh.reset_ssh_pool()
    auth_before = ssh_server_stats["begin_auth"]

    _, _, stale = await _target_for(db_session, test_settings, host["id"])
    assert stale.allow_tofu is True
    forget = await client.delete(f"{PIN.format(host['id'])}/{pinned.key_type}", headers=_auth(token))
    assert forget.status_code == 204, forget.text

    db_session.expire_all()
    with pytest.raises(ssh.HostKeyUnknown):
        await ssh.connect(db_session, stale)
    assert await _known(db_session) == []
    assert ssh_server_stats["begin_auth"] == auth_before, "kein Passwort an den unbestätigten Schlüssel"
