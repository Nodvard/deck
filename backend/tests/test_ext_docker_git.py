"""Stacks aus Git: Quelle prüfen und speichern, Holen ohne Arbeitskopie in eigenem Prozess, Vergleich mit dem Server."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from socketserver import ThreadingMixIn
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


def _gs():
    from nodvard_deck_ext_docker import git_source

    return git_source


def _gf():
    from nodvard_deck_ext_docker import git_fetch

    return git_fetch


URL = "https://git.example.org/team/stacks"
COMPOSE = b"services:\n  web:\n    image: busybox:1.37\n"
SHOWN = {"working_dir": "/opt/stacks/shop", "config_files": ["/opt/stacks/shop/compose.yaml"]}
POSIX_LIMITS = pytest.mark.skipif(sys.platform == "win32", reason="Grenzen für Speicher gibt es nur mit dem Modul resource")


def _tree(repo, entries):
    """Ein Baum aus `name -> Inhalt`; ein Tupel `(Modus, Inhalt)` setzt den Modus, auch einen, der nicht zum Inhalt passt."""
    from dulwich.objects import Blob, Tree

    tree = Tree()
    for name, value in entries.items():
        mode, data = value if isinstance(value, tuple) else (0o040000 if isinstance(value, dict) else 0o100644, value)
        if isinstance(data, dict):
            sha = _tree(repo, data).id
        elif mode == 0o160000:
            sha = data
        else:
            blob = Blob.from_string(data)
            repo.object_store.add_object(blob)
            sha = blob.id
        tree.add(name.encode(), mode, sha)
    repo.object_store.add_object(tree)
    return tree


def _commit(path: Path, entries, *, branch="main", message=b"Erster Stand\n\nmehr", tag=None, tree=None, when=1_700_000_000,
            author=b"Ada Lovelace <ada@example.org>") -> str:
    """Legt einen Commit an (und auf Wunsch ein Tag darauf); das Repository entsteht beim ersten Aufruf."""
    from dulwich.objects import Commit, Tag
    from dulwich.repo import Repo

    repo = Repo(str(path)) if path.exists() else Repo.init(str(path), mkdir=True)
    try:
        parent = repo.refs.as_dict().get(f"refs/heads/{branch}".encode())
        commit = Commit()
        commit.tree = tree(repo) if tree else _tree(repo, entries).id
        commit.parents = [parent] if parent else []
        commit.author = commit.committer = author
        commit.author_time = commit.commit_time = when
        commit.author_timezone = commit.commit_timezone = 3600
        commit.message = message
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


def _fetch(tmp_path, entries=None, *, ref="main", path="shop/compose.yaml", limits=None, origin=None, store=None):
    gf = _gf()
    origin = origin or tmp_path / "origin"
    if entries is not None:
        _commit(origin, entries)
    return gf.fetch(URL, ref, path, store or tmp_path / "store", None, _local(origin), limits or gf.Limits())


def _in_process(client_for):
    async def run(request, limits):
        return await asyncio.to_thread(_gf().handle, request, client_for)

    return run


# --- Eingaben ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("raw", "clean"), [
    ("https://github.com/team/stacks", "https://github.com/team/stacks"),
    ("  https://GitHub.com/team/stacks.git/ ", "https://github.com/team/stacks.git"),
    ("https://git.example.org:443/a/b", "https://git.example.org/a/b"),
    ("https://git.example.org:3000/a/b", "https://git.example.org:3000/a/b"),
    ("https://192.168.1.10:3000/a/b", "https://192.168.1.10:3000/a/b"),
    ("https://10.0.0.5/a/b", "https://10.0.0.5/a/b"),
    ("https://172.16.0.1/a/b", "https://172.16.0.1/a/b"),
    ("https://1password.example/a/b", "https://1password.example/a/b"),
    ("https://gitea.localdomain/a/b", "https://gitea.localdomain/a/b"),
])
def test_an_address_is_cleaned(raw, clean):
    assert _gs().check_url(raw) == clean


@pytest.mark.parametrize("raw", [
    "http://github.com/a/b", "ssh://git@github.com/a/b", "git@github.com:a/b.git", "file:///srv/repo", "ext::sh -c x",
    "https://user:" + "pw@github.com/a/b", "https://user@github.com/a/b", "https://github.com/a/b?x=1", "https://github.com/a/b#x",
    "https://github.com", "https://github.com/", "https://github.com/a/../b", "https://github.com/a b", "https://-x/a",
    "https://github.com:99999/a", "https://github.com/" + "a" * 520, "/srv/repo", "",
])
def test_unusable_addresses_are_refused(raw):
    with pytest.raises(ValueError):
        _gs().check_url(raw)


@pytest.mark.parametrize(("host", "reason"), [
    ("localhost", "Deck selbst"), ("api.localhost", "Deck selbst"), ("ip6-localhost", "Deck selbst"), ("ip6-loopback", "Deck selbst"),
    ("localhost.localdomain", "Deck selbst"), ("localhost4", "Deck selbst"), ("localhost6", "Deck selbst"), ("127.0.0.1", "Deck selbst"), ("127.8.9.10:3000", "Deck selbst"),
    ("0.0.0.0", "Deck selbst"), ("0.1.2.3", "Deck selbst"), ("169.254.169.254", "Deck selbst"), ("224.0.0.1", "Deck selbst"),
    ("255.255.255.255", "Deck selbst"), ("2130706433", "vier Zahlen"), ("0x7f.1", "vier Zahlen"), ("127.1", "vier Zahlen"),
    ("0177.0.0.1", "vier Zahlen"), ("git.0x7f", "vier Zahlen"),
])
def test_addresses_that_point_to_deck_itself_are_refused(host, reason):
    with pytest.raises(ValueError, match=reason):
        _gs().check_url(f"https://{host}/a/b")


@pytest.mark.parametrize("ref", ["main", "feature/neu", "v1.2.3", "release-2026_10"])
def test_usual_branch_and_tag_names(ref):
    assert _gs().check_ref(ref) == ref


@pytest.mark.parametrize("ref", ["refs/heads/main", "a..b", "a//b", "-x", "x.lock", "x/", "x.", "a@{1}", "a b", "", "/x", "a/.b"])
def test_odd_branch_names_are_refused(ref):
    with pytest.raises(ValueError):
        _gs().check_ref(ref)


@pytest.mark.parametrize("path", ["compose.yaml", "shop/compose.yml", "stacks/shop/docker-compose.yaml", "mein stack/compose.yaml"])
def test_usual_compose_paths(path):
    assert _gs().check_path(path) == path
    assert _gs().check_path(f"  {path} ") == path


@pytest.mark.parametrize("path", [
    "/compose.yaml", "../compose.yaml", "shop/../compose.yaml", "shop//compose.yaml", "./compose.yaml", ".git/compose.yaml",
    ".GIT/compose.yaml", "compose.txt", "shop\\compose.yaml", "shop /compose.yaml", "shop/ x.yaml", "a/b/c/d/e/f/g/h/i.yaml",
    "$(x).yaml", "",
])
def test_compose_paths_stay_inside_the_repository(path):
    with pytest.raises(ValueError):
        _gs().check_path(path)


def test_accesses_from_the_settings():
    entries = [
        {"name": "github", "host": "github.com", "username": "deck"},
        {"name": "gitea", "host": "git.example.org:443", "username": "ada"},
        {"name": "zweimal", "host": "a.example.org", "username": "x"},
        {"name": "zweimal", "host": "b.example.org", "username": "x"},
        {"name": "kaputt", "host": "https://github.com", "username": "x"},
        {"name": "doppelpunkt", "host": "github.com", "username": "a:b"},
        {"name": "port80", "host": "git.example.org:80", "username": "x"},
        "kein Eintrag",
    ]
    found = _gs().accesses({"git_accesses": entries})
    assert {name: (a.host, a.username) for name, a in found.items()} == {"github": ("github.com", "deck"), "gitea": ("git.example.org", "ada")}
    assert found["github"].secret_label == "docker-git-token:github"


# --- Speicher --------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sources_are_remembered_per_host_and_stack(tmp_path):
    gs = _gs()
    store = gs.GitSources(tmp_path)
    source = gs.Source("https://github.com/a/b", "main", "compose.yaml", "github", "2026-10-24T00:00:00+00:00")
    await store.put("h1", "shop", source)
    assert store.get("h1", "shop") == source
    assert store.get("h2", "shop") is None
    assert await store.remove("h1", "shop") is True
    assert await store.remove("h1", "shop") is False
    assert json.loads((tmp_path / "git_sources.json").read_text(encoding="utf-8")) == {"sources": {}}


@pytest.mark.parametrize("entry", [
    {"url": "http://github.com/a/b", "ref": "main", "path": "compose.yaml"},
    {"url": "https://github.com/a/b", "ref": "../x", "path": "compose.yaml"},
    {"url": "https://github.com/a/b", "ref": "main", "path": "../compose.yaml"},
    {"url": "https://github.com/a/b", "ref": "main", "path": "compose.yaml", "access": "mit leerzeichen"},
    "kaputt",
])
def test_a_hand_edited_source_is_ignored(tmp_path, entry):
    (tmp_path / "git_sources.json").write_text(json.dumps({"sources": {"h1": {"shop": entry}}}), encoding="utf-8")
    assert _gs().GitSources(tmp_path).get("h1", "shop") is None


@pytest.mark.asyncio
async def test_a_broken_store_is_set_aside_before_writing(tmp_path):
    gs = _gs()
    (tmp_path / "git_sources.json").write_text("{halb", encoding="utf-8")
    store = gs.GitSources(tmp_path)
    assert store.get("h1", "shop") is None
    await store.put("h1", "shop", gs.Source("https://github.com/a/b", "main", "compose.yaml", None, ""))
    assert [p.read_text(encoding="utf-8") for p in tmp_path.glob("git_sources.json.kaputt-*")] == ["{halb"]
    assert store.get("h1", "shop") is not None


@pytest.mark.asyncio
async def test_forgetting_a_stack_tells_the_hook_and_survives_its_failure(tmp_path, caplog):
    from nodvard_deck_ext_docker import adopt, stacks

    store = adopt.AdoptedStacks(tmp_path)
    await store.add("h1", stacks.Stack("shop", [], "/opt/stacks/shop", ("/opt/stacks/shop/compose.yaml",), (), "compose"))
    seen = []

    async def hook(host_id, project):
        seen.append((host_id, project))
        raise OSError("platt")

    store.on_remove = hook
    assert await store.remove("h1", "shop") is True
    assert seen == [("h1", "shop")] and "docker_stack_forget_failed" in caplog.text
    assert await store.remove("h1", "shop") is False and len(seen) == 1


# --- Holen -------------------------------------------------------------------------------------------------------------


def test_fetch_reads_the_folder_of_the_compose_file(tmp_path):
    fetched = _fetch(tmp_path, {"README.md": b"hi", "shop": {"compose.yaml": COMPOSE, "run.sh": (0o100755, b"#!/bin/sh\n"),
                                                               "config": {"app.ini": b"a=1\n"}}})
    assert (fetched.ref_kind, fetched.author, fetched.message, fetched.compose, fetched.problems, fetched.warnings) == (
        "branch", "Ada Lovelace", "Erster Stand", "compose.yaml", [], [])
    assert fetched.when == "2023-11-14T23:13:20+01:00"
    assert {path: (oct(mode), data) for path, (mode, data) in fetched.files.items()} == {
        "compose.yaml": ("0o644", COMPOSE), "run.sh": ("0o755", b"#!/bin/sh\n"), "config/app.ini": ("0o644", b"a=1\n")}


def test_fetch_from_the_top_of_the_repository(tmp_path):
    fetched = _fetch(tmp_path, {"compose.yaml": COMPOSE, ".env.example": b"A=1\n"}, path="compose.yaml")
    assert sorted(fetched.files) == [".env.example", "compose.yaml"]


def test_an_annotated_tag_is_followed_to_its_commit(tmp_path):
    commit = _commit(tmp_path / "origin", {"shop": {"compose.yaml": COMPOSE}}, tag="v1.0")
    fetched = _fetch(tmp_path, ref="v1.0")
    assert (fetched.commit, fetched.ref_kind) == (commit, "tag")


def test_a_branch_wins_over_a_tag_of_the_same_name(tmp_path):
    _commit(tmp_path / "origin", {"shop": {"compose.yaml": b"services: {}\n"}}, branch="x", tag="x")
    assert _fetch(tmp_path, ref="x").ref_kind == "branch"


def test_a_missing_branch_or_folder_is_a_problem(tmp_path):
    gf = _gf()
    _commit(tmp_path / "origin", {"shop": {"compose.yaml": COMPOSE}, "datei": b"x"})
    with pytest.raises(gf.GitProblem, match="keinen Branch und kein Tag „dev“"):
        _fetch(tmp_path, ref="dev")
    with pytest.raises(gf.GitProblem, match="den Ordner „blog“ nicht"):
        _fetch(tmp_path, path="blog/compose.yaml")
    with pytest.raises(gf.GitProblem, match="„datei“ ist im Repository kein Ordner"):
        _fetch(tmp_path, path="datei/compose.yaml")
    assert _fetch(tmp_path, path="shop/docker-compose.yml").problems == ["Im Repository gibt es die Datei „shop/docker-compose.yml“ nicht."]
    _commit(tmp_path / "origin", {"shop": {"compose.yaml": (0o120000, b"../x.yaml"), "a;b": b"x"}})
    assert _fetch(tmp_path).problems == [
        "„a;b“: Der Name enthält Zeichen, die Deck nicht auf einen Server schreibt.",
        "„compose.yaml“ ist ein Verweis (Symlink); Verweise holt Deck nicht.",
    ]
    _commit(tmp_path / "origin", {"shop": {"a;b": b"x"}})
    assert _fetch(tmp_path).problems[-1] == "Im Repository gibt es die Datei „shop/compose.yaml“ nicht."


def test_links_submodules_lfs_and_odd_names_are_refused(tmp_path):
    lfs = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 12\n"
    fetched = _fetch(tmp_path, {"shop": {
        "compose.yaml": COMPOSE, "link": (0o120000, b"../../etc/passwd"), "modul": (0o160000, b"1" * 40), "gross.bin": lfs,
        "a;b": b"x", "ok.txt": b"ok",
    }})
    assert sorted(fetched.files) == ["compose.yaml", "ok.txt"]
    assert sorted(fetched.problems) == sorted([
        "„link“ ist ein Verweis (Symlink); Verweise holt Deck nicht.",
        "„modul“ ist ein Submodul; Submodule holt Deck nicht.",
        "„gross.bin“ liegt in Git LFS; solche Dateien holt Deck nicht.",
        "„a;b“: Der Name enthält Zeichen, die Deck nicht auf einen Server schreibt.",
    ])


def test_odd_names_are_shown_short_and_without_control_characters(tmp_path):
    fetched = _fetch(tmp_path, {"shop": {"compose.yaml": COMPOSE, "a\u202eb\x1b[31m": b"x", "x" * 300 + ";": b"y"}})
    assert all("\u202e" not in line and "\x1b" not in line and len(line) < 200 for line in fetched.problems)
    assert any(line.startswith("„a?b?[31m“") for line in fetched.problems)


def test_author_and_message_are_shown_without_control_characters(tmp_path):
    _commit(tmp_path / "origin", {"shop": {"compose.yaml": COMPOSE}}, author=b"Ada \xe2\x80\xae Lovelace\x1b[31m <ada@example.org>",
            message=b"Neu\xe2\x80\xaeer Stand\x07\tdazu\n\nmehr")
    fetched = _fetch(tmp_path)
    assert (fetched.author, fetched.message) == ("Ada ? Lovelace?[31m", "Neu?er Stand? dazu")


def test_texts_from_the_server_are_shown_without_control_characters():
    from dulwich.errors import GitProtocolError

    gf = _gf()
    shown = [str(gf._fetch_problem(exc, URL, False)) for exc in (
        gf._ForeignOrigin("https://evil.example/\u202ex\x1b[2J"), GitProtocolError("kaputt\u202e\x1b[2J"), OSError("weg\u202e\x1b[2J"))]
    assert "https://evil.example/?x?[2J" in shown[0]
    assert shown[1].endswith(": kaputt??[2J")
    assert shown[2].endswith(": weg??[2J")


@pytest.mark.parametrize("odd", [(0o040000, b"eine Datei als Ordner"), (0o100644, {"a.txt": b"x"})])
def test_an_entry_of_the_wrong_kind_is_a_problem(tmp_path, odd):
    with pytest.raises(_gf().GitProblem, match="Ein Teil hat eine andere Art"):
        _fetch(tmp_path, {"shop": {"compose.yaml": COMPOSE, "falsch": odd}})


def test_limits_for_files_depth_size_and_entries(tmp_path):
    gf = _gf()
    fetched = _fetch(tmp_path, {"shop": {"compose.yaml": COMPOSE, **{f"f{i}": b"x" for i in range(5)}}}, limits=gf.Limits(files=3))
    assert fetched.problems == ["Der Ordner hat mehr als 3 Dateien oder mehr als 10 MB."]
    _commit(tmp_path / "origin2", {"compose.yaml": COMPOSE, "a": {"b": {"c.txt": b"x"}}})
    deep = _fetch(tmp_path, path="compose.yaml", limits=gf.Limits(depth=2), origin=tmp_path / "origin2", store=tmp_path / "store2")
    assert deep.problems == ["„a/b“ liegt tiefer als 2 Ordner."]
    _commit(tmp_path / "origin3", {"compose.yaml": os.urandom(4000)})
    with pytest.raises(gf.GitProblem, match="Das Repository ist größer als"):
        _fetch(tmp_path, path="compose.yaml", limits=gf.Limits(pack_bytes=100), origin=tmp_path / "origin3", store=tmp_path / "store3")
    assert not (tmp_path / "store3").exists() and not list(tmp_path.glob("store3.*"))


def test_a_tree_that_repeats_itself_stops_at_the_limit(tmp_path):
    """200 Einträge je Ordner auf denselben Unterordner, sechs Ebenen tief: wenige Bytes, aber 200 hoch 6 Wege."""

    def bomb(repo):
        from dulwich.objects import Blob, Tree

        blob = Blob.from_string(b"x")
        repo.object_store.add_object(blob)
        inner = Tree()
        inner.add(b"x.txt", 0o100644, blob.id)
        repo.object_store.add_object(inner)
        for _ in range(6):
            outer = Tree()
            for i in range(200):
                outer.add(f"d{i}".encode(), 0o040000, inner.id)
            repo.object_store.add_object(outer)
            inner = outer
        compose = Blob.from_string(COMPOSE)
        repo.object_store.add_object(compose)
        top = Tree()
        top.add(b"compose.yaml", 0o100644, compose.id)
        top.add(b"tief", 0o040000, inner.id)
        repo.object_store.add_object(top)
        return top.id

    _commit(tmp_path / "origin", None, tree=bomb)
    started = time.monotonic()
    fetched = _fetch(tmp_path, path="compose.yaml", limits=_gf().Limits(files=10**9, total_bytes=10**12))
    assert fetched.problems == ["Der Ordner hat mehr als 5000 Einträge."]
    assert time.monotonic() - started < 10


def test_only_the_last_fetched_state_is_kept(tmp_path):
    origin, store = tmp_path / "origin", tmp_path / "store"
    first = _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    for _ in range(2):
        assert _fetch(tmp_path).commit == first
    second = _commit(origin, {"shop": {"compose.yaml": COMPOSE + b"    restart: always\n"}}, message=b"Neustart")
    fetched = _fetch(tmp_path)
    assert (fetched.commit, fetched.message) == (second, "Neustart")
    assert fetched.files["compose.yaml"][1].endswith(b"restart: always\n")
    assert len(list((store / "objects" / "pack").glob("*.pack"))) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["origin", "store"]


def test_a_failed_fetch_leaves_the_last_state_and_no_rest(tmp_path):
    gf = _gf()
    origin = tmp_path / "origin"
    first = _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    _fetch(tmp_path)
    _commit(origin, {"shop": {"compose.yaml": os.urandom(20000)}})
    with pytest.raises(gf.GitProblem, match="größer als"):
        _fetch(tmp_path, limits=gf.Limits(pack_bytes=5000))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["origin", "store"]
    from dulwich.repo import Repo

    with Repo(str(tmp_path / "store")) as repo:
        assert repo.refs[gf.FETCHED_REF].decode() == first


def test_a_broken_store_is_made_new(tmp_path):
    store = tmp_path / "store"
    (store / "objects" / "pack").mkdir(parents=True)
    (store / "HEAD").write_text("kaputt", encoding="utf-8")
    (store / "objects" / "pack" / "pack-x.idx").write_bytes(b"kein index")
    (tmp_path / "store.neu-abcd").mkdir()
    assert _fetch(tmp_path, {"shop": {"compose.yaml": COMPOSE}}).files
    assert sorted(p.name for p in tmp_path.iterdir()) == ["origin", "store"]


@pytest.mark.parametrize("damage", ["idx", "pack"])
def test_a_damaged_store_heals_on_the_next_fetch(tmp_path, damage):
    first = _commit(tmp_path / "origin", {"shop": {"compose.yaml": COMPOSE}})
    _fetch(tmp_path)
    [packed] = (tmp_path / "store" / "objects" / "pack").glob(f"*.{damage}")
    packed.chmod(0o644)  # Pack-Dateien sind schreibgeschützt; nur root könnte sie sonst beschädigen.
    data = bytearray(packed.read_bytes())
    data[12:-20] = b"\xff" * (len(data) - 32)
    packed.write_bytes(bytes(data))
    fetched = _fetch(tmp_path)
    assert fetched.commit == first and fetched.files["compose.yaml"][1] == COMPOSE
    assert sorted(p.name for p in tmp_path.iterdir()) == ["origin", "store"]


def test_the_last_state_is_reused_and_kept_on_ordinary_problems(tmp_path):
    gf = _gf()
    _commit(tmp_path / "origin", {"shop": {"compose.yaml": COMPOSE}})
    _fetch(tmp_path)
    before = (tmp_path / "store").stat().st_ino
    assert _fetch(tmp_path).files and (tmp_path / "store").stat().st_ino == before
    with pytest.raises(gf.GitProblem, match="den Ordner „blog“ nicht"):
        _fetch(tmp_path, path="blog/compose.yaml")
    assert (tmp_path / "store").stat().st_ino == before


@pytest.mark.parametrize("when", [2**40, -(2**40)])
def test_an_odd_date_does_not_break_the_fetch(tmp_path, when):
    _commit(tmp_path / "origin", {"shop": {"compose.yaml": COMPOSE}}, when=when)
    assert _fetch(tmp_path).when == ""


@contextmanager
def _no_programs(monkeypatch):
    """Jeder Versuch, ein Programm zu starten, schlägt fehl und wird gezählt."""
    started: list[str] = []

    def refuse(name):
        def run(*args, **kwargs):
            started.append(name)
            raise AssertionError(f"{name} gestartet")
        return run

    for module, name in [(subprocess, "Popen"), (os, "system"), (os, "posix_spawn"), (os, "posix_spawnp"), (os, "fork"),
                         (os, "execv"), (os, "execve"), (os, "execvp"), (os, "popen")]:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, refuse(name))
    yield started
    monkeypatch.undo()


def test_nothing_from_the_repository_runs(tmp_path, monkeypatch):
    """Filter aus .gitattributes, Hooks und Einstellungen greifen nicht, und kein Programm startet."""
    marker = tmp_path / "gelaufen"
    script = f"sh -c 'touch {marker}'"
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".gitconfig"
    config.write_text(
        f'[filter "boom"]\n\tsmudge = {script}\n\tclean = {script}\n\trequired = true\n'
        f"[core]\n\tfsmonitor = {script}\n\thooksPath = {home}\n\tsshCommand = {script}\n"
        f'[credential]\n\thelper = !{script}\n[uploadpack]\n\tpackObjectsHook = {script}\n', encoding="utf-8")
    origin = tmp_path / "origin"
    _commit(origin, {".gitattributes": b"* filter=boom\n", "shop": {"compose.yaml": COMPOSE, ".gitattributes": b"* filter=boom diff=boom\n"}})
    for hook in ("post-checkout", "post-merge", "pre-receive", "post-receive", "post-update", "reference-transaction"):
        for folder in (origin / ".git" / "hooks", home):
            (folder / hook).write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
            (folder / hook).chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    with _no_programs(monkeypatch) as started:
        fetched = _fetch(tmp_path)
    assert started == [] and not marker.exists()
    assert fetched.files["compose.yaml"][1] == COMPOSE and fetched.files[".gitattributes"][1] == b"* filter=boom diff=boom\n"
    assert not list((tmp_path / "store").rglob("compose.yaml"))


# --- Über HTTP ---------------------------------------------------------------------------------------------------------


class _Threaded(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def _serve(app, host="127.0.0.1"):
    server = make_server(host, 0, app, server_class=_Threaded, handler_class=_Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _git_app(origin: Path):
    from dulwich.repo import Repo
    from dulwich.server import DictBackend
    from dulwich.web import make_wsgi_chain

    return make_wsgi_chain(DictBackend({"/team/stacks": Repo(str(origin))}))


@pytest.fixture
def no_proxy(monkeypatch):
    for name in ("https_proxy", "http_proxy", "all_proxy", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)


def _over_http(base: str, path: str = "/team/stacks"):
    return lambda url, credentials, limits: _gf().http_client(f"{base}{path}", credentials, limits)


def _http_fetch(tmp_path, base, credentials=None, path="/team/stacks", store="store", limits=None):
    gf = _gf()
    return gf.fetch(URL, "main", "shop/compose.yaml", tmp_path / store, credentials, _over_http(base, path), limits or gf.Limits())


def test_fetch_over_http_without_programs(tmp_path, monkeypatch, no_proxy):
    origin = tmp_path / "origin"
    commit = _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    with _serve(_git_app(origin)) as base, _no_programs(monkeypatch) as started:
        fetched = _http_fetch(tmp_path, base)
    assert (fetched.commit, fetched.files["compose.yaml"][1], started) == (commit, COMPOSE, [])


def test_a_token_goes_to_the_server_and_a_refusal_is_explained(tmp_path, no_proxy):
    gf = _gf()
    origin = tmp_path / "origin"
    _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    token = "tok" + "en-" + "4711"
    expected = "Basic " + base64.b64encode(f"deck:{token}".encode()).decode()
    inner = _git_app(origin)
    seen: list[str | None] = []

    def app(environ, start_response):
        seen.append(environ.get("HTTP_AUTHORIZATION"))
        if environ.get("HTTP_AUTHORIZATION") != expected:
            start_response("401 Unauthorized", [("WWW-Authenticate", 'Basic realm="git"')])
            return [b""]
        return inner(environ, start_response)

    with _serve(app) as base:
        with pytest.raises(gf.GitProblem, match="verlangt eine Anmeldung"):
            _http_fetch(tmp_path, base)
        with pytest.raises(gf.GitProblem, match="hat den Zugang abgelehnt"):
            _http_fetch(tmp_path, base, ("deck", "falsch"))
        assert _http_fetch(tmp_path, base, ("deck", token)).files
    assert seen[0] is None and expected in seen


def test_redirects_stay_on_the_same_server(tmp_path, no_proxy):
    gf = _gf()
    origin = tmp_path / "origin"
    _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    inner = _git_app(origin)
    elsewhere: list[dict] = []

    def foreign(environ, start_response):
        elsewhere.append({"path": environ["PATH_INFO"], "auth": environ.get("HTTP_AUTHORIZATION")})
        start_response("404 Not Found", [])
        return [b""]

    with _serve(foreign, host="127.0.0.2") as other:
        def app(environ, start_response):
            path = environ["PATH_INFO"]
            if path.startswith("/old/"):
                start_response("301 Moved Permanently", [("Location", path.replace("/old/", "/team/", 1) + "?" + environ.get("QUERY_STRING", ""))])
                return [b""]
            if path.startswith("/away/"):
                start_response("302 Found", [("Location", f"{other}/team/stacks/info/refs?service=git-upload-pack")])
                return [b""]
            return inner(environ, start_response)

        with _serve(app) as base:
            assert _http_fetch(tmp_path, base, ("deck", "x"), path="/old/stacks").files
            with pytest.raises(gf.GitProblem, match="zu einer anderen Adresse"):
                _http_fetch(tmp_path, base, ("deck", "x"), path="/away/stacks", store="store2")
    assert elsewhere == []


def test_a_redirect_while_fetching_or_without_end_is_refused(tmp_path, no_proxy):
    gf = _gf()
    origin = tmp_path / "origin"
    _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    inner = _git_app(origin)

    def app(environ, start_response):
        if environ["REQUEST_METHOD"] == "POST":
            start_response("307 Temporary Redirect", [("Location", environ["PATH_INFO"] + "-neu")])
            return [b""]
        if environ["PATH_INFO"].startswith("/kreis/"):
            start_response("302 Found", [("Location", environ["PATH_INFO"] + "?" + environ.get("QUERY_STRING", ""))])
            return [b""]
        return inner(environ, start_response)

    with _serve(app) as base:
        with pytest.raises(gf.GitProblem, match="leitet beim Holen auf eine andere Adresse"):
            _http_fetch(tmp_path, base)
        with pytest.raises(gf.GitProblem, match="leitet zu oft weiter"):
            _http_fetch(tmp_path, base, path="/kreis/stacks", store="store2")


def test_no_request_leaves_the_entered_server():
    """Auch ein vom Server genanntes Pack an anderer Adresse (packfile-uris) holt Deck nicht."""
    gf = _gf()
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(get_redirect_location=lambda: False, read=lambda amt=None: b"")

    pool = gf._SameOrigin(SimpleNamespace(headers={}, request=request), "https://git.example.org/team/stacks", gf.Limits())
    for url in ("https://elsewhere.example.org/pack", "https://git.example.org:8443/pack", "http://git.example.org/pack"):
        with pytest.raises(gf._ForeignOrigin):
            pool.request("GET", url)
    assert calls == []
    pool.request("POST", "https://GIT.example.org:443/team/stacks/git-upload-pack", body=b"x")
    # Keine Wiederholung durch urllib3: Sonst liefe jede Anfrage bis zu viermal so lange wie die Frist.
    assert calls == [("POST", "https://GIT.example.org:443/team/stacks/git-upload-pack", {"redirect": False, "retries": False, "body": b"x"})]


def _answer(body, content_type="application/x-git-upload-pack-advertisement", pause=0.0):
    def app(environ, start_response):
        start_response("200 OK", [("Content-Type", content_type)])

        def chunks():
            for chunk in body:
                time.sleep(pause)
                yield chunk

        return chunks()

    return app


def test_a_dumb_server_is_refused(tmp_path, no_proxy):
    with _serve(_answer([b"0" * 40 + b"\trefs/heads/main\n"], content_type="text/plain")) as base, \
            pytest.raises(_gf().GitProblem, match="dumb HTTP"):
        _http_fetch(tmp_path, base)


def test_a_huge_or_slow_answer_is_cut_off(tmp_path, no_proxy):
    gf = _gf()
    with _serve(_answer([b"0fff" + b"x" * 4091])) as base, pytest.raises(gf.GitProblem, match="Der Server schickt mehr als"):
        _http_fetch(tmp_path, base, limits=gf.Limits(refs_bytes=2000))
    line = b"001e# service=git-upload-pack\n"
    with _serve(_answer([line[i:i + 4] for i in range(0, len(line), 4)] * 20, pause=0.1)) as base:
        started = time.monotonic()
        with pytest.raises(gf.GitProblem, match="länger als 0.4 Sekunden"):
            _http_fetch(tmp_path, base, store="store2", limits=gf.Limits(seconds=0.4))
    assert time.monotonic() - started < 3


def test_a_flood_of_progress_messages_is_cut_off(tmp_path, no_proxy):
    """Fortschrittsmeldungen zählen nicht zum Pack; ohne Grenze je Antwort liefe Deck bis zur Frist."""
    gf = _gf()
    caps = b"shallow side-band-64k ofs-delta agent=test"

    def app(environ, start_response):
        if environ["PATH_INFO"].endswith("/info/refs"):
            start_response("200 OK", [("Content-Type", "application/x-git-upload-pack-advertisement")])
            return [_pkt(b"# service=git-upload-pack\n") + b"0000" + _pkt(b"1" * 40 + b" refs/heads/main\0" + caps + b"\n") + b"0000"]
        environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0))
        start_response("200 OK", [("Content-Type", "application/x-git-upload-pack-result")])
        return [b"0000" + _pkt(b"NAK\n")] + [_pkt(b"\x02" + b"Fortschritt " * 5000) for _ in range(40)]

    started = time.monotonic()
    with _serve(app) as base, pytest.raises(gf.GitProblem, match="Der Server schickt mehr als"):
        _http_fetch(tmp_path, base, limits=gf.Limits(response_bytes=200_000))
    assert time.monotonic() - started < 10


def test_a_server_that_does_not_answer_is_a_problem(tmp_path, no_proxy):
    gf = _gf()
    with pytest.raises(gf.GitProblem, match="keine Verbindung an|nicht erreichbar|antwortet nicht"):
        gf.fetch(URL, "main", "compose.yaml", tmp_path / "store", None, _over_http("http://127.0.0.1:9"), gf.Limits())


@pytest.mark.parametrize(("text", "human"), [
    (("HTTPSConnectionPool(host='x', port=443): Max retries exceeded (Caused by SSLError(SSLCertVerificationError(1, "
      "'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate')))"), "nicht vertrauenswürdig"),
    ("HTTPSConnectionPool(host='x'): Max retries exceeded (Caused by NameResolutionError(...))", "kennt das Netz nicht"),
    ("Caused by ConnectTimeoutError(...)", "antwortet nicht"),
    ("Caused by NewConnectionError('...: Failed to establish a new connection: [Errno 111] Connection refused')", "keine Verbindung an"),
    (("Caused by NewConnectionError('...: Failed to establish a new connection: [WinError 10061] No connection could be made "
      "because the target machine actively refused it')"), "nicht erreichbar"),
    ("('Unable to connect to proxy', OSError('Tunnel connection failed: 502 Bad Gateway'))", "Der Proxy"),
    ("unexpected http resp 403 for https://git.example.org/x", "Fehler 403 geantwortet. Der Server verweigert"),
    ("unexpected http resp 503 for https://git.example.org/x", "Störung beim Git-Server"),
    ("unexpected http resp 418 for https://git.example.org/x", "Fehler 418 geantwortet. Prüf die Adresse."),
    ("server does not support shallow capability required for depth", "(shallow)"),
])
def test_network_errors_are_told_in_plain_words(text, human):
    from dulwich.errors import GitProtocolError

    assert human in str(_gf()._fetch_problem(GitProtocolError(text), URL, False))


# --- Eigener Prozess ---------------------------------------------------------------------------------------------------


def _pkt(data: bytes) -> bytes:
    return b"%04x" % (len(data) + 4) + data


def _bomb_server(size: int):
    """Ein Git-Server, dessen Pack eine Compose-Datei aus `size` Nullbytes enthält, gepackt nur wenige KB."""
    blob_hash = hashlib.sha1(b"blob %d\0" % size)
    for _ in range(size // (1 << 20)):
        blob_hash.update(bytes(1 << 20))
    tree = b"100644 compose.yaml\0" + blob_hash.digest()
    tree_sha = hashlib.sha1(b"tree %d\0" % len(tree) + tree).hexdigest()
    commit = (f"tree {tree_sha}\nauthor A <a@example.org> 1700000000 +0000\ncommitter A <a@example.org> 1700000000 +0000\n\nbombe\n").encode()
    commit_sha = hashlib.sha1(b"commit %d\0" % len(commit) + commit).hexdigest()

    def header(kind: int, length: int) -> bytes:
        out, first, length = bytearray(), (kind << 4) | (length & 0x0F), length >> 4
        while length:
            out.append(first | 0x80)
            first, length = length & 0x7F, length >> 7
        out.append(first)
        return bytes(out)

    def pack():
        digest = hashlib.sha1()
        packer = zlib.compressobj(9)
        parts = [b"PACK" + struct.pack(">LL", 2, 3), header(1, len(commit)) + zlib.compress(commit),
                 header(2, len(tree)) + zlib.compress(tree), header(3, size)]
        parts += [packer.compress(bytes(1 << 20)) for _ in range(size // (1 << 20))] + [packer.flush()]
        for part in parts:
            digest.update(part)
        return b"".join(parts) + digest.digest()

    body = pack()
    caps = b"shallow side-band-64k ofs-delta thin-pack agent=test"

    def app(environ, start_response):
        if environ["PATH_INFO"].endswith("/info/refs"):
            start_response("200 OK", [("Content-Type", "application/x-git-upload-pack-advertisement")])
            return [_pkt(b"# service=git-upload-pack\n") + b"0000" + _pkt(commit_sha.encode() + b" refs/heads/main\0" + caps + b"\n") + b"0000"]
        environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0))
        start_response("200 OK", [("Content-Type", "application/x-git-upload-pack-result")])
        return [b"0000" + _pkt(b"NAK\n") + b"".join(_pkt(b"\x01" + body[i:i + 65000]) for i in range(0, len(body), 65000)) + b"0000"]

    return app


def _request(tmp_path, base, **limits):
    return {"url": f"{base}/team/stacks", "ref": "main", "path": "shop/compose.yaml", "store": str(tmp_path / "store"),
            "credentials": None, "limits": limits}


@pytest.mark.asyncio
async def test_the_worker_process_fetches_and_answers(tmp_path, no_proxy):
    gs = _gs()
    origin = tmp_path / "origin"
    commit = _commit(origin, {"shop": {"compose.yaml": COMPOSE + b"    volumes: [/srv:/srv]\n"}})
    with _serve(_git_app(origin)) as base:
        fetched = gs.parse_answer(await asyncio.wait_for(gs.run_worker(_request(tmp_path, base), gs.LIMITS), 60), gs.LIMITS)
    assert fetched.commit == commit and fetched.files["compose.yaml"][1].startswith(COMPOSE)
    assert fetched.warnings == ["„web“ bindet Ordner oder Dateien vom Server ein, die nicht aus dem Repository kommen: /srv."]
    assert (tmp_path / "store" / "HEAD").is_file()
    if sys.platform != "win32":
        assert all(path.stat().st_mode & 0o077 == 0 for path in (tmp_path / "store").rglob("*"))


@POSIX_LIMITS
@pytest.mark.asyncio
async def test_a_pack_bomb_hits_the_memory_limit_of_the_worker(tmp_path, no_proxy):
    gs, gf = _gs(), _gf()
    limits = gf.Limits(memory_bytes=400 * 1024 * 1024)
    with _serve(_bomb_server(800 * 1024 * 1024)) as base:
        started = time.monotonic()
        with pytest.raises(gf.GitProblem, match="Speicher"):
            gs.parse_answer(await asyncio.wait_for(gs.run_worker(_request(tmp_path, base, memory_bytes=limits.memory_bytes), limits), 60), limits)
    assert time.monotonic() - started < 60
    assert not (tmp_path / "store").exists() or not list((tmp_path / "store" / "objects" / "pack").glob("*.pack"))


@pytest.mark.asyncio
async def test_a_hanging_worker_is_ended(tmp_path, monkeypatch, no_proxy):
    gs, gf = _gs(), _gf()
    monkeypatch.setattr(gs, "GRACE_S", 1)
    limits = gf.Limits(seconds=2)
    started_processes = []
    spawn = asyncio.create_subprocess_exec

    async def keep(*args, **kwargs):
        started_processes.append(_WaitNeedsEmptyPipes(await spawn(*args, **kwargs)))
        return started_processes[-1]

    monkeypatch.setattr(gs.asyncio, "create_subprocess_exec", keep)
    # Die Länge der Zeile kommt sofort, ihr Inhalt ein Byte je halbe Sekunde: Dieses eine Lesen endet erst nach 22 Sekunden.
    with _serve(_answer([b"0030"] + [b"#"] * 44, pause=0.5)) as base:
        started = time.monotonic()
        with pytest.raises(gf.GitProblem, match="länger als 2 Sekunden"):
            await asyncio.wait_for(gs.run_worker(_request(tmp_path, base, seconds=2), limits), 20)
    assert time.monotonic() - started < 8
    assert [proc.returncode is not None for proc in started_processes] == [True]


GOOD = {"ok": True, "commit": "a" * 40, "ref_kind": "branch", "author": "A", "when": "", "message": "m", "compose": "compose.yaml",
        "files": [{"path": "conf/a.ini", "mode": "755", "data": base64.b64encode(b"x").decode()}], "problems": [], "warnings": []}


def test_a_good_answer_of_the_worker_is_read():
    fetched = _gs().parse_answer(GOOD, _gs().LIMITS)
    assert (fetched.commit, fetched.files) == ("a" * 40, {"conf/a.ini": (0o755, b"x")})


@pytest.mark.parametrize(("change", "limits"), [
    ({"files": [{"path": f"f{i}", "mode": "644", "data": ""} for i in range(301)]}, {}),
    ({}, {"total_bytes": 0}),
    ({"files": [{"path": "a/b/c/d/e/f/g/h/i", "mode": "644", "data": ""}]}, {}),
    ({"author": "A" * 301}, {}),
    ({"compose": "../compose.yaml"}, {}),
])
def test_an_answer_beyond_the_limits_is_refused(change, limits):
    with pytest.raises(_gf().GitProblem, match="unlesbar"):
        _gs().parse_answer({**GOOD, **change}, _gf().Limits(**limits))


@pytest.mark.parametrize("change", [
    {"files": [{"path": "../x", "mode": "644", "data": ""}]},
    {"files": [{"path": "a//b", "mode": "644", "data": ""}]},
    {"files": [{"path": "x", "mode": "777", "data": ""}]},
    {"files": [{"path": "x", "mode": "644", "data": "kein base64!"}]},
    {"files": None},
    {"commit": "x"},
    {"ref_kind": "irgendwas"},
    {"problems": 5},
])
def test_an_odd_answer_of_the_worker_is_refused(change):
    with pytest.raises(_gf().GitProblem, match="unlesbar"):
        _gs().parse_answer({**GOOD, **change}, _gs().LIMITS)


def test_texts_in_the_answer_of_the_worker_are_cleaned_again():
    gs = _gs()
    fetched = gs.parse_answer({**GOOD, "author": "A\u202eB", "message": "m\x1b[2J", "compose": "docker  compose.yaml",
                               "problems": ["p\u202e"], "warnings": ["w\x07"]}, gs.LIMITS)
    assert (fetched.author, fetched.message, fetched.compose, fetched.problems, fetched.warnings) == (
        "A?B", "m?[2J", "docker  compose.yaml", ["p?"], ["w?"])
    with pytest.raises(_gf().GitProblem, match="^kaputt\\?$"):
        gs.parse_answer({"ok": False, "problem": "kaputt\u202e"}, gs.LIMITS)


def test_the_reason_from_the_worker_is_passed_on(caplog):
    gs = _gs()
    with pytest.raises(_gf().GitProblem, match="^Holen ist gescheitert \\(KeyError\\).$"):
        gs.parse_answer({"ok": False, "problem": "Holen ist gescheitert (KeyError).", "error": "KeyError"}, gs.LIMITS)
    assert "docker_git_fetch_error error='KeyError'" in caplog.text


def test_the_worker_gets_only_what_it_needs_from_the_environment(monkeypatch):
    for name in ("https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (("GIT_DIR", "/elsewhere"), ("git_ssh_command", "x"), ("PYTHONPATH", "/elsewhere"), ("LD_PRELOAD", "/x.so"),
                        ("NODVARD_DECK_SECRET_KEY", "x"), ("HTTPS_PROXY", "http://proxy.example.org:3128"), ("no_proxy", "localhost"),
                        ("LANG", "de_DE.UTF-8"), ("LC_ALL", "C.UTF-8"), ("SSL_CERT_FILE", "/etc/ssl/ca.pem")):
        monkeypatch.setenv(name, value)
    env = {k.upper(): v for k, v in _gs()._worker_env().items()}
    assert not {"GIT_DIR", "GIT_SSH_COMMAND", "PYTHONPATH", "LD_PRELOAD", "NODVARD_DECK_SECRET_KEY"} & set(env)
    assert {k: env[k] for k in ("HTTPS_PROXY", "NO_PROXY", "LANG", "LC_ALL", "SSL_CERT_FILE")} == {
        "HTTPS_PROXY": "http://proxy.example.org:3128", "NO_PROXY": "localhost", "LANG": "de_DE.UTF-8", "LC_ALL": "C.UTF-8",
        "SSL_CERT_FILE": "/etc/ssl/ca.pem"}


@pytest.mark.asyncio
async def test_the_worker_ignores_files_next_to_it(tmp_path, monkeypatch, no_proxy):
    """Mit `-I` kommt kein Modul aus dem Ordner des Skripts, auch kein gleichnamiges wie json.py."""
    gs = _gs()
    folder = tmp_path / "skript"
    folder.mkdir()
    shutil.copy(gs.WORKER, folder / "git_fetch.py")
    marker = tmp_path / "gelaufen"
    for name in ("json.py", "yaml.py", "secrets.py"):
        (folder / name).write_text(f"open({str(marker)!r}, 'a').write({name!r})\n", encoding="utf-8")
    monkeypatch.setattr(gs, "WORKER", folder / "git_fetch.py")
    origin = tmp_path / "origin"
    _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    with _serve(_git_app(origin)) as base:
        fetched = gs.parse_answer(await asyncio.wait_for(gs.run_worker(_request(tmp_path, base), gs.LIMITS), 60), gs.LIMITS)
    assert fetched.files and not marker.exists()


@pytest.mark.asyncio
async def test_a_worker_that_talks_too_much_or_dies_early_is_a_problem(tmp_path, monkeypatch, caplog):
    gs, gf = _gs(), _gf()
    loud = tmp_path / "laut.py"
    loud.write_text("import sys\nsys.stdout.write('x' * 3_000_000)\n", encoding="utf-8")
    monkeypatch.setattr(gs, "WORKER", loud)
    with pytest.raises(gf.GitProblem, match="unlesbar"):
        await asyncio.wait_for(gs.run_worker({}, gf.Limits(total_bytes=1000)), 20)
    dying = tmp_path / "stirbt.py"
    dying.write_text("import sys\nsys.stderr.write('Kaputt beim Start' * 1000)\nsys.exit(1)\n", encoding="utf-8")
    monkeypatch.setattr(gs, "WORKER", dying)
    with pytest.raises(gf.GitProblem, match="abgebrochen"):
        await asyncio.wait_for(gs.run_worker({}, gf.Limits()), 20)
    assert "Kaputt beim Start" in caplog.text and len(caplog.text) < 5000


class _ReadToEnd:
    def __init__(self, stream):
        self._stream, self.ended = stream, False

    async def read(self, n=-1):
        chunk = await self._stream.read(n)
        self.ended = self.ended or not chunk
        return chunk


class _WaitNeedsEmptyPipes:
    """`wait()`, das erst zurückkehrt, wenn beide Ausgaben bis zum Ende gelesen sind; so hängt es auch mit echten Rohren."""

    def __init__(self, proc):
        self._proc, self.pid, self.stdin = proc, proc.pid, proc.stdin
        self.stdout, self.stderr = _ReadToEnd(proc.stdout), _ReadToEnd(proc.stderr)

    @property
    def returncode(self):
        return self._proc.returncode

    def kill(self):
        self._proc.kill()

    async def wait(self):
        while not (self.stdout.ended and self.stderr.ended):
            await asyncio.sleep(0.05)
        return await self._proc.wait()


@pytest.mark.asyncio
async def test_a_talkative_worker_ends_even_where_waiting_needs_empty_pipes(tmp_path, monkeypatch, caplog):
    gs, gf = _gs(), _gf()
    loud = tmp_path / "laut.py"
    loud.write_text("import sys\nwhile True:\n    sys.stdout.write('x' * 65536)\n", encoding="utf-8")
    monkeypatch.setattr(gs, "WORKER", loud)
    spawn = asyncio.create_subprocess_exec

    async def strict(*args, **kwargs):
        return _WaitNeedsEmptyPipes(await spawn(*args, **kwargs))

    monkeypatch.setattr(gs.asyncio, "create_subprocess_exec", strict)
    with pytest.raises(gf.GitProblem, match="unlesbar"):
        await asyncio.wait_for(gs.run_worker({}, gf.Limits(total_bytes=1000)), 20)
    assert "docker_git_worker_stuck" not in caplog.text


@pytest.mark.asyncio
async def test_ending_the_worker_reads_its_pipes_empty_and_gives_up_after_a_while(monkeypatch, caplog):
    gs = _gs()
    monkeypatch.setattr(gs, "REAP_S", 0.3)

    class Proc:
        pid = 4711

        def __init__(self, stuck):
            self.stuck, self.killed = stuck, False
            self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
            self.stdout.feed_data(b"x" * 300_000)
            self.stdout.feed_eof()
            self.stderr.feed_eof()

        def kill(self):
            self.killed = True
            raise ProcessLookupError

        async def wait(self):
            if self.stuck:
                await asyncio.Event().wait()
            assert self.stdout.at_eof() and self.stderr.at_eof()
            return -9

    done = Proc(stuck=False)
    await gs._reap(done)
    assert done.killed and "docker_git_worker_stuck" not in caplog.text
    started = time.monotonic()
    await gs._reap(Proc(stuck=True))
    assert time.monotonic() - started < 3 and "docker_git_worker_stuck pid=4711" in caplog.text


@pytest.mark.asyncio
async def test_a_read_error_on_a_pipe_ends_reading_without_a_crash():
    gs = _gs()

    class Stdin:
        def write(self, data):
            pass

        async def drain(self):
            pass

        def close(self):
            pass

    class Proc:
        pid, stdin = 4712, Stdin()

        def __init__(self, broken):
            self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
            getattr(self, broken).set_exception(OSError("Lesefehler"))
            other = self.stderr if broken == "stdout" else self.stdout
            other.feed_data(b"Grund")
            other.feed_eof()

        def kill(self):
            pass

        async def wait(self):
            return 1

    assert await gs._exchange(Proc("stdout"), b"{}", 1000) == (b"", b"Grund")
    assert await gs._exchange(Proc("stderr"), b"{}", 1000) == (b"Grund", b"")
    for broken in ("stdout", "stderr"):
        await gs._reap(Proc(broken))


@pytest.mark.asyncio
async def test_a_worker_that_does_not_read_its_order_is_a_problem(tmp_path, monkeypatch, caplog):
    gs, gf = _gs(), _gf()
    deaf = tmp_path / "taub.py"
    deaf.write_text("import os, sys, time\nos.close(0)\nsys.stderr.write('Auftrag nicht gelesen')\nsys.stderr.flush()\ntime.sleep(0.5)\n",
                    encoding="utf-8")
    monkeypatch.setattr(gs, "WORKER", deaf)
    with pytest.raises(gf.GitProblem, match="abgebrochen"):
        await asyncio.wait_for(gs.run_worker({"rest": "x" * 2_000_000}, gf.Limits()), 20)
    assert "Auftrag nicht gelesen" in caplog.text


@POSIX_LIMITS
def test_the_worker_limits_itself():
    # Vorher Kern-Abzüge erlauben, soweit die harte Grenze es zulässt: Sonst fiele nicht auf, wenn _confine sie nicht abschaltet.
    code = ("import os, resource, sys; sys.path.insert(0, sys.argv[1]); import git_fetch as g; "
            "hard = resource.getrlimit(resource.RLIMIT_CORE)[1]; "
            "resource.setrlimit(resource.RLIMIT_CORE, (1 << 20 if hard == resource.RLIM_INFINITY else hard, hard)); "
            "g._confine(g.Limits(seconds=7, pack_bytes=1000, memory_bytes=300 * 1024 * 1024)); "
            "print([resource.getrlimit(getattr(resource, n))[0] for n in ('RLIMIT_AS', 'RLIMIT_CPU', 'RLIMIT_FSIZE', 'RLIMIT_CORE')], "
            "oct(os.umask(0)))")
    out = subprocess.run([sys.executable, "-I", "-c", code, str(EXT_DIR / "src" / "nodvard_deck_ext_docker")],
                         capture_output=True, text=True, timeout=60, check=True)
    assert out.stdout.split() == ["[314572800,", "12,", "16779216,", "0]", "0o77"]


@pytest.mark.asyncio
async def test_the_worker_takes_the_proxy_also_in_capitals(tmp_path, monkeypatch, no_proxy):
    gs = _gs()
    origin = tmp_path / "origin"
    _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    with _serve(_git_app(origin)) as base, pytest.raises(_gf().GitProblem, match="Der Proxy"):
        gs.parse_answer(await asyncio.wait_for(gs.run_worker(_request(tmp_path, base), gs.LIMITS), 60), gs.LIMITS)


# --- Warnungen aus der Compose-Datei -------------------------------------------------------------------------------------


def test_what_comes_from_outside_the_folder_is_named():
    text = b"""
