"""„Verbindung prüfen“ mit Hilfsskripten: je Modul ein Punkt `helper:<kennung>`. Ein Modul ruft sein Skript nur auf,
wenn es genau das mitgelieferte ist (sha256): `ok` dann, wenn das so ist und nur root es ändern kann. Fehlt es,
`skipped`; veraltet, neuer oder für andere änderbar ist eine Warnung, ein verändertes Skript ein Fehler. Die Abfrage
führt das Skript nie aus. Der Einrichtungsbefehl (`GET .../setup`) nennt die Skripte, die er mitbringt.

API-Tests gegen den lokalen SSH-Server aus `conftest.py` (`fake_probe` beantwortet Befehle), dazu der Abfragebefehl
selbst in einer echten Shell."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from nodvard_sdk import ExtensionManifest, HostRequirementSpec

from nodvard_deck.ext.root_helpers import RootHelperFile
from nodvard_deck.ext.runtime import LoadedExtension, get_extension_runtime
from nodvard_deck.services import host_check
from nodvard_deck.services.host_check import helper_check_command, parse_helper_check

CHECK = "/api/v1/hosts/{}/check"
PIN = "/api/v1/hosts/{}/known-hosts"
CREDENTIALS = "/api/v1/hosts/{}/credentials"
GENERATE = "/api/v1/hosts/{}/credentials/generate-key"


def _helper(ext_id: str, version: int = 1) -> RootHelperFile:
    text = f"#!/bin/sh\nset -eu\nHELPER_VERSION={version}\n[ \"${{1:-}}\" = version ] && echo {version}\n"
    return RootHelperFile(
        ext_id=ext_id, ext_name=f"Modul {ext_id}", version=version,
        install_path=f"/usr/local/lib/nodvard-deck/{ext_id}-root", text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
    )


SHIELD = _helper("fake-a", 2)
OTHER = _helper("fake-b", 1)


def _fake_modules(entries):
    runtime = get_extension_runtime()
    host_check.reset_state()
    for ext_id, helper in entries:
        manifest = ExtensionManifest(id=ext_id, name=f"Modul {ext_id}", version="0.1.0", api_version="0.1", entrypoint="x:y")
        runtime.loaded[ext_id] = LoadedExtension(
            manifest=manifest, instance=None, ctx=None, granted_permissions=[], root_helper=helper
        )
        runtime.ui.register_host_requirement(ext_id, HostRequirementSpec(id="root", label="Root", needs_root=True))
    yield
    host_check.reset_state()
    for ext_id, _ in entries:
        runtime.loaded.pop(ext_id, None)
        runtime.ui.clear_extension(ext_id)


@pytest.fixture
def modules(client):
    """Zwei vorgetäuschte eingeschaltete Erweiterungen mit Hilfsskript und root-Bedarf; `fake-c` braucht root, hat aber
    keins. Nach `client`: Der Start der App setzt die Laufzeit zurück."""
    yield from _fake_modules((("fake-a", SHIELD), ("fake-b", OTHER), ("fake-c", None)))


@pytest.fixture
def helper_modules(client):
    """Wie `modules`, aber jedes Modul mit root-Bedarf hat ein Hilfsskript."""
    yield from _fake_modules((("fake-a", SHIELD), ("fake-b", OTHER)))


async def _owner(client) -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _host_with_login(client, headers, port, username="testuser") -> str:
    host = (await client.post("/api/v1/hosts", json={"name": "bastel-pi", "address": "127.0.0.1"}, headers=headers)).json()
    r = await client.post(
        CREDENTIALS.format(host["id"]),
        json={"kind": "ssh_password", "username": username, "port": port, "secret_value": "test-password-123"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    key = (await client.post(CHECK.format(host["id"]), headers=headers)).json()["host_key"]
    pinned = await client.post(PIN.format(host["id"]), json={"key_type": key["key_type"], "fingerprint": key["fingerprint"]}, headers=headers)
    assert pinned.status_code == 201, pinned.text
    return host["id"]


def _answer(*entries: str, sudo_list: str = "") -> str:
    return "".join(entries) + "@@nd-sudo\n" + sudo_list


def _present(helper: RootHelperFile, *, version: str | None = None, sha: str | None = None, owner: str = "ok") -> str:
    return (
        f"@@nd-path {helper.install_path}\n@@nd-version {version if version is not None else helper.version}\n"
        f"@@nd-sha {sha or helper.sha256}\n@@nd-owner {owner}\n"
    )


def _missing(helper: RootHelperFile) -> str:
    return f"@@nd-path {helper.install_path}\n@@nd-missing\n"


async def _items(client, headers, host_id) -> dict[str, dict]:
    r = await client.post(CHECK.format(host_id), headers=headers)
    assert r.status_code == 200, r.text
    return {item["id"]: item for item in r.json()["items"]}


@pytest.mark.asyncio
async def test_each_module_with_a_helper_gets_one_item_after_root(client, modules, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = (0, "", "")
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(
        _present(SHIELD), _missing(OTHER),
        sudo_list=f"User testuser may run the following commands:\n    (root) NOPASSWD: ALL\n    (root) NOPASSWD: {SHIELD.install_path}\n",
    ), "")

    r = await client.post(CHECK.format(host_id), headers=headers)
    ids = [item["id"] for item in r.json()["items"]]
    assert ids[ids.index("root") + 1: ids.index("root") + 3] == ["helper:fake-a", "helper:fake-b"]
    assert "helper:fake-c" not in ids, "ohne Hilfsskript kein Punkt"
    items = {item["id"]: item for item in r.json()["items"]}
    assert (items["helper:fake-a"]["status"], items["helper:fake-a"]["label"]) == ("ok", "Hilfsskript für Modul fake-a")
    assert items["helper:fake-a"]["detail"] == "Version 2, nur root kann es ändern. Eigene sudo-Regel vorhanden."
    # Volle Regel gesetzt, Skript fehlt: ein Hinweis (gelb), kein Fehler, und nicht mehr „Alles in Ordnung“.
    assert items["helper:fake-b"]["status"] == "warn" and "Fehlt auf dem Server" in items["helper:fake-b"]["detail"]
    assert "Server neu einrichten" in items["helper:fake-b"]["hint"]
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_a_missing_helper_without_the_full_rule_is_only_skipped(client, modules, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = (1, "", "sudo: a password is required\n")
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(_missing(SHIELD), _missing(OTHER)), "")
    items = await _items(client, headers, host_id)
    for item_id in ("helper:fake-a", "helper:fake-b"):
        assert items[item_id]["status"] == "skipped" and "Noch nicht auf dem Server" in items[item_id]["detail"]


UNUSED = " Nodvard Deck ruft es nicht auf."


@pytest.mark.parametrize(
    ("answer", "status", "detail"),
    [
        (_present(SHIELD, version="1", sha="1" * 64), "warn",
         f"Veraltet: Version 1 auf dem Server, dieses Dashboard bringt Version 2 mit.{UNUSED}"),
        (_present(SHIELD, version="3", sha="3" * 64), "warn",
         f"Version 3 auf dem Server ist neuer als die dieses Dashboards (2).{UNUSED}"),
        (_present(SHIELD, sha="0" * 64), "fail", f"Version 2, aber mit anderem Inhalt als hier.{UNUSED}"),
        (_present(SHIELD, owner="bad"), "warn",
         ("Liegt auf dem Server, aber nicht nur root kann es ändern (Datei oder ein Ordner darüber). Wer es ändern kann, "
          "kommt über seine sudo-Regel an root.")),
        (_present(SHIELD, version="zwei", sha="0" * 64), "fail",
         f"Die Version lässt sich nicht lesen, der Inhalt ist unbekannt.{UNUSED}"),
        (_present(SHIELD, version="2; rm -rf /", sha="0" * 64), "fail",
         f"Die Version lässt sich nicht lesen, der Inhalt ist unbekannt.{UNUSED}"),
        (_present(SHIELD, sha="nicht-da"), "warn", f"Die Prüfsumme lässt sich auf dem Server nicht bilden (sha256sum).{UNUSED}"),
        ("ran:irgendwas\n", "skipped", "Keine verwertbare Antwort auf die Abfrage."),
    ],
    ids=["veraltet", "neuer", "anderer-inhalt", "besitzer", "version-unlesbar", "version-fremd", "ohne-pruefsumme",
         "keine-antwort"],
)
@pytest.mark.asyncio
async def test_the_item_says_what_is_wrong(client, modules, local_ssh_server, fake_probe, answer, status, detail):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = (0, "", "")
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(answer, _present(OTHER)), "")
    r = await client.post(CHECK.format(host_id), headers=headers)
    items = {item["id"]: item for item in r.json()["items"]}
    assert (items["helper:fake-a"]["status"], items["helper:fake-a"]["detail"]) == (status, detail)
    assert items["helper:fake-b"]["status"] == "ok"
    # Ein Fehler (verändertes Skript) lässt die ganze Prüfung durchfallen, eine Warnung nicht.
    assert r.json()["ok"] is (status != "fail")


@pytest.mark.asyncio
async def test_without_any_sudo_rule_the_item_says_so(client, modules, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = (1, "", "sudo: a password is required\n")
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(_present(SHIELD), _present(OTHER)), "")
    items = await _items(client, headers, host_id)
    assert items["helper:fake-a"]["detail"] == "Version 2, nur root kann es ändern. sudo erlaubt es noch nicht."


def test_lines_before_the_sudo_list_cannot_be_faked_by_it():
    """Was `sudo -l` ausgibt, zählt nur als Regel-Liste, nie als Antwort zu einem Skript; fremde Orte zählen nie."""
    out = _answer(
        _missing(SHIELD), "@@nd-path /tmp/fremd\n@@nd-version 9\n",
        sudo_list=f"@@nd-path {OTHER.install_path}\n@@nd-version 9\n    (root) NOPASSWD: {SHIELD.install_path}\n",
    )
    seen = parse_helper_check(out, [SHIELD, OTHER])
    assert set(seen) == {SHIELD.install_path}
    assert seen[SHIELD.install_path].present is False and seen[SHIELD.install_path].tight_rule is True


@pytest.mark.asyncio
async def test_root_needs_no_helper():
    state = host_check._State()
    host_check._step_helpers(state, "root", [SHIELD], None)  # für root keine Abfrage
    assert [(i.id, i.status, i.detail) for i in state.items] == [
        ("helper:fake-a", "skipped", "Angemeldet als root – kein Hilfsskript nötig.")
    ]


@pytest.mark.asyncio
async def test_setup_brings_the_helpers_of_the_modules_only_with_sudo(client, modules):
    headers = await _owner(client)
    host = (await client.post("/api/v1/hosts", json={"name": "pi", "address": "10.0.0.5"}, headers=headers)).json()
    generated = (await client.post(GENERATE.format(host["id"]), json={}, headers=headers)).json()
    url = f"/api/v1/hosts/{host['id']}/credentials/{generated['credential']['id']}/setup"

    body = (await client.get(url, params={"sudo": "true"}, headers=headers)).json()
    assert [(h["ext_id"], h["module"], h["path"], h["version"], h["sha256"]) for h in body["helpers"]] == [
        ("fake-a", "Modul fake-a", SHIELD.install_path, 2, SHIELD.sha256),
        ("fake-b", "Modul fake-b", OTHER.install_path, 1, OTHER.sha256),
    ]
    assert body["helpers"][0]["text"] == SHIELD.text
    assert "HS fake-a-root" in body["script"] and "HS fake-b-root" in body["script"]
    assert any("fake-a-root, fake-b-root" in n for n in body["notes"])

    plain = (await client.get(url, headers=headers)).json()
    assert plain["helpers"] == [] and "HS " not in plain["script"]


def _tight(*helpers: RootHelperFile) -> str:
    return "User testuser may run the following commands:\n" + "".join(
        f"    (root) NOPASSWD: {helper.install_path}\n" for helper in helpers
    )


NO_PASSWORDLESS_SUDO = (1, "", "sudo: a password is required\n")
ENOUGH = "sudo erlaubt nur die Hilfsskripte der Module, keine volle Regel. Das reicht."
NOT_ENOUGH = "sudo erlaubt nur einzelne Hilfsskripte. Hier ohne root: "


@pytest.mark.asyncio
async def test_helper_rules_alone_are_enough_when_every_module_has_one(client, helper_modules, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = NO_PASSWORDLESS_SUDO
    fake_probe[helper_check_command([SHIELD, OTHER])] = (
        0, _answer(_present(SHIELD), _present(OTHER), sudo_list=_tight(SHIELD, OTHER)), "",
    )
    r = await client.post(CHECK.format(host_id), headers=headers)
    items = {item["id"]: item for item in r.json()["items"]}
    assert (items["root"]["status"], items["root"]["detail"]) == ("ok", ENOUGH)
    assert items["helper:fake-a"]["detail"] == "Version 2, nur root kann es ändern. Eigene sudo-Regel vorhanden."
    assert items["helper:fake-b"]["status"] == "ok" and r.json()["ok"] is True


@pytest.mark.asyncio
async def test_helper_rules_are_not_enough_for_a_module_without_one(client, modules, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = NO_PASSWORDLESS_SUDO
    fake_probe[helper_check_command([SHIELD, OTHER])] = (
        0, _answer(_present(SHIELD), _present(OTHER), sudo_list=_tight(SHIELD, OTHER)), "",
    )
    root = (await _items(client, headers, host_id))["root"]
    assert (root["status"], root["detail"]) == ("warn", f"{NOT_ENOUGH}Module ohne Hilfsskript.")
    assert "Server neu einrichten" in root["hint"]


@pytest.mark.parametrize(
    ("first", "rules"),
    [
        (_present(SHIELD), ""),
        (_present(SHIELD, sha="0" * 64), _tight(SHIELD, OTHER)),
        (_present(SHIELD, owner="bad"), _tight(SHIELD, OTHER)),
        (_present(SHIELD, version="1", sha="1" * 64), _tight(SHIELD, OTHER)),
        (_present(SHIELD), _tight(OTHER) + f"    (root) NOPASSWD: /usr/bin/tee {SHIELD.install_path}\n"),
        (_present(SHIELD), _tight(OTHER) + f"    (root) PASSWD: {SHIELD.install_path}\n"),
        (_present(SHIELD), _tight(OTHER) + f"    (www-data) NOPASSWD: {SHIELD.install_path}\n"),
    ],
    ids=["ohne-regeln", "anderer-inhalt", "besitzer", "veraltet", "nur-als-argument", "mit-passwort", "anderer-benutzer"],
)
@pytest.mark.asyncio
async def test_without_the_full_rule_root_is_only_ok_when_every_script_is_ready(
    client, helper_modules, local_ssh_server, fake_probe, first, rules
):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = NO_PASSWORDLESS_SUDO
    fake_probe[helper_check_command([SHIELD, OTHER])] = (0, _answer(first, _present(OTHER), sudo_list=rules), "")
    root = (await _items(client, headers, host_id))["root"]
    assert root["status"] == "warn" and root["detail"] != ENOUGH


@pytest.mark.asyncio
async def test_a_missing_helper_next_to_helper_rules_means_no_root(client, helper_modules, local_ssh_server, fake_probe):
    _, port, *_ = local_ssh_server
    headers = await _owner(client)
    host_id = await _host_with_login(client, headers, port)
    fake_probe[host_check.SUDO_COMMAND] = NO_PASSWORDLESS_SUDO
    fake_probe[helper_check_command([SHIELD, OTHER])] = (
        0, _answer(_present(SHIELD), _missing(OTHER), sudo_list=_tight(SHIELD)), "",
    )
    items = await _items(client, headers, host_id)
    assert (items["root"]["status"], items["root"]["detail"]) == ("warn", f"{NOT_ENOUGH}Modul fake-b.")
    assert items["helper:fake-a"]["status"] == "ok"
    assert (items["helper:fake-b"]["status"], items["helper:fake-b"]["detail"]) == (
        "warn", "Fehlt auf dem Server. Ohne das Skript hat dieses Modul hier kein root.",
    )


@pytest.mark.asyncio
async def test_setup_gives_only_helper_rules_when_they_cover_every_module(client, helper_modules):
    headers = await _owner(client)
    host = (await client.post("/api/v1/hosts", json={"name": "pi", "address": "10.0.0.5"}, headers=headers)).json()
    generated = (await client.post(GENERATE.format(host["id"]), json={}, headers=headers)).json()
    url = f"/api/v1/hosts/{host['id']}/credentials/{generated['credential']['id']}/setup"

    narrow = (await client.get(url, params={"sudo": "true"}, headers=headers)).json()
    assert (narrow["sudo"], narrow["full_sudo"]) == (True, False)
    assert "NOPASSWD: ALL" not in narrow["script"] and "HS fake-a-root" in narrow["script"]
    assert not any("praktisch alles" in n for n in narrow["notes"])

    full = (await client.get(url, params={"sudo": "true", "full_sudo": "true"}, headers=headers)).json()
    assert (full["sudo"], full["full_sudo"]) == (True, True)
    assert "NOPASSWD: ALL" in full["script"] and "HS fake-a-root" in full["script"]

    plain = (await client.get(url, params={"full_sudo": "true"}, headers=headers)).json()
    assert (plain["sudo"], plain["full_sudo"]) == (False, False) and "sudoers" not in plain["script"]


@pytest.mark.asyncio
async def test_setup_keeps_the_full_rule_for_a_module_without_helper(client, modules):
    headers = await _owner(client)
    host = (await client.post("/api/v1/hosts", json={"name": "pi", "address": "10.0.0.5"}, headers=headers)).json()
    generated = (await client.post(GENERATE.format(host["id"]), json={}, headers=headers)).json()
    url = f"/api/v1/hosts/{host['id']}/credentials/{generated['credential']['id']}/setup"
    body = (await client.get(url, params={"sudo": "true"}, headers=headers)).json()
    assert body["full_sudo"] is True and "NOPASSWD: ALL" in body["script"] and "HS fake-c" not in body["script"]


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="braucht eine echte POSIX-Shell")
def test_the_query_runs_in_a_real_shell(tmp_path: Path):
    """Der Abfragebefehl selbst: Version und Prüfsumme kommen richtig an. Die Ablage im tmp-Ordner gehört nicht root
    (oder der Test läuft als root, der überall schreiben darf), also zählt sie nie als sicher."""
    folder = tmp_path / "lib"
    folder.mkdir()
    ran = tmp_path / "ran"
    # Ein Skript, das bei jedem Aufruf eine Spur hinterlässt: Die Abfrage darf es nicht ausführen, auch nicht `version`.
    text = f"#!/bin/sh\ntouch {ran}\nHELPER_VERSION=2\n[ \"${{1:-}}\" = version ] && echo 2\n"
    present = RootHelperFile(
        **{**SHIELD.__dict__, "install_path": str(folder / "fake-a-root"), "text": text,
           "sha256": hashlib.sha256(text.encode()).hexdigest()}
    )
    missing = RootHelperFile(**{**OTHER.__dict__, "install_path": str(folder / "fake-b-root")})
    Path(present.install_path).write_text(present.text)
    Path(present.install_path).chmod(0o755)
    done = subprocess.run(
        ["sh", "-c", helper_check_command([present, missing])], capture_output=True, text=True, timeout=30, check=False,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    seen = parse_helper_check(done.stdout, [present, missing])
    item = seen[present.install_path]
    assert (item.present, item.version, item.sha256, item.owner_ok) == (True, 2, present.sha256, False)
    assert seen[missing.install_path].present is False
    assert not ran.exists(), "die Abfrage hat das Skript ausgeführt"
