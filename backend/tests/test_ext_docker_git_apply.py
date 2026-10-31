from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from docker_stack_fakes import (  # noqa: F401 - Fixtures
    EXT_DIR,
    FakeSftp,
    docker_on,
    real_sftp,
    result,
)
from test_ext_docker_git import COMPOSE, _adopted, _commit, _git, _in_process, _local


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def _running(request, monkeypatch, _ext_docker_on_path):
    """Nach `up` laufen die Dienste; erst nach dem Einschalten ersetzt, das lädt das Modul neu."""
    import importlib

    if "docker_on" in request.fixturenames:
        request.getfixturevalue("docker_on")
    ac = importlib.import_module("nodvard_deck_ext_docker.after_check")
    image_apply = importlib.import_module("nodvard_deck_ext_docker.image_apply")

    async def check_running(ctx, host, refs, **_):
        return image_apply.Verdict(ok=True, text="läuft stabil"), None

    monkeypatch.setattr(ac, "check_running", check_running)


def _ga():
    from nodvard_deck_ext_docker import git_apply

    return git_apply


FOLDER = "/opt/stacks/shop"
COMPOSE_PATH = f"{FOLDER}/compose.yaml"
NEW = COMPOSE + b"    restart: always\n"
URL = "https://git.example.org/team/stacks"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _source(client, docker_on, tmp_path, entries) -> tuple[str, str]:
    url = await _adopted(client, docker_on)
    commit = _commit(tmp_path / "origin", {"shop": entries})
    _git(docker_on).run_fetch = _in_process(_local(tmp_path / "origin"))
    assert (await client.put(url, headers=docker_on.owner, json={"url": URL, "path": "shop/compose.yaml"})).status_code == 200
    return url, commit


async def _approve(client, docker_on, proposed) -> dict:
    assert proposed.status_code == 202, proposed.text
    approved = await client.post(f"/api/v1/actions/{proposed.json()['action_id']}/approve", headers=docker_on.owner)
    return approved.json()


async def _apply(client, docker_on, url, commit) -> dict:
    return await _approve(client, docker_on, await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit}))


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_through_the_gate_and_one_step_back(client, docker_on, tmp_path):
    old = docker_on.exec.files[COMPOSE_PATH]
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "conf": {"app.ini": b"a=1\n"}, "start.sh": (0o100755, b"#!/bin/sh\n")})
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json() == {"applied": None, "previous": None}

    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert proposed.status_code == 202, proposed.text
    assert (proposed.json()["status"], proposed.json()["risk"]) == ("proposed", "high")
    assert docker_on.exec.files[COMPOSE_PATH] == old, "vor der Freigabe ändert sich nichts"
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).text
    assert f"Stand {commit[:7]} aus {URL} (main)" in action and "2 neu, 1 geändert, 0 entfernt" in action and "restart: always" not in action

    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "succeeded", approved
    files, sftp = docker_on.exec.files, docker_on.exec.sftp_server
    assert (files[COMPOSE_PATH], files[f"{FOLDER}/conf/app.ini"], files[f"{FOLDER}/start.sh"]) == (NEW, b"a=1\n", b"#!/bin/sh\n")
    assert (sftp.modes[f"{FOLDER}/conf/app.ini"] & 0o777, sftp.modes[f"{FOLDER}/start.sh"] & 0o777, sftp.dir_modes[f"{FOLDER}/conf"]) == (0o644, 0o755, 0o755)
    assert not any(".nodvard-git-" in path for path in files)
    called = [argv for argv in docker_on.exec.argv_calls if "compose" in argv]
    checks = [i for i, argv in enumerate(called) if argv[-3:] == ["config", "--format", "json"]]
    ups = [i for i, argv in enumerate(called) if "up" in argv]
    assert checks and ups and checks[-1] < ups[-1], "erst prüfen, dann anwenden"
    assert "-" not in called[checks[-1]] and docker_on.exec.stdin[-1] is None, "geprüft wird, was auf der Platte liegt"
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    assert state["applied"]["commit"] == commit and state["applied"]["files"] == 3
    assert state["previous"]["commit"] == commit and state["previous"]["commit_before"] is None

    back = await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": state["previous"]["id"]}))
    assert back["status"] == "succeeded", back
    assert files[COMPOSE_PATH] == old and f"{FOLDER}/conf/app.ini" not in files and f"{FOLDER}/start.sh" not in files
    assert f"{FOLDER}/conf" not in sftp.dirs, "der Ordner, den das Einspielen angelegt hat, geht leer wieder weg"
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json() == {"applied": None, "previous": None}


@pytest.mark.asyncio
async def test_only_files_deck_wrote_and_nobody_changed_are_removed(client, docker_on, tmp_path):
    url, first = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "a.txt": b"a\n", "b.txt": b"b\n"})
    docker_on.exec.files[f"{FOLDER}/eigen.txt"] = b"vom Nutzer\n"
    assert (await _apply(client, docker_on, url, first))["status"] == "succeeded"
    docker_on.exec.files[f"{FOLDER}/b.txt"] = b"auf dem Server geaendert\n"
    second = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW}})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": second})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).text
    assert "0 neu, 0 geändert, 1 entfernt" in action and "1 Datei(en) fehlen im Repository" in action
    assert (await _approve(client, docker_on, proposed))["status"] == "succeeded"
    files = docker_on.exec.files
    assert f"{FOLDER}/a.txt" not in files
    assert files[f"{FOLDER}/b.txt"] == b"auf dem Server geaendert\n" and files[f"{FOLDER}/eigen.txt"] == b"vom Nutzer\n"

    previous = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()["previous"]
    assert previous["commit_before"] == first
    assert (await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": previous["id"]})))["status"] == "succeeded"
    assert files[f"{FOLDER}/a.txt"] == b"a\n", "Zurück holt die entfernte Datei wieder"
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json()["applied"]["commit"] == first


@pytest.mark.asyncio
async def test_a_change_on_the_server_after_the_proposal_stops_everything(client, docker_on, tmp_path):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "a.txt": b"a\n"})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    docker_on.exec.files[COMPOSE_PATH] = b"services: {}\n"
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "failed" and "seit dem Vorschlag" in approved["result"]["error"]
    assert docker_on.exec.files[COMPOSE_PATH] == b"services: {}\n" and f"{FOLDER}/a.txt" not in docker_on.exec.files
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json() == {"applied": None, "previous": None}


@pytest.mark.asyncio
async def test_compose_refusing_the_new_state_puts_the_old_one_back(client, docker_on, tmp_path):
    old = docker_on.exec.files[COMPOSE_PATH]
    docker_on.exec.files[f"{FOLDER}/a.txt"] = b"alt\n"
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "a.txt": b"neu\n", "sub": {"b.txt": b"b\n"}})
    docker_on.exec.config = result(stderr="services.web Additional property foo is not allowed\n", code=15)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "Compose lehnt die Datei ab" in approved["result"]["error"]
    assert "is not allowed. Der alte Stand steht wieder" in approved["result"]["error"]
    files = docker_on.exec.files
    assert (files[COMPOSE_PATH], files[f"{FOLDER}/a.txt"]) == (old, b"alt\n")
    assert f"{FOLDER}/sub/b.txt" not in files and f"{FOLDER}/sub" not in docker_on.exec.sftp_server.dirs
    assert not any("up" in argv for argv in docker_on.exec.argv_calls if "compose" in argv)
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json() == {"applied": None, "previous": None}


@pytest.mark.asyncio
async def test_a_write_that_breaks_off_is_taken_back(client, docker_on, tmp_path):
    old = docker_on.exec.files[COMPOSE_PATH]
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "zz": {"b.txt": b"b\n"}})
    docker_on.exec.sftp_server.denied_paths.add(f"{FOLDER}/zz")
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "ließen sich nicht schreiben" in approved["result"]["error"] and "alte Stand steht wieder" in approved["result"]["error"]
    assert docker_on.exec.files[COMPOSE_PATH] == old


@pytest.mark.asyncio
@pytest.mark.parametrize(("setup", "reason"), [
    (lambda sftp: (sftp.dirs.add(f"{FOLDER}/conf"), sftp.dir_owners.__setitem__(f"{FOLDER}/conf", (999, 999))), "einem anderen Benutzer"),
    (lambda sftp: (sftp.files.__setitem__(f"{FOLDER}/conf", b"/etc"), sftp.modes.__setitem__(f"{FOLDER}/conf", 0o120777)), "ein Verweis"),
    (lambda sftp: sftp.files.__setitem__(f"{FOLDER}/conf", b"datei"), "kein Ordner"),
    (lambda sftp: (sftp.files.__setitem__(f"{FOLDER}/conf/app.ini", b"x"), sftp.dirs.add(f"{FOLDER}/conf"),
                   sftp.modes.__setitem__(f"{FOLDER}/conf/app.ini", 0o120777)), "Verweis oder keine normale Datei"),
])
async def test_paths_through_links_or_foreign_folders_are_refused(client, docker_on, tmp_path, setup, reason):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "conf": {"app.ini": b"a=1\n"}})
    setup(docker_on.exec.sftp_server)
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert proposed.status_code == 409 and reason in proposed.json()["detail"], proposed.text


@pytest.mark.asyncio
async def test_a_stack_folder_that_is_a_link_is_refused(client, docker_on, tmp_path):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    sftp = docker_on.exec.sftp_server
    sftp.dirs.discard(FOLDER)
    sftp.files[FOLDER], sftp.modes[FOLDER] = b"/srv/shop", 0o120777
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert proposed.status_code == 409 and "Verweis auf einen anderen Ordner" in proposed.json()["detail"]


