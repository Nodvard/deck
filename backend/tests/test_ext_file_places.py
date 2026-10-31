"""Die Anbieter der "Wichtigen Orte" (`FilePlaceProvider`) in den Erweiterungen: Docker-Projektordner
(docker), Sicherungsordner eines Proxmox-Knotens (proxmox), Protokolle (system).

Jeder Anbieter wird mit genau den Daten gefuettert, die seine Erweiterung ohnehin hat (die Ausgabe von
`docker ps`, die Speicherliste eines Knotens, der Host-Eintrag) -- sie stellen keine eigenen Fragen an den Server.
"""

from __future__ import annotations

import sys
import types
from enum import Enum
from pathlib import Path

import pytest
from nodvard_sdk import FilePlace
from nodvard_sdk.capabilities import FilePlaceProvider

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
_EXTENSIONS = ("docker", "proxmox", "system")


@pytest.fixture
def ext(monkeypatch):
    """Die drei Erweiterungen sind keine installierten Pakete: Pfad nur für diese Tests, danach sind die Module vergessen."""
    for name in _EXTENSIONS:
        monkeypatch.syspath_prepend(str(REPO_EXTENSIONS_DIR / name / "src"))
    from nodvard_deck_ext_docker import capabilities as docker_caps
    from nodvard_deck_ext_docker.places import DockerFilePlaces
    from nodvard_deck_ext_proxmox import places as proxmox_places
    from nodvard_deck_ext_system import SystemFilePlaces

    yield types.SimpleNamespace(
        docker_caps=docker_caps,
        proxmox_places=proxmox_places,
        DockerFilePlaces=DockerFilePlaces,
        SystemFilePlaces=SystemFilePlaces,
    )
    for module in list(sys.modules):
        if module.startswith(tuple(f"nodvard_deck_ext_{name}" for name in _EXTENSIONS)):
            del sys.modules[module]


class _Status(Enum):
    UP = "up"
    DOWN = "down"


def _host(host_id: str = "h1", *, tags=("docker",), status=_Status.UP, **extra):
    return types.SimpleNamespace(
        id=host_id, name=host_id, address="192.168.2.30", display_name=host_id, tags=list(tags), status=status, **extra
    )


class _Hosts:
    def __init__(self, *hosts) -> None:
        self._hosts = {h.id: h for h in hosts}

    async def get(self, host_id: str):
        return self._hosts.get(host_id)

    async def list(self, *, tag: str | None = None):
        return [h for h in self._hosts.values() if tag is None or tag in h.tags]


class _Result:
    def __init__(self, stdout: str = "", exit_code: int = 0, stderr: str = "") -> None:
        self.stdout, self.exit_code, self.stderr = stdout, exit_code, stderr


class _Exec:
    """Antwortet je Host mit einer Ausgabe (oder wirft) und merkt sich jeden Befehl."""

    def __init__(self, outputs: dict) -> None:
        self.outputs = outputs
        self.commands: list[tuple[str, str]] = []

    async def run(self, host, command: str, *, timeout_s: int = 60):
        self.commands.append((host.id, command))
        out = self.outputs[host.id]
        if isinstance(out, Exception):
            raise out
        return out


class _Settings:
    def __init__(self, values: dict | None = None) -> None:
        self._values = values or {}

    async def get(self) -> dict:
        return self._values


def _ctx(*hosts, outputs: dict | None = None, settings: dict | None = None, ext_id: str = "x"):
    return types.SimpleNamespace(
        hosts=_Hosts(*hosts), exec=_Exec(outputs or {}), settings=_Settings(settings), ext_id=ext_id
    )


def _ps(name: str, project: str = "", folder: str | None = "", ports: str = "") -> str:
    """Eine Zeile von `commands.ps_all()`; `folder=None` = das alte Format mit sieben Feldern."""
    line = f"0123456789ab|{name}|running|Up|img:latest|{project}|{ports}"
    return line if folder is None else f"{line}|{folder}"


