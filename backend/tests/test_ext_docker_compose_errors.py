"""Fehlermeldungen von Compose zu einer kaputten `.env` verlassen den Server ohne den zitierten Inhalt."""

from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace

import pytest
from docker_stack_fakes import (  # noqa: F401 - Fixtures
    EXT_DIR,
    HASHES,
    MARKER,
    docker_on,
    result,
)

SECRET = "Geh31m-Wert-42"
# Ein Stück nach einem einzelnen `$` in einem Wert, das Compose für einen Variablennamen hält.
REST = "sw0rdGeh31m"
# Echte Ausgaben von Docker Compose 5.6, nur Pfad und Wert ersetzt.
UNTERMINATED = f'failed to read /opt/stacks/shop/stack.env: line 2: unterminated quoted value "{SECRET}\nNOCH={SECRET}-2\n'
BAD_NAME = f'failed to read /opt/stacks/shop/.env: line 3: unexpected character "\\"" in variable name "{SECRET}\\""\n'
SPACE = "failed to read /opt/stacks/shop/.env: line 1: key cannot contain a space\n"
TEMPLATE = f'failed to read /opt/stacks/shop/.env: Invalid template: "${{C:-{SECRET}}}${{"\n'
REQUIRED = f"failed to read /opt/stacks/shop/.env: required variable C is missing a value: {SECRET}\n"
UNSET = f'time="2026-10-17T07:00:00Z" level=warning msg="The \\"{REST}\\" variable is not set. Defaulting to a blank string."\n'
SHOWN = {"working_dir": "/opt/stacks/shop", "config_files": ["/opt/stacks/shop/compose.yaml"]}
HIDDEN = "Den Inhalt zeigt Nodvard Deck nicht, er kann Passwörter enthalten."


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _scrub(text):
    from nodvard_deck_ext_docker.compose_errors import scrub_compose_output

    return scrub_compose_output(text)


@pytest.mark.parametrize(
    ("output", "path", "reason"),
    [
        (UNTERMINATED, "/opt/stacks/shop/stack.env", "Ein Wert in Anführungszeichen wird nicht geschlossen"),
        (BAD_NAME, "/opt/stacks/shop/.env", "Ein Name enthält ein ungültiges Zeichen (Zeile 3)"),
        (SPACE, "/opt/stacks/shop/.env", "Ein Name enthält ein Leerzeichen (Zeile 1)"),
        (TEMPLATE, "/opt/stacks/shop/.env", "Ein Wert enthält ein unvollständiges ${…}; ein $, das im Wert stehen soll, als $$ schreiben"),
        (REQUIRED, "/opt/stacks/shop/.env", "Eine Pflicht-Variable hat keinen Wert"),
    ],
)
def test_only_file_and_a_fixed_reason_stay(output, path, reason):
    assert _scrub(output) == f"Compose kann {path} nicht lesen: {reason}. {HIDDEN}\n"


def test_an_unknown_reason_is_not_shown_at_all():
    scrubbed = _scrub(f'failed to load /x/.env: line 4: neu "{SECRET}"\n{SECRET}\n')
    assert scrubbed == "Compose kann /x/.env nicht lesen. Die Meldung zeigt Nodvard Deck nicht, sie kann Passwörter enthalten.\n"


def test_a_file_that_cannot_be_opened_keeps_what_follows():
    output = "failed to read /x/.env: open /x/.env: permission denied\nweiter\n"
    assert _scrub(output) == "Compose kann /x/.env nicht lesen: Nodvard Deck darf die Datei nicht lesen.\nweiter\n"
    broken = "failed to read /var/lib/x/config.v2.json: input/output error\n"
    assert _scrub(broken) == "Compose kann /var/lib/x/config.v2.json nicht lesen: Der Datenträger meldet einen Lesefehler.\n"


