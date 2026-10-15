"""Docker-Modul: belegte Ports je Container (`published_ports`) und die Server-Adresse in jeder Zeile von
`list_services()`. Beides braucht der Adress-Check der Netzwerk-Erweiterung. Gestoppte Container zeigen in
`docker ps` keine Ports; ihre Zuordnung kommt einmal aus `docker inspect` und danach aus dem Speicher."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_ext_docker import _FakeCtx, _FakeExecResult, _FakeHost, _ps_line

SRC = Path(__file__).resolve().parents[2] / "extensions" / "docker" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_deck_ext_docker.capabilities import (
    DockerServiceCatalog,
    parse_port_bindings,
    published_ports,
)

ID_RUN = "aaaaaaaaaaaa"
ID_STOP = "bbbbbbbbbbbb"
ID_NEW = "cccccccccccc"


@pytest.mark.parametrize(
    ("ports", "expected"),
    [
        ("0.0.0.0:8080->80/tcp, :::8080->80/tcp", [8080]),
        ("[::]:2283->2283/tcp", [2283]),
        ("0.0.0.0:8000-8002->8000-8002/tcp", [8000, 8001, 8002]),
        ("127.0.0.1:5432->5432/tcp", []),
        ("[::1]:5432->5432/tcp, 0.0.0.0:8080->80/tcp", [8080]),
        ("0.0.0.0:53->53/udp, 0.0.0.0:53->53/tcp, 0.0.0.0:8081->80/tcp", [53, 8081]),
        ("0.0.0.0:5353->5353/udp", []),
        ("80/tcp", []),
        ("", []),
    ],
)
def test_published_ports_reads_what_docker_ps_shows(ports, expected):
    assert published_ports(ports) == expected


def test_a_huge_range_is_capped():
    ports = published_ports("0.0.0.0:1-65535->1-65535/tcp")
    assert len(ports) == 256 and ports[0] == 1


def test_parse_port_bindings_reads_the_container_config():
    stdout = "\n".join([
        f"{ID_STOP}0123456789|" + json.dumps({"2283/tcp": [{"HostIp": "", "HostPort": "2283"}], "53/udp": [{"HostIp": "", "HostPort": "53"}]}),
        f"{ID_NEW}0123456789|" + json.dumps({"80/tcp": [{"HostIp": "", "HostPort": ""}], "81/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8181"}]}),
        "kaputt|{",
        "zzzz|{}",
    ])
    assert parse_port_bindings(stdout) == {ID_STOP: [2283], ID_NEW: []}


class _CountingExec:
    def __init__(self, ps: str, inspect: str) -> None:
        self.ps = ps
        self.inspect = inspect
        self.commands: list[str] = []

    async def run(self, host, command: str, *, timeout_s: int = 60):
        self.commands.append(command)
        if command.startswith("docker inspect"):
            return _FakeExecResult(0, self.inspect)
        return _FakeExecResult(0, self.ps)


def _ctx(exec_handle: _CountingExec) -> _FakeCtx:
    ctx = _FakeCtx(settings={}, hosts=[_FakeHost("h1", "docker", "10.0.0.5")], outputs={})
    ctx.exec = exec_handle
    return ctx


@pytest.mark.asyncio
async def test_rows_carry_address_and_ports_and_stopped_containers_are_inspected_once():
    ps = "\n".join([
        _ps_line("nginx", "running", "Up 2 hours", ports="0.0.0.0:8080->80/tcp", container_id=ID_RUN),
        _ps_line("immich", "exited", "Exited (0) 1 hour ago", container_id=ID_STOP),
    ])
    inspect = f"{ID_STOP}ffff|" + json.dumps({"2283/tcp": [{"HostIp": "", "HostPort": "2283"}]})
    exec_handle = _CountingExec(ps, inspect)
    catalog = DockerServiceCatalog(_ctx(exec_handle))

    rows = {r["name"]: r for r in await catalog.list_services()}
    assert rows["nginx"]["host_address"] == "10.0.0.5"
    assert rows["nginx"]["published_ports"] == [8080]
    assert rows["immich"]["published_ports"] == [2283], "aus der Container-Konfiguration, docker ps zeigt nichts"
    inspects = [c for c in exec_handle.commands if c.startswith("docker inspect")]
    assert inspects == [f"docker inspect --type container --format '{{{{.Id}}}}|{{{{json .HostConfig.PortBindings}}}}' {ID_STOP}"]

    await catalog.list_services()
    assert sum(c.startswith("docker inspect") for c in exec_handle.commands) == 1, "danach aus dem Speicher"

    # Ein neuer gestoppter Container wird einmal nachgelesen, der verschwundene vergessen.
    exec_handle.ps = _ps_line("neu", "created", "Created", container_id=ID_NEW)
    exec_handle.inspect = f"{ID_NEW}ffff|" + json.dumps({"9000/tcp": [{"HostIp": "", "HostPort": "9001"}]})
    rows = {r["name"]: r for r in await catalog.list_services()}
    assert rows["neu"]["published_ports"] == [9001]
    assert exec_handle.commands[-1].endswith(ID_NEW)
    assert catalog._stopped_ports == {"h1": {ID_NEW: [9001]}}


@pytest.mark.asyncio
async def test_a_failing_inspect_keeps_the_listing_and_is_retried():
    ps = _ps_line("immich", "exited", "Exited (0) 1 hour ago", container_id=ID_STOP)

    class _Failing(_CountingExec):
        async def run(self, host, command: str, *, timeout_s: int = 60):
            if command.startswith("docker inspect"):
                self.commands.append(command)
                raise TimeoutError("zu langsam")
            return await super().run(host, command, timeout_s=timeout_s)

    exec_handle = _Failing(ps, "")
    catalog = DockerServiceCatalog(_ctx(exec_handle))
    rows = await catalog.list_services()
    assert [r["name"] for r in rows] == ["immich"] and rows[0]["published_ports"] == []
    await catalog.list_services()
    assert sum(c.startswith("docker inspect") for c in exec_handle.commands) == 2, "nichts gemerkt, also neuer Versuch"


@pytest.mark.asyncio
async def test_readable_lines_count_even_if_one_container_vanished():
    """Zwischen `docker ps` und `docker inspect` verschwindet ein Container: Exit 1, die anderen Zeilen gelten trotzdem."""
    ps = "\n".join([
        _ps_line("immich", "exited", "Exited (0) 1 hour ago", container_id=ID_STOP),
        _ps_line("weg", "exited", "Exited (0) 1 hour ago", container_id=ID_NEW),
        _ps_line("loop", "restarting", "Restarting (1) 3 seconds ago", container_id=ID_RUN),
    ])

    class _Partial(_CountingExec):
        async def run(self, host, command: str, *, timeout_s: int = 60):
            if command.startswith("docker inspect"):
                self.commands.append(command)
                lines = [
                    f"{ID_STOP}ffff|" + json.dumps({"2283/tcp": [{"HostIp": "", "HostPort": "2283"}]}),
                    f"{ID_RUN}ffff|" + json.dumps({"80/tcp": [{"HostIp": "", "HostPort": "8088"}]}),
                ]
                return _FakeExecResult(1, "\n".join(lines))
            return await super().run(host, command, timeout_s=timeout_s)

    exec_handle = _Partial(ps, "")
    catalog = DockerServiceCatalog(_ctx(exec_handle))
    rows = {r["name"]: r for r in await catalog.list_services()}
    assert rows["immich"]["published_ports"] == [2283]
    assert rows["loop"]["published_ports"] == [8088], "auch ein Container, der staendig neu startet, zeigt in ps keine Ports"
    assert rows["weg"]["published_ports"] == []
    await catalog.list_services()
    inspects = [c for c in exec_handle.commands if c.startswith("docker inspect")]
    assert len(inspects) == 2 and inspects[-1].endswith(ID_NEW), "nur der fehlende wird erneut gelesen"


@pytest.mark.asyncio
async def test_an_unreachable_server_keeps_its_address():
    class _Down(_CountingExec):
        async def run(self, host, command: str, *, timeout_s: int = 60):
            raise ConnectionError("weg")

    rows = await DockerServiceCatalog(_ctx(_Down("", ""))).list_services()
    assert len(rows) == 1 and rows[0]["unreachable"] is True and rows[0]["host_address"] == "10.0.0.5"


def test_module_level_names_of_the_catalog_are_defined_once():
    """Eine zweite Konstante gleichen Namens weiter unten im Modul (etwa aus einem anderen Zweig) ueberschriebe die erste
    still -- so waere die Port-Pruefung gestoppter Container bei kurzen IDs nie gelaufen."""
    import ast

    import nodvard_deck_ext_docker.capabilities as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    assert sorted({n for n in names if names.count(n) > 1}) == []
