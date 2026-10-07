"""service-matrix-Extension -- `DockerServiceCatalog`. Fake-`ctx`-Unit-Tests wie
`test_ext_shield_watcher.py` -- die reale
`ctx.exec.run()` -> echter SSH-Server-Kette ist bereits bewiesen, hier
geht es um die Docker-ps-Parsing-/URL-Rate-Logik selbst.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nodvard_deck.services import extensions as extensions_service

SRC = Path(__file__).resolve().parents[2] / "extensions" / "service-matrix" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nodvard_deck_ext_service_matrix.capabilities import DockerServiceCatalog  # noqa: E402

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _FakeHost:
    def __init__(self, id: str, name: str, address: str, display_name: str | None = None) -> None:
        self.id = id
        self.name = name
        self.address = address
        self.display_name = display_name or name


class _FakeExecResult:
    def __init__(self, exit_code: int, stdout: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = ""


class _FakeSettingsHandle:
    def __init__(self, data: dict) -> None:
        self._data = data

    async def get(self) -> dict:
        return self._data


class _FakeHostsHandle:
    def __init__(self, hosts: list[_FakeHost]) -> None:
        self._hosts = hosts

    async def list(self, *, tag: str | None = None) -> list[_FakeHost]:
        return self._hosts


class _FakeExecHandle:
    def __init__(self, outputs: dict) -> None:
        self._outputs = outputs

    async def run(self, host, command: str, *, timeout_s: int = 60):  # noqa: ANN001
        result = self._outputs[host.id]
        if isinstance(result, Exception):
            raise result
        return result


class _FakeCtx:
    def __init__(self, *, settings: dict, hosts: list[_FakeHost], outputs: dict) -> None:
        self.settings = _FakeSettingsHandle(settings)
        self.hosts = _FakeHostsHandle(hosts)
        self.exec = _FakeExecHandle(outputs)


def _ps_line(
    name: str, state: str, status: str, ports: str = "", container_id: str = "0123456789ab", image: str = "img:latest",
    project: str | None = None,
) -> str:
    # project=None: altes 6-Felder-Format (ohne Compose-Projekt) -- muss weiter gehen.
    middle = f"{image}|{project}" if project is not None else image
    return f"{container_id}|{name}|{state}|{status}|{middle}|{ports}"


@pytest.mark.asyncio
async def test_lists_running_and_stopped_containers_with_tone():
    host = _FakeHost("h1", "docker", "10.0.0.5")
    ctx = _FakeCtx(
        settings={},
        hosts=[host],
        outputs={"h1": _FakeExecResult(0, "\n".join([
            _ps_line("nginx", "running", "Up 2 hours"),
            _ps_line("worker", "exited", "Exited (1) 3 minutes ago"),
        ]))},
    )
    services = await DockerServiceCatalog(ctx).list_services()
    by_name = {s["name"]: s for s in services}
    assert by_name["nginx"]["tone"] == "good"
    assert by_name["worker"]["tone"] == "neutral"
    assert by_name["nginx"]["host"] == "docker"


def test_guess_url_extracts_first_ipv4_published_port():
    from nodvard_deck_ext_service_matrix.capabilities import _guess_url

    url = _guess_url("0.0.0.0:8080->80/tcp, :::8080->80/tcp", "10.0.0.5", "http")
    assert url == "http://10.0.0.5:8080"


def test_guess_url_returns_none_when_no_port_published():
    from nodvard_deck_ext_service_matrix.capabilities import _guess_url

    assert _guess_url("", "10.0.0.5", "http") is None


def test_guess_url_ignores_ipv6_only_bindings():
    """Live-relevant: manche Container binden NUR auf `:::PORT` (kein IPv4-Host-
    Mapping) -- eine geratene URL waere dann trotzdem falsch/nicht erreichbar aus
    einem gewoehnlichen Browser heraus, deshalb bewusst kein Fallback auf IPv6."""
    from nodvard_deck_ext_service_matrix.capabilities import _guess_url

    assert _guess_url(":::9000->9000/tcp", "10.0.0.5", "http") is None


@pytest.mark.asyncio
async def test_unreachable_host_shows_a_visible_error_tile_not_raised():
    """Vorher stumm
    verschluckt -- fachlich ununterscheidbar von "keine Container laufen"."""
    host = _FakeHost("h1", "docker", "10.0.0.5")
    ctx = _FakeCtx(settings={}, hosts=[host], outputs={"h1": ConnectionError("no route to host")})
    services = await DockerServiceCatalog(ctx).list_services()
    assert len(services) == 1
    assert services[0]["tone"] == "danger"
    assert "no route to host" in services[0]["status"]
    # "host" muss der ECHTE Host-Anzeigename
    # bleiben, nicht die Fehlermeldung -- ServiceMatrixPage.tsx gruppiert danach,
    # eine Fehlermeldung als "Hostname" haette dort eine eigene falsche Gruppe
    # erzeugt.
    assert services[0]["host"] == "docker"
    # Marker fuer die Uebersicht: Platzhalter, kein Container.
    assert services[0]["unreachable"] is True


@pytest.mark.asyncio
async def test_a_broken_host_does_not_hide_services_on_a_working_one():
    broken = _FakeHost("h1", "docker", "10.0.0.5")
    working = _FakeHost("h2", "docker-lxc", "10.0.0.9")
    ctx = _FakeCtx(
        settings={}, hosts=[broken, working],
        outputs={
            "h1": ConnectionError("no route to host"),
            "h2": _FakeExecResult(0, _ps_line("grafana", "running", "Up")),
        },
    )
    services = await DockerServiceCatalog(ctx).list_services()
    tones = {s["name"]: s["tone"] for s in services}
    assert tones["grafana"] == "good"
    assert any(t == "danger" for t in tones.values())
    assert [s["name"] for s in services if not s.get("unreachable")] == ["grafana"]


@pytest.mark.asyncio
async def test_uses_configured_tag_and_url_scheme():
    host = _FakeHost("h1", "docker-lxc", "10.0.0.9")
    ctx = _FakeCtx(
        settings={"docker_host_tag": "docker-hosts", "url_scheme": "https"},
        hosts=[host],
        outputs={"h1": _FakeExecResult(0, _ps_line("grafana", "running", "Up", "0.0.0.0:3000->3000/tcp"))},
    )
    services = await DockerServiceCatalog(ctx).list_services()
    assert services[0]["url"] == "https://10.0.0.9:3000"


@pytest.mark.asyncio
async def test_non_zero_exit_code_shows_a_visible_error_tile_not_raised():
    """Der konkrete Fall aus der Checkliste: SSH klappt, aber der Docker-Daemon
    laeuft nicht (oder fehlende Rechte) -- `docker ps` selbst schlaegt fehl."""
    host = _FakeHost("h1", "docker", "10.0.0.5")
    result = _FakeExecResult(1, "")
    result.stderr = "permission denied"
    ctx = _FakeCtx(settings={}, hosts=[host], outputs={"h1": result})
    services = await DockerServiceCatalog(ctx).list_services()
    assert len(services) == 1
    assert services[0]["tone"] == "danger"
    assert "permission denied" in services[0]["status"]


@pytest.mark.asyncio
async def test_matrix_page_is_registered_after_enabling(client, db_session, test_settings):
    """service-matrix hatte bisher NUR ein
    Dashboard-Widget, keine eigene Seite -- `GET /pages` muss nach dem Aktivieren
    die neue ServiceMatrixPage zeigen, wie es fuer hello-world/proxmox/backups
    bereits bewiesen ist (test_extensions_api.py)."""
    token = await _bootstrap_owner(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)

    enabled = await client.post("/api/v1/extensions/service-matrix/enable", headers=_auth_header(token))
    assert enabled.status_code == 200, enabled.text

    pages = await client.get("/api/v1/pages", headers=_auth_header(token))
    assert pages.status_code == 200
    matrix_page = next(p for p in pages.json() if p["ext_id"] == "service-matrix")
    assert matrix_page["path"] == "/matrix"
    assert matrix_page["component"] == "ServiceMatrixPage"

    bundle = await client.get("/api/v1/extensions/service-matrix/frontend/index.js")
    assert bundle.status_code == 200
    assert "ServiceMatrixPage" in bundle.text


@pytest.mark.asyncio
async def test_container_tool_follows_the_configured_docker_tag(client, db_session, test_settings):
    """Server-Seite: die Kachel "Container" haengt am Docker-Tag. Aendert der Admin
    den Tag, wandert die Kachel mit -- ueber den jetzt echten on_settings_changed-Hook."""
    from nodvard_deck.ext.runtime import get_extension_runtime

    token = await _bootstrap_owner(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    assert (await client.post("/api/v1/extensions/service-matrix/enable", headers=_auth_header(token))).status_code == 200

    runtime = get_extension_runtime()
    tags = lambda: [t.tags for e, t in runtime.ui.all_host_tools() if e == "service-matrix" and t.id == "containers"]  # noqa: E731
    assert tags() == [["docker"]]

    await runtime.loaded["service-matrix"].ctx.settings.set({"docker_host_tag": "container-host"})
    assert tags() == [["container-host"]]


@pytest.mark.asyncio
async def test_docker_group_requirement_follows_the_configured_docker_tag(client, db_session, test_settings):
    """Der Einrichtungsbefehl fuer einen Server bietet die Gruppe "docker" nur dort an, wo
    Container verwaltet werden -- und folgt dem eingestellten Tag; ohne die Extension weg."""
    from nodvard_deck.ext.runtime import get_extension_runtime

    token = await _bootstrap_owner(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    assert (await client.post("/api/v1/extensions/service-matrix/enable", headers=_auth_header(token))).status_code == 200

    runtime = get_extension_runtime()
    specs = lambda: [r for e, r in runtime.ui.all_host_requirements() if e == "service-matrix"]  # noqa: E731
    [spec] = specs()
    assert spec.id == "docker-group" and spec.unix_group == "docker" and spec.tags == ["docker"]
    assert spec.check_command == "docker ps -q" and spec.needs_root is False
    assert "{user}" in spec.fail_hint and "usermod -aG docker" in spec.fail_hint
    assert "Service-Matrix" in spec.label

    await runtime.loaded["service-matrix"].ctx.settings.set({"docker_host_tag": "container-host"})
    [spec] = specs()
    assert spec.tags == ["container-host"], "gleiche ID ersetzt, nicht doppelt"

    assert (await client.post("/api/v1/extensions/service-matrix/disable", headers=_auth_header(token))).status_code == 200
    assert specs() == []


# ---------------------------------------------------------------------------
# Container-Verwaltung: Start/Stop/Neustart + Live-Logs
# ---------------------------------------------------------------------------


class _RecordingExec:
    def __init__(self, results: dict[str, _FakeExecResult] | None = None) -> None:
        self.commands: list[str] = []
        self._results = results or {}

    async def run(self, host, command: str, *, timeout_s: int = 60):  # noqa: ANN001
        self.commands.append(command)
        for prefix, result in self._results.items():
            if command.startswith(prefix):
                return result
        result = _FakeExecResult(0, "ok\n")
        result.duration_ms = 5
        return result


class _ExecutorCtx:
    def __init__(self, exec_handle: _RecordingExec) -> None:
        self.exec = exec_handle
        host = _FakeHost("h1", "pi-host", "192.168.1.72")

        class _Hosts:
            async def get(self, host_id: str):  # noqa: ANN202
                return host if host_id == "h1" else None

        self.hosts = _Hosts()


def _request(action_type: str, container: str):  # noqa: ANN202
    from nodvard_sdk import ActionRequest, Actor

    return ActionRequest(
        action_type=action_type, payload={"container": container}, host_ref="h1",
        proposed_by=Actor.user("u1", "owner1"), reason="Test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["x; rm -rf /", "$(reboot)", "a b", "-f", "", "../etc", "name`id`"])
async def test_executor_rejects_container_names_that_could_escape_into_the_shell(bad):
    """Der Name landet in einem SSH-Kommando -- alles ausser Dockers eigener
    Namensregel wird abgelehnt, BEVOR irgendein Befehl den Host erreicht."""
    from nodvard_deck_ext_service_matrix.capabilities import DockerContainerExecutor

    exec_handle = _RecordingExec()
    result = await DockerContainerExecutor(_ExecutorCtx(exec_handle)).execute(_request("container.restart", bad))
    assert result.success is False
    assert "Ungültiger Containername" in (result.error or "")
    assert exec_handle.commands == []


@pytest.mark.asyncio
async def test_executor_builds_the_exact_docker_command():
    from nodvard_deck_ext_service_matrix.capabilities import DockerContainerExecutor

    exec_handle = _RecordingExec()
    executor = DockerContainerExecutor(_ExecutorCtx(exec_handle))
    assert (await executor.execute(_request("container.start", "pihole"))).success
    assert (await executor.execute(_request("container.restart", "npm-nginx-1"))).success
    assert exec_handle.commands == ["docker start pihole", "docker restart npm-nginx-1"]


@pytest.mark.asyncio
async def test_executor_refuses_to_stop_the_container_the_dashboard_runs_in(monkeypatch):
    """Nodvard Deck laeuft selbst als Container auf einem der Server -- "Stoppen" darauf schaltete
    genau die Oberflaeche ab, mit der man ihn wieder starten muesste."""
    import nodvard_deck_ext_service_matrix.capabilities as caps

    monkeypatch.setattr(caps, "_own_container_id", lambda: "3f2a9c1b7d4e")
    exec_handle = _RecordingExec({"docker inspect": _FakeExecResult(0, "3f2a9c1b7d4e0011223344\n")})
    result = await caps.DockerContainerExecutor(_ExecutorCtx(exec_handle)).execute(
        _request("container.stop", "lattice-deploy-test-lattice-1")
    )
    assert result.success is False
    assert "Nodvard Deck selbst" in (result.error or "")
    assert not any(c.startswith("docker stop") for c in exec_handle.commands)


@pytest.mark.asyncio
async def test_own_container_is_flagged_in_the_listing(monkeypatch):
    import nodvard_deck_ext_service_matrix.capabilities as caps

    monkeypatch.setattr(caps, "_own_container_id", lambda: "3f2a9c1b7d4e")
    ctx = _FakeCtx(
        settings={}, hosts=[_FakeHost("h1", "pi-host", "10.0.0.5")],
        outputs={"h1": _FakeExecResult(0, "\n".join([
            _ps_line("lattice-1", "running", "Up", container_id="3f2a9c1b7d4e"),
            _ps_line("pihole", "running", "Up", container_id="0123456789ab"),
        ]))},
    )
    services = {s["name"]: s for s in await caps.DockerServiceCatalog(ctx).list_services()}
    assert services["lattice-1"]["is_self"] is True
    assert services["pihole"]["is_self"] is False
    assert services["pihole"]["host_id"] == "h1"
    assert services["pihole"]["container"] == "pihole"


async def _setup_real_docker_host(running_app, db_session, test_settings, local_ssh_server, docker):  # noqa: ANN202
    """Echte Kette: echte App (running_app), echter SSH-Server mit Fake-`docker`
    (conftest.py `_fake_docker`), echte service-matrix-Extension."""
    from httpx import AsyncClient

    from nodvard_deck.services import hosts as hosts_service

    http_base, _ = running_app
    host_addr, port, username, password, _ = local_ssh_server
    host = await hosts_service.create_host(db_session, name="pi-host", address=host_addr, tags=["docker"])
    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await db_session.commit()

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    async with AsyncClient(base_url=http_base) as ac:
        token = await _bootstrap_owner(ac)
        enabled = await ac.post("/api/v1/extensions/service-matrix/enable", headers=_auth_header(token))
        assert enabled.status_code == 200, enabled.text
    return http_base, token, host, docker


@pytest.mark.asyncio
async def test_start_goes_through_the_gate_and_really_reaches_docker(
    running_app, db_session, test_settings, local_ssh_server, fake_docker
):
    from httpx import AsyncClient

    http_base, token, host, docker = await _setup_real_docker_host(
        running_app, db_session, test_settings, local_ssh_server, fake_docker
    )
    async with AsyncClient(base_url=http_base, timeout=30) as ac:
        listing = (await ac.get("/api/v1/ext/service-matrix/widgets/matrix", headers=_auth_header(token))).json()["data"]
        idle = next(s for s in listing if s["name"] == "idle")
        assert (idle["host_id"], idle["container"], idle["state"]) == (host.id, "idle", "exited")

        proposed = await ac.post(
            f"/api/v1/hosts/{host.id}/actions/container.start",
            json={"payload": {"container": "idle"}, "reason": "Über die Service-Matrix ausgelöst."},
            headers=_auth_header(token),
        )
        assert proposed.status_code == 202, proposed.text
        body = proposed.json()
        assert body["status"] == "proposed"
        assert docker["containers"]["idle"] == "exited", "vor der Freigabe darf nichts passieren"

        approved = await ac.post(f"/api/v1/actions/{body['id']}/approve", headers=_auth_header(token))
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "succeeded"

    assert docker["containers"]["idle"] == "running"
    assert "docker start idle" in docker["calls"]


@pytest.mark.asyncio
async def test_portainer_parts_details_storage_and_cleanup_through_the_gate(
    running_app, db_session, test_settings, local_ssh_server, fake_docker
):
    """Details (ohne Env-Werte), Speicher mit freigebbarem Anteil,
    Aufraeumen nur nach Freigabe -- und alles nur fuer Hosts mit dem Docker-Tag."""
    from httpx import AsyncClient

    from nodvard_deck.services import hosts as hosts_service

    http_base, token, host, docker = await _setup_real_docker_host(
        running_app, db_session, test_settings, local_ssh_server, fake_docker
    )
    no_docker = await hosts_service.create_host(db_session, name="proxmox-knoten", address="10.9.9.9")
    await db_session.commit()
    async with AsyncClient(base_url=http_base, timeout=30) as ac:
        auth = _auth_header(token)
        details = await ac.get(f"/api/v1/ext/service-matrix/containers/{host.id}/web/inspect", headers=auth)
        assert details.status_code == 200, details.text
        body = details.json()
        assert "GEHEIM" not in details.text
        assert body["env_keys"] == ["API_TOKEN", "TZ"]
        assert body["ports"] == [{"container": "80/tcp", "published": ["8080"]}]
        assert (await ac.get(f"/api/v1/ext/service-matrix/containers/{host.id}/fehlt/inspect", headers=auth)).status_code == 502
        assert (await ac.get(f"/api/v1/ext/service-matrix/containers/{host.id}/x;rm/inspect", headers=auth)).status_code == 400

        storage = (await ac.get(f"/api/v1/ext/service-matrix/hosts/{host.id}/docker", headers=auth)).json()
        cache = next(d for d in storage["disk"] if d["type"] == "Build Cache")
        assert cache["reclaimable"] == 9_113_000_000
        assert [i["in_use"] for i in storage["images"]] == [False, True]

        # Kein Docker-Tag -> weder Endpunkt noch Aktion.
        assert (await ac.get(f"/api/v1/ext/service-matrix/hosts/{no_docker.id}/docker", headers=auth)).status_code == 404
        offered = {a["action_type"] for a in (await ac.get(f"/api/v1/hosts/{no_docker.id}/actions", headers=auth)).json()}
        assert not offered & {"container.restart", "docker.prune_build_cache"}
        refused = await ac.post(
            f"/api/v1/hosts/{no_docker.id}/actions/docker.prune_build_cache",
            json={"payload": {}, "reason": "Test"}, headers=auth,
        )
        assert refused.status_code == 404

        proposed = await ac.post(
            f"/api/v1/hosts/{host.id}/actions/docker.prune_build_cache",
            json={"payload": {}, "reason": "Test"}, headers=auth,
        )
        assert proposed.status_code == 202, proposed.text
        assert proposed.json()["risk"] == "medium"
        assert "docker builder prune -f" not in docker["calls"], "vor der Freigabe darf nichts passieren"
        approved = (await ac.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=auth)).json()
        assert approved["status"] == "succeeded", approved
        assert approved["result"]["output"] == "Freigegeben: 9.113GB"
    assert "docker builder prune -f" in docker["calls"]


@pytest.mark.asyncio
async def test_live_logs_stream_and_the_remote_process_ends_when_the_browser_leaves(
    running_app, db_session, test_settings, local_ssh_server, fake_docker
):
    import asyncio

    from httpx import AsyncClient

    http_base, token, host, docker = await _setup_real_docker_host(
        running_app, db_session, test_settings, local_ssh_server, fake_docker
    )
    async with AsyncClient(base_url=http_base, timeout=30) as ac:
        async with ac.stream(
            "GET", f"/api/v1/ext/service-matrix/containers/{host.id}/web/logs?tail=50",
            headers=_auth_header(token),
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/plain")
            received = b""
            async for chunk in resp.aiter_bytes():
                received += chunk
                if b"bereit" in received:
                    break
        # Zeilenenden normalisiert (PTY liefert CR LF).
        assert b"\r" not in received
        assert b"web gestartet\n" in received

    assert "docker logs --tail 50 --timestamps --follow web" in docker["calls"]
    for _ in range(100):
        if docker["log_streams"] and docker["log_streams"][0]["closed"]:
            break
        await asyncio.sleep(0.05)
    assert docker["log_streams"][0]["closed"], "entferntes docker logs -f muss enden, wenn niemand mehr zusieht"


@pytest.mark.asyncio
async def test_logs_reject_bad_names_and_require_login(
    running_app, db_session, test_settings, local_ssh_server, fake_docker
):
    from httpx import AsyncClient

    http_base, token, host, docker = await _setup_real_docker_host(
        running_app, db_session, test_settings, local_ssh_server, fake_docker
    )
    async with AsyncClient(base_url=http_base, timeout=30) as ac:
        bad = await ac.get(
            f"/api/v1/ext/service-matrix/containers/{host.id}/a%3Brm%20-rf/logs", headers=_auth_header(token)
        )
        anonymous = await ac.get(f"/api/v1/ext/service-matrix/containers/{host.id}/web/logs")
    assert bad.status_code == 400
    assert anonymous.status_code == 401
    assert not any(c.startswith("docker logs") for c in docker["calls"])


# ---------------------------------------------------------------------------
# CPU/RAM je Container + Image (Portainer-Ersatz)
# ---------------------------------------------------------------------------


def test_parse_stats_line_reads_cpu_and_memory():
    from nodvard_deck_ext_service_matrix.capabilities import parse_stats_line

    name, values = parse_stats_line("pihole|0.47%|45.3MiB / 3.7GiB|1.19%")
    assert name == "pihole"
    assert values["cpu_percent"] == 0.47
    assert values["mem_used"] == int(45.3 * 2**20)
    assert values["mem_limit"] == int(3.7 * 2**30)


def test_memory_is_unknown_not_zero_when_the_host_does_not_account_it():
    """Live auf einem Raspberry Pi: "0B / 0B" -- der Kernel zaehlt dort keinen Speicher je
    cgroup. Das ist UNBEKANNT, nicht "0 B belegt"."""
    from nodvard_deck_ext_service_matrix.capabilities import parse_stats_line

    _name, values = parse_stats_line("deploy-lattice-1|0.29%|0B / 0B|0.00%")
    assert values == {"cpu_percent": 0.29, "mem_used": None, "mem_limit": None}
    assert parse_stats_line("") is None
    assert parse_stats_line("kaputt") is None


@pytest.mark.asyncio
async def test_listing_carries_the_image():
    host = _FakeHost("h1", "pi-host", "10.0.0.5")
    ctx = _FakeCtx(settings={}, hosts=[host], outputs={"h1": _FakeExecResult(0, _ps_line("pihole", "running", "Up", image="pihole/pihole:latest"))})
    services = await DockerServiceCatalog(ctx).list_services()
    assert services[0]["image"] == "pihole/pihole:latest"


@pytest.mark.asyncio
async def test_stats_endpoint_over_real_ssh(running_app, db_session, test_settings, local_ssh_server, fake_docker):
    from httpx import AsyncClient

    http_base, token, host, docker = await _setup_real_docker_host(
        running_app, db_session, test_settings, local_ssh_server, fake_docker
    )
    async with AsyncClient(base_url=http_base, timeout=30) as ac:
        r = await ac.get("/api/v1/ext/service-matrix/stats", headers=_auth_header(token))
        anonymous = await ac.get("/api/v1/ext/service-matrix/stats")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # Nur der laufende Container ("web"), nicht der gestoppte ("idle").
    assert set(data) == {f"{host.id}:web"}
    assert data[f"{host.id}:web"]["cpu_percent"] == 1.5
    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_compose_project_is_listed_and_old_format_still_parses():
    import nodvard_deck_ext_service_matrix.capabilities as caps

    ctx = _FakeCtx(
        settings={}, hosts=[_FakeHost("h1", "docker", "10.0.0.5")],
        outputs={"h1": _FakeExecResult(0, chr(10).join([
            _ps_line("nextcloud-app", "running", "Up", ports="0.0.0.0:8080->80/tcp", project="nextcloud"),
            _ps_line("solo", "running", "Up", project=""),
            _ps_line("alt", "running", "Up", ports="0.0.0.0:9000->9000/tcp"),
        ]))},
    )
    services = {s["name"]: s for s in await caps.DockerServiceCatalog(ctx).list_services()}
    assert services["nextcloud-app"]["compose_project"] == "nextcloud"
    assert services["nextcloud-app"]["url"] == "http://10.0.0.5:8080"
    assert services["solo"]["compose_project"] is None
    assert (services["alt"]["compose_project"], services["alt"]["url"]) == (None, "http://10.0.0.5:9000")


@pytest.mark.asyncio
@pytest.mark.parametrize("project", ["uptime-kuma", "deploy"])
async def test_stack_restart_runs_compose_without_a_file(project):
    import nodvard_deck_ext_service_matrix.capabilities as caps
    from nodvard_sdk import ActionRequest, Actor

    exec_handle = _RecordingExec()
    req = ActionRequest(action_type="docker.stack_restart", payload={"project": project}, host_ref="h1",
                        proposed_by=Actor.user("u1", "owner1"), reason="Test")
    result = await caps.DockerMaintenanceExecutor(_ExecutorCtx(exec_handle)).execute(req)
    assert result.success is True and result.output == f"Stack '{project}' neu gestartet."
    assert exec_handle.commands == [f"docker compose -p {project} restart"]


@pytest.mark.asyncio
@pytest.mark.parametrize("project", ["x; rm -rf /", "Gross", "", "-a", "a" * 70, "web\n", "web\nrm -rf /"])
async def test_stack_restart_rejects_names_outside_composes_rule(project):
    import nodvard_deck_ext_service_matrix.capabilities as caps
    from nodvard_sdk import ActionRequest, Actor

    exec_handle = _RecordingExec()
    req = ActionRequest(action_type="docker.stack_restart", payload={"project": project}, host_ref="h1",
                        proposed_by=Actor.user("u1", "owner1"), reason="Test")
    result = await caps.DockerMaintenanceExecutor(_ExecutorCtx(exec_handle)).execute(req)
    assert result.success is False and exec_handle.commands == []



def test_name_rules_do_not_accept_a_trailing_newline():
    import nodvard_deck_ext_service_matrix.capabilities as caps

    assert caps.CONTAINER_NAME_RE.match("web") and caps.COMPOSE_PROJECT_RE.match("web")
    for bad in ("web\n", "web\r\n", "web\nx"):
        assert caps.CONTAINER_NAME_RE.match(bad) is None
        assert caps.COMPOSE_PROJECT_RE.match(bad) is None
    # Das JSON-Schema an die Seite bleibt in ECMA-Syntax (kein `\\Z`).
    for spec in caps.container_action_specs():
        assert "\\Z" not in str(spec.params_schema)


@pytest.mark.asyncio
async def test_stack_restart_quotes_the_project_in_the_command():
    import nodvard_deck_ext_service_matrix.capabilities as caps
    from nodvard_sdk import ActionRequest, Actor

    exec_handle = _RecordingExec()
    # Die Regel laesst nichts Sonderbares durch -- der Befehl ist trotzdem gequotet (zweite Sicherung).
    project = "deploy"
    req = ActionRequest(action_type="docker.stack_restart", payload={"project": project}, host_ref="h1",
                        proposed_by=Actor.user("u1", "owner1"), reason="Test")
    await caps.DockerMaintenanceExecutor(_ExecutorCtx(exec_handle)).execute(req)
    import shlex

    assert shlex.split(exec_handle.commands[0]) == ["docker", "compose", "-p", "deploy", "restart"]


# ---------------------------------------------------------------------------
# Image-Updates pruefen (Teil C) -- ueber die echte Kette, nur lesend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_update_check_goes_over_ssh_reads_only_and_follows_the_permissions(
    running_app, db_session, test_settings, local_ssh_server, fake_docker
):
    import asyncio

    from httpx import AsyncClient

    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service, hosts as hosts_service

    http_base, token, host, docker = await _setup_real_docker_host(
        running_app, db_session, test_settings, local_ssh_server, fake_docker
    )
    no_docker = await hosts_service.create_host(db_session, name="proxmox-knoten", address="10.9.9.9")
    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    await db_session.commit()
    docker["remote_digest"] = "sha256:" + "b" * 64  # die Registry hat etwas Neueres

    base = "/api/v1/ext/service-matrix/image-updates"
    async with AsyncClient(base_url=http_base, timeout=30) as ac:
        auth = _auth_header(token)

        async def check(force: bool = False) -> dict:
            started = await ac.post(f"{base}/check", params={"force": force}, headers=auth)
            assert started.status_code == 202, started.text
            assert started.json()["hosts"][host.id]["checking"] is True
            for _ in range(200):
                snap = (await ac.get(base, headers=auth)).json()
                if not snap["hosts"][host.id]["checking"]:
                    return snap
                await asyncio.sleep(0.05)
            raise AssertionError("Pruefung wurde nicht fertig")

        assert (await ac.get(base, headers=auth)).json() == {"data": {}, "hosts": {}, "cache_ttl_s": 21600, "applying": {}, "applied": {}}
        snap = await check()
        assert snap["hosts"][host.id]["error"] is None
        assert list(snap["data"]) == [f"{host.id}:web"], "nur laufende Container, 'idle' ist gestoppt"
        web = snap["data"][f"{host.id}:web"]
        assert (web["status"], web["image"], web["remote_digest"]) == ("update", "nginx:1.27", "sha256:" + "b" * 64)

        # Update eingespielt (lokal jetzt dieselbe Pruefsumme wie die Registry) -> "aktuell".
        docker["local_digest"] = docker["remote_digest"]
        assert (await check())["data"][f"{host.id}:web"]["status"] == "current"

        # Nur Docker-Hosts; Betrachter duerfen den Stand lesen, aber nichts anstossen.
        assert (await ac.post(f"{base}/check", params={"host_id": no_docker.id}, headers=auth)).status_code == 404
        assert (await ac.post(f"{base}/check", params={"host_id": "gibt-es-nicht"}, headers=auth)).status_code == 404
        login = await ac.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
        viewer_auth = _auth_header(login.json()["access_token"])
        assert (await ac.get(base, headers=viewer_auth)).status_code == 200
        assert (await ac.post(f"{base}/check", headers=viewer_auth)).status_code == 403
        assert (await ac.get(base)).status_code == 401
        assert (await ac.post(f"{base}/check")).status_code == 401

    # Nichts davon hat etwas veraendert: nur lesende docker-Befehle.
    image_calls = [c for c in docker["calls"] if "inspect" in c or c.startswith(("docker ps -q", "docker buildx"))]
    assert image_calls and all(c.startswith(("docker ps -q", "docker container inspect", "docker image inspect", "docker buildx imagetools inspect")) for c in image_calls)
    assert not any(word in c for c in docker["calls"] for word in ("pull", " rm ", "docker start", "docker stop", "docker restart", "docker run"))
    assert docker["containers"] == {"web": "running", "idle": "exited"}


@pytest.mark.asyncio
async def test_image_update_plan_route_follows_the_permissions_and_checks_its_input(
    running_app, db_session, test_settings, local_ssh_server, fake_docker
):
    from httpx import AsyncClient

    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service, hosts as hosts_service

    http_base, token, host, docker = await _setup_real_docker_host(
        running_app, db_session, test_settings, local_ssh_server, fake_docker
    )
    no_docker = await hosts_service.create_host(db_session, name="proxmox-knoten", address="10.9.9.9")
    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    await db_session.commit()

    async with AsyncClient(base_url=http_base, timeout=30) as ac:
        auth = _auth_header(token)

        def url(host_id: str, name: str) -> str:
            return f"/api/v1/ext/service-matrix/containers/{host_id}/{name}/image-update/plan"

        # Ein Container, den es nicht gibt (oder den die Pruefung nicht als "Update" kennt): 200 mit Grund, nichts wird veraendert.
        unknown = await ac.get(url(host.id, "web"), headers=auth)
        assert unknown.status_code == 200, unknown.text
        assert unknown.json()["ok"] is False and unknown.json()["reason"]
        assert (await ac.get(url(no_docker.id, "web"), headers=auth)).status_code == 404
        assert (await ac.get(url("gibt-es-nicht", "web"), headers=auth)).status_code == 404
        for bad in ("x;rm", "web%0A", "-f", "a b"):
            assert (await ac.get(url(host.id, bad), headers=auth)).status_code == 400, bad
        login = await ac.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
        assert (await ac.get(url(host.id, "web"), headers=_auth_header(login.json()["access_token"]))).status_code == 403
        assert (await ac.get(url(host.id, "web"))).status_code == 401

    assert not any(word in c for c in docker["calls"] for word in (" pull", " up ", " rm ", "docker start", "docker stop", "docker restart", "docker run"))


@pytest.mark.asyncio
async def test_image_update_goes_through_the_gate_and_never_through_the_host_page(
    running_app, db_session, test_settings, local_ssh_server, fake_docker
):
    """Die Aktion `container.image_update` ist nur ueber die Route der Extension zu haben: sie
    braucht `hosts.execute`, prueft den Namen und die Uebersicht (`plan_id`), legt ihren Vorschlag
    im Gate an (Begruendung mit den Fakten, Befehl im Payload) und laeuft erst nach der Freigabe.
    Auf der Server-Seite (`/hosts/{id}/actions`) taucht sie nicht auf."""
    from httpx import AsyncClient
    from nodvard_deck_ext_service_matrix import applier as ap
    from nodvard_deck_ext_service_matrix import image_apply as ia
    from nodvard_sdk.types import Risk

    from nodvard_deck.core import security
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service, hosts as hosts_service

    http_base, token, host, docker = await _setup_real_docker_host(
        running_app, db_session, test_settings, local_ssh_server, fake_docker
    )
    no_docker = await hosts_service.create_host(db_session, name="proxmox-knoten", address="10.9.9.9")
    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    await db_session.commit()

    loaded = get_extension_runtime().loaded["service-matrix"]
    applier = loaded.instance._applier
    t = ia.ComposeTarget(
        container="web", container_id="a" * 64, image="nginx:1.27", image_id="sha256:" + "c" * 64, project="webstack", service="web",
        working_dir="/opt/web", config_files=("/opt/web/docker-compose.yml",), env_files=(), config_hash=None,
    )
    command = ia.display_command(t)
    remote = "sha256:" + "b" * 64
    plan = ap.Plan(
        target=t, host_id=host.id, host_name="pi-host", affected=("web",), stopped=(), command=command,
        rollback_ref=ia.rollback_ref("web"), rollback=ia.rollback_commands(t, ia.rollback_ref("web")), risk=Risk.MEDIUM, warnings=(),
        remote_digest=remote, registry_at=None, stale=False, plan_id=ia.plan_id(command, t.image_id, remote),
    )

    async def canned(_host, container):  # noqa: ANN001, ANN202 - die Uebersicht selbst ist in test_ext_service_matrix_apply.py geprueft
        return plan if container == "web" else ap.NotUpdatable("gone", "Container nicht gefunden.")

    applier.plan = canned
    base = f"/api/v1/ext/service-matrix/containers/{host.id}"
    async with AsyncClient(base_url=http_base, timeout=60) as ac:
        auth = _auth_header(token)
        body = {"plan_id": plan.plan_id}

        # Rechte, Eingaben, Host.
        login = await ac.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
        assert (await ac.post(f"{base}/web/image-update", json=body, headers=_auth_header(login.json()["access_token"]))).status_code == 403
        assert (await ac.post(f"{base}/web/image-update", json=body)).status_code == 401
        assert (await ac.post(f"/api/v1/ext/service-matrix/containers/{no_docker.id}/web/image-update", json=body, headers=auth)).status_code == 404
        for bad in ("x;rm", "web%0A", "-f"):
            assert (await ac.post(f"{base}/{bad}/image-update", json=body, headers=auth)).status_code == 400, bad
        assert (await ac.post(f"{base}/web/image-update", json={"plan_id": "zu kurz"}, headers=auth)).status_code == 422
        assert (await ac.post(f"{base}/web/image-update", json={}, headers=auth)).status_code == 422
        assert (await ac.post(f"{base}/anderer/image-update", json=body, headers=auth)).status_code == 409
        stale = await ac.post(f"{base}/web/image-update", json={"plan_id": "0" * 16}, headers=auth)
        assert stale.status_code == 409 and "neu laden" in stale.json()["detail"]

        # Nie ueber die Server-Seite.
        listed = await ac.get(f"/api/v1/hosts/{host.id}/actions", headers=auth)
        assert "container.image_update" not in {a["action_type"] for a in listed.json()}
        direct = await ac.post(
            f"/api/v1/hosts/{host.id}/actions/container.image_update", json={"payload": {"container": "web"}, "reason": "Test"}, headers=auth,
        )
        assert direct.status_code in (404, 400, 403, 422), direct.text

        # Vorschlag: nichts laeuft vor der Freigabe.
        proposed = await ac.post(f"{base}/web/image-update", json=body, headers=auth)
        assert proposed.status_code == 202, proposed.text
        answer = proposed.json()
        assert (answer["status"], answer["risk"]) == ("proposed", "medium")
        action = (await ac.get(f"/api/v1/actions/{answer['action_id']}", headers=auth)).json()
        assert action["action_type"] == "container.image_update" and action["payload"]["command"] == command
        assert action["payload"]["old_image_id"] == t.image_id and action["host_id"] == host.id
        assert "„web“ auf pi-host" in action["reason"] and "nginx:1.27" in action["reason"] and "„webstack“/„web“" in action["reason"]
        assert action["correlation_id"].startswith("imgupd_")
        assert not any("compose" in c or "@@launch" in c for c in docker["calls"]), "vor der Freigabe darf nichts laufen"

        # Freigabe: der Executor baut den Befehl neu, prueft ihn und startet den Lauf -- hier ueber den
        # echten SSH-Kanal. Der Test-Server kennt den entkoppelten Start nicht und meldet nur `ran:...`;
        # das muss als "nicht gestartet" enden, nicht als Erfolg und nicht als Absturz.
        approved = await ac.post(f"/api/v1/actions/{answer['action_id']}/approve", headers=auth)
        assert approved.status_code == 200, approved.text
        done = approved.json()
        assert done["status"] == "failed", done
        assert done["result"]["error"].startswith("Update-Lauf konnte nicht gestartet werden"), done["result"]

    assert applier.snapshot()["applying"] == {} and applier._busy == {}


@pytest.mark.asyncio
async def test_image_update_settings_are_declared_and_the_job_follows_them(client, db_session, test_settings):
    """Tagesjob: standardmaessig aus, `enabled` + Zeitplan folgen den Einstellungen."""
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.models import Job

    from sqlalchemy import select

    token = await _bootstrap_owner(client)
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    assert (await client.post("/api/v1/extensions/service-matrix/enable", headers=_auth_header(token))).status_code == 200

    async def job() -> Job:
        db_session.expire_all()
        return (await db_session.execute(select(Job).where(Job.ext_id == "service-matrix", Job.ext_job_key == "image-updates"))).scalar_one()

    row = await job()
    assert (row.enabled, row.schedule, row.name) == (False, "0 0 31 2 *", "Image-Updates prüfen")

    ctx = get_extension_runtime().loaded["service-matrix"].ctx
    await ctx.settings.set({"image_updates_enabled": True, "image_updates_cron": "15 5 * * *"})
    row = await job()
    assert (row.enabled, row.schedule) == (True, "15 5 * * *")

    await ctx.settings.set({"image_updates_enabled": False})
    assert (await job()).enabled is False