def test_text_before_the_error_and_marks_after_it_stay():
    scrubbed = _scrub("Container shop-web-1  Creating\n" + UNTERMINATED + f"@@{SECRET}\n@@rc=1\n@@step=up\n")
    assert SECRET not in scrubbed
    assert scrubbed.startswith("Container shop-web-1  Creating\nCompose kann /opt/stacks/shop/stack.env nicht lesen")
    assert scrubbed.endswith("\n@@rc=1\n@@step=up\n")


def test_a_reason_without_the_usual_start_is_hidden_too():
    assert SECRET not in _scrub(f'level=fatal msg="unterminated quoted value \\"{SECRET}"\n{SECRET}\n')
    assert SECRET not in _scrub(f'level=fatal msg="unterminated quoted value \\"failed to read {SECRET}: x"\n')


def test_a_variable_that_is_not_set_loses_its_name():
    long_rest = REST * 30
    scrubbed = _scrub(
        UNSET + UNSET.replace(REST, "TAG") + UNSET.replace(REST, long_rest) + f"WARN[0000] The {REST} variable is not set.\n"
        + "Container shop-web-1  Started\n"
    )
    assert REST not in scrubbed and "TAG" not in scrubbed
    assert scrubbed.count("Eine Variable ist nicht gesetzt") == 1
    assert scrubbed.startswith('time="2026-10-17T07:00:00Z" level=warning msg="Eine Variable')
    assert scrubbed.endswith("\nContainer shop-web-1  Started\n")


def test_a_warning_in_the_same_line_as_the_error_loses_its_name():
    scrubbed = _scrub(UNSET.rstrip("\n") + " " + BAD_NAME)
    assert REST not in scrubbed and SECRET not in scrubbed and "ungültiges Zeichen" in scrubbed


def test_a_lone_carriage_return_still_ends_the_line():
    assert _scrub(UNTERMINATED.replace("\n", "\r", 1) + "@@rc=1\n").endswith(f"{HIDDEN}\n@@rc=1\n")


def test_a_very_long_line_is_quick():
    started = time.monotonic()
    _scrub("failed to read x " * 8000 + "\n" + 'The \\"' * 50000 + "\n")
    assert time.monotonic() - started < 1


def test_other_output_stays_as_it_is():
    plain = (
        "Container shop-web-1  Started\nError response from daemon: pull access denied\n"
        "yaml: line 3: mapping values are not allowed in this context\n"
        'service "app" refers to undefined volume fehlt: invalid compose project\n'
        "env file /opt/stacks/shop/.env not found: stat /opt/stacks/shop/.env: no such file or directory\n"
        "error reading from server: EOF\n"
        "failed to load compose file /opt/stacks/shop/compose.yaml: yaml: line 2: did not find expected key\n"
        "Error response from daemon: failed to read from registry: unauthorized\n"
    )
    assert _scrub(plain) == plain and _scrub("") == ""


def test_a_failed_config_check_before_an_image_update():
    from nodvard_deck_ext_docker import image_apply as ia

    target = ia.ComposeTarget(
        container="shop-web-1", container_id="a" * 64, image="busybox:1.37", image_id="sha256:" + "b" * 64, project="shop",
        service="web", working_dir="/opt/stacks/shop", config_files=("/opt/stacks/shop/compose.yaml",),
        env_files=("/opt/stacks/shop/.env",), config_hash=None,
    )
    why = ia.check_config("", TEMPLATE, 1, target).why
    assert SECRET not in why and "unvollständiges" in why
    why = ia.check_config("", UNSET, 0, target).why
    assert REST not in why and "eine Variable ohne Wert" in why
    why = ia.check_config("", UNSET.replace(REST, REST * 30), 0, target).why
    assert REST not in why and "eine Variable ohne Wert" in why
    why = ia.check_config("", UNSET + "Error: kaputt\n", 1, target).why
    assert why.endswith("meldet einen Fehler: Error: kaputt")


def test_the_reason_of_any_compose_run():
    from nodvard_deck_ext_docker.stack_actions import failure_line

    reason = failure_line("\x1b[33m" + UNSET + "\x1b[0m" + BAD_NAME)
    assert reason.startswith("Compose kann /opt/stacks/shop/.env nicht lesen") and SECRET not in reason


