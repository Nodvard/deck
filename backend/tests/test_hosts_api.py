"""GET/POST/PATCH/DELETE /hosts, /hosts/{id}/credentials, /host-groups
(docs/04-API.md §3)."""

from __future__ import annotations

import asyncssh
import pytest
from nodvard_sdk import ActionResult, ActionSpec, DryRunReport, HostStatus, Risk
from nodvard_sdk.capabilities import ActionExecutor, HostProvider, MetricsProvider

from nodvard_deck.ext.runtime import get_extension_runtime


async def _bootstrap_owner(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(client):
    assert (await client.get("/api/v1/hosts")).status_code == 401


@pytest.mark.asyncio
async def test_create_list_get_patch_delete_host(client):
    token = await _bootstrap_owner(client)

    created = await client.post(
        "/api/v1/hosts",
        json={"name": "docker", "address": "192.168.1.10", "tags": ["docker"]},
        headers=_auth_header(token),
    )
    assert created.status_code == 201, created.text
    host_id = created.json()["id"]
    assert created.json()["tags"] == ["docker"]

    listed = await client.get("/api/v1/hosts", headers=_auth_header(token))
    assert [h["name"] for h in listed.json()] == ["docker"]

    filtered = await client.get("/api/v1/hosts", params={"tag": "docker"}, headers=_auth_header(token))
    assert len(filtered.json()) == 1
    filtered_miss = await client.get("/api/v1/hosts", params={"tag": "berry"}, headers=_auth_header(token))
    assert filtered_miss.json() == []

    got = await client.get(f"/api/v1/hosts/{host_id}", headers=_auth_header(token))
    assert got.status_code == 200

    patched = await client.patch(
        f"/api/v1/hosts/{host_id}", json={"display_name": "Docker-Host"}, headers=_auth_header(token)
    )
    assert patched.json()["display_name"] == "Docker-Host"

    deleted = await client.delete(f"/api/v1/hosts/{host_id}", headers=_auth_header(token))
    assert deleted.status_code == 204

    missing = await client.get(f"/api/v1/hosts/{host_id}", headers=_auth_header(token))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_host_name_conflicts(client):
    token = await _bootstrap_owner(client)
    payload = {"name": "docker", "address": "1.2.3.4"}
    first = await client.post("/api/v1/hosts", json=payload, headers=_auth_header(token))
    assert first.status_code == 201
    second = await client.post("/api/v1/hosts", json=payload, headers=_auth_header(token))
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_create_host_requires_hosts_write_permission(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer1", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.flush()

    login = await client.post("/api/v1/auth/login", json={"username": "viewer1", "password": "whatever123"})
    token = login.json()["access_token"]

    r = await client.post("/api/v1/hosts", json={"name": "x", "address": "1.1.1.1"}, headers=_auth_header(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_credential_lifecycle_never_exposes_value(client):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()

    created = await client.post(
        f"/api/v1/hosts/{host['id']}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": 22, "secret_value": "GEHEIM-WERT"},
        headers=_auth_header(token),
    )
    assert created.status_code == 201, created.text
    assert "secret_value" not in created.json()
    assert "GEHEIM-WERT" not in created.text

    listed = await client.get(f"/api/v1/hosts/{host['id']}/credentials", headers=_auth_header(token))
    assert "GEHEIM-WERT" not in listed.text
    assert len(listed.json()) == 1

    deleted = await client.delete(
        f"/api/v1/hosts/{host['id']}/credentials/{created.json()['id']}", headers=_auth_header(token)
    )
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_host_groups_and_membership(client):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    group = (
        await client.post("/api/v1/host-groups", json={"name": "game-servers"}, headers=_auth_header(token))
    ).json()

    add = await client.post(
        f"/api/v1/host-groups/{group['id']}/members/{host['id']}", headers=_auth_header(token)
    )
    assert add.status_code == 204

    filtered = await client.get("/api/v1/hosts", params={"group": group["id"]}, headers=_auth_header(token))
    assert [h["id"] for h in filtered.json()] == [host["id"]]

    remove = await client.delete(
        f"/api/v1/host-groups/{group['id']}/members/{host['id']}", headers=_auth_header(token)
    )
    assert remove.status_code == 204
    filtered_after = await client.get("/api/v1/hosts", params={"group": group["id"]}, headers=_auth_header(token))
    assert filtered_after.json() == []


@pytest.mark.asyncio
async def test_host_status_without_credentials_returns_stored_status(client):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()

    r = await client.get(f"/api/v1/hosts/{host['id']}/status", headers=_auth_header(token))
    assert r.status_code == 200
    assert r.json()["checked_live"] is False
    assert r.json()["status"] == "unknown"


async def _host_with_live_ssh(client, local_ssh_server) -> dict:
    host_addr, port, username, password, _ = local_ssh_server
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "ssh-a", "address": host_addr}, headers=_auth_header(token))
    ).json()
    created = await client.post(
        f"/api/v1/hosts/{host['id']}/credentials",
        json={"kind": "ssh_password", "username": username, "port": port, "secret_value": password},
        headers=_auth_header(token),
    )
    assert created.status_code == 201, created.text
    return {"token": token, "host": host}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        asyncssh.ConnectionLost("Connection lost"),
        asyncssh.ChannelOpenError(asyncssh.OPEN_CONNECT_FAILED, "Channel open failed"),
        ConnectionResetError("Connection reset by peer"),
    ],
    ids=["timeout", "connection-lost", "channel-open", "os-error"],
)
async def test_host_status_dead_pooled_connection_reports_down_not_500(client, local_ssh_server, monkeypatch, error):
    """Ist die gepoolte Verbindung tot, wirft der Befehl TimeoutError,
    ConnectionLost, ChannelOpenError oder einen OSError -- keiner davon ist ein
    SshError. Das heisst trotzdem nur "Server nicht erreichbar", kein HTTP 500."""
    from nodvard_deck.core import ssh

    ctx = await _host_with_live_ssh(client, local_ssh_server)
    url = f"/api/v1/hosts/{ctx['host']['id']}/status"
    first = await client.get(url, headers=_auth_header(ctx["token"]))
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "up"

    async def _dead_run(conn, command, *, timeout_s=60.0):
        raise error

    monkeypatch.setattr(ssh, "run", _dead_run)
    try:
        second = await client.get(url, headers=_auth_header(ctx["token"]))
        assert second.status_code == 200, second.text
        body = second.json()
        assert body["status"] == "down"
        assert body["checked_live"] is True
        assert body["detail"]
    finally:
        await ssh.reset_ssh_pool()