@pytest.mark.asyncio
async def test_apply_needs_the_commit_from_the_preview_and_something_to_do(client, docker_on, tmp_path):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    other = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": "0" * 40})
    assert other.status_code == 409 and "anderen Stand" in other.json()["detail"]
    assert (await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": "x"})).status_code == 422
    docker_on.exec.files[COMPOSE_PATH] = NEW
    same = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert same.status_code == 409 and "schon genau dieser Stand" in same.json()["detail"]
    assert (await client.post(f"{url}/apply", headers=docker_on.viewer, json={"commit": commit})).status_code == 403
    assert (await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": "0" * 32})).status_code == 404


@pytest.mark.asyncio
async def test_an_env_on_the_server_is_never_replaced_a_new_one_is_private(client, docker_on, tmp_path):
    sftp = docker_on.exec.sftp_server
    docker_on.exec.files[f"{FOLDER}/.env"], sftp.modes[f"{FOLDER}/.env"] = b"GEHEIM=alt\n", 0o100640
    docker_on.exec.files[f"{FOLDER}/run.sh"], sftp.modes[f"{FOLDER}/run.sh"] = b"echo alt\n", 0o100750
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, ".env": b"GEHEIM=neu\n", "run.sh": b"echo neu\n",
                                                             "sub": {".env": b"X=1\n"}})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).text
    assert "Diese Umgebungsdateien bleiben auf dem Server, wie sie sind; die aus dem Repository werden nicht übernommen: .env." in action and "GEHEIM" not in action
    assert "Rechte werden strenger, erlaubt sind nur 0600, 0644 und 0755: run.sh 750 → 600." in action
    assert "Volumes werden vorher nicht gesichert." in action
    assert (await _approve(client, docker_on, proposed))["status"] == "succeeded"
    assert (docker_on.exec.files[f"{FOLDER}/.env"], sftp.modes[f"{FOLDER}/.env"] & 0o777) == (b"GEHEIM=alt\n", 0o640)
    assert (docker_on.exec.files[f"{FOLDER}/sub/.env"], sftp.modes[f"{FOLDER}/sub/.env"] & 0o777) == (b"X=1\n", 0o600)
    assert sftp.modes[f"{FOLDER}/run.sh"] & 0o777 == 0o600, "bisher für andere nicht lesbar"
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    assert state["applied"]["files"] == 3, "die stehen gebliebene .env gehört nicht zu dem, was Deck geschrieben hat"


@pytest.mark.asyncio
async def test_risky_settings_lead_the_approval_and_unseen_ones_stop_it(client, docker_on, tmp_path):
    risky = (COMPOSE + b"    privileged: true\n    volumes: [./data:/data, /etc:/host-etc:ro]\n")
    docker_on.exec.config = result(stderr="env file /opt/stacks/shop/app.env not found\n", code=1)
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": risky})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert action["reason"].startswith("Riskant: „web“ bekommt alle Rechte des Servers (privileged). „web“ bindet /etc vom Server ein (nur lesend).")
    assert action["payload"]["risky"] == ["„web“ bekommt alle Rechte des Servers (privileged).", "„web“ bindet /etc vom Server ein (nur lesend)."]
    docker_on.exec.config = result(json.dumps({"services": {"web": {"image": "busybox:1.37", "privileged": True, "network_mode": "host"}}}))
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "failed" and "nicht in der Freigabe standen: „web“ teilt das Netz" in approved["result"]["error"]
    assert "alte Stand steht wieder" in approved["result"]["error"] and docker_on.exec.files[COMPOSE_PATH] != risky


@pytest.mark.asyncio
async def test_forgetting_or_releasing_drops_what_was_applied(client, docker_on, tmp_path):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json()["applied"] is not None
    assert (await client.delete(url, headers=docker_on.owner)).status_code == 200
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json() == {"applied": None, "previous": None}
    assert docker_on.exec.files[COMPOSE_PATH] == NEW, "Vergessen ändert nichts auf dem Server"


@pytest.mark.asyncio
async def test_a_proposal_runs_once_and_only_for_a_person(client, docker_on, tmp_path):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    robot = SimpleNamespace(action_type=ga.APPLY_ACTION, host_ref=docker_on.host, payload=action["payload"],
                            proposed_by=SimpleNamespace(type="extension"))
    assert (await executor.execute(robot)).error == ga.NOT_A_USER
    assert (await _approve(client, docker_on, proposed))["status"] == "succeeded"
    person = SimpleNamespace(**{**robot.__dict__, "proposed_by": SimpleNamespace(type="user")})
    assert "abgelaufen" in (await executor.execute(person)).error, "der Entwurf ist nach dem ersten Lauf weg"


@pytest.mark.asyncio
async def test_files_that_were_already_there_are_never_removed(client, docker_on, tmp_path):
    files = docker_on.exec.files
    files[f"{FOLDER}/vom-nutzer.conf"] = b"x=1\n"
    url, first = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "vom-nutzer.conf": b"x=1\n", "a.txt": b"a\n"})
    assert (await _apply(client, docker_on, url, first))["status"] == "succeeded"
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json()["applied"]["files"] == 2
    second = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW + b"    init: true\n", "a.txt": b"a\n"}})
    assert (await _apply(client, docker_on, url, second))["status"] == "succeeded"
    third = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW}})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": third})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert action["payload"]["removes"] == [f"{FOLDER}/a.txt"], "a.txt hat Deck geschrieben und blieb seitdem gleich"
    assert "Entfernt: a.txt." in action["reason"]
    assert (await _approve(client, docker_on, proposed))["status"] == "succeeded"
    assert files[f"{FOLDER}/vom-nutzer.conf"] == b"x=1\n" and f"{FOLDER}/a.txt" not in files


@pytest.mark.asyncio
async def test_forgetting_drops_proposals_and_old_ones_expire(client, docker_on, tmp_path):
    import os

    drafts = docker_on.data_dir / "git-apply" / "drafts"
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, ".env": b"GEHEIM=1\n"})
    assert (await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})).status_code == 202
    assert [p for p in drafts.iterdir() if p.is_dir()]
    assert (await client.delete(url, headers=docker_on.owner)).status_code == 200
    assert not [p for p in drafts.iterdir() if p.is_dir()], "nach dem Vergessen liegt kein Inhalt aus dem Repository mehr da"

    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, ".env": b"GEHEIM=1\n"})
    assert (await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})).status_code == 202
    [draft] = [p for p in drafts.iterdir() if p.is_dir()]
    os.utime(draft, (1, 1))
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).status_code == 200
    assert not draft.exists()


@pytest.mark.asyncio
async def test_services_reading_changed_files_restart_and_the_approval_names_them(client, docker_on, tmp_path, monkeypatch):
    compose = COMPOSE + b"    volumes: [./conf:/etc/app:ro]\n"
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": compose, "conf": {"app.ini": b"a=2\n"}})
    docker_on.exec.files[COMPOSE_PATH] = compose
    docker_on.exec.config = result(json.dumps({"services": {
        "web": {"image": "busybox:1.37", "volumes": [{"type": "bind", "source": f"{FOLDER}/conf", "target": "/etc/app", "read_only": True}]},
        "worker": {"image": "busybox:1.37"},
    }}))
    before = len(docker_on.exec.argv_calls)
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert not [argv for argv in docker_on.exec.argv_calls[before:] if "compose" in argv], "vor der Freigabe sieht Compose nichts aus dem Repository"
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert "Neu starten, weil sie geänderte Dateien einlesen: web." in action["reason"] and "Neu: conf/app.ini." in action["reason"]
    assert action["payload"]["restarts"] == ["web"]
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "succeeded", approved
    assert "Neu gestartet, weil sie geänderte Dateien einlesen: web." in approved["result"]["output"]
    calls = [argv for argv in docker_on.exec.argv_calls if "compose" in argv]
    restarts = [i for i, argv in enumerate(calls) if "restart" in argv]
    assert len(restarts) == 1 and calls[restarts[0]][-3:] == ["restart", "--", "web"]
    assert restarts[0] < max(i for i, argv in enumerate(calls) if "up" in argv), "zuletzt gleicht up -d alles ab"

    original = docker_on.exec.run_argv

    async def restart_fails(host, argv, **kwargs):
        if "restart" in argv:
            return result(stderr="Error response from daemon: boom\n", code=1)
        return await original(host, argv, **kwargs)

    monkeypatch.setattr(docker_on.handle, "run_argv", restart_fails, raising=False)
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    proposed = await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": state["previous"]["id"]})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert "Neu starten, weil sie zurückgestellte Dateien einlesen: web." in action["reason"] and action["payload"]["restarts"] == ["web"]
    back = await _approve(client, docker_on, proposed)
    assert back["status"] == "failed" and "Neustart von web ist gescheitert: Error response from daemon: boom" in back["result"]["error"]
    assert f"{FOLDER}/conf/app.ini" not in docker_on.exec.files, "zurückgestellt ist trotzdem"


