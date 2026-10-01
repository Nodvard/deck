"""system-Extension: "Dienst neu starten" (system.service_restart).

Geprueft wird, was schiefgehen koennte: Namen mit Shell-Zeichen, die Sperrliste,
der genaue Befehl (auch in einer echten Shell), die Route (Vorschlag mit Risiko
MITTEL, nie Ausfuehrung) und der ganze Weg durch das echte Gate.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from nodvard_deck.ext.runtime import get_extension_runtime
from nodvard_deck.models import Action
from nodvard_deck.services import extensions as extensions_service
from nodvard_sdk import ActionRequest, Actor, GateDecision, GateOutcome, Host, Risk
from nodvard_sdk.actions import ActionStatus
from sqlalchemy import select

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "system" / "src"))

from nodvard_deck_ext_system import actions as sa  # noqa: E402

RESTART_OK = "@@rc=0\nactive\n"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


# ---------------------------------------------------------------------------
# Namen und Sperrliste
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unit", ["nginx.service", "docker.socket", "apt-daily.timer", "getty@tty1.service", "user@1000.service", "a_b.c:d-e.service", "X" * 128 + ".service"])
def test_normal_unit_names_are_accepted(unit):
    assert sa.validate_unit(unit) == unit


@pytest.mark.parametrize(
    "unit",
    [
        "nginx.service; rm -rf /",
        "nginx.service && reboot",
        "$(id).service",
        "`id`.service",
        "nginx.service\n",
        "nginx.service\nreboot.service",
        "a b.service",
        "-h.service",
        "--now.service",
        "nginx",
        "nginx.mount",
        "nginx.service.bak",
        "ng*.service",
        "ng?nx.service",
        "[a].service",
        "/etc/passwd.service",
        "../x.service",
        "ngïnx.service",
        ".service",
        "",
        "x" * 129 + ".service",
        None,
        42,
        ["nginx.service"],
    ],
)
def test_injection_and_odd_names_are_rejected(unit):
    with pytest.raises(sa.PayloadError):
        sa.validate_unit(unit)
    with pytest.raises(sa.PayloadError):
        sa.restart_command(unit)


@pytest.mark.parametrize(
    "payload",
    [
        None, [], "x", {}, {"unit": "nginx.service"}, {"host_id": "h1"}, {"host_id": 1, "unit": "nginx.service"},
        {"host_id": "", "unit": "nginx.service"}, {"host_id": " h1", "unit": "nginx.service"}, {"host_id": "h" * 65, "unit": "nginx.service"},
        {"host_id": "h1", "unit": "nginx.service", "command": "reboot"},
    ],
)
def test_payload_needs_exactly_host_and_unit(payload):
    with pytest.raises(sa.PayloadError):
        sa.validate_payload(payload)


def test_valid_payload():
    assert sa.validate_payload({"host_id": "h1", "unit": "nginx.service"}) == ("h1", "nginx.service")


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("sshd.service", sa.REFUSAL_SSH), ("ssh.service", sa.REFUSAL_SSH), ("ssh.socket", sa.REFUSAL_SSH), ("sshd@1.service", sa.REFUSAL_SSH),
        ("SSHD.service", sa.REFUSAL_SSH), ("sshd-keygen.service", sa.REFUSAL_SSH),
        ("systemd-journald.service", sa.REFUSAL_SYSTEM), ("systemd-logind.service", sa.REFUSAL_SYSTEM), ("systemd-resolved.socket", sa.REFUSAL_SYSTEM),
        ("dbus.service", sa.REFUSAL_SYSTEM), ("dbus.socket", sa.REFUSAL_SYSTEM), ("dbus-broker.service", sa.REFUSAL_SYSTEM),
        ("networking.service", sa.REFUSAL_NETWORK), ("NetworkManager.service", sa.REFUSAL_NETWORK), ("dhcpcd.service", sa.REFUSAL_NETWORK),
        ("lattice.service", sa.REFUSAL_DASHBOARD), ("lattice-backend.service", sa.REFUSAL_DASHBOARD),
        ("nodvard-deck.service", sa.REFUSAL_DASHBOARD), ("nodvard-deck-backend.service", sa.REFUSAL_DASHBOARD),
        ("Nodvard-Deck.service", sa.REFUSAL_DASHBOARD), ("nodvard.service", sa.REFUSAL_DASHBOARD),
    ],
)
def test_deny_list_holds_on_every_host(unit, expected):
    for dashboard in (True, False, None):
        assert sa.deny_reason(unit, dashboard_host=dashboard) == expected


@pytest.mark.parametrize("unit", ["docker.service", "docker.socket", "containerd.service", "docker-cleanup.timer"])
def test_docker_is_only_blocked_on_the_dashboard_host(unit):
    assert sa.deny_reason(unit, dashboard_host=True) == sa.REFUSAL_DASHBOARD_HOST
    assert sa.deny_reason(unit, dashboard_host=None) == sa.REFUSAL_UNKNOWN_HOST
    assert sa.deny_reason(unit, dashboard_host=False) is None


@pytest.mark.parametrize("unit", ["nginx.service", "cron.service", "pihole-FTL.service", "sshguard.service", "systemdfoo.service", "cloud-init.service", "getty@tty1.service"])
def test_normal_services_are_not_blocked(unit):
    assert sa.deny_reason(unit, dashboard_host=False) is None
    assert sa.needs_dashboard_check(unit) is False


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("nginx.service", True), ("docker.service", True), ("backup.timer", True),
        ("sshd.service", False), ("dbus.service", False), ("systemd-journald.service", False), ("lattice.service", False),
        ("nodvard-deck.service", False), ("nodvard.service", False), ("nodvard-link.service", True),
        ("mnt-daten.mount", False), ("user.slice", False), ("session-1.scope", False), ("-x.service", False), ("", False),
    ],
)
def test_is_restartable_is_a_static_check_by_name(unit, expected):
    assert sa.is_restartable(unit) is expected


# ---------------------------------------------------------------------------
# Befehl
# ---------------------------------------------------------------------------


def test_restart_command_is_exact():
    assert sa.restart_command("nginx.service") == 'systemctl restart -- nginx.service; echo "@@rc=$?"; systemctl is-active -- nginx.service'
    # Ein Name mit Sonderzeichen, die systemd erlaubt (@ : .), braucht keine Anfuehrungszeichen;
    # shlex.quote schuetzt trotzdem, falls die Regel je erweitert wird.
    assert shlex.quote("getty@tty1.service") == "getty@tty1.service"


def test_as_root_wraps_with_sudo_fallback():
    inner = sa.restart_command("nginx.service")
    wrapped = sa.as_root(inner)
    assert wrapped == (
        f"if [ \"$(id -u)\" -eq 0 ]; then sh -c {shlex.quote(inner)}; "
        f"elif sudo -n true 2>/dev/null; then sudo -n sh -c {shlex.quote(inner)}; "
        f"else echo '@@noroot'; exit 126; fi"
    )


def _run_in_fake_shell(tmp_path: Path, command: str, *, uid: str, sudo: bool, restart_rc: int = 0, state: str = "active") -> subprocess.CompletedProcess:
    """Der gebaute Befehl in einer ECHTEN sh, mit falschem systemctl/id/sudo im PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "systemctl").write_text(
        "#!/bin/sh\n"
        f"echo \"systemctl $*\" >> {log}\n"
        f"if [ \"$1\" = restart ]; then exit {restart_rc}; fi\n"
        f"if [ \"$1\" = is-active ]; then echo {state}; fi\n"
    )
    (bin_dir / "id").write_text(f"#!/bin/sh\necho {uid}\n")
    if sudo:
        (bin_dir / "sudo").write_text(f"#!/bin/sh\necho \"sudo $*\" >> {log}\nif [ \"$1\" = -n ] && [ \"$2\" = true ]; then exit 0; fi\nshift\nexec \"$@\"\n")
    else:
        (bin_dir / "sudo").write_text("#!/bin/sh\nexit 1\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    return subprocess.run(["sh", "-c", command], capture_output=True, text=True, env=env, check=False)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell (sh, ausführbare Fake-Programme)")
