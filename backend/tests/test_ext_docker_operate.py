"""Modus „Nur bedienen“: nur feste Befehle über `sudo -n docker-root`, alles andere abgelehnt, das Skript prüft selbst."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, docker_on, result  # noqa: F401 - Fixtures

SCRIPT = EXT_DIR / "root-helper" / "docker-root.sh"
HELPER = "/usr/local/lib/nodvard-deck/docker-root"
NAME, ID = "web-1", "a" * 64


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _m():
    from nodvard_deck_ext_docker import (
        after_check,
        commands,
        docker_details,
        image_apply,
        operate,
    )

    return SimpleNamespace(after_check=after_check, commands=commands, docker_details=docker_details, image_apply=image_apply,
                           operate=operate)


def _allowed(m):
    c = m.commands
    return [
        (c.ps_all(), ["ps"]),
        (c.stats(), ["stats"]),
        (c.port_bindings([ID, "b" * 12]), ["ports", ID, "b" * 12]),
        (c.inspect_container(NAME), ["inspect", NAME]),
        (c.container_id(NAME), ["id", NAME]),
        (c.container_full_id(NAME), ["id", NAME]),
        (c.container_id_state(NAME), ["id-state", NAME]),
        (m.after_check.cmd_state([NAME, ID]), ["state", NAME, ID]),
        (c.container_verb("start", NAME), ["start", NAME]),
        (c.container_verb("stop", NAME), ["stop", NAME]),
        (c.container_verb("restart", NAME), ["restart", NAME]),
        (c.logs(NAME, 200, follow=False), ["logs", "200", "-", "0", NAME]),
        (c.logs(NAME, 0, follow=True), ["logs", "0", "-", "1", NAME]),
        ("docker logs --tail 50 --timestamps --since 15m --follow web-1", ["logs", "50", "15m", "1", NAME]),
        ("docker logs --tail 50 --timestamps --since 2h web-1", ["logs", "50", "2h", "0", NAME]),
    ]


def test_every_allowed_command_has_its_helper_call():
    m = _m()
    for command, args in _allowed(m):
        assert m.operate.helper_args(command) == args, command
        assert m.operate.operate_command(command) == shlex.join(["sudo", "-n", HELPER, *args])


def test_everything_else_is_refused():
    m = _m()
    c = m.commands
    refused = [
        c.system_df(), c.system_df_verbose(), c.networks(), c.images(), c.container_verb("rm", NAME), c.compose_restart("shop"),
        c.prune("docker.prune_images"), c.shell(NAME), c.image_remove("sha256:" + "c" * 64), c.volume_remove("v"),
        c.network_remove("d" * 64), m.after_check.cmd_stack_running("shop"),
        "docker run --rm busybox:1.37", "docker ps -a --format '{{json .}}'", "docker logs --tail 5 --timestamps --details web-1",
        "docker inspect --format '{{.Config.Env}}' web-1", "sh -c 'docker ps'", "docker 'unterminated", "",
    ]
    for command in refused:
        assert m.operate.operate_command(command) is None, command


def test_the_script_knows_the_same_formats():
    m = _m()
    text = SCRIPT.read_text(encoding="ascii")

    def value(name):
        return re.search(rf"^{name}='(.*)'$", text, re.MULTILINE).group(1)

    assert value("PS_FORMAT") == m.commands.PS_FORMAT
    assert value("STATS_FORMAT") == m.commands.STATS_FORMAT
    assert value("PORTS_FORMAT") == m.commands.PORTS_FORMAT
    assert value("STATE_FORMAT") == m.image_apply.VERIFY_FORMAT
    assert value("DETAILS_FORMAT") == m.docker_details.DETAILS_FORMAT
    assert f"MAX_TAIL={m.commands.MAX_LOG_TAIL}" in text


def _script_with_fake_docker(tmp_path):
    """Eine Kopie des Skripts, die statt Docker eine Attrappe findet; die schreibt ihre Argumente je Zeile in `calls`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > {tmp_path / "calls"}\n', encoding="ascii")
    fake.chmod(0o755)
    copy = tmp_path / "docker-root.sh"
    copy.write_text(SCRIPT.read_text(encoding="ascii").replace("PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={bin_dir}:/usr/bin:/bin"),
                    encoding="ascii")
    return copy


def _shells():
    shells = [path for name in ("sh", "dash") if (path := shutil.which(name))]
    if not shells:
        pytest.skip("braucht eine POSIX-Shell")
    return shells


