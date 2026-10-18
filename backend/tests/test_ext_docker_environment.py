"""Docker-Modul: Logs mit Zeitraum und was Docker auf einem Host an CPU und Arbeitsspeicher sieht (LXC, VM)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EXT_SRC = Path(__file__).resolve().parents[2] / "extensions" / "docker" / "src"


@pytest.fixture
def ext(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_SRC))
    from nodvard_deck_ext_docker import capabilities, commands

    yield capabilities, commands
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def test_logs_with_since(ext):
    _, commands = ext
    assert commands.logs("web", 5000, follow=True, since="24h") == "docker logs --tail 5000 --timestamps --since 24h --follow web"
    assert commands.logs("web", 200, follow=False, since="10m") == "docker logs --tail 200 --timestamps --since 10m web"


@pytest.mark.parametrize("bad", ["", "0m", "10", "10s", "1d", "10m;id", "10m\n", "-10m", "$(id)", "99999h", 10, True])
def test_since_is_checked(ext, bad):
    _, commands = ext
    with pytest.raises(ValueError):
        commands.logs("web", 10, follow=False, since=bad)


def test_info_and_virtualization(ext):
    _, commands = ext
    assert commands.info() == "docker info --format '{{json .}}'"
    assert commands.virtualization() == "systemd-detect-virt"


INFO = json.dumps({"NCPU": 2, "MemTotal": 4_294_967_296, "ServerVersion": "29.8.2", "Name": "egal"})


@pytest.mark.parametrize(
    ("virt", "expected"),
    [
        ("lxc\n", "lxc"),
        ("lxc-libvirt\n", "lxc"),
        ("kvm\n", "vm"),
        ("microsoft\n", "vm"),
        ("docker\n", "container"),
        ("none\n", "none"),
        ("", None),
        (None, None),
        ("sh: systemd-detect-virt: not found", None),
    ],
)
def test_environment(ext, virt, expected):
    capabilities, _ = ext
    assert capabilities.parse_environment(INFO, virt) == {
        "virtualization": expected, "cpus": 2, "mem_total": 4_294_967_296, "docker_version": "29.8.2",
    }


@pytest.mark.parametrize(
    "info",
    [
        "",
        "kein json",
        "[]",
        # So antwortet `docker info`, wenn der Docker-Dienst nicht läuft.
        json.dumps({"NCPU": 0, "MemTotal": 0, "ServerVersion": ""}),
        json.dumps({"NCPU": "2"}),
        json.dumps({"NCPU": True}),
    ],
)
def test_environment_needs_an_answering_docker(ext, info):
    capabilities, _ = ext
    assert capabilities.parse_environment(info, "lxc") is None


def test_environment_drops_odd_values(ext):
    capabilities, _ = ext
    odd = json.dumps({"NCPU": 4, "MemTotal": -1, "ServerVersion": "29<script>"})
    assert capabilities.parse_environment(odd, "kvm") == {
        "virtualization": "vm", "cpus": 4, "mem_total": None, "docker_version": None,
    }


class _Host:
    def __init__(self, host_id: str) -> None:
        self.id = host_id
        self.name = host_id
        self.display_name = host_id
        self.address = "192.0.2.1"


class _Result:
    def __init__(self, exit_code: int, stdout: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = ""


def _kind(command: str) -> str:
    if command.startswith("sh -c"):
        return "restarts"
    return command if command == "systemd-detect-virt" else command.split()[1]


class _Ctx:
    def __init__(self, answers: dict[tuple[str, str], object]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str]] = []
        ctx = self

        class _Settings:
            async def get(self) -> dict:
                return {}

        class _Hosts:
            async def list(self, *, tag: str | None = None) -> list[_Host]:
                return [_Host("lxc"), _Host("down")]

        class _Exec:
            async def run(self, host, command: str, *, timeout_s: int = 60):
                ctx.calls.append((host.id, command))
                answer = ctx.answers.get((host.id, _kind(command)))
                if isinstance(answer, Exception):
                    raise answer
                return answer if answer is not None else _Result(127, "")

        self.settings = _Settings()
        self.hosts = _Hosts()
        self.exec = _Exec()


@pytest.mark.asyncio
async def test_measure_names_the_limits_of_an_lxc_container(ext):
    capabilities, _ = ext
    ctx = _Ctx({
        ("lxc", "stats"): _Result(0, "web|1.50%|45.3MiB / 4GiB|1.10%\n"),
        ("lxc", "info"): _Result(0, INFO),
        ("lxc", "systemd-detect-virt"): _Result(0, "lxc\n"),
        ("lxc", "restarts"): _Result(1, "/web|0\n/absturz|8\n"),
        ("down", "stats"): OSError("Keine Verbindung"),
    })
    catalog = capabilities.DockerServiceCatalog(ctx)
    measured = await catalog.measure()
    assert measured["data"] == {"lxc:web": {"cpu_percent": 1.5, "mem_used": 47_500_492, "mem_limit": 4 * 2**30}}
    assert measured["hosts"] == {"lxc": {"virtualization": "lxc", "cpus": 2, "mem_total": 4_294_967_296, "docker_version": "29.8.2"}}
    assert measured["restarts"] == {"lxc:absturz": 8}
    # Ein Host ohne Messwerte wird gar nicht erst nach der Umgebung gefragt.
    assert [c for c in ctx.calls if c[0] == "down"] == [("down", "docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}'")]

    ctx.calls.clear()
    await catalog.measure()
    assert [c for c in ctx.calls if c[0] == "lxc" and _kind(c[1]) not in ("stats", "restarts")] == [], "die Umgebung gilt zehn Minuten"


@pytest.mark.asyncio
async def test_measure_keeps_the_last_environment_when_docker_info_fails(ext, monkeypatch):
    capabilities, _ = ext
    ctx = _Ctx({
        ("lxc", "stats"): _Result(0, ""),
        ("lxc", "info"): _Result(0, INFO),
        ("lxc", "systemd-detect-virt"): OSError("Zeitüberschreitung"),
    })
    catalog = capabilities.DockerServiceCatalog(ctx)
    first = await catalog.measure()
    assert first["hosts"]["lxc"]["virtualization"] is None
    assert first["hosts"]["lxc"]["cpus"] == 2

    monkeypatch.setattr(capabilities, "ENVIRONMENT_TTL_S", 0.0)
    ctx.answers[("lxc", "info")] = _Result(1, "")
    assert (await catalog.measure())["hosts"]["lxc"]["cpus"] == 2


def test_restart_counts_in_one_call(ext):
    capabilities, commands = ext
    assert commands.restart_counts() == (
        "sh -c 'ids=$(docker ps -aq) && [ -n \"$ids\" ] && docker inspect --type container --format "
        "'\"'\"'{{.Name}}|{{.RestartCount}}'\"'\"' $ids'"
    )
    # Die ersten beiden Zeilen wie bei Docker 29.8.2 (`absturz` mit `--restart always`), danach ungültige.
    text = "/absturz|8\n/web|0\n/x;id|3\n/kaputt|viele\n/hoch|²\n/lang|" + "9" * 5000 + "\nohne-strich\n"
    assert capabilities.parse_restart_counts(text) == {"absturz": 8}
