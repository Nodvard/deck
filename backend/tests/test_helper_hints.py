"""Hinweis „Server neu einrichten“ in der Übersicht (`services.helper_hints`): nur wenn sudo dort Skripte erlaubt (volle
Regel oder die eigene Regel eines Skripts) und ein Hilfsskript nicht das mitgelieferte ist; nie für root und Windows.
Den Stand schreiben „Verbindung prüfen“ und der Kern-Job, die Übersicht liest nur den Merker. Wegklicken gilt je
Server, bis sich die fehlenden Skripte ändern.

Gegen den lokalen SSH-Server aus `conftest.py` (`fake_probe` beantwortet die Abfrage)."""

from __future__ import annotations

import pytest
from nodvard_sdk import ExtensionManifest, HostRequirementSpec
from test_apps_api import _user_token
from test_host_check_helpers import (
    OTHER,
    SHIELD,
    _answer,
    _host_with_login,
    _missing,
    _owner,
    _present,
)

from nodvard_deck.db.session import session_scope
from nodvard_deck.ext.runtime import LoadedExtension, get_extension_runtime
from nodvard_deck.models import Host
from nodvard_deck.services import helper_hints, host_check
from nodvard_deck.services.host_check import helper_check_command


@pytest.fixture
def modules(client):
    """Zwei vorgetäuschte eingeschaltete Module mit Hilfsskript und root-Bedarf (wie in `test_host_check_helpers`)."""
    runtime = get_extension_runtime()
    host_check.reset_state()
    for ext_id, helper in (("fake-a", SHIELD), ("fake-b", OTHER)):
        manifest = ExtensionManifest(id=ext_id, name=f"Modul {ext_id}", version="0.1.0", api_version="0.1", entrypoint="x:y")
        runtime.loaded[ext_id] = LoadedExtension(
            manifest=manifest, instance=None, ctx=None, granted_permissions=[], root_helper=helper
        )
        runtime.ui.register_host_requirement(ext_id, HostRequirementSpec(id="root", label="Root", needs_root=True))
    yield
    host_check.reset_state()
    for ext_id in ("fake-a", "fake-b"):
        runtime.loaded.pop(ext_id, None)
        runtime.ui.clear_extension(ext_id)


CHECK = "/api/v1/hosts/{}/check"
DISMISS = "/api/v1/hosts/{}/helper-hint/dismiss"
FULL_RULE = "User testuser may run the following commands:\n    (root) NOPASSWD: ALL\n"


@pytest.mark.parametrize(
    ("line", "full"),
    [
        ("(root) NOPASSWD: ALL", True),
        ("(ALL : ALL) NOPASSWD: ALL", True),
        ("(root) NOPASSWD: /usr/local/lib/nodvard-deck/shield-root", False),
        ("(ALL) ALL", False),
        ("(root) NOPASSWD: SETENV: ALL", True),
        ("(ALL) SETENV: NOPASSWD: ALL", True),
        ("(root) NOPASSWD:ALL", True),
        ("(www-data) NOPASSWD: ALL", False),
        ("(root) PASSWD: ALL", False),
        ("(root) NOPASSWD: ALL, /usr/bin/true", False),
        ("", False),
    ],
)
def test_only_the_full_rule_without_password_counts(line, full):
    assert helper_hints.has_full_rule([line]) is full


def test_stale_means_missing_or_not_the_bundled_script():
    seen = host_check.parse_helper_check(
        _answer(_present(SHIELD, version="1", sha="1" * 64), _missing(OTHER)), [SHIELD, OTHER]
    )
    assert helper_hints.stale_helpers([SHIELD, OTHER], seen) == {"fake-a": 2, "fake-b": 1}
    fine = host_check.parse_helper_check(_answer(_present(SHIELD), _present(OTHER)), [SHIELD, OTHER])
    assert helper_hints.stale_helpers([SHIELD, OTHER], fine) == {}


