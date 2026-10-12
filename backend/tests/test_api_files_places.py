"""`GET /files/places` -- die "Wichtigen Orte" eines Servers im Dateimanager (docs/04-API.md, "Dateien").

Getestet gegen Fake-Anbieter (`FilePlaceProvider`) und Fake-Dateiquellen: Der Kern sammelt, entdoppelt,
begrenzt und schuetzt sich vor Anbietern, die scheitern oder haengen -- das ist allen Anbietern gemeinsam.
Die echten Anbieter der Erweiterungen stehen in `test_ext_file_places.py`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import types

import pytest
from nodvard_sdk import FilePlace
from nodvard_sdk.capabilities import FilePlaceProvider, FileSourceProvider

from nodvard_deck.api.v1 import files as files_api
from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
from nodvard_deck.services import hosts as hosts_service

PASSWORD = "correct-horse-battery"


class _Places:
    """Ein Anbieter mit festen Orten; optional wirft er oder haengt."""

    def __init__(self, places=(), *, error: Exception | None = None, delay_s: float = 0.0) -> None:
        self._places = list(places)
        self._error = error
        self._delay_s = delay_s
        self.asked: list[str] = []

    async def places_for_host(self, host_id: str):
        self.asked.append(host_id)
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return self._places


class _Sources:
    def __init__(self, *sources) -> None:
        self._sources = list(sources)

    async def file_sources(self) -> list:
        return self._sources


def _ssh_source(host_id: str, *, permission: str | None = "hosts.execute"):
    source = types.SimpleNamespace(source_id=f"ssh-sftp:{host_id}", label="SSH", icon="server", host_id=host_id)
    if permission is not None:
        source.required_permission = permission
    return source


def _place(label: str, path: str, **extra) -> FilePlace:
    return FilePlace(label=label, path=path, **extra)


@pytest.fixture(autouse=True)
def _clean_runtime():
    reset_extension_runtime()
    yield
    reset_extension_runtime()


def _provide(ext_id: str, protocol, implementation) -> None:
    get_extension_runtime().capabilities.provide(ext_id, protocol, implementation)


async def _host(db_session, name: str = "pi") -> str:
    host = await hosts_service.create_host(db_session, name=name, address="192.168.2.20")
    await db_session.commit()
    return host.id


async def _owner(client) -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": PASSWORD, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user(client, db_session, username: str, *, role: str | None = None, permissions: tuple[str, ...] = ()) -> dict:
    from nodvard_deck.core import security
    from nodvard_deck.models import Role, RolePermission, User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password(PASSWORD), is_active=True)
    if role is not None:
        user.roles.append(roles[role])
    if permissions:
        custom = Role(name=f"rolle-{username}", permissions=[RolePermission(permission=p) for p in permissions])
        db_session.add(custom)
        user.roles.append(custom)
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _get(client, headers, host_id: str):
    return await client.get(f"/api/v1/files/places?host_id={host_id}", headers=headers)


async def _with_source(client, db_session) -> tuple[dict, str]:
    """Ein Owner, ein Server mit SFTP-Quelle."""
    headers = await _owner(client)
    host_id = await _host(db_session)
    _provide("terminal", FileSourceProvider, _Sources(_ssh_source(host_id)))
    return headers, host_id


@pytest.mark.asyncio
async def test_places_of_several_providers_are_collected_in_order_and_deduplicated(client, db_session):
    headers, host_id = await _with_source(client, db_session)
    first = _Places([_place("Docker: web", "/srv/web", icon="boxes", hint="Ordner des Projekts"), _place("Protokolle", "/var/log")])
    second = _Places([_place("Logs", "/var/log/"), _place("Sicherungen", "/srv//backup/./dump")])
    _provide("service-matrix", FilePlaceProvider, first)
    _provide("system", FilePlaceProvider, second)

    res = await _get(client, headers, host_id)

    assert res.status_code == 200, res.text
    assert res.json() == [
        {"label": "Docker: web", "path": "/srv/web", "icon": "boxes", "hint": "Ordner des Projekts"},
        {"label": "Protokolle", "path": "/var/log", "icon": "folder", "hint": None},  # "Logs" auf demselben Pfad fällt weg
        {"label": "Sicherungen", "path": "/srv/backup/dump", "icon": "folder", "hint": None},
    ]
    assert first.asked == second.asked == [host_id]


@pytest.mark.asyncio
async def test_only_clean_absolute_paths_with_a_label_are_kept(client, db_session):
    headers, host_id = await _with_source(client, db_session)
    _provide("x", FilePlaceProvider, _Places([
        _place("Relativ", "var/log"),
        _place("Heimat", "~/daten"),
        _place("Hoch", "/srv/../etc"),
        _place("Steuerzeichen", "/srv/a\x00b"),
        _place("   ", "/srv/ohne-namen"),
        _place("Zu lang", "/" + "a" * 1100),
        {"label": "Ohne Pfad"},  # ein Wörterbuch ohne Pfad ist kein Ort
        "kein-ort",
        _place("Wurzel", "/"),
        _place("Gut", "/srv/gut/"),
    ]))

    res = await _get(client, headers, host_id)

    assert res.status_code == 200, res.text
    assert [(p["label"], p["path"]) for p in res.json()] == [("Wurzel", "/"), ("Gut", "/srv/gut")]


@pytest.mark.asyncio
async def test_label_hint_and_icon_are_limited_and_filled(client, db_session):
    headers, host_id = await _with_source(client, db_session)
    _provide("x", FilePlaceProvider, _Places([_place("L" * 100, "/a", icon="  ", hint="H" * 300)]))

    place = (await _get(client, headers, host_id)).json()[0]

    assert (len(place["label"]), len(place["hint"]), place["icon"]) == (60, 160, "folder")


@pytest.mark.asyncio
async def test_the_list_is_limited(client, db_session):
    headers, host_id = await _with_source(client, db_session)
    _provide("x", FilePlaceProvider, _Places([_place(f"Ort {i}", f"/srv/{i}") for i in range(files_api.MAX_PLACES + 12)]))

    res = await _get(client, headers, host_id)

    assert len(res.json()) == files_api.MAX_PLACES == 30
    assert res.json()[0]["path"] == "/srv/0"


@pytest.mark.asyncio
async def test_a_failing_provider_only_loses_its_own_places_and_leaves_no_traceback(client, db_session, caplog):
    headers, host_id = await _with_source(client, db_session)
    _provide("kaputt", FilePlaceProvider, _Places(error=RuntimeError("Server antwortet nicht")))
    _provide("heil", FilePlaceProvider, _Places([_place("Protokolle", "/var/log")]))

    with caplog.at_level(logging.WARNING, logger="nodvard_deck.files"):
        res = await _get(client, headers, host_id)

    assert res.status_code == 200, res.text
    assert [p["path"] for p in res.json()] == ["/var/log"]
    warned = [r for r in caplog.records if "file_places_provider_failed" in r.getMessage()]
    assert len(warned) == 1 and "Server antwortet nicht" in warned[0].getMessage()
    assert warned[0].exc_info is None  # das Protokoll gilt beim Ausliefern als Fehlerzeichen, sobald ein Traceback darin steht


@pytest.mark.asyncio
async def test_a_hanging_provider_is_cut_off_after_the_time_limit(client, db_session, monkeypatch):
    headers, host_id = await _with_source(client, db_session)
    monkeypatch.setattr(files_api, "PLACES_TIMEOUT_S", 0.1)
    hanging = _Places([_place("Nie", "/nie")], delay_s=30)
    _provide("haengt", FilePlaceProvider, hanging)
    _provide("heil", FilePlaceProvider, _Places([_place("Protokolle", "/var/log")]))

    started = time.monotonic()
    res = await _get(client, headers, host_id)

    assert res.status_code == 200, res.text
    assert time.monotonic() - started < 5
    assert [p["path"] for p in res.json()] == ["/var/log"]
    assert hanging.asked == [host_id]


@pytest.mark.asyncio
async def test_providers_are_asked_at_the_same_time(client, db_session):
    headers, host_id = await _with_source(client, db_session)
    for name in ("a", "b", "c"):
        _provide(name, FilePlaceProvider, _Places([_place(name, f"/srv/{name}")], delay_s=0.3))

    started = time.monotonic()
    res = await _get(client, headers, host_id)

    assert [p["path"] for p in res.json()] == ["/srv/a", "/srv/b", "/srv/c"]
    assert time.monotonic() - started < 0.8  # nacheinander wären es 0,9 s


@pytest.mark.asyncio
async def test_a_provider_that_returns_nonsense_gives_no_places(client, db_session):
    headers, host_id = await _with_source(client, db_session)
    _provide("x", FilePlaceProvider, _Places())
    broken = _Places()
    broken._places = "/var/log"  # keine Liste
    _provide("y", FilePlaceProvider, broken)

    res = await _get(client, headers, host_id)

    assert res.status_code == 200 and res.json() == []


@pytest.mark.asyncio
async def test_unknown_host_is_404(client, db_session):
    headers = await _owner(client)
    _provide("x", FilePlaceProvider, _Places([_place("Protokolle", "/var/log")]))

    res = await _get(client, headers, "gibt-es-nicht")

    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_host_id_is_required(client, db_session):
    headers = await _owner(client)

    res = await client.get("/api/v1/files/places", headers=headers)

    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_needs_a_login(client, db_session):
    assert (await client.get("/api/v1/files/places?host_id=x")).status_code == 401


@pytest.mark.asyncio
async def test_needs_files_read_and_hosts_read(client, db_session):
    headers = await _owner(client)
    host_id = await _host(db_session)
    _provide("terminal", FileSourceProvider, _Sources(_ssh_source(host_id)))
    provider = _Places([_place("Protokolle", "/var/log")])
    _provide("x", FilePlaceProvider, provider)
    no_files = await _user(client, db_session, "ohne-dateien", permissions=("hosts.read", "hosts.execute"))
    no_hosts = await _user(client, db_session, "ohne-server", permissions=("files.read",))

    assert (await _get(client, no_files, host_id)).status_code == 403
    assert (await _get(client, no_hosts, host_id)).status_code == 403
    assert provider.asked == []
    assert (await _get(client, headers, host_id)).status_code == 200


@pytest.mark.asyncio
async def test_without_the_right_to_open_the_servers_files_there_are_no_places(client, db_session):
    """Der Leser darf Dateien und Server sehen, aber keine Befehle ausfuehren: Die SSH-Quelle bleibt ihm verborgen,
    also nennt der Kern ihm auch die Ordner nicht (und fragt die Anbieter gar nicht erst)."""
    await _owner(client)
    host_id = await _host(db_session)
    _provide("terminal", FileSourceProvider, _Sources(_ssh_source(host_id)))
    provider = _Places([_place("Protokolle", "/var/log")])
    _provide("x", FilePlaceProvider, provider)
    viewer = await _user(client, db_session, "gast", role="viewer")
    operator = await _user(client, db_session, "betrieb", permissions=("files.read", "hosts.read", "hosts.execute"))

    seen_by_viewer = await _get(client, viewer, host_id)
    seen_by_operator = await _get(client, operator, host_id)

    assert seen_by_viewer.status_code == 200 and seen_by_viewer.json() == []
    assert [p["path"] for p in seen_by_operator.json()] == ["/var/log"]
    assert provider.asked == [host_id]


@pytest.mark.asyncio
async def test_a_server_without_a_file_source_has_no_places(client, db_session):
    headers = await _owner(client)
    with_source = await _host(db_session, "mit")
    without_source = await _host(db_session, "ohne")
    _provide("terminal", FileSourceProvider, _Sources(_ssh_source(with_source), types.SimpleNamespace(source_id="cloud", host_id=None)))
    provider = _Places([_place("Protokolle", "/var/log")])
    _provide("x", FilePlaceProvider, provider)

    res = await _get(client, headers, without_source)

    assert res.status_code == 200 and res.json() == []
    assert provider.asked == []
    assert len((await _get(client, headers, with_source)).json()) == 1


@pytest.mark.asyncio
async def test_a_source_without_its_own_permission_counts_for_everyone(client, db_session):
    """Quellen ohne `required_permission` (ein Fremdanbieter ohne die Angabe) gelten fuer jeden mit `files.read`."""
    await _owner(client)
    host_id = await _host(db_session)
    _provide("fremd", FileSourceProvider, _Sources(_ssh_source(host_id, permission=None)))
    _provide("x", FilePlaceProvider, _Places([_place("Protokolle", "/var/log")]))
    viewer = await _user(client, db_session, "gast", role="viewer")

    assert [p["path"] for p in (await _get(client, viewer, host_id)).json()] == ["/var/log"]


@pytest.mark.asyncio
async def test_no_providers_give_an_empty_list(client, db_session):
    headers, host_id = await _with_source(client, db_session)

    res = await _get(client, headers, host_id)

    assert res.status_code == 200 and res.json() == []