# --- docker: Projektordner der Compose-Projekte --------------------------------------------------------


def test_the_docker_ps_format_asks_for_the_project_folder_in_the_same_call(ext):
    assert 'com.docker.compose.project.working_dir' in ext.docker_caps.commands.PS_FORMAT
    assert ext.docker_caps.commands.PS_FORMAT.count("|") == 7  # acht Felder, der Ordner steht ganz hinten


def test_compose_dirs_are_read_from_the_eighth_field(ext):
    text = "\n".join([
        _ps("web", "shop", "/srv/shop", ports="0.0.0.0:8080->80/tcp, :::8080->80/tcp"),
        _ps("db", "shop", "/srv/shop"),
        _ps("kuma", "uptime-kuma", "/opt/stacks/kuma/"),
        _ps("solo", "", ""),
        _ps("alt", "shop-alt", None),  # altes Format: kein Ordner bekannt
    ])
    assert ext.docker_caps.parse_compose_dirs(text) == {"shop": "/srv/shop", "uptime-kuma": "/opt/stacks/kuma"}


def test_compose_dirs_skip_folders_that_do_not_exist_on_the_host(ext):
    text = "\n".join([
        _ps("a", "von-portainer", "/data/compose/12"),
        _ps("b", "relativ", "stacks/b"),
        _ps("c", "hoch", "/srv/../etc"),
        _ps("d", "steuer", "/srv/a\tb"),
        _ps("e", "gut", "/srv/gut"),
        _ps("f", "gut", "/srv/zweiter"),  # je Projekt zaehlt der erste Ordner
    ])
    assert ext.docker_caps.parse_compose_dirs(text) == {"gut": "/srv/gut"}


def test_a_pipe_in_the_folder_name_stays_in_the_folder(ext):
    assert ext.docker_caps.parse_compose_dirs(_ps("a", "p", "/srv/a|b")) == {"p": "/srv/a|b"}


@pytest.mark.asyncio
async def test_the_service_list_still_reads_all_formats_and_keeps_urls_and_projects(ext):
    host = _host()
    ctx = _ctx(host, outputs={"h1": _Result("\n".join([
        _ps("web", "shop", "/srv/shop", ports="0.0.0.0:8080->80/tcp"),
        _ps("alt", "shop-alt", None, ports="0.0.0.0:9000->9000/tcp"),
        "0123456789ab|sehr-alt|running|Up|img:latest|0.0.0.0:8070->8070/tcp",  # das erste Format mit sechs Feldern
    ]))})
    rows = {s["name"]: s for s in await ext.docker_caps.DockerServiceCatalog(ctx).list_services()}
    assert (rows["web"]["compose_project"], rows["web"]["url"]) == ("shop", "http://192.168.2.30:8080")
    assert (rows["alt"]["compose_project"], rows["alt"]["url"]) == ("shop-alt", "http://192.168.2.30:9000")
    assert (rows["sehr-alt"]["compose_project"], rows["sehr-alt"]["url"]) == (None, "http://192.168.2.30:8070")
    assert not any("working_dir" in key or "/srv" in str(value) for row in rows.values() for key, value in row.items()), (
        "der Ordner gehoert nicht in die Zeilen, die Cockpit und Widget jedem mit hosts.read zeigen"
    )


@pytest.mark.asyncio
async def test_the_places_come_from_the_state_the_service_list_already_read(ext):
    host = _host()
    ctx = _ctx(host, outputs={"h1": _Result(_ps("web", "shop", "/srv/shop") + "\n" + _ps("kuma", "uptime-kuma", "/opt/kuma"))})
    catalog = ext.docker_caps.DockerServiceCatalog(ctx)
    await catalog.list_services()
    assert len(ctx.exec.commands) == 1

    places = await ext.DockerFilePlaces(ctx, catalog).places_for_host("h1")

    assert len(ctx.exec.commands) == 1  # kein zweiter Befehl auf dem Server
    assert [(p.label, p.path, p.icon) for p in places] == [
        ("Docker: shop", "/srv/shop", "boxes"),
        ("Docker: uptime-kuma", "/opt/kuma", "boxes"),
    ]
    assert places[0].hint == "Ordner des Compose-Projekts „shop“"


