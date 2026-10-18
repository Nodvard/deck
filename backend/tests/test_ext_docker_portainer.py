"""Umzug von Portainer: Datei und Variablen aus Portainers Speicher in einen eigenen Ordner, dann neu ausrollen."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from docker_stack_fakes import EXT_DIR, FakeExec, docker_on, ps, result, row  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True)
def _ext_docker_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


ROOT = "/opt/stacks"
FOLDER = "/opt/stacks/kuma"
COMPOSE = "services:\n  app:\n    image: louislam/uptime-kuma:2.5.6\n    environment:\n      TOKEN: ${TOKEN}\n    volumes:\n      - daten:/app/data\nvolumes:\n  daten:\n"
ENV = "TOKEN=geheim123\n"
PORTAINER = {"id": "a" * 64, "name": "/portainer", "project": "", "image": "portainer/portainer-ce:lts", "running": True,
             "mounts": [{"Type": "volume", "Name": "portainer_data", "Destination": "/data"}]}


def _m():
    from nodvard_deck_ext_docker import adopt, after_check, editor, image_apply, new_stack, portainer_move, stack_actions, stacks

    return SimpleNamespace(adopt=adopt, after_check=after_check, editor=editor, image_apply=image_apply, new_stack=new_stack,
                           move=portainer_move, stack_actions=stack_actions, stacks=stacks)


def _portainer_row(**changes):
    return row(**{"project": "kuma", "name": "kuma-app-1", "service": "app", "working_dir": "/data/compose/7",
                  "config_files": "/data/compose/7/docker-compose.yml", **changes})


async def _async(value):
    return value


class _Settings:
    async def get(self):
        return {}


LINK = object()
"""Steht im Speicher an Stelle eines Inhalts: die Datei ist dort ein Verweis."""


def _setup(monkeypatch, tmp_path, *, files=None, verdict=True, tags=("docker",)):
    m = _m()
    fake = FakeExec()
    fake.rows = [_portainer_row()]
    fake.containers = [PORTAINER]
    fake.up = {"moves": True, "hang": False, "timeout": False}
    """Was `up --force-recreate` tut: Container in den neuen Ordner holen (auch wenn es danach scheitert), hängen, Zeit überschreiten."""
    stored = {"docker-compose.yml": COMPOSE, "stack.env": ENV} if files is None else files
    original = fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:2] == ["docker", "run"] and argv[6].endswith(":/data:ro"):
            fake.argv_calls.append(list(argv))
            name, limit = argv[-2], int(argv[-1])
            if name not in stored:
                return result(code=m.move.MISSING_EXIT)
            if stored[name] is LINK:
                return result(code=m.move.NOT_A_FILE_EXIT)
            return result(stored[name][:limit])
        if argv[:3] == ["docker", "ps", "-a"] and m.move.WORKING_DIRS_FORMAT in argv:
            fake.argv_calls.append(list(argv))
            return result("".join(f"{r['working_dir']}\t{r.get('state', 'running')}\n" for r in fake.rows if r["project"] == "kuma"))
        if "--force-recreate" in argv:
            fake.argv_calls.append(list(argv))
            if fake.up["hang"]:
                await asyncio.sleep(3600)
            if fake.up["moves"]:
                fake.rows = [_portainer_row(working_dir=FOLDER, config_files=f"{FOLDER}/compose.yaml")]
            if fake.up["timeout"]:
                raise TimeoutError
            return fake.verb
        return await original(host, argv, **kwargs)

    fake.run_argv = run_argv
    host = SimpleNamespace(id="h1", tags=list(tags))
    ctx = SimpleNamespace(exec=fake, hosts=SimpleNamespace(get=lambda host_id: _async(host if host_id == "h1" else None)), settings=_Settings())
    store = m.adopt.AdoptedStacks(Path(tmp_path))
    st = m.stacks.DockerStacks(ctx, store)
    actions = m.stack_actions.DockerStackExecutor(ctx, st, store)
    editor = m.editor.StackEditor(ctx, actions, Path(tmp_path))
    new = m.new_stack.NewStacks(ctx, editor, st, store, asyncio.Lock())
    create = m.new_stack.DockerStackCreateExecutor(ctx, new, store, actions)
    move = m.move.PortainerMove(ctx, st, store, new)

    async def check_running(ctx, host, refs, **_):
        return m.image_apply.Verdict(ok=verdict, text="Container läuft stabil." if verdict else "Container startet immer wieder neu."), None

    monkeypatch.setattr(m.after_check, "check_running", check_running)
    return SimpleNamespace(m=m, fake=fake, host=host, store=store, move=move, actions=actions, sftp=fake.sftp_server, stored=stored,
                           executor=m.move.DockerPortainerMoveExecutor(ctx, move, create, store, actions))


@pytest.fixture
def env(monkeypatch, tmp_path):
    return lambda **kw: _setup(monkeypatch, tmp_path, **kw)


def _request(plan, *, risk="high", actor="user", project="kuma"):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor, Risk

    return ActionRequest(action_type="docker.stack_from_portainer", host_ref="h1", risk=Risk(risk), proposed_by=Actor(type=actor, id="u1"),
                         reason="Test", payload={"project": project, "folder": plan["folder"], "portainer_id": plan["portainer_id"]})


def test_commands():
    mv = _m().move
    argv = mv.cmd_read("portainer_data", "7", "docker-compose.yml", 1000)
    assert argv[:10] == ["docker", "run", "--rm", "--network", "none", "-v", "portainer_data:/data:ro", "busybox:1.37", "sh", "-c"]
    assert argv[-4:] == ["sh", "7", "docker-compose.yml", "1001"] and "head -c" in argv[10] and "cat" not in argv[10]
    assert mv.cmd_read("/srv/portainer", "12", "stack.env", 10)[6] == "/srv/portainer:/data:ro"
    for bad in (("a;b", "7", "x.yml", 1), ("/srv/a:b", "7", "x.yml", 1), ("v", "7a", "x.yml", 1), ("v", "7", "../etc/passwd", 1),
                ("v", "7", "x.env", 1), ("relativ/pfad", "7", "x.yml", 1), ("v", "1234567890", "x.yml", 1), ("v", "\u0667", "x.yml", 1),
                ("v", "7\n", "x.yml", 1), ("v", "7", "x.yml\n", 1), ("v", "7", "x.yml", 0)):
        with pytest.raises(ValueError):
            mv.cmd_read(*bad)
    assert mv.cmd_working_dirs("kuma")[:5] == ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=kuma"]
    with pytest.raises(ValueError):
        mv.cmd_working_dirs("Kuma;id")
    up = mv.cmd_move_up(_m().new_stack.new_stack("kuma", ROOT))
    assert up[-3:] == ["up", "-d", "--force-recreate"] and "-v" not in up
    with pytest.raises(ValueError):
        mv.cmd_portainer_inspect(["abc"])


def test_finding_portainers_storage():
    import json

    mv = _m().move

    def lines(*rows):
        return "".join(json.dumps(r) + "\n" for r in rows)

    other = {"name": "/web", "image": "nginx:1.30", "mounts": [{"Type": "volume", "Name": "web_data", "Destination": "/data"}]}
    assert mv.find_storage(lines(other, PORTAINER)) == "portainer_data"
    bind = {"name": "/p", "image": "ghcr.io/portainer/portainer-ee:2.45.0@sha256:" + "b" * 64,
            "mounts": [{"Type": "bind", "Source": "/srv/portainer", "Destination": "/data"}]}
    assert mv.find_storage(lines(bind)) == "/srv/portainer"
    elsewhere = {**PORTAINER, "mounts": [{"Type": "volume", "Name": "portainer_data", "Destination": "/var/data"}]}
    assert mv.find_storage(lines(elsewhere, other) + "kaputt\n") is None
    fake = {**PORTAINER, "image": "portainer/portainer-ce-fake:1"}
    assert mv.find_storage(lines(fake)) is None
    stopped = {**PORTAINER, "running": False, "mounts": [{"Type": "volume", "Name": "alt_data", "Destination": "/data"}]}
    assert mv.find_storage(lines(stopped, PORTAINER)) == "portainer_data", "ein laufender Portainer geht vor"
    assert mv.find_storage(lines(stopped)) == "alt_data"
    second = {**PORTAINER, "mounts": [{"Type": "volume", "Name": "zweiter_data", "Destination": "/data"}]}
    with pytest.raises(mv.PortainerProblem, match="mehrere Portainer"):
        mv.find_storage(lines(PORTAINER, second))
    assert mv.find_storage(lines(PORTAINER, PORTAINER)) == "portainer_data"


def test_which_portainer_stacks_can_move():
    m = _m()
    stack = m.stacks.parse_members(ps(_portainer_row()))[0]
    assert m.move.portainer_entry(stack) == ("7", "docker-compose.yml")
    two = m.stacks.parse_members(ps(_portainer_row(config_files="/data/compose/7/a.yml,/data/compose/7/b.yml")))[0]
    assert m.move.portainer_entry(two).startswith("Dieser Portainer-Stack besteht aus mehreren Dateien")
    git = m.stacks.parse_members(ps(_portainer_row(working_dir="/data/compose/7/repo", config_files="/data/compose/7/repo/c.yml")))[0]
    assert m.move.portainer_entry(git).startswith("Portainer führt diesen Stack in mehreren Fassungen oder holt ihn aus Git.")
    plain = m.stacks.parse_members(ps(row()))[0]
    assert m.move.portainer_entry(plain).startswith("Das ist kein Stack")


def test_what_would_point_elsewhere_after_the_move():
    mv = _m().move
    assert mv.move_blockers(COMPOSE, "/data/compose/7") == []
    text = """services:
  app:
    image: a:1
    build: .
    env_file: [stack.env, ./extra.env, /etc/app.env]
    volumes:
      - ./daten:/daten
      - type: bind
        source: ../geteilt
        target: /x
      - /srv/abs:/abs
      - named:/n