def test_command_runs_in_a_real_shell_as_root(tmp_path):
    res = _run_in_fake_shell(tmp_path, sa.as_root(sa.restart_command("nginx.service")), uid="0", sudo=False)
    assert sa.parse_restart_output(res.stdout) == (0, "active")
    assert (tmp_path / "calls.log").read_text().splitlines() == ["systemctl restart -- nginx.service", "systemctl is-active -- nginx.service"]


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell (sh, ausführbare Fake-Programme)")
def test_command_uses_sudo_when_not_root(tmp_path):
    res = _run_in_fake_shell(tmp_path, sa.as_root(sa.restart_command("nginx.service")), uid="1000", sudo=True)
    assert sa.parse_restart_output(res.stdout) == (0, "active")
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls[0] == "sudo -n true" and calls[1].startswith("sudo -n sh -c ")
    assert "systemctl restart -- nginx.service" in calls


def test_command_reports_missing_rights_without_running_systemctl(tmp_path):
    res = _run_in_fake_shell(tmp_path, sa.as_root(sa.restart_command("nginx.service")), uid="1000", sudo=False)
    assert res.stdout.strip() == "@@noroot" and res.returncode == 126
    assert not (tmp_path / "calls.log").exists()


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ("@@rc=0\nactive\n", (0, "active")), ("@@rc=5\nfailed\n", (5, "failed")), ("@@rc=0\n", (0, "")),
        ("", (None, "")), ("active\n@@rc=0\ninactive\n", (0, "inactive")),
    ],
)
def test_parse_restart_output(stdout, expected):
    assert sa.parse_restart_output(stdout) == expected