@pytest.mark.asyncio
async def test_without_a_remembered_state_the_places_read_the_same_docker_ps_once(monkeypatch, ext):
    host = _host()
    ctx = _ctx(host, outputs={"h1": _Result(_ps("web", "shop", "/srv/shop"))})
    provider = ext.DockerFilePlaces(ctx, ext.docker_caps.DockerServiceCatalog(ctx))

    first = await provider.places_for_host("h1")
    second = await provider.places_for_host("h1")

    assert [p.path for p in first] == [p.path for p in second] == ["/srv/shop"]
    assert len(ctx.exec.commands) == 1  # der zweite Aufruf nimmt den gemerkten Stand
    command = ctx.exec.commands[0][1]
    assert command == ext.docker_caps.commands.ps_all()  # derselbe Befehl wie in der Service-Liste

    monkeypatch.setattr(ext.docker_caps, "COMPOSE_DIRS_TTL_S", 0.0)
    await provider.places_for_host("h1")
    assert len(ctx.exec.commands) == 2  # ein zu alter Stand wird neu gelesen


@pytest.mark.asyncio
async def test_an_old_state_survives_a_host_that_stops_answering(monkeypatch, ext):
    host = _host()
    ctx = _ctx(host, outputs={"h1": _Result(_ps("web", "shop", "/srv/shop"))})
    catalog = ext.docker_caps.DockerServiceCatalog(ctx)
    await catalog.list_services()
    monkeypatch.setattr(ext.docker_caps, "COMPOSE_DIRS_TTL_S", 0.0)  # der Stand gilt als zu alt, also wird neu gelesen

    ctx.exec.outputs["h1"] = TimeoutError("keine Antwort")
    assert await catalog.compose_dirs(host) == {"shop": "/srv/shop"}
    ctx.exec.outputs["h1"] = _Result("", exit_code=1, stderr="Cannot connect to the Docker daemon")
    assert await catalog.compose_dirs(host) == {"shop": "/srv/shop"}
    assert len(ctx.exec.commands) == 3


@pytest.mark.asyncio
async def test_a_host_that_never_answered_gives_an_error_the_core_turns_into_a_log_line(ext):
    host = _host()
    ctx = _ctx(host, outputs={"h1": TimeoutError("keine Antwort")})
    with pytest.raises(TimeoutError):
        await ext.DockerFilePlaces(ctx, ext.docker_caps.DockerServiceCatalog(ctx)).places_for_host("h1")


@pytest.mark.asyncio
async def test_a_switched_off_host_is_not_asked_at_all(ext):
    host = _host(status=_Status.DOWN)
    ctx = _ctx(host, outputs={"h1": _Result(_ps("web", "shop", "/srv/shop"))})
    assert await ext.DockerFilePlaces(ctx, ext.docker_caps.DockerServiceCatalog(ctx)).places_for_host("h1") == []
    assert ctx.exec.commands == []


@pytest.mark.asyncio
async def test_only_docker_hosts_get_docker_places(ext):
    other = _host("h2", tags=("proxmox",))
    custom = _host("h3", tags=("container",))
    ctx = _ctx(other, custom, outputs={"h2": _Result(_ps("web", "shop", "/srv/shop")), "h3": _Result(_ps("web", "shop", "/srv/shop"))},
               settings={"docker_host_tag": "container"})
    provider = ext.DockerFilePlaces(ctx, ext.docker_caps.DockerServiceCatalog(ctx))

    assert await provider.places_for_host("unbekannt") == []
    assert await provider.places_for_host("h2") == []  # nicht mit dem eingestellten Tag markiert
    assert [p.path for p in await provider.places_for_host("h3")] == ["/srv/shop"]
    assert [host for host, _ in ctx.exec.commands] == ["h3"]


