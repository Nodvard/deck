"""Image holen: feste Argumente, geprüfte Referenz, verständliche Gründe, nur über die Freigabe."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, docker_on, result  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


class Ctx:
    def __init__(self, answer=None, tags=("docker",), error=None):
        self.argv_calls: list[list[str]] = []
        self.timeouts: list[int] = []
        self.forgotten: list[str] = []
        self._answer = answer or result("docker.io/library/nginx:1.27\n")
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
        self.timeouts.append(timeout_s)
        if self._error:
            raise self._error
        return self._answer


async def _run(ctx, image, action="docker.image_pull"):
    from nodvard_deck_ext_docker.image_pull import DockerImagePullExecutor

    req = SimpleNamespace(action_type=action, host_ref="h1", payload={"image": image})
    return await DockerImagePullExecutor(ctx, ctx.resources).execute(req)


@pytest.mark.asyncio
@pytest.mark.parametrize("image", ["nginx", "nginx:1.27", "ghcr.io/besitzer/app:1.0", "registry.local:5000/team/app:v2",
                                   "nginx@sha256:" + "a" * 64])
async def test_pull_with_fixed_arguments(image):
    ctx = Ctx()
    done = await _run(ctx, image)
    assert done.success and done.output == f"Image „{image}“ geholt."
    assert ctx.argv_calls == [["docker", "pull", "-q", image]]
    assert ctx.timeouts == [1800]
    assert ctx.forgotten == ["h1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("image", ["", "-a", "--all-tags", "Nginx", "nginx latest", "nginx;id", "nginx\n", "$(id)", "nginx:" + "x" * 129,
                                   "a/" * 128 + "b", "nginx:-1", "@sha256:" + "a" * 64, "nginx@sha256:abc", "a" * 256])
async def test_bad_references_run_nothing(image):
    ctx = Ctx()
    done = await _run(ctx, image)
    assert not done.success and done.error.startswith("Der Name des Images ist ungültig")
    assert ctx.argv_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("stderr", "plain"), [
    ("Error response from daemon: toomanyrequests: You have reached your unauthenticated pull rate limit.", "Die Registry lässt gerade keine"),
    ("Error response from daemon: pull access denied for besitzer/privat, repository does not exist or may require 'docker login'",
     "Die Registry gibt das Image nicht heraus"),
    ("Error response from daemon: manifest for nginx:9.9.9 not found: manifest unknown: manifest unknown", "Diesen Tag gibt es"),
    ("Error response from daemon: Get \"https://registry-1.docker.io/v2/\": dial tcp: lookup registry-1.docker.io: no such host",
     "Die Registry ist vom Server aus nicht erreichbar"),
    ("failed to register layer: write /var/lib/docker/x: no space left on device", "Auf dem Server ist kein Platz mehr frei"),
    ("Error response from daemon: no matching manifest for linux/arm64/v8 in the manifest list entries", "Das Image gibt es nicht für die Bauart"),
    ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?", "Docker läuft auf dem Server nicht"),
    ("permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock", "Docker läuft auf dem Server nicht"),
    ("Error response from daemon: Get \"https://192.0.2.5:5000/v2/\": http: server gave HTTP response to HTTPS client",
     "Die Registry spricht nur HTTP"),
    ("Error response from daemon: Get \"https://registry.lan/v2/\": tls: failed to verify certificate: x509: certificate signed by unknown authority",
     "Die Registry spricht nur HTTP"),
    ("invalid reference format", "Docker versteht den Namen nicht"),
    ("Error response from daemon: etwas ganz Neues", "Docker meldet: Error response from daemon: etwas ganz Neues"),
])
async def test_registry_refusals_in_plain_words(stderr, plain):
    ctx = Ctx(answer=result(stderr=stderr + "\n", code=1))
    done = await _run(ctx, "nginx:1.27")
    assert not done.success and done.error.startswith(plain) and done.exit_code == 1
    assert ctx.forgotten == ["h1"]


@pytest.mark.asyncio
async def test_timeout_and_lost_connection_are_named():
    from nodvard_deck.core.ssh import SshTimeout

    ctx = Ctx(error=TimeoutError())
    done = await _run(ctx, "nginx:1.27")
    assert done.error.startswith("Nach 30 Minuten ohne Ende abgebrochen.") and "Reiter Images" in done.error
    assert ctx.forgotten == ["h1"]
    done = await _run(Ctx(error=SshTimeout("Der Server antwortet nicht. Ist er an und im Netz?")), "nginx:1.27")
    assert done.error.startswith("Server nicht erreichbar: Der Server antwortet nicht. Ist er an und im Netz? Ob Docker")
    ctx = Ctx(error=ConnectionResetError("weg"))
    done = await _run(ctx, "nginx:1.27")
    assert done.error.startswith("Server nicht erreichbar: weg. Ob Docker") and ctx.forgotten == ["h1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tags", [("docker-bedienen",), ("nas",)])
async def test_nothing_runs_on_other_servers(tags):
    ctx = Ctx(tags=tags)
    done = await _run(ctx, "nginx:1.27")
    assert not done.success and ctx.argv_calls == []


@pytest.mark.asyncio
async def test_unknown_action_and_dry_run():
    from nodvard_deck_ext_docker.image_pull import DockerImagePullExecutor

    ctx = Ctx()
    assert not (await _run(ctx, "nginx", action="docker.image_remove")).success and ctx.argv_calls == []
    executor = DockerImagePullExecutor(ctx, ctx.resources)
    report = await executor.dry_run(SimpleNamespace(action_type="docker.image_pull", payload={"image": "nginx:1.27"}))
    assert report.would_change and report.summary == "Würde das Image „nginx:1.27“ holen."


@pytest.mark.asyncio
async def test_through_the_gate(client, docker_on):
    url = f"/api/v1/hosts/{docker_on.host}/actions/docker.image_pull"
    for image in ("-a", "a" * 256):
        bad = await client.post(url, json={"payload": {"image": image}, "reason": "Test"}, headers=docker_on.owner)
        assert bad.status_code in (400, 422)
    other = f"/api/v1/hosts/{docker_on.other}/actions/docker.image_pull"
    assert (await client.post(other, json={"payload": {"image": "nginx"}, "reason": "Test"}, headers=docker_on.owner)).status_code in (400, 403, 404, 422)
    assert (await client.post(url, json={"payload": {"image": "nginx"}, "reason": "Test"}, headers=docker_on.viewer)).status_code == 403
    proposed = await client.post(url, json={"payload": {"image": "nginx:1.27"}, "reason": "Test"}, headers=docker_on.owner)
    assert proposed.status_code == 202 and proposed.json()["status"] == "proposed"
    assert docker_on.exec.argv_calls == []
    docker_on.exec.verb = result("docker.io/library/nginx:1.27\n")
    done = (await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=docker_on.owner)).json()
    assert done["status"] == "succeeded" and done["result"]["output"] == "Image „nginx:1.27“ geholt."
    assert docker_on.exec.argv_calls[-1] == ["docker", "pull", "-q", "nginx:1.27"]
