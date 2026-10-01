"""Image-Updates pruefen (service-matrix, Teil C) -- Fake-`ctx` mit einem kleinen
`docker`, das die Befehle der Extension wirklich liest (shlex) und Beispielausgaben
liefert. Die echte Kette ueber SSH steht am Ende von `test_ext_service_matrix.py`.

Die Ausgabeformate stammen aus der Docker-Dokumentation bzw. aus Ausgaben, die wir von
Docker 29 auf dem Pi kennen -- gegen echte Registries ist das hier NICHT geprueft."""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "extensions" / "service-matrix" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_deck_ext_service_matrix import image_updates as iu
from nodvard_deck_ext_service_matrix.image_updates import (
    CURRENT,
    LOCAL,
    UNKNOWN,
    UPDATE,
    ImageUpdateService,
)


def dg(char: str) -> str:
    """Ein Digest, an dem man erkennt, welcher gemeint ist."""
    return "sha256:" + char * 64


LOCAL_REASON = "Selbst gebautes Image – es gibt dafür keine Registry, Updates kommen über den eigenen Build (beim Dashboard: Deploy)."
DENIED = "pull access denied for lattice, repository does not exist or may require 'docker login': denied"
UNAUTHORIZED = "unauthorized: authentication required"
NOT_FOUND = "ERROR: docker.io/library/lattice:latest: not found"


# --- Namen und Befehle -----------------------------------------------------------------

GOOD_REFS = [
    "nginx",
    "nginx:1.27-alpine",
    "pihole/pihole:latest",
    "ghcr.io/linuxserver/sonarr:latest",
    "lscr.io/linuxserver/radarr:5.2.6.8376-ls1",
    "localhost:5000/team/app:v1",
    "mcr.microsoft.com/dotnet/aspnet:8.0",
    "docker.io/library/redis:7",
    "registry.example.org:8443/a/b_c/d-e.f:1.0",
    "quay.io/prometheus/node-exporter@" + dg("a"),
    "nginx:1.27@" + dg("b"),
]
BAD_REFS = [
    "", " ", "x; rm -rf /", "$(reboot)", "`id`", "a b", "-f", "--help", "../etc", "nginx\nls", "nginx|cat",
    "Nginx:1", "nginx:", ":tag", "nginx:a:b", "a" * 300, "nginx@sha256:abc", "nginx&", "nginx'x", 'nginx"x',
    "-v", "nginx:tag with space", "/nginx", "nginx/", "a//b", "http://nginx",
    "nginx\n", "nginx:1.27\n", "nginx\r", "nginx@" + "sha256:" + "a" * 64 + "\n",
]


@pytest.mark.parametrize("ref", GOOD_REFS)
def test_ref_regex_accepts_real_image_names(ref):
    assert iu.is_valid_ref(ref)


@pytest.mark.parametrize("ref", BAD_REFS)
def test_ref_regex_rejects_everything_a_shell_could_use(ref):
    assert not iu.is_valid_ref(ref)


def test_ref_regex_stays_fast_on_hostile_input():
    started = time.monotonic()
    for hostile in ("a" * 250 + "!", "a-" * 120 + "!", "a." * 120 + "/!", "a" * 100 + ".b" * 60 + ":!", "0" * 250 + "@"):
        iu.is_valid_ref(hostile)
    assert time.monotonic() - started < 0.5


def test_command_builders_quote_and_check_by_themselves():
    assert shlex.split(iu.cmd_local_inspect("nginx:1.27")) == [
        "docker", "image", "inspect", "--format", '{"repo_digests": {{json .RepoDigests}}, "id": {{json .Id}}}', "nginx:1.27",
    ]
    assert shlex.split(iu.cmd_local_inspect(dg("c")))[-1] == dg("c")
    assert shlex.split(iu.cmd_remote_imagetools("ghcr.io/x/y:latest")) == [
        "docker", "buildx", "imagetools", "inspect", "ghcr.io/x/y:latest", "--format", "{{json .Manifest.Digest}}",
    ]
    assert shlex.split(iu.cmd_remote_manifest("nginx:latest")) == ["docker", "manifest", "inspect", "-v", "nginx:latest"]
    assert shlex.split(iu.cmd_inspect_containers(["0123456789ab", "f" * 64]))[3:5] == ["--format", "{{.Name}}|{{.Config.Image}}|{{.Image}}"]
    # Die Bauer pruefen selbst -- egal, ob der Aufrufer vorher geprueft hat.
    for bad in ("x; rm -rf /", "$(id)", "-f", "a b", "nginx`id`"):
        for build in (iu.cmd_local_inspect, iu.cmd_remote_imagetools, iu.cmd_remote_manifest):
            with pytest.raises(ValueError):
                build(bad)
    for bad_id in ("abc", "0123456789ab; id", "XYZ" * 5, "-a" + "0" * 12, "0123456789ab\n"):
        with pytest.raises(ValueError):
            iu.cmd_inspect_containers([bad_id])


def test_ref_analysis_names_the_reason_when_a_check_makes_no_sense():
    assert iu.analyze_ref("nginx").query == "nginx:latest"
    assert iu.analyze_ref("ghcr.io/x/y:1.2").query == "ghcr.io/x/y:1.2"
    assert (iu.analyze_ref("nginx").registry, iu.analyze_ref("ghcr.io/x/y").registry, iu.analyze_ref("localhost:5000/a").registry) == (
        "docker.io", "ghcr.io", "localhost:5000",
    )
    assert "Digest" in (iu.analyze_ref("nginx@" + dg("a")).problem or "")
    assert "ID" in (iu.analyze_ref("3f2a9c1b7d4e").problem or "")
    assert "ID" in (iu.analyze_ref(dg("a")).problem or "")
    assert "ohne Namen" in (iu.analyze_ref("<none>").problem or "")
    assert "Format" in (iu.analyze_ref("x; rm -rf /").problem or "")


def test_hub_names_match_in_every_spelling():
    assert iu.canonical_repo("docker.io/library/nginx") == iu.canonical_repo("nginx") == "nginx"
    assert iu.canonical_repo("library/nginx") == "nginx"
    assert iu.canonical_repo("myreg.io/library/nginx") == "myreg.io/library/nginx"
    digests = ("nginx@" + dg("a"), "ghcr.io/other/nginx@" + dg("b"))
    assert iu.digests_for_repo(digests, "docker.io/library/nginx") == {dg("a")}
    assert iu.digests_for_repo(digests, "ghcr.io/other/nginx") == {dg("b")}
    assert iu.digests_for_repo(digests, "quay.io/nginx") == set()


@pytest.mark.parametrize(
    "repo, official",
    [
        ("lattice", True),
        ("library/lattice", True),
        ("docker.io/library/lattice", True),
        ("index.docker.io/library/lattice", True),
        ("registry-1.docker.io/library/lattice", True),
        ("docker.io/lattice", True),
        ("nico/lattice", False),
        ("docker.io/nico/lattice", False),
        ("ghcr.io/lattice", False),
        ("ghcr.io/x/lattice", False),
        ("registry.local:5000/lattice", False),
        ("localhost:5000/lattice", False),
        ("localhost/lattice", False),
        ("myreg.io/library/lattice", False),
    ],
)
def test_official_docker_hub_names_are_the_ones_without_namespace_and_registry(repo, official):
    """Nur ein Hub-Name ohne Namespace ist ein offizielles `library/`-Image -- und das ist
    immer oeffentlich; verweigert die Registry den Zugriff, kann es nur selbst gebaut sein."""
    assert iu.is_official_hub_repo(repo) is official