def test_readers_are_services_that_bind_a_changed_file():
    ga = _ga()
    config = {
        "services": {
            "web": {"volumes": [{"type": "bind", "source": f"{FOLDER}/conf"}, {"type": "volume", "source": "data"}]},
            "db": {"volumes": [{"type": "bind", "source": f"{FOLDER}/db.conf"}]},
            "proxy": {"configs": [{"source": "site"}], "secrets": ["token"]},
            "other": {"volumes": [{"type": "bind", "source": f"{FOLDER}/conf2"}]},
            "bad name": {"volumes": [{"type": "bind", "source": FOLDER}]},
        },
        "configs": {"site": {"file": f"{FOLDER}/site.conf"}},
        "secrets": {"token": {"file": f"{FOLDER}/token.txt"}},
    }
    assert ga.readers(config, [f"{FOLDER}/conf/app.ini"]) == ["web"]
    assert ga.readers(config, [f"{FOLDER}/db.conf", f"{FOLDER}/token.txt"]) == ["db", "proxy"]
    assert ga.readers(config, [f"{FOLDER}/site.conf"]) == ["proxy"]
    assert ga.readers(config, [f"{FOLDER}/data/x"]) == []
    assert ga.readers(None, [f"{FOLDER}/conf/app.ini"]) == []
    local = ga.local_config(b"services:\n  web:\n    image: x\n    volumes: [./conf:/etc/app]\n    configs: [site]\n"
                            b"configs:\n  site:\n    file: ./site.conf\n", FOLDER)
    assert ga.readers(local, [f"{FOLDER}/conf/a", f"{FOLDER}/site.conf"]) == ["web"]
    broken = {"services": {"web": {"configs": [{"source": [1]}, {"source": {"a": 1}}, ["x"]]}}, "configs": {"site": {"file": "/x"}}}
    assert ga.readers(broken, ["/x"]) == []
    short = ga.local_config(b"services:\n  web:\n    image: x\n    volumes: ['./conf/:/etc/app', '../shop/data:/d']\n", FOLDER)
    assert ga.readers(short, [f"{FOLDER}/conf/a"]) == ["web"] and ga.readers(short, [f"{FOLDER}/data/x"]) == ["web"]
    assert ga.readers({"services": {"web": {"volumes": [{"type": "bind", "source": f"{FOLDER}/./conf/"}]}}}, [f"{FOLDER}/conf/a"]) == ["web"]
    escape = ga.local_config(b"services:\n  web:\n    image: x\n    volumes: ['../../../etc:/host-etc']\n", FOLDER)
    assert escape["services"]["web"]["volumes"][0]["source"] == "/etc"
    assert any("/etc" in line for line in ga.risky_settings(escape))
    with pytest.raises(ValueError):
        ga.cmd_restart_services(SimpleNamespace(working_dir=FOLDER), ["web; rm -rf /"])


@pytest.mark.asyncio
async def test_a_change_between_plan_and_write_stops_and_takes_back(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    files = docker_on.exec.files
    files[f"{FOLDER}/z.txt"] = b"alt\n"
    old = files[COMPOSE_PATH]
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "z.txt": b"neu\n"})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    plan = ga.GitApply.plan

    async def then_someone_writes(self, *args):
        planned = await plan(self, *args)
        files[f"{FOLDER}/z.txt"] = b"von jemand anderem\n"
        return planned

    monkeypatch.setattr(ga.GitApply, "plan", then_someone_writes)
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "failed" and "„/opt/stacks/shop/z.txt“ hat sich auf dem Server gerade geändert" in approved["result"]["error"]
    assert files[f"{FOLDER}/z.txt"] == b"von jemand anderem\n" and files[COMPOSE_PATH] == old


@pytest.mark.asyncio
async def test_rolling_back_twice_goes_on_where_it_stopped(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    files = docker_on.exec.files
    files[f"{FOLDER}/a.txt"], files[f"{FOLDER}/b.txt"] = b"a alt\n", b"b alt\n"
    both = (b"services:\n  web:\n    image: busybox:1.37\n    volumes: ['./a.txt:/a']\n"
            b"  db:\n    image: busybox:1.37\n    volumes: ['./b.txt:/b']\n")
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": both, "a.txt": b"a neu\n", "b.txt": b"b neu\n"})
    files[COMPOSE_PATH] = both
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    docker_on.exec.config = result(json.dumps({"services": {
        "web": {"volumes": [{"type": "bind", "source": f"{FOLDER}/a.txt"}]}, "db": {"volumes": [{"type": "bind", "source": f"{FOLDER}/b.txt"}]},
    }}))
    write = ga.SftpFiles.write

    async def b_breaks(self, path, mode, data):
        if path.endswith("/b.txt"):
            raise OSError("Verbindung weg")
        await write(self, path, mode, data)

    monkeypatch.setattr(ga.SftpFiles, "write", b_breaks)
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    back = await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": state["previous"]["id"]}))
    assert back["status"] == "failed" and "stehen schon wieder auf dem alten Stand" in back["result"]["error"]
    assert files[f"{FOLDER}/a.txt"] == b"a alt\n" and files[f"{FOLDER}/b.txt"] == b"b neu\n"
    monkeypatch.setattr(ga.SftpFiles, "write", write)
    back = await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": state["previous"]["id"]}))
    assert back["status"] == "succeeded" and "seit dem Einspielen geändert" not in back["result"]["output"]
    assert files[f"{FOLDER}/b.txt"] == b"b alt\n"
    restarted = [argv for argv in docker_on.exec.argv_calls if "restart" in argv]
    assert restarted[-1][-3:] == ["--", "db", "web"], "auch der Dienst der schon im ersten Anlauf zurückgestellten Datei"
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json()["applied"] is None


@pytest.mark.asyncio
async def test_rollback_names_the_services_of_the_compose_file_it_puts_back(client, docker_on, tmp_path):
    docker_on.exec.files[COMPOSE_PATH] = COMPOSE + b"    volumes: ['./conf:/etc/app']\n"
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "conf": {"app.ini": b"a=1\n"}})
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    proposed = await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": state["previous"]["id"]})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert action["payload"]["restarts"] == ["web"], "die zurückgestellte Compose-Datei bindet conf/ wieder ein"
    assert "Neu starten, weil sie zurückgestellte Dateien einlesen: web." in action["reason"]


@pytest.mark.asyncio
async def test_a_rollback_proposal_survives_an_odd_or_linked_compose_file(client, docker_on, tmp_path):
    files, modes = docker_on.exec.files, docker_on.exec.modes
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": files[COMPOSE_PATH], "a.txt": b"a\n"})
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    previous = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()["previous"]["id"]

    files[COMPOSE_PATH] = COMPOSE + b"    volumes: 5\n"
    proposed = await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": previous})
    assert proposed.status_code == 202, proposed.text
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert action["payload"]["restarts"] == [] and "eigens neu gestartet wird keiner" in action["reason"]

    files[COMPOSE_PATH], modes[COMPOSE_PATH] = f"{FOLDER}/a.txt".encode(), 0o120777
    proposed = await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": previous})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert action["payload"]["restarts"] == [] and "ließ sich vorab nicht lesen" in action["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("odd", [b"    volumes: true\n", b"    cap_add: 5\n", b"    devices: true\n    security_opt: x\n"])
async def test_an_odd_compose_file_in_the_repository_does_not_break_the_proposal(client, docker_on, tmp_path, odd):
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": COMPOSE + odd + b"volumes:\n  1: {}\n  daten: {}\n"})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert proposed.status_code == 202, proposed.text
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert "Kein Dienst liest eine geänderte Datei ein" in action["reason"]


@pytest.mark.asyncio
async def test_what_the_file_alone_cannot_tell_compose_checks_after_the_approval(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()

    def unreadable(config):
        raise TypeError("unerwartet")

    monkeypatch.setattr(ga, "risky_settings", unreadable)
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    assert (await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})).status_code == 202


@pytest.mark.asyncio
async def test_when_applying_breaks_the_services_left_out_still_get_their_button(client, docker_on, tmp_path, monkeypatch):
    compose = COMPOSE + b"    volumes: ['${CONF:-./conf}:/etc/app']\n"
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": compose, "conf": {"app.ini": b"a=2\n"}})
    docker_on.exec.files[COMPOSE_PATH] = compose
    docker_on.exec.config = result(json.dumps({"services": {"web": {"volumes": [{"type": "bind", "source": f"{FOLDER}/conf"}]}}}))

    async def broken(*args, **kwargs):
        raise OSError("Verbindung weg")

    monkeypatch.setattr(_instance(docker_on)._git_apply.actions, "run", broken)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and approved["result"]["detail"] == {"written": True, "stale": ["web"]}


@pytest.mark.asyncio
async def test_rollback_leaves_changed_files_and_keeps_the_time_of_the_state_before(client, docker_on, tmp_path):
    files = docker_on.exec.files
    url, first = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "a.txt": b"a\n"})
    assert (await _apply(client, docker_on, url, first))["status"] == "succeeded"
    [written] = (docker_on.data_dir / "git-apply").glob("*/written.json")
    first_at = "2026-10-01T08:00:00+00:00"
    written.write_text(json.dumps({**json.loads(written.read_text()), "at": first_at}))
    second = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW, "a.txt": b"a2\n", "b.txt": b"b\n"}})
    assert (await _apply(client, docker_on, url, second))["status"] == "succeeded"
    files[f"{FOLDER}/b.txt"] = b"von Hand\n"
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    back = await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": state["previous"]["id"]}))
    assert back["status"] == "succeeded" and "1 Datei(en) wurden seit dem Einspielen geändert" in back["result"]["output"]
    assert (files[f"{FOLDER}/a.txt"], files[f"{FOLDER}/b.txt"]) == (b"a\n", b"von Hand\n")
    applied = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()["applied"]
    assert (applied["commit"], applied["at"]) == (first, first_at)


@pytest.mark.asyncio
async def test_a_damaged_backup_changes_nothing(client, docker_on, tmp_path):
    files = docker_on.exec.files
    files[f"{FOLDER}/a.txt"] = b"alt\n"
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "a.txt": b"neu\n"})
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    for backup in (docker_on.data_dir / "git-apply").glob("*/previous/*.bin"):
        backup.write_bytes(b"kaputt")
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    back = await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": state["previous"]["id"]}))
    assert back["status"] == "failed" and "zurückgestellt wurde nichts" in back["result"]["error"]
    assert (files[COMPOSE_PATH], files[f"{FOLDER}/a.txt"]) == (NEW, b"neu\n")


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "refused"), [(0o100640, True), (0o100644, False)])
async def test_files_of_another_owner(client, docker_on, tmp_path, mode, refused):
    sftp = docker_on.exec.sftp_server
    sftp.files[f"{FOLDER}/app.ini"], sftp.modes[f"{FOLDER}/app.ini"], sftp.owners[f"{FOLDER}/app.ini"] = b"alt\n", mode, (999, 999)
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "app.ini": b"neu\n"})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    if refused:
        assert proposed.status_code == 409 and "Nummer 999 und ist nur für ihn lesbar" in proposed.json()["detail"]
        return
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert "Gehört danach dem Besitzer des Stack-Ordners statt einem anderen Benutzer: app.ini." in action["reason"]