# ---------------------------------------------------------------------------
# Executor und Route mit falschem ctx
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, *, tags: list[str] | None = None, os_family: str = "linux") -> None:
        self.commands: list[str] = []
        self.proposals: list[ActionRequest] = []
        self.waits: list[float | None] = []
        self.replies: list[SimpleNamespace] = []  # nacheinander verbraucht; sonst RESTART_OK
        self.probe_reply: SimpleNamespace | Exception = SimpleNamespace(exit_code=0, stdout="@@other\n", stderr="", duration_ms=1)
        self._host = Host(id="h1", name="pi", display_name="Raspberry Pi", address="10.0.0.2", tags=tags or [], os_family=os_family)
        self.api = SimpleNamespace(current_actor=lambda: Actor.user("u1", "nico"))
        self.hosts = SimpleNamespace(get=self._get)
        self.exec = SimpleNamespace(run=self._run)
        self.actions = SimpleNamespace(propose=self._propose, result=self._result)

    async def _get(self, host_id):
        return self._host if host_id == "h1" else None

    async def _run(self, host, command, timeout_s=60):
        self.commands.append(command)
        if "docker inspect" in command and "@@own" in command:
            if isinstance(self.probe_reply, Exception):
                raise self.probe_reply
            return self.probe_reply
        if self.replies:
            return self.replies.pop(0)
        return SimpleNamespace(exit_code=0, stdout=RESTART_OK, stderr="", duration_ms=5)

    async def _propose(self, req: ActionRequest, wait_s=None) -> GateDecision:
        self.proposals.append(req)
        self.waits.append(wait_s)
        return GateDecision(action_id=f"a{len(self.proposals)}", outcome=GateOutcome.REQUIRE_CONFIRMATION, status=ActionStatus.PROPOSED, rule="test")

    async def _result(self, action_id):
        return None


def _req(unit="nginx.service", host_id="h1", host_ref="h1", **extra) -> ActionRequest:
    return ActionRequest(
        action_type=sa.SERVICE_RESTART, payload={"host_id": host_id, "unit": unit, **extra}, host_ref=host_ref,
        risk=Risk.MEDIUM, proposed_by=Actor.user("u1", "nico"), reason="Test",
    )


@pytest.mark.asyncio
async def test_executor_builds_the_exact_command_and_reports_the_state():
    ctx = _Ctx()
    result = await sa.SystemActionExecutor(ctx).execute(_req())
    assert ctx.commands == [sa.as_root(sa.restart_command("nginx.service"))]
    assert result.success is True
    assert result.output == "nginx.service auf Raspberry Pi neu gestartet – Zustand jetzt: active."
    assert result.detail == {"host_id": "h1", "unit": "nginx.service", "state": "active"}