@pytest.mark.asyncio
async def test_host_status_sets_the_login_proof_and_a_refused_login_drops_it(client, local_ssh_server, monkeypatch):
    """Eine Live-Prüfung mit Anmeldung belegt den Zugang (`login_ok_at`); lehnt der Server die Anmeldung
    später ab, gilt der Beleg nicht mehr."""
    from nodvard_deck.core import ssh

    ctx = await _host_with_live_ssh(client, local_ssh_server)
    host_url = f"/api/v1/hosts/{ctx['host']['id']}"
    first = await client.get(f"{host_url}/status", headers=_auth_header(ctx["token"]))
    assert first.json()["status"] == "up"
    assert (await client.get(host_url, headers=_auth_header(ctx["token"]))).json()["login_ok_at"] is not None

    await ssh.reset_ssh_pool()

    class _RefusingPool:
        def generation(self, host_id: str) -> int:
            return 0

        async def get(self, session, target, **kwargs):  # noqa: ANN001, ARG002
            raise ssh.SshAuthError("abgelehnt")

    monkeypatch.setattr(ssh, "_pool", _RefusingPool())
    second = await client.get(f"{host_url}/status", headers=_auth_header(ctx["token"]))
    assert second.status_code == 200, second.text
    assert (await client.get(host_url, headers=_auth_header(ctx["token"]))).json()["login_ok_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_key", [True, False], ids=["changed-key", "unconfirmed-key"])
async def test_host_status_key_problem_keeps_or_drops_the_login_proof(client, local_ssh_server, monkeypatch, changed_key):
    """Zeigt der Server einen anderen Schlüssel als gemerkt, galt die frühere Anmeldung einem anderen
    Gegenüber: der Beleg fällt weg. Ein nur noch nicht bestätigter Schlüssel widerruft nichts. In beiden
    Fällen steht der Server auf „unbekannt“, nicht auf „down“."""
    from nodvard_deck.core import ssh

    ctx = await _host_with_live_ssh(client, local_ssh_server)
    host_url = f"/api/v1/hosts/{ctx['host']['id']}"
    assert (await client.get(f"{host_url}/status", headers=_auth_header(ctx["token"]))).json()["status"] == "up"
    proof = (await client.get(host_url, headers=_auth_header(ctx["token"]))).json()["login_ok_at"]
    assert proof is not None

    await ssh.reset_ssh_pool()
    error = (
        ssh.HostKeyMismatch(ctx["host"]["id"], "ssh-ed25519", "SHA256:gemerkt", "SHA256:jetzt")
        if changed_key
        else ssh.HostKeyUnknown(ctx["host"]["id"], "ssh-ed25519", "SHA256:jetzt")
    )

    class _KeyProblemPool:
        def generation(self, host_id: str) -> int:
            return 0

        async def get(self, session, target, **kwargs):
            raise error

    monkeypatch.setattr(ssh, "_pool", _KeyProblemPool())
    second = await client.get(f"{host_url}/status", headers=_auth_header(ctx["token"]))
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "unknown"
    assert (await client.get(host_url, headers=_auth_header(ctx["token"]))).json()["login_ok_at"] == (None if changed_key else proof)


class _FakeUpHostProvider:
    provider_id = "fake-provider"

    async def discover_hosts(self):
        return []

    async def host_status(self, provider_ref: str) -> HostStatus:
        return HostStatus.UP

    async def host_actions(self, provider_ref: str) -> list[ActionSpec]:
        return []


class _FakeMetricsProvider:
    async def sample(self, host) -> dict[str, float]:  # noqa: ANN001 - nodvard_sdk.Host
        return {"cpu_percent": 12.5, "mem_used_bytes": 1024.0}

    async def metric_names(self) -> list[str]:
        return ["cpu_percent", "mem_used_bytes"]


async def _host_with_provider(client, db_session, *, ext_id: str = "fake-provider") -> dict:
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    from nodvard_deck.models import Host as HostModel

    db_host = await db_session.get(HostModel, host["id"])
    db_host.provider_ext_id = ext_id
    db_host.provider_ref = "vm/100"
    await db_session.flush()
    return {"token": token, "host": host}