@pytest.mark.asyncio
async def test_limits_folders_starting_with_dots_and_name_clashes(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "..daten": {"x.txt": b"x\n"}})
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    assert docker_on.exec.files[f"{FOLDER}/..daten/x.txt"] == b"x\n"

    monkeypatch.setattr(ga, "SERVER_TOTAL_LIMIT", 10)
    docker_on.exec.files[f"{FOLDER}/y.txt"] = b"y" * 20
    second = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW, "y.txt": b"y\n"}})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": second})
    assert proposed.status_code == 409 and "zusammen größer als" in proposed.json()["detail"]

    clash = _commit(tmp_path / "origin", {"shop": {"docker-compose.yml": NEW, "compose.yaml": b"services: {}\n"}})
    assert (await client.put(url, headers=docker_on.owner, json={"url": URL, "path": "shop/docker-compose.yml"})).status_code == 200
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": clash})
    assert proposed.status_code == 409 and "wüsste nicht, welche gilt" in proposed.json()["detail"]


@pytest.mark.asyncio
async def test_the_executor_checks_draft_source_and_lock_again(client, docker_on, tmp_path):
    ga = _ga()
    git_apply = _instance(docker_on)._git_apply
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    [draft] = [p for p in (docker_on.data_dir / "git-apply" / "drafts").iterdir() if p.is_dir()]
    meta = json.loads((draft / "meta.json").read_text())
    other = b"services: {}\n"
    (draft / "0.bin").write_bytes(other)
    meta["files"][COMPOSE_PATH]["sha256"] = _sha(other)
    (draft / "meta.json").write_text(json.dumps(meta))
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "failed" and "passt nicht mehr" in approved["result"]["error"]
    assert docker_on.exec.files[COMPOSE_PATH] != other

    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    source = git_apply.git.sources.get(docker_on.host, "shop")
    await git_apply.git.sources.put(docker_on.host, "shop", type(source)(**{**source.__dict__, "ref": "v2"}))
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "failed" and approved["result"]["error"] == ga.CHANGED
    await git_apply.git.sources.put(docker_on.host, "shop", source)

    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    with git_apply.actions.locks.hold(docker_on.host, "shop", "Anwenden"):
        approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "failed" and "Anwenden" in approved["result"]["error"]
    assert docker_on.exec.files[COMPOSE_PATH] != NEW


@pytest.mark.asyncio
async def test_breaking_off_while_writing_puts_the_old_state_back(client, docker_on, tmp_path, monkeypatch):
    import asyncio

    ga = _ga()
    files = docker_on.exec.files
    old = files[COMPOSE_PATH]
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "z.txt": b"z\n"})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    write = ga.SftpFiles.write

    async def cancelled_at_z(self, path, mode, data):
        if path.endswith("/z.txt"):
            raise asyncio.CancelledError
        await write(self, path, mode, data)

    monkeypatch.setattr(ga.SftpFiles, "write", cancelled_at_z)
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    person = SimpleNamespace(action_type=ga.APPLY_ACTION, host_ref=docker_on.host, payload=action["payload"],
                             proposed_by=SimpleNamespace(type="user"))
    with pytest.raises(asyncio.CancelledError):
        await executor.execute(person)
    assert files[COMPOSE_PATH] == old and f"{FOLDER}/z.txt" not in files
    assert not list((docker_on.data_dir / "git-apply").glob("*/previous*"))


@pytest.mark.asyncio
async def test_forgetting_while_applying_keeps_nothing_of_it(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    git_apply = _instance(docker_on)._git_apply
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    plan = ga.GitApply.plan

    async def forgotten_meanwhile(self, *args):
        planned = await plan(self, *args)
        await git_apply.git.sources.remove(docker_on.host, "shop")
        return planned

    monkeypatch.setattr(ga.GitApply, "plan", forgotten_meanwhile)
    assert (await _approve(client, docker_on, proposed))["status"] == "succeeded"
    assert (await client.get(f"{url}/applied", headers=docker_on.owner)).json() == {"applied": None, "previous": None}


def test_leftovers_of_a_broken_off_run_go_away(tmp_path):
    ga = _ga()
    state = ga.AppliedState(tmp_path)
    folder = tmp_path / ga._stack_key("h", "shop")
    (folder / "previous.neu-dead").mkdir(parents=True)
    (folder / "previous.neu-dead" / "0.bin").write_bytes(b"GEHEIM=1")
    previous = ga.Previous(id="1" * 32, commit="c", commit_before=None, at="t", written={}, removed=[], restore={}, manifest_before={})
    staged = state.stage_previous("h", "shop", previous, {})
    assert sorted(p.name for p in folder.iterdir()) == [staged.name]


@pytest.mark.asyncio
async def test_a_step_that_took_effect_before_breaking_off_is_taken_back(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    files = docker_on.exec.files
    files[f"{FOLDER}/zz.txt"] = b"alt\n"
    old = files[COMPOSE_PATH]
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "z.txt": b"z\n", "zz.txt": b"neu\n"})
    write, broken = ga.SftpFiles.write, []

    async def lands_then_times_out(self, path, mode, data):
        await write(self, path, mode, data)
        if path.endswith("/zz.txt") and not broken:
            broken.append(path)
            raise TimeoutError

    monkeypatch.setattr(ga.SftpFiles, "write", lands_then_times_out)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "alte Stand steht wieder" in approved["result"]["error"]
    assert (files[COMPOSE_PATH], files[f"{FOLDER}/zz.txt"]) == (old, b"alt\n") and f"{FOLDER}/z.txt" not in files


@pytest.mark.asyncio
async def test_taking_back_never_goes_through_a_folder_swapped_for_a_link(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    sftp = docker_on.exec.sftp_server
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "conf": {"app.ini": b"a=1\n"}, "zz.txt": b"z\n"})
    write = ga.SftpFiles.write

    async def swap_then_break(self, path, mode, data):
        if path.endswith("/zz.txt"):
            sftp.dirs.discard(f"{FOLDER}/conf")
            sftp.files[f"{FOLDER}/conf"], sftp.modes[f"{FOLDER}/conf"] = b"/etc", 0o120777
            raise OSError("Verbindung weg")
        await write(self, path, mode, data)

    monkeypatch.setattr(ga.SftpFiles, "write", swap_then_break)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "nicht ganz zurückschreiben" in approved["result"]["error"]
    assert sftp.files[f"{FOLDER}/conf/app.ini"] == b"a=1\n", "hinter dem Verweis fasst der Rückbau nichts an"


@pytest.mark.asyncio
async def test_an_env_deck_wrote_stays_when_the_repository_drops_it(client, docker_on, tmp_path):
    files, sftp = docker_on.exec.files, docker_on.exec.sftp_server
    url, first = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, ".env": b"A=1\n"})
    assert (await _apply(client, docker_on, url, first))["status"] == "succeeded"
    assert (files[f"{FOLDER}/.env"], sftp.modes[f"{FOLDER}/.env"] & 0o777) == (b"A=1\n", 0o600)
    second = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW + b"    init: true\n"}})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": second})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert action["payload"]["removes"] == [] and "Umgebungsdateien fehlen jetzt im Repository und bleiben auf dem Server: .env." in action["reason"]
    assert (await _approve(client, docker_on, proposed))["status"] == "succeeded"
    assert files[f"{FOLDER}/.env"] == b"A=1\n"


@pytest.mark.asyncio
async def test_only_services_named_in_the_approval_restart_the_others_get_a_button(client, docker_on, tmp_path, monkeypatch):
    compose = COMPOSE + b"    volumes: ['${CONF:-./conf}:/etc/app']\n"
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": compose, "conf": {"app.ini": b"a=2\n"}})
    docker_on.exec.files[COMPOSE_PATH] = compose
    docker_on.exec.config = result(json.dumps({"services": {"web": {"volumes": [{"type": "bind", "source": f"{FOLDER}/conf"}]}}}))
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert action["payload"]["restarts"] == [] and "Neu starten" not in action["reason"]
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "succeeded" and approved["result"]["detail"]["stale"] == ["web"]
    assert "Diese Dienste lesen vielleicht noch den alten Stand: web." in approved["result"]["output"]
    assert not [argv for argv in docker_on.exec.argv_calls if "restart" in argv], "was nicht in der Freigabe stand, startet nicht neu"

    _running_before(docker_on, monkeypatch, {"web": ("shop-web-1",)})
    later = await client.post(f"{url}/restart", headers=docker_on.owner, json={"services": ["web"]})
    restart = (await client.get(f"/api/v1/actions/{later.json()['action_id']}", headers=docker_on.owner)).json()
    assert (later.json()["risk"], restart["risk"], restart["action_type"]) == ("medium", "medium", "docker.stack_git_restart")
    assert "web" in restart["reason"]
    done = await _approve(client, docker_on, later)
    assert done["status"] == "succeeded" and done["result"]["output"] == "Neu gestartet: web.\nPrüfung danach: läuft stabil"
    assert [argv for argv in docker_on.exec.argv_calls if "restart" in argv][-1][-3:] == ["restart", "--", "web"]
    for bad in (["web; rm -rf /"], ["web", "web"], [], ["x"] * 51):
        assert (await client.post(f"{url}/restart", headers=docker_on.owner, json={"services": bad})).status_code in (400, 422), bad