@pytest.mark.asyncio
async def test_the_number_of_docker_places_is_limited(ext):
    lines = "\n".join(_ps(f"c{i}", f"projekt-{i:02d}", f"/srv/p{i}") for i in range(ext.docker_caps.MAX_COMPOSE_DIRS + 5))
    ctx = _ctx(_host(), outputs={"h1": _Result(lines)})
    places = await ext.DockerFilePlaces(ctx, ext.docker_caps.DockerServiceCatalog(ctx)).places_for_host("h1")
    assert len(places) == ext.docker_caps.MAX_COMPOSE_DIRS


# --- proxmox: Sicherungsordner --------------------------------------------------------------------------------


def _pool(storage: str, kind: str, content: str = "backup,iso", **extra) -> dict:
    return {"storage": storage, "type": kind, "content": content, "active": 1, "enabled": 1, **extra}


@pytest.mark.parametrize(
    ("pool", "folder"),
    [
        (_pool("local", "dir"), "/var/lib/vz/dump"),
        (_pool("nas", "nfs"), "/mnt/pve/nas/dump"),
        (_pool("freigabe", "cifs"), "/mnt/pve/freigabe/dump"),
        (_pool("ceph", "cephfs"), "/mnt/pve/ceph/dump"),
        (_pool("usb", "dir"), None),  # Verzeichnis mit eigenem Pfad: den nennt Proxmox nicht
        (_pool("local-zfs", "zfspool"), None),
        (_pool("pbs", "pbs"), None),
        (_pool("lvm", "lvmthin"), None),
        (_pool("local", "zfspool"), None),
        (_pool("a/b", "nfs"), None),
        ({"type": "nfs"}, None),
    ],
)
def test_the_backup_folder_is_only_named_where_proxmox_fixes_it(pool, folder, ext):
    assert ext.proxmox_places.backup_folder(pool) == folder


def test_backup_places_need_the_backup_content_and_a_usable_storage(ext):
    pools = [
        _pool("local", "dir", "iso,vztmpl,backup"),
        _pool("nas", "nfs", "images,rootdir"),  # nimmt keine Sicherungen auf
        _pool("alt", "nfs", "backup", active=0),
        _pool("aus", "cifs", "backup", enabled=0),
        _pool("usb", "dir", "backup"),
        _pool("netz", "nfs", "backup", active="1"),
    ]
    places = ext.proxmox_places.backup_places(pools)
    assert [(p.label, p.path, p.icon) for p in places] == [
        ("Sicherungen (local)", "/var/lib/vz/dump", "database-backup"),
        ("Sicherungen (netz)", "/mnt/pve/netz/dump", "database-backup"),
    ]
    assert places[0].hint == "Proxmox-Sicherungen auf dem Speicher „local“"


class _Connector:
    def __init__(self, pools: list[dict] | Exception) -> None:
        self._pools = pools
        self.nodes: list[str] = []

    async def node_storage(self, node: str):
        self.nodes.append(node)
        if isinstance(self._pools, Exception):
            raise self._pools
        return self._pools


def _proxmox_ctx(*hosts, connector: _Connector, monkeypatch, known_connections=("pve",)):
    ctx = _ctx(*hosts, ext_id="proxmox")
    asked: list[str] = []

    async def build_connector(_ctx, name: str):
        asked.append(name)
        if name not in known_connections:
            raise RuntimeError(f"Proxmox-Verbindung '{name}' ist nicht (mehr) konfiguriert.")
        return connector

    monkeypatch.setattr("nodvard_deck_ext_proxmox.places.build_connector", build_connector)
    return ctx, asked