@pytest.mark.asyncio
async def test_host_status_falls_back_to_host_provider_without_credentials(client, db_session):
    """Ein per `HostProvider` entdeckter Host hat typischerweise keine
    SSH-Zugangsdaten (Proxmox spricht seine eigene API) -- vorher blieb `GET
    /hosts/{id}/status` in diesem Fall beim zuletzt gespeicherten `status` stehen,
    OHNE je den registrierten `HostProvider` zu fragen."""
    ctx = await _host_with_provider(client, db_session)
    get_extension_runtime().capabilities.provide("fake-provider", HostProvider, _FakeUpHostProvider())

    r = await client.get(f"/api/v1/hosts/{ctx['host']['id']}/status", headers=_auth_header(ctx["token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["checked_live"] is True
    assert body["status"] == "up"


@pytest.mark.asyncio
async def test_host_metrics_dispatches_to_metrics_provider(client, db_session):
    ctx = await _host_with_provider(client, db_session)
    get_extension_runtime().capabilities.provide("fake-provider", MetricsProvider, _FakeMetricsProvider())

    r = await client.get(f"/api/v1/hosts/{ctx['host']['id']}/metrics", headers=_auth_header(ctx["token"]))
    assert r.status_code == 200
    body = r.json()
    assert body["values"] == {"cpu_percent": 12.5, "mem_used_bytes": 1024.0}
    assert body["sampled_at"] is not None


@pytest.mark.asyncio
async def test_host_metrics_404_without_registered_provider(client):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()

    r = await client.get(f"/api/v1/hosts/{host['id']}/metrics", headers=_auth_header(token))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Aktionen -- GET/POST /hosts/{id}/actions..., gehen durch core.gate
# ---------------------------------------------------------------------------


class _FakeShellExecutor:
    action_types = frozenset({"shell.exec"})

    async def execute(self, req) -> ActionResult:  # noqa: ANN001 - nodvard_sdk.ActionRequest
        return ActionResult(success=True, exit_code=0, output="ok")

    async def dry_run(self, req) -> DryRunReport | None:  # noqa: ANN001
        return None


class _FakeHostProvider:
    provider_id = "fake-provider"

    async def discover_hosts(self):
        return []

    async def host_status(self, provider_ref: str) -> HostStatus:
        return HostStatus.UP

    async def host_actions(self, provider_ref: str) -> list[ActionSpec]:
        return [ActionSpec(action_type="vm.start", label="VM starten", host_bound=True)]


def _register_shell_exec_spec(*, default_risk=Risk.HIGH) -> None:
    get_extension_runtime().actions.register(
        "terminal",
        ActionSpec(
            action_type="shell.exec", label="Shell-Befehl ausfuehren", host_bound=True,
            permissions=["hosts.execute"], command_field="command", default_risk=default_risk,
        ),
    )


@pytest.mark.asyncio
async def test_list_host_actions_returns_generic_registered_specs(client):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    _register_shell_exec_spec()

    r = await client.get(f"/api/v1/hosts/{host['id']}/actions", headers=_auth_header(token))
    assert r.status_code == 200
    assert "shell.exec" in {s["action_type"] for s in r.json()}


@pytest.mark.asyncio
async def test_list_host_actions_merges_provider_specific_specs(client, db_session):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()

    from nodvard_deck.models import Host as HostModel

    db_host = await db_session.get(HostModel, host["id"])
    db_host.provider_ext_id = "fake-provider"
    await db_session.flush()
    get_extension_runtime().capabilities.provide("fake-provider", HostProvider, _FakeHostProvider())
    _register_shell_exec_spec()

    r = await client.get(f"/api/v1/hosts/{host['id']}/actions", headers=_auth_header(token))
    assert r.status_code == 200
    by_type = {s["action_type"]: s for s in r.json()}
    # Server-Seite: was der Provider fuer DIESEN Host anbietet, steht als Knopf oben;
    # fuer jeden Host registrierte Aktionen unter "Weitere Aktionen".
    assert by_type["vm.start"]["source"] == "host"
    assert by_type["shell.exec"]["source"] == "global"


@pytest.mark.asyncio
async def test_list_host_actions_unknown_host_is_404(client):
    token = await _bootstrap_owner(client)
    r = await client.get("/api/v1/hosts/does-not-exist/actions", headers=_auth_header(token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_trigger_host_action_unknown_action_type_is_404(client):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    r = await client.post(
        f"/api/v1/hosts/{host['id']}/actions/does.not.exist",
        json={"reason": "Test"}, headers=_auth_header(token),
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_trigger_host_action_creates_proposal_and_returns_202(client):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    _register_shell_exec_spec()

    r = await client.post(
        f"/api/v1/hosts/{host['id']}/actions/shell.exec",
        json={"payload": {"command": "uptime"}, "reason": "Diagnose"},
        headers=_auth_header(token),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "proposed"
    assert body["proposed_by_type"] == "user"
    assert body["proposed_by_label"] == "owner1", "Name statt user/<uuid>"
    assert body["host_id"] == host["id"]


@pytest.mark.asyncio
async def test_trigger_host_action_dangerous_command_is_denied_with_403(client):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    _register_shell_exec_spec()

    r = await client.post(
        f"/api/v1/hosts/{host['id']}/actions/shell.exec",
        json={"payload": {"command": "rm -rf /"}, "reason": "oops"},
        headers=_auth_header(token),
    )
    assert r.status_code == 403
    assert r.json()["status"] == "denied"


@pytest.mark.asyncio
async def test_trigger_host_action_requires_the_specs_declared_permission(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    owner_token = await _bootstrap_owner(client)
    host = (
        await client.post(
            "/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(owner_token)
        )
    ).json()
    _register_shell_exec_spec()

    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer2", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])  # hat kein hosts.execute
    db_session.add(user)
    await db_session.flush()

    login = await client.post("/api/v1/auth/login", json={"username": "viewer2", "password": "whatever123"})
    token = login.json()["access_token"]

    r = await client.post(
        f"/api/v1/hosts/{host['id']}/actions/shell.exec",
        json={"payload": {"command": "uptime"}, "reason": "Diagnose"},
        headers=_auth_header(token),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_trigger_host_action_under_full_autonomy_executes_immediately(client):
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    _register_shell_exec_spec(default_risk=Risk.LOW)
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, _FakeShellExecutor())

    assert (
        await client.put("/api/v1/settings/autonomy.mode", json={"value": "full"}, headers=_auth_header(token))
    ).status_code == 200
    assert (
        await client.put("/api/v1/settings/autonomy.max_risk", json={"value": "low"}, headers=_auth_header(token))
    ).status_code == 200

    r = await client.post(
        f"/api/v1/hosts/{host['id']}/actions/shell.exec",
        json={"payload": {"command": "uptime"}, "reason": "Diagnose"},
        headers=_auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_trigger_host_action_under_full_autonomy_runs_long_actions_in_background(client, file_db, monkeypatch):
    """Auch die sofort freigegebene Aktion laeuft im Hintergrund. Nach der
    Wartezeit kommt 202 mit 'executing', das Ergebnis gibt es unter GET /actions/{id}."""
    import asyncio

    from nodvard_deck.core import gate

    class _SlowShellExecutor(_FakeShellExecutor):
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def execute(self, req) -> ActionResult:  # noqa: ANN001
            await self.release.wait()
            return ActionResult(success=True, exit_code=0, output="spaeter fertig")

    monkeypatch.setattr(gate, "WAIT_S", 0.2)
    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    _register_shell_exec_spec(default_risk=Risk.LOW)
    executor = _SlowShellExecutor()
    get_extension_runtime().capabilities.provide("terminal", ActionExecutor, executor)
    await client.put("/api/v1/settings/autonomy.mode", json={"value": "full"}, headers=_auth_header(token))
    await client.put("/api/v1/settings/autonomy.max_risk", json={"value": "low"}, headers=_auth_header(token))

    r = await client.post(
        f"/api/v1/hosts/{host['id']}/actions/shell.exec?wait=0",
        json={"payload": {"command": "uptime"}, "reason": "Diagnose"},
        headers=_auth_header(token),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "executing"
    assert r.headers["location"] == f"/api/v1/actions/{body['id']}"

    executor.release.set()
    assert await gate.wait_for_action(body["id"], 5) is True
    done = (await client.get(f"/api/v1/actions/{body['id']}", headers=_auth_header(token))).json()
    assert done["status"] == "succeeded"
    assert done["result"]["output"] == "spaeter fertig"


@pytest.mark.asyncio
async def test_trigger_host_action_rejects_specs_that_are_not_host_bound(client, db_session):
    """Eine Aktion mit host_bound=False (Beispiel: eine IP-Sperre, deren
    Befehl die Extension selbst aus geprueften Feldern baut) steht nicht auf der
    Server-Seite -- und darf sich auch per POST nicht mit einem frei gewaehlten
    `command` ausloesen lassen. Sie verhaelt sich wie eine unbekannte Aktion (404)."""
    from sqlalchemy import func, select

    from nodvard_deck.models import Action

    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    get_extension_runtime().actions.register(
        "demo-ext",
        ActionSpec(
            action_type="demo.ban", label="IP sperren", default_risk=Risk.LOW, host_bound=False,
            permissions=["hosts.execute"], command_field="command",
        ),
    )
    _register_shell_exec_spec()

    listed = await client.get(f"/api/v1/hosts/{host['id']}/actions", headers=_auth_header(token))
    assert "demo.ban" not in {s["action_type"] for s in listed.json()}

    r = await client.post(
        f"/api/v1/hosts/{host['id']}/actions/demo.ban",
        json={"payload": {"command": "id; cat /etc/shadow"}, "reason": "Test"}, headers=_auth_header(token),
    )
    assert r.status_code == 404, r.text
    assert "nicht verfügbar" in r.json()["detail"]
    assert (await db_session.execute(select(func.count()).select_from(Action))).scalar_one() == 0

    # Eine host-gebundene Aktion laeuft ueber denselben Weg weiter.
    ok = await client.post(
        f"/api/v1/hosts/{host['id']}/actions/shell.exec",
        json={"payload": {"command": "uptime"}, "reason": "Diagnose"}, headers=_auth_header(token),
    )
    assert ok.status_code == 202, ok.text
    assert ok.json()["status"] == "proposed"


async def _discovered_host(tmp_path, tags):
    from nodvard_sdk import DiscoveredHost

    from test_ext_context import _build

    _, _, ctx = _build(tmp_path, ext_id="proxmox", permissions=["hosts.read", "hosts.write"])
    dh = DiscoveredHost(provider_ref="vm/100", name="dockervm", address="192.168.1.10", tags=tags)
    return ctx, dh, (await ctx.hosts.upsert_discovered([dh]))[0]


@pytest.mark.asyncio
async def test_patch_host_tags_with_real_discovery_sync(client, db_session, tmp_path):
    token = await _bootstrap_owner(client)
    ctx, dh, host = await _discovered_host(tmp_path, ["proxmox", "vm"])
    url = f"/api/v1/hosts/{host.id}"

    r = await client.patch(url, json={"tags": ["docker", "gameserver", "docker", "proxmox"]}, headers=_auth_header(token))
    assert r.status_code == 200, r.text
    assert sorted(r.json()["tags"]) == ["docker", "gameserver", "proxmox", "vm"]

    # Ersetzen: manuelle Tags exakt wie angegeben, Extension-Tags bleiben.
    r = await client.patch(url, json={"tags": ["gameserver"]}, headers=_auth_header(token))
    assert sorted(r.json()["tags"]) == ["gameserver", "proxmox", "vm"]
    assert sorted((await client.get(url, headers=_auth_header(token))).json()["tags"]) == ["gameserver", "proxmox", "vm"]

    # Ohne `tags` im Body bleibt alles wie es ist; leere Liste entfernt nur manuelle.
    assert sorted((await client.patch(url, json={"display_name": "X"}, headers=_auth_header(token))).json()["tags"]) == ["gameserver", "proxmox", "vm"]
    assert sorted((await client.patch(url, json={"tags": []}, headers=_auth_header(token))).json()["tags"]) == ["proxmox", "vm"]

    # Ein späterer Discovery-Lauf entfernt manuelle Tags nicht.
    await client.patch(url, json={"tags": ["docker"]}, headers=_auth_header(token))
    await ctx.hosts.upsert_discovered([dh.model_copy(update={"tags": ["proxmox"]})])
    tags = (await client.get(url, headers=_auth_header(token))).json()["tags"]
    assert sorted(tags) == ["docker", "proxmox"]


@pytest.mark.asyncio
async def test_patch_host_tags_normalized_and_keeps_extension_tag(client, db_session):
    from nodvard_deck.models import HostTag

    token = await _bootstrap_owner(client)
    created = await client.post(
        "/api/v1/hosts", json={"name": "pve-vm", "address": "10.0.0.5", "tags": ["alt"]}, headers=_auth_header(token)
    )
    host_id = created.json()["id"]
    db_session.add(HostTag(host_id=host_id, tag="proxmox", managed_by_ext_id="proxmox"))
    await db_session.flush()

    patched = await client.patch(
        f"/api/v1/hosts/{host_id}", json={"tags": [" Docker", "gameserver", "docker", "proxmox"]}, headers=_auth_header(token)
    )
    assert patched.status_code == 200, patched.text
    assert sorted(patched.json()["tags"]) == ["docker", "gameserver", "proxmox"]
    cleared = await client.patch(f"/api/v1/hosts/{host_id}", json={"tags": []}, headers=_auth_header(token))
    assert cleared.json()["tags"] == ["proxmox"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tags", [["Bad Tag!"], ["-x"], ["a" * 33], [f"t{i}" for i in range(21)], [""]])
async def test_patch_host_rejects_invalid_tags(client, tags):
    token = await _bootstrap_owner(client)
    created = await client.post("/api/v1/hosts", json={"name": "h", "address": "10.0.0.6"}, headers=_auth_header(token))
    resp = await client.patch(f"/api/v1/hosts/{created.json()['id']}", json={"tags": tags}, headers=_auth_header(token))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_host_removes_its_secrets(client, db_session):
    from sqlalchemy import func, select

    from nodvard_deck.models import Secret

    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    created = await client.post(
        f"/api/v1/hosts/{host['id']}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": 22, "secret_value": "GEHEIM-WERT"},
        headers=_auth_header(token),
    )
    assert created.status_code == 201, created.text
    count = select(func.count()).select_from(Secret).where(Secret.label.like("host-cred:%"))
    assert (await db_session.execute(count)).scalar_one() == 1

    assert (await client.delete(f"/api/v1/hosts/{host['id']}", headers=_auth_header(token))).status_code == 204
    assert (await db_session.execute(count)).scalar_one() == 0


@pytest.mark.asyncio
async def test_delete_host_is_refused_with_409_while_an_action_executes(client, db_session):
    from nodvard_deck.models import Action

    token = await _bootstrap_owner(client)
    host = (
        await client.post("/api/v1/hosts", json={"name": "a", "address": "1.1.1.1"}, headers=_auth_header(token))
    ).json()
    db_session.add(Action(
        ext_id="core", action_type="shell.exec", host_id=host["id"], status="executing",
        proposed_by_type="user", proposed_by_id="u1", reason="Test",
    ))
    await db_session.commit()

    refused = await client.delete(f"/api/v1/hosts/{host['id']}", headers=_auth_header(token))
    assert refused.status_code == 409
    assert "läuft gerade eine Aktion" in refused.json()["detail"]
    assert (await client.get(f"/api/v1/hosts/{host['id']}", headers=_auth_header(token))).status_code == 200


# ---------------------------------------------------------------------------
# Validierung (Kurzname, Adresse, Markierungen, Zugangsdaten)
# ---------------------------------------------------------------------------


def _error_text(resp) -> str:
    return " ".join(str(item.get("msg", "")) for item in resp.json()["detail"])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Bad Name", "-x", "_x", "a" * 65, "ä", "a.b", "a/b", "  "])
async def test_create_host_rejects_invalid_names(client, name):
    token = await _bootstrap_owner(client)
    resp = await client.post("/api/v1/hosts", json={"name": name, "address": "1.1.1.1"}, headers=_auth_header(token))
    assert resp.status_code == 422
    if name.strip():
        assert "Kurzname: nur Kleinbuchstaben, Ziffern, - und _ (höchstens 64 Zeichen)." in _error_text(resp)


@pytest.mark.asyncio
async def test_create_host_normalises_the_name_and_address(client):
    token = await _bootstrap_owner(client)
    resp = await client.post(
        "/api/v1/hosts", json={"name": "  Mein-Pi  ", "address": "  Mein.Fritz.Box "}, headers=_auth_header(token)
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "mein-pi"
    assert resp.json()["address"] == "mein.fritz.box"
    assert resp.json()["display_name"] == "mein-pi"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "http://1.2.3.4", "https://host", "root@host", "host/pfad", "my host", "1.2.3.4:22", "host:22",
        "0.0.0.0", "::", "224.0.0.1", "ff02::1", "-host", "host-", "a..b", "a" * 64 + ".de", "x." * 130 + "de",
        "999.1.1.1", "1.2.3", "", "   ", "host_name", ".", "a..", "host.fritz.box..",
    ],
)
async def test_create_host_rejects_invalid_addresses(client, address):
    token = await _bootstrap_owner(client)
    resp = await client.post("/api/v1/hosts", json={"name": "h", "address": address}, headers=_auth_header(token))
    assert resp.status_code == 422, address
    if address.strip():
        assert "Adresse: nur IP-Adresse oder Rechnername, ohne http:// und ohne Port." in _error_text(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["192.168.1.72", "127.0.0.1", "fd00::1", "2001:db8::5", "mein-pi", "mein-pi.fritz.box", "a1.b2"])
async def test_create_host_accepts_valid_addresses(client, address):
    token = await _bootstrap_owner(client)
    resp = await client.post("/api/v1/hosts", json={"name": "h", "address": address}, headers=_auth_header(token))
    assert resp.status_code == 201, (address, resp.text)
    assert resp.json()["address"] == address


@pytest.mark.asyncio
async def test_patch_host_validates_the_address(client):
    token = await _bootstrap_owner(client)
    created = await client.post("/api/v1/hosts", json={"name": "h", "address": "10.0.0.6"}, headers=_auth_header(token))
    url = f"/api/v1/hosts/{created.json()['id']}"
    assert (await client.patch(url, json={"address": "http://x"}, headers=_auth_header(token))).status_code == 422
    ok = await client.patch(url, json={"address": " 10.0.0.7 "}, headers=_auth_header(token))
    assert ok.status_code == 200
    assert ok.json()["address"] == "10.0.0.7"


@pytest.mark.asyncio
@pytest.mark.parametrize("tags", [["Bad Tag!"], ["-x"], ["a" * 33], [f"t{i}" for i in range(21)], [""]])
async def test_create_host_rejects_invalid_tags(client, tags):
    token = await _bootstrap_owner(client)
    resp = await client.post(
        "/api/v1/hosts", json={"name": "h", "address": "1.1.1.1", "tags": tags}, headers=_auth_header(token)
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_host_normalises_tags(client):
    token = await _bootstrap_owner(client)
    resp = await client.post(
        "/api/v1/hosts", json={"name": "h", "address": "1.1.1.1", "tags": [" Docker", "docker", "Berry"]},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201, resp.text
    assert sorted(resp.json()["tags"]) == ["berry", "docker"]


async def _host_id(client, token):
    created = await client.post("/api/v1/hosts", json={"name": "h", "address": "1.1.1.1"}, headers=_auth_header(token))
    return created.json()["id"]


def _new_key(**kwargs) -> str:
    return asyncssh.generate_private_key("ssh-ed25519").export_private_key("openssh", **kwargs).decode()


@pytest.mark.asyncio
@pytest.mark.parametrize("port", [0, -1, 65536, 99999])
async def test_credential_rejects_invalid_ports(client, port):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    resp = await client.post(
        f"/api/v1/hosts/{host_id}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": port, "secret_value": "x"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422
    assert "SSH-Port: eine Zahl von 1 bis 65535." in _error_text(resp)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "username", ["", "  ", "-root", ".x", "root;rm", "a" * 65, "ü", "a\nb", "a\tb", "a\x00b", "a'b", 'a"b', "a$(x)", "a`x`"]
)
async def test_credential_rejects_invalid_usernames(client, username):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    resp = await client.post(
        f"/api/v1/hosts/{host_id}/credentials",
        json={"kind": "ssh_password", "username": username, "port": 22, "secret_value": "x"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422, username


@pytest.mark.asyncio
@pytest.mark.parametrize("username", [
        "root", "lattice", "Administrator", "_svc", "deploy.user", "a-b_c", "1user",
        "Max Mustermann", "user@domain.local", "DOMAIN\\user",
    ])
async def test_credential_accepts_usual_usernames(client, username):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    resp = await client.post(
        f"/api/v1/hosts/{host_id}/credentials",
        json={"kind": "ssh_password", "username": username, "port": 2222, "secret_value": "x"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201, (username, resp.text)
    assert resp.json()["username"] == username
    assert resp.json()["port"] == 2222


@pytest.mark.asyncio
async def test_credential_accepts_a_valid_key_in_the_usual_paste_shapes(client):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    key = _new_key()
    for pasted in (key, key.strip(), "\n  " + key.replace("\n", "\r\n") + "\n\n"):
        resp = await client.post(
            f"/api/v1/hosts/{host_id}/credentials",
            json={"kind": "ssh_key", "username": "lattice", "port": 22, "secret_value": pasted},
            headers=_auth_header(token),
        )
        assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_credential_rejects_an_invalid_key_without_echoing_it(client):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    key = _new_key()
    marker = key.splitlines()[1]  # eine Zeile echten Schluesselmaterials
    cases = {
        "Das ist kein gültiger privater SSH-Schlüssel.": [
            "hallo welt",
            key[:-60],  # abgeschnitten, Material steht noch drin
            asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode(),
        ],
        "Der Schlüssel ist mit einer Passphrase geschützt – Nodvard Deck braucht einen Schlüssel ohne Passphrase.": [
            asyncssh.generate_private_key("ssh-ed25519").export_private_key("pkcs8-pem", passphrase="x").decode(),
        ],
    }
    for message, values in cases.items():
        for value in values:
            resp = await client.post(
                f"/api/v1/hosts/{host_id}/credentials",
                json={"kind": "ssh_key", "username": "lattice", "port": 22, "secret_value": value},
                headers=_auth_header(token),
            )
            assert resp.status_code == 422, value
            assert message in _error_text(resp)
            # Weder Schluesselmaterial noch die Eingabe kommen in der Antwort zurueck.
            assert "input" not in resp.text
            assert marker not in resp.text
            assert value.strip() not in resp.text
            assert "BEGIN" not in resp.text
    # Nichts wurde gespeichert.
    listed = await client.get(f"/api/v1/hosts/{host_id}/credentials", headers=_auth_header(token))
    assert listed.json() == []


@pytest.mark.asyncio
async def test_password_credential_is_not_parsed_as_a_key(client):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    resp = await client.post(
        f"/api/v1/hosts/{host_id}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": 22, "secret_value": "kein schluessel"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_validation_errors_never_echo_the_input(client):
    """Die Standard-422-Antwort von FastAPI haengt die Eingabe an (`input`) -- bei
    Passwoertern und Schluesseln darf das nie zurueckkommen."""
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    resp = await client.post(
        f"/api/v1/hosts/{host_id}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": 70000, "secret_value": "GEHEIMES-PASSWORT"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422
    assert "GEHEIMES-PASSWORT" not in resp.text
    assert "70000" not in resp.text


# ---------------------------------------------------------------------------
# Gruppen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_group_member_is_idempotent_and_404s_on_unknown_ids(client):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    group = (await client.post("/api/v1/host-groups", json={"name": "g"}, headers=_auth_header(token))).json()
    url = f"/api/v1/host-groups/{group['id']}/members/{host_id}"

    assert (await client.post(url, headers=_auth_header(token))).status_code == 204
    assert (await client.post(url, headers=_auth_header(token))).status_code == 204  # kein 500
    members = await client.get("/api/v1/hosts", params={"group": group["id"]}, headers=_auth_header(token))
    assert [h["id"] for h in members.json()] == [host_id]

    bad_group = await client.post(f"/api/v1/host-groups/gibt-es-nicht/members/{host_id}", headers=_auth_header(token))
    assert bad_group.status_code == 404
    bad_host = await client.post(f"/api/v1/host-groups/{group['id']}/members/gibt-es-nicht", headers=_auth_header(token))
    assert bad_host.status_code == 404


@pytest.mark.asyncio
async def test_patch_and_delete_host_group(client):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    group = (await client.post("/api/v1/host-groups", json={"name": "alt"}, headers=_auth_header(token))).json()
    other = (await client.post("/api/v1/host-groups", json={"name": "anders"}, headers=_auth_header(token))).json()
    await client.post(f"/api/v1/host-groups/{group['id']}/members/{host_id}", headers=_auth_header(token))

    renamed = await client.patch(
        f"/api/v1/host-groups/{group['id']}", json={"name": " neu ", "description": "Beschreibung"},
        headers=_auth_header(token),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json() == {"id": group["id"], "name": "neu", "description": "Beschreibung"}

    only_description = await client.patch(
        f"/api/v1/host-groups/{group['id']}", json={"description": ""}, headers=_auth_header(token)
    )
    assert only_description.json()["name"] == "neu"
    assert only_description.json()["description"] == ""

    clash = await client.patch(f"/api/v1/host-groups/{group['id']}", json={"name": "anders"}, headers=_auth_header(token))
    assert clash.status_code == 409
    same = await client.patch(f"/api/v1/host-groups/{group['id']}", json={"name": "neu"}, headers=_auth_header(token))
    assert same.status_code == 200  # der eigene Name ist kein Konflikt
    assert (await client.patch(f"/api/v1/host-groups/{group['id']}", json={"name": " "}, headers=_auth_header(token))).status_code == 422
    assert (await client.patch("/api/v1/host-groups/gibt-es-nicht", json={"name": "x"}, headers=_auth_header(token))).status_code == 404

    assert (await client.delete(f"/api/v1/host-groups/{group['id']}", headers=_auth_header(token))).status_code == 204
    assert (await client.delete(f"/api/v1/host-groups/{group['id']}", headers=_auth_header(token))).status_code == 404
    listed = await client.get("/api/v1/host-groups", headers=_auth_header(token))
    assert [g["id"] for g in listed.json()] == [other["id"]]
    # Der Server selbst bleibt.
    assert (await client.get(f"/api/v1/hosts/{host_id}", headers=_auth_header(token))).status_code == 200
    # Gruppenfilter auf eine geloeschte Gruppe liefert leer (Skripte loesen nur group_id auf).
    gone = await client.get("/api/v1/hosts", params={"group": group["id"]}, headers=_auth_header(token))
    assert gone.json() == []


@pytest.mark.asyncio
async def test_group_writes_need_hosts_write(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service

    owner = await _bootstrap_owner(client)
    group = (await client.post("/api/v1/host-groups", json={"name": "g"}, headers=_auth_header(owner))).json()
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username="viewer2", password_hash=security.hash_password("whatever123"), is_active=True)
    user.roles.append(roles["viewer"])
    db_session.add(user)
    await db_session.commit()
    token = (await client.post("/api/v1/auth/login", json={"username": "viewer2", "password": "whatever123"})).json()["access_token"]
    assert (await client.patch(f"/api/v1/host-groups/{group['id']}", json={"name": "x"}, headers=_auth_header(token))).status_code == 403
    assert (await client.delete(f"/api/v1/host-groups/{group['id']}", headers=_auth_header(token))).status_code == 403


# ---------------------------------------------------------------------------
# Protokoll (Audit) fuer alle Aenderungen an Servern, Zugaengen, Schluesseln, Gruppen
# ---------------------------------------------------------------------------


async def _audit_rows(db_session, prefix="host."):
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    rows = (await db_session.execute(select(AuditEntry).order_by(AuditEntry.ts, AuditEntry.id))).scalars().all()
    return [r for r in rows if r.action.startswith(prefix)]


@pytest.mark.asyncio
async def test_host_lifecycle_is_audited(client, db_session):
    token = await _bootstrap_owner(client)
    me = (await client.get("/api/v1/me", headers=_auth_header(token))).json()
    created = (
        await client.post(
            "/api/v1/hosts",
            json={"name": "berry", "address": "10.0.0.5", "tags": ["docker"]},
            headers=_auth_header(token),
        )
    ).json()
    hid = created["id"]
    await client.patch(
        f"/api/v1/hosts/{hid}",
        json={"address": "10.0.0.6", "display_name": "Berry", "tags": ["docker", "pi"], "enabled": False},
        headers=_auth_header(token),
    )
    # Ein Patch ohne tatsaechliche Aenderung schreibt nichts.
    await client.patch(f"/api/v1/hosts/{hid}", json={"address": "10.0.0.6"}, headers=_auth_header(token))
    await client.delete(f"/api/v1/hosts/{hid}", headers=_auth_header(token))

    rows = await _audit_rows(db_session)
    assert [r.action for r in rows] == ["host.created", "host.updated", "host.deleted"]
    assert all(r.outcome == "success" and r.actor_type == "user" and r.actor_id == me["id"] for r in rows)
    assert all(r.target_type == "host" and r.target_id == hid for r in rows)
    assert rows[0].detail == {"name": "berry", "address": "10.0.0.5", "os_family": "linux", "kind": None, "tags": ["docker"]}
    changed = rows[1].detail["changed"]
    assert changed["address"] == {"from": "10.0.0.5", "to": "10.0.0.6"}
    assert changed["display_name"] == {"from": "berry", "to": "Berry"}
    assert changed["enabled"] == {"from": True, "to": False}
    assert changed["tags"] == {"from": ["docker"], "to": ["docker", "pi"]}
    assert rows[2].detail["name"] == "berry"
    assert rows[2].detail["address"] == "10.0.0.6"


@pytest.mark.asyncio
async def test_credential_and_known_key_changes_are_audited_without_secrets(client, db_session):
    from nodvard_deck.models import KnownHostKey

    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    key = _new_key()
    created = await client.post(
        f"/api/v1/hosts/{host_id}/credentials",
        json={"kind": "ssh_key", "username": "lattice", "port": 2222, "secret_value": key},
        headers=_auth_header(token),
    )
    cid = created.json()["id"]
    await client.post(
        f"/api/v1/hosts/{host_id}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": 22, "secret_value": "GEHEIM-WERT"},
        headers=_auth_header(token),
    )
    await client.delete(f"/api/v1/hosts/{host_id}/credentials/{cid}", headers=_auth_header(token))
    # Unbekannter Zugang: 404 und kein Protokolleintrag.
    assert (await client.delete(f"/api/v1/hosts/{host_id}/credentials/gibt-es-nicht", headers=_auth_header(token))).status_code == 404
    db_session.add(KnownHostKey(host_id=host_id, key_type="ssh-ed25519", fingerprint="SHA256:abc"))
    await db_session.commit()
    assert (await client.delete(f"/api/v1/hosts/{host_id}/known-hosts/ssh-ed25519", headers=_auth_header(token))).status_code == 204
    assert (await client.delete(f"/api/v1/hosts/{host_id}/known-hosts/ssh-ed25519", headers=_auth_header(token))).status_code == 404

    rows = [r for r in await _audit_rows(db_session) if r.action != "host.created"]
    assert [r.action for r in rows] == [
        "host.credential_added", "host.credential_added", "host.credential_deleted", "host.known_key_forgotten",
    ]
    assert rows[0].detail == {"credential_id": cid, "kind": "ssh_key", "username": "lattice", "port": 2222, "is_default": True}
    assert rows[2].detail == {"credential_id": cid, "kind": "ssh_key", "username": "lattice", "port": 2222}
    assert rows[3].detail == {"key_type": "ssh-ed25519", "fingerprint": "SHA256:abc"}
    assert all(r.target_type == "host" and r.target_id == host_id for r in rows)
    dump = " ".join(f"{r.action} {r.reason} {r.detail}" for r in await _audit_rows(db_session, ""))
    assert "GEHEIM-WERT" not in dump
    assert "PRIVATE KEY" not in dump
    assert key.splitlines()[1] not in dump


@pytest.mark.asyncio
async def test_group_changes_are_audited(client, db_session):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    group = (await client.post("/api/v1/host-groups", json={"name": "g1"}, headers=_auth_header(token))).json()
    gid = group["id"]
    await client.patch(f"/api/v1/host-groups/{gid}", json={"name": "g2"}, headers=_auth_header(token))
    await client.post(f"/api/v1/host-groups/{gid}/members/{host_id}", headers=_auth_header(token))
    await client.post(f"/api/v1/host-groups/{gid}/members/{host_id}", headers=_auth_header(token))  # schon drin
    await client.delete(f"/api/v1/host-groups/{gid}/members/{host_id}", headers=_auth_header(token))
    await client.delete(f"/api/v1/host-groups/{gid}/members/{host_id}", headers=_auth_header(token))  # schon raus
    await client.delete(f"/api/v1/host-groups/{gid}", headers=_auth_header(token))

    rows = await _audit_rows(db_session, "host.group_changed")
    assert [r.detail["change"] for r in rows] == ["created", "renamed", "member_added", "member_removed", "deleted"]
    assert all(r.target_type == "host_group" and r.target_id == gid for r in rows)
    assert rows[0].detail["name"] == "g1"
    assert rows[1].detail == {"change": "renamed", "from": "g1", "to": "g2"}
    assert rows[2].detail["host_id"] == host_id
    assert rows[4].detail["name"] == "g2"


@pytest.mark.asyncio
async def test_refused_or_failed_changes_write_no_audit_rows(client, db_session):
    token = await _bootstrap_owner(client)
    await client.post("/api/v1/hosts", json={"name": "Bad Name", "address": "1.1.1.1"}, headers=_auth_header(token))
    await client.delete("/api/v1/hosts/gibt-es-nicht", headers=_auth_header(token))
    await client.patch("/api/v1/hosts/gibt-es-nicht", json={"display_name": "x"}, headers=_auth_header(token))
    assert await _audit_rows(db_session) == []


# ---------------------------------------------------------------------------
# HostOut.credential / managed_tags, GET /hosts/{id}/known-hosts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_host_out_carries_the_default_credential_summary(client):
    token = await _bootstrap_owner(client)
    host_id = await _host_id(client, token)
    headers = _auth_header(token)

    bare = (await client.get(f"/api/v1/hosts/{host_id}", headers=headers)).json()
    assert bare["credential"] is None
    assert bare["managed_tags"] == []
    assert bare["enabled"] is True and bare["is_managed"] is True

    first = (
        await client.post(
            f"/api/v1/hosts/{host_id}/credentials",
            json={"kind": "ssh_key", "username": "lattice", "port": 2222, "secret_value": _new_key()},
            headers=headers,
        )
    ).json()
    expected = {"id": first["id"], "kind": "ssh_key", "username": "lattice", "port": 2222}
    assert (await client.get(f"/api/v1/hosts/{host_id}", headers=headers)).json()["credential"] == expected
    assert [h["credential"] for h in (await client.get("/api/v1/hosts", headers=headers)).json()] == [expected]

    # Ein zweiter, NICHT-Standard-Zugang aendert die Zusammenfassung nicht ...
    second = (
        await client.post(
            f"/api/v1/hosts/{host_id}/credentials",
            json={"kind": "ssh_password", "username": "root", "port": 22, "secret_value": "GEHEIM-WERT", "is_default": False},
            headers=headers,
        )
    ).json()
    shown = (await client.get(f"/api/v1/hosts/{host_id}", headers=headers)).json()
    assert shown["credential"] == expected
    # ... und nirgends steht ein Geheimnis.
    assert "GEHEIM-WERT" not in str(shown) and "secret" not in shown["credential"]

    # Als Standard angelegt, wechselt sie; nach dem Loeschen des Standards gibt es keinen mehr.
    third = (
        await client.post(
            f"/api/v1/hosts/{host_id}/credentials",
            json={"kind": "ssh_password", "username": "admin", "port": 22, "secret_value": "x"},
            headers=headers,
        )
    ).json()
    assert (await client.get(f"/api/v1/hosts/{host_id}", headers=headers)).json()["credential"]["id"] == third["id"]
    await client.delete(f"/api/v1/hosts/{host_id}/credentials/{third['id']}", headers=headers)
    assert (await client.get(f"/api/v1/hosts/{host_id}", headers=headers)).json()["credential"] is None
    assert second["id"]


@pytest.mark.asyncio
async def test_host_out_credential_is_fresh_in_create_and_patch_responses(client):
    token = await _bootstrap_owner(client)
    headers = _auth_header(token)
    created = await client.post("/api/v1/hosts", json={"name": "h", "address": "1.1.1.1"}, headers=headers)
    assert created.json()["credential"] is None
    host_id = created.json()["id"]
    await client.post(
        f"/api/v1/hosts/{host_id}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": 22, "secret_value": "x"},
        headers=headers,
    )
    patched = await client.patch(f"/api/v1/hosts/{host_id}", json={"display_name": "Neu"}, headers=headers)
    assert patched.json()["credential"]["username"] == "root"


@pytest.mark.asyncio
async def test_host_out_managed_tags_lists_extension_owned_tags(client, db_session):
    from nodvard_deck.models import HostTag

    token = await _bootstrap_owner(client)
    headers = _auth_header(token)
    host_id = (
        await client.post("/api/v1/hosts", json={"name": "h", "address": "1.1.1.1", "tags": ["docker"]}, headers=headers)
    ).json()["id"]
    db_session.add(HostTag(host_id=host_id, tag="proxmox", managed_by_ext_id="proxmox"))
    await db_session.commit()
    shown = (await client.get("/api/v1/hosts", headers=headers)).json()[0]
    assert sorted(shown["tags"]) == ["docker", "proxmox"]
    assert shown["managed_tags"] == ["proxmox"]


@pytest.mark.asyncio
async def test_known_hosts_endpoint_lists_pinned_keys_with_who_accepted(client, db_session):
    from nodvard_deck.models import KnownHostKey

    token = await _bootstrap_owner(client)
    headers = _auth_header(token)
    me = (await client.get("/api/v1/me", headers=headers)).json()
    host_id = await _host_id(client, token)
    assert (await client.get(f"/api/v1/hosts/{host_id}/known-hosts", headers=headers)).json() == []

    db_session.add(KnownHostKey(host_id=host_id, key_type="ssh-rsa", fingerprint="SHA256:rsa", accepted_by_user_id=me["id"]))
    db_session.add(KnownHostKey(host_id=host_id, key_type="ssh-ed25519", fingerprint="SHA256:ed"))  # automatisch (TOFU)
    db_session.add(KnownHostKey(host_id=host_id, key_type="ecdsa-sha2-nistp256", fingerprint="SHA256:ec", accepted_by_user_id="weg-1234"))
    await db_session.commit()

    resp = await client.get(f"/api/v1/hosts/{host_id}/known-hosts", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["key_type"] for r in rows] == ["ecdsa-sha2-nistp256", "ssh-ed25519", "ssh-rsa"]
    by_type = {r["key_type"]: r for r in rows}
    assert by_type["ssh-rsa"]["fingerprint"] == "SHA256:rsa"
    assert by_type["ssh-rsa"]["accepted_by_user_id"] == me["id"]
    assert by_type["ssh-rsa"]["accepted_by_label"] == me["username"]
    assert by_type["ssh-ed25519"]["accepted_by_user_id"] is None
    assert by_type["ssh-ed25519"]["accepted_by_label"] is None
    assert by_type["ecdsa-sha2-nistp256"]["accepted_by_label"] == "user/weg-1234"  # nicht mehr aufloesbar
    assert all(r["first_seen_at"] for r in rows)

    assert (await client.get("/api/v1/hosts/gibt-es-nicht/known-hosts", headers=headers)).status_code == 404
    assert (await client.get(f"/api/v1/hosts/{host_id}/known-hosts")).status_code == 401


@pytest.mark.asyncio
async def test_known_hosts_endpoint_hides_other_users_names_from_a_viewer(client, db_session):
    from nodvard_deck.core import security
    from nodvard_deck.models import KnownHostKey, User
    from nodvard_deck.services import auth as auth_service

    owner = await _bootstrap_owner(client)
    owner_id = (await client.get("/api/v1/me", headers=_auth_header(owner))).json()["id"]
    host_id = await _host_id(client, owner)
    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="viewer3", password_hash=security.hash_password("whatever123"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)
    db_session.add(KnownHostKey(host_id=host_id, key_type="ssh-rsa", fingerprint="SHA256:rsa", accepted_by_user_id=owner_id))
    await db_session.commit()
    token = (await client.post("/api/v1/auth/login", json={"username": "viewer3", "password": "whatever123"})).json()["access_token"]

    rows = (await client.get(f"/api/v1/hosts/{host_id}/known-hosts", headers=_auth_header(token))).json()
    assert rows[0]["accepted_by_label"] == f"user/{owner_id}"  # Betrachter sehen keine fremden Namen


@pytest.mark.asyncio
async def test_address_with_trailing_dot_is_accepted_without_the_dot(client):
    token = await _bootstrap_owner(client)
    resp = await client.post(
        "/api/v1/hosts", json={"name": "pve", "address": "PVE.fritz.box."}, headers=_auth_header(token)
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["address"] == "pve.fritz.box"


@pytest.mark.asyncio
async def test_patch_group_logs_name_and_description_change_together(client, db_session):
    token = await _bootstrap_owner(client)
    headers = _auth_header(token)
    gid = (await client.post("/api/v1/host-groups", json={"name": "a"}, headers=headers)).json()["id"]
    await client.patch(f"/api/v1/host-groups/{gid}", json={"name": "b", "description": "neu"}, headers=headers)
    rows = await _audit_rows(db_session, "host.group_changed")
    assert rows[-1].detail == {"change": "renamed", "from": "a", "to": "b", "description_changed": True}


@pytest.mark.asyncio
async def test_host_status_keeps_the_status_when_the_target_changed_meanwhile(client, monkeypatch):
    from nodvard_deck.core import ssh

    token = await _bootstrap_owner(client)
    headers = _auth_header(token)
    host_id = await _host_id(client, token)
    await client.post(
        f"/api/v1/hosts/{host_id}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": 22, "secret_value": "x"},
        headers=headers,
    )

    async def _changed(self, session, target, **kwargs):
        raise ssh.SshTargetChanged("Die Verbindungsdaten dieses Servers haben sich geändert.")

    monkeypatch.setattr(ssh.SshPool, "get", _changed)
    resp = await client.get(f"/api/v1/hosts/{host_id}/status", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown"  # der gespeicherte Status, nicht "down"
    assert resp.json()["checked_live"] is False
    assert "geändert" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_new_address_removes_stored_ssh_password_but_keeps_key(client, db_session):
    """Ein gespeichertes SSH-Passwort gehoert zu dem Server, bei dem es eingegeben wurde;
    unter einer neuen Adresse darf es nicht mehr stehen. Ein Schluessel bleibt."""
    from sqlalchemy import select

    from nodvard_deck.models import HostCredential, Secret

    token = await _bootstrap_owner(client)
    headers = _auth_header(token)
    host = (await client.post("/api/v1/hosts", json={"name": "pw-host", "address": "192.168.2.50"}, headers=headers)).json()
    pw = await client.post(
        f"/api/v1/hosts/{host['id']}/credentials",
        json={"kind": "ssh_password", "username": "root", "port": 22, "secret_value": "GEHEIM-WERT"},
        headers=headers,
    )
    assert pw.status_code == 201, pw.text
    key = await client.post(
        f"/api/v1/hosts/{host['id']}/credentials",
        json={"kind": "ssh_key", "username": "deck", "port": 22, "secret_value": _new_key(), "is_default": False},
        headers=headers,
    )
    assert key.status_code == 201, key.text

    # Gleiche Adresse (und nur ein anderer Anzeigename): nichts wird gelöscht.
    same = await client.patch(f"/api/v1/hosts/{host['id']}", json={"display_name": "Neu", "address": "192.168.2.50"}, headers=headers)
    assert same.status_code == 200, same.text
    kinds = (await db_session.execute(select(HostCredential.kind).where(HostCredential.host_id == host["id"]))).scalars().all()
    assert sorted(kinds) == ["ssh_key", "ssh_password"]

    moved = await client.patch(f"/api/v1/hosts/{host['id']}", json={"address": "192.168.2.51"}, headers=headers)
    assert moved.status_code == 200, moved.text
    kinds = (await db_session.execute(select(HostCredential.kind).where(HostCredential.host_id == host["id"]))).scalars().all()
    assert kinds == ["ssh_key"]
    left = (await db_session.execute(select(Secret.label).where(Secret.label.like(f"host-cred:{host['id']}:%")))).scalars().all()
    assert len(left) == 1  # nur der Schluessel

    updated = [r for r in await _audit_rows(db_session) if r.action == "host.updated"]
    assert updated[-1].detail["password_credentials_removed"] == 1