@pytest.mark.asyncio
async def test_the_restart_executor_checks_the_names_itself(client, docker_on, tmp_path):
    ga = _ga()
    await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)

    def restart(services):
        return SimpleNamespace(action_type=ga.RESTART_ACTION, host_ref=docker_on.host, payload={"project": "shop", "services": services},
                               proposed_by=SimpleNamespace(type="user"))

    before = len(docker_on.exec.argv_calls)
    for bad in (["web; rm -rf /"], ["web", "web"], "web", [], [f"s{n}" for n in range(51)]):
        assert (await executor.execute(restart(bad))).error == "Ungültige Dienstnamen.", bad
    with _instance(docker_on)._git_apply.actions.locks.hold(docker_on.host, "shop", "Anwenden"):
        assert "Anwenden" in (await executor.execute(restart(["web"]))).error
    assert docker_on.exec.argv_calls[before:] == []


@pytest.mark.asyncio
async def test_before_the_approval_compose_never_sees_content_from_the_repository(client, docker_on, tmp_path):
    compose = b"include:\n  - git@git.example.org:team/common.git\n" + NEW
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": compose, "a.txt": b"a\n"})
    before = len(docker_on.exec.argv_calls), len(docker_on.exec.calls)
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert proposed.status_code == 202, proposed.text
    called = [*docker_on.exec.argv_calls[before[0]:], *docker_on.exec.calls[before[1]:]]
    assert not [call for call in called if (isinstance(call, list) and "compose" in call) or "docker compose" in str(call)], called
    assert docker_on.exec.stdin == [], "auch nichts über die Standardeingabe"


@pytest.mark.asyncio
async def test_rollback_does_not_follow_a_link_and_says_so(client, docker_on, tmp_path):
    sftp = docker_on.exec.sftp_server
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "conf": {"app.ini": b"a=1\n"}})
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    del sftp.files[f"{FOLDER}/conf/app.ini"]
    sftp.dirs.discard(f"{FOLDER}/conf")
    sftp.files[f"{FOLDER}/conf"], sftp.modes[f"{FOLDER}/conf"] = b"/etc", 0o120777
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    back = await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": state["previous"]["id"]}))
    assert back["status"] == "succeeded" and "1 Datei(en) liegen hinter einem Verweis" in back["result"]["output"]
    assert sftp.files[f"{FOLDER}/conf"] == b"/etc"


@pytest.mark.asyncio
async def test_without_a_place_for_the_state_before_the_answer_says_so(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})

    def no_room(*args):
        raise OSError("voll")

    monkeypatch.setattr(ga.AppliedState, "keep_previous", no_room)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "succeeded" and "einen Schritt zurück gibt es diesmal nicht" in approved["result"]["output"]
    assert "bleibt für einen Schritt zurück" not in approved["result"]["output"]


def test_the_approval_keeps_its_fixed_hints_when_it_gets_long():
    ga = _ga()
    stack = SimpleNamespace(working_dir=FOLDER, project="shop")
    source = SimpleNamespace(url=URL, ref="main")
    writes = [f"{FOLDER}/{'d' * 40}/f{i}.txt" for i in range(30)]
    plan = ga.Plan("c" * 40, {}, writes=writes, env_kept=[f"{FOLDER}/.env"])
    plan.targets = {path: ga.Target(path) for path in writes}
    risky = ["„web“ bekommt alle Rechte des Servers (privileged)."]
    for head, notes in ((risky, ["Hinweis aus dem Repository. " * 20] * 20), (risky * 200, [])):
        text = ga.apply_reason(source, stack, "c" * 40, plan, head, [], notes)
        assert len(text) <= ga.REASON_LIMIT and text.startswith("Riskant:")
        assert ga.NO_VOLUME_BACKUP in text
    assert "Diese Umgebungsdateien bleiben auf dem Server, wie sie sind; die aus dem Repository werden nicht übernommen: .env." in (
        ga.apply_reason(source, stack, "c" * 40, plan, risky, [], []))


def test_long_names_never_push_the_warnings_out_of_the_approval():
    ga = _ga()
    stack = SimpleNamespace(working_dir=FOLDER, project="shop")
    source = SimpleNamespace(url=URL, ref="main")
    long = "n" * 255
    writes = [f"{FOLDER}/{long}{i}/{long}.txt" for i in range(30)]
    envs = [f"{FOLDER}/{long}{i}/.env" for i in range(40)]
    plan = ga.Plan("c" * 40, {}, writes=writes, env_kept=envs)
    plan.targets = {path: ga.Target(path) for path in writes}
    warning = "„web“ baut sein Image aus dem Repository (build); dabei läuft Code aus dem Repository auf dem Server."
    text = ga.apply_reason(source, stack, "c" * 40, plan, [], [], [warning])
    assert len(text) <= ga.REASON_LIMIT
    assert ga.NO_VOLUME_BACKUP in text and warning in text and "und 30 weitere" in text
    assert text.index(ga.NO_VOLUME_BACKUP) < text.index(warning) < text.index("Umgebungsdateien") < text.index("Neu:")
    assert long not in text, "ein Name zeigt höchstens sein Ende"
    crowded = ga.Plan("c" * 40, {}, writes=writes, env_kept=envs, env_left=envs, owner_changes=writes, kept=writes,
                      mode_changes=[(path, 0o640, 0o600) for path in writes])
    crowded.targets = plan.targets
    text = ga.apply_reason(source, stack, "c" * 40, crowded, [], [], [warning])
    assert len(text) <= ga.REASON_LIMIT and ga.NO_VOLUME_BACKUP in text and warning in text, "auch volle Hinweise verdrängen keine Warnung"


def test_reading_as_the_owner_names_the_real_reason():
    import asyncio

    ga = _ga()

    def owner_files(code, stderr=""):
        async def run_argv(host, argv, **kwargs):
            return result(stderr=stderr, code=code)
        return ga.OwnerFiles(SimpleNamespace(exec=SimpleNamespace(run_argv=run_argv)), "h", ga.Owner(1000, 1000, sudo=True))

    with pytest.raises(ga.Refused, match="Verweis oder keine normale Datei"):
        asyncio.run(owner_files(3).read(f"{FOLDER}/a", 10))
    with pytest.raises(ga.Refused, match="ließ sich auf dem Server nicht lesen: sudo: a password is required"):
        asyncio.run(owner_files(1, "sudo: a password is required\n").read(f"{FOLDER}/a", 10))
    with pytest.raises(ga.Refused, match="nennt den Besitzer"):
        asyncio.run(ga.owner_files(None, "h", None, SimpleNamespace(permissions=0o040755)))

    class Short:
        async def read(self, path, limit):
            return b""

    with pytest.raises(ga.Refused, match="nicht vollständig"):
        asyncio.run(ga.read_target(FakeSftp({f"{FOLDER}/a": b"zehn bytes"}), Short(), f"{FOLDER}/a", 100))


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("setpriv") is None or not hasattr(os, "geteuid") or os.geteuid() != 0,
                    reason="braucht eine POSIX-Shell, setpriv und root")
def test_the_fixed_scripts_refuse_unreadable_files_and_folders_as_targets():
    ga = _ga()
    folder = Path(tempfile.mkdtemp())
    try:
        folder.chmod(0o755)
        locked = folder / "zu.txt"
        locked.write_bytes(b"geheim")
        locked.chmod(0)
        nobody = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "--"]
        read = subprocess.run([*nobody, "sh", "-c", ga._READ, "sh", str(locked), "100"], capture_output=True, timeout=10, check=False)
        assert (read.returncode, read.stdout) == (4, b"")
        (folder / "ordner").mkdir()
        tmp = folder / ".neu.tmp"
        write = subprocess.run(["sh", "-c", ga._WRITE, "sh", str(tmp), "644", str(folder / "ordner")], input=b"x", capture_output=True, timeout=10, check=False)
        assert write.returncode == 5 and not tmp.exists() and not list((folder / "ordner").iterdir())
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("afterwards", ["unlesbar", "fremd"])
async def test_an_unclear_step_is_left_alone_and_only_the_way_back_touches_it(client, docker_on, tmp_path, monkeypatch, afterwards):
    ga = _ga()
    files = docker_on.exec.files
    files[f"{FOLDER}/zz.txt"] = b"alt\n"
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "zz.txt": b"neu\n"})
    write, read, broken = ga.SftpFiles.write, ga.read_target, []

    async def lands_then_times_out(self, path, mode, data):
        await write(self, path, mode, data)
        if path.endswith("/zz.txt") and not broken:
            broken.append(path)
            if afterwards == "fremd":
                files[path] = b"fremd\n"
            raise TimeoutError

    async def unreadable_once(sftp, io, path, limit):
        if afterwards == "unlesbar" and broken == [path]:
            broken.append("gelesen")
            raise OSError("Verbindung weg")
        return await read(sftp, io, path, limit)

    monkeypatch.setattr(ga.SftpFiles, "write", lands_then_times_out)
    monkeypatch.setattr(ga, "read_target", unreadable_once)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "nicht sicher feststellen; zz.txt hat Deck deshalb nicht angefasst" in approved["result"]["error"]
    left = b"neu\n" if afterwards == "unlesbar" else b"fremd\n"
    assert files[f"{FOLDER}/zz.txt"] == left, "unklar heißt: nicht anfassen"
    previous = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()["previous"]
    back = await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": previous["id"]}))
    assert back["status"] == "succeeded", back
    assert files[f"{FOLDER}/zz.txt"] == (b"alt\n" if afterwards == "unlesbar" else b"fremd\n")