include:
  - ../common/compose.yaml
  - path: [./inner.yaml, /srv/other.yaml]
  - oci://registry.example.org/compose:1
  - git@git.example.org:team/common.git
services:
  web:
    image: nginx
    extends: {file: ../base.yaml, service: web}
    env_file: [./ok.env, ../shared.env, {path: /etc/app.env, required: false}]
    volumes:
      - ./data:/data
      - /srv/media:/media:ro
      - $HOME/x:/x
      - ~/y:/y
      - named:/named
      - type: bind
        source: ../../secret
        target: /s
      - type: volume
        source: vol
        target: /v
  worker:
    build: ../src
  local:
    build: {context: ., dockerfile: Dockerfile}
    extends: web
secrets:
  key: {file: ../key.txt}
configs:
  conf: {file: ./conf.ini}
"""
    warnings, problem = _gf().compose_warnings(text)
    assert problem is None
    assert warnings == [
        "Die Datei bindet eine weitere Compose-Datei von außerhalb des Ordners ein (include: ../common/compose.yaml).",
        "Die Datei bindet eine weitere Compose-Datei von außerhalb des Ordners ein (include: /srv/other.yaml).",
        "Die Datei bindet eine weitere Compose-Datei von außerhalb des Ordners ein (include: oci://registry.example.org/compose:1 (aus dem Netz)).",
        "Die Datei bindet eine weitere Compose-Datei von außerhalb des Ordners ein (include: git@git.example.org:team/common.git (aus dem Netz)).",
        "„local“ wird auf dem Server gebaut (build): Dabei läuft Code aus dem Repository auf dem Server.",
        "„web“ erbt Einstellungen aus einer Datei außerhalb des Ordners (extends: ../base.yaml).",
        "„web“ liest Variablen aus Dateien außerhalb des Ordners (env_file: ../shared.env, /etc/app.env).",
        "„web“ bindet Ordner oder Dateien vom Server ein, die nicht aus dem Repository kommen: /srv/media, $HOME/x (enthält Variablen), ~/y, ../../secret.",
        "„worker“ wird auf dem Server gebaut (build): Dabei läuft Code aus dem Repository auf dem Server. Gebaut wird von außerhalb des Ordners (../src).",
        "Das Geheimnis „key“ kommt aus einer Datei außerhalb des Ordners (../key.txt).",
    ]


def test_compose_tags_are_read_as_data():
    text = b"services:\n  web:\n    image: nginx\n    ports: !reset []\n    volumes: !override\n      - /srv:/srv\n"
    assert _gf().compose_warnings(text) == (["„web“ bindet Ordner oder Dateien vom Server ein, die nicht aus dem Repository kommen: /srv."], None)


def test_names_and_paths_in_warnings_are_shown_without_control_characters():
    text = (b'include: ["oci://reg/\\e[1m"]\nservices:\n  "we\\u202eb":\n    image: nginx\n    volumes: ["/srv/\\e[31mx:/x", "$X/\\e:/y"]\n'
            b'secrets:\n  "k\\u202ey": {file: "../k\\a.txt"}\n')
    assert _gf().compose_warnings(text) == ([
        "Die Datei bindet eine weitere Compose-Datei von außerhalb des Ordners ein (include: oci://reg/?[1m (aus dem Netz)).",
        "„we?b“ bindet Ordner oder Dateien vom Server ein, die nicht aus dem Repository kommen: /srv/?[31mx, $X/? (enthält Variablen).",
        "Das Geheimnis „k?y“ kommt aus einer Datei außerhalb des Ordners (../k?.txt).",
    ], None)
    warnings, problem = _gf().compose_warnings("services: [a\u202e, b".encode())
    assert warnings == [] and problem.startswith("Die Compose-Datei ist kein gültiges YAML") and "\u202e" not in problem


@pytest.mark.parametrize(("text", "start"), [
    (b"services: [", "Die Compose-Datei ist kein gültiges YAML"),
    (b"- a\n- b\n", "Die Compose-Datei enthält keine Zuordnung"),
    (b"\xff\xfe", "Die Compose-Datei ist kein gültiges YAML"),
    (b"!!python/object/apply:os.system ['true']\n", "Die Compose-Datei ist kein gültiges YAML"),
    pytest.param(b"services: " + b"[" * 50000 + b"]" * 50000, "Die Compose-Datei ist", id="tief"),
    (b"services:\n  web:\n    volumes: kaputt\n", None),
])
def test_an_unreadable_compose_file_is_a_problem(text, start):
    warnings, problem = _gf().compose_warnings(text)
    assert warnings == []
    assert problem is None if start is None else problem.startswith(start)


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


def _git(docker_on):
    from nodvard_deck.ext.runtime import get_extension_runtime

    return get_extension_runtime().loaded["docker"].instance._git


async def _adopted(client, docker_on) -> str:
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop"
    assert (await client.post(f"{base}/adopt", json=SHOWN, headers=docker_on.owner)).status_code == 200
    return f"{base}/git"


@pytest.mark.asyncio
async def test_enter_fetch_and_forget_a_source(client, db_session, docker_on, tmp_path):
    from nodvard_deck.models import AuditEntry
    from sqlalchemy import select

    url = await _adopted(client, docker_on)
    origin = tmp_path / "origin"
    commit = _commit(origin, {"shop": {"compose.yaml": COMPOSE + b"    restart: always\n", "app.ini": b"a=1\n",
                                       "same.txt": b"gleich\n", "size.txt": b"neu\n"}})
    docker_on.exec.files["/opt/stacks/shop/same.txt"] = b"gleich\n"
    docker_on.exec.files["/opt/stacks/shop/size.txt"] = b"alt\n"
    _git(docker_on).run_fetch = _in_process(_local(origin))

    empty = await client.get(url, headers=docker_on.owner)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"source": None, "accesses": [], "layout_problem": None}
    assert (await client.post(f"{url}/fetch", headers=docker_on.owner)).status_code == 404
    saved = await client.put(url, headers=docker_on.owner, json={"url": "https://Git.example.org/team/stacks/", "path": "shop/compose.yaml"})
    assert saved.status_code == 200, saved.text
    assert {k: v for k, v in saved.json()["source"].items() if k != "set_at"} == {
        "url": "https://git.example.org/team/stacks", "ref": "main", "path": "shop/compose.yaml", "access": None}

    fetched = await client.post(f"{url}/fetch", headers=docker_on.owner)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["commit"] == {"id": commit, "short": commit[:7], "ref_kind": "branch", "author": "Ada Lovelace",
                              "date": "2023-11-14T23:13:20+01:00", "message": "Erster Stand"}
    assert (body["ok"], body["problems"], body["warnings"], body["unchanged"]) == (True, [], [], False)
    assert [(f["path"], f["server_path"], f["state"]) for f in body["files"]] == [
        ("app.ini", "/opt/stacks/shop/app.ini", "new"), ("compose.yaml", "/opt/stacks/shop/compose.yaml", "changed"),
        ("same.txt", "/opt/stacks/shop/same.txt", "same"), ("size.txt", "/opt/stacks/shop/size.txt", "changed")]
    assert body["compose"]["old"] == docker_on.exec.files["/opt/stacks/shop/compose.yaml"].decode()
    assert body["compose"]["new"].endswith("restart: always\n") and body["compose"]["same"] is False
    assert "/opt/stacks/shop/app.ini" not in docker_on.exec.files, "Holen schreibt nichts auf den Server"
    assert (_git(docker_on).repo_dir(docker_on.host, "shop").parent.stat().st_mode & 0o777) == 0o700 or sys.platform == "win32"

    assert (await client.delete(url, headers=docker_on.owner)).json() == {"source": None}
    assert not _git(docker_on).repo_dir(docker_on.host, "shop").exists()
    assert (await client.delete(url, headers=docker_on.owner)).status_code == 404
    actions = (await db_session.execute(select(AuditEntry.action).where(AuditEntry.action.like("docker.stack_git%")))).scalars().all()
    assert sorted(actions) == ["docker.stack_git_fetch", "docker.stack_git_source", "docker.stack_git_source_removed"]


@pytest.mark.asyncio
async def test_a_source_needs_an_adopted_stack_and_good_input(client, docker_on):
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/git"
    good = {"url": "https://github.com/team/stacks", "path": "shop/compose.yaml"}
    refused = await client.put(url, headers=docker_on.owner, json=good)
    assert refused.status_code == 409 and refused.json()["detail"].startswith("Der Stack ist nicht übernommen.")
    await _adopted(client, docker_on)
    for body, detail in [
        ({**good, "url": "http://github.com/team/stacks"}, "Nur Adressen mit https://"),
        ({**good, "path": "../compose.yaml"}, "Der Pfad enthält .."),
        ({**good, "ref": "refs/heads/main"}, "Nur den Namen des Branches"),
        ({**good, "access": "github"}, "Den Zugang „github“ gibt es nicht"),
    ]:
        answer = await client.put(url, headers=docker_on.owner, json=body)
        assert answer.status_code == 422 and answer.json()["detail"].startswith(detail), answer.text
    assert (await client.put(url, headers=docker_on.owner, json={**good, "extra": 1})).status_code == 422
    assert (await client.put(url, headers=docker_on.viewer, json=good)).status_code == 403
    assert (await client.get(url, headers=docker_on.viewer)).status_code == 403
    assert (await client.post(f"{url}/fetch", headers=docker_on.viewer)).status_code == 403
    assert (await client.get(f"/api/v1/ext/docker/hosts/{docker_on.other}/stacks/shop/git", headers=docker_on.owner)).status_code == 404


async def _with_access(client, docker_on, host="git.example.org", token=None):
    saved = await client.put("/api/v1/extensions/docker/settings", headers=docker_on.owner, json={
        "current_password": "correct-horse-battery",
        "values": {"git_accesses": [{"name": "team", "host": host, "username": "deck"}]}})
    assert saved.status_code == 200, saved.text
    if token:
        stored = await client.put("/api/v1/extensions/docker/secrets", headers=docker_on.owner,
                                  json={"current_password": "correct-horse-battery", "label": "docker-git-token:team", "value": token})
        assert stored.status_code == 204, stored.text


@pytest.mark.asyncio
async def test_a_private_repository_with_a_token_from_the_vault(client, docker_on, tmp_path):
    url = await _adopted(client, docker_on)
    origin = tmp_path / "origin"
    _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    seen: list = []
    local = _local(origin)
    _git(docker_on).run_fetch = _in_process(lambda address, credentials, limits: seen.append(credentials) or local(address, credentials, limits))
    body = {"url": "https://git.example.org/team/stacks", "path": "shop/compose.yaml", "access": "team"}

    await _with_access(client, docker_on, host="github.com")
    mismatch = await client.put(url, headers=docker_on.owner, json=body)
    assert mismatch.status_code == 422 and "gilt für github.com" in mismatch.json()["detail"]

    await _with_access(client, docker_on)
    assert (await client.get(url, headers=docker_on.owner)).json()["accesses"] == [{"name": "team", "host": "git.example.org"}]
    assert (await client.put(url, headers=docker_on.owner, json=body)).status_code == 200
    missing = await client.post(f"{url}/fetch", headers=docker_on.owner)
    assert missing.status_code == 502 and "kein Token im Tresor" in missing.json()["detail"]

    token = "tok" + "en-" + "0815"
    await _with_access(client, docker_on, token=token)
    fetched = await client.post(f"{url}/fetch", headers=docker_on.owner)
    assert fetched.status_code == 200, fetched.text
    assert seen == [("deck", token)]
    assert token not in fetched.text

    await _with_access(client, docker_on, host="github.com")
    moved = await client.post(f"{url}/fetch", headers=docker_on.owner)
    assert moved.status_code == 502 and "gilt für github.com, das Repository liegt aber auf git.example.org" in moved.json()["detail"]
    assert seen == [("deck", token)]


@pytest.mark.asyncio
async def test_a_replaced_token_is_used_for_the_next_fetch_without_a_hook(client, docker_on, tmp_path):
    url = await _adopted(client, docker_on)
    origin = tmp_path / "origin"
    _commit(origin, {"shop": {"compose.yaml": COMPOSE}})
    seen: list = []
    local = _local(origin)
    _git(docker_on).run_fetch = _in_process(lambda address, credentials, limits: seen.append(credentials) or local(address, credentials, limits))
    first, second = "tok" + "en-" + "0815", "tok" + "en-" + "4711"
    await _with_access(client, docker_on, token=first)
    body = {"url": "https://git.example.org/team/stacks", "path": "shop/compose.yaml", "access": "team"}
    assert (await client.put(url, headers=docker_on.owner, json=body)).status_code == 200

    assert (await client.post(f"{url}/fetch", headers=docker_on.owner)).status_code == 200
    replaced = await client.put("/api/v1/extensions/docker/secrets", headers=docker_on.owner,
                                json={"current_password": "correct-horse-battery", "label": "docker-git-token:team", "value": second})
    assert replaced.status_code == 204, replaced.text
    assert (await client.post(f"{url}/fetch", headers=docker_on.owner)).status_code == 200
    assert seen == [("deck", first), ("deck", second)]


@pytest.mark.asyncio
async def test_problems_and_warnings_in_the_preview(client, docker_on, tmp_path):
    url = await _adopted(client, docker_on)
    origin = tmp_path / "origin"
    _commit(origin, {"shop": {"docker-compose.yml": b"services:\n  web:\n    image: nginx\n    volumes: [/srv:/srv]\n",
                              "compose.yaml": b"anders", "link": (0o120000, b"/etc")}})
    docker_on.exec.sftp_server.modes["/opt/stacks/shop/compose.yaml"] = 0o120777
    _git(docker_on).run_fetch = _in_process(_local(origin))
    await client.put(url, headers=docker_on.owner, json={"url": "https://git.example.org/team/stacks", "path": "shop/docker-compose.yml"})
    body = (await client.post(f"{url}/fetch", headers=docker_on.owner)).json()
    assert body["ok"] is False
    assert body["warnings"] == ["„web“ bindet Ordner oder Dateien vom Server ein, die nicht aus dem Repository kommen: /srv."]
    assert body["problems"] == [
        "„link“ ist ein Verweis (Symlink); Verweise holt Deck nicht.",
        ("Im Ordner aus dem Repository liegt neben „docker-compose.yml“ auch „compose.yaml“, der Name der Compose-Datei des Stacks. "
         "Deck wüsste nicht, welche gilt."),
        "Auf dem Server ist „/opt/stacks/shop/compose.yaml“ ein Verweis oder keine normale Datei; Deck ersetzt das nicht.",
    ]
    assert body["compose"] is None


@pytest.mark.asyncio
async def test_the_same_state_with_problems_is_not_called_unchanged(client, docker_on, tmp_path):
    url = await _adopted(client, docker_on)
    _commit(tmp_path / "origin", {"shop": {"compose.yaml": docker_on.exec.files["/opt/stacks/shop/compose.yaml"], "link": (0o120000, b"x")}})
    _git(docker_on).run_fetch = _in_process(_local(tmp_path / "origin"))
    await client.put(url, headers=docker_on.owner, json={"url": "https://git.example.org/team/stacks", "path": "shop/compose.yaml"})
    body = (await client.post(f"{url}/fetch", headers=docker_on.owner)).json()
    assert [f["state"] for f in body["files"]] == ["same"]
    assert (body["ok"], body["unchanged"]) == (False, False)


@pytest.mark.asyncio
async def test_fetch_problems_are_a_502_with_the_reason(client, docker_on, tmp_path):
    url = await _adopted(client, docker_on)
    _commit(tmp_path / "origin", {"shop": {"compose.yaml": COMPOSE}})
    _git(docker_on).run_fetch = _in_process(_local(tmp_path / "origin"))
    await client.put(url, headers=docker_on.owner, json={"url": "https://git.example.org/team/stacks", "ref": "dev", "path": "shop/compose.yaml"})
    answer = await client.post(f"{url}/fetch", headers=docker_on.owner)
    assert answer.status_code == 502 and answer.json()["detail"] == "Im Repository gibt es keinen Branch und kein Tag „dev“."


@pytest.mark.asyncio
async def test_a_source_changed_meanwhile_is_not_fetched(client, docker_on, tmp_path):
    gs = _gs()
    await _adopted(client, docker_on)
    git = _git(docker_on)
    calls = []

    async def run(request, limits):
        calls.append(request)
        return {}

    git.run_fetch = run
    source = gs.Source("https://git.example.org/team/stacks", "main", "shop/compose.yaml", None, "")
    await git.sources.put(docker_on.host, "shop", source)
    await git.forget(docker_on.host, "shop")
    with pytest.raises(gs.GitProblem, match="inzwischen geändert oder vergessen"):
        await git.fetch(docker_on.host, "shop", source)
    assert calls == [] and not git.repo_dir(docker_on.host, "shop").exists()


@pytest.mark.asyncio
async def test_releasing_the_stack_forgets_its_source(client, docker_on, tmp_path):
    url = await _adopted(client, docker_on)
    _commit(tmp_path / "origin", {"shop": {"compose.yaml": COMPOSE}})
    _git(docker_on).run_fetch = _in_process(_local(tmp_path / "origin"))
    await client.put(url, headers=docker_on.owner, json={"url": "https://git.example.org/team/stacks", "path": "shop/compose.yaml"})
    assert (await client.post(f"{url}/fetch", headers=docker_on.owner)).status_code == 200
    assert _git(docker_on).repo_dir(docker_on.host, "shop").exists()
    adopt_url = url.removesuffix("/git") + "/adopt"
    assert (await client.delete(adopt_url, headers=docker_on.owner)).status_code == 200
    assert _git(docker_on).sources.get(docker_on.host, "shop") is None
    assert not _git(docker_on).repo_dir(docker_on.host, "shop").exists()
    await client.post(adopt_url, json=SHOWN, headers=docker_on.owner)
    assert (await client.get(url, headers=docker_on.owner)).json()["source"] is None


@pytest.mark.asyncio
async def test_a_stack_with_several_files_cannot_take_a_source(client, docker_on):
    from nodvard_deck_ext_docker import adopt

    store = adopt.AdoptedStacks(docker_on.data_dir)
    stack = SimpleNamespace(project="shop", working_dir="/opt/stacks/shop", files=("/opt/stacks/shop/a.yaml", "/opt/stacks/shop/b.yaml"), env_files=())
    await store.add(docker_on.host, stack)
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/git"
    assert (await client.get(url, headers=docker_on.owner)).json()["layout_problem"].startswith("Der Stack besteht aus mehreren Compose-Dateien.")
    refused = await client.put(url, headers=docker_on.owner, json={"url": "https://github.com/a/b"})
    assert refused.status_code == 409


@pytest.mark.asyncio
async def test_forget_clears_store_and_rests_even_when_the_list_fails(tmp_path, monkeypatch):
    from nodvard_deck_ext_docker import adopt

    gs = _gs()
    git = gs.GitStacks(SimpleNamespace(), None, adopt.AdoptedStacks(tmp_path), tmp_path)
    store = git.repo_dir("h1", "shop")
    for path in (store / "objects", store.with_name(store.name + ".neu-1234"), store.with_name(store.name + ".alt-1234")):
        path.mkdir(parents=True)

    async def broken(host_id, project):
        raise adopt.StoreProblem("platt")

    monkeypatch.setattr(git.sources, "remove", broken)
    with pytest.raises(adopt.StoreProblem):
        await git.forget("h1", "shop")
    assert list(store.parent.iterdir()) == []


@pytest.mark.asyncio
async def test_at_most_two_fetches_run_at_once(tmp_path):
    from nodvard_deck_ext_docker import adopt

    gs = _gs()
    git = gs.GitStacks(SimpleNamespace(), None, adopt.AdoptedStacks(tmp_path), tmp_path)
    running, peak = 0, 0

    async def slow(request, limits):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return GOOD

    git.run_fetch = slow
    sources = [gs.Source(URL, "main", "compose.yaml", None, str(i)) for i in range(5)]
    for i, source in enumerate(sources):
        await git.sources.put("h1", f"s{i}", source)
    await asyncio.gather(*(git.fetch("h1", f"s{i}", source) for i, source in enumerate(sources)))
    assert peak == gs.PARALLEL == 2


def test_the_settings_refuse_port_80_for_a_git_access():
    import re

    schema = json.loads((EXT_DIR / "settings.schema.json").read_text(encoding="utf-8"))
    pattern = schema["properties"]["git_accesses"]["items"]["properties"]["host"]["pattern"]
    assert [bool(re.match(pattern, host)) for host in ("github.com", "git.example.org:3000", "git.example.org:8080", "git.example.org:80")] == [
        True, True, True, False]
    assert any(e["label"] == "docker-git-token:{name}" and e["per_item"] == "git_accesses" for e in schema["x-secrets"])


def test_layout_of_the_stack():
    gs = _gs()
    from nodvard_deck_ext_docker.stacks import Stack

    def stack(files, working_dir="/opt/stacks/shop"):
        return Stack("shop", [], working_dir, files, (), "compose")

    assert gs.layout_problem(stack(("/opt/stacks/shop/compose.yaml",))) is None
    assert gs.layout_problem(stack(("/opt/stacks/shop/compose.yaml",), "/opt/stacks/shop/")) is None
    assert gs.layout_problem(stack(("/opt/stacks/shop/deploy/compose.yaml",))).startswith("Die Compose-Datei des Stacks liegt nicht direkt")
    assert gs.layout_problem(stack(("/opt/a.yaml", "/opt/b.yaml"))).startswith("Der Stack besteht aus mehreren")


def test_the_compose_limit_matches_the_editor():
    from nodvard_deck_ext_docker.stacks import MAX_FILE_BYTES

    assert _gs().LIMITS.compose_bytes == MAX_FILE_BYTES