def test_official_hub_check_works_on_analyzed_refs():
    for ref in ("lattice", "lattice:latest", "docker.io/library/lattice:1", "library/lattice"):
        assert iu.is_official_hub_repo(iu.analyze_ref(ref).repo), ref
    for ref in ("nico/lattice:latest", "ghcr.io/x/y", "registry.local:5000/x:2"):
        assert not iu.is_official_hub_repo(iu.analyze_ref(ref).repo), ref


def test_local_is_a_fourth_status_next_to_the_others():
    assert (CURRENT, UPDATE, UNKNOWN, LOCAL) == ("current", "update", "unknown", "local")
    assert len({CURRENT, UPDATE, UNKNOWN, LOCAL}) == 4
    assert iu.LOCAL_REASON == LOCAL_REASON
    assert iu.TRANSIENT.isdisjoint({"auth", "not_found"}), "auth/not_found sperren die Registry nicht"
    # Nur "Zugriff verweigert" deutet auf selbst gebaut: Docker Hub antwortet auf ein Repository,
    # das es gar nicht gibt (library/lattice), mit 401/denied. Ein entfernter Tag eines echten
    # Images (nginx:1.27) heisst dagegen "manifest unknown" (not_found).
    assert iu.SELF_BUILT_HINTS == {"auth"}


# --- Ausgaben lesen --------------------------------------------------------------------


def manifest_list_output() -> str:
    """`docker manifest inspect -v` eines Multi-Arch-Images: Liste mit einem Eintrag je Plattform."""
    def entry(arch: str, manifest: str, config: str, variant: str | None = None) -> dict:
        platform = {"architecture": arch, "os": "linux", **({"variant": variant} if variant else {})}
        return {
            "Ref": f"docker.io/library/nginx:1.27@{manifest}",
            "Descriptor": {"mediaType": "application/vnd.docker.distribution.manifest.v2+json", "digest": manifest, "size": 1570, "platform": platform},
            "SchemaV2Manifest": {
                "schemaVersion": 2, "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
                "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "size": 7300, "digest": config},
                "layers": [{"mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip", "size": 29, "digest": dg("9")}],
            },
        }
    return json.dumps([entry("amd64", dg("1"), dg("2")), entry("arm64", dg("3"), dg("4"), "v8"), entry("arm", dg("5"), dg("6"), "v7")], indent="\t")


def test_manifest_inspect_reads_only_the_hosts_platform():
    answer = iu.parse_manifest_inspect(manifest_list_output(), "linux/arm64")
    assert answer is not None
    assert answer.digest == dg("3")
    assert answer.matches == {dg("3"), dg("4")}
    assert iu.parse_manifest_inspect(manifest_list_output(), "linux/amd64").matches == {dg("1"), dg("2")}
    assert iu.parse_manifest_inspect(manifest_list_output(), "linux/riscv64") is None


def test_manifest_inspect_single_image_and_oci_layout():
    single = {"Descriptor": {"digest": dg("7"), "size": 1}, "OCIManifest": {"config": {"digest": dg("8")}}}
    answer = iu.parse_manifest_inspect(json.dumps(single), "linux/arm64")
    assert answer is not None and answer.matches == {dg("7"), dg("8")}
    assert iu.parse_manifest_inspect("kein json", "linux/arm64") is None
    assert iu.parse_manifest_inspect("[]", "linux/arm64") is None


def test_imagetools_digest_and_local_image_parsing():
    assert iu.parse_imagetools_digest(f'"{dg("a")}"\n') == dg("a")
    assert iu.parse_imagetools_digest("null") is None
    assert iu.parse_imagetools_digest("sha256:kurz") is None
    local = iu.parse_local_image(json.dumps({"repo_digests": ["nginx@" + dg("a")], "id": dg("1")}))
    assert local == iu.LocalImage(("nginx@" + dg("a"),), dg("1"))
    # Aeltere Docker-Versionen liefern null statt einer leeren Liste.
    assert iu.parse_local_image('{"repo_digests": null, "id": "' + dg("1") + '"}') == iu.LocalImage((), dg("1"))
    assert iu.parse_local_image("[]") is None and iu.parse_local_image("Fehler") is None


@pytest.mark.parametrize(
    "stderr, kind",
    [
        ("toomanyrequests: You have reached your unauthenticated pull rate limit. https://www.docker.com/increase-rate-limit", "rate_limit"),
        ("Error response from daemon: 429 Too Many Requests", "rate_limit"),
        ("unauthorized: authentication required", "auth"),
        ("pull access denied for x, repository does not exist or may require 'docker login': denied", "auth"),
        ("ERROR: failed to authorize: 403 Forbidden", "auth"),
        ("no such manifest: docker.io/library/nope:latest", "not_found"),
        ("manifest unknown: manifest unknown", "not_found"),
        ('Get "https://registry-1.docker.io/v2/": dial tcp: lookup registry-1.docker.io: no such host', "network"),
        ("net/http: TLS handshake timeout", "network"),
        ("ERROR: docker.io/library/nope:latest: not found", "not_found"),
        # Ein fehlendes Hilfsprogramm ist weder "nicht gefunden" noch "Anmeldung verweigert".
        ('error getting credentials - err: exec: "docker-credential-desktop": executable file not found in $PATH, out: ``', "credentials"),
        ('exec: "docker-credential-pass": executable file not found in $PATH', "credentials"),
        ("sh: 1: docker: command not found", "error"),
        ("permission denied while trying to connect to the Docker daemon socket", "error"),
        ("etwas ganz anderes\nzweite Zeile", "error"),
    ],
)
def test_registry_errors_get_a_readable_reason(stderr, kind):
    problem = iu.classify_registry_error(stderr)
    assert problem.kind == kind
    assert problem.message


def test_unknown_registry_error_keeps_the_first_line_only():
    assert iu.classify_registry_error("etwas ganz anderes\nzweite Zeile").message == "Abfrage fehlgeschlagen: etwas ganz anderes"
    assert iu.classify_registry_error("", 3).message == "Abfrage fehlgeschlagen: Exit-Code 3"


# --- Der Dienst gegen ein Fake-Docker --------------------------------------------------


class FakeResult:
    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.exit_code, self.stdout, self.stderr, self.duration_ms = exit_code, stdout, stderr, 1


class FakeDocker:
    """Antwortet auf genau die Befehle der Extension; alles andere waere ein Fehler im Test."""

    def __init__(self) -> None:
        self.containers: dict[str, tuple[str, str]] = {}  # Name -> (Image-Name, Image-ID)
        self.images: dict[str, tuple[list[str], str]] = {}  # Ziel (ID oder Name) -> (RepoDigests, ID)
        self.remote: dict[str, object] = {}  # Anfrage -> Digest | ("err", stderr) | Exception
        self.manifests: dict[str, object] = {}
        self.buildx = True
        self.platform = "linux/arm64"
        self.down: Exception | None = None
        self.calls: list[str] = []

    @staticmethod
    def cid(name: str) -> str:
        return hashlib.sha256(name.encode()).hexdigest()[:12]

    def add(self, name: str, ref: str, *, image_id: str, repo_digests: list[str], remote: object = None) -> None:
        self.containers[name] = (ref, image_id)
        self.images[image_id] = (repo_digests, image_id)
        if remote is not None:
            self.remote[iu.analyze_ref(ref).query or ref] = remote

    def registry_calls(self) -> list[str]:
        return [c for c in self.calls if " imagetools " in c or " manifest " in c]

    async def run(self, command: str) -> FakeResult:
        self.calls.append(command)
        if self.down:
            raise self.down
        argv = shlex.split(command)
        if argv[:3] == ["docker", "ps", "-q"]:
            return FakeResult(stdout="".join(self.cid(n) + "\n" for n in self.containers))
        if argv[:3] == ["docker", "container", "inspect"]:
            by_id = {self.cid(n): n for n in self.containers}
            lines = [f"/{by_id[i]}|{self.containers[by_id[i]][0]}|{self.containers[by_id[i]][1]}" for i in argv[5:] if i in by_id]
            return FakeResult(stdout="\n".join(lines) + "\n")
        if argv[:3] == ["docker", "image", "inspect"]:
            found = self.images.get(argv[-1])
            if found is None:
                return FakeResult(1, "", f"Error response from daemon: No such image: {argv[-1]}")
            return FakeResult(stdout=json.dumps({"repo_digests": found[0], "id": found[1]}) + "\n")
        if argv[:4] == ["docker", "buildx", "imagetools", "inspect"]:
            if not self.buildx:
                return FakeResult(1, "", "docker: 'buildx' is not a docker command.\nSee 'docker --help'")
            return self._answer(self.remote, argv[4], lambda v: json.dumps(v) + "\n")
        if argv[:4] == ["docker", "manifest", "inspect", "-v"]:
            return self._answer(self.manifests, argv[4], lambda v: str(v))
        if argv[:2] == ["docker", "version"]:
            return FakeResult(stdout=self.platform + "\n")
        raise AssertionError(f"Unerwarteter Befehl: {command}")

    @staticmethod
    def _answer(table: dict, query: str, render) -> FakeResult:
        value = table.get(query)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return FakeResult(1, "", value[1])
        if value is None:
            return FakeResult(1, "", "no such manifest: " + query)
        return FakeResult(stdout=render(value))