@pytest.mark.parametrize("shell", ["sh", "dash"])
def test_the_script_runs_exactly_the_original_command(tmp_path, shell):
    if shell not in [p.rsplit("/", 1)[-1] for p in _shells()]:
        pytest.skip(f"{shell} fehlt")
    m = _m()
    copy = _script_with_fake_docker(tmp_path)
    for command, args in _allowed(m):
        if command == m.commands.container_id(NAME):
            # Das Skript fragt immer nur nach Containern, nie nach gleichnamigen Images oder Netzen.
            command = m.commands.container_full_id(NAME)
        if command == m.commands.inspect_container(NAME):
            command = shlex.join(["docker", "inspect", "--type", "container", "--format", m.docker_details.DETAILS_FORMAT, NAME])
        (tmp_path / "calls").unlink(missing_ok=True)
        done = subprocess.run([shell, str(copy), *args], capture_output=True, text=True, timeout=30, check=False)
        assert done.returncode == 0, (args, done.stderr)
        called = (tmp_path / "calls").read_text().splitlines()
        assert [w for w in called if w != "--"] == shlex.split(command)[1:], command


@pytest.mark.parametrize("args", [
    ["ps", "x"], ["stats", "--format", "{{json .}}"], ["ports"], ["ports", "nicht-hex-123"], ["ports", "abc"], ["inspect"],
    ["inspect", "-f"], ["inspect", "a", "b"], ["inspect", "--format={{.Config.Env}}"], ["inspect", "a;b"], ["inspect", "x" * 256],
    ["id", "../x"], ["state"], ["state", "-v"], ["logs", "1", "-", "0"], ["logs", "5001", "-", "0", "web"], ["logs", "-1", "-", "0", "web"],
    ["logs", "10", "1d", "0", "web"], ["logs", "10", "0m", "0", "web"], ["logs", "10", "-", "2", "web"], ["logs", "10", "-", "0", "--details"],
    ["start"], ["start", "a", "b"], ["rm", "web"], ["kill", "web"], ["exec", "web", "sh"], ["run", "busybox"], ["start", "-a"],
    ["start", "web\nrm"], ["logs", "10", "15m\n", "0", "web"], ["ps\n"], ["version", "x"], ["logs", "10", "1a2m", "0", "web"],
    ["logs", "10", "10000m", "0", "web"], ["logs", "10", "5mh", "0", "web"], ["logs", "10", "-5m", "0", "web"], ["logs", "10", "m", "0", "web"],
    ["state", *["web"] * 201], ["ports", *["a" * 12] * 201],
])
def test_the_script_refuses_and_runs_nothing(tmp_path, args):
    copy = _script_with_fake_docker(tmp_path)
    for shell in _shells():
        done = subprocess.run([shell, str(copy), *args], capture_output=True, text=True, timeout=30, check=False)
        assert done.returncode == 64 and done.stdout == "" and done.stderr.startswith("docker-root: "), (shell, args, done.stderr)
        assert not (tmp_path / "calls").exists(), (shell, args)


class _Inner:
    def __init__(self):
        self.calls = []

    async def run(self, host, command, **kwargs):
        self.calls.append(("run", command))
        return result("ok\n")

    async def run_argv(self, host, argv, **kwargs):
        self.calls.append(("run_argv", list(argv)))
        return result("ok\n")

    def stream(self, host, command):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def opened():
            self.calls.append(("stream", command))
            yield iter([b"x"])

        return opened()

    def sftp(self, host):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def opened():
            self.calls.append(("sftp", None))
            yield object()

        return opened()

    def open_shell(self, host, **kwargs):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def opened():
            self.calls.append(("shell", kwargs.get("command")))
            yield object()

        return opened()


async def _yes(host):
    return True


async def _no(host):
    return False


