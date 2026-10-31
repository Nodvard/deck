"""Stacks übernehmen: Prüfung mit Gründen, Merkliste im Datenordner, Übernehmen und Loslassen ohne Änderung am Server."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, FakeExec, docker_on, ps, result, row  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _mods():
    from nodvard_deck_ext_docker import adopt, stacks

    return adopt, stacks


def _stack(**changes):
    _, stacks = _mods()
    return stacks.parse_members(ps(row(**changes)))[0]


async def _problems(stack, fake: FakeExec | None = None):
    adopt, stacks = _mods()
    fake = fake or FakeExec()
    ctx = SimpleNamespace(exec=fake)
    return await adopt.problems(ctx, stacks.DockerStacks(ctx), SimpleNamespace(id="h1"), stack)


# --- Merkliste -------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_list_remembers_folder_and_files(tmp_path):
    adopt, _ = _mods()
    store = adopt.AdoptedStacks(tmp_path)
    assert store.for_host("h1") == {}
    entry = await store.add("h1", _stack(env_files="/opt/stacks/shop/prod.env"))
    assert store.for_host("h1") == {"shop": entry}
    assert (entry.working_dir, entry.config_files, entry.env_files) == ("/opt/stacks/shop", ("/opt/stacks/shop/compose.yaml",), ("/opt/stacks/shop/prod.env",))
    assert store.for_host("h2") == {}
    assert await store.remove("h1", "shop") is True
    assert await store.remove("h1", "shop") is False
    assert json.loads((tmp_path / "stacks.json").read_text(encoding="utf-8")) == {"adopted": {}}


@pytest.mark.parametrize(
    "content",
    [
        "kein json",
        '{"adopted": []}',
        '{"adopted": {"h1": {"Shop": {"working_dir": "/opt/a", "config_files": ["/opt/a/c.yml"]}}}}',
        '{"adopted": {"h1": {"shop": {"working_dir": "/opt/a", "config_files": ["/opt/$(reboot).yml"]}}}}',
        '{"adopted": {"h1": {"shop": {"working_dir": "/opt/../etc", "config_files": ["/opt/a/c.yml"]}}}}',
        '{"adopted": {"h1": {"shop": {"working_dir": "/opt/a", "config_files": []}}}}',
        '{"adopted": {"h1": {"shop": "kaputt"}}}',
    ],
)
def test_a_broken_or_hand_edited_list_is_ignored(tmp_path, content):
    adopt, _ = _mods()
    (tmp_path / "stacks.json").write_text(content, encoding="utf-8")
    assert adopt.AdoptedStacks(tmp_path).for_host("h1") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["{halb geschrieben", '{"adopted": "x"}'])
async def test_a_broken_list_is_set_aside_not_overwritten(tmp_path, content):
    adopt, _ = _mods()
    (tmp_path / "stacks.json").write_text(content, encoding="utf-8")
    store = adopt.AdoptedStacks(tmp_path)
    await store.add("h1", _stack())
    [aside] = tmp_path.glob("stacks.json.kaputt-*")
    assert aside.read_text(encoding="utf-8") == content
    assert list(store.for_host("h1")) == ["shop"]


@pytest.mark.asyncio
async def test_removing_from_a_broken_list_sets_it_aside_too(tmp_path):
    adopt, _ = _mods()
    (tmp_path / "stacks.json").write_text("{halb", encoding="utf-8")
    assert await adopt.AdoptedStacks(tmp_path).remove("h1", "shop") is False
    assert len(list(tmp_path.glob("stacks.json.kaputt-*"))) == 1


@pytest.mark.asyncio
async def test_a_list_that_cannot_be_read_right_now_is_left_alone(tmp_path):
    adopt, _ = _mods()
    # Ein Ordner statt einer Datei: Lesen scheitert mit einem OSError, nicht an kaputtem JSON.
    (tmp_path / "stacks.json").mkdir()
    store = adopt.AdoptedStacks(tmp_path)
    assert store.for_host("h1") == {}
    with pytest.raises(adopt.StoreProblem):
        await store.add("h1", _stack())
    assert (tmp_path / "stacks.json").is_dir() and not list(tmp_path.glob("stacks.json.kaputt-*"))


def test_a_host_entry_that_is_not_a_map_is_skipped(tmp_path):
    adopt, _ = _mods()
    shop = {"working_dir": "/opt/a", "config_files": ["/opt/a/c.yml"]}
    (tmp_path / "stacks.json").write_text(json.dumps({"adopted": {"h1": "kaputt", "h2": ["shop"], "h3": {"shop": shop}}}), encoding="utf-8")
    store = adopt.AdoptedStacks(tmp_path)
    assert (store.for_host("h1"), store.for_host("h2")) == ({}, {})
    assert list(store.for_host("h3")) == ["shop"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["add", "remove"])
async def test_a_broken_list_that_cannot_be_set_aside_stays_as_it_is(tmp_path, monkeypatch, change):
    adopt, _ = _mods()
    path = tmp_path / "stacks.json"
    path.write_text("{halb", encoding="utf-8")

    def refuse(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(adopt.os, "replace", refuse)
    store = adopt.AdoptedStacks(tmp_path)
    with pytest.raises(adopt.StoreProblem) as raised:
        await (store.add("h1", _stack()) if change == "add" else store.remove("h1", "shop"))
    assert str(raised.value) == "Die kaputte Liste der übernommenen Stacks ließ sich nicht beiseitelegen."
    assert isinstance(raised.value.__cause__, PermissionError)
    assert path.read_text(encoding="utf-8") == "{halb" and [p.name for p in tmp_path.iterdir()] == ["stacks.json"]


@pytest.mark.asyncio
async def test_a_stack_without_files_is_not_added(tmp_path):
    adopt, _ = _mods()
    stack = _stack(config_files="")
    assert stack.files is None
    with pytest.raises(ValueError, match="^Stack ohne lesbare Dateien: 'shop'$"):
        await adopt.AdoptedStacks(tmp_path).add("h1", stack)
    assert not (tmp_path / "stacks.json").exists()


def test_a_moved_stack_no_longer_counts_as_adopted():
    adopt, _ = _mods()
    entry = adopt.Adopted("/opt/stacks/shop", ("/opt/stacks/shop/compose.yaml",), (), "2026-10-16T00:00:00+00:00")
    assert entry.note(_stack()) is None
    assert entry.note(entry.stack("shop")) is None
    moved = entry.note(_stack(working_dir="/srv/shop", config_files="/srv/shop/compose.yaml"))
    assert moved.startswith("Übernommen aus /opt/stacks/shop, der Stack läuft inzwischen aber mit anderen Dateien.")


# --- Prüfung ---------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_plain_compose_stack_can_be_adopted():
    fake = FakeExec()
    assert await _problems(_stack(), fake) == []
    assert fake.calls[-1].endswith("config --quiet 2>&1'"), "zuletzt prüft Compose selbst die Datei"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "start"),
    [
        ({"working_dir": "/data/compose/3", "config_files": "/data/compose/3/docker-compose.yml"}, "Diesen Stack verwaltet Portainer"),
        ({"config_files": ""}, "Docker nennt für diesen Stack keine Compose-Datei"),
        ({"version": "1.29.2"}, "Angelegt mit dem alten docker-compose (Version 1)"),
        ({"image": "ghcr.io/nodvard/deck:0.9.0"}, "Das ist Nodvard Deck selbst"),
        ({"image": "nodvard-deck:latest"}, "Das ist Nodvard Deck selbst"),
        ({"image": "ghcr.io/nodvard/dock:0.11.0"}, "Das ist Nodvard Deck selbst"),
        # Was ohnehin nie übernommen werden kann, steht vor allem, was sich beheben ließe.
        ({"image": "nodvard-deck:latest", "version": "1.29.2"}, "Das ist Nodvard Deck selbst"),
    ],
)
async def test_reasons_against_adopting(changes, start):
    reasons = await _problems(_stack(**changes))
    assert len(reasons) == 1 and reasons[0].startswith(start), reasons


@pytest.mark.asyncio
async def test_the_dashboards_own_container_is_never_adopted(monkeypatch):
    from nodvard_deck_ext_docker import capabilities

    monkeypatch.setattr(capabilities, "_own_container_id", lambda: "8408834a78d1")
    assert (await _problems(_stack()))[0].startswith("Das ist Nodvard Deck selbst")


@pytest.mark.asyncio
async def test_files_must_be_readable_compose_files():
    _, stacks = _mods()
    fake = FakeExec()
    reasons = await _problems(_stack(config_files="/opt/stacks/shop/compose.yaml,/opt/stacks/shop/fehlt.yml,/opt/stacks/shop/compose.override"), fake)
    assert reasons == [
        "/opt/stacks/shop/fehlt.yml: Die Datei gibt es nicht mehr.",
        "/opt/stacks/shop/compose.override: Nodvard Deck liest nur Compose-Dateien mit der Endung .yml oder .yaml.",
    ]
    assert not any("config --quiet" in c for c in fake.calls), "Compose läuft erst, wenn alle Dateien lesbar sind"
    fake.files["/opt/stacks/shop/compose.yaml"] = b"#" * (stacks.MAX_FILE_BYTES + 1)
    assert await _problems(_stack(), fake) == [f"/opt/stacks/shop/compose.yaml: {stacks.TOO_BIG}"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output", "reason"),
    [
        (
            'time="2026-10-15T22:53:19Z" level=warning msg="The \\"TAG\\" variable is not set."\n'
            'service "app" refers to undefined volume fehlt: invalid compose project\n',
            'Docker Compose kann die Datei nicht lesen: service "app" refers to undefined volume fehlt: invalid compose project',
        ),
        ("docker: 'compose' is not a docker command.\nSee 'docker --help'\n", "Auf dem Server fehlt Docker Compose v2, also der Befehl „docker compose“."),
    ],
)
async def test_compose_itself_must_read_the_file(output, reason):
    fake = FakeExec()
    fake.validate = result(output, code=1)
    assert await _problems(_stack(), fake) == [reason]


class _SftpDown:
    """Ein SFTP-Kanal, der sich nicht öffnen lässt."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def __aenter__(self):
        raise self._error

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (OSError("Verbindung getrennt"), "Dateien nicht lesbar: Verbindung getrennt"),
        (RuntimeError(), "Dateien nicht lesbar: RuntimeError"),
    ],
)
async def test_files_that_cannot_be_opened_are_a_stack_problem(error, reason):
    _, stacks = _mods()
    fake = FakeExec()
    fake.sftp = lambda host: _SftpDown(error)
    with pytest.raises(stacks.StackProblem) as raised:
        await _problems(_stack(), fake)
    assert str(raised.value) == reason and raised.value.__cause__ is error
    assert not any("config --quiet" in c for c in fake.calls), "Compose läuft erst, wenn die Dateien gelesen sind"


