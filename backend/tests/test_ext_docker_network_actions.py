"""Netze anlegen, Container verbinden und trennen: feste Argumente, Selbstschutz, verständliche Ablehnungen, nur über die Freigabe."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, docker_on, result  # noqa: F401 - Fixtures

NET = "b" * 64
CID = "c" * 64


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


class Ctx:
    def __init__(self, answers=None, tags=("docker",), error=None):
        self.argv_calls: list[list[str]] = []
        self.forgotten: list[str] = []
        self._answers = answers or {}
        self._error = error
        host = SimpleNamespace(id="h1", name="pi", tags=list(tags))
        self.hosts = SimpleNamespace(get=self._get(host))
        self.settings = SimpleNamespace(get=self._settings)
        self.exec = SimpleNamespace(run_argv=self._run_argv)
        self.resources = SimpleNamespace(forget=self.forgotten.append)

    @staticmethod
    def _get(host):
        async def get(ref):
            return host if ref == host.id else None
        return get

    async def _settings(self):
        return {}

    async def _run_argv(self, host, argv, *, stdin=None, timeout_s=60, log_command=None):
        self.argv_calls.append(list(argv))
        if self._error:
            raise self._error
        if argv[1] == "inspect":
            return self._answers.get("inspect", result(f"{CID}|bridge\n"))
        return self._answers.get(argv[2], result())


async def _run(ctx, action, **payload):
    from nodvard_deck_ext_docker.network_actions import DockerNetworkExecutor

    req = SimpleNamespace(action_type=action, host_ref="h1", payload=payload)
    return await DockerNetworkExecutor(ctx, ctx.resources).execute(req)


@pytest.mark.asyncio
@pytest.mark.parametrize(("internal", "flags"), [(True, ["--internal"]), (False, []), ("ja", [])])
async def test_create_with_fixed_arguments(internal, flags):
    ctx = Ctx()
    done = await _run(ctx, "docker.network_create", name="intern-1", internal=internal)
    assert done.success
    assert ctx.argv_calls == [["docker", "network", "create", "--driver", "bridge", *flags, "intern-1"]]
    assert done.output == ("Netz „intern-1“ angelegt (nur intern)." if flags else "Netz „intern-1“ angelegt.")
    assert ctx.forgotten == ["h1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["bridge", "host", "none", "default", "-d", "a b", "", "x" * 64, "ä"])
async def test_create_refuses_bad_names_without_a_command(name):
    ctx = Ctx()
    done = await _run(ctx, "docker.network_create", name=name)
    assert not done.success and "Der Name des Netzes ist ungültig" in done.error
    assert ctx.argv_calls == []


@pytest.mark.asyncio
async def test_connect_and_disconnect_name_the_full_id():
    ctx = Ctx()
    assert (await _run(ctx, "docker.network_connect", network=NET, container="shop-web-1")).output == "„shop-web-1“ mit dem Netz verbunden."
    done = await _run(ctx, "docker.network_disconnect", network=NET, container="shop-web-1")
    assert done.output == "„shop-web-1“ vom Netz getrennt."
    inspect = ["docker", "inspect", "--type", "container", "--format", "{{.Id}}|{{.HostConfig.NetworkMode}}", "shop-web-1"]
    assert ctx.argv_calls == [
        inspect, ["docker", "network", "connect", NET, CID],
        inspect, ["docker", "network", "disconnect", NET, CID],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("network", "container"), [("b" * 12, "web"), (NET.upper(), "web"), (NET, "-f"), (NET, "a;b"), (NET + "\n", "web")])
async def test_links_refuse_bad_values(network, container):
    ctx = Ctx()
    for action in ("docker.network_connect", "docker.network_disconnect"):
        done = await _run(ctx, action, network=network, container=container)
        assert not done.success and done.error.startswith("Ungültige")
    assert ctx.argv_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["docker.network_connect", "docker.network_disconnect"])
async def test_the_own_container_keeps_its_networks(monkeypatch, action):
    from nodvard_deck_ext_docker import network_actions

    monkeypatch.setattr(network_actions, "is_own_container", lambda cid: cid == CID)
    ctx = Ctx()
    done = await _run(ctx, action, network=NET, container="nodvard-dock")
    assert not done.success and "Container von Nodvard Deck selbst" in done.error
    assert [argv[1] for argv in ctx.argv_calls] == ["inspect"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "error"), [
    (result(""), "Die Angaben zum Container ließen sich nicht lesen. Nichts geändert."),
    (result("kaputt|bridge\n"), "Die Angaben zum Container ließen sich nicht lesen. Nichts geändert."),
    (result("c" * 12 + "|bridge\n"), "Die Angaben zum Container ließen sich nicht lesen. Nichts geändert."),
    (result(stderr="Error response from daemon: No such container: web\n", code=1), "Den Container gibt es nicht (mehr)."),
    (result(f"{CID}|none\n"), "Der Container nutzt kein eigenes Netz (network_mode: none)."),
    (result(f"{CID}|host\n"), "Der Container nutzt kein eigenes Netz (network_mode: host)."),
    (result(f"{CID}|container:{'d' * 64}\n"), "Der Container nutzt kein eigenes Netz (network_mode: container)."),
    (result(f"{CID}|service:db\n"), "Der Container nutzt kein eigenes Netz (network_mode: service)."),
])
async def test_nothing_changes_without_a_readable_container(answer, error):
    for action in ("docker.network_connect", "docker.network_disconnect"):
        ctx = Ctx(answers={"inspect": answer})
        done = await _run(ctx, action, network=NET, container="web")
        assert not done.success and done.error.startswith(error)
        assert [argv[1] for argv in ctx.argv_calls] == ["inspect"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "stderr", "plain"), [
    ("docker.network_create", "Error response from daemon: network with name intern already exists", "Ein Netz mit diesem Namen gibt es schon."),
    ("docker.network_connect", "Error response from daemon: endpoint with name web already exists in network intern", "Der Container hängt schon an diesem Netz."),
    ("docker.network_connect", "Error response from daemon: container cannot be disconnected from host network or connected to host network",
     "Am Netz host hängt ein Container ganz oder gar nicht"),
    ("docker.network_connect", "Error response from daemon: cannot connect container to host network - container must be created in host network mode",
     "Am Netz host hängt ein Container ganz oder gar nicht"),
    ("docker.network_disconnect", "Error response from daemon: cannot disconnect container from host network - container was created in host network mode",
     "Vom Netz host lässt sich ein Container nicht trennen"),
    ("docker.network_connect", "Error response from daemon: container cannot be connected to multiple networks with one of the networks in private (none) mode",
     "Der Container läuft ohne Netz"),
    ("docker.network_connect", "Error response from daemon: container sharing network namespace with another container or host cannot be connected to any other network",
     "Der Container nutzt das Netz seines Servers"),
    ("docker.network_connect", "Error response from daemon: Could not attach to network ingress: rpc error: network ingress not manually attachable",
     "An dieses Netz lassen sich Container nicht von Hand hängen"),
    ("docker.network_connect", "Error response from daemon: No such container: web", "Den Container gibt es nicht (mehr)."),
    ("docker.network_connect", "Error response from daemon: network " + NET + " not found", "Das Netz oder der Container ist nicht (mehr) da."),
    ("docker.network_disconnect", "Error response from daemon: container abc is not connected to network intern", "Der Container hängt nicht an diesem Netz."),
    ("docker.network_create", "Error response from daemon: etwas ganz Neues", "Error response from daemon: etwas ganz Neues"),
])
async def test_docker_refusals_in_plain_words(action, stderr, plain):
    verb = {"docker.network_create": "create", "docker.network_connect": "connect", "docker.network_disconnect": "disconnect"}[action]
    ctx = Ctx(answers={verb: result(stderr=stderr + "\n", code=1)})
    payload = {"name": "intern"} if verb == "create" else {"network": NET, "container": "web"}
    done = await _run(ctx, action, **payload)
    assert not done.success and done.error.startswith(plain) and done.exit_code == 1


@pytest.mark.asyncio
async def test_a_lost_connection_is_named_and_the_list_reloads():
    ctx = Ctx(error=ConnectionResetError("weg"))
    done = await _run(ctx, "docker.network_create", name="intern")
    assert not done.success and done.error.startswith("Server nicht erreichbar: weg.") and "Reiter Netze" in done.error
    assert ctx.forgotten == ["h1"]
    done = await _run(ctx, "docker.network_connect", network=NET, container="web")
    assert done.error == "Server nicht erreichbar: weg. Nichts geändert."


@pytest.mark.asyncio
@pytest.mark.parametrize("tags", [("docker-bedienen",), ("nas",)])
async def test_nothing_runs_on_other_servers(tags):
    ctx = Ctx(tags=tags)
    done = await _run(ctx, "docker.network_create", name="intern")
    assert not done.success and ctx.argv_calls == []


@pytest.mark.asyncio
async def test_dry_run_says_what_would_happen():
    from nodvard_deck_ext_docker.network_actions import DockerNetworkExecutor

    executor = DockerNetworkExecutor(Ctx(), SimpleNamespace(forget=lambda h: None))
    report = await executor.dry_run(SimpleNamespace(action_type="docker.network_disconnect", payload={"network": NET, "container": "web"}))
    assert report.would_change and report.summary == f"Würde „web“ vom Netz {NET[:12]} trennen."


@pytest.mark.asyncio
async def test_through_the_gate(client, docker_on):
    url = f"/api/v1/hosts/{docker_on.host}/actions/docker.network_create"
    bad = await client.post(url, json={"payload": {"name": "a b"}, "reason": "Test"}, headers=docker_on.owner)
    assert bad.status_code in (400, 422)
    assert (await client.post(url, json={"payload": {"name": "intern"}, "reason": "Test"}, headers=docker_on.viewer)).status_code == 403
    proposed = await client.post(url, json={"payload": {"name": "intern", "internal": True}, "reason": "Test"}, headers=docker_on.owner)
    assert proposed.status_code == 202 and proposed.json()["status"] == "proposed"
    assert docker_on.exec.argv_calls == []
    docker_on.exec.verb = result(NET + "\n")
    done = (await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=docker_on.owner)).json()
    assert done["status"] == "succeeded" and done["result"]["output"] == "Netz „intern“ angelegt (nur intern)."
    assert docker_on.exec.argv_calls[-1] == ["docker", "network", "create", "--driver", "bridge", "--internal", "intern"]

    linked = await client.post(f"/api/v1/hosts/{docker_on.host}/actions/docker.network_connect",
                               json={"payload": {"network": NET, "container": "shop-web-1"}, "reason": "Test"}, headers=docker_on.owner)
    docker_on.exec.argv_calls.clear()
    docker_on.exec.verb = result(f"{CID}|bridge\n")
    done = (await client.post(f"/api/v1/actions/{linked.json()['id']}/approve", headers=docker_on.owner)).json()
    assert done["status"] == "succeeded" and docker_on.exec.argv_calls[-1] == ["docker", "network", "connect", NET, CID]


def test_the_network_list_names_every_container():
    from nodvard_deck_ext_docker.resources import parse_containers

    df = {"Containers": [
        {"Names": "shop-web-1", "ID": "e" * 12, "Labels": "com.docker.compose.project=shop,x=1"},
        {"Names": "solo", "Labels": ""},
        {"Names": "solo"},
        {"Names": "a b"},
        "kaputt",
    ]}
    assert parse_containers(df) == [
        {"name": "shop-web-1", "compose_project": "shop", "self": False}, {"name": "solo", "compose_project": None, "self": False},
    ]
    assert json.dumps(parse_containers({})) == "[]"