@pytest.mark.asyncio
async def test_the_exec_wrapper_maps_or_refuses():
    m = _m()
    inner = _Inner()
    wrapped = m.operate.OperateExec(inner, _yes)
    host = SimpleNamespace(id="h1")
    assert (await wrapped.run(host, m.commands.ps_all(), timeout_s=10)).stdout == "ok\n"
    assert inner.calls == [("run", f"sudo -n {HELPER} ps")]
    refused = await wrapped.run(host, m.commands.system_df())
    assert (refused.exit_code, refused.stderr) == (126, m.operate.REFUSED) and len(inner.calls) == 1
    assert (await wrapped.run_argv(host, ["docker", "compose", "up"])).exit_code == 126 and len(inner.calls) == 1
    async with wrapped.stream(host, m.commands.logs(NAME, 10, follow=True)):
        pass
    assert inner.calls[-1] == ("stream", f"sudo -n {HELPER} logs 10 - 1 {NAME}")
    with pytest.raises(PermissionError):
        async with wrapped.stream(host, "docker events"):
            pass
    with pytest.raises(PermissionError):
        async with wrapped.open_shell(host, cols=80, rows=24, command=m.commands.shell(NAME)):
            pass
    with pytest.raises(PermissionError):
        async with wrapped.sftp(host):
            pass
    with pytest.raises(PermissionError):
        async with wrapped.stream_argv(host, ["docker", "events"]):
            pass
    assert [c[0] for c in inner.calls] == ["run", "stream"]
    assert not hasattr(wrapped, "something_new")
    plain = m.operate.OperateExec(inner, _no)
    await plain.run(host, m.commands.system_df())
    await plain.run_argv(host, ["docker", "compose", "up"])
    async with plain.sftp(host):
        pass
    assert inner.calls[-3:] == [("run", m.commands.system_df()), ("run_argv", ["docker", "compose", "up"]), ("sftp", None)]


def test_the_details_never_ask_docker_for_values_or_commands():
    details = _m().docker_details.DETAILS_FORMAT
    for field in (".Config.Cmd", ".Config.Entrypoint", ".Args", ".Path", ".HostConfig.Binds", ".State.Health.Log", "json .Config.Labels",
                  "json .Config}", "json .}"):
        assert field not in details
    assert details.count(".Config.Env") == 1 and '{{range $i, $e := .Config.Env}}{{if $i}},{{end}}{{json (index (split . "=") 0)}}{{end}}' in details


def test_the_refusal_names_the_step_that_changes_the_mode():
    m = _m()
    text = m.operate.refused_text("voll")
    assert "Einstellungen, Server & Zugänge die Markierung „voll“" in text and "voller Docker-Server" in text and "Gruppe docker" in text
    assert m.operate.REFUSED == m.operate.refused_text("docker") and "Markierung „docker“" in m.operate.REFUSED


@pytest.mark.asyncio
async def test_the_exec_wrapper_names_the_configured_mark():
    m = _m()

    async def full_tag():
        return "voll"

    wrapped = m.operate.OperateExec(_Inner(), _yes, full_tag)
    host = SimpleNamespace(id="h1")
    assert (await wrapped.run_argv(host, ["docker", "compose", "up"])).stderr == m.operate.refused_text("voll")
    assert (await wrapped.run(host, m.commands.system_df())).stderr == m.operate.refused_text("voll")
    with pytest.raises(PermissionError, match="Markierung „voll“"):
        async with wrapped.sftp(host):
            pass


def test_which_servers_may_do_more_than_operate():
    m = _m()
    settings = {}
    assert m.operate.not_full_host(SimpleNamespace(tags=["docker"]), settings) is None
    assert m.operate.not_full_host(SimpleNamespace(tags=["docker", "docker-bedienen"]), settings) is None
    assert m.operate.not_full_host(SimpleNamespace(tags=["nas"]), settings) == "Kein Docker-Host."
    assert m.operate.not_full_host(SimpleNamespace(tags=[]), settings, "Markierung fehlt.") == "Markierung fehlt."
    assert m.operate.not_full_host(SimpleNamespace(tags=["docker-bedienen"]), settings, "Markierung fehlt.") == m.operate.REFUSED
    custom = {"docker_host_tag": "voll", "docker_operate_tag": "lesen"}
    assert m.operate.not_full_host(SimpleNamespace(tags=["lesen"]), custom) == m.operate.refused_text("voll")
    assert m.operate.not_full_host(SimpleNamespace(tags=["voll"]), custom) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stderr", [
    "sudo: a password is required\n", "Sorry, user deck is not allowed to execute '/usr/local/lib/nodvard-deck/docker-root ps' as root\n",
    "sudo: /usr/local/lib/nodvard-deck/docker-root: command not found\n", "bash: line 1: sudo: command not found\n",
])
async def test_a_missing_helper_is_explained(stderr):
    m = _m()

    class Failing(_Inner):
        async def run(self, host, command, **kwargs):
            return result(stderr=stderr, code=1)

    done = await m.operate.OperateExec(Failing(), _yes).run(SimpleNamespace(id="h1"), m.commands.ps_all())
    assert (done.exit_code, done.stderr) == (1, m.operate.MISSING)


