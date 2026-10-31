"""Dateimanager-Orte der Compose-Projekte: Projektordner aus `docker ps`, Zwischenspeicher des Katalogs, Orte und die Kern-Route."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, docker_on, result  # noqa: F401 - Fixtures
from nodvard_sdk import Host, HostStatus
from nodvard_sdk.capabilities import FileSourceProvider


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _m():
    from nodvard_deck_ext_docker import capabilities, commands, places

    return SimpleNamespace(caps=capabilities, commands=commands, places=places)


def _line(project: str = "shop", folder: str = "/srv/shop", *, ports: str = "") -> str:
    """Eine Zeile im Format von `commands.PS_FORMAT`; Reihenfolge und Zahl der Felder kommen aus dem Format selbst."""
    values = {
        "{{.ID}}": "0123456789ab",
        "{{.Names}}": "web",
        "{{.State}}": "running",
        "{{.Status}}": "Up 2 hours",
        "{{.Image}}": "img:latest",
        '{{.Label "com.docker.compose.project"}}': project,
        "{{.Ports}}": ports,
        '{{.Label "com.docker.compose.project.working_dir"}}': folder,
    }
    return "|".join(values[field] for field in _m().commands.PS_FORMAT.split("|"))


def _parse(*lines: str) -> dict[str, str]:
    return _m().caps.parse_compose_dirs("\n".join(lines))


def test_a_line_with_all_fields_gives_the_project_and_its_folder():
    shop = _line("shop", "/srv/shop", ports="0.0.0.0:8080->80/tcp, :::8080->80/tcp")
    assert _parse(shop, _line("kuma", "/opt/stacks/kuma")) == {"shop": "/srv/shop", "kuma": "/opt/stacks/kuma"}


def test_blanks_around_project_and_folder_are_dropped():
    assert _parse(_line(" shop ", "  /srv/shop ")) == {"shop": "/srv/shop"}


@pytest.mark.parametrize(
    ("project", "folder"),
    [
        ("", "/srv/shop"),
        ("   ", "/srv/shop"),
        ("shop", ""),
        ("shop", "stacks/shop"),
        ("shop", "~/stacks"),
        ("shop", "/data/compose/12"),
        ("shop", "/data/compose/"),
        ("shop", "/srv/../etc"),
        ("shop", "/.."),
        ("shop", "/srv/.."),
        ("shop", "/srv/a\tb"),
        ("shop", "/srv/a\x00b"),
        ("shop", "/srv/a\x1fb"),
        ("shop", "/srv/a\x7fb"),
        ("shop", "/srv/a\x85b"),
        ("shop", "/srv/a\x9bb"),
    ],
)
def test_lines_without_project_or_usable_folder_are_skipped(project, folder):
    assert _parse(_line(project, folder)) == {}


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("/srv/shop/", "/srv/shop"),
        ("/srv/shop//", "/srv/shop"),
        ("/", "/"),
        ("//", "/"),
        ("///", "/"),
        ("/srv/a..b", "/srv/a..b"),
        ("/srv/..versteckt", "/srv/..versteckt"),
        ("/data/composer/shop", "/data/composer/shop"),
        ("/data/compose", "/data/compose"),
        ("/srv/mit leerzeichen/größe", "/srv/mit leerzeichen/größe"),
        ("/srv/a|b", "/srv/a|b"),
        ("/srv/a\u2028b", "/srv/a\u2028b"),
        ("/srv/shop\r", "/srv/shop"),
    ],
)
def test_usable_folders_lose_only_the_trailing_slash(folder, expected):
    assert _parse(_line("shop", folder)) == {"shop": expected}


def test_the_first_folder_of_a_project_wins():
    assert _parse(_line("shop", "/srv/erster"), _line("shop", "/srv/zweiter"), _line("shop", "relativ")) == {"shop": "/srv/erster"}


def test_a_skipped_line_does_not_claim_its_project():
    assert _parse(_line("shop", "/data/compose/3"), _line("shop", "/srv/shop")) == {"shop": "/srv/shop"}


def test_lines_with_too_few_fields_are_skipped():
    fields = _line("shop", "/srv/shop").split("|")
    older = "|".join(fields[:7])
    oldest = "|".join(fields[:6])
    assert _parse(older, oldest, "", "kaputt", _line("gut", "/srv/gut")) == {"gut": "/srv/gut"}


# --- Zwischenspeicher des Katalogs -----------------------------------------------------------------


class _Exec:
    """`ctx.exec.run`: antwortet mit `answer` oder wirft sie, und merkt sich jeden Aufruf."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    async def run(self, host, command, *, timeout_s=60):
        self.calls.append((host.id, command))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Hosts:
    def __init__(self, *hosts: Host) -> None:
        self._hosts = {host.id: host for host in hosts}

    async def get(self, host_id: str) -> Host | None:
        return self._hosts.get(host_id)