@pytest.mark.asyncio
async def test_an_unclear_removal_is_left_alone_and_the_way_back_brings_the_file_back(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    files = docker_on.exec.files
    url, first = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "a.txt": b"a\n"})
    assert (await _apply(client, docker_on, url, first))["status"] == "succeeded"
    second = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW, "b.txt": b"b\n"}})
    remove, read, broken = ga.SftpFiles.remove, ga.read_target, []

    async def lands_then_times_out(self, path):
        await remove(self, path)
        if path.endswith("/a.txt"):
            broken.append(path)
            raise TimeoutError

    async def unreadable_once(sftp, io, path, limit):
        if broken == [path]:
            broken.append("gelesen")
            raise OSError("Verbindung weg")
        return await read(sftp, io, path, limit)

    monkeypatch.setattr(ga.SftpFiles, "remove", lands_then_times_out)
    monkeypatch.setattr(ga, "read_target", unreadable_once)
    approved = await _apply(client, docker_on, url, second)
    assert approved["status"] == "failed" and "nicht sicher feststellen; a.txt hat Deck" in approved["result"]["error"]
    assert f"{FOLDER}/a.txt" not in files, "unklar heißt: nicht anfassen, auch nicht zurückschreiben"
    assert f"{FOLDER}/b.txt" not in files, "was sicher geschrieben war, ist zurückgenommen"
    previous = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()["previous"]
    back = await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": previous["id"]}))
    assert back["status"] == "succeeded", back
    assert files[f"{FOLDER}/a.txt"] == b"a\n" and f"{FOLDER}/b.txt" not in files


@pytest.mark.asyncio
async def test_breaking_off_during_the_compose_check_puts_the_old_state_back(client, docker_on, tmp_path, monkeypatch):
    import asyncio

    ga = _ga()
    files = docker_on.exec.files
    old = files[COMPOSE_PATH]
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "z.txt": b"z\n"})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()

    async def ended_while_checking(self, host, stack):
        raise asyncio.CancelledError

    monkeypatch.setattr(ga.GitApply, "check", ended_while_checking)
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    person = SimpleNamespace(action_type=ga.APPLY_ACTION, host_ref=docker_on.host, payload=action["payload"],
                             proposed_by=SimpleNamespace(type="user"))
    with pytest.raises(asyncio.CancelledError):
        await executor.execute(person)
    assert files[COMPOSE_PATH] == old and f"{FOLDER}/z.txt" not in files
    assert not list((docker_on.data_dir / "git-apply").glob("*/previous*"))


@pytest.mark.asyncio
async def test_a_compose_check_that_cannot_run_counts_as_failed(client, docker_on, tmp_path, monkeypatch):
    files = docker_on.exec.files
    old = files[COMPOSE_PATH]
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    run_argv = docker_on.handle.run_argv

    async def config_times_out(host, argv, **kwargs):
        if argv[-3:] == ["config", "--format", "json"]:
            raise TimeoutError
        return await run_argv(host, argv, **kwargs)

    monkeypatch.setattr(docker_on.handle, "run_argv", config_times_out)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "nicht innerhalb von" in approved["result"]["error"]
    assert "alte Stand steht wieder" in approved["result"]["error"] and files[COMPOSE_PATH] == old


@pytest.mark.asyncio
async def test_a_check_that_cannot_be_read_says_so_and_puts_the_old_state_back(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    files = docker_on.exec.files
    old = files[COMPOSE_PATH]
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})

    async def unreadable(self, host, stack):
        raise ValueError("unerwartete Ausgabe")

    monkeypatch.setattr(ga.GitApply, "check", unreadable)
    approved = await _apply(client, docker_on, url, commit)
    error = approved["result"]["error"]
    assert error.startswith("Die Prüfung mit Compose ließ sich nicht auswerten: unerwartete Ausgabe."), error
    assert "alte Stand steht wieder" in error and files[COMPOSE_PATH] == old


@pytest.mark.asyncio
async def test_a_failed_restart_does_not_show_values_from_an_env_file(client, docker_on, tmp_path):
    ga = _ga()
    await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    docker_on.exec.verb = result(stderr="failed to read /opt/stacks/shop/.env: line 1: unterminated quoted value GEHEIM=x\n", code=1)
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    req = SimpleNamespace(action_type=ga.RESTART_ACTION, host_ref=docker_on.host, payload={"project": "shop", "services": ["web"]},
                          proposed_by=SimpleNamespace(type="user"))
    done = await executor.execute(req)
    assert "gescheitert" in done.error and "GEHEIM" not in done.error


def _restart_request(ga, docker_on, services):
    return SimpleNamespace(action_type=ga.RESTART_ACTION, host_ref=docker_on.host, payload={"project": "shop", "services": services},
                           proposed_by=SimpleNamespace(type="user"))


def _running_before(docker_on, monkeypatch, up: dict[str, tuple[str, ...]]):
    from nodvard_deck_ext_docker.stack_update import Running

    async def running(host, stack):
        return {service: Running(service, f"shop-{service}-1", "busybox:1.37", "sha256:" + "a" * 64, (f"shop-{service}-1",),
                                 running=bool(names), up=names) for service, names in up.items()}

    monkeypatch.setattr(_instance(docker_on)._git_apply.update, "running", running)


def _check_says(monkeypatch, verdict):
    import importlib

    ac = importlib.import_module("nodvard_deck_ext_docker.after_check")
    checked = []

    async def check_running(ctx, host, refs, **_):
        checked.append(refs)
        return verdict, None

    monkeypatch.setattr(ac, "check_running", check_running)
    return checked


@pytest.mark.asyncio
async def test_after_a_restart_only_the_restarted_services_must_keep_running(client, docker_on, tmp_path, monkeypatch):
    from nodvard_deck_ext_docker.image_apply import Verdict

    ga = _ga()
    await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _running_before(docker_on, monkeypatch, {"web": ("shop-web-1",), "worker": ("shop-worker-1",)})
    checked = _check_says(monkeypatch, Verdict(ok=False, text="Container startet immer wieder neu.", crashed=True))
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    done = await executor.execute(_restart_request(ga, docker_on, ["web"]))
    assert checked == [["shop-web-1"]], "nur der Container von web, nicht worker"
    assert (done.success, done.output, done.error) == (
        False, "Neu gestartet: web.", "Prüfung danach: Container startet immer wieder neu.")
    assert done.detail == {"check": {"ok": False, "text": "Container startet immer wieder neu."}}


@pytest.mark.asyncio
async def test_a_stopped_service_is_started_by_the_restart_and_checked_too(client, docker_on, tmp_path, monkeypatch):
    from nodvard_deck_ext_docker.image_apply import Verdict

    ga = _ga()
    await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    _running_before(docker_on, monkeypatch, {"web": (), "worker": ("shop-worker-1",)})
    checked = _check_says(monkeypatch, Verdict(ok=False, text="Container läuft nicht (beendet, Exit-Code 1).", crashed=True))
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    done = await executor.execute(_restart_request(ga, docker_on, ["web"]))
    assert checked == [["shop-web-1"]], "auch der gestoppte Container von web, nicht worker"
    assert (done.success, done.error) == (False, "Prüfung danach: Container läuft nicht (beendet, Exit-Code 1).")


@pytest.mark.asyncio
async def test_a_restart_without_containers_says_why_it_did_not_check(client, docker_on, tmp_path, monkeypatch):
    from nodvard_deck_ext_docker.image_apply import Verdict
    from nodvard_deck_ext_docker.stacks import StackProblem

    ga = _ga()
    await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    checked = _check_says(monkeypatch, Verdict(ok=True, text="läuft stabil"))
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)
    _running_before(docker_on, monkeypatch, {"worker": ("shop-worker-1",)})
    done = await executor.execute(_restart_request(ga, docker_on, ["web"]))
    assert done.success and done.output == f"Neu gestartet: web.\n{ga.RESTART_UNCHECKED}"

    async def unreadable(host, stack):
        raise StackProblem("Docker antwortet nicht")

    monkeypatch.setattr(_instance(docker_on)._git_apply.update, "running", unreadable)
    done = await executor.execute(_restart_request(ga, docker_on, ["web"]))
    assert done.success and done.output == f"Neu gestartet: web.\n{ga.RESTART_UNREADABLE}"
    assert checked == []


@pytest.mark.asyncio
async def test_the_way_back_restores_the_old_rights_too(client, docker_on, tmp_path):
    files, sftp = docker_on.exec.files, docker_on.exec.sftp_server
    files[f"{FOLDER}/run.sh"], sftp.modes[f"{FOLDER}/run.sh"] = b"echo\n", 0o100644
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "run.sh": (0o100755, b"echo\n")})
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    assert sftp.modes[f"{FOLDER}/run.sh"] & 0o777 == 0o755
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    back = await _approve(client, docker_on, await client.post(f"{url}/rollback", headers=docker_on.owner, json={"previous": state["previous"]["id"]}))
    assert back["status"] == "succeeded" and sftp.modes[f"{FOLDER}/run.sh"] & 0o777 == 0o644


@pytest.mark.asyncio
async def test_the_way_back_checks_its_state_and_the_lock_again(client, docker_on, tmp_path):
    ga = _ga()
    files = docker_on.exec.files
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "a.txt": b"a\n"})
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    state = (await client.get(f"{url}/applied", headers=docker_on.owner)).json()
    executor = ga.DockerStackGitApplyExecutor(_instance(docker_on)._git_apply)

    def back(previous):
        return SimpleNamespace(action_type=ga.ROLLBACK_ACTION, host_ref=docker_on.host, payload={"project": "shop", "previous": previous},
                               proposed_by=SimpleNamespace(type="user"))

    assert "hat sich seit dem Vorschlag geändert" in (await executor.execute(back("f" * 32))).error
    with _instance(docker_on)._git_apply.actions.locks.hold(docker_on.host, "shop", "Anwenden"):
        assert "Anwenden" in (await executor.execute(back(state["previous"]["id"]))).error
    assert files[f"{FOLDER}/a.txt"] == b"a\n", "zurückgestellt wurde nichts"