configs:
  c:
    file: ./c.conf
include: [other.yml]
"""
    found = mv.move_blockers(text, "/data/compose/7")
    assert found[0] == "Die Datei bindet weitere Dateien ein (include)."
    assert "„app“ bindet ./daten ein. Die Daten liegen auf dem Server unter /data/compose/7/daten; trage in Portainer diesen vollen Pfad ein, dann klappt der Umzug ohne Datenverlust." in found
    assert any("../geteilt" in f and "/data/compose/geteilt" in f for f in found)
    assert any("build, extends" in f for f in found) and any("./extra.env" in f for f in found) and any("./c.conf" in f for f in found)
    assert not any("/srv/abs" in f or "stack.env" in f or "/etc/app.env" in f for f in found)
    assert mv.move_blockers("services: [", "/data/compose/7")[0].startswith("Die Datei lässt sich nicht lesen")
    assert mv.uses_stack_env("services:\n  a:\n    env_file:\n      - path: stack.env\n") is True
    assert mv.uses_stack_env(COMPOSE) is False


@pytest.mark.asyncio
async def test_the_plan(env):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    assert plan == {"project": "kuma", "folder": FOLDER, "portainer_id": "7", "services": ["app"], "stopped": False, "problem": None}
    original = e.fake.run_argv

    async def one_gone(host, argv, **kwargs):
        answer = await original(host, argv, **kwargs)
        if argv[:3] == ["docker", "container", "inspect"]:
            return result(answer.stdout, "Error: No such container: " + "f" * 64 + "\n", 1)
        return answer

    e.fake.run_argv = one_gone
    assert (await e.move.plan(e.host, "kuma"))["problem"] is None, "ein inzwischen verschwundener Container stört nicht"
    e.fake.run_argv = original
    e.fake.containers = []
    assert (await e.move.plan(e.host, "kuma"))["problem"].startswith("Auf diesem Server läuft kein Portainer")
    e.fake.containers = [PORTAINER]
    e.sftp.dirs.add(FOLDER)
    assert (await e.move.plan(e.host, "kuma"))["problem"] == f"Den Ordner {FOLDER} gibt es schon. Nodvard Deck überschreibt nie einen bestehenden Ordner."
    assert (await e.move.plan(e.host, "fehlt"))["problem"] == "Auf diesem Server läuft kein Stack „fehlt“."


@pytest.mark.asyncio
async def test_a_broken_channel_is_no_free_folder(env):
    e = env()
    lost = pytest.importorskip("asyncssh").ChannelOpenError(2, "Kanal zu")

    async def broken(path):
        raise lost

    e.sftp.lstat = broken
    assert (await e.move.plan(e.host, "kuma"))["problem"] == (f"Ob es den Ordner {FOLDER} schon gibt, ließ sich nicht prüfen (Kanal zu). "
                                                             "Versuch es noch einmal.")


@pytest.mark.asyncio
async def test_the_move(env):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    done = await e.executor.execute(_request(plan))
    assert done.success is True, done.error
    assert done.output.startswith(f"Stack „kuma“ liegt jetzt in {FOLDER} und ist übernommen. Prüfung danach: Container läuft stabil.")
    assert "Lösche den Stack danach nicht in Portainer" in done.output
    assert e.sftp.files[f"{FOLDER}/compose.yaml"] == COMPOSE.encode() and e.sftp.files[f"{FOLDER}/.env"] == ENV.encode()
    assert e.sftp.modes[f"{FOLDER}/.env"] & 0o777 == 0o600 and f"{FOLDER}/stack.env" not in e.sftp.files
    assert "kuma" in e.store.for_host("h1")
    reads = [c for c in e.fake.argv_calls if c[:2] == ["docker", "run"]]
    assert [c[-2] for c in reads] == ["docker-compose.yml", "stack.env"]
    assert any(c[-3:] == ["up", "-d", "--force-recreate"] for c in e.fake.argv_calls)
    assert all(ENV.strip() not in " ".join(c) for c in e.fake.argv_calls), "Werte nie auf der Kommandozeile"


@pytest.mark.asyncio
async def test_stack_env_comes_along_when_the_file_reads_it(env):
    compose = COMPOSE.replace("    environment:\n      TOKEN: ${TOKEN}\n", "    env_file: stack.env\n")
    e = env(files={"docker-compose.yml": compose, "stack.env": ENV})
    done = await e.executor.execute(_request(await e.move.plan(e.host, "kuma")))
    assert done.success is True, done.error
    assert e.sftp.files[f"{FOLDER}/stack.env"] == ENV.encode() and e.sftp.files[f"{FOLDER}/.env"] == ENV.encode()


@pytest.mark.asyncio
async def test_without_variables_there_is_no_env_file(env):
    e = env(files={"docker-compose.yml": COMPOSE.replace("${TOKEN}", "fest")})
    assert (await e.executor.execute(_request(await e.move.plan(e.host, "kuma")))).success is True
    assert f"{FOLDER}/.env" not in e.sftp.files


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepare", "error"),
    [
        (lambda e: e.stored.update({"docker-compose.yml": COMPOSE.replace("daten:/app/data", "./daten:/app/data")}),
         "„app“ bindet ./daten ein. Die Daten liegen auf dem Server unter /data/compose/7/daten"),
        (lambda e: e.stored.pop("docker-compose.yml"), "docker-compose.yml fehlt in Portainers Speicher."),
        (lambda e: e.stored.update({"docker-compose.yml": LINK}), "docker-compose.yml ist in Portainers Speicher keine normale Datei"),
        (lambda e: e.stored.update({"stack.env": LINK}), "stack.env ist in Portainers Speicher keine normale Datei"),
        (lambda e: e.stored.update({"stack.env": "WERT=caf\ufffd\n"}), "stack.env ist keine Textdatei in UTF-8."),
        (lambda e: setattr(e.fake, "config", result(json.dumps({"services": {"app": {"volumes": [
            {"type": "bind", "source": f"{FOLDER}/daten", "target": "/daten"}]}}}))),
         "„app“ bindet einen Ordner neben der Datei ein. Unter Portainer liegen die Daten auf dem Server unter /data/compose/7/daten"),
        (lambda e: setattr(e.fake, "config", result(json.dumps({"services": {}, "secrets": {"pw": {"file": f"{FOLDER}/pw.txt"}}}))),
         "„pw“ (secrets) liest /data/compose/7/pw.txt aus dem Ordner bei Portainer."),
        (lambda e: setattr(e.fake, "config", result(stderr=f"failed to read {FOLDER}/.env: line 1: unterminated quoted value \"geheim123\n", code=15)),
         "Compose liest die Variablen aus Portainer nicht; den Wert zeigt Nodvard Deck nicht."),
        (lambda e: setattr(e.fake, "config", result(
            stderr=f"failed to read {FOLDER}/.env: line 3: unexpected character \"!\" in variable name \"A!B=geheim123\"\n", code=15)),
         "Compose liest die Variablen aus Portainer nicht (Zeile 3 in stack.env); den Wert zeigt Nodvard Deck nicht."),
        (lambda e: e.stored.update({"docker-compose.yml": "x" * (256 * 1024 + 1)}), "docker-compose.yml ist größer als 256 KB."),
        (lambda e: setattr(e.fake, "config", result(stderr="service app: invalid\n", code=15)), "Compose lehnt die Datei ab"),
    ],
)
async def test_refusals_leave_nothing_behind(env, prepare, error):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    prepare(e)
    done = await e.executor.execute(_request(plan))
    assert done.success is False and error in done.error
    assert done.error.endswith(("Geändert wurde nichts.", "Am Stack hat sich nichts geändert.")) and "geheim123" not in done.error
    assert FOLDER not in e.sftp.dirs and not [p for p in e.sftp.files if p.startswith(FOLDER)]
    assert "kuma" not in e.store.for_host("h1") and not any("--force-recreate" in c for c in e.fake.argv_calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"actor": "ai"}, "Einen Stack zieht nur ein Mensch über die Docker-Seite um."),
        ({"risk": "medium"}, "Der Umzug braucht eine Freigabe mit hohem Risiko."),
        ({"project": "Kuma;id"}, "Ungültiger Stack-Name: 'Kuma;id'"),
    ],
)
async def test_who_and_what_may_move(env, change, error):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    e.fake.argv_calls.clear()
    assert (await e.executor.execute(_request(plan, **change))).error == error
    assert e.fake.argv_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("compose", "error"),
    [
        ("services:\n  fremd:\n    image: nginx:1.27\n", "Darin fehlt „app“, dafür steht „fremd“ darin."),
        (COMPOSE.replace("volumes:\n  daten:\n", "  extra:\n    image: redis:7\nvolumes:\n  daten:\n"),
         "Für „extra“ steht ein Dienst in der Datei bei Portainer, es gibt aber keinen Container dazu."),
    ],
    ids=["fremde-datei", "dienst-mehr"],
)
async def test_a_file_that_does_not_match_the_running_stack_is_refused(env, compose, error):
    e = env(files={"docker-compose.yml": compose, "stack.env": ENV})
    done = await e.executor.execute(_request(await e.move.plan(e.host, "kuma")))
    assert done.success is False and error in done.error and done.error.endswith("Geändert wurde nichts."), done.error
    assert FOLDER not in e.sftp.dirs and not any("--force-recreate" in c for c in e.fake.argv_calls)


@pytest.mark.asyncio
async def test_a_container_left_from_a_removed_service_is_named(env):
    e = env()
    e.fake.rows = [_portainer_row(), _portainer_row(name="kuma-alt-1", service="alt")]
    done = await e.executor.execute(_request(await e.move.plan(e.host, "kuma")))
    assert done.error.startswith("Für „alt“ gibt es noch einen Container, in der Datei bei Portainer steht der Dienst aber nicht. Ist das "
                                 "ein Rest eines entfernten Dienstes, lösche den Container im Reiter Container"), done.error


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", ["    profiles: [debug]\n", "    scale: 0\n", "    deploy:\n      replicas: 0\n"], ids=["profil", "scale", "replicas"])
async def test_a_service_that_does_not_start_may_be_missing(env, extra):
    compose = COMPOSE.replace("volumes:\n  daten:\n", f"  debug:\n    image: busybox:1.37\n{extra}volumes:\n  daten:\n")
    e = env(files={"docker-compose.yml": compose, "stack.env": ENV})
    assert (await e.executor.execute(_request(await e.move.plan(e.host, "kuma")))).success is True


@pytest.mark.asyncio
async def test_an_env_file_others_could_read_is_refused(env, monkeypatch):
    e = env()
    real_stat = e.sftp.stat

    async def stat(path):
        attrs = await real_stat(path)
        if path.endswith("/.env"):
            attrs.permissions = 0o100640
        return attrs

    monkeypatch.setattr(e.sftp, "stat", stat)
    done = await e.executor.execute(_request(await e.move.plan(e.host, "kuma")))
    assert done.error.startswith(".env wäre für andere auf dem Server lesbar; der Server übernimmt die Rechte 600 nicht."), done.error
    assert FOLDER not in e.sftp.dirs and not any("--force-recreate" in c for c in e.fake.argv_calls)


@pytest.mark.asyncio
async def test_a_server_that_ignores_the_mode_still_gets_private_files(env):
    e = env()
    e.sftp.open_ignores_mode = True
    assert (await e.executor.execute(_request(await e.move.plan(e.host, "kuma")))).success is True
    assert e.sftp.modes[f"{FOLDER}/.env"] & 0o777 == 0o600 and e.sftp.modes[f"{FOLDER}/compose.yaml"] & 0o777 == 0o600


@pytest.mark.asyncio
async def test_a_compose_file_others_could_read_is_refused(env, monkeypatch):
    e = env()
    real_stat = e.sftp.stat

    async def stat(path):
        attrs = await real_stat(path)
        if path.endswith("/compose.yaml"):
            attrs.permissions = 0o100644
        return attrs

    monkeypatch.setattr(e.sftp, "stat", stat)
    done = await e.executor.execute(_request(await e.move.plan(e.host, "kuma")))
    assert done.error.startswith("compose.yaml wäre für andere auf dem Server lesbar"), done.error
    assert FOLDER not in e.sftp.dirs and not any("--force-recreate" in c for c in e.fake.argv_calls)


@pytest.mark.asyncio
async def test_a_changed_plan_and_a_busy_stack_stop_the_move(env):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    stale = await e.executor.execute(_request({**plan, "portainer_id": "8"}))
    assert stale.error == "Der Stack oder der Ordner hat sich inzwischen geändert. Bitte noch einmal umziehen."
    with e.actions.locks.hold("h1", "kuma", "Anwenden"):
        busy = await e.executor.execute(_request(plan))
    assert busy.error == "An diesem Stack läuft gerade schon etwas (Anwenden). Versuch es danach noch einmal."
    assert not any(c[:2] == ["docker", "run"] for c in e.fake.argv_calls)


@pytest.mark.asyncio
async def test_a_failed_roll_out_takes_its_folder_back(env):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    e.fake.up["moves"] = False
    e.fake.verb = result(stderr="Error response from daemon: port is already allocated\n", code=1)
    done = await e.executor.execute(_request(plan))
    assert done.success is False and done.error == (
        "Neu ausrollen ist gescheitert: Error response from daemon: port is already allocated. Den neuen Ordner hat Nodvard Deck wieder "
        "entfernt. Die Container laufen weiter wie unter Portainer.")
    assert FOLDER not in e.sftp.dirs and "kuma" not in e.store.for_host("h1")
    again = await e.move.plan(e.host, "kuma")
    assert again["problem"] is None, "kein Rest, der einen zweiten Versuch verhindert"
    e.move.working_dirs = lambda *_a: _async([("/data/compose/7", "exited")])
    gone = await e.executor.execute(_request(again))
    assert gone.error.endswith("Die alten Container laufen nicht mehr; rolle den Stack in Portainer noch einmal aus, dann läuft er wie vorher.")


@pytest.mark.asyncio
async def test_a_half_done_roll_out_is_adopted_and_says_so(env):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    e.fake.verb = result(stderr="Error response from daemon: port is already allocated\n", code=1)
    done = await e.executor.execute(_request(plan))
    assert done.error.startswith(f"Neu ausrollen ist gescheitert: Error response from daemon: port is already allocated. Die Container "
                                 f"kommen schon aus {FOLDER}; der Stack ist übernommen. Behebe den Grund und wende ihn im Reiter Stacks "
                                 "noch einmal an.")
    assert "kuma" in e.store.for_host("h1") and f"{FOLDER}/compose.yaml" in e.sftp.files


@pytest.mark.asyncio
async def test_a_half_done_roll_out_says_how_to_finish(env):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    e.fake.verb = result(stderr="Error response from daemon: port is already allocated\n", code=1)
    e.move.working_dirs = lambda *_a: _async([(FOLDER, "running"), ("/data/compose/7", "running")])
    done = await e.executor.execute(_request(plan))
    assert (f"Ein Teil der Container kommt schon aus {FOLDER}, 1 noch aus dem Ordner bei Portainer; der Stack ist übernommen. Behebe den "
            "Grund, fahre ihn dann im Reiter Stacks herunter und wende ihn wieder an (die Volumes bleiben)") in done.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("states", "after", "now"),
    [
        (["running", "running"], ["running", "running"], "Die Container laufen weiter wie unter Portainer."),
        (["running", "running"], ["exited", "running"], "Von den alten Containern laufen nur noch 1 von 2; rolle den Stack in Portainer"),
        (["running", "running"], ["exited", "exited"], "Die alten Container laufen nicht mehr; rolle den Stack in Portainer"),
        (["exited", "exited"], ["exited", "exited"], "Der Stack bleibt gestoppt wie vorher."),
    ],
    ids=["alle", "teil", "keiner", "vorher-gestoppt"],
)
async def test_a_failed_roll_out_says_what_runs(env, states, after, now):
    compose = COMPOSE.replace("volumes:\n  daten:\n", "  db:\n    image: redis:7\nvolumes:\n  daten:\n")
    e = env(files={"docker-compose.yml": compose, "stack.env": ENV})
    e.fake.rows = [_portainer_row(state=states[0]), _portainer_row(name="kuma-db-1", service="db", state=states[1])]
    plan = await e.move.plan(e.host, "kuma")
    e.fake.up["moves"] = False
    e.fake.verb = result(stderr="Error response from daemon: port is already allocated\n", code=1)
    e.move.working_dirs = lambda *_a: _async([("/data/compose/7", state) for state in after])
    done = await e.executor.execute(_request(plan))
    assert now in done.error, done.error


@pytest.mark.asyncio
async def test_a_roll_out_that_runs_out_of_time_keeps_the_folder(env):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    e.fake.up.update({"moves": False, "timeout": True})
    done = await e.executor.execute(_request(plan))
    assert done.error.startswith("Neu ausrollen hat nach 45 Minuten nicht geendet; Compose kann auf dem Server noch weiterlaufen")
    assert f"lösche den Ordner {FOLDER} im Dateimanager" in done.error
    assert FOLDER in e.sftp.dirs and "kuma" not in e.store.for_host("h1")


@pytest.mark.asyncio
async def test_a_cancel_during_the_roll_out_adopts_what_already_moved(env):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    e.fake.up["hang"] = True
    task = asyncio.create_task(e.executor.execute(_request(plan)))
    while not any("--force-recreate" in c for c in e.fake.argv_calls):
        await asyncio.sleep(0)
    e.fake.rows = [_portainer_row(working_dir=FOLDER, config_files=f"{FOLDER}/compose.yaml")]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(20):
        await asyncio.sleep(0)
    assert "kuma" in e.store.for_host("h1") and e.actions.locks.held("h1", "kuma") is None


def _breaks(e, when):
    inner = e.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if when(argv, e.fake.argv_calls):
            raise pytest.importorskip("asyncssh").ConnectionLost("Connection lost")
        return await inner(host, argv, **kwargs)

    e.fake.run_argv = run_argv


def _rolled(calls):
    return any("--force-recreate" in c for c in calls)


@pytest.mark.asyncio
async def test_a_lost_connection_while_reading_changes_nothing(env):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    _breaks(e, lambda argv, _: argv[:2] == ["docker", "run"])
    done = await e.executor.execute(_request(plan))
    assert done.error == "docker-compose.yml ließ sich bei Portainer nicht lesen: die Verbindung zum Server ist abgebrochen. Geändert wurde nichts."
    _breaks(e, lambda argv, _: True)
    done = await e.executor.execute(_request(plan))
    assert done.error == "Der Stack ließ sich bei Portainer nicht lesen: die Verbindung zum Server ist abgebrochen. Geändert wurde nichts."
    assert FOLDER not in e.sftp.dirs and e.actions.locks.held("h1", "kuma") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("moved", [False, True])
async def test_a_lost_connection_during_the_roll_out_keeps_the_folder(env, moved):
    e = env()
    plan = await e.move.plan(e.host, "kuma")

    def when(argv, _):
        if "--force-recreate" not in argv:
            return False
        if moved:
            e.fake.rows = [_portainer_row(working_dir=FOLDER, config_files=f"{FOLDER}/compose.yaml")]
        return True

    _breaks(e, when)
    done = await e.executor.execute(_request(plan))
    assert done.error.startswith("Beim Ausrollen brach die Verbindung ab (die Verbindung zum Server ist abgebrochen); Compose kann "
                                 "auf dem Server noch weiterlaufen")
    assert e.m.move.KEEP_IN_PORTAINER in done.error and "45 Minuten" not in done.error
    assert f"{FOLDER}/compose.yaml" in e.sftp.files and ("kuma" in e.store.for_host("h1")) is moved


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["lost", "exit"])
async def test_an_unreadable_state_after_the_roll_out_keeps_the_folder(env, broken):
    e = env()
    plan = await e.move.plan(e.host, "kuma")
    if broken == "lost":
        _breaks(e, lambda argv, calls: argv[:3] == ["docker", "ps", "-a"] and _rolled(calls))
    else:
        inner = e.fake.run_argv

        async def run_argv(host, argv, **kwargs):
            if argv[:3] == ["docker", "ps", "-a"] and _rolled(e.fake.argv_calls):
                return result(stderr="Cannot connect to the Docker daemon\n", code=1)
            return await inner(host, argv, **kwargs)

        e.fake.run_argv = run_argv
    done = await e.executor.execute(_request(plan))
    assert done.success is False and done.error.startswith(f"Ob die Container jetzt aus {FOLDER} laufen, ließ sich danach nicht lesen.")
    assert e.m.move.KEEP_IN_PORTAINER in done.error and "laufen nicht mehr" not in done.error
    assert f"{FOLDER}/compose.yaml" in e.sftp.files and "kuma" not in e.store.for_host("h1")


@pytest.mark.asyncio
async def test_deck_itself_old_compose_and_stopped_stacks(env, monkeypatch):
    e = env()
    monkeypatch.setattr(e.m.move, "is_self_image", lambda image: True)
    assert (await e.move.plan(e.host, "kuma"))["problem"] == e.m.adopt.ITSELF
    monkeypatch.setattr(e.m.move, "is_self_image", lambda image: False)
    e.fake.rows = [_portainer_row(version="1.29.2")]
    assert (await e.move.plan(e.host, "kuma"))["problem"] == e.m.adopt.COMPOSE_V1
    e.fake.rows = [_portainer_row(state="exited")]
    stopped = await e.move.plan(e.host, "kuma")
    assert stopped["stopped"] is True and stopped["problem"] is None


@pytest.mark.asyncio
async def test_only_on_docker_hosts(monkeypatch, tmp_path):
    e = _setup(monkeypatch, tmp_path, tags=())
    plan = {"folder": FOLDER, "portainer_id": "7"}
    assert (await e.executor.execute(_request(plan))).error == "Kein Docker-Host."


def test_resolved_paths_inside_the_new_folder_are_refused():
    mv = _m().move
    config = {
        "services": {"app": {
            "volumes": [{"type": "bind", "source": f"{FOLDER}/daten", "target": "/d"}, {"type": "bind", "source": "/srv/abs", "target": "/a"},
                        {"type": "volume", "source": "named", "target": "/n"}, {"type": "bind", "source": FOLDER, "target": "/all"}],
            "env_file": [{"path": f"{FOLDER}/stack.env"}, {"path": f"{FOLDER}/.env"}, {"path": f"{FOLDER}/extra.env"}],
            "build": {"context": FOLDER},
        }},
        "configs": {"c": {"file": f"{FOLDER}/c.conf"}, "abs": {"file": "/etc/c.conf"}},
    }
    found = mv.resolved_blockers(config, FOLDER, "/data/compose/7")
    assert sum("bindet einen Ordner" in f for f in found) == 2
    assert any("/data/compose/7/daten" in f for f in found) and any("unter /data/compose/7;" in f for f in found)
    assert any("extra.env" in f for f in found) and not any("stack.env" in f or f"{FOLDER}/.env" in f for f in found)
    assert any("(build)" in f for f in found) and any("/data/compose/7/c.conf" in f for f in found)
    assert not any("/srv/abs" in f or "/etc/c.conf" in f for f in found)
    assert mv.resolved_blockers({"services": {"app": {"volumes": [{"type": "bind", "source": FOLDER + "-andere/x"}]}}}, FOLDER, "/d") == []


def test_values_never_show_in_messages():
    mv = _m().move
    values = mv.secret_values(b'TOKEN="geheim123"\nKURZ=ab\nLEER=\nexport API=schluessel-9\n')
    assert values == ["schluessel-9", "geheim123"]
    assert mv.scrub("Invalid template: \"${schluessel-9\"", values) == "Invalid template: \"${…\""
    assert "geheim123" not in mv.scrub("failed to read /opt/stacks/kuma/.env: line 3: unterminated quoted value \"geheim123", values)
    assert mv.scrub("port is already allocated", values) == "port is already allocated"
    assert mv.scrub("failed to read .env", []) == "failed to read .env"
    for own in (".env ließ sich nicht schreiben: Permission denied", "stack.env wäre für andere auf dem Server lesbar"):
        assert mv.scrub(own, values) == own


def test_values_as_compose_reads_them():
    mv = _m().move
    values = mv.secret_values(b'MIT=geheim-mit # Kommentar\nESC="a\\"bcdef"\nZEILEN="erste-zeile\nzweite-zeile"\n')
    for value in ("geheim-mit", 'a"bcdef', "erste-zeile", "zweite-zeile"):
        assert value in values, value


def test_values_as_docker_quotes_them():
    mv = _m().move
    values = mv.secret_values(b'P=ab$$cd-efgh\nQ=ab\\cd"ef-ghij\n')
    assert "ab$cd-efgh" in values and 'ab\\\\cd\\"ef-ghij' in values
    shown = 'exec: "ab$cd-efgh": executable file not found in $PATH; exec: "ab\\\\cd\\"ef-ghij"'
    assert "cd-efgh" not in mv.scrub(shown, values) and "ef-ghij" not in mv.scrub(shown, values)


def test_a_cut_off_value_is_trimmed():
    mv = _m().move
    values = mv.secret_values(b"MIT=geheim-mit # Kommentar\n")
    assert mv.scrub("Error: " + "x" * 280 + "geheim-m", values) == "Error: " + "x" * 280 + "…"


@pytest.mark.asyncio
async def test_a_value_cut_off_in_the_reason_never_shows(env):
    secret = "Sehr-Geheim-0815"
    e = env(files={"docker-compose.yml": COMPOSE, "stack.env": f"TOKEN={secret} # Kommentar\n"})
    plan = await e.move.plan(e.host, "kuma")
    e.fake.up["moves"] = False
    e.fake.verb = result(stderr=f"Error response from daemon: {'x' * 265} {secret}\n", code=1)
    done = await e.executor.execute(_request(plan))
    assert done.error.startswith("Neu ausrollen ist gescheitert: Error response from daemon: xxxx") and "…" in done.error
    assert secret[:4] not in done.error, done.error


@pytest.mark.asyncio
async def test_a_value_cut_off_in_the_check_never_shows(env):
    secret = "Sehr-Geheim-0815"
    e = env(files={"docker-compose.yml": COMPOSE, "stack.env": f"TOKEN={secret}\n"})
    plan = await e.move.plan(e.host, "kuma")
    e.fake.config = result(stderr=f"invalid hostPort: {'x' * 276}{secret}\n", code=15)
    done = await e.executor.execute(_request(plan))
    assert done.success is False and "invalid hostPort: xxxx" in done.error and secret[:4] not in done.error, done.error


@pytest.mark.asyncio
async def test_a_crash_after_the_move_is_reported(env):
    e = env(verdict=False)
    done = await e.executor.execute(_request(await e.move.plan(e.host, "kuma")))
    assert done.success is False and "Container startet immer wieder neu." in done.error


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_move_through_the_gate(client, docker_on):
    docker_on.exec.rows.append(_portainer_row())
    docker_on.exec.containers = [PORTAINER]
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/kuma/from-portainer"
    plan = await client.get(url, headers=docker_on.owner)
    assert plan.status_code == 200 and plan.json()["portainer_id"] == "7" and plan.json()["problem"] is None
    proposed = await client.post(url, headers=docker_on.owner)
    assert proposed.status_code == 202 and proposed.json()["risk"] == "high"
    assert not any(c[:2] == ["docker", "run"] for c in docker_on.exec.argv_calls), "vor der Freigabe liest nichts bei Portainer"
    action = await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)
    assert "Portainer-Stack „kuma“ nach /opt/stacks/kuma holen" in action.text
    docker_on.exec.containers = []
    refused = await client.post(url, headers=docker_on.owner)
    assert refused.status_code == 409 and "kein Portainer" in refused.json()["detail"]
    assert (await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/Kuma;id/from-portainer", headers=docker_on.owner)).status_code == 400


@pytest.mark.asyncio
async def test_not_offered_on_the_server_page_and_only_with_hosts_execute(client, docker_on):
    refused = await client.post(f"/api/v1/hosts/{docker_on.host}/actions/docker.stack_from_portainer",
                                json={"payload": {"project": "kuma"}, "reason": "Test"}, headers=docker_on.owner)
    assert refused.status_code == 404
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/kuma/from-portainer"
    for method in ("get", "post"):
        assert (await getattr(client, method)(url)).status_code == 401
        assert (await getattr(client, method)(url, headers=docker_on.viewer)).status_code == 403
