"""Images, Volumes und Netze: Listen aus echten Ausgaben von Docker 29.8.2 (containerd-Speicher) und Löschen nur über die
Freigabe."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "docker"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docker"
DF = json.loads((FIXTURES / "system_df_v.json").read_text(encoding="utf-8"))
NETWORKS = (FIXTURES / "network_ls.jsonl").read_text(encoding="utf-8")
BUSYBOX = "sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e"
OWN_BUILD = "sha256:b56a8d6d307f51a80b3fe3a17a4f0aa360625238d2f57a0bff0c0c381fcd672e"
ANONYMOUS = "71eb2622455116bd99f1e416beaf8e5959601d100fa2768752b61dd023c88350"
UNUSED_NET = "2a956b625e886d5d7ed8e9c2554fb1267b6453aeb11891e5cd4279bf1aa9f110"
TESTNETZ = "acc0ef96c6ffdd77c9956eec154f6583fa2ea6d0c5118fab7ba430259055f9e8"
BRIDGE = "715a128f61f0d9e386366455ebd6c39c490995c87a89dc326b28353b484aa974"


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _res():
    from nodvard_deck_ext_docker import resources

    return resources


def _result(stdout: str = "", stderr: str = "", code: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, exit_code=code, duration_ms=3)


class _Exec:
    def __init__(self, answers: dict[str, object] | None = None) -> None:
        from nodvard_deck_ext_docker import commands

        self.calls: list[str] = []
        self.answers = {commands.system_df_verbose(): _result(json.dumps(DF)), commands.networks(): _result(NETWORKS)}
        self.answers.update(answers or {})

    async def run(self, host, command, timeout_s=60):
        self.calls.append(command)
        answer = self.answers.get(command, _result())
        if isinstance(answer, Exception):
            raise answer
        return answer


class _Hosts:
    async def get(self, host_id):
        return SimpleNamespace(id=host_id) if host_id == "h1" else None


def _ctx(answers: dict[str, object] | None = None):
    return SimpleNamespace(exec=_Exec(answers), hosts=_Hosts())


HOST = SimpleNamespace(id="h1")


# --- Listen ---------------------------------------------------------------------------------------------------------


def test_images_with_names_size_and_users():
    images = {i["id"]: i for i in _res().parse_images(DF)}
    busybox, own = images[BUSYBOX], images[OWN_BUILD]
    assert (busybox["names"], busybox["containers"], busybox["in_use"]) == (["busybox:1.37"], 3, True)
    assert busybox["used_by"] == ["alt", "demo-app-1", "web"]
    assert (own["names"], own["used_by"], own["in_use"], own["dangling"]) == (["eigenes-image:1"], [], False, False)
    assert own["short_id"] == "b56a8d6d307f" and own["size_bytes"] == 6_790_000
    assert own["unique_size_bytes"] == 19_730
    assert (own["removable"], busybox["removable"]) == (True, False)


def test_an_image_with_two_names_is_one_entry_and_one_without_name_is_dangling():
    df = copy.deepcopy(DF)
    df["Images"].append({**df["Images"][1], "Repository": "busybox", "Tag": "latest"})
    df["Images"].append({**df["Images"][0], "ID": "sha256:" + "c" * 64, "Repository": "<none>", "Tag": "<none>", "Containers": "0"})
    images = {i["id"]: i for i in _res().parse_images(df)}
    assert images[BUSYBOX]["names"] == ["busybox:1.37", "busybox:latest"]
    assert images["sha256:" + "c" * 64]["dangling"] is True and images["sha256:" + "c" * 64]["names"] == []
    assert len(images) == 3


def test_an_unused_image_with_two_names_is_not_offered_for_removal():
    df = copy.deepcopy(DF)
    df["Images"].append({**df["Images"][0], "Repository": "eigenes-image", "Tag": "2"})
    own = next(i for i in _res().parse_images(df) if i["id"] == OWN_BUILD)
    assert (own["in_use"], own["removable"]) == (False, False)


@pytest.mark.parametrize(
    ("repo", "tag", "rollback"),
    [
        ("nodvard-deck", "previous", True),
        ("docker.io/library/nodvard-deck", "previous", True),
        ("nodvard-deck-previous", "0.7.0", True),
        ("nodvard-deck", "latest", False),
        ("nodvard-deck-previous-test", "1", False),
        ("ghcr.io/nodvard/deck", "previous", False),
    ],
)
def test_the_rollback_copy_of_deck_is_never_offered_for_removal(repo, tag, rollback):
    df = copy.deepcopy(DF)
    df["Images"][0].update({"Repository": repo, "Tag": tag})
    own = next(i for i in _res().parse_images(df) if i["id"] == OWN_BUILD)
    assert own["in_use"] is False
    assert (own["rollback"], own["removable"]) == (rollback, not rollback)


def test_an_unknown_count_means_in_use():
    df = copy.deepcopy(DF)
    df["Images"][0]["Containers"] = "N/A"
    df["Volumes"][2]["Links"] = "-1"
    own = next(i for i in _res().parse_images(df) if i["id"] == OWN_BUILD)
    leer = next(v for v in _res().parse_volumes(df) if v["name"] == "leer")
    assert (own["in_use"], own["removable"], leer["in_use"], leer["removable"]) == (True, False, True, False)


def test_a_container_made_from_the_image_id_counts_as_a_user():
    df = copy.deepcopy(DF)
    df["Containers"][1]["Image"] = OWN_BUILD.removeprefix("sha256:")[:12]
    own = next(i for i in _res().parse_images(df) if i["id"] == OWN_BUILD)
    assert own["used_by"] == ["alt"] and own["in_use"] is True


def test_volumes_with_users_and_kind():
    volumes = {v["name"]: v for v in _res().parse_volumes(DF)}
    assert volumes["daten"]["used_by"] == ["web"] and volumes["daten"]["in_use"] is True
    assert volumes[ANONYMOUS]["anonymous"] is True and volumes[ANONYMOUS]["used_by"] == ["web"]
    assert volumes["leer"]["in_use"] is False and volumes["leer"]["used_by"] == []
    assert volumes["demo_appdaten"]["compose_project"] == "demo"
    assert volumes["daten"]["size_bytes"] == 0
    assert (volumes["leer"]["removable"], volumes["daten"]["removable"]) == (True, False)
    assert [v["name"] for v in _res().parse_volumes(DF)][-1] == ANONYMOUS, "benannte zuerst"


def test_networks_with_users_and_builtins():
    networks = {n["name"]: n for n in _res().parse_networks(NETWORKS, DF)}
    assert {name for name, n in networks.items() if n["builtin"]} == {"bridge", "host", "none"}
    assert networks["testnetz"]["used_by"] == ["web"] and networks["bridge"]["used_by"] == ["alt"]
    assert networks["demo_default"]["compose_project"] == "demo"
    assert networks["ungenutzt"]["in_use"] is False and networks["ungenutzt"]["id"] == UNUSED_NET
    assert [n["name"] for n in networks.values() if n["removable"]] == ["ungenutzt"]


def test_a_network_name_with_a_comma_still_finds_its_container():
    df = copy.deepcopy(DF)
    df["Containers"].append({"Names": "komma", "Image": "busybox:1.37", "Networks": "a,b", "Mounts": ""})
    line = json.dumps({"ID": "d" * 64, "Name": "a,b", "Driver": "bridge", "Labels": ""})
    (network,) = _res().parse_networks(line, df)
    assert (network["used_by"], network["removable"]) == (["komma"], False)


def test_a_comma_in_a_label_does_not_make_a_named_volume_anonymous():
    df = copy.deepcopy(DF)
    df["Volumes"][2]["Labels"] = "info=a,com.docker.volume.anonymous="
    assert next(v for v in _res().parse_volumes(df) if v["name"] == "leer")["anonymous"] is False


def test_unreadable_or_odd_rows_are_left_out():
    df = {"Images": ["x", {"ID": ""}], "Volumes": [{"Name": "-x"}, {"Name": "a b"}, 5], "Containers": ["y"]}
    lines = "\n".join(["kein json", "5", json.dumps({"ID": "abc", "Name": "x"}), json.dumps({"ID": "f" * 64, "Name": ""})])
    assert _res().parse_images(df) == [] and _res().parse_volumes(df) == [] and _res().parse_networks(lines, df) == []
    assert _res().parse_images({"Images": "kaputt", "Containers": None}) == []


def test_an_image_with_an_unexpected_id_is_listed_but_not_removable():
    (image,) = _res().parse_images({"Images": [{"ID": "b56a8d6d307f", "Repository": "x", "Tag": "1", "Containers": "0"}]})
    assert (image["names"], image["in_use"], image["removable"]) == (["x:1"], False, False)


# --- Abruf ----------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_three_lists_share_one_reading_for_a_while():
    now = [100.0]
    ctx = _ctx()
    resources = _res().DockerResources(ctx, clock=lambda: now[0])
    first = await resources.snapshot(HOST)
    await resources.snapshot(HOST)
    assert len(ctx.exec.calls) == 2  # df und network ls, einmal
    now[0] += _res().CACHE_TTL_S + 1
    await resources.snapshot(HOST)
    assert len(ctx.exec.calls) == 4
    assert {n["name"] for n in first.networks} >= {"testnetz", "ungenutzt"} and first.network_error is None


@pytest.mark.asyncio
async def test_a_docker_error_becomes_a_reason():
    from nodvard_deck_ext_docker import commands

    for answer, reason in [
        (_result(stderr="permission denied while trying to connect to the docker API\n", code=1), "permission denied"),
        (_result("nicht json"), "unlesbare Antwort"),
        (_result("[]"), "unlesbare Antwort"),
        (OSError("Verbindung abgebrochen"), "Server nicht erreichbar: Verbindung abgebrochen"),
    ]:
        resources = _res().DockerResources(_ctx({commands.system_df_verbose(): answer}))
        with pytest.raises(_res().ResourceProblem, match=reason):
            await resources.snapshot(HOST)


@pytest.mark.asyncio
async def test_a_broken_network_list_keeps_the_other_lists():
    from nodvard_deck_ext_docker import commands

    resources = _res().DockerResources(_ctx({commands.networks(): _result(stderr="Fehler im Netz\n", code=1)}))
    snapshot = await resources.snapshot(HOST)
    assert snapshot.networks == [] and snapshot.network_error == "Fehler im Netz"
    assert snapshot.images and snapshot.volumes


# --- Löschen --------------------------------------------------------------------------------------------------------


def _request(action_type: str, payload: dict, host_ref: str | None = "h1"):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor

    return ActionRequest(
        action_type=action_type, host_ref=host_ref, payload=payload, reason="Test", proposed_by=Actor.user("u1", "owner1"),
    )


def _executor(answers: dict[str, object] | None = None):
    ctx = _ctx(answers)
    resources = _res().DockerResources(ctx)
    return _res().DockerResourceExecutor(ctx, resources), ctx, resources


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_type", "payload", "command", "output"),
    [
        ("docker.image_remove", {"image": OWN_BUILD}, f"docker image rm {OWN_BUILD}", "Image b56a8d6d307f gelöscht."),
        ("docker.volume_remove", {"volume": "leer"}, "docker volume rm leer", "Volume leer gelöscht."),
    ],
)
async def test_removal_runs_exactly_one_fixed_command(action_type, payload, command, output):
    executor, ctx, _ = _executor()
    result = await executor.execute(_request(action_type, payload))
    assert (result.success, result.output) == (True, output)
    assert ctx.exec.calls == [command]


@pytest.mark.asyncio
async def test_a_network_is_removed_in_one_call_that_checks_first():
    from nodvard_deck_ext_docker import commands

    command = commands.network_remove(UNUSED_NET)
    executor, ctx, _ = _executor({command: _result("ungenutzt\n")})
    result = await executor.execute(_request("docker.network_remove", {"network": UNUSED_NET}))
    assert (result.success, result.output) == (True, "Netz ungenutzt gelöscht.")
    assert ctx.exec.calls == [command]


@pytest.mark.asyncio
async def test_a_network_with_a_container_is_not_removed():
    from nodvard_deck_ext_docker import commands

    command = commands.network_remove(TESTNETZ)
    executor, _, _ = _executor({command: _result("komma\nweb\n", code=commands.NETWORK_IN_USE)})
    result = await executor.execute(_request("docker.network_remove", {"network": TESTNETZ}))
    assert result.success is False and "noch Container, auch gestoppte zählen: komma, web. Nichts gelöscht." in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_type", "payload"),
    [
        ("docker.image_remove", {"image": "busybox:1.37"}),
        ("docker.image_remove", {"image": OWN_BUILD + "; reboot"}),
        ("docker.image_remove", {}),
        ("docker.volume_remove", {"volume": "-f"}),
        ("docker.volume_remove", {"volume": "daten\n"}),
        ("docker.volume_remove", {"volume": "$(id)"}),
        ("docker.network_remove", {"network": "testnetz"}),
        ("docker.network_remove", {"network": UNUSED_NET[:12]}),
    ],
)
async def test_bad_values_never_reach_the_server(action_type, payload):
    executor, ctx, _ = _executor()
    result = await executor.execute(_request(action_type, payload))
    assert result.success is False and result.error
    assert ctx.exec.calls == []


# Wörtlich von Docker 29.8.2.
REFUSALS = [
    (("Error response from daemon: conflict: unable to delete b56a8d6d307f (must be forced) - image is being used by "
      "stopped container 23b1c7fea915"), "Ein Container nutzt das Image noch"),
    (("Error response from daemon: conflict: unable to delete bdf57e528e45 (cannot be forced) - image is being used by "
      "running container c616baff31e8"), "Ein Container nutzt das Image noch"),
    ("Error response from daemon: remove daten: volume is in use - [c616baff31e8b1c195257d7d0edf4db831dd9f9ce3d7b2bed5c8c845ecf1fbe8]",
     "Ein Container nutzt das Volume noch"),
    ("Error response from daemon: get gibtsnicht: no such volume", "Nicht (mehr) vorhanden."),
    (('Error response from daemon: error while removing network: network testnetz has active endpoints (name:"web" '
      'id:"2280f0b4727a")\nexit status 1'), "An dem Netz hängen noch Container."),
    ("Error response from daemon: bridge is a pre-defined network and cannot be removed\nexit status 1",
     "Die eingebauten Netze von Docker lassen sich nicht löschen."),
    ("Error response from daemon: network nurgestoppt-netz not found\nexit status 1", "Nicht (mehr) vorhanden."),
    ("Error: something new", "Error: something new"),
]


@pytest.mark.parametrize(("stderr", "plain"), REFUSALS)
def test_docker_refusals_read_as_plain_text(stderr, plain):
    assert _res()._plain(stderr + "\n").startswith(plain)


@pytest.mark.asyncio
async def test_a_refusal_ends_the_action_with_the_plain_text():
    from nodvard_deck_ext_docker import commands

    stderr = REFUSALS[2][0]
    executor, _, _ = _executor({commands.volume_remove("leer"): _result(stderr=stderr + "\n", code=1)})
    result = await executor.execute(_request("docker.volume_remove", {"volume": "leer"}))
    assert result.success is False and result.error.startswith(REFUSALS[2][1])


@pytest.mark.asyncio
async def test_after_a_removal_the_lists_are_read_again():
    executor, ctx, resources = _executor()
    await resources.snapshot(HOST)
    await executor.execute(_request("docker.volume_remove", {"volume": "leer"}))
    calls = len(ctx.exec.calls)
    await resources.snapshot(HOST)
    assert len(ctx.exec.calls) == calls + 2


@pytest.mark.asyncio
async def test_unknown_action_and_unknown_host():
    executor, ctx, _ = _executor()
    assert (await executor.execute(_request("docker.volume_prune", {}))).success is False
    assert (await executor.execute(_request("docker.volume_remove", {"volume": "leer"}, host_ref="h2"))).success is False
    assert ctx.exec.calls == []


def test_the_actions_need_the_docker_tag_and_volumes_are_high_risk():
    from nodvard_sdk.types import Risk

    specs = {s.action_type: s for s in _res().resource_action_specs("box")}
    assert {s.host_tags[0] for s in specs.values()} == {"box"}
    assert specs["docker.volume_remove"].default_risk == Risk.HIGH
    assert specs["docker.image_remove"].default_risk == Risk.MEDIUM
    assert specs["docker.network_remove"].default_risk == Risk.LOW
    assert "Daten darin sind dann weg" in specs["docker.volume_remove"].confirm_text
    assert set(specs) == _res().DockerResourceExecutor.action_types


# --- Durch den echten Kern ------------------------------------------------------------------------------------------


async def _owner(client) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"},
    )
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user(client, db_session, role: str) -> dict[str, str]:
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=f"nur-{role}", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": f"nur-{role}", "password": "whatever123"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture
async def docker_on(client, db_session, test_settings, monkeypatch):
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.services import extensions as extensions_service
    from nodvard_deck.services import hosts as hosts_service

    headers = await _owner(client)
    viewer = await _user(client, db_session, "viewer")
    docker = await hosts_service.create_host(db_session, name="pi-host", address="10.0.0.5", tags=["docker"])
    other = await hosts_service.create_host(db_session, name="nas", address="10.0.0.6", tags=[])
    await db_session.commit()
    test_settings.extensions_dir = EXT_DIR.parent
    await extensions_service.discover_and_sync(db_session, test_settings)
    assert (await client.post("/api/v1/extensions/docker/enable", headers=headers)).status_code == 200
    loaded = get_extension_runtime().loaded["docker"]
    fake = _Exec()
    monkeypatch.setattr(loaded.ctx.exec, "run", fake.run)
    return SimpleNamespace(headers=headers, viewer=viewer, host=docker.id, other=other.id, exec=fake)


@pytest.mark.asyncio
async def test_the_lists_through_the_core(client, docker_on):
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}"
    images = await client.get(f"{base}/images", headers=docker_on.headers)
    volumes = await client.get(f"{base}/volumes", headers=docker_on.headers)
    networks = await client.get(f"{base}/networks", headers=docker_on.headers)
    assert images.status_code == volumes.status_code == networks.status_code == 200
    assert {i["id"] for i in images.json()["images"]} == {BUSYBOX, OWN_BUILD}
    assert {v["name"] for v in volumes.json()["volumes"]} == {"daten", "leer", "demo_appdaten", ANONYMOUS}
    assert networks.json()["error"] is None and len(networks.json()["networks"]) == 6
    assert len(docker_on.exec.calls) == 2, "ein Abruf für alle drei Listen"
    assert (await client.get(f"/api/v1/ext/docker/hosts/{docker_on.other}/images", headers=docker_on.headers)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["images", "volumes", "networks"])
async def test_the_lists_need_a_login_and_hosts_execute(client, docker_on, kind):
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/{kind}"
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=docker_on.viewer)).status_code == 403
    assert docker_on.exec.calls == []


@pytest.mark.asyncio
async def test_the_docker_servers_for_the_tabs(client, docker_on):
    listed = await client.get("/api/v1/ext/docker/hosts", headers=docker_on.viewer)
    assert listed.status_code == 200 and listed.json() == {"hosts": [{"id": docker_on.host, "name": "pi-host", "mode": "full"}]}
    assert docker_on.exec.calls == []
    assert (await client.get("/api/v1/ext/docker/hosts")).status_code == 401


@pytest.mark.asyncio
async def test_a_docker_error_is_a_502_with_the_reason(client, docker_on):
    from nodvard_deck_ext_docker import commands

    docker_on.exec.answers[commands.system_df_verbose()] = _result(stderr="Cannot connect to the Docker daemon\n", code=1)
    response = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/volumes", headers=docker_on.headers)
    assert response.status_code == 502 and response.json()["detail"] == "Cannot connect to the Docker daemon"


@pytest.mark.asyncio
async def test_removing_a_volume_waits_for_approval_and_then_runs(client, docker_on):
    proposed = await client.post(
        f"/api/v1/hosts/{docker_on.host}/actions/docker.volume_remove",
        json={"payload": {"volume": "leer"}, "reason": "Aufräumen"}, headers=docker_on.headers,
    )
    assert proposed.status_code == 202, proposed.text
    assert proposed.json()["risk"] == "high" and not any("volume rm" in c for c in docker_on.exec.calls)
    approved = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=docker_on.headers)
    assert approved.status_code == 200 and approved.json()["status"] == "succeeded", approved.text
    assert docker_on.exec.calls[-1] == "docker volume rm leer"


@pytest.mark.asyncio
async def test_a_bad_volume_name_is_refused_when_proposed(client, db_session, docker_on):
    # Die Kern-Route prüft `params_schema` schon beim Vorschlag: 422, keine Aktion, nichts geht ans Gate.
    from sqlalchemy import func, select

    from nodvard_deck.models import Action

    refused = await client.post(
        f"/api/v1/hosts/{docker_on.host}/actions/docker.volume_remove",
        json={"payload": {"volume": "x; rm -rf /"}, "reason": "Test"}, headers=docker_on.headers,
    )
    assert refused.status_code == 422, refused.text
    assert "„volume“" in refused.json()["detail"]
    assert (await db_session.execute(select(func.count()).select_from(Action))).scalar_one() == 0
    assert docker_on.exec.calls == []


@pytest.mark.asyncio
async def test_a_bad_volume_name_never_reaches_the_server_even_when_approved(client, db_session, docker_on):
    # Die Prüfung des Ausführers gilt weiter, auch wenn ein Vorschlag nicht über die Route kommt (verbindlich).
    from nodvard_sdk import ActionRequest, Actor, ActorType, Risk

    from nodvard_deck.core import gate

    decision = await gate.propose(
        db_session, ext_id="core", command_field=None,
        request=ActionRequest(
            action_type="docker.volume_remove", payload={"volume": "x; rm -rf /"}, host_ref=docker_on.host,
            risk=Risk.HIGH, proposed_by=Actor(type=ActorType.AI, id="test-model"), reason="Test",
        ),
    )
    await db_session.commit()
    approved = await client.post(f"/api/v1/actions/{decision.action_id}/approve", headers=docker_on.headers)
    assert approved.json()["status"] == "failed", approved.text
    assert docker_on.exec.calls == []


# --- Der Lösch-Befehl für Netze in einer echten Shell ---------------------------------------------------------------

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$LOG"
case "$1 $2" in
  "network inspect") [ -n "$NAME" ] || { echo "Error: No such network: $5" >&2; exit 1; }; printf '%s\\n' "$NAME" ;;
  "ps -a") [ -z "$PS_FAILS" ] || exit 1
    case " $* " in *" --filter network=$MATCH "*) printf '%s\\n' "$USERS" ;; esac ;;
  "network rm") printf '%s\\n' "$3" ;;
esac
"""