async def _overview_hints(client, headers) -> list[dict]:
    r = await client.get("/api/v1/overview", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["helper_hints"]


@pytest.mark.asyncio
async def test_check_with_full_rule_and_missing_script_shows_a_hint_until_it_is_fine(
    client, modules, local_ssh_server, fake_probe
):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = (0, "", "")
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(_present(SHIELD), _missing(OTHER), sudo_list=FULL_RULE), "")
    assert (await client.post(CHECK.format(host_id), headers=headers)).status_code == 200

    hints = await _overview_hints(client, headers)
    assert [(h["host_id"], h["host_name"], h["modules"], h["without_root"]) for h in hints] == [
        (host_id, "bastel-pi", ["Modul fake-b"], False)
    ]

    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(_present(SHIELD), _present(OTHER), sudo_list=FULL_RULE), "")
    await client.post(CHECK.format(host_id), headers=headers)
    assert await _overview_hints(client, headers) == []


@pytest.mark.asyncio
async def test_without_the_full_rule_there_is_no_hint(client, modules, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = (1, "", "sudo: a password is required\n")
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(_missing(SHIELD), _missing(OTHER)), "")
    await client.post(CHECK.format(host_id), headers=headers)
    assert await _overview_hints(client, headers) == []


@pytest.mark.asyncio
async def test_check_and_job_judge_the_full_rule_the_same_way(
    client, modules, local_ssh_server, fake_probe, monkeypatch, test_settings
):
    """`NOPASSWD: SETENV: ALL`: „Verbindung prüfen“ und der Job urteilen beide über `has_full_rule`, der Hinweis bleibt
    stabil."""
    from nodvard_deck import config

    monkeypatch.setattr(config, "_settings", test_settings)
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    rule = "User testuser may run the following commands:\n    (root) NOPASSWD: SETENV: ALL\n"
    fake_probe[host_check.SUDO_COMMAND] = (0, "", "")
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(_present(SHIELD), _missing(OTHER), sudo_list=rule), "")
    await client.post(CHECK.format(host_id), headers=headers)
    assert [h["modules"] for h in await _overview_hints(client, headers)] == [["Modul fake-b"]]
    await helper_hints.run_once()
    assert [h["modules"] for h in await _overview_hints(client, headers)] == [["Modul fake-b"]]

    # Volle Regel nur über eine Gruppe, die `sudo -n -l` nicht als `ALL` zeigt: beide melden nichts.
    narrow = "User testuser may run the following commands:\n    (root) NOPASSWD: /usr/bin/true\n"
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(_present(SHIELD), _missing(OTHER), sudo_list=narrow), "")
    await client.post(CHECK.format(host_id), headers=headers)
    assert await _overview_hints(client, headers) == []
    await helper_hints.run_once()
    assert await _overview_hints(client, headers) == []


@pytest.mark.asyncio
async def test_a_server_with_only_helper_rules_gets_the_hint_too(
    client, modules, local_ssh_server, fake_probe, monkeypatch, test_settings
):
    from nodvard_deck import config

    monkeypatch.setattr(config, "_settings", test_settings)
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    rules = (
        "User testuser may run the following commands:\n"
        f"    (root) NOPASSWD: {SHIELD.install_path}\n    (root) NOPASSWD: {OTHER.install_path}\n"
    )
    fake_probe[host_check.SUDO_COMMAND] = (1, "", "sudo: a password is required\n")
    fake_probe[helper_check_command([SHIELD, OTHER])] = (
        0, _answer(_present(SHIELD), _present(OTHER, version="0", sha="0" * 64), sudo_list=rules), "",
    )
    await client.post(CHECK.format(host_id), headers=headers)
    assert [(h["modules"], h["without_root"]) for h in await _overview_hints(client, headers)] == [(["Modul fake-b"], True)]
    await helper_hints.run_once()
    assert [(h["modules"], h["without_root"]) for h in await _overview_hints(client, headers)] == [(["Modul fake-b"], True)]


def test_a_helper_rule_must_name_the_script_itself():
    path = SHIELD.install_path
    assert helper_hints.allows_helper([f"(root) NOPASSWD: {path}"], path)
    assert helper_hints.allows_helper([f"(ALL : ALL) NOPASSWD: /usr/bin/true, {path}"], path)
    assert helper_hints.allows_helper([f"(root) SETENV: NOPASSWD: {path}"], path)
    for line in (
        f"(root) NOPASSWD: /usr/bin/tee {path}", f"(root) PASSWD: {path}", f"(www-data) NOPASSWD: {path}",
        f"(root) NOPASSWD: {path}.alt", f"{path}", "(root) NOPASSWD: ALL",
    ):
        assert not helper_hints.allows_helper([line], path), line