@pytest.mark.asyncio
async def test_a_change_before_removing_stops_and_takes_back(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    files = docker_on.exec.files
    url, first = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "r.txt": b"r\n"})
    assert (await _apply(client, docker_on, url, first))["status"] == "succeeded"
    second = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW}})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": second})
    plan = ga.GitApply.plan

    async def then_someone_writes(self, *args):
        planned = await plan(self, *args)
        files[f"{FOLDER}/r.txt"] = b"von jemand anderem\n"
        return planned

    monkeypatch.setattr(ga.GitApply, "plan", then_someone_writes)
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "failed" and "r.txt“ hat sich auf dem Server gerade geändert" in approved["result"]["error"]
    assert files[f"{FOLDER}/r.txt"] == b"von jemand anderem\n"


@pytest.mark.asyncio
async def test_route_and_executor_both_check_the_layout(client, docker_on, tmp_path, monkeypatch):
    ga = _ga()
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    monkeypatch.setattr(ga, "layout_problem", lambda stack: "Der Stack hat jetzt zwei Compose-Dateien.")
    refused = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert refused.status_code == 409 and "zwei Compose-Dateien" in refused.json()["detail"]
    approved = await _approve(client, docker_on, proposed)
    assert approved["status"] == "failed" and approved["result"]["error"] == "Der Stack hat jetzt zwei Compose-Dateien."


@pytest.mark.asyncio
async def test_every_env_file_of_the_stack_is_kept_not_only_dot_env(client, docker_on, tmp_path):
    files, modes = docker_on.exec.files, docker_on.exec.sftp_server.modes
    named = f"{FOLDER}/prod.env,{FOLDER}/vars.txt"
    docker_on.exec.rows = [{**row, "env_files": named} if row["project"] == "shop" else row for row in docker_on.exec.rows]
    files[f"{FOLDER}/prod.env"] = b"A=server\n"
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop"
    adopt = {"working_dir": FOLDER, "config_files": [COMPOSE_PATH], "env_files": named.split(",")}
    assert (await client.post(f"{base}/adopt", json=adopt, headers=docker_on.owner)).status_code == 200
    commit = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW, "prod.env": b"A=repo\n", "vars.txt": b"B=1\n"}})
    _git(docker_on).run_fetch = _in_process(_local(tmp_path / "origin"))
    assert (await client.put(f"{base}/git", headers=docker_on.owner, json={"url": URL, "path": "shop/compose.yaml"})).status_code == 200
    shown = (await client.post(f"{base}/git/fetch", headers=docker_on.owner)).json()
    assert [(f["path"], f["state"], f["env"]) for f in shown["files"]] == [
        ("compose.yaml", "changed", False), ("prod.env", "changed", True), ("vars.txt", "new", True)]
    proposed = await client.post(f"{base}/git/apply", headers=docker_on.owner, json={"commit": commit})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert "die aus dem Repository werden nicht übernommen: prod.env." in action["reason"]
    assert (await _approve(client, docker_on, proposed))["status"] == "succeeded"
    assert files[f"{FOLDER}/prod.env"] == b"A=server\n"
    assert files[f"{FOLDER}/vars.txt"] == b"B=1\n" and modes[f"{FOLDER}/vars.txt"] & 0o777 == 0o600, "neu, aber eine Umgebungsdatei"

    second = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW, "prod.env": b"A=anders\n"}})
    shown = (await client.post(f"{base}/git/fetch", headers=docker_on.owner)).json()
    assert shown["commit"]["id"] == second and shown["unchanged"] is True, "nur eine Umgebungsdatei weicht ab: nichts einzuspielen"
    refused = await client.post(f"{base}/git/apply", headers=docker_on.owner, json={"commit": second})
    assert refused.status_code == 409 and "schon genau dieser Stand" in refused.json()["detail"]
    third = _commit(tmp_path / "origin", {"shop": {"compose.yaml": NEW + b"    stop_grace_period: 5s\n"}})
    proposed = await client.post(f"{base}/git/apply", headers=docker_on.owner, json={"commit": third})
    action = (await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)).json()
    assert "Umgebungsdateien fehlen jetzt im Repository und bleiben auf dem Server: vars.txt." in action["reason"]
    assert (await _approve(client, docker_on, proposed))["status"] == "succeeded"
    assert files[f"{FOLDER}/vars.txt"] == b"B=1\n"


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine POSIX-Shell")
def test_the_fixed_scripts_refuse_links_and_folders_without_root(tmp_path):
    ga = _ga()
    (tmp_path / "echt.txt").write_bytes(b"x")
    (tmp_path / "verweis.txt").symlink_to(tmp_path / "echt.txt")
    (tmp_path / "ordner").mkdir()
    for target in ("verweis.txt", "ordner"):
        read = subprocess.run(["sh", "-c", ga._READ, "sh", str(tmp_path / target), "100"], capture_output=True, timeout=10, check=False)
        assert (read.returncode, read.stdout) == (3, b""), target
    write = subprocess.run(["sh", "-c", ga._WRITE, "sh", str(tmp_path / ".neu.tmp"), "644", str(tmp_path / "ordner")], input=b"x",
                           capture_output=True, timeout=10, check=False)
    assert write.returncode == 5 and not (tmp_path / ".neu.tmp").exists() and not list((tmp_path / "ordner").iterdir())


@pytest.mark.asyncio
async def test_writing_over_sftp_refuses_other_rights():
    ga = _ga()
    sftp = FakeSftp({f"{FOLDER}/a.txt": b"alt"})
    io = ga.SftpFiles(sftp, SimpleNamespace)
    for mode in (0o4755, 0o666, 0o700):
        with pytest.raises(ValueError):
            await io.write(f"{FOLDER}/a.txt", mode, b"neu")
    assert sftp.files == {f"{FOLDER}/a.txt": b"alt"}


def _instance(docker_on):
    from nodvard_deck.ext.runtime import get_extension_runtime

    return get_extension_runtime().loaded["docker"].instance


# --- Bausteine ---------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writing_a_new_file_works_without_posix_rename():
    ga = _ga()
    sftp = FakeSftp({f"{FOLDER}/alt.txt": b"alt"})
    sftp.posix_rename_missing = True
    io = ga.SftpFiles(sftp, SimpleNamespace)
    await io.write(f"{FOLDER}/neu.txt", 0o644, b"neu")
    await io.write(f"{FOLDER}/alt.txt", 0o600, b"anders")
    assert (sftp.files[f"{FOLDER}/neu.txt"], sftp.files[f"{FOLDER}/alt.txt"]) == (b"neu", b"anders")
    assert sftp.modes[f"{FOLDER}/alt.txt"] & 0o777 == 0o600 and sorted(sftp.files) == [f"{FOLDER}/alt.txt", f"{FOLDER}/neu.txt"]
    assert all(mode == 0o600 for path, mode in sftp.seen_modes), "bis zum Umbenennen nur für den Zugang lesbar"


@pytest.mark.parametrize(("repo", "current", "path", "wanted"), [
    (0o644, None, "/x/app.ini", 0o644), (0o755, None, "/x/run.sh", 0o755), (0o644, None, "/x/.env", 0o600),
    (0o644, None, "/x/prod.env", 0o600), (0o644, None, "/x/.env.local", 0o600), (0o644, None, "/x/environment", 0o644),
    (0o644, 0o600, "/x/a", 0o600), (0o755, 0o750, "/x/a", 0o600), (0o755, 0o4755, "/x/a", 0o755), (0o644, 0o2775, "/x/a", 0o644),
    (0o644, 0o755, "/x/a", 0o644), (0o755, 0o644, "/x/a", 0o755),
])
def test_only_0644_0755_or_0600_are_written(repo, current, path, wanted):
    ga = _ga()
    target = ga.Target(path, attrs=None if current is None else SimpleNamespace(permissions=0o100000 | current))
    assert ga.wanted_mode(repo, target, path) == wanted
    restored = ga._restore_mode(target, path) if current is not None else None
    assert restored in (None, 0o600, 0o644, 0o755)


def test_owner_commands_take_paths_and_modes_only_as_checked_words():
    ga = _ga()
    owner = ga.Owner(1000, 1000, sudo=True)
    assert ga.cmd_owner_mkdir(owner, f"{FOLDER}/conf") == [
        "sudo", "-n", "setpriv", "--reuid=1000", "--regid=1000", "--clear-groups", "--", "mkdir", "-m", "755", "--", f"{FOLDER}/conf",
    ]
    bad = [
        lambda: ga.cmd_owner_write(owner, f"{FOLDER}/.a", 0o4755, f"{FOLDER}/a"),
        lambda: ga.cmd_owner_write(owner, f"{FOLDER}/.a", 0o666, f"{FOLDER}/a"),
        lambda: ga.cmd_owner_remove(owner, "-rf"),
        lambda: ga.cmd_owner_rmdir(owner, "conf/sub"),
        lambda: ga.cmd_owner_probe(ga.Owner("0", 0, sudo=False)),
        lambda: ga.cmd_owner_probe(ga.Owner(-1, 0, sudo=False)),
    ]
    for build in bad:
        with pytest.raises(ValueError):
            build()


@pytest.mark.asyncio
async def test_drafts_hold_exactly_the_proposed_files(tmp_path):
    ga = _ga()
    drafts = ga.FileDrafts(tmp_path / "drafts")
    draft = drafts.put("h1", "shop", "a" * 40, {f"{FOLDER}/a": (0o644, b"a"), f"{FOLDER}/b": (0o755, b"b")})
    meta, files = drafts.get(draft)
    assert files == {f"{FOLDER}/a": (0o644, b"a"), f"{FOLDER}/b": (0o755, b"b")} and meta["commit"] == "a" * 40
    (tmp_path / "drafts" / draft / "0.bin").write_bytes(b"anders")
    assert drafts.get(draft) is None and drafts.get("../x") is None
    drafts.drop(draft)
    assert not (tmp_path / "drafts" / draft).exists()