class FakeHost:
    def __init__(self, host_id: str, name: str) -> None:
        self.id, self.name, self.display_name, self.address, self.tags = host_id, name, name, "10.0.0.5", ["docker"]


class Ctx:
    def __init__(self, tmp_path: Path, hosts: dict[str, FakeDocker], names: dict[str, str] | None = None) -> None:
        self.data_dir = tmp_path
        self.docker = hosts
        self.sent: list = []
        host_objects = [FakeHost(hid, (names or {}).get(hid, hid)) for hid in hosts]

        outer = self

        class _Settings:
            async def get(self) -> dict:
                return {}

        class _Hosts:
            async def list(self, *, tag: str | None = None):
                return host_objects

        class _Exec:
            async def run(self, host, command: str, *, timeout_s: int = 60):
                return await outer.docker[host.id].run(command)

        class _Notify:
            async def send(self, notification) -> None:
                outer.sent.append(notification)

        self.settings, self.hosts, self.exec, self.notify = _Settings(), _Hosts(), _Exec(), _Notify()
        self.host_objects = host_objects


def make(tmp_path, docker: FakeDocker | dict[str, FakeDocker], names: dict[str, str] | None = None) -> tuple[ImageUpdateService, Ctx]:
    ctx = Ctx(tmp_path, docker if isinstance(docker, dict) else {"h1": docker}, names)
    return ImageUpdateService(ctx), ctx  # type: ignore[arg-type]


def result_of(service: ImageUpdateService, host_id: str, container: str) -> dict:
    return service.snapshot()["data"][f"{host_id}:{container}"]


@pytest.mark.asyncio
async def test_up_to_date_update_and_not_checkable_in_one_run(tmp_path):
    docker = FakeDocker()
    docker.add("nginx", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("a"))
    docker.add("pihole", "pihole/pihole:latest", image_id=dg("2"), repo_digests=["pihole/pihole@" + dg("b")], remote=dg("c"))
    docker.add("lattice", "lattice:latest", image_id=dg("3"), repo_digests=[])  # lokal gebaut
    docker.add("db", "postgres@" + dg("d"), image_id=dg("4"), repo_digests=["postgres@" + dg("d")])  # fest verdrahtet
    docker.add("orphan", "3f2a9c1b7d4e", image_id=dg("5"), repo_digests=[])  # nur eine ID
    service, ctx = make(tmp_path, docker)

    await service.check_host(ctx.host_objects[0])

    assert result_of(service, "h1", "nginx") | {"registry_at": None} == {
        "image": "nginx:1.27", "status": CURRENT, "reason": None, "remote_digest": dg("a"), "registry_at": None, "stale": False,
    }
    update = result_of(service, "h1", "pihole")
    assert (update["status"], update["remote_digest"], update["registry_at"] is not None) == (UPDATE, dg("c"), True)
    assert result_of(service, "h1", "lattice")["status"] == LOCAL
    assert result_of(service, "h1", "lattice")["reason"] == LOCAL_REASON
    assert "Digest" in result_of(service, "h1", "db")["reason"]
    assert "ID" in result_of(service, "h1", "orphan")["reason"]
    # Lokal gebaute, festgelegte und namenlose Images gehen gar nicht erst zur Registry.
    assert sorted(shlex.split(c)[4] for c in docker.registry_calls()) == ["nginx:1.27", "pihole/pihole:latest"]
    assert not any("3f2a9c1b7d4e" in c or "postgres" in c for c in docker.calls if "inspect" in c and "container" not in c)
    assert service.snapshot()["hosts"]["h1"] == {"checking": False, "checked_at": service.snapshot()["hosts"]["h1"]["checked_at"], "error": None}


@pytest.mark.asyncio
async def test_only_read_only_docker_commands_are_ever_sent(tmp_path):
    docker = FakeDocker()
    docker.add("nginx", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("b"))
    docker.buildx = False
    docker.manifests["nginx:1.27"] = manifest_list_output()
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0], force=True)
    allowed = (
        "docker ps -q", "docker container inspect ", "docker image inspect ", "docker buildx imagetools inspect ",
        "docker manifest inspect -v ", "docker version --format ",
    )
    assert docker.calls and all(c.startswith(allowed) for c in docker.calls), docker.calls
    forbidden = (" pull", " run ", " rm ", " restart", " stop", " start", " kill", " exec ", " prune", " create", " compose", "sudo")
    assert not any(word in c for c in docker.calls for word in forbidden)


@pytest.mark.asyncio
async def test_check_uses_the_image_the_container_really_runs_not_what_the_name_shows_now(tmp_path):
    """Nach `docker pull` ohne Neustart zeigt der Name schon aufs neue Image -- der Container
    laeuft aber noch mit dem alten. Lokal wird deshalb die ID des Containers gelesen."""
    docker = FakeDocker()
    docker.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("b"))
    docker.images["nginx:1.27"] = (["nginx@" + dg("b")], dg("2"))  # der Name zeigt schon aufs neue Image
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert result_of(service, "h1", "web")["status"] == UPDATE
    assert not any(c.startswith("docker image inspect") and shlex.split(c)[-1] == "nginx:1.27" for c in docker.calls)