VALIDATE_LOG = """#!/bin/sh
printf '%s|%s\\n' "$PWD" "$*" >> "$LOG"
"""


@pytest.mark.skipif(shutil.which("sh") is None or sys.platform == "win32", reason="braucht eine POSIX-Shell")
def test_the_check_runs_in_the_stack_folder(tmp_path):
    adopt, _ = _mods()
    folder = tmp_path / "my stacks" / "shop"
    folder.mkdir(parents=True)
    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text(VALIDATE_LOG, encoding="utf-8")
    fake.chmod(0o755)
    stack = _stack(working_dir=str(folder), config_files=f"{folder}/compose.yaml")
    env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}", "LOG": str(tmp_path / "log")}
    done = subprocess.run(["sh", "-c", adopt.cmd_validate(stack)], env=env, cwd="/", capture_output=True, text=True, check=False, timeout=10)
    assert done.returncode == 0
    assert (tmp_path / "log").read_text(encoding="utf-8") == (
        f"{folder}|compose --ansi never -p shop --project-directory {folder} -f {folder}/compose.yaml config --quiet\n"
    )


# --- Liste mit übernommenen Stacks -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_adopted_stack_stays_listed_without_containers(tmp_path):
    adopt, stacks = _mods()
    store = adopt.AdoptedStacks(tmp_path)
    fake = FakeExec()
    await store.add("h1", _stack())
    await store.add("h1", _stack(project="leer", working_dir="/opt/stacks/leer", config_files="/opt/stacks/leer/compose.yaml"))
    ctx = SimpleNamespace(exec=fake)
    out = await stacks.DockerStacks(ctx, store).overview(SimpleNamespace(id="h1"))
    by_name = {s["project"]: s for s in out["stacks"]}
    assert list(by_name) == ["leer", "media", "prof", "shop", "warn", "weg"]
    leer = by_name["leer"]
    assert (leer["adopted"], leer["state"], leer["total"], leer["source"]) == (True, "stopped", 0, "compose")
    # Ohne Container gäbe der Vergleich nur „alles neu in der Datei“: keine Angabe statt einer falschen.
    assert (leer["changed"], leer["changes"], leer["check_error"]) == (None, None, None)
    assert by_name["shop"]["adopted"] is True and by_name["media"]["adopted"] is False
    assert not any("-p leer" in c for c in fake.calls if c.startswith("sh -c"))