@pytest.mark.asyncio
async def test_real_sftp_writes_beside_and_renames(real_sftp):
    ga = _ga()
    import asyncssh

    async with real_sftp.sftp(None) as sftp:
        io = ga.SftpFiles(sftp, asyncssh.SFTPAttrs)
        await ga.make_dirs(sftp, io, FOLDER, f"{FOLDER}/conf/deep/app.ini", made := [])
        await io.write(f"{FOLDER}/conf/deep/app.ini", 0o600, b"a=1\n")
        real_sftp.file.write_bytes(b"alt\n")
        await io.write(f"{FOLDER}/compose.yaml", 0o644, b"neu\n")
    written = real_sftp.root / "opt" / "stacks" / "shop" / "conf" / "deep" / "app.ini"
    assert made == [f"{FOLDER}/conf", f"{FOLDER}/conf/deep"] and written.read_bytes() == b"a=1\n" and real_sftp.file.read_bytes() == b"neu\n"
    assert (written.stat().st_mode & 0o777) == 0o600 or sys.platform == "win32"
    assert sorted(p.name for p in written.parent.iterdir()) == ["app.ini"], "keine Reste daneben"


def test_dry_run_tells_what_would_happen():
    import asyncio

    ga = _ga()
    executor = ga.DockerStackGitApplyExecutor(SimpleNamespace(actions=None))
    apply = SimpleNamespace(action_type=ga.APPLY_ACTION, payload={"project": "shop", "commit": "abcdef1234", "writes": ["a", "b"], "removes": ["c"]})
    back = SimpleNamespace(action_type=ga.ROLLBACK_ACTION, payload={"project": "shop"})
    assert asyncio.run(executor.dry_run(apply)).summary == "Würde 2 Datei(en) schreiben und 1 entfernen (Stand abcdef1) und Stack „shop“ anwenden."
    assert "Stand vor dem letzten Einspielen" in asyncio.run(executor.dry_run(back)).summary


def test_payload_and_draft_must_match():
    ga = _ga()
    meta = {"host_id": "h", "project": "shop", "commit": "c", "files": {"/a": {"sha256": "1"}}}
    good = {"commit": "c", "files": {"/a": "1"}}
    assert ga._draft_fits(meta, "h", "shop", good)
    assert not ga._draft_fits(meta, "h", "shop", {**good, "files": {"/a": "2"}})
    assert not ga._draft_fits(meta, "anderer", "shop", good)
    assert not ga._draft_fits(meta, "h", "shop", {**good, "commit": "d"})
    assert json.dumps(good)


# --- Mit den Rechten des Besitzers --------------------------------------------------------------------------------------


class OwnerServer:
    """Deck ist als `login` angemeldet: führt die festen Befehle aus `git_apply` mit den Rechten aus, die `setpriv` setzt,
    und folgt Verweisen wie ein echtes Dateisystem; schreiben darf nur root oder der Besitzer eines Ordners."""

    def __init__(self, fake, login: int = 0) -> None:
        self.fake, self.sftp, self.login = fake, fake.sftp_server, login
        self.sftp.dirs.add("/etc")
        self.sftp.dir_owners["/etc"] = (0, 0)
        self.calls: list[list[str]] = []
        self.before_write = None
        self.sudo = result()
        self.setpriv = result()

    def resolve(self, path: str) -> str:
        parts, current = path.strip("/").split("/"), ""
        for index, part in enumerate(parts):
            current = f"{current}/{part}"
            if current in self.sftp.files and self.sftp.modes.get(current, 0) & 0o170000 == 0o120000 and index < len(parts) - 1:
                current = self.sftp.files[current].decode().rstrip("/")
        return current

    def _may_write(self, uid: int, folder: str) -> bool:
        return folder in self.sftp.dirs and (uid == 0 or self.sftp.dir_owners.get(folder, (1000, 1000))[0] == uid)

    async def run_argv(self, host, argv, *, stdin=None, timeout_s=60, log_command=None):
        ga = _ga()
        self.calls.append(list(argv))
        if argv == ["id", "-u"]:
            return result(f"{self.login}\n")
        uid = self.login
        if argv[:2] == ["sudo", "-n"]:
            if self.sudo.exit_code:
                return self.sudo
            argv, uid = argv[2:], 0
        if argv[:1] == ["setpriv"]:
            if self.setpriv.exit_code:
                return self.setpriv
            uid, argv = int(argv[1].split("=", 1)[1]), argv[argv.index("--") + 1:]
        if argv == ["true"]:
            return result()
        files, modes, owners = self.sftp.files, self.sftp.modes, self.sftp.owners
        if argv[:3] == ["sh", "-c", ga._READ]:
            real = self.resolve(argv[4])
            if real not in files or modes.get(real, 0o100644) & 0o170000 != 0o100000:
                return result(code=3)
            return result(base64.b64encode(files[real][: int(argv[5])]).decode() + "\n")
        if argv[:3] == ["sh", "-c", ga._WRITE]:
            if self.before_write and argv[6].startswith(self.before_write[0]):
                (_, hook), self.before_write = self.before_write, None
                hook()
            tmp, mode, path = self.resolve(argv[4]), int(argv[5], 8), self.resolve(argv[6])
            if not self._may_write(uid, tmp.rsplit("/", 1)[0]) or not self._may_write(uid, path.rsplit("/", 1)[0]):
                return result(stderr="Permission denied\n", code=1)
            files[path], modes[path], owners[path] = stdin, 0o100000 | mode, (uid, uid)
            return result()
        if argv[:4] == ["mkdir", "-m", "755", "--"] and len(argv) == 5:
            real = self.resolve(argv[4])
            if not self._may_write(uid, real.rsplit("/", 1)[0]):
                return result(stderr="Permission denied\n", code=1)
            self.sftp.dirs.add(real)
            self.sftp.dir_owners[real] = (uid, uid)
            return result()
        if argv[:3] == ["rm", "-f", "--"] and len(argv) == 4:
            real = self.resolve(argv[3])
            if not self._may_write(uid, real.rsplit("/", 1)[0]):
                return result(stderr="Permission denied\n", code=1)
            files.pop(real, None)
            return result()
        if argv[:2] == ["rmdir", "--"] and len(argv) == 3:
            self.sftp.dirs.discard(self.resolve(argv[2]))
            return result()
        return await self.fake.run_argv(host, argv, stdin=stdin, timeout_s=timeout_s, log_command=log_command)


def _as_root(docker_on, monkeypatch, login: int = 0) -> OwnerServer:
    server = OwnerServer(docker_on.exec, login)
    monkeypatch.setattr(docker_on.handle, "run_argv", server.run_argv, raising=False)
    return server


@pytest.mark.asyncio
async def test_as_root_deck_writes_only_with_the_rights_of_the_folder_owner(client, docker_on, tmp_path, monkeypatch):
    server = _as_root(docker_on, monkeypatch)
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "conf": {"app.ini": b"a=1\n"}})
    assert (await _apply(client, docker_on, url, commit))["status"] == "succeeded"
    sftp = docker_on.exec.sftp_server
    assert (sftp.files[f"{FOLDER}/conf/app.ini"], sftp.owners[f"{FOLDER}/conf/app.ini"], sftp.dir_owners[f"{FOLDER}/conf"]) == (b"a=1\n", (1000, 1000), (1000, 1000))
    steps = [call for call in server.calls if call[:1] == ["setpriv"]]
    assert steps and all(call[1:5] == ["--reuid=1000", "--regid=1000", "--clear-groups", "--"] for call in steps)
    assert not any(call[:1] in (["mkdir"], ["rm"], ["sh"]) for call in server.calls if call[0] != "setpriv" and "compose" not in call)


@pytest.mark.asyncio
async def test_a_folder_swapped_for_a_link_while_applying_leads_nowhere(client, docker_on, tmp_path, monkeypatch):
    server = _as_root(docker_on, monkeypatch)
    sftp = docker_on.exec.sftp_server
    sftp.dirs.add(f"{FOLDER}/conf")
    old = sftp.files[COMPOSE_PATH]
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW, "conf": {"app.ini": b"a=1\n"}})

    def swap():
        sftp.dirs.discard(f"{FOLDER}/conf")
        sftp.files[f"{FOLDER}/conf"], sftp.modes[f"{FOLDER}/conf"] = b"/etc", 0o120777

    server.before_write = (f"{FOLDER}/conf/", swap)
    approved = await _apply(client, docker_on, url, commit)
    assert approved["status"] == "failed" and "Permission denied" in approved["result"]["error"]
    assert not [path for path in sftp.files if path.startswith("/etc/")], "nichts landet hinter dem Verweis"
    assert sftp.files[COMPOSE_PATH] == old and "alte Stand steht wieder" in approved["result"]["error"]


@pytest.mark.asyncio
async def test_without_root_or_setpriv_deck_names_what_is_missing(client, docker_on, tmp_path, monkeypatch):
    server = _as_root(docker_on, monkeypatch, login=1001)
    url, commit = await _source(client, docker_on, tmp_path, {"compose.yaml": NEW})
    server.sudo = result(stderr="sudo: a password is required\n", code=1)
    refused = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert refused.status_code == 409 and "meldet sich aber als 1001 an" in refused.json()["detail"] and "sudo -n setpriv" in refused.json()["detail"]
    server.sudo = result()
    proposed = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert proposed.status_code == 202, proposed.text
    assert any(call[:3] == ["sudo", "-n", "setpriv"] for call in server.calls)
    server.login = 0
    server.setpriv = result(stderr="sh: setpriv: not found\n", code=127)
    missing = await client.post(f"{url}/apply", headers=docker_on.owner, json={"commit": commit})
    assert missing.status_code == 409 and "util-linux" in missing.json()["detail"]