class _Settings:
    def __init__(self, values: dict | None) -> None:
        self._values = values or {}

    async def get(self) -> dict:
        return self._values


def _host(host_id: str = "h1", *, tags=("docker",), status=HostStatus.UP) -> Host:
    return Host(id=host_id, name=host_id, display_name=host_id, address="192.0.2.30", status=status, tags=list(tags))


def _ctx(*hosts: Host, answer=None, settings: dict | None = None):
    return SimpleNamespace(hosts=_Hosts(*hosts), exec=_Exec(answer), settings=_Settings(settings))


def _catalog(ctx):
    return _m().caps.DockerServiceCatalog(ctx)


def _stale(monkeypatch) -> None:
    monkeypatch.setattr(_m().caps, "COMPOSE_DIRS_TTL_S", 0.0)


def _clock(monkeypatch) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(_m().caps, "time", SimpleNamespace(monotonic=lambda: now[0]))
    return now


@pytest.mark.asyncio
async def test_a_fresh_state_is_returned_without_asking_the_server_again():
    host = _host()
    ctx = _ctx(host, answer=result(_line("shop", "/srv/shop")))
    catalog = _catalog(ctx)

    first = await catalog.compose_dirs(host)
    ctx.exec.answer = result(_line("neu", "/srv/neu"))
    second = await catalog.compose_dirs(host)

    assert first == second == {"shop": "/srv/shop"}
    assert ctx.exec.calls == [("h1", _m().commands.ps_all())]


@pytest.mark.asyncio
async def test_every_host_has_its_own_state():
    one, two = _host("h1"), _host("h2")
    ctx = _ctx(one, two, answer=result(_line("shop", "/srv/shop")))
    catalog = _catalog(ctx)

    await catalog.compose_dirs(one)
    ctx.exec.answer = result(_line("kuma", "/opt/kuma"))

    assert await catalog.compose_dirs(two) == {"kuma": "/opt/kuma"}
    assert await catalog.compose_dirs(one) == {"shop": "/srv/shop"}
    assert [host_id for host_id, _ in ctx.exec.calls] == ["h1", "h2"]


@pytest.mark.asyncio
async def test_a_state_older_than_the_limit_is_read_again(monkeypatch):
    host = _host()
    ctx = _ctx(host, answer=result(_line("shop", "/srv/shop")))
    catalog = _catalog(ctx)
    await catalog.compose_dirs(host)

    _stale(monkeypatch)
    ctx.exec.answer = result(_line("neu", "/srv/neu"))

    assert await catalog.compose_dirs(host) == {"neu": "/srv/neu"}
    assert len(ctx.exec.calls) == 2


@pytest.mark.asyncio
async def test_a_down_host_without_an_old_state_is_not_asked():
    down = _host(status=HostStatus.DOWN)
    ctx = _ctx(down, answer=result(_line("shop", "/srv/shop")))

    assert await _catalog(ctx).compose_dirs(down) == {}
    assert ctx.exec.calls == []