def _run_network_remove(tmp_path, *, name="", users="", match="", ps_fails=""):
    from nodvard_deck_ext_docker import commands

    fake = tmp_path / "bin" / "docker"
    fake.parent.mkdir()
    fake.write_text(FAKE_DOCKER, encoding="utf-8")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}", "LOG": str(tmp_path / "log"),
           "NAME": name, "USERS": users, "MATCH": match or "-", "PS_FAILS": ps_fails}
    done = subprocess.run(
        ["sh", "-c", commands.network_remove(UNUSED_NET)], env=env, cwd=tmp_path, capture_output=True, text=True,
        check=False, timeout=10,
    )
    log = (tmp_path / "log").read_text(encoding="utf-8").splitlines() if (tmp_path / "log").exists() else []
    return done, log


needs_sh = pytest.mark.skipif(shutil.which("sh") is None or sys.platform == "win32", reason="braucht eine POSIX-Shell")


@needs_sh
def test_shell_removes_an_unused_network_and_names_it(tmp_path):
    done, log = _run_network_remove(tmp_path, name="ungenutzt")
    assert (done.returncode, done.stdout) == (0, "ungenutzt\n")
    assert log[-1] == f"network rm {UNUSED_NET}"


@needs_sh
def test_shell_keeps_a_network_with_a_never_started_container(tmp_path):
    from nodvard_deck_ext_docker import commands

    done, log = _run_network_remove(tmp_path, name="a,b", users="komma", match="a,b")
    assert (done.returncode, done.stdout) == (commands.NETWORK_IN_USE, "komma\n")
    assert not any(line.startswith("network rm") for line in log)


@needs_sh
@pytest.mark.parametrize("key", [UNUSED_NET, UNUSED_NET[:12]])
def test_shell_keeps_a_network_a_container_knows_by_its_id(tmp_path, key):
    from nodvard_deck_ext_docker import commands

    done, log = _run_network_remove(tmp_path, name="ungenutzt", users="per-api", match=key)
    assert (done.returncode, done.stdout) == (commands.NETWORK_IN_USE, "per-api\n")
    assert not any(line.startswith("network rm") for line in log)


@needs_sh
@pytest.mark.parametrize(("name", "ps_fails"), [("", ""), ("ungenutzt", "ja")])
def test_shell_removes_nothing_when_a_check_fails(tmp_path, name, ps_fails):
    done, log = _run_network_remove(tmp_path, name=name, ps_fails=ps_fails)
    assert done.returncode == 1 and not any(line.startswith("network rm") for line in log)


@needs_sh
def test_shell_never_runs_a_network_name(tmp_path):
    weird = "$(touch boese); `touch boese`"
    done, log = _run_network_remove(tmp_path, name=weird, users="x", match=weird)
    assert done.returncode == 3 and not (tmp_path / "boese").exists()
    assert any(f"--filter network={weird}" in line for line in log)