@pytest.mark.asyncio
async def test_errors_of_docker_itself_stay_as_they_are():
    m = _m()

    class Failing(_Inner):
        async def run(self, host, command, **kwargs):
            return result(stderr="Error response from daemon: No such container: web\n", code=1)

    done = await m.operate.OperateExec(Failing(), _yes).run(SimpleNamespace(id="h1"), m.commands.container_verb("stop", "web"))
    assert done.stderr == "Error response from daemon: No such container: web\n"


def test_which_hosts_are_in_which_mode():
    m = _m()
    settings = {}
    assert m.operate.is_operate_host(SimpleNamespace(tags=["docker-bedienen"]), settings) is True
    assert m.operate.is_operate_host(SimpleNamespace(tags=["docker", "docker-bedienen"]), settings) is False
    assert m.operate.is_operate_host(SimpleNamespace(tags=["docker"]), settings) is False
    assert m.operate.is_docker_host(SimpleNamespace(tags=["docker-bedienen"]), settings) is True
    assert m.operate.is_docker_host(SimpleNamespace(tags=["nas"]), settings) is False
    custom = {"docker_host_tag": "voll", "docker_operate_tag": "lesen"}
    assert m.operate.is_operate_host(SimpleNamespace(tags=["lesen"]), custom) is True
    assert m.operate.is_docker_host(SimpleNamespace(tags=["docker"]), custom) is False


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operate_host_runs_only_the_helper(client, db_session, docker_on):
    from nodvard_deck.services import hosts as hosts_service

    host = await hosts_service.create_host(db_session, name="nur-lesen", address="10.0.0.7", tags=["docker-bedienen"])
    await db_session.commit()
    listed = await client.get("/api/v1/ext/docker/hosts", headers=docker_on.owner)
    modes = {h["name"]: h["mode"] for h in listed.json()["hosts"]}
    assert modes == {"pi-host": "full", "nur-lesen": "operate"}
    docker_on.exec.calls.clear()
    await client.get("/api/v1/ext/docker/widgets/matrix", headers=docker_on.owner)
    assert f"sudo -n {HELPER} ps" in docker_on.exec.calls and "docker ps -a --format" in " ".join(docker_on.exec.calls)
    stacks = await client.get(f"/api/v1/ext/docker/hosts/{host.id}/stacks", headers=docker_on.owner)
    assert "Nur bedienen" in stacks.text
    assert not any(c.startswith("docker ") for c in docker_on.exec.calls if HELPER not in c and "10.0.0.7" in c)
    shell = await client.post(f"/api/v1/hosts/{host.id}/actions/container.remove",
                              json={"payload": {"container": "web"}, "reason": "Test"}, headers=docker_on.owner)
    assert shell.status_code in (400, 404, 422), "Löschen gibt es im Modus „Nur bedienen“ nicht"
    started = await client.post("/api/v1/ext/docker/image-updates/check", headers=docker_on.owner)
    assert started.status_code == 202 and set(started.json()["hosts"]) == {docker_on.host}


@pytest.mark.asyncio
async def test_setting_up_an_operate_host_allows_only_the_helper(client, db_session, docker_on):
    from nodvard_deck.services import hosts as hosts_service

    host = await hosts_service.create_host(db_session, name="nur-lesen", address="10.0.0.7", tags=["docker-bedienen"])
    await db_session.commit()
    generated = await client.post(f"/api/v1/hosts/{host.id}/credentials/generate-key", json={"username": "deck"},
                                  headers=docker_on.owner)
    assert generated.status_code == 201, generated.text
    url = f"/api/v1/hosts/{host.id}/credentials/{generated.json()['credential']['id']}/setup"
    narrow = (await client.get(url, params={"sudo": "true", "groups": "docker"}, headers=docker_on.owner)).json()
    assert (narrow["sudo"], narrow["full_sudo"], narrow["groups"]) == (True, False, [])
    assert [h["ext_id"] for h in narrow["helpers"]] == ["docker"]
    assert "NOPASSWD: ALL" not in narrow["script"]
    assert "/etc/sudoers.d/nodvard_docker_$U" in narrow["script"]
    assert any("„Volle Rechte“ hilft bei docker-root nicht" in note for note in narrow["notes"])
    assert "hilft hier nicht: Docker arbeitet nur über sein Hilfsskript." in narrow["script"]
    full = (await client.get(url, params={"sudo": "true", "full_sudo": "true"}, headers=docker_on.owner)).json()
    assert full["full_sudo"] is True and "NOPASSWD: ALL" in full["script"]
    assert any("Ausnahme: docker-root." in note for note in full["notes"])