@pytest.mark.asyncio
async def test_a_host_that_went_down_keeps_showing_its_old_state(monkeypatch):
    up = _host()
    ctx = _ctx(up, answer=result(_line("shop", "/srv/shop")))
    catalog = _catalog(ctx)
    await catalog.compose_dirs(up)
    _stale(monkeypatch)

    assert await catalog.compose_dirs(up.model_copy(update={"status": HostStatus.DOWN})) == {"shop": "/srv/shop"}
    assert len(ctx.exec.calls) == 1


def _failure(kind: str):
    return {
        "no answer": TimeoutError("keine Antwort"),
        "docker error": result(stderr="Cannot connect to the Docker daemon", code=1),
        "exit code with output": result(stdout=_line("neu", "/srv/neu"), code=125),
    }[kind]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["no answer", "docker error", "exit code with output"])
async def test_a_failed_read_keeps_the_old_state_and_is_tried_again_next_time(monkeypatch, kind):
    now = _clock(monkeypatch)
    host = _host()
    ctx = _ctx(host, answer=result(_line("shop", "/srv/shop")))
    catalog = _catalog(ctx)
    await catalog.compose_dirs(host)
    now[0] += _m().caps.COMPOSE_DIRS_TTL_S + 1

    ctx.exec.answer = _failure(kind)
    assert await catalog.compose_dirs(host) == {"shop": "/srv/shop"}
    assert await catalog.compose_dirs(host) == {"shop": "/srv/shop"}
    assert len(ctx.exec.calls) == 3

    ctx.exec.answer = result(_line("kuma", "/opt/kuma"))
    assert await catalog.compose_dirs(host) == {"kuma": "/opt/kuma"}


@pytest.mark.asyncio
async def test_a_timeout_without_an_old_state_reaches_the_caller():
    host = _host()
    ctx = _ctx(host, answer=TimeoutError("keine Antwort"))
    catalog = _catalog(ctx)

    with pytest.raises(TimeoutError, match="keine Antwort"):
        await catalog.compose_dirs(host)

    ctx.exec.answer = result(_line("shop", "/srv/shop"))
    assert await catalog.compose_dirs(host) == {"shop": "/srv/shop"}
    assert len(ctx.exec.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("with_output", "stderr", "code", "message"),
    [
        (False, " Cannot connect to the Docker daemon \n", 1, "Cannot connect to the Docker daemon"),
        (False, "", 3, "Exit-Code 3"),
        (True, "  ", 1, "Exit-Code 1"),
    ],
)
async def test_a_failing_docker_ps_without_an_old_state_names_the_reason(with_output, stderr, code, message):
    host = _host()
    ctx = _ctx(host, answer=result(_line("shop", "/srv/shop") if with_output else "", stderr, code))
    catalog = _catalog(ctx)

    with pytest.raises(RuntimeError) as raised:
        await catalog.compose_dirs(host)

    assert str(raised.value) == message
    ctx.exec.answer = result(_line("shop", "/srv/shop"))
    assert await catalog.compose_dirs(host) == {"shop": "/srv/shop"}


# --- Orte für den Dateimanager ---------------------------------------------------------------------------


def _places(ctx):
    return _m().places.DockerFilePlaces(ctx, _catalog(ctx))


@pytest.mark.asyncio
@pytest.mark.parametrize("tags", [[], ["proxmox"], ["docker-bedienen"]])
async def test_a_host_without_the_docker_marker_has_no_places_and_is_not_asked(tags):
    ctx = _ctx(_host("h1", tags=tags), answer=result(_line()))

    assert await _places(ctx).places_for_host("h1") == []
    assert ctx.exec.calls == []


