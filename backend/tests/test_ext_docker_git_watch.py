"""Stacks aus Git: neuen Stand im Repository erkennen und melden, ohne etwas zu holen oder einzuspielen."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pytest
from docker_stack_fakes import EXT_DIR, docker_on  # noqa: F401 - Fixtures


@pytest.fixture(autouse=True, scope="module")
def _ext_docker_on_path():
    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(EXT_DIR / "src"))
        yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_docker"):
            del sys.modules[name]


def _gf():
    from nodvard_deck_ext_docker import git_fetch

    return git_fetch


@pytest.fixture(autouse=True)
def _servers_on_loopback(monkeypatch):
    """Die Testserver laufen auf 127.0.0.1, das Deck sonst nie fragt; die Prüfung der Ziel-Adresse steht in
    test_ext_docker_git_target.py."""
    check = _gf().target_addresses
    monkeypatch.setattr(_gf(), "target_addresses",
                        lambda host, port, allow_private: [host] if host.startswith("127.") else check(host, port, allow_private))


URL = "https://git.example.org/team/stacks"
COMPOSE = b"services:\n  web:\n    image: busybox:1.37\n"
SHOWN = {"working_dir": "/opt/stacks/shop", "config_files": ["/opt/stacks/shop/compose.yaml"]}


def _commit(path: Path, text: bytes = COMPOSE, *, branch: str = "main", tag: str | None = None) -> str:
    from dulwich.objects import Blob, Commit, Tag, Tree
    from dulwich.repo import Repo

    repo = Repo(str(path)) if path.exists() else Repo.init(str(path), mkdir=True)
    try:
        blob = Blob.from_string(text)
        inner = Tree()
        inner.add(b"compose.yaml", 0o100644, blob.id)
        top = Tree()
        top.add(b"shop", 0o040000, inner.id)
        for obj in (blob, inner, top):
            repo.object_store.add_object(obj)
        parent = repo.refs.as_dict().get(f"refs/heads/{branch}".encode())
        commit = Commit()
        commit.tree, commit.parents = top.id, [parent] if parent else []
        commit.author = commit.committer = b"Ada Lovelace <ada@example.org>"
        commit.author_time = commit.commit_time = 1_700_000_000 + len(commit.parents)
        commit.author_timezone = commit.commit_timezone = 0
        commit.message = b"Stand\n"
        repo.object_store.add_object(commit)
        repo.refs[f"refs/heads/{branch}".encode()] = commit.id
        if tag:
            annotated = Tag()
            annotated.name, annotated.object, annotated.tagger = tag.encode(), (Commit, commit.id), b"Ada <ada@example.org>"
            annotated.tag_time, annotated.tag_timezone, annotated.message = 1_700_000_000, 0, b"Version"
            repo.object_store.add_object(annotated)
            repo.refs[f"refs/tags/{tag}".encode()] = annotated.id
        return commit.id.decode()
    finally:
        repo.close()


def _local(origin: Path):
    from dulwich.client import LocalGitClient

    return lambda url, credentials, limits: (LocalGitClient(thin_packs=False), str(origin))


def _in_process(client_for):
    async def run(request, limits):
        return await asyncio.to_thread(_gf().handle, request, client_for)

    return run


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def _serve(origin: Path):
    from dulwich.repo import Repo
    from dulwich.server import DictBackend
    from dulwich.web import make_wsgi_chain

    server = make_server("127.0.0.1", 0, make_wsgi_chain(DictBackend({"/team/stacks": Repo(str(origin))})), server_class=WSGIServer,
                         handler_class=_Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def no_proxy(monkeypatch):
    for name in ("https_proxy", "http_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


# --- Abfrage im Arbeiter -----------------------------------------------------------------------------------------------


def test_head_names_the_commit_of_a_branch_without_a_store(tmp_path):
    origin = tmp_path / "origin"
    commit = _commit(origin)
    assert _gf().head(URL, "main", None, _local(origin)) == (commit, "branch")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["origin"]


def test_head_follows_an_annotated_tag_to_its_commit(tmp_path, no_proxy):
    origin = tmp_path / "origin"
    commit = _commit(origin, tag="v1")
    gf = _gf()
    with _serve(origin) as base:
        found = gf.head(URL, "v1", None, lambda url, credentials, limits: gf.http_client(f"{base}/team/stacks", credentials, limits))
    assert found == (commit, "tag")


def _tag_on(origin: Path, target: str, *, annotated: bool) -> None:
    from dulwich.objects import Blob, Tag, Tree
    from dulwich.repo import Repo

    repo = Repo(str(origin))
    try:
        blob = Blob.from_string(b"kein Commit\n")
        tree = Tree()
        tree.add(b"liesmich", 0o100644, blob.id)
        for obj in (blob, tree):
            repo.object_store.add_object(obj)
        obj = {"blob": blob, "tree": tree}[target]
        sha = obj.id
        if annotated:
            tag = Tag()
            tag.name, tag.object, tag.tagger = b"kaputt", (type(obj), obj.id), b"Ada <ada@example.org>"
            tag.tag_time, tag.tag_timezone, tag.message = 1_700_000_000, 0, b"Kein Commit"
            repo.object_store.add_object(tag)
            sha = tag.id
        repo.refs[b"refs/tags/kaputt"] = sha
    finally:
        repo.close()


class _Counting:
    def __init__(self, client_for):
        self.fetches = 0
        self._client_for = client_for

    def __call__(self, url, credentials, limits):
        client, path = self._client_for(url, credentials, limits)
        counter = self

        class Client:
            def get_refs(self, *args, **kwargs):
                return client.get_refs(*args, **kwargs)

            def fetch_pack(self, *args, **kwargs):
                counter.fetches += 1
                return client.fetch_pack(*args, **kwargs)

        return Client(), path


@pytest.mark.parametrize(("target", "annotated"), [("tree", False), ("blob", False), ("tree", True), ("blob", True)])
def test_a_tag_on_a_tree_or_a_file_is_no_new_state(tmp_path, target, annotated):
    origin = tmp_path / "origin"
    _commit(origin)
    _tag_on(origin, target, annotated=annotated)
    gf = _gf()
    with pytest.raises(gf.GitProblem, match="Der Tag zeigt nicht auf einen Commit."):
        gf.head(URL, "kaputt", None, _local(origin))
    assert _gf().handle({"mode": "head", "url": URL, "ref": "kaputt"}, _local(origin)) == {"ok": False, "problem": gf.NOT_A_COMMIT}


def test_a_tag_on_a_tree_is_refused_over_http_too(tmp_path, no_proxy):
    origin = tmp_path / "origin"
    _commit(origin)
    _tag_on(origin, "tree", annotated=True)
    gf = _gf()
    with _serve(origin) as base, pytest.raises(gf.GitProblem, match="Der Tag zeigt nicht auf einen Commit."):
        gf.head(URL, "kaputt", None, lambda url, credentials, limits: gf.http_client(f"{base}/team/stacks", credentials, limits))


def test_a_tag_is_looked_up_only_when_its_commit_is_new(tmp_path):
    from dulwich.repo import Repo

    origin = tmp_path / "origin"
    commit = _commit(origin, tag="v1")
    with Repo(str(origin)) as repo:
        repo.refs[b"refs/tags/leicht"] = commit.encode()
    counting = _Counting(_local(origin))
    gf = _gf()
    assert gf.head(URL, "v1", None, counting) == (commit, "tag") and counting.fetches == 1
    assert gf.head(URL, "leicht", None, counting) == (commit, "tag") and counting.fetches == 2
    assert gf.head(URL, "leicht", None, counting, known=[commit]) == (commit, "tag") and counting.fetches == 2
    assert gf.head(URL, "main", None, counting) == (commit, "branch") and counting.fetches == 2
    answer = gf.handle({"mode": "head", "url": URL, "ref": "leicht", "known": [commit, 5, "x" * 40, None]}, counting)
    assert answer == {"ok": True, "commit": commit, "ref_kind": "tag"} and counting.fetches == 2


def test_head_prefers_a_branch_over_a_tag_of_the_same_name(tmp_path):
    origin = tmp_path / "origin"
    _commit(origin, tag="main")
    newer = _commit(origin, COMPOSE + b"    restart: always\n")
    assert _gf().head(URL, "main", None, _local(origin)) == (newer, "branch")


def test_head_of_a_missing_branch_is_a_problem(tmp_path):
    origin = tmp_path / "origin"
    _commit(origin)
    gf = _gf()
    with pytest.raises(gf.GitProblem, match="keinen Branch und kein Tag „fehlt“"):
        gf.head(URL, "fehlt", None, _local(origin))


def test_the_worker_answers_a_head_request(tmp_path):
    origin = tmp_path / "origin"
    commit = _commit(origin)
    answer = _gf().handle({"mode": "head", "url": URL, "ref": "main"}, _local(origin))
    assert answer == {"ok": True, "commit": commit, "ref_kind": "branch"}
    assert _gf().handle({"mode": "head", "url": URL, "ref": "fehlt"}, _local(origin))["ok"] is False


@pytest.mark.parametrize("answer", [
    {"ok": True, "commit": "abc", "ref_kind": "branch"},
    {"ok": True, "commit": "a" * 40, "ref_kind": "note"},
    {"ok": True, "commit": "A" * 40, "ref_kind": "branch"},
    {"ok": True},
])
def test_an_odd_head_answer_is_refused(answer):
    from nodvard_deck_ext_docker import git_source

    with pytest.raises(git_source.GitProblem, match="unlesbar"):
        git_source.parse_head(answer)


def test_the_reason_of_a_failed_head_is_passed_on():
    from nodvard_deck_ext_docker import git_source

    with pytest.raises(git_source.GitProblem, match="Der Server antwortet nicht"):
        git_source.parse_head({"ok": False, "problem": "Der Server antwortet nicht."})


# --- Über das Modul ----------------------------------------------------------------------------------------------------


def _instance():
    from nodvard_deck.ext.runtime import get_extension_runtime

    return get_extension_runtime().loaded["docker"].instance


async def _with_source(client, docker_on, origin: Path) -> str:
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop"
    assert (await client.post(f"{base}/adopt", json=SHOWN, headers=docker_on.owner)).status_code == 200
    _instance()._git.run_fetch = _in_process(_local(origin))
    saved = await client.put(f"{base}/git", headers=docker_on.owner, json={"url": URL, "path": "shop/compose.yaml"})
    assert saved.status_code == 200, saved.text
    return f"{base}/git"


def _notes(monkeypatch) -> list:
    sent: list = []

    async def send(note):
        sent.append(note)

    monkeypatch.setattr(_instance()._git_watch, "_ctx", SimpleNamespace(notify=SimpleNamespace(send=send), hosts=_instance()._git_watch._ctx.hosts))
    return sent


@pytest.mark.asyncio
async def test_check_on_request_reads_only_the_newest_commit(client, docker_on, tmp_path):
    origin = tmp_path / "origin"
    commit = _commit(origin)
    url = await _with_source(client, docker_on, origin)

    assert (await client.get(f"{url}/watch", headers=docker_on.owner)).json() == {"watch": None}
    before = dict(docker_on.exec.files)
    checked = await client.post(f"{url}/check", headers=docker_on.owner)
    assert checked.status_code == 200, checked.text
    watch = checked.json()["watch"]
    assert {k: watch[k] for k in ("remote", "short", "ref_kind", "problem", "applied", "new")} == {
        "remote": commit, "short": commit[:7], "ref_kind": "branch", "problem": None, "applied": None, "new": False}
    assert watch["checked_at"]
    assert (await client.get(f"{url}/watch", headers=docker_on.owner)).json()["watch"] == watch
    assert not _instance()._git.repo_dir(docker_on.host, "shop").exists(), "die Prüfung holt nichts"
    assert docker_on.exec.files == before, "auf dem Server ändert sich nichts"


@pytest.mark.asyncio
async def test_check_needs_rights_a_source_and_an_adopted_stack(client, docker_on, tmp_path):
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/git"
    assert (await client.post(f"{base}/check", headers=docker_on.viewer)).status_code == 403
    assert (await client.get(f"{base}/watch", headers=docker_on.viewer)).status_code == 403
    assert (await client.post(f"{base}/check", headers=docker_on.owner)).status_code == 409
    adopt = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/adopt"
    assert (await client.post(adopt, json=SHOWN, headers=docker_on.owner)).status_code == 200
    assert (await client.post(f"{base}/check", headers=docker_on.owner)).status_code == 404
    bad = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/Shop!/git/watch"
    assert (await client.get(bad, headers=docker_on.owner)).status_code == 400


@pytest.mark.asyncio
async def test_a_new_commit_is_reported_once_and_not_after_applying_it(client, docker_on, tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    first = _commit(origin)
    url = await _with_source(client, docker_on, origin)
    watch, state = _instance()._git_watch, _instance()._git_apply.state
    sent = _notes(monkeypatch)

    assert await watch.check_all() == {"stacks": 1, "new": 0, "problems": 0, "saved": True}, "noch nie eingespielt: keine Meldung"
    state.put_written(docker_on.host, "shop", first, {})
    assert (await watch.check_all())["new"] == 0 and sent == []

    second = _commit(origin, COMPOSE + b"    restart: always\n")
    assert (await watch.check_all())["new"] == 1
    assert len(sent) == 1
    assert sent[0].title == "Stacks aus Git: neuer Stand für 1 Stack"
    assert f"pi-host: shop ({second[:7]})" in sent[0].body and "Es wurde nichts geändert." in sent[0].body
    assert sent[0].payload["path"] == f"/ext/docker/matrix?tab=stacks&host={docker_on.host}"
    shown = (await client.get(f"{url}/watch", headers=docker_on.owner)).json()["watch"]
    assert (shown["remote"], shown["applied"], shown["new"]) == (second, first, True)

    assert (await watch.check_all())["new"] == 0 and len(sent) == 1, "derselbe Commit nur einmal"
    state.put_written(docker_on.host, "shop", second, {})
    assert (await client.get(f"{url}/watch", headers=docker_on.owner)).json()["watch"]["new"] is False


@pytest.mark.asyncio
async def test_a_failed_check_keeps_the_last_commit_and_says_why(client, docker_on, tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    first = _commit(origin)
    url = await _with_source(client, docker_on, origin)
    _notes(monkeypatch)
    await client.post(f"{url}/check", headers=docker_on.owner)

    async def offline(request, limits):
        return {"ok": False, "problem": "Der Server antwortet nicht."}

    _instance()._git.run_fetch = offline
    assert await _instance()._git_watch.check_all() == {"stacks": 1, "new": 0, "problems": 1, "saved": True}
    shown = (await client.get(f"{url}/watch", headers=docker_on.owner)).json()["watch"]
    assert (shown["remote"], shown["problem"], shown["new"]) == (first, "Der Server antwortet nicht.", False)


@pytest.mark.asyncio
async def test_a_changed_or_forgotten_source_drops_the_old_state(client, docker_on, tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    _commit(origin)
    url = await _with_source(client, docker_on, origin)
    _notes(monkeypatch)
    await client.post(f"{url}/check", headers=docker_on.owner)

    moved = await client.put(url, headers=docker_on.owner, json={"url": URL, "ref": "andere", "path": "shop/compose.yaml"})
    assert moved.status_code == 200
    assert (await client.get(f"{url}/watch", headers=docker_on.owner)).json() == {"watch": None}
    await client.put(url, headers=docker_on.owner, json={"url": URL, "path": "shop/compose.yaml"})
    await client.post(f"{url}/check", headers=docker_on.owner)
    assert (await client.delete(url, headers=docker_on.owner)).status_code == 200
    stored = json.loads((docker_on.data_dir / "git_watch.json").read_text(encoding="utf-8"))
    assert stored == {"stacks": {}}, "Vergessen räumt auf, auch ohne Lauf nach Zeitplan"
    await client.put(url, headers=docker_on.owner, json={"url": URL, "path": "shop/compose.yaml"})
    assert (await client.get(f"{url}/watch", headers=docker_on.owner)).json() == {"watch": None}


@pytest.mark.asyncio
async def test_one_broken_stack_does_not_stop_the_others(client, docker_on, tmp_path, monkeypatch, caplog):
    origin = tmp_path / "origin"
    _commit(origin)
    await _with_source(client, docker_on, origin)
    _notes(monkeypatch)

    async def crash(request, limits):
        raise RuntimeError("kaputt")

    _instance()._git.run_fetch = crash
    assert (await _instance()._git_watch.check_all())["problems"] == 1
    assert any("docker_git_watch_failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_the_job_runs_only_when_switched_on(docker_on, monkeypatch):
    from nodvard_deck_ext_docker import git_watch

    registered: list = []

    async def register_job(spec):
        registered.append(spec)

    ctx = SimpleNamespace(scheduler=SimpleNamespace(register_job=register_job))
    watch = _instance()._git_watch
    await git_watch.register_watch_job(ctx, watch, {})
    await git_watch.register_watch_job(ctx, watch, {"git_watch_enabled": True, "git_watch_cron": " 5 */2 * * * "})
    await git_watch.register_watch_job(ctx, watch, {"git_watch_enabled": True})
    assert [(s.id, s.enabled, s.schedule) for s in registered] == [
        ("git-watch", False, "0 0 31 2 *"), ("git-watch", True, "5 */2 * * *"), ("git-watch", True, git_watch.DEFAULT_CRON)]


def test_the_settings_offer_the_switch_and_the_schedule():
    schema = json.loads((EXT_DIR / "settings.schema.json").read_text(encoding="utf-8"))["properties"]
    assert schema["git_watch_enabled"]["default"] is False
    assert schema["git_watch_cron"]["x-widget"] == "schedule"


@pytest.mark.asyncio
async def test_nothing_is_reported_while_the_state_cannot_be_saved(client, docker_on, tmp_path, monkeypatch):
    from nodvard_deck_ext_docker import git_watch

    origin = tmp_path / "origin"
    first = _commit(origin)
    url = await _with_source(client, docker_on, origin)
    _instance()._git_apply.state.put_written(docker_on.host, "shop", first, {})
    second = _commit(origin, COMPOSE + b"    restart: always\n")
    sent = _notes(monkeypatch)

    def full(path, text):
        raise OSError(28, "Kein Platz")

    monkeypatch.setattr(git_watch, "_write_atomic", full)
    for _ in range(2):
        assert (await _instance()._git_watch.check_all())["saved"] is False
    assert sent == [], "ohne gespeicherten Stand käme sonst bei jedem Lauf dieselbe Meldung"
    checked = await client.post(f"{url}/check", headers=docker_on.owner)
    assert checked.json()["watch"]["remote"] == second, "der Knopf zeigt das Ergebnis trotzdem"


@pytest.mark.asyncio
async def test_a_check_on_request_does_not_wait_for_the_scheduled_run(client, docker_on, tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    commit = _commit(origin)
    url = await _with_source(client, docker_on, origin)
    _notes(monkeypatch)
    fast = _instance()._git.run_fetch
    gate, started = asyncio.Event(), asyncio.Event()

    async def first_waits(request, limits):
        if not started.is_set():
            started.set()
            await gate.wait()
        return await fast(request, limits)

    _instance()._git.run_fetch = first_waits
    running = asyncio.create_task(_instance()._git_watch.check_all())
    await started.wait()
    checked = await asyncio.wait_for(client.post(f"{url}/check", headers=docker_on.owner), 10)
    assert checked.json()["watch"]["remote"] == commit and not running.done()
    gate.set()
    assert (await running)["saved"] is True


@pytest.mark.asyncio
async def test_a_warning_after_three_failed_runs_and_again_only_after_a_success(client, docker_on, tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    _commit(origin)
    await _with_source(client, docker_on, origin)
    sent = _notes(monkeypatch)
    working = _instance()._git.run_fetch

    async def offline(request, limits):
        return {"ok": False, "problem": "Der Server antwortet nicht."}

    _instance()._git.run_fetch = offline
    for _ in range(4):
        await _instance()._git_watch.check_all()
    assert [n.title for n in sent] == ["Stacks aus Git: Prüfung scheitert bei 1 Stack"]
    assert "pi-host: shop (Der Server antwortet nicht.)" in sent[0].body and sent[0].severity.value == "warning"
    _instance()._git.run_fetch = working
    await _instance()._git_watch.check_all()
    _instance()._git.run_fetch = offline
    for _ in range(3):
        await _instance()._git_watch.check_all()
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_a_report_that_could_not_be_sent_comes_again(client, docker_on, tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    first = _commit(origin)
    await _with_source(client, docker_on, origin)
    _instance()._git_apply.state.put_written(docker_on.host, "shop", first, {})
    sent: list = []
    down = True

    async def send(note):
        if down:
            raise RuntimeError("Datenbank gesperrt")
        sent.append(note)

    watch = _instance()._git_watch
    monkeypatch.setattr(watch, "_ctx", SimpleNamespace(notify=SimpleNamespace(send=send), hosts=watch._ctx.hosts))
    working = _instance()._git.run_fetch

    async def offline(request, limits):
        return {"ok": False, "problem": "Der Server antwortet nicht."}

    _commit(origin, COMPOSE + b"    restart: always\n")
    await watch.check_all()
    _instance()._git.run_fetch = offline
    for _ in range(3):
        await watch.check_all()
    assert sent == []
    down = False
    await watch.check_all()
    await watch.check_all()
    assert [n.title for n in sent] == ["Stacks aus Git: Prüfung scheitert bei 1 Stack"]
    _instance()._git.run_fetch = working
    await watch.check_all()
    assert [n.title for n in sent][1:] == ["Stacks aus Git: neuer Stand für 1 Stack"]
    await watch.check_all()
    assert len(sent) == 2, "nach dem Versand nur einmal"


@pytest.mark.asyncio
async def test_an_access_for_another_server_is_refused_before_any_request(client, docker_on, tmp_path):
    from nodvard_deck_ext_docker import git_source

    git = _instance()._git
    calls: list = []

    async def record(request, limits):
        calls.append(request)
        return {"ok": True, "commit": "a" * 40, "ref_kind": "branch"}

    async def accesses():
        return {"fremd": git_source.Access("fremd", "other.example.org", "ada")}

    git.run_fetch = record
    git.accesses = accesses
    source = git_source.Source(URL, "main", "shop/compose.yaml", "fremd", "2026-10-30T00:00:00+00:00")
    with pytest.raises(git_source.GitProblem, match="gilt für other.example.org"):
        await git.head(docker_on.host, "shop", source)
    assert calls == []


@pytest.mark.asyncio
async def test_the_head_request_carries_the_setting_for_private_networks(client, docker_on):
    from nodvard_deck_ext_docker import git_source

    git = _instance()._git
    seen: list = []

    async def record(request, limits):
        seen.append(request["allow_private"])
        return {"ok": True, "commit": "a" * 40, "ref_kind": "branch"}

    git.run_fetch = record
    source = git_source.Source(URL, "main", "shop/compose.yaml", None, "2026-10-30T00:00:00+00:00")
    await git.sources.put(docker_on.host, "shop", source)
    await git.head(docker_on.host, "shop", source)
    saved = await client.put("/api/v1/extensions/docker/settings", headers=docker_on.owner, json={
        "current_password": "correct-horse-battery", "values": {"git_private_networks": True}})
    assert saved.status_code == 200, saved.text
    await git.head(docker_on.host, "shop", source)
    assert seen == [False, True]


@pytest.mark.asyncio
async def test_a_broken_state_file_counts_as_not_checked(client, docker_on, tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    _commit(origin)
    url = await _with_source(client, docker_on, origin)
    _notes(monkeypatch)
    (docker_on.data_dir / "git_watch.json").write_text("{kaputt", encoding="utf-8")
    assert (await client.get(f"{url}/watch", headers=docker_on.owner)).json() == {"watch": None}
    assert (await _instance()._git_watch.check_all())["saved"] is True
    assert (await client.get(f"{url}/watch", headers=docker_on.owner)).json()["watch"]["problem"] is None