@pytest.mark.asyncio
async def test_executor_ignores_a_command_smuggled_into_the_payload():
    ctx = _Ctx()
    result = await sa.SystemActionExecutor(ctx).execute(_req(command="rm -rf /"))
    assert result.success is False and "unerwartete Angaben (command)" in result.error
    assert ctx.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", ["nginx.service; reboot", "$(reboot).service", "-x.service", "nginx.service\n"])
async def test_executor_revalidates_the_unit(unit):
    ctx = _Ctx()
    result = await sa.SystemActionExecutor(ctx).execute(_req(unit=unit))
    assert result.success is False and "Ungültiger Dienstname" in result.error
    assert ctx.commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", ["sshd.service", "ssh.socket", "systemd-logind.service", "dbus.service", "lattice.service", "nodvard-deck.service"])
async def test_executor_refuses_the_deny_list(unit):
    ctx = _Ctx()
    result = await sa.SystemActionExecutor(ctx).execute(_req(unit=unit))
    assert result.success is False
    assert result.error == sa.deny_reason(unit, dashboard_host=False)
    assert ctx.commands == []


@pytest.mark.asyncio
async def test_executor_refuses_docker_on_the_dashboard_host_only():
    on_dashboard = _Ctx()
    on_dashboard.probe_reply = SimpleNamespace(exit_code=0, stdout="@@own\n", stderr="", duration_ms=1)
    refused = await sa.SystemActionExecutor(on_dashboard).execute(_req(unit="docker.service"))
    assert refused.success is False and refused.error == sa.REFUSAL_DASHBOARD_HOST
    assert not any("systemctl restart" in c for c in on_dashboard.commands)

    tagged = _Ctx(tags=["dashboard"])
    assert (await sa.SystemActionExecutor(tagged).execute(_req(unit="containerd.service"))).error == sa.REFUSAL_DASHBOARD_HOST
    assert tagged.commands == [], "der Tag reicht, ohne SSH-Abfrage"

    for tag in ("lattice", "nodvard-deck"):
        tagged_old_new = _Ctx(tags=[tag])
        assert (await sa.SystemActionExecutor(tagged_old_new).execute(_req(unit="docker.service"))).error == sa.REFUSAL_DASHBOARD_HOST, tag
        assert tagged_old_new.commands == [], f"der Tag {tag} reicht, ohne SSH-Abfrage"

    elsewhere = _Ctx()
    allowed = await sa.SystemActionExecutor(elsewhere).execute(_req(unit="docker.service"))
    assert allowed.success is True
    assert any("systemctl restart -- docker.service" in c for c in elsewhere.commands)

    unknown = _Ctx()
    unknown.probe_reply = RuntimeError("Zeitüberschreitung")
    assert (await sa.SystemActionExecutor(unknown).execute(_req(unit="docker.service"))).error == sa.REFUSAL_UNKNOWN_HOST
    assert not any("systemctl restart" in c for c in unknown.commands)


def test_container_probe_quotes_the_id():
    probe = sa.own_container_probe("ab12cd34ef56")
    assert "docker inspect --format '{{.Id}}' ab12cd34ef56" in probe and "sudo -n docker inspect" in probe
    assert shlex.quote("x; reboot") in sa.own_container_probe("x; reboot")