@pytest.mark.asyncio
async def test_the_marker_comes_from_the_setting_and_falls_back_to_docker():
    custom = _ctx(_host("h1", tags=["docker"]), _host("h2", tags=["container"]), answer=result(_line()), settings={"docker_host_tag": "container"})
    places = _places(custom)
    assert await places.places_for_host("h1") == []
    assert [p.path for p in await places.places_for_host("h2")] == ["/srv/shop"]

    blank = _ctx(_host("h1"), answer=result(_line()), settings={"docker_host_tag": ""})
    assert [p.path for p in await _places(blank).places_for_host("h1")] == ["/srv/shop"]


@pytest.mark.asyncio
async def test_a_docker_host_gets_one_sorted_place_per_project():
    ctx = _ctx(_host(), answer=result("\n".join([_line("zeta", "/srv/zeta"), _line("alpha", "/opt/alpha/"), _line("mitte", "/srv/mitte")])))

    places = await _places(ctx).places_for_host("h1")

    assert [(p.label, p.path, p.icon, p.hint) for p in places] == [
        ("Docker: alpha", "/opt/alpha", "boxes", "Ordner des Compose-Projekts „alpha“"),
        ("Docker: mitte", "/srv/mitte", "boxes", "Ordner des Compose-Projekts „mitte“"),
        ("Docker: zeta", "/srv/zeta", "boxes", "Ordner des Compose-Projekts „zeta“"),
    ]


@pytest.mark.asyncio
async def test_the_number_of_places_is_limited_to_the_first_projects_in_order():
    limit = _m().caps.MAX_COMPOSE_DIRS
    lines = [_line(f"projekt-{i:02d}", f"/srv/p{i}") for i in reversed(range(limit + 5))]
    ctx = _ctx(_host(), answer=result("\n".join(lines)))

    places = await _places(ctx).places_for_host("h1")

    assert [p.label for p in places] == [f"Docker: projekt-{i:02d}" for i in range(limit)]


@pytest.mark.asyncio
async def test_a_docker_host_without_compose_projects_has_no_places():
    ctx = _ctx(_host(), answer=result(_line("", "")))

    assert await _places(ctx).places_for_host("h1") == []


# --- über den Kern ----------------------------------------------------------------------------------------


class _Sources:
    def __init__(self, *sources) -> None:
        self._sources = list(sources)

    async def file_sources(self) -> list:
        return self._sources


def _ssh_source(host_id: str) -> SimpleNamespace:
    return SimpleNamespace(source_id=f"ssh-sftp:{host_id}", label="SSH", icon="server", host_id=host_id, required_permission="hosts.execute")


@pytest.mark.asyncio
async def test_the_core_route_gives_a_docker_host_its_project_folders(client, docker_on, monkeypatch):
    from nodvard_deck.ext.runtime import get_extension_runtime

    get_extension_runtime().capabilities.provide(
        "terminal", FileSourceProvider, _Sources(_ssh_source(docker_on.host), _ssh_source(docker_on.other))
    )
    asked: list[tuple[str, str]] = []

    async def run(host, command, *, timeout_s=60):
        asked.append((host.id, command))
        return result("\n".join([_line("zebra", "/srv/zebra"), _line("alpha", "/opt/stacks/alpha/"), _line("portainer", "/data/compose/7")]))

    monkeypatch.setattr(docker_on.handle, "run", run)

    answer = await client.get(f"/api/v1/files/places?host_id={docker_on.host}", headers=docker_on.owner)

    assert answer.status_code == 200, answer.text
    assert answer.json() == [
        {"label": "Docker: alpha", "path": "/opt/stacks/alpha", "icon": "boxes", "hint": "Ordner des Compose-Projekts „alpha“"},
        {"label": "Docker: zebra", "path": "/srv/zebra", "icon": "boxes", "hint": "Ordner des Compose-Projekts „zebra“"},
    ]
    assert asked == [(docker_on.host, _m().commands.ps_all())]

    elsewhere = await client.get(f"/api/v1/files/places?host_id={docker_on.other}", headers=docker_on.owner)
    assert elsewhere.status_code == 200 and elsewhere.json() == []
    assert len(asked) == 1