@pytest.mark.asyncio
async def test_a_moved_adopted_stack_is_flagged(tmp_path):
    adopt, stacks = _mods()
    store = adopt.AdoptedStacks(tmp_path)
    await store.add("h1", _stack(working_dir="/srv/shop", config_files="/srv/shop/compose.yaml"))
    ctx = SimpleNamespace(exec=FakeExec())
    shop = next(s for s in (await stacks.DockerStacks(ctx, store).overview(SimpleNamespace(id="h1")))["stacks"] if s["project"] == "shop")
    assert shop["adopted"] is False and shop["adoption_note"].startswith("Übernommen aus /srv/shop")


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


SHOWN = {"working_dir": "/opt/stacks/shop", "config_files": ["/opt/stacks/shop/compose.yaml"]}
FOLDER_ONLY = "/opt/stacks/shop"


@pytest.mark.asyncio
async def test_an_unreadable_list_is_a_503_and_not_overwritten(client, docker_on):
    (docker_on.data_dir / "stacks.json").mkdir(parents=True)
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    refused = await client.post(url, json=SHOWN, headers=docker_on.owner)
    assert refused.status_code == 503 and "später noch einmal" in refused.json()["detail"]
    assert (await client.delete(url, headers=docker_on.owner)).status_code == 503


@pytest.mark.asyncio
async def test_a_list_that_cannot_be_written_is_a_503(client, docker_on, monkeypatch):
    from nodvard_deck_ext_docker import adopt

    def full(path, text):
        raise OSError(28, "No space left on device")

    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    working = adopt._write_atomic
    monkeypatch.setattr(adopt, "_write_atomic", full)
    refused = await client.post(url, json=SHOWN, headers=docker_on.owner)
    assert refused.status_code == 503
    assert refused.json()["detail"].startswith("Die Liste der übernommenen Stacks ließ sich nicht speichern (No space left on device).")
    monkeypatch.setattr(adopt, "_write_atomic", working)
    assert (await client.post(url, json=SHOWN, headers=docker_on.owner)).status_code == 200
    monkeypatch.setattr(adopt, "_write_atomic", full)
    assert (await client.delete(url, headers=docker_on.owner)).status_code == 503


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="braucht Unix-Rechte; root darf trotz Rechten schreiben")
async def test_a_data_folder_without_write_access_is_a_503(tmp_path):
    adopt, _ = _mods()
    folder = tmp_path / "daten"
    folder.mkdir()
    store = adopt.AdoptedStacks(folder)
    folder.chmod(0o500)
    try:
        with pytest.raises(adopt.StoreProblem, match="ließ sich nicht speichern"):
            await store.add("h1", _stack())
    finally:
        folder.chmod(0o700)