def _run_probe(tmp_path: Path, *, docker: str, sudo: str = "echo 'sudo: a password is required' >&2; exit 1") -> str:
    """Die Probe in einer echten Shell mit gefaelschtem `docker` und `sudo` auf dem PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", docker), ("sudo", sudo)):
        (bin_dir / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin_dir / name).chmod(0o755)
    out = subprocess.run(["/bin/sh", "-c", sa.own_container_probe("ab12cd34ef56")], capture_output=True, text=True,
                         env={"PATH": f"{bin_dir}:/usr/bin:/bin"}, timeout=30, check=False)
    return out.stdout.strip()


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize(("docker", "sudo", "expected"), [
    ("echo sha256:abc", None, "@@own"),  # laeuft direkt
    ("echo 'permission denied' >&2; exit 1", "echo sha256:abc", "@@own"),  # nur mit sudo lesbar
    ("echo 'Error: No such object: ab12cd34ef56' >&2; exit 1", None, "@@other"),  # Docker antwortet: dort nicht
    ("echo 'permission denied' >&2; exit 1", "echo 'Error: No such object: ab12cd34ef56' >&2; exit 1", "@@other"),
    # Nicht feststellbar: keine Rechte, Daemon weg (auch mit "no such file" im Text), kein docker-Programm
    ("echo 'permission denied while trying to connect to the docker API at unix:///var/run/docker.sock' >&2; exit 1", None, "@@unknown"),
    ("echo 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?' >&2; exit 1", None, "@@unknown"),
    ("echo 'dial unix /var/run/docker.sock: connect: no such file or directory' >&2; exit 1", None, "@@unknown"),
    ("exit 127", None, "@@unknown"),
])
def test_container_probe_tells_another_server_from_not_knowing(tmp_path, docker, sudo, expected):
    """Jeder Fehlschlag von `docker inspect` (keine Rechte, Daemon abgestuerzt) galt als
    "anderer Server" -- auf dem Server des Dashboards blieb der docker/containerd-Knopf sichtbar. Nur "gibt es nicht" heisst nein."""
    kwargs = {"sudo": sudo} if sudo is not None else {}
    assert _run_probe(tmp_path, docker=docker, **kwargs) == expected


@pytest.mark.asyncio
async def test_executor_needs_a_linux_host_that_exists_and_matches():
    ex = sa.SystemActionExecutor(_Ctx(os_family="windows"))
    assert "nur auf Linux-Servern" in (await ex.execute(_req())).error
    ex = sa.SystemActionExecutor(_Ctx())
    assert (await ex.execute(_req(host_id="weg", host_ref="weg"))).error == "Server nicht gefunden."
    assert "passt nicht" in (await ex.execute(_req(host_ref="anderer"))).error
    assert "Unbekannte Aktion" in (await ex.execute(ActionRequest(action_type="system.other", payload={}, proposed_by=Actor.user("u", "u"), reason="x"))).error


@pytest.mark.asyncio
async def test_executor_reports_failures_honestly():
    ctx = _Ctx()
    ctx.replies = [SimpleNamespace(exit_code=126, stdout="@@noroot\n", stderr="", duration_ms=1)]
    assert (await sa.SystemActionExecutor(ctx).execute(_req())).error == sa.NO_ROOT_MESSAGE

    ctx = _Ctx()
    ctx.replies = [SimpleNamespace(exit_code=5, stdout="@@rc=5\ninactive\n", stderr="Failed to restart nginx.service: Unit not found.\n", duration_ms=1)]
    failed = await sa.SystemActionExecutor(ctx).execute(_req())
    assert failed.success is False
    assert failed.error == "nginx.service auf Raspberry Pi ließ sich nicht neu starten: Failed to restart nginx.service: Unit not found."

    ctx = _Ctx()
    ctx.replies = [SimpleNamespace(exit_code=3, stdout="@@rc=0\nfailed\n", stderr="", duration_ms=1)]
    crashed = await sa.SystemActionExecutor(ctx).execute(_req())
    assert crashed.success is False and "gleich wieder ausgefallen" in crashed.error

    ctx = _Ctx()
    ctx.replies = [SimpleNamespace(exit_code=3, stdout="@@rc=0\ninactive\n", stderr="", duration_ms=1)]
    oneshot = await sa.SystemActionExecutor(ctx).execute(_req())
    assert oneshot.success is True and "Zustand jetzt: inactive" in oneshot.output and "einmal" in oneshot.output

    ctx = _Ctx()
    ctx.replies = [SimpleNamespace(exit_code=255, stdout="", stderr="", duration_ms=1)]
    silent = await sa.SystemActionExecutor(ctx).execute(_req())
    assert silent.success is False and "keine Antwort" in silent.error


@pytest.mark.asyncio
async def test_dry_run_only_describes():
    ctx = _Ctx()
    ex = sa.SystemActionExecutor(ctx)
    report = await ex.dry_run(_req())
    assert report is not None and report.would_change is True and report.summary == "Würde nginx.service auf Raspberry Pi neu starten."
    refused = await ex.dry_run(_req(unit="sshd.service"))
    assert refused is not None and refused.would_change is False and refused.summary == sa.REFUSAL_SSH
    assert ctx.commands == []


@pytest.fixture
async def route():
    ctx = _Ctx()
    app = FastAPI()
    app.include_router(sa.build_action_router(ctx))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sys") as client:
        yield SimpleNamespace(ctx=ctx, client=client)


@pytest.mark.asyncio
async def test_route_proposes_with_medium_risk_and_never_executes(route):
    res = await route.client.post("/hosts/h1/services/restart", json={"unit": "nginx.service"})
    assert res.status_code == 200, res.text
    assert res.json() == {"action_id": "a1", "status": "proposed", "risk": "medium", "detail": None}
    [proposal] = route.ctx.proposals
    assert (proposal.action_type, proposal.risk, proposal.host_ref) == ("system.service_restart", Risk.MEDIUM, "h1")
    assert proposal.payload == {"host_id": "h1", "unit": "nginx.service"}
    assert "nginx.service" in proposal.reason and "Raspberry Pi" in proposal.reason
    assert route.ctx.commands == []
    assert route.ctx.waits == [20.0]
    # Die Spalte im Kern fasst nur 36 Zeichen (strenge Datenbanken lehnen längere ab).
    assert len(proposal.correlation_id) <= 36


@pytest.mark.asyncio
async def test_route_rejects_bad_requests_before_proposing(route):
    post = route.client.post
    assert (await post("/hosts/weg/services/restart", json={"unit": "nginx.service"})).status_code == 404
    assert (await post("/hosts/h1/services/restart", json={"unit": "nginx.service; reboot"})).status_code == 422
    assert (await post("/hosts/h1/services/restart", json={"unit": "-x.service"})).status_code == 422
    assert (await post("/hosts/h1/services/restart", json={})).status_code == 422
    denied = await post("/hosts/h1/services/restart", json={"unit": "sshd.service"})
    assert denied.status_code == 409 and denied.json()["detail"] == sa.REFUSAL_SSH
    assert route.ctx.proposals == []

    route.ctx.probe_reply = SimpleNamespace(exit_code=0, stdout="@@own\n", stderr="", duration_ms=1)
    docker = await post("/hosts/h1/services/restart", json={"unit": "docker.service"})
    assert docker.status_code == 409 and docker.json()["detail"] == sa.REFUSAL_DASHBOARD_HOST
    assert route.ctx.proposals == []

    route.ctx.probe_reply = SimpleNamespace(exit_code=0, stdout="@@other\n", stderr="", duration_ms=1)
    assert (await post("/hosts/h1/services/restart", json={"unit": "docker.service"})).status_code == 200


@pytest.mark.asyncio
async def test_route_refuses_windows_hosts():
    ctx = _Ctx(os_family="windows")
    app = FastAPI()
    app.include_router(sa.build_action_router(ctx))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sys") as client:
        res = await client.post("/hosts/h1/services/restart", json={"unit": "nginx.service"})
    assert res.status_code == 400 and ctx.proposals == []


def test_action_spec_is_medium_and_not_offered_on_the_host_page():
    [spec] = sa.ACTION_SPECS
    assert (spec.action_type, spec.default_risk, spec.host_bound, spec.permissions) == ("system.service_restart", Risk.MEDIUM, False, ["hosts.execute"])


# ---------------------------------------------------------------------------
# Durch den echten Kern: Route -> Gate -> Freigabe -> Executor
# ---------------------------------------------------------------------------


async def _login(client, username="owner1", password="correct-horse-battery") -> dict:
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _enable(client, db_session, test_settings, monkeypatch) -> tuple[dict, dict, list[str]]:
    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    headers = await _login(client)
    enabled = await client.post("/api/v1/extensions/system/enable", headers=headers)
    assert enabled.status_code == 200, enabled.text
    host = (await client.post("/api/v1/hosts", json={"name": "pi", "address": "10.0.0.5"}, headers=headers)).json()
    commands: list[str] = []

    async def fake_run(host, command, timeout_s=60):
        commands.append(command)
        if "@@own" in command:
            return SimpleNamespace(exit_code=0, stdout="@@other\n", stderr="", duration_ms=1)
        return SimpleNamespace(exit_code=0, stdout=RESTART_OK, stderr="", duration_ms=1)

    monkeypatch.setattr(get_extension_runtime().loaded["system"].ctx.exec, "run", fake_run)
    return headers, host, commands


@pytest.mark.asyncio
async def test_restart_goes_through_the_gate_and_reaches_the_server(client, db_session, test_settings, monkeypatch):
    headers, host, commands = await _enable(client, db_session, test_settings, monkeypatch)
    url = f"/api/v1/ext/system/hosts/{host['id']}/services/restart"

    proposed = await client.post(url, json={"unit": "nginx.service"}, headers=headers)
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert (body["status"], body["risk"]) == ("proposed", "medium")
    assert commands == [], "vor der Freigabe passiert nichts"

    approved = await client.post(f"/api/v1/actions/{body['action_id']}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    done = approved.json()
    assert done["status"] == "succeeded", done
    assert done["result"]["output"].endswith("neu gestartet – Zustand jetzt: active.")
    assert done["payload"] == {"host_id": host["id"], "unit": "nginx.service"}
    assert commands == [sa.as_root(sa.restart_command("nginx.service"))]

    row = (await db_session.execute(select(Action))).scalars().one()
    assert row.action_type == "system.service_restart" and row.host_id == host["id"]
    assert row.correlation_id is not None and len(row.correlation_id) <= 36


@pytest.mark.asyncio
async def test_info_marks_only_restartable_units(client, db_session, test_settings, monkeypatch):
    headers, host, _commands = await _enable(client, db_session, test_settings, monkeypatch)

    async def fake_run(host, command, timeout_s=60):
        out = "@@os\nDebian\n@@failed\nnginx.service\nmnt-daten.mount\nsshd.service\n@@services\nnginx.service\ndbus.service\ncron.service\nsystemd-logind.service\n@@end\n"
        return SimpleNamespace(exit_code=0, stdout=out, stderr="", duration_ms=1)

    monkeypatch.setattr(get_extension_runtime().loaded["system"].ctx.exec, "run", fake_run)
    res = await client.get(f"/api/v1/ext/system/hosts/{host['id']}/info", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["failed_units"] == ["nginx.service", "mnt-daten.mount", "sshd.service"]
    assert body["restartable_units"] == ["nginx.service", "cron.service"]


INFO_WITH_DOCKER = (
    "@@os\nDebian\n@@failed\n@@services\nnginx.service\ndocker.service\ncontainerd.service\ncron.service\n@@end\n"
)


async def _info_units(client, headers, host_id: str, monkeypatch, *, probe) -> tuple[list[str], list[str]]:
    """`restartable_units` der Info-Route; `probe` ist die Antwort auf die Docker-Abfrage
    (Text oder Ausnahme). Zweiter Rueckgabewert: die Befehle, die auf dem Server liefen."""
    commands: list[str] = []

    async def fake_run(host, command, timeout_s=60):
        commands.append(command)
        if "@@own" in command:
            if isinstance(probe, Exception):
                raise probe
            return SimpleNamespace(exit_code=0, stdout=probe, stderr="", duration_ms=1)
        return SimpleNamespace(exit_code=0, stdout=INFO_WITH_DOCKER, stderr="", duration_ms=1)

    monkeypatch.setattr(get_extension_runtime().loaded["system"].ctx.exec, "run", fake_run)
    res = await client.get(f"/api/v1/ext/system/hosts/{host_id}/info", headers=headers)
    assert res.status_code == 200, res.text
    return sorted(res.json()["restartable_units"]), commands


@pytest.mark.asyncio
async def test_info_offers_no_docker_restart_where_the_dashboard_runs(client, db_session, test_settings, monkeypatch):
    """Auf dem Dashboard-Server gibt es fuer docker/containerd keinen
    Knopf -- sonst kaeme erst nach der Rueckfrage die Absage. Wie die Route: "unbekannt"
    zaehlt als gesperrt."""
    headers, host, _commands = await _enable(client, db_session, test_settings, monkeypatch)

    # Container mit unserer Kennung laeuft dort: kein Docker-Knopf, alles andere bleibt.
    units, _ = await _info_units(client, headers, host["id"], monkeypatch, probe="@@own\n")
    assert units == ["cron.service", "nginx.service"]
    # Anderer Server: Docker darf neu gestartet werden.
    units, _ = await _info_units(client, headers, host["id"], monkeypatch, probe="@@other\n")
    assert units == ["containerd.service", "cron.service", "docker.service", "nginx.service"]
    # Nicht feststellbar (Abfrage scheitert oder ohne Antwort): vorsichtshalber gesperrt.
    for probe in (RuntimeError("Zeitüberschreitung"), "", "@@unknown\n"):
        units, _ = await _info_units(client, headers, host["id"], monkeypatch, probe=probe)
        assert units == ["cron.service", "nginx.service"]

    # Der Tag "dashboard" reicht -- ohne zusaetzliche SSH-Abfrage.
    tagged = (await client.post("/api/v1/hosts", json={"name": "berry", "address": "10.0.0.6", "tags": ["dashboard"]}, headers=headers)).json()
    units, commands = await _info_units(client, headers, tagged["id"], monkeypatch, probe="@@other\n")
    assert units == ["cron.service", "nginx.service"]
    assert not any("@@own" in c for c in commands)


@pytest.mark.asyncio
async def test_info_only_asks_the_server_about_the_dashboard_when_docker_is_listed(client, db_session, test_settings, monkeypatch):
    headers, host, _commands = await _enable(client, db_session, test_settings, monkeypatch)

    async def fake_run(host, command, timeout_s=60):
        assert "@@own" not in command, "ohne docker-Einheit keine Docker-Abfrage"
        out = "@@os\nDebian\n@@failed\n@@services\nnginx.service\ncron.service\n@@end\n"
        return SimpleNamespace(exit_code=0, stdout=out, stderr="", duration_ms=1)

    monkeypatch.setattr(get_extension_runtime().loaded["system"].ctx.exec, "run", fake_run)
    res = await client.get(f"/api/v1/ext/system/hosts/{host['id']}/info", headers=headers)
    assert res.status_code == 200, res.text
    assert sorted(res.json()["restartable_units"]) == ["cron.service", "nginx.service"]


@pytest.mark.asyncio
async def test_route_and_gate_refuse_bad_names_and_viewers_cannot_propose(client, db_session, test_settings, monkeypatch):
    headers, host, commands = await _enable(client, db_session, test_settings, monkeypatch)
    url = f"/api/v1/ext/system/hosts/{host['id']}/services/restart"
    assert (await client.post(url, json={"unit": "nginx.service"})).status_code == 401
    assert (await client.post(url, json={"unit": "nginx.service; reboot"}, headers=headers)).status_code == 422
    assert (await client.post(url, json={"unit": "sshd.service"}, headers=headers)).status_code == 409

    roles = {r["name"]: r["id"] for r in (await client.get("/api/v1/roles", headers=headers)).json()}
    created = await client.post("/api/v1/users", json={"username": "gast", "password": "gast-passwort-1", "role_ids": [roles["viewer"]]}, headers=headers)
    assert created.status_code == 201, created.text
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "gast-passwort-1"})
    viewer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.post(url, json={"unit": "nginx.service"}, headers=viewer)).status_code == 403
    assert (await db_session.execute(select(Action))).scalars().all() == []
    assert commands == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"host_id": "HOST", "unit": "nginx.service; reboot"}, "Ungültiger Dienstname"),
        ({"host_id": "HOST", "unit": "sshd.service"}, "SSH-Dienst"),
        ({"host_id": "HOST", "unit": "nginx.service", "command": "reboot"}, "unerwartete Angaben (command)"),
        ({"unit": "nginx.service"}, "der Server fehlt"),
    ],
)
async def test_forged_payloads_fail_in_the_executor(client, db_session, test_settings, monkeypatch, payload, error):
    """Der Payload liegt zwischen Vorschlag und Ausfuehrung in der DB -- der Executor prueft ihn selbst noch einmal."""
    headers, host, commands = await _enable(client, db_session, test_settings, monkeypatch)
    ctx = get_extension_runtime().loaded["system"].ctx
    payload = {k: (host["id"] if v == "HOST" else v) for k, v in payload.items()}
    decision = await ctx.actions.propose(ActionRequest(
        action_type="system.service_restart", payload=payload, risk=Risk.MEDIUM, proposed_by=Actor.user("x", "x"), reason="Test",
    ))
    approved = await client.post(f"/api/v1/actions/{decision.action_id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "failed", approved.json()
    assert error in approved.json()["result"]["error"]
    assert commands == []
