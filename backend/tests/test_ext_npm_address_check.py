"""Nginx Proxy Manager: Adress-Check (`address_check.py`, `GET /ext/npm/address-check`).

Erst die Zuordnungsregeln als reine Funktionen, dann der Endpunkt durch den echten Kern. Die Container liefert dort ein
Nachbau der `ServiceCatalog`-Fähigkeit unter der Kennung des Moduls Docker (nur die darf das Modul laut `requires`
fragen)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_sdk import ServiceCatalog
from test_ext_npm import (
    NPM_URL,
    _cleanup_sys_path,  # noqa: F401 -- autouse-Fixture
    _configure,
    _enable,
)

SRC = Path(__file__).resolve().parents[2] / "extensions" / "npm" / "src"


@pytest.fixture(autouse=True)
def _ext_npm_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(SRC))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_npm"):
            del sys.modules[name]


def check_all(*args, **kwargs):
    return importlib.import_module("nodvard_deck_ext_npm.address_check").check_all(*args, **kwargs)


def _c(name: str, state: str = "running", *, host: str = "docker", host_id: str = "h1", address: str = "10.0.0.85",
       ports: list[int] | None = None, image: str = "app:latest", project: str | None = None) -> dict:
    return {
        "id": f"{host_id}:{name}", "name": name, "container": name, "host": host, "host_id": host_id,
        "host_address": address, "state": state, "image": image, "published_ports": ports or [], "compose_project": project,
    }


def _p(forward_host: str, port: int | None = 80, *, pid: int = 1, enabled: bool = True) -> dict:
    return {"id": pid, "forward_host": forward_host, "forward_port": port, "enabled": enabled}


def _one(proxy: dict, rows: list[dict], npm_url: str | None = None) -> dict:
    return check_all([proxy], rows, npm_url=npm_url)[0]


NPM = _c("npm", image="jc21/nginx-proxy-manager:latest", ports=[80, 81, 443])


def test_server_address_and_port_finds_the_stopped_container():
    rows = [NPM, _c("immich_server", "exited", ports=[2283]), _c("web", ports=[8080])]
    out = _one(_p("10.0.0.85", 2283), rows)
    assert out["status"] == "stopped" and out["tone"] == "bad"
    assert out["text"] == "Führt zum Container immich_server auf docker – der ist gestoppt. Die Seite ist so vermutlich nicht erreichbar."
    assert (out["container"], out["docker_host_id"], out["can_start"]) == ("immich_server", "h1", True)


def test_server_name_works_too_and_a_running_container_is_good():
    rows = [_c("web", ports=[8080])]
    for target in ("docker", "DOCKER.fritz.box", "docker.lan", "docker.home.arpa"):
        out = _one(_p(target, 8080), rows)
        assert (out["status"], out["tone"], out["can_start"]) == ("running", "good", False), target


def test_a_public_domain_is_not_the_server_with_the_same_first_name():
    rows = [_c("immich_server", "exited", ports=[2283])]
    out = _one(_p("docker.example.com", 2283), rows)
    assert out["status"] == "unknown" and out["container"] is None and out["can_start"] is False
    # Dieselbe Domain wie der Proxy Manager gilt als lokal.
    out = _one(_p("docker.home.example", 2283), rows, npm_url="http://npm.home.example:81")
    assert out["status"] == "stopped"
    # Traegt der Server selbst eine Domain, passt genau die.
    rows = [_c("immich_server", "exited", host="docker.home.example", ports=[2283])]
    assert _one(_p("docker.home.example", 2283), rows)["status"] == "stopped"
    assert _one(_p("docker", 2283), rows)["status"] == "stopped"


def test_one_running_container_wins_over_old_stopped_ones_on_the_same_port():
    rows = [_c("web", ports=[8080]), _c("web-alt", "exited", ports=[8080])]
    out = _one(_p("10.0.0.85", 8080), rows)
    assert (out["status"], out["container"]) == ("running", "web")
    rows = [_c("a", "exited", ports=[8080]), _c("b", "exited", ports=[8080])]
    assert _one(_p("10.0.0.85", 8080), rows)["status"] == "ambiguous"


def test_a_dead_container_is_red_but_has_no_start_button():
    out = _one(_p("10.0.0.85", 2283), [_c("immich_server", "dead", ports=[2283])])
    assert (out["status"], out["tone"], out["can_start"]) == ("stopped", "bad", False)
    assert "vermutlich nicht erreichbar" in out["text"]


def test_ipv6_targets_are_compared_in_one_spelling():
    rows = [_c("web", address="fd00::85", ports=[8080])]
    assert _one(_p("[fd00:0:0::85]", 8080), rows)["status"] == "running"


def test_a_port_without_container_is_neutral_not_an_error():
    out = _one(_p("10.0.0.85", 8006), [_c("web", ports=[8080])])
    assert out["status"] == "unknown" and out["tone"] == "neutral"
    assert "Auf docker belegt kein Container den Port 8006" in out["text"]


def test_localhost_means_the_server_the_proxy_manager_runs_on():
    npm_host_net = _c("npm", image="jc21/nginx-proxy-manager:latest")  # Host-Netz: veroeffentlicht keine Ports
    others = [_c("app", "exited", ports=[3000]), _c("app", ports=[3000], host="mini", host_id="h2", address="10.0.0.65")]
    for target in ("localhost", "127.0.0.1", "host.docker.internal", "172.17.0.1", "172.18.0.1"):
        out = _one(_p(target, 3000), [npm_host_net, *others])
        assert (out["status"], out["docker_host_id"]) == ("stopped", "h1"), target
    # Im Bridge-Netz ist localhost der Proxy Manager selbst: keine Zuordnung. host.docker.internal bleibt der Server.
    for target in ("localhost", "127.0.0.1"):
        out = _one(_p(target, 3000), [NPM, *others])
        assert (out["status"], out["tone"], out["container"]) == ("unknown", "neutral", None), target
        assert "vermutlich der Nginx Proxy Manager selbst" in out["text"]
    assert _one(_p("host.docker.internal", 3000), [NPM, *others])["status"] == "stopped"


def test_a_server_address_wins_over_a_gateway_lookalike_and_172_16_is_no_gateway():
    rows = [NPM, _c("router-ui", ports=[443], host="gw", host_id="h9", address="172.17.0.1")]
    out = _one(_p("172.17.0.1", 443), rows)
    assert (out["status"], out["docker_host_id"]) == ("running", "h9")
    assert _one(_p("172.16.0.1", 443), [NPM])["status"] == "unknown"


def test_a_running_proxy_manager_wins_over_an_old_stopped_one():
    rows = [
        NPM, _c("npm-alt", "exited", image="jc21/nginx-proxy-manager:2", host="mini", host_id="h2", address="10.0.0.65"),
        _c("app"), _c("app", "exited", host="mini", host_id="h2", address="10.0.0.65"),
    ]
    out = _one(_p("app"), rows)
    assert (out["status"], out["docker_host_id"]) == ("running", "h1")


def test_localhost_without_known_proxy_server_says_so():
    out = _one(_p("localhost", 3000), [_c("app", ports=[3000])])
    assert out["status"] == "unknown" and "ließ sich nicht erkennen" in out["text"]
    # Mit der Adresse aus den Einstellungen ist der Server bekannt: host.docker.internal passt dann. Bei localhost bleibt
    # offen, ob der Proxy Manager im Host- oder im Bridge-Netz laeuft (sein Container ist nicht erkannt) -- keine Zuordnung.
    out = _one(_p("host.docker.internal", 3000), [_c("app", ports=[3000])], npm_url="http://10.0.0.85:81")
    assert out["status"] == "running"
    assert _one(_p("localhost", 3000), [_c("app", ports=[3000])], npm_url="http://10.0.0.85:81")["status"] == "unknown"


def test_container_name_and_compose_service_names_match():
    rows = [
        NPM, _c("immich_server", ports=[2283]), _c("paperless-webserver-1", "restarting", project="paperless"),
        _c("vault_vaultwarden_1", "paused", project="vault"), _c("proj-web-app-1", "exited", project="proj"),
    ]
    assert _one(_p("immich_server", 2283), rows)["status"] == "running"
    restarting = _one(_p("webserver", 8000), rows)
    assert (restarting["status"], restarting["tone"], restarting["container"]) == ("restarting", "warn", "paperless-webserver-1")
    assert "startet ständig neu" in restarting["text"]
    assert _one(_p("vaultwarden", 80), rows)["status"] == "paused"
    assert _one(_p("server", 80), rows)["status"] == "unknown", "nur ganze Dienstnamen, kein Teilwort"
    assert _one(_p("app", 80), rows)["status"] == "unknown", "Dienst `web-app`, nicht `app`"
    assert _one(_p("web-app", 80), rows)["status"] == "stopped"
    # Ohne Compose-Projekt ist ein Name mit Ziffer am Ende nur er selbst.
    assert _one(_p("webserver", 80), [NPM, _c("paperless-webserver-1")])["status"] == "unknown"


def test_names_only_count_on_the_proxy_managers_server():
    rows = [
        NPM, _c("app"),
        _c("app", "exited", host="mini", host_id="h2", address="10.0.0.65"),
        _c("jellyfin", "exited", host="mini", host_id="h2", address="10.0.0.65"),
    ]
    assert _one(_p("app"), rows)["docker_host_id"] == "h1", "Docker loest Namen nur im eigenen Netz auf"
    other = _one(_p("jellyfin"), rows)
    assert (other["status"], other["container"], other["can_start"]) == ("unknown", None, False)
    assert "Auf docker, wo der Nginx Proxy Manager läuft, heißt kein Container jellyfin" in other["text"]


def test_without_a_known_proxy_server_a_name_is_only_a_guess():
    rows = [
        _c("jellyfin", "exited", host="mini", host_id="h2", address="10.0.0.65"),
        _c("db", host="mini", host_id="h2", address="10.0.0.65"),
        _c("db", host="pi", host_id="h3", address="10.0.0.72"),
    ]
    guess = _one(_p("jellyfin"), rows)
    assert (guess["status"], guess["tone"], guess["can_start"]) == ("stopped", "neutral", False)
    assert guess["text"].startswith("Führt vermutlich zum Container jellyfin auf mini")
    ambiguous = _one(_p("db"), rows)
    assert ambiguous["status"] == "ambiguous" and ambiguous["tone"] == "neutral"
    assert "db auf mini, db auf pi" in ambiguous["text"]


def test_unreachable_docker_server_is_a_warning_not_a_verdict():
    rows = [{"id": "h1:__error__", "name": "⚠ docker", "host": "docker", "state": "error", "unreachable": True, "host_address": "10.0.0.85"}]
    out = _one(_p("10.0.0.85", 2283), rows)
    assert out["status"] == "unreachable" and out["tone"] == "warn" and "docker ist gerade nicht erreichbar" in out["text"]
    by_name = _one(_p("immich_server"), rows)
    assert by_name["status"] == "unreachable" and "nicht geprüft: docker" in by_name["text"]


def test_foreign_ip_and_missing_target_stay_neutral():
    rows = [_c("web", ports=[8080])]
    assert _one(_p("192.168.50.10", 8006), rows)["tone"] == "neutral"
    assert _one(_p("", None), rows)["text"] == "Kein Ziel eingetragen."


def test_a_disabled_proxy_host_is_never_red_and_offers_no_start():
    out = _one(_p("10.0.0.85", 2283, enabled=False), [_c("immich_server", "exited", ports=[2283])])
    assert out["status"] == "stopped" and out["tone"] == "neutral" and out["can_start"] is False


# ---------------------------------------------------------------------------
# Endpunkt durch den echten Kern
# ---------------------------------------------------------------------------


class _Catalog:
    def __init__(self, rows: list[dict] | Exception) -> None:
        self.rows = rows
        self.calls = 0

    async def list_services(self) -> list[dict]:
        self.calls += 1
        if isinstance(self.rows, Exception):
            raise self.rows
        return list(self.rows)


@pytest.fixture
def catalog():
    provided: list[_Catalog] = []

    def _provide(rows: list[dict] | Exception) -> _Catalog:
        impl = _Catalog(rows)
        get_extension_runtime().capabilities.provide("docker", ServiceCatalog, impl)
        provided.append(impl)
        return impl

    yield _provide
    get_extension_runtime().capabilities.clear_extension("docker")


@pytest.mark.asyncio
async def test_endpoint_maps_every_proxy_host(client, db_session, test_settings, catalog):
    headers, fake = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    fake.hosts.append({"id": 4, "domain_names": ["fotos.home.example"], "forward_scheme": "http", "forward_host": "immich_server",
                       "forward_port": 2283, "certificate_id": 0, "enabled": True, "meta": {}})
    catalog([
        _c("npm", image="jc21/nginx-proxy-manager:latest", address="192.168.1.85", ports=[80, 81, 443]),
        _c("nextcloud", address="192.168.1.85", ports=[8080]),
        _c("immich_server", "exited", address="192.168.1.85", ports=[2283]),
    ])

    res = await client.get("/api/v1/ext/npm/address-check", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["state"], body["message"], body["problems"]) == ("ok", None, 1)
    by_id = {c["proxy_id"]: c for c in body["checks"]}
    assert by_id[1]["status"] == "running" and by_id[1]["container"] == "nextcloud"
    assert by_id[2]["status"] == "unknown", "192.168.1.10 ist kein Docker-Server"
    assert by_id[3]["tone"] == "neutral", "ausgeschalteter Proxy-Host"
    assert by_id[4]["status"] == "stopped" and by_id[4]["can_start"] is True and by_id[4]["docker_host_id"] == "h1"

    npm = (await client.get("/api/v1/ext/npm/summary", headers=headers)).json()
    cloud = next(h for h in npm["hosts"] if h["id"] == 1)
    assert (cloud["forward_host"], cloud["forward_port"]) == ("192.168.1.85", 8080)


@pytest.mark.asyncio
async def test_endpoint_without_service_matrix_or_docker_says_why(client, db_session, test_settings, catalog):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)

    body = (await client.get("/api/v1/ext/npm/address-check", headers=headers)).json()
    assert body["state"] == "no_catalog" and "Modul Docker" in body["message"] and body["checks"] == []

    impl = catalog([])
    body = (await client.get("/api/v1/ext/npm/address-check", headers=headers)).json()
    assert body["state"] == "ok" and "Kein Server ist als Docker-Server markiert" in body["message"]
    assert impl.calls == 1

    get_extension_runtime().capabilities.clear_extension("docker")
    catalog(RuntimeError("SSH kaputt"))
    body = (await client.get("/api/v1/ext/npm/address-check", headers=headers)).json()
    assert body["state"] == "docker_unreadable" and "SSH kaputt" in body["message"]


@pytest.mark.asyncio
async def test_endpoint_names_a_timeout_in_words(client, db_session, test_settings, catalog, monkeypatch):
    import asyncio

    from nodvard_deck_ext_npm import service

    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    monkeypatch.setattr(service, "CATALOG_TIMEOUT_S", 0.05)

    class _Slow(_Catalog):
        async def list_services(self) -> list[dict]:
            await asyncio.sleep(1)
            return []

    get_extension_runtime().capabilities.provide("docker", ServiceCatalog, _Slow([]))
    body = (await client.get("/api/v1/ext/npm/address-check", headers=headers)).json()
    assert body["state"] == "docker_unreadable" and "Zeitüberschreitung nach 0 s" in body["message"]


@pytest.mark.asyncio
async def test_endpoint_only_asks_the_declared_extension_and_needs_login(client, db_session, test_settings, catalog):
    headers, _ = await _enable(client, db_session, test_settings)
    await _configure(client, headers)
    stranger = _Catalog([_c("web", ports=[8080])])
    get_extension_runtime().capabilities.provide("fremd", ServiceCatalog, stranger)
    try:
        body = (await client.get("/api/v1/ext/npm/address-check", headers=headers)).json()
        assert body["state"] == "no_catalog" and stranger.calls == 0
    finally:
        get_extension_runtime().capabilities.clear_extension("fremd")
    assert (await client.get("/api/v1/ext/npm/address-check")).status_code == 401


@pytest.mark.asyncio
async def test_endpoint_not_configured(client, db_session, test_settings, catalog):
    headers, _ = await _enable(client, db_session, test_settings)
    catalog([])
    body = (await client.get("/api/v1/ext/npm/address-check", headers=headers)).json()
    assert body["state"] == "not_configured" and body["checks"] == []
    assert (urlsplit(NPM_URL).scheme, urlsplit(NPM_URL).hostname) == ("http", "npm.test")