@pytest.mark.asyncio
async def test_repo_digests_of_another_registry_do_not_count(tmp_path):
    docker = FakeDocker()
    docker.add("web", "ghcr.io/x/web:1", image_id=dg("1"), repo_digests=["docker.io/x/web@" + dg("a")], remote=dg("a"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    result = result_of(service, "h1", "web")
    assert result["status"] == UNKNOWN and "anderen Registry" in result["reason"]
    assert docker.registry_calls() == []


@pytest.mark.asyncio
async def test_docker_hub_spellings_and_untagged_names_are_matched(tmp_path):
    docker = FakeDocker()
    docker.add("cache", "docker.io/library/redis", image_id=dg("1"), repo_digests=["redis@" + dg("a")], remote=dg("a"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert result_of(service, "h1", "cache")["status"] == CURRENT
    assert shlex.split(docker.registry_calls()[0])[4] == "docker.io/library/redis:latest"


@pytest.mark.asyncio
async def test_manifest_error_and_auth_error_are_reported_per_container(tmp_path):
    docker = FakeDocker()
    docker.add("gone", "ghcr.io/x/gone:1", image_id=dg("1"), repo_digests=["ghcr.io/x/gone@" + dg("a")],
               remote=("err", "manifest unknown: manifest unknown"))
    docker.add("private", "registry.example.org/app:2", image_id=dg("2"), repo_digests=["registry.example.org/app@" + dg("b")],
               remote=("err", "unauthorized: authentication required"))
    docker.add("fine", "quay.io/a/b:3", image_id=dg("3"), repo_digests=["quay.io/a/b@" + dg("c")], remote=dg("c"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert "nicht gefunden" in result_of(service, "h1", "gone")["reason"]
    assert "docker login" in result_of(service, "h1", "private")["reason"]
    assert result_of(service, "h1", "fine")["status"] == CURRENT
    assert result_of(service, "h1", "gone")["status"] == UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["lattice:latest", "lattice", "nico/private:latest", "ghcr.io/x/y:1", "registry.local:5000/x"])
async def test_an_image_without_repo_digests_is_self_built_whatever_its_name(tmp_path, ref):
    docker = FakeDocker()
    docker.add("mine", ref, image_id=dg("1"), repo_digests=[])
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    result = result_of(service, "h1", "mine")
    assert result["status"] == LOCAL and result["reason"] == LOCAL_REASON
    assert result["remote_digest"] is None and result["stale"] is False
    assert docker.registry_calls() == [], "ohne RepoDigests wird die Registry gar nicht gefragt"


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["lattice:latest", "lattice", "docker.io/library/lattice:latest", "library/lattice:1", "docker.io/lattice"])
@pytest.mark.parametrize(
    "answer", [DENIED, UNAUTHORIZED, "requested access to the resource is denied", "ERROR: failed to authorize: 403 Forbidden"],
    ids=["denied", "401", "resource-denied", "403"],
)
async def test_an_official_looking_name_the_registry_refuses_is_self_built(tmp_path, ref, answer):
    """Live gesehen: `lattice:latest` wird per `docker load` auf den Pi gebracht und hat mit dem
    containerd-Image-Speicher trotzdem RepoDigests. Docker Hub antwortet auf `library/lattice`
    (das Repository gibt es gar nicht) mit 401/"denied" -- jedes echte offizielle Image ist
    oeffentlich, also kann das nur selbst gebaut sein."""
    docker = FakeDocker()
    docker.add("lattice-1", ref, image_id=dg("1"), repo_digests=["lattice@" + dg("a")], remote=("err", answer))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    result = result_of(service, "h1", "lattice-1")
    assert result["status"] == LOCAL
    assert result["reason"] == LOCAL_REASON
    assert "docker login" not in result["reason"]
    assert len(docker.registry_calls()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["nginx:1.27", "nginx", "docker.io/library/nginx:1.27", "library/nginx:1"])
@pytest.mark.parametrize("answer", ["manifest unknown: manifest unknown", NOT_FOUND, "no such manifest: docker.io/library/nginx:1.27"])
async def test_an_official_name_with_a_removed_tag_stays_not_checkable(tmp_path, ref, answer):
    """Ein echtes offizielles Image, dessen Tag bei Docker Hub entfernt wurde, antwortet mit
    "manifest unknown" (nicht 401): das ist KEIN selbst gebautes Image, sondern ein Befund."""
    docker = FakeDocker()
    docker.add("web", ref, image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=("err", answer))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    result = result_of(service, "h1", "web")
    assert result["status"] == UNKNOWN
    assert result["reason"] == "Image bei der Registry nicht gefunden"


@pytest.mark.asyncio
async def test_the_same_official_name_is_self_built_when_denied_but_not_checkable_when_not_found(tmp_path):
    for answer, status in ((DENIED, LOCAL), ("manifest unknown: manifest unknown", UNKNOWN)):
        docker = FakeDocker()
        docker.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=("err", answer))
        service, ctx = make(tmp_path, docker)
        await service.check_host(ctx.host_objects[0])
        assert result_of(service, "h1", "web")["status"] == status, answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repo, digest_name",
    [
        ("nico/private", "nico/private"),
        ("docker.io/nico/private", "nico/private"),
        ("ghcr.io/x/y", "ghcr.io/x/y"),
        ("ghcr.io/x", "ghcr.io/x"),
        ("registry.local:5000/x", "registry.local:5000/x"),
    ],
)
async def test_other_names_keep_the_registry_messages_when_the_registry_refuses(tmp_path, repo, digest_name):
    docker = FakeDocker()
    docker.add("denied", f"{repo}:1", image_id=dg("1"), repo_digests=[digest_name + "@" + dg("a")], remote=("err", UNAUTHORIZED))
    docker.add("gone", f"{repo}-b:1", image_id=dg("2"), repo_digests=[digest_name + "-b@" + dg("b")], remote=("err", "manifest unknown"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    denied, gone = result_of(service, "h1", "denied"), result_of(service, "h1", "gone")
    assert denied["status"] == UNKNOWN
    assert denied["reason"] == "Registry verweigert den Zugriff (privates oder dort nicht vorhandenes Image) -- ggf. auf dem Host per docker login anmelden"
    assert gone["status"] == UNKNOWN
    assert gone["reason"] == "Image bei der Registry nicht gefunden"
    assert len(docker.registry_calls()) == 2


@pytest.mark.asyncio
async def test_an_official_name_is_only_self_built_when_the_registry_gave_a_clear_refusal(tmp_path):
    """Abruflimit, Netzproblem, Anmeldedaten-Fehler und "nicht gefunden" (entfernter Tag) sagen
    nichts darueber, ob das Image selbst gebaut ist -- das bleibt "nicht pruefbar"."""
    for answer, word in (
        ("manifest unknown: manifest unknown", "nicht gefunden"),
        ("toomanyrequests: You have reached your unauthenticated pull rate limit.", "Abruflimit"),
        ("dial tcp: lookup registry-1.docker.io: no such host", "nicht erreichbar"),
        ('error getting credentials - err: exec: "docker-credential-pass": executable file not found in $PATH', "Anmeldedaten"),
        ("etwas ganz anderes", "Abfrage fehlgeschlagen"),
    ):
        docker = FakeDocker()
        docker.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=("err", answer))
        service, ctx = make(tmp_path, docker)
        await service.check_host(ctx.host_objects[0])
        result = result_of(service, "h1", "web")
        assert result["status"] == UNKNOWN and word in result["reason"], answer


@pytest.mark.asyncio
async def test_a_self_built_answer_does_not_block_the_registry_for_the_rest_of_the_run(tmp_path):
    docker = FakeDocker()
    # Reihenfolge nach Container-Name: erst das selbst gebaute Image, dann die echten.
    docker.add("a-lattice", "lattice:latest", image_id=dg("1"), repo_digests=["lattice@" + dg("a")], remote=("err", DENIED))
    docker.add("b-nginx", "nginx:1.27", image_id=dg("2"), repo_digests=["nginx@" + dg("b")], remote=dg("b"))
    docker.add("c-pihole", "pihole/pihole:latest", image_id=dg("3"), repo_digests=["pihole/pihole@" + dg("c")], remote=dg("d"))
    docker.add("d-mine", "mine:1", image_id=dg("4"), repo_digests=["mine@" + dg("e")], remote=("err", UNAUTHORIZED))
    docker.add("e-redis", "redis:7", image_id=dg("5"), repo_digests=["redis@" + dg("f")], remote=dg("f"))
    docker.add("f-oldtag", "debian:9", image_id=dg("6"), repo_digests=["debian@" + dg("1")], remote=("err", "manifest unknown"))
    docker.add("g-nginx", "nginx:1.28", image_id=dg("7"), repo_digests=["nginx@" + dg("2")], remote=dg("2"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert len(docker.registry_calls()) == 7, "jedes Image wurde gefragt, nichts wurde gesperrt"
    statuses = {n: result_of(service, "h1", n)["status"] for n in docker.containers}
    assert statuses == {
        "a-lattice": LOCAL, "b-nginx": CURRENT, "c-pihole": UPDATE, "d-mine": LOCAL, "e-redis": CURRENT,
        "f-oldtag": UNKNOWN, "g-nginx": CURRENT,
    }


@pytest.mark.asyncio
async def test_a_self_built_container_is_asked_again_next_time_but_never_cached_as_an_answer(tmp_path):
    """Es gibt keine Antwort der Registry, die man merken koennte -- und wer den Container
    spaeter aus einer echten Registry zieht, soll das sofort sehen."""
    docker = FakeDocker()
    docker.add("lattice-1", "lattice:latest", image_id=dg("1"), repo_digests=["lattice@" + dg("a")], remote=("err", DENIED))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert service._hosts["h1"].remote == {}
    docker.remote["lattice:latest"] = dg("a")  # jetzt gibt es das Image doch bei der Registry
    await service.check_host(ctx.host_objects[0])
    assert result_of(service, "h1", "lattice-1")["status"] == CURRENT


@pytest.mark.asyncio
async def test_rate_limit_stops_further_questions_to_the_same_registry_only(tmp_path):
    docker = FakeDocker()
    limit = "toomanyrequests: You have reached your unauthenticated pull rate limit."
    docker.add("a", "aaa/one:1", image_id=dg("1"), repo_digests=["aaa/one@" + dg("a")], remote=("err", limit))
    docker.add("b", "bbb/two:1", image_id=dg("2"), repo_digests=["bbb/two@" + dg("b")], remote=dg("b"))
    docker.add("c", "ccc/three:1", image_id=dg("3"), repo_digests=["ccc/three@" + dg("c")], remote=dg("c"))
    docker.add("d", "ghcr.io/x/four:1", image_id=dg("4"), repo_digests=["ghcr.io/x/four@" + dg("d")], remote=dg("d"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    # Alle Docker-Hub-Images nach dem ersten Fehlschlag: keine weitere Anfrage, gleicher Grund.
    hub_calls = [c for c in docker.registry_calls() if "ghcr.io" not in c]
    assert len(hub_calls) == 1
    for name in ("a", "b", "c"):
        assert result_of(service, "h1", name)["status"] == UNKNOWN
        assert "Abruflimit" in result_of(service, "h1", name)["reason"]
    assert result_of(service, "h1", "d")["status"] == CURRENT


@pytest.mark.asyncio
async def test_slow_registry_only_hurts_that_container(tmp_path):
    docker = FakeDocker()
    docker.add("slow", "slow.example.org/a:1", image_id=dg("1"), repo_digests=["slow.example.org/a@" + dg("a")], remote=TimeoutError())
    docker.add("fine", "quay.io/a/b:3", image_id=dg("3"), repo_digests=["quay.io/a/b@" + dg("c")], remote=dg("c"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert "Zeitüberschreitung" in result_of(service, "h1", "slow")["reason"]
    assert result_of(service, "h1", "fine")["status"] == CURRENT


@pytest.mark.asyncio
async def test_without_buildx_the_manifest_fallback_reads_the_hosts_platform(tmp_path):
    docker = FakeDocker()
    docker.buildx = False
    docker.platform = "linux/arm64"
    # Lokal: klassischer Image-Speicher -- die Image-ID ist der Config-Digest des Manifests (dg 4).
    docker.add("web", "nginx:1.27", image_id=dg("4"), repo_digests=["nginx@" + dg("f")])
    docker.manifests["nginx:1.27"] = manifest_list_output()
    docker.add("old", "nginx:1.26", image_id=dg("8"), repo_digests=["nginx@" + dg("e")])
    docker.manifests["nginx:1.26"] = manifest_list_output()
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert result_of(service, "h1", "web")["status"] == CURRENT
    assert result_of(service, "h1", "old")["status"] == UPDATE
    assert result_of(service, "h1", "old")["remote_digest"] == dg("3")
    # buildx nur EINMAL versucht, die Plattform nur einmal gefragt.
    assert sum(" imagetools " in c for c in docker.calls) == 1
    assert sum(c.startswith("docker version") for c in docker.calls) == 1


@pytest.mark.asyncio
async def test_manifest_fallback_without_the_platform_and_with_errors(tmp_path):
    docker = FakeDocker()
    docker.buildx = False
    docker.platform = "linux/riscv64"
    docker.add("web", "nginx:1.27", image_id=dg("4"), repo_digests=["nginx@" + dg("f")])
    docker.manifests["nginx:1.27"] = manifest_list_output()
    docker.add("x", "quay.io/a/b:1", image_id=dg("6"), repo_digests=["quay.io/a/b@" + dg("f")])
    docker.manifests["quay.io/a/b:1"] = ("err", "toomanyrequests: rate limit")
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert "riscv64" in result_of(service, "h1", "web")["reason"]
    assert "Abruflimit" in result_of(service, "h1", "x")["reason"]


@pytest.mark.asyncio
async def test_registry_answers_are_cached_six_hours_but_the_local_state_is_always_fresh(tmp_path):
    docker = FakeDocker()
    docker.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("b"))
    service, ctx = make(tmp_path, docker)
    now = [1_000_000.0]
    service._clock = lambda: now[0]
    host = ctx.host_objects[0]

    await service.check_host(host)
    assert result_of(service, "h1", "web")["status"] == UPDATE
    assert len(docker.registry_calls()) == 1

    # Der Nutzer spielt das Update ein (neues Image, gleicher Name) und prueft erneut:
    # sofort "aktuell", ohne dass die Registry noch einmal gefragt wird.
    docker.containers["web"] = ("nginx:1.27", dg("2"))
    docker.images[dg("2")] = (["nginx@" + dg("b")], dg("2"))
    now[0] += 600
    await service.check_host(host)
    assert result_of(service, "h1", "web")["status"] == CURRENT
    assert len(docker.registry_calls()) == 1

    # Nach 6 Stunden fragt auch ein normaler Klick wieder bei der Registry nach ...
    now[0] += iu.CACHE_TTL_S
    await service.check_host(host)
    assert len(docker.registry_calls()) == 2
    # ... und "neu abfragen" (force) auch davor.
    await service.check_host(host, force=True)
    assert len(docker.registry_calls()) == 3


@pytest.mark.asyncio
async def test_the_registry_is_asked_once_per_image_name_not_per_container(tmp_path):
    docker = FakeDocker()
    for n in ("one", "two", "three"):
        docker.add(n, "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("a"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert len(docker.registry_calls()) == 1
    assert sum(c.startswith("docker image inspect") for c in docker.calls) == 1
    assert {result_of(service, "h1", n)["status"] for n in ("one", "two", "three")} == {CURRENT}


@pytest.mark.asyncio
async def test_unreachable_host_keeps_the_last_good_result_and_shows_the_error(tmp_path):
    docker = FakeDocker()
    docker.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("b"))
    other = FakeDocker()
    other.add("db", "postgres:16", image_id=dg("2"), repo_digests=["postgres@" + dg("c")], remote=dg("c"))
    service, ctx = make(tmp_path, {"h1": docker, "h2": other})
    await service.check_hosts(ctx.host_objects)
    docker.down = ConnectionError("no route to host")
    await service.check_hosts(ctx.host_objects, force=True)

    snap = service.snapshot()
    assert "no route to host" in snap["hosts"]["h1"]["error"]
    assert snap["hosts"]["h2"]["error"] is None
    assert snap["data"]["h1:web"]["status"] == UPDATE, "der letzte gute Stand bleibt sichtbar"
    assert snap["data"]["h2:db"]["status"] == CURRENT


@pytest.mark.asyncio
async def test_docker_daemon_down_and_ssh_loss_during_the_registry_query_are_host_errors(tmp_path):
    class NoDaemon(FakeDocker):
        async def run(self, command: str) -> FakeResult:
            self.calls.append(command)
            return FakeResult(1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock")

    service, ctx = make(tmp_path, NoDaemon())
    await service.check_host(ctx.host_objects[0])
    assert "docker ps fehlgeschlagen: Cannot connect" in service.snapshot()["hosts"]["h1"]["error"]

    docker = FakeDocker()
    docker.add("a", "aaa/one:1", image_id=dg("1"), repo_digests=["aaa/one@" + dg("a")], remote=ConnectionResetError("Verbindung weg"))
    docker.add("b", "bbb/two:1", image_id=dg("2"), repo_digests=["bbb/two@" + dg("b")], remote=dg("b"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    # Kein zweiter Versuch mit derselben toten Verbindung.
    assert "Verbindung weg" in service.snapshot()["hosts"]["h1"]["error"]
    assert len(docker.registry_calls()) == 1


@pytest.mark.asyncio
async def test_start_runs_in_the_background_and_a_running_host_is_not_started_twice(tmp_path):
    import asyncio

    docker = FakeDocker()
    docker.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("a"))
    service, ctx = make(tmp_path, docker)
    host = ctx.host_objects[0]
    assert service.start([host]) == ["h1"]
    assert service.snapshot()["hosts"]["h1"]["checking"] is True
    assert service.start([host]) == [], "laeuft schon"
    for _ in range(100):
        if not service.snapshot()["hosts"]["h1"]["checking"]:
            break
        await asyncio.sleep(0.01)
    assert service.snapshot()["hosts"]["h1"]["checking"] is False
    assert result_of(service, "h1", "web")["status"] == CURRENT


@pytest.mark.asyncio
async def test_stale_registry_answers_of_removed_containers_are_dropped(tmp_path):
    docker = FakeDocker()
    docker.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("a"))
    docker.add("db", "postgres:16", image_id=dg("2"), repo_digests=["postgres@" + dg("b")], remote=dg("b"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert set(service._hosts["h1"].remote) == {"nginx:1.27", "postgres:16"}
    del docker.containers["db"]
    await service.check_host(ctx.host_objects[0])
    assert set(service._hosts["h1"].remote) == {"nginx:1.27"}
    assert "h1:db" not in service.snapshot()["data"]


# --- Tagesjob und Meldung --------------------------------------------------------------


def one_update_host(remote: str = "b") -> FakeDocker:
    docker = FakeDocker()
    docker.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg(remote))
    return docker


@pytest.mark.asyncio
async def test_daily_job_sends_one_message_and_repeats_it_only_when_the_set_changes(tmp_path):
    other = FakeDocker()
    other.add("db", "postgres:16", image_id=dg("2"), repo_digests=["postgres@" + dg("c")], remote=dg("d"))
    other.add("cache", "redis:7", image_id=dg("3"), repo_digests=["redis@" + dg("e")], remote=dg("e"))
    docker = one_update_host()
    service, ctx = make(tmp_path, {"h1": docker, "h2": other}, {"h1": "docker", "h2": "pi-host"})

    out = await service.scheduled_check()
    assert out == {"hosts": 2, "updates": 2, "notified": True}
    assert len(ctx.sent) == 1, "EINE Meldung fuer alle Container"
    note = ctx.sent[0]
    assert note.title == "Image-Updates: 2 Container mit neuer Version"
    assert "- docker: web (nginx:1.27)" in note.body and "- pi-host: db (postgres:16)" in note.body
    assert "redis" not in note.body
    assert note.payload == {"path": "/ext/service-matrix/matrix", "tags": ["package"]}

    # Naechster Tag, nichts Neues: still.
    assert (await service.scheduled_check())["notified"] is False
    assert len(ctx.sent) == 1

    # Eine NEUE Version desselben Containers ist eine Neuigkeit ...
    docker.remote["nginx:1.27"] = dg("f")
    assert (await service.scheduled_check())["notified"] is True
    assert len(ctx.sent) == 2
    assert ctx.sent[1].title == "Image-Updates: 2 Container mit neuer Version"

    # ... ein aktualisierter Container ist keine (die Liste wird nur kuerzer) ...
    other.containers["db"] = ("postgres:16", dg("9"))
    other.images[dg("9")] = (["postgres@" + dg("d")], dg("9"))
    out = await service.scheduled_check()
    assert out["updates"] == 1 and out["notified"] is False
    assert len(ctx.sent) == 2

    # ... aber der naechste Stand von db wird wieder gemeldet, weil der alte vergessen wurde.
    other.remote["postgres:16"] = dg("a")
    assert (await service.scheduled_check())["notified"] is True
    assert len(ctx.sent) == 3
    assert "- pi-host: db (postgres:16)" in ctx.sent[2].body


@pytest.mark.asyncio
async def test_no_message_when_everything_is_current_and_the_state_survives_a_restart(tmp_path):
    docker = FakeDocker()
    docker.add("web", "nginx:1.27", image_id=dg("1"), repo_digests=["nginx@" + dg("a")], remote=dg("a"))
    service, ctx = make(tmp_path, docker)
    assert (await service.scheduled_check()) == {"hosts": 1, "updates": 0, "notified": False}
    assert ctx.sent == []

    docker.remote["nginx:1.27"] = dg("b")
    await service.scheduled_check()
    assert len(ctx.sent) == 1
    # Nodvard Deck startet neu: neuer Dienst, gleicher Ordner -> keine zweite Meldung fuer dieselbe Version.
    again = ImageUpdateService(ctx)  # type: ignore[arg-type]
    assert (await again.scheduled_check())["notified"] is False
    assert len(ctx.sent) == 1
    saved = json.loads((tmp_path / iu.STATE_FILE).read_text(encoding="utf-8"))
    assert saved == {"notified": {"h1": ["web|" + dg("b")]}}


@pytest.mark.asyncio
async def test_a_host_that_did_not_answer_does_not_make_old_updates_look_new(tmp_path):
    docker = one_update_host()
    other = one_update_host()
    service, ctx = make(tmp_path, {"h1": docker, "h2": other})
    await service.scheduled_check()
    assert len(ctx.sent) == 1

    other.down = ConnectionError("weg")
    await service.scheduled_check()
    assert len(ctx.sent) == 1
    other.down = None
    await service.scheduled_check()
    assert len(ctx.sent) == 1, "h2 ist zurueck, die Version ist dieselbe wie vorher"
    saved = json.loads((tmp_path / iu.STATE_FILE).read_text(encoding="utf-8"))
    assert set(saved["notified"]) == {"h1", "h2"}


@pytest.mark.asyncio
async def test_daily_job_forces_a_fresh_registry_answer(tmp_path):
    docker = one_update_host("b")
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])  # Antwort im Speicher
    docker.remote["nginx:1.27"] = dg("c")  # inzwischen ist eine neuere Version da
    await service.scheduled_check()
    assert result_of(service, "h1", "web")["remote_digest"] == dg("c")


@pytest.mark.asyncio
async def test_the_job_is_registered_disabled_by_default_with_the_chosen_schedule(tmp_path):
    registered = []

    class Scheduler:
        async def register_job(self, spec) -> None:
            registered.append(spec)

    class C:
        scheduler = Scheduler()

    service, _ = make(tmp_path, FakeDocker())
    await iu.register_job(C(), service, {})  # type: ignore[arg-type]
    await iu.register_job(C(), service, {"image_updates_enabled": True, "image_updates_cron": "0 5 * * 1"})  # type: ignore[arg-type]
    await iu.register_job(C(), service, {"image_updates_enabled": True, "image_updates_cron": ""})  # type: ignore[arg-type]
    off, on, default = registered
    assert (off.id, off.enabled, off.schedule) == ("image-updates", False, "0 0 31 2 *")
    assert (on.enabled, on.schedule) == (True, "0 5 * * 1")
    assert default.schedule == iu.DEFAULT_CRON
    assert off.params == {} and off.name == "Image-Updates prüfen"


DAY_S = 24 * 3600
LIMIT = "toomanyrequests: You have reached your unauthenticated pull rate limit."


def with_clock(service: ImageUpdateService, start: float = 1_000_000.0) -> list[float]:
    now = [start]
    service._clock = lambda: now[0]
    return now


@pytest.mark.asyncio
async def test_a_container_that_could_not_be_checked_does_not_make_an_old_update_look_new(tmp_path):
    """Tag 1: Update b (Meldung). Tag 2: Abruflimit nur fuer diesen Container. Tag 3: dieselbe
    Version b -- ist keine Neuigkeit, auch wenn Tag 2 keine Aussage machte."""
    docker = one_update_host("b")
    docker.add("db", "postgres:16", image_id=dg("2"), repo_digests=["postgres@" + dg("c")], remote=dg("c"))
    service, ctx = make(tmp_path, docker)
    now = with_clock(service)

    assert (await service.scheduled_check())["notified"] is True
    assert len(ctx.sent) == 1

    now[0] += DAY_S  # Antworten von gestern sind laengst abgelaufen
    docker.remote["nginx:1.27"] = ("err", LIMIT)
    out = await service.scheduled_check()
    assert result_of(service, "h1", "web")["status"] == UNKNOWN
    assert out == {"hosts": 1, "updates": 0, "notified": False}
    saved = json.loads((tmp_path / iu.STATE_FILE).read_text(encoding="utf-8"))
    assert saved == {"notified": {"h1": ["web|" + dg("b")]}}, "der alte Stand bleibt fuer Unklares erhalten"

    now[0] += DAY_S
    docker.remote["nginx:1.27"] = dg("b")
    assert (await service.scheduled_check())["notified"] is False
    assert len(ctx.sent) == 1

    # Eine wirklich neue Version danach wird wieder gemeldet.
    now[0] += DAY_S
    docker.remote["nginx:1.27"] = dg("d")
    assert (await service.scheduled_check())["notified"] is True
    assert len(ctx.sent) == 2


@pytest.mark.asyncio
async def test_a_positive_answer_forgets_the_notified_version_even_after_an_unclear_day(tmp_path):
    docker = one_update_host("b")
    service, ctx = make(tmp_path, docker)
    now = with_clock(service)
    await service.scheduled_check()
    assert len(ctx.sent) == 1

    now[0] += DAY_S
    docker.remote["nginx:1.27"] = ("err", LIMIT)
    await service.scheduled_check()

    # Der Nutzer aktualisiert: "aktuell" ist eine klare Antwort -> vergessen.
    now[0] += DAY_S
    docker.remote["nginx:1.27"] = dg("b")
    docker.containers["web"] = ("nginx:1.27", dg("9"))
    docker.images[dg("9")] = (["nginx@" + dg("b")], dg("9"))
    await service.scheduled_check()
    saved = json.loads((tmp_path / iu.STATE_FILE).read_text(encoding="utf-8"))
    assert saved == {"notified": {"h1": []}}

    # Dieselbe Version b als "neu" fuer einen anderen lokalen Stand: wieder eine Meldung.
    now[0] += DAY_S
    docker.containers["web"] = ("nginx:1.27", dg("1"))
    await service.scheduled_check()
    assert len(ctx.sent) == 2


@pytest.mark.asyncio
async def test_forced_check_under_rate_limit_keeps_the_last_answer_marked_as_old(tmp_path):
    docker = one_update_host("b")
    service, ctx = make(tmp_path, docker)
    now = with_clock(service)
    host = ctx.host_objects[0]
    await service.check_host(host)
    first_at = now[0]
    assert result_of(service, "h1", "web")["stale"] is False

    # "Registry neu abfragen" -- und die Registry sagt: Abruflimit.
    now[0] += 600
    docker.remote["nginx:1.27"] = ("err", LIMIT)
    await service.check_host(host, force=True)
    kept = result_of(service, "h1", "web")
    assert (kept["status"], kept["remote_digest"], kept["stale"]) == (UPDATE, dg("b"), True)
    assert "Abruflimit" in kept["reason"] and "letzte Antwort" in kept["reason"]
    assert kept["registry_at"] == iu._iso(first_at)
    assert service._hosts["h1"].remote["nginx:1.27"].fetched_at == first_at, "die alte Antwort behaelt ihr Alter"

    # Ein normaler Klick danach fragt die Registry gar nicht (Speicher gilt noch) und zeigt keine Warnung.
    calls = len(docker.registry_calls())
    now[0] += 600
    await service.check_host(host)
    assert len(docker.registry_calls()) == calls
    assert result_of(service, "h1", "web")["status"] == UPDATE
    assert result_of(service, "h1", "web")["stale"] is False

    # Der lokale Stand ist trotzdem frisch: nach dem Update zeigt die alte Antwort "aktuell".
    docker.containers["web"] = ("nginx:1.27", dg("2"))
    docker.images[dg("2")] = (["nginx@" + dg("b")], dg("2"))
    now[0] += 600
    await service.check_host(host, force=True)
    assert (result_of(service, "h1", "web")["status"], result_of(service, "h1", "web")["stale"]) == (CURRENT, True)


@pytest.mark.asyncio
async def test_an_expired_answer_is_not_kept_under_a_rate_limit_and_conclusive_errors_never_keep_it(tmp_path):
    docker = one_update_host("b")
    service, ctx = make(tmp_path, docker)
    now = with_clock(service)
    host = ctx.host_objects[0]
    await service.check_host(host)

    now[0] += iu.CACHE_TTL_S + 1
    docker.remote["nginx:1.27"] = ("err", LIMIT)
    await service.check_host(host, force=True)
    assert result_of(service, "h1", "web")["status"] == UNKNOWN
    assert service._hosts["h1"].remote == {}

    # Das Image gibt es bei der Registry nicht mehr: eine klare Antwort, die alte gilt nicht weiter.
    docker = one_update_host("b")
    service, ctx = make(tmp_path, docker)
    now = with_clock(service)
    await service.check_host(ctx.host_objects[0])
    docker.remote["nginx:1.27"] = ("err", "manifest unknown: manifest unknown")
    await service.check_host(ctx.host_objects[0], force=True)
    assert result_of(service, "h1", "web")["status"] == UNKNOWN
    assert "nicht gefunden" in result_of(service, "h1", "web")["reason"]
    assert service._hosts["h1"].remote == {}


@pytest.mark.asyncio
async def test_an_unreachable_registry_is_asked_once_per_run_not_once_per_image(tmp_path):
    docker = FakeDocker()
    dead = 'Get "https://ghcr.io/v2/": dial tcp 140.82.121.34:443: i/o timeout'
    for n in range(6):
        docker.add(f"c{n}", f"ghcr.io/x/img{n}:1", image_id=dg(str(n)), repo_digests=[f"ghcr.io/x/img{n}@" + dg("a")], remote=("err", dead))
    docker.add("fine", "quay.io/a/b:3", image_id=dg("7"), repo_digests=["quay.io/a/b@" + dg("c")], remote=dg("c"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert len([c for c in docker.registry_calls() if "ghcr.io" in c]) == 1
    for n in range(6):
        assert result_of(service, "h1", f"c{n}")["status"] == UNKNOWN
        assert "nicht erreichbar" in result_of(service, "h1", f"c{n}")["reason"]
    assert result_of(service, "h1", "fine")["status"] == CURRENT


@pytest.mark.asyncio
async def test_a_registry_that_times_out_is_also_asked_only_once(tmp_path):
    docker = FakeDocker()
    for n in range(4):
        docker.add(f"c{n}", f"slow.example.org/x/img{n}:1", image_id=dg(str(n)), repo_digests=[f"slow.example.org/x/img{n}@" + dg("a")], remote=TimeoutError())
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert len(docker.registry_calls()) == 1
    assert {result_of(service, "h1", f"c{n}")["reason"] for n in range(4)} == {"Registry antwortet nicht (Zeitüberschreitung)"}
    # Ein neuer Lauf fragt wieder (die Registry kann inzwischen zurueck sein).
    await service.check_host(ctx.host_objects[0])
    assert len(docker.registry_calls()) == 2


@pytest.mark.asyncio
async def test_conclusive_errors_do_not_block_the_rest_of_the_registry(tmp_path):
    docker = FakeDocker()
    docker.add("gone", "ghcr.io/x/gone:1", image_id=dg("1"), repo_digests=["ghcr.io/x/gone@" + dg("a")], remote=("err", "manifest unknown"))
    docker.add("fine", "ghcr.io/x/fine:1", image_id=dg("2"), repo_digests=["ghcr.io/x/fine@" + dg("b")], remote=dg("b"))
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    assert len(docker.registry_calls()) == 2
    assert result_of(service, "h1", "fine")["status"] == CURRENT


def state_file(tmp_path: Path) -> dict:
    return json.loads((tmp_path / iu.STATE_FILE).read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_a_self_built_container_is_no_update_and_no_reason_for_a_message(tmp_path):
    docker = FakeDocker()
    docker.add("lattice-1", "lattice:latest", image_id=dg("1"), repo_digests=["lattice@" + dg("a")], remote=("err", DENIED))
    docker.add("local", "mine:1", image_id=dg("2"), repo_digests=[])
    docker.add("web", "nginx:1.27", image_id=dg("3"), repo_digests=["nginx@" + dg("c")], remote=dg("c"))
    service, ctx = make(tmp_path, docker)

    out = await service.scheduled_check()

    assert out == {"hosts": 1, "updates": 0, "notified": False}
    assert ctx.sent == []
    assert state_file(tmp_path) == {"notified": {"h1": []}}
    assert {n: result_of(service, "h1", n)["status"] for n in docker.containers} == {"lattice-1": LOCAL, "local": LOCAL, "web": CURRENT}


@pytest.mark.asyncio
async def test_self_built_containers_stay_out_of_the_message_next_to_real_updates(tmp_path):
    docker = one_update_host("b")
    docker.add("lattice-1", "lattice:latest", image_id=dg("2"), repo_digests=["lattice@" + dg("a")], remote=("err", DENIED))
    service, ctx = make(tmp_path, docker)
    now = with_clock(service)

    out = await service.scheduled_check()
    assert out == {"hosts": 1, "updates": 1, "notified": True}
    assert ctx.sent[0].title == "Image-Updates: 1 Container mit neuer Version"
    assert "lattice" not in ctx.sent[0].body
    assert state_file(tmp_path) == {"notified": {"h1": ["web|" + dg("b")]}}

    # Naechster Tag mit Abruflimit nur fuer web: web bleibt "unklar" (alter Stand gehalten),
    # der selbst gebaute Container aendert daran nichts und bekommt keinen Eintrag.
    now[0] += DAY_S
    docker.remote["nginx:1.27"] = ("err", LIMIT)
    out = await service.scheduled_check()
    assert out == {"hosts": 1, "updates": 0, "notified": False}
    assert state_file(tmp_path) == {"notified": {"h1": ["web|" + dg("b")]}}
    assert len(ctx.sent) == 1


@pytest.mark.asyncio
async def test_self_built_is_a_clear_result_not_an_unclear_one(tmp_path):
    """Ein Container, der frueher gemeldet wurde und jetzt als selbst gebaut erkannt wird, ist
    ein klares Ergebnis wie "aktuell": sein alter Meldungsstand wird vergessen, nicht gehalten
    (gehalten wird nur bei "nicht pruefbar")."""
    docker = FakeDocker()
    docker.add("lattice-1", "lattice:latest", image_id=dg("1"), repo_digests=["lattice@" + dg("a")], remote=("err", DENIED))
    docker.add("web", "nginx:1.27", image_id=dg("2"), repo_digests=["nginx@" + dg("b")], remote=("err", LIMIT))
    service, _ = make(tmp_path, docker)
    (tmp_path / iu.STATE_FILE).write_text(
        json.dumps({"notified": {"h1": ["lattice-1|" + dg("9"), "web|" + dg("9")]}}), encoding="utf-8",
    )

    out = await service.scheduled_check()

    assert out == {"hosts": 1, "updates": 0, "notified": False}
    assert state_file(tmp_path) == {"notified": {"h1": ["web|" + dg("9")]}}


@pytest.mark.asyncio
async def test_a_host_that_never_answered_has_no_check_time(tmp_path):
    docker = one_update_host()
    docker.down = ConnectionError("no route to host")
    service, ctx = make(tmp_path, docker)
    await service.check_host(ctx.host_objects[0])
    host = service.snapshot()["hosts"]["h1"]
    assert host["checked_at"] is None and "no route to host" in host["error"]
    assert service.snapshot()["data"] == {}

    # Nach dem ersten guten Lauf gibt es die Zeit; ein spaeterer Fehler behaelt sie.
    docker.down = None
    await service.check_host(ctx.host_objects[0])
    good_at = service.snapshot()["hosts"]["h1"]["checked_at"]
    assert good_at is not None and service.snapshot()["hosts"]["h1"]["error"] is None
    docker.down = ConnectionError("wieder weg")
    await service.check_host(ctx.host_objects[0])
    assert service.snapshot()["hosts"]["h1"]["checked_at"] == good_at


@pytest.mark.asyncio
async def test_the_state_file_is_written_atomically(tmp_path, monkeypatch):
    docker = one_update_host("b")
    service, _ = make(tmp_path, docker)
    await service.scheduled_check()
    path = tmp_path / iu.STATE_FILE
    before = path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp")), "keine Reste der Zwischendatei"

    # Der Rechner stuerzt beim Umbenennen ab: die alte Datei bleibt ganz, es gibt keine halbe.
    def boom(*_a, **_k):
        raise OSError("Absturz")

    monkeypatch.setattr(iu.os, "replace", boom)
    docker.remote["nginx:1.27"] = dg("d")
    with pytest.raises(OSError):
        await service.scheduled_check()
    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before) == {"notified": {"h1": ["web|" + dg("b")]}}


@pytest.mark.asyncio
async def test_an_invalid_schedule_falls_back_to_the_default(tmp_path):
    registered = []

    class Scheduler:
        async def register_job(self, spec) -> None:
            if spec.schedule == "kein cron":
                raise ValueError("Ungueltiger Zeitplan")
            registered.append(spec)

    class C:
        scheduler = Scheduler()

    service, _ = make(tmp_path, FakeDocker())
    await iu.register_job(C(), service, {"image_updates_enabled": True, "image_updates_cron": "kein cron"})  # type: ignore[arg-type]
    assert len(registered) == 1
    assert (registered[0].enabled, registered[0].schedule) == (True, iu.DEFAULT_CRON)