def test_the_check_in_the_editor():
    from nodvard_deck_ext_docker.editor import parse_check

    checked = parse_check(1, "", UNSET + TEMPLATE, "compose.yaml")
    assert "unvollständiges" in checked.problem and SECRET not in checked.problem and REST not in checked.problem


def test_the_log_of_an_update_run():
    from nodvard_deck_ext_docker import detached as dt

    running = dt.parse_poll(_scrub(f"@@running\n@@step=up\n@@tail\n{UNTERMINATED}"))
    assert running.state == "running" and SECRET not in running.progress
    done = dt.parse_poll(_scrub(f"@@rc=1\n@@step=up\n@@tail\nCreating web\n{UNTERMINATED}"))
    assert (done.state, done.rc) == ("done", 1) and SECRET not in done.output and "Creating web" in done.output


@pytest.mark.asyncio
async def test_a_stack_restart():
    import nodvard_deck_ext_docker.capabilities as caps
    from nodvard_sdk import ActionRequest, Actor

    async def run(host, command, *, timeout_s=60):
        if command.startswith("docker compose"):
            return result(stderr=UNSET + REQUIRED, code=1)
        return result("")

    async def get(host_id):
        return SimpleNamespace(id=host_id, name="h1", address="192.0.2.1")

    ctx = SimpleNamespace(exec=SimpleNamespace(run=run), hosts=SimpleNamespace(get=get))
    req = ActionRequest(action_type="docker.stack_restart", payload={"project": "shop"}, host_ref="h1",
                        proposed_by=Actor.user("u1", "owner1"), reason="Test")
    done = await caps.DockerMaintenanceExecutor(ctx).execute(req)
    assert done.success is False and "Pflicht-Variable" in done.error
    assert SECRET not in done.error and REST not in done.error


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


def _broken_hashes() -> str:
    head, rest = HASHES.split(f"#{MARKER} hashes shop\n", 1)
    _, tail = rest.split(f"#{MARKER} rc 0\n", 1)
    return f"{head}#{MARKER} hashes shop\n{UNTERMINATED}#{MARKER} rc 1\n{tail}"


@pytest.mark.asyncio
async def test_the_value_never_leaves_the_server(client, docker_on, caplog):
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks"
    seen: list[str] = []

    docker_on.exec.hashes = result(_broken_hashes())
    listed = await client.get(base, headers=docker_on.owner)
    shop = next(s for s in listed.json()["stacks"] if s["project"] == "shop")
    assert "nicht geschlossen" in json.dumps(shop["check_error"], ensure_ascii=False)
    seen.append(listed.text)

    docker_on.exec.hashes = result(HASHES)
    docker_on.exec.validate = result(UNSET + TEMPLATE, code=1)
    checked = await client.get(f"{base}/shop/adopt", headers=docker_on.owner)
    assert checked.json()["ok"] is False and "unvollständiges" in checked.json()["reasons"][0]
    seen.append(checked.text)

    docker_on.exec.validate = result()
    assert (await client.post(f"{base}/shop/adopt", json=SHOWN, headers=docker_on.owner)).status_code == 200
    docker_on.exec.verb = result(stderr=UNSET + "Container shop-web-1  Creating\n" + BAD_NAME, code=1)
    proposed = await client.post(
        f"/api/v1/hosts/{docker_on.host}/actions/docker.stack_up", json={"payload": {"project": "shop"}, "reason": "Test"},
        headers=docker_on.owner,
    )
    approved = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=docker_on.owner)
    assert approved.json()["status"] == "failed", approved.json()
    action = await client.get(f"/api/v1/actions/{proposed.json()['id']}", headers=docker_on.owner)
    assert "ungültiges Zeichen (Zeile 3)" in action.text
    audit = await client.get("/api/v1/audit", headers=docker_on.owner)
    seen += [approved.text, action.text, audit.text, caplog.text]

    for text in seen:
        assert SECRET not in text and SECRET.replace("-", "\\u002d") not in text and REST not in text