@pytest.mark.asyncio
async def test_the_check_records_nothing_for_root():
    """„Verbindung prüfen“ als root: kein Hilfsskript nötig, der Merker wird geleert (`{}`), ohne Abfrage."""
    state = host_check._State()
    host_check._step_helpers(state, "root", [SHIELD, OTHER], None)
    assert state.helper_stale == {}
    assert {item.status for item in state.items} == {"skipped"}


@pytest.mark.asyncio
async def test_the_job_uses_the_same_query_and_skips_root_and_windows(
    client, db_session, modules, local_ssh_server, fake_probe, monkeypatch, test_settings
):
    from nodvard_deck import config

    monkeypatch.setattr(config, "_settings", test_settings)  # der Job liest `get_settings()` wie im Betrieb
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[helper_check_command([SHIELD, OTHER])] = (
        0, _answer(_present(SHIELD, version="1", sha="1" * 64), _present(OTHER), sudo_list=FULL_RULE), "",
    )
    counts = await helper_hints.run_once()
    assert counts["hint"] == 1, counts
    assert [h["modules"] for h in await _overview_hints(client, headers)] == [["Modul fake-a"]]

    host = await db_session.get(Host, host_id)
    host.os_family = "windows"
    await db_session.commit()
    assert await _overview_hints(client, headers) == []  # Windows nie, auch mit altem Stand im Merker
    await helper_hints.run_once()
    async with session_scope() as session:
        state = await helper_hints.settings_service.get_global(session, helper_hints.KEY_STATE, {})
    assert host_id not in (state or {})

    # root: der Job fragt gar nicht erst (kein SSH) und leert einen alten Stand.
    root_host = (await client.post("/api/v1/hosts", json={"name": "root-server", "address": "127.0.0.1"}, headers=headers)).json()
    r = await client.post(
        f"/api/v1/hosts/{root_host['id']}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": port, "secret_value": "test-password-123"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    async with session_scope() as session:
        await helper_hints.record(session, root_host["id"], {"fake-a": 2})
    assert [h["host_id"] for h in await _overview_hints(client, headers)] == [root_host["id"]]
    counts = await helper_hints.run_once()
    assert counts["skipped"] == 1 and counts["checked"] == 1, counts  # nur root, Windows ist gar nicht mehr dabei
    assert await _overview_hints(client, headers) == []


@pytest.mark.asyncio
async def test_dismiss_hides_the_hint_until_the_missing_scripts_change(client, db_session, modules, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = (0, "", "")
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(_present(SHIELD), _missing(OTHER), sudo_list=FULL_RULE), "")
    await client.post(CHECK.format(host_id), headers=headers)
    assert len(await _overview_hints(client, headers)) == 1

    viewer = await _user_token(client, db_session, "nurlesen", permissions=("hosts.read",))
    denied = await client.post(DISMISS.format(host_id), headers={"Authorization": f"Bearer {viewer}"})
    assert denied.status_code == 403

    assert (await client.post(DISMISS.format(host_id), headers=headers)).status_code == 204
    assert await _overview_hints(client, headers) == []
    await client.post(CHECK.format(host_id), headers=headers)  # derselbe Stand: bleibt ausgeblendet
    assert await _overview_hints(client, headers) == []

    # Ein Update bringt eine neuere Version mit: wieder sichtbar.
    async with session_scope() as session:
        await helper_hints.record(session, host_id, {"fake-b": 2})
    assert len(await _overview_hints(client, headers)) == 1

    assert (await client.post(DISMISS.format("gibt-es-nicht"), headers=headers)).status_code == 404


def _tight_and_full() -> str:
    return FULL_RULE + "".join(f"    (root) NOPASSWD: {helper.install_path}\n" for helper in (SHIELD, OTHER))


@pytest.mark.asyncio
async def test_an_unneeded_full_rule_is_yellow_until_it_is_wanted(client, modules, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[helper_check_command([SHIELD, OTHER])] = (
        0, _answer(_present(SHIELD), _present(OTHER), sudo_list=_tight_and_full()), "",
    )
    body = (await client.post(CHECK.format(host_id), headers=headers)).json()
    root = next(item for item in body["items"] if item["id"] == "root")
    assert (root["status"], root["detail"]) == ("warn", host_check.FULL_RULE_NOT_NEEDED)
    assert root["hint"] == host_check.FULL_RULE_HINT and "„Volle sudo-Regel entfernen“" in root["hint"]
    assert body["full_rule_removable"] is True
    hints = await _overview_hints(client, headers)
    assert [(h["modules"], h["full_rule_removable"]) for h in hints] == [([], True)]

    saved = await client.patch(f"/api/v1/hosts/{host_id}", json={"sudo_full_wanted": True}, headers=headers)
    assert saved.status_code == 200 and saved.json()["sudo_full_wanted"] is True
    assert await _overview_hints(client, headers) == []
    body = (await client.post(CHECK.format(host_id), headers=headers)).json()
    root = next(item for item in body["items"] if item["id"] == "root")
    assert (root["status"], root["detail"]) == ("ok", host_check.FULL_RULE_WANTED)
    assert body["full_rule_removable"] is True

    saved = await client.patch(f"/api/v1/hosts/{host_id}", json={"sudo_full_wanted": False}, headers=headers)
    assert saved.json()["sudo_full_wanted"] is False
    assert [h["full_rule_removable"] for h in await _overview_hints(client, headers)] == [True]
    audit = (await client.get("/api/v1/audit?action=host.updated", headers=headers)).json()
    assert [e["detail"]["changed"]["sudo_full_wanted"]["to"] for e in audit] == [False, True]


@pytest.mark.asyncio
async def test_dismissing_an_unneeded_full_rule_lasts_until_the_rule_was_gone(
    client, modules, local_ssh_server, fake_probe,
):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    query = helper_check_command([SHIELD, OTHER])
    fake_probe[query] = (0, _answer(_present(SHIELD), _present(OTHER), sudo_list=_tight_and_full()), "")
    await client.post(CHECK.format(host_id), headers=headers)
    assert (await client.post(DISMISS.format(host_id), headers=headers)).status_code == 204
    await client.post(CHECK.format(host_id), headers=headers)
    assert await _overview_hints(client, headers) == []

    tight = _tight_and_full().replace(FULL_RULE, "User testuser may run the following commands:\n")
    fake_probe[query] = (0, _answer(_present(SHIELD), _present(OTHER), sudo_list=tight), "")
    body = (await client.post(CHECK.format(host_id), headers=headers)).json()
    assert body["full_rule_removable"] is False and await _overview_hints(client, headers) == []

    fake_probe[query] = (0, _answer(_present(SHIELD), _present(OTHER), sudo_list=_tight_and_full()), "")
    await client.post(CHECK.format(host_id), headers=headers)
    assert [h["full_rule_removable"] for h in await _overview_hints(client, headers)] == [True]


@pytest.mark.asyncio
async def test_the_job_reports_an_unneeded_full_rule_only_when_every_module_has_a_script(
    client, modules, local_ssh_server, fake_probe, monkeypatch, test_settings,
):
    from nodvard_deck import config

    monkeypatch.setattr(config, "_settings", test_settings)
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    await _host_with_login(client, headers, port)
    fake_probe[helper_check_command([SHIELD, OTHER])] = (
        0, _answer(_present(SHIELD), _present(OTHER), sudo_list=_tight_and_full()), "",
    )
    counts = await helper_hints.run_once(helpers_suffice=lambda _host: False)
    assert counts["ok"] == 1 and await _overview_hints(client, headers) == []

    counts = await helper_hints.run_once()
    assert counts["hint"] == 1
    assert [(h["modules"], h["full_rule_removable"]) for h in await _overview_hints(client, headers)] == [([], True)]


@pytest.mark.parametrize(("sudo_list", "removable"), [
    (_tight_and_full(), True),
    (FULL_RULE + f"    (root) NOPASSWD: {SHIELD.install_path}\n", False),
    (_tight_and_full().replace(FULL_RULE, "User testuser may run the following commands:\n"), False),
])
def test_the_full_rule_is_removable_only_with_a_rule_for_every_script(sudo_list, removable):
    out = _answer(_present(SHIELD), _present(OTHER), sudo_list=sudo_list)
    seen = host_check.parse_helper_check(out, [SHIELD, OTHER])
    assert helper_hints.full_rule_removable([SHIELD, OTHER], host_check.split_sudo_lines(out), seen) is removable
    seen[SHIELD.install_path].owner_ok = False
    assert helper_hints.full_rule_removable([SHIELD, OTHER], host_check.split_sudo_lines(out), seen) is False