@pytest.mark.asyncio
async def test_a_proxmox_node_gets_the_folders_of_its_backup_storages(monkeypatch, ext):
    node = _host("n1", provider_ext_id="proxmox", provider_ref="pve/node/pve1")
    connector = _Connector([_pool("local", "dir"), _pool("usb", "dir", "backup"), _pool("nas", "nfs", "backup")])
    ctx, asked = _proxmox_ctx(node, connector=connector, monkeypatch=monkeypatch)

    places = await ext.proxmox_places.ProxmoxFilePlaces(ctx).places_for_host("n1")

    assert [p.path for p in places] == ["/var/lib/vz/dump", "/mnt/pve/nas/dump"]
    assert asked == ["pve"] and connector.nodes == ["pve1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        _host("g1", provider_ext_id="proxmox", provider_ref="pve/qemu/pve1/100"),  # ein Gast hat keinen Sicherungsordner
        _host("g2", provider_ext_id="proxmox", provider_ref="pve/lxc/pve1/101"),
        _host("o1", provider_ext_id="andere", provider_ref="pve/node/pve1"),  # eingelesen von einer anderen Erweiterung
        _host("o2", provider_ext_id=None, provider_ref=None),  # von Hand angelegt
        _host("k1", provider_ext_id="proxmox", provider_ref="kaputt"),
    ],
)
async def test_guests_and_foreign_servers_get_no_proxmox_places(host, monkeypatch, ext):
    connector = _Connector([_pool("local", "dir")])
    ctx, asked = _proxmox_ctx(host, connector=connector, monkeypatch=monkeypatch)

    assert await ext.proxmox_places.ProxmoxFilePlaces(ctx).places_for_host(host.id) == []
    assert asked == [] and connector.nodes == []


@pytest.mark.asyncio
async def test_an_unknown_host_and_a_missing_connection(monkeypatch, ext):
    node = _host("n1", provider_ext_id="proxmox", provider_ref="alt/node/pve1")
    ctx, _ = _proxmox_ctx(node, connector=_Connector([]), monkeypatch=monkeypatch)
    provider = ext.proxmox_places.ProxmoxFilePlaces(ctx)

    assert await provider.places_for_host("gibt-es-nicht") == []
    with pytest.raises(RuntimeError, match="nicht .mehr. konfiguriert"):
        await provider.places_for_host("n1")  # der Kern macht daraus eine Log-Zeile und laesst die Orte weg


# --- system: Protokolle ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("os_family", "has_credential", "expected"),
    [("linux", True, ["/var/log"]), ("windows", True, []), ("linux", False, [])],
)
async def test_system_names_the_log_folder_of_linux_servers_with_access(os_family, has_credential, expected, ext):
    ctx = _ctx(_host("h1", os_family=os_family, has_credential=has_credential))
    provider = ext.SystemFilePlaces(ctx)

    assert [p.path for p in await provider.places_for_host("h1")] == expected
    assert await provider.places_for_host("gibt-es-nicht") == []
    assert ctx.exec.commands == []  # kein Befehl auf dem Server


@pytest.mark.asyncio
async def test_system_place_text(ext):
    (place,) = await ext.SystemFilePlaces(_ctx(_host("h1", os_family="linux", has_credential=True))).places_for_host("h1")
    assert (place.label, place.icon, place.hint) == ("Protokolle", "file-text", "Systemprotokolle dieses Servers")


# --- alle: erfuellen das Protokoll und lassen sich beim Kern anmelden -----------------------------------------


def test_every_provider_is_a_file_place_provider_and_nothing_else_by_accident(ext):
    from nodvard_sdk import ALL_CAPABILITIES

    ctx = _ctx()
    providers = [
        ext.SystemFilePlaces(ctx),
        ext.proxmox_places.ProxmoxFilePlaces(ctx),
        ext.DockerFilePlaces(ctx, ext.docker_caps.DockerServiceCatalog(ctx)),
    ]
    for provider in providers:
        matched = [proto for proto in ALL_CAPABILITIES if isinstance(provider, proto)]
        assert matched == [FilePlaceProvider], (provider, matched)
    assert FilePlace(label="a", path="/a").icon == "folder"