@pytest.mark.asyncio
async def test_check_adopt_and_release_through_the_core(client, db_session, docker_on):
    from nodvard_deck.models import AuditEntry
    from sqlalchemy import select

    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    check = await client.get(url, headers=docker_on.owner)
    assert check.status_code == 200, check.text
    assert check.json() == {
        "project": "shop", "ok": True, "reasons": [], "working_dir": "/opt/stacks/shop",
        "config_files": ["/opt/stacks/shop/compose.yaml"], "env_files": [], "adopted": False,
    }

    adopted = await client.post(url, json=SHOWN, headers=docker_on.owner)
    assert adopted.status_code == 200, adopted.text
    stored = json.loads((docker_on.data_dir / "stacks.json").read_text(encoding="utf-8"))
    assert stored["adopted"][docker_on.host]["shop"]["working_dir"] == "/opt/stacks/shop"
    listed = (await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks", headers=docker_on.owner)).json()
    assert next(s for s in listed["stacks"] if s["project"] == "shop")["adopted"] is True
    assert not any(c.startswith(("docker compose up", "docker rm", "docker stop")) for c in docker_on.exec.calls), "am Server ändert sich nichts"

    released = await client.delete(url, headers=docker_on.owner)
    assert released.status_code == 200 and released.json() == {"project": "shop", "adopted": False}
    assert (await client.delete(url, headers=docker_on.owner)).status_code == 404

    entries = (await db_session.execute(select(AuditEntry).where(AuditEntry.target_type == "stack").order_by(AuditEntry.ts))).scalars().all()
    assert [(e.action, e.target_id, e.outcome) for e in entries] == [
        ("docker.stack_adopt", f"{docker_on.host}:shop", "success"), ("docker.stack_release", f"{docker_on.host}:shop", "success"),
    ]


DAEMON_DOWN = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_a_server_that_cannot_list_its_stacks_is_a_502(client, docker_on, method):
    docker_on.exec.list = result(stderr=f"{DAEMON_DOWN}\n", code=1)
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    refused = await client.request(method, url, json=SHOWN if method == "POST" else None, headers=docker_on.owner)
    assert (refused.status_code, refused.json()["detail"]) == (502, DAEMON_DOWN)
    assert not (docker_on.data_dir / "stacks.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_files_that_cannot_be_read_are_a_502(client, docker_on, monkeypatch, method):
    monkeypatch.setattr(docker_on.handle, "sftp", lambda host: _SftpDown(OSError("Verbindung getrennt")))
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    refused = await client.request(method, url, json=SHOWN if method == "POST" else None, headers=docker_on.owner)
    assert (refused.status_code, refused.json()["detail"]) == (502, "Dateien nicht lesbar: Verbindung getrennt")
    assert not (docker_on.data_dir / "stacks.json").exists()
    assert not any("config --quiet" in c for c in docker_on.exec.calls)


@pytest.mark.asyncio
async def test_a_failing_audit_does_not_undo_the_adoption(client, docker_on, monkeypatch, caplog):
    from nodvard_deck.ext.runtime import get_extension_runtime

    actions = []

    async def broken(**kwargs):
        actions.append(kwargs["action"])
        raise RuntimeError("Protokoll nicht erreichbar")

    monkeypatch.setattr(get_extension_runtime().loaded["docker"].ctx.audit, "log", broken)
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    stored = docker_on.data_dir / "stacks.json"
    with caplog.at_level(logging.ERROR):
        adopted = await client.post(url, json=SHOWN, headers=docker_on.owner)
        assert adopted.status_code == 200, adopted.text
        assert list(json.loads(stored.read_text(encoding="utf-8"))["adopted"][docker_on.host]) == ["shop"]
        released = await client.delete(url, headers=docker_on.owner)
        assert (released.status_code, released.json()) == (200, {"project": "shop", "adopted": False})
        assert json.loads(stored.read_text(encoding="utf-8")) == {"adopted": {}}
    assert actions == ["docker.stack_adopt", "docker.stack_release"]
    logged = [(r.getMessage(), type(r.exc_info[1])) for r in caplog.records if "docker_stack_audit_failed" in r.getMessage()]
    assert logged == [
        ("docker_stack_audit_failed action=docker.stack_adopt", RuntimeError),
        ("docker_stack_audit_failed action=docker.stack_release", RuntimeError),
    ]


@pytest.mark.asyncio
async def test_after_compose_down_the_file_of_an_adopted_stack_stays_readable(client, docker_on):
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks"
    assert (await client.post(f"{base}/shop/adopt", json=SHOWN, headers=docker_on.owner)).status_code == 200
    docker_on.exec.rows = [r for r in docker_on.exec.rows if r["project"] != "shop"]
    listed = (await client.get(base, headers=docker_on.owner)).json()
    shop = next(s for s in listed["stacks"] if s["project"] == "shop")
    assert (shop["adopted"], shop["total"], shop["state"]) == (True, 0, "stopped")
    file = (await client.get(f"{base}/shop/file", headers=docker_on.owner)).json()
    assert file["files"][0]["content"].startswith("services:")
    assert (await client.get(f"{base}/media-weg/file", headers=docker_on.owner)).status_code == 404


@pytest.mark.asyncio
async def test_a_link_to_a_file_without_compose_ending_is_not_shown(client, docker_on):
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks"
    docker_on.exec.links["/opt/stacks/shop/compose.yaml"] = "/home/deck/.ssh/id_ed25519"
    file = (await client.get(f"{base}/shop/file", headers=docker_on.owner)).json()["files"][0]
    assert file["content"] is None and file["error"].startswith("Die Datei verweist auf eine Datei ohne Endung")
    docker_on.exec.links["/opt/stacks/shop/compose.yaml"] = "/srv/gemeinsam/shop.yml"
    docker_on.exec.files["/opt/stacks/shop/compose.yaml"] = b"services: {}\n"
    assert (await client.get(f"{base}/shop/file", headers=docker_on.owner)).json()["files"][0]["content"] == "services: {}\n"


@pytest.mark.asyncio
async def test_a_stack_with_a_reason_is_not_adopted(client, docker_on):
    docker_on.exec.validate = result("yaml: line 3: did not find expected key\n", code=15)
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    check = (await client.get(url, headers=docker_on.owner)).json()
    assert (check["ok"], check["reasons"]) == (False, ["Docker Compose kann die Datei nicht lesen: yaml: line 3: did not find expected key"])
    refused = await client.post(url, json=SHOWN, headers=docker_on.owner)
    assert refused.status_code == 409 and refused.json()["detail"] == check["reasons"][0]
    assert not (docker_on.data_dir / "stacks.json").exists()


@pytest.mark.asyncio
async def test_only_the_paths_the_check_showed_are_adopted(client, docker_on):
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    other = {**SHOWN, "config_files": ["/opt/stacks/shop/compose.yaml", "/opt/stacks/shop/override.yml"]}
    for changed in (other, {**SHOWN, "working_dir": "/srv/shop"}, {**SHOWN, "env_files": ["/opt/stacks/shop/prod.env"]}):
        moved = await client.post(url, json=changed, headers=docker_on.owner)
        assert moved.status_code == 409 and "Bitte die Prüfung neu laden" in moved.json()["detail"], changed
    for odd in (None, {"working_dir": FOLDER_ONLY}, {**SHOWN, "noch": "etwas"}):
        assert (await client.post(url, json=odd, headers=docker_on.owner)).status_code == 422, odd
    assert not (docker_on.data_dir / "stacks.json").exists()
    assert not any("config --quiet" in c for c in docker_on.exec.calls), "ohne passende Pfade wird gar nicht erst geprüft"


@pytest.mark.asyncio
async def test_unknown_odd_and_foreign(client, docker_on):
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks"
    assert (await client.get(f"{base}/fehlt/adopt", headers=docker_on.owner)).status_code == 404
    assert (await client.post(f"{base}/Shop%3Bid/adopt", json=SHOWN, headers=docker_on.owner)).status_code == 400
    assert (await client.delete(f"{base}/Shop%3Bid/adopt", headers=docker_on.owner)).status_code == 400
    assert (await client.post(f"/api/v1/ext/docker/hosts/{docker_on.other}/stacks/shop/adopt", json=SHOWN, headers=docker_on.owner)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
async def test_adopting_needs_a_login_and_hosts_execute(client, docker_on, method):
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    body = SHOWN if method == "POST" else None
    assert (await client.request(method, url, json=body)).status_code == 401
    assert (await client.request(method, url, json=body, headers=docker_on.viewer)).status_code == 403
    assert docker_on.exec.calls == []