@pytest.mark.asyncio
async def test_a_host_with_both_tags_is_a_full_docker_host(client, db_session, docker_on):
    from nodvard_deck.services import hosts as hosts_service

    host = await hosts_service.create_host(db_session, name="beides", address="10.0.0.8", tags=["docker", "docker-bedienen"])
    await db_session.commit()
    listed = await client.get(f"/api/v1/hosts/{host.id}/requirements", headers=docker_on.owner)
    ids = [item["id"] for item in listed.json() if item["ext_id"] == "docker"]
    assert "docker-group" in ids and not any(i.startswith("docker-operate") for i in ids), ids
    generated = await client.post(f"/api/v1/hosts/{host.id}/credentials/generate-key", json={"username": "deck"},
                                  headers=docker_on.owner)
    url = f"/api/v1/hosts/{host.id}/credentials/{generated.json()['credential']['id']}/setup"
    setup = (await client.get(url, params={"sudo": "true", "groups": "docker"}, headers=docker_on.owner)).json()
    assert setup["helpers"] == [] and setup["groups"] == ["docker"]


@pytest.mark.asyncio
async def test_own_tag_names_decide_which_host_is_full(client, db_session, docker_on):
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.services import hosts as hosts_service

    await get_extension_runtime().loaded["docker"].ctx.settings.set({"docker_host_tag": "voll", "docker_operate_tag": "lesen"})
    both = await hosts_service.create_host(db_session, name="beides", address="10.0.0.8", tags=["voll", "lesen"])
    operate = await hosts_service.create_host(db_session, name="lesen", address="10.0.0.9", tags=["lesen", "docker"])
    await db_session.commit()

    async def ids(host) -> list[str]:
        listed = await client.get(f"/api/v1/hosts/{host.id}/requirements", headers=docker_on.owner)
        return [item["id"] for item in listed.json() if item["ext_id"] == "docker"]

    assert "docker-group" in await ids(both) and not any(i.startswith("docker-operate") for i in await ids(both))
    assert "docker-operate" in await ids(operate) and "docker-group" not in await ids(operate)


def _with_fakes(tmp_path, groups="deck adm", docker_ok=False, sudo_lines=(), uid=1000, rootless=False, env=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # `rootless`: Nur ein eigenes Docker des Zugangs antwortet, nicht das unter /var/run/docker.sock.
    fakes = {
        "id": f'#!/bin/sh\ncase "$1" in -u) echo {uid} ;; -un) echo deck ;; -nG) echo "{groups}" ;; esac\n',
        "docker": (f'#!/bin/sh\n[ -n "$DOCKER_TLS_VERIFY$DOCKER_API_VERSION" ] && exit 1\n'
                   f'[ "$1 $2" = "-H unix:///var/run/docker.sock" ] && exit {0 if docker_ok else 1}\n'
                   f"exit {0 if docker_ok or rootless else 1}\n"),
        "sudo": "#!/bin/sh\necho 'User deck may run the following commands on pi:'\n"
                + "".join(f"echo '    {line}'\n" for line in sudo_lines),
    }
    for name, text in fakes.items():
        (bin_dir / name).write_text(text, encoding="utf-8")
        (bin_dir / name).chmod(0o755)

    def run(check):
        return subprocess.run(["sh", "-c", check], capture_output=True, text=True, timeout=30, check=False,
                              env={"PATH": f"{bin_dir}:/usr/bin:/bin", **(env or {})}).returncode

    return run


@pytest.mark.parametrize(("groups", "docker_ok", "rootless", "env", "code"), [
    ("deck adm", False, False, None, 0), ("deck docker", False, False, None, 1), ("deck", True, False, None, 1),
    ("deck docker-users", False, False, None, 0), ("deck", False, True, None, 0),
    ("deck", True, False, {"DOCKER_TLS_VERIFY": "1", "DOCKER_API_VERSION": "1.12"}, 1),
], ids=["ohne", "gruppe", "anderer-weg", "aehnlicher-name", "rootless", "docker-variablen"])
def test_the_group_check_finds_docker_without_sudo(tmp_path, groups, docker_ok, rootless, env, code):
    if shutil.which("sh") is None:
        pytest.skip("braucht eine POSIX-Shell")
    run = _with_fakes(tmp_path, groups=groups, docker_ok=docker_ok, rootless=rootless, env=env)
    assert run(_m().operate.GROUP_CHECK) == code


@pytest.mark.parametrize(("uid", "codes"), [(0, (1, 0, 0)), (1000, (0, 1, 1))], ids=["root", "eigener-benutzer"])
def test_root_gets_one_hint_of_its_own(tmp_path, uid, codes):
    if shutil.which("sh") is None:
        pytest.skip("braucht eine POSIX-Shell")
    run = _with_fakes(tmp_path, groups="root docker", docker_ok=True, sudo_lines=["(ALL : ALL) NOPASSWD: ALL"], uid=uid)
    operate = _m().operate
    assert tuple(run(check) for check in (operate.ROOT_CHECK, operate.GROUP_CHECK, operate.FULL_SUDO_CHECK)) == codes


@pytest.mark.parametrize("lines", [
    [], [f"(root) NOPASSWD: {HELPER}"], ["(ALL : ALL) NOPASSWD: ALL"], ["(root) NOPASSWD: ALL", f"(root) NOPASSWD: {HELPER}"],
    ["(ALL) ALL"], ["(root) NOPASSWD: SETENV: ALL"], ["(root) NOPASSWD: /bin/ls, ALL"], ["(root) NOPASSWD: ALL, /bin/ls"],
    ["(rootless) NOPASSWD: ALL"],
], ids=["keine", "nur-skript", "voll", "voll-und-skript", "mit-passwort", "mit-setenv", "liste", "liste-mit-all-vorn", "anderer-name"])
def test_the_sudo_check_sees_the_full_rule_like_the_core(tmp_path, lines):
    from nodvard_deck.services.helper_hints import has_full_rule

    if shutil.which("sh") is None:
        pytest.skip("braucht eine POSIX-Shell")
    assert _with_fakes(tmp_path, sudo_lines=lines)(_m().operate.FULL_SUDO_CHECK) == (1 if has_full_rule(lines) else 0)


def test_without_docker_sudo_and_id_the_checks_pass(tmp_path):
    tools = {name: shutil.which(name) for name in ("sh", "tr", "grep")}
    if None in tools.values():
        pytest.skip("braucht sh, tr und grep")
    only = tmp_path / "nur"
    only.mkdir()
    for name, path in tools.items():
        (only / name).symlink_to(path)
    m = _m()
    for check in (m.operate.ROOT_CHECK, m.operate.GROUP_CHECK, m.operate.FULL_SUDO_CHECK):
        done = subprocess.run([str(only / "sh"), "-c", check], capture_output=True, text=True, timeout=30, check=False,
                              env={"PATH": str(only)})
        assert done.returncode == 0, (check, done.stderr)


@pytest.mark.asyncio
async def test_an_operate_host_is_checked_for_leftover_root(client, docker_on):
    from nodvard_deck.ext.runtime import get_extension_runtime

    m = _m()
    specs = {spec.id: spec for ext_id, spec in get_extension_runtime().ui.all_host_requirements() if ext_id == "docker"}
    root, group, sudo = specs["docker-operate-root"], specs["docker-operate-group"], specs["docker-operate-sudo"]
    assert root.tags == group.tags == sudo.tags == ["docker-bedienen"]
    assert (root.check_command, group.check_command, sudo.check_command) == (
        m.operate.ROOT_CHECK, m.operate.GROUP_CHECK, m.operate.FULL_SUDO_CHECK)
    assert [spec.label for spec in sorted((sudo, group, root), key=lambda spec: spec.order)] == [
        "Nur bedienen: Zugang ist nicht root", "Nur bedienen: Zugang nicht in der Gruppe docker", "Nur bedienen: keine volle sudo-Regel"]
    assert "sudo gpasswd -d deck docker" in group.fail_hint.replace("{user}", "deck")
    assert "„Verbindung prüfen“ meldet sich dafür neu an" in group.fail_hint
    assert "eigenen Benutzer statt root" in root.fail_hint and "gpasswd" not in root.fail_hint
    assert all("oder er ist root (dann gilt „Zugang ist nicht root“)" in spec.ok_text for spec in (group, sudo))
    assert all(spec.skip_tags == ["docker"] for spec in (root, group, sudo, specs["docker-operate"]))
    assert not any("Markierung „docker“" in spec.fail_hint for spec in (root, group, sudo))
    assert not any(spec.needs_root or spec.unix_group for spec in (root, group, sudo)), "die Einrichtung bleibt, wie sie ist"


@pytest.mark.asyncio
async def test_stopping_on_an_operate_host_goes_through_the_gate_and_the_helper(client, db_session, docker_on, monkeypatch):
    from nodvard_deck.services import hosts as hosts_service

    host = await hosts_service.create_host(db_session, name="nur-lesen", address="10.0.0.7", tags=["docker-bedienen"])
    await db_session.commit()
    calls = []

    async def run(target, command, timeout_s=60):
        calls.append((target.name, command))
        return result()

    monkeypatch.setattr(docker_on.handle, "run", run)
    proposed = await client.post(f"/api/v1/hosts/{host.id}/actions/container.stop",
                                 json={"payload": {"container": "web"}, "reason": "Test"}, headers=docker_on.owner)
    assert proposed.status_code == 202, proposed.text
    approved = await client.post(f"/api/v1/actions/{proposed.json()['id']}/approve", headers=docker_on.owner)
    assert approved.json()["status"] == "succeeded", approved.text
    # Erst die ID (Nodvard Deck stoppt sich nicht selbst), dann der Befehl, beides über das Hilfsskript.
    assert calls == [("nur-lesen", f"sudo -n {HELPER} id web"), ("nur-lesen", f"sudo -n {HELPER} stop web")]


@pytest.mark.asyncio
async def test_measuring_leaves_out_what_the_helper_cannot_tell(client, db_session, docker_on, monkeypatch):
    from nodvard_deck.services import hosts as hosts_service

    host = await hosts_service.create_host(db_session, name="nur-lesen", address="10.0.0.7", tags=["docker-bedienen"])
    await db_session.commit()
    calls = []

    async def run(target, command, timeout_s=60):
        calls.append((target.name, command))
        if "stats" in command:
            return result("web|1.50%|1MiB / 4GiB|0.03%\n")
        return result("/web|3\n" if "RestartCount" in command else "")

    monkeypatch.setattr(docker_on.handle, "run", run)
    measured = await client.get("/api/v1/ext/docker/stats", headers=docker_on.owner)
    assert measured.status_code == 200, measured.text
    body = measured.json()
    assert set(body["data"]) == {f"{docker_on.host}:web", f"{host.id}:web"}
    assert list(body["restarts"]) == [f"{docker_on.host}:web"]
    assert [command for name, command in calls if name == "nur-lesen"] == [f"sudo -n {HELPER} stats"]


# Was auf einem Server im Modus „Nur bedienen“ geht; jede andere Route mit `{host_id}` antwortet 409 mit Grund.
OPERATE_ROUTES = {
    "GET /containers/{host_id}/{container}/inspect",
    "GET /containers/{host_id}/{container}/logs",
    "PUT /containers/{host_id}/{container}/url",
}

# Minimal gültige Bodies der übrigen Routen mit Pflichtfeldern: Mit `{}` kämen sie nie bis zur Sperre ihres Handlers.
BODIES = {
    "POST /containers/{host_id}/{container}/image-update": {"plan_id": "0" * 16},
    "POST /hosts/{host_id}/stacks/{project}/adopt": {"working_dir": "/opt/stacks/shop", "config_files": ["/opt/stacks/shop/compose.yaml"]},
    "POST /hosts/{host_id}/stacks/{project}/check": {"path": "/opt/stacks/shop/compose.yaml", "content": "services: {}\n"},
    "POST /hosts/{host_id}/stacks/{project}/save": {
        "path": "/opt/stacks/shop/compose.yaml", "content": "services: {}\n", "base_sha256": "0" * 64,
    },
    "POST /hosts/{host_id}/new-stack/check": {"name": "neu", "content": "services: {}\n"},
    "POST /hosts/{host_id}/new-stack": {"name": "neu", "content": "services: {}\n"},
    "POST /hosts/{host_id}/stacks/{project}/update": {"services": ["web"]},
    "POST /hosts/{host_id}/stacks/{project}/rollback": {"services": ["web"]},
    "POST /hosts/{host_id}/stacks/{project}/backups/{stamp}/restore": {"volumes": ["data"]},
    "PUT /hosts/{host_id}/stacks/{project}/git": {"url": "https://github.com/team/stacks"},
    "POST /hosts/{host_id}/stacks/{project}/git/apply": {"commit": "0" * 40},
    "POST /hosts/{host_id}/stacks/{project}/git/rollback": {"previous": "0" * 32},
    "POST /hosts/{host_id}/stacks/{project}/git/restart": {"services": ["web"]},
}


def _routes(routes):
    for route in routes:
        if hasattr(route, "original_router"):
            yield from _routes(route.original_router.routes)
        else:
            yield route


@pytest.mark.asyncio
async def test_every_other_host_route_says_why_on_an_operate_host(client, db_session, docker_on, monkeypatch):
    from contextlib import asynccontextmanager

    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.services import hosts as hosts_service

    host = await hosts_service.create_host(db_session, name="nur-lesen", address="10.0.0.7", tags=["docker-bedienen"])
    await db_session.commit()
    sent = []

    async def run(target, command, timeout_s=60):
        sent.append(("run", target.name))
        return result()

    async def run_argv(target, argv, **kwargs):
        sent.append(("run_argv", target.name))
        return result()

    @asynccontextmanager
    async def sftp(target):
        sent.append(("sftp", target.name))
        yield docker_on.exec.sftp_server

    monkeypatch.setattr(docker_on.handle, "run", run)
    monkeypatch.setattr(docker_on.handle, "run_argv", run_argv, raising=False)
    monkeypatch.setattr(docker_on.handle, "sftp", sftp)
    values = {"host_id": host.id, "container": "web", "project": "shop", "entry_id": "1", "name": "hub", "stamp": "20261016-120000"}
    seen = set()
    for route in _routes(get_extension_runtime().loaded["docker"].router.routes):
        if "{host_id}" not in route.path:
            continue
        for method in route.methods:
            key = f"{method} {route.path}"
            seen.add(key)
            if key in OPERATE_ROUTES:
                continue
            answer = await client.request(method, "/api/v1/ext/docker" + route.path.format(**values), json=BODIES.get(key, {}),
                                          headers=docker_on.owner)
            assert answer.status_code == 409 and "Nur bedienen" in answer.json()["detail"], (key, answer.text)
    assert OPERATE_ROUTES | BODIES.keys() <= seen
    checked = await client.post(f"/api/v1/ext/docker/image-updates/check?host_id={host.id}", headers=docker_on.owner)
    assert checked.status_code == 409
    assert [call for call in sent if call[1] == "nur-lesen"] == []
    await get_extension_runtime().loaded["docker"].ctx.settings.set({"docker_host_tag": "voll"})
    named = await client.get(f"/api/v1/ext/docker/hosts/{host.id}/stacks", headers=docker_on.owner)
    assert "Einstellungen, Server & Zugänge die Markierung „voll“" in named.json()["detail"]


def test_the_script_ignores_docker_settings_from_the_environment(tmp_path):
    copy = _script_with_fake_docker(tmp_path)
    fake = tmp_path / "bin" / "docker"
    fake.write_text(f'#!/bin/sh\nenv | grep "^DOCKER_" > {tmp_path / "calls"} || true\n', encoding="ascii")
    foreign = {"DOCKER_HOST": "tcp://203.0.113.9:2375", "DOCKER_CONTEXT": "fremd", "DOCKER_CONFIG": "/tmp/x"}
    for shell in _shells():
        # Ohne geerbte Werte zeigt sich auch ein vergessenes `export`.
        for env in ({"PATH": "/usr/bin:/bin", **foreign}, {"PATH": "/usr/bin:/bin"}):
            done = subprocess.run([shell, str(copy), "ps"], capture_output=True, text=True, timeout=30, check=False, env=env)
            assert done.returncode == 0, shell
            assert sorted((tmp_path / "calls").read_text().splitlines()) == [
                "DOCKER_CONFIG=/usr/local/lib/nodvard-deck/docker-root-config", "DOCKER_HOST=unix:///var/run/docker.sock",
            ], (shell, env)


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("docker") is None, reason="braucht die Docker-CLI unter Linux")
def test_a_context_of_root_does_not_change_the_daemon(tmp_path):
    shells = _shells()
    home = tmp_path / "root"
    home.mkdir()
    cli = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    for args in (["context", "create", "fremd", "--docker", "host=tcp://127.0.0.1:1"], ["context", "use", "fremd"]):
        assert subprocess.run(["docker", *args], capture_output=True, timeout=30, check=False, env=cli).returncode == 0
    copy = tmp_path / "docker-root.sh"
    copy.write_text(SCRIPT.read_text(encoding="ascii")
                    .replace("PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={shutil.which('docker').rsplit('/', 1)[0]}:/usr/bin:/bin")
                    .replace("HOME=/root", f"HOME={home}"), encoding="ascii")
    for shell in shells:
        done = subprocess.run([shell, str(copy), "ps"], capture_output=True, text=True, timeout=30, check=False, env={})
        assert "127.0.0.1:1" not in done.stderr, shell
        assert done.returncode == 0 or "unix:///var/run/docker.sock" in done.stderr, (shell, done.stderr)
