"""Volume-Sicherungen zurückspielen und löschen: nur Volumes des Stacks, erst prüfen und entpacken, dann tauschen."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from docker_stack_fakes import docker_on, result, row  # noqa: F401 - Fixtures
from test_ext_docker_stack_update import (  # noqa: F401 - Fixture
    _async,
    _ext_docker_on_path,
    _setup,
)
from test_ext_docker_stack_update import _request as _update_request

HOME = "/home/deck"
STAMP, OLDER = "20261016-041500", "20261001-120000"
FOLDER = f"{HOME}/nodvard-sicherungen/shop/{STAMP}"
SHA, SIZE = "ab" * 32, 1234


def _mod():
    from nodvard_deck_ext_docker import stack_restore

    return stack_restore


_APPLETS = ("sh", "tar", "gzip", "sha256sum", "wc", "cut", "tr", "stat", "find", "mv", "rm", "mkdir", "chown", "chmod", "rmdir", "df", "tail")
BUSYBOX = pytest.param("busybox", marks=pytest.mark.skipif(not shutil.which("busybox"), reason="braucht busybox"))


def _shell(kind, tmp_path, *front):
    """Aufruf und Umgebung für ein Skript: die Shell des Testrechners oder nur BusyBox-Applets wie im Hilfsimage."""
    if kind != "busybox":
        return [kind], {**os.environ, "PATH": os.pathsep.join([*map(str, front), os.environ.get("PATH", "")])}
    tools = tmp_path / "busybox"
    tools.mkdir()
    for applet in _APPLETS:
        (tools / applet).symlink_to(shutil.which("busybox"))
    return [str(tools / "sh")], {**os.environ, "PATH": os.pathsep.join([*map(str, front), str(tools)])}


async def _until(check):
    for _ in range(1000):
        if check():
            return
        await asyncio.sleep(0)
    raise AssertionError("Der erwartete Zustand trat nicht ein.")


def _backup(sftp, stamp=STAMP, files=("shop_data.tar.gz",)):
    for path in (f"{HOME}/nodvard-sicherungen", f"{HOME}/nodvard-sicherungen/shop", f"{HOME}/nodvard-sicherungen/shop/{stamp}"):
        sftp.dirs.add(path)
    for name in files:
        sftp.files[f"{HOME}/nodvard-sicherungen/shop/{stamp}/{name}"] = b"x" * 1234


async def _env(tmp_path, monkeypatch, used_by=None, **kwargs):
    env = await _setup(tmp_path, monkeypatch, **kwargs)
    users = {"shop_data": ["shop-web-1"], "shop_cache": ["shop-worker-1"], **(used_by or {})}
    env.users = users
    resources = SimpleNamespace(
        forget=lambda host_id: None,
        snapshot=lambda host: _async(SimpleNamespace(volumes=[{"name": n, "used_by": u} for n, u in users.items()])),
    )
    m = _mod()
    restore = m.StackRestore(env.update._ctx, env.update, resources)
    env.restore = restore
    env.executor = m.DockerStackRestoreExecutor(env.update._ctx, restore)
    env.sftp = env.docker.fake.sftp_server
    env.restored: list[list[str]] = []
    env.checked: list[str] = []
    env.unpack: dict[str, object] = {}
    """Je Volume ein Exit-Code oder (Exit-Code, stderr) fürs Zurückspielen; `hang` lässt es hängen."""
    env.check: dict[str, object] = {}
    """Je Volume (Exit-Code, stdout, stderr) der Prüfung vor dem Stoppen."""
    env.helper: dict[str, str] = {}
    """Je Volume `true`/`false` für einen Hilfscontainer; fehlt er, gibt es keinen."""
    env.orphans: set[str] = set()
    """Dienste mit Container, die nicht mehr in der Compose-Datei stehen; `compose stop/start` lehnt sie ab."""
    env.leftovers: dict[str, object] = {}
    """Je Volume der Exit-Code beim Nachsehen nach Resten im Volume (0 = keine) oder `lost`."""
    original = env.docker.run_argv

    async def run_argv(host, argv, **kwargs):
        verb = next((v for v in ("stop", "start") if argv[:1] == ["sh"] and v in argv), None)
        if verb:
            names = argv[argv.index(verb) + 1:]
            if unknown := [n for n in names if n in env.orphans]:
                env.docker.calls.append(list(argv))
                return result(stderr=f"no such service: {unknown[0]}\n", code=1)
            answer = await original(host, argv, **kwargs)
            if answer.exit_code == 0:
                (env.docker.stopped.update if verb == "stop" else env.docker.stopped.difference_update)(names)
            return answer
        if argv[:3] == ["docker", "container", "inspect"] and argv[-1].startswith("nodvard-zurueck-"):
            state = env.helper.get(argv[-1].removeprefix("nodvard-zurueck-"))
            if state == "fehler":
                return result(stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock\n", code=1)
            return result(state + "\n") if state else result(stderr=f"Error: No such container: {argv[-1]}\n", code=1)
        if argv[:2] == ["docker", "run"] and argv[-1] == m._LEFTOVER_SCRIPT:
            env.docker.calls.append(list(argv))
            answer = env.leftovers.get(argv[argv.index("-v") + 1].split(":", 1)[0], 0)
            if answer == "lost":
                raise ConnectionError("Verbindung verloren")
            return result(code=answer)
        if argv[:2] == ["docker", "run"] and any(a.endswith(":/backup:ro") for a in argv):
            env.docker.calls.append(list(argv))
            volume = argv[argv.index("-v") + 1].split(":", 1)[0]
            if f"{volume}:/data:ro" in argv:
                env.checked.append(volume)
                code, out, err = env.check.get(volume, (0, f"1000 99999999 {SHA} {SIZE}\n", ""))
                return result(out, err, code)
            env.restored.append(list(argv))
            if "hang" in env.unpack:
                env.helper[volume] = "true"
                await asyncio.sleep(3600)
            answer = env.unpack.get(volume, 0)
            if answer == "timeout":
                raise TimeoutError
            if answer == "lost":
                raise ConnectionError("Verbindung verloren")
            code, err = answer if isinstance(answer, tuple) else (answer, "rm: can't remove '/data/x': Device busy\n")
            return result(stderr=err, code=code) if code else result()
        return await original(host, argv, **kwargs)

    env.docker.fake.run_argv = run_argv
    return env


def _request(action="docker.stack_volume_restore", volumes=("shop_data",), stamp=STAMP, risk="high", actor="user"):
    from nodvard_sdk.actions import ActionRequest
    from nodvard_sdk.types import Actor, Risk

    payload = {"project": "shop", "stamp": stamp, **({"volumes": list(volumes)} if action.endswith("restore") else {})}
    return ActionRequest(action_type=action, host_ref="h1", risk=Risk(risk), proposed_by=Actor(type=actor, id="u1"), reason="Test", payload=payload)


def test_the_commands():
    m = _mod()
    argv = m.cmd_restore("shop_data", FOLDER, SHA, SIZE)
    assert argv[:5] == ["docker", "run", "--rm", "--name", "nodvard-zurueck-shop_data"]
    assert argv[5:11] == ["--network", "none", "--security-opt", "no-new-privileges", "--pids-limit", "64"]
    assert argv[11:15] == ["-v", "shop_data:/data", "-v", f"{FOLDER}:/backup:ro"]
    assert argv[15] == "busybox:1.37" and argv[-4:] == ["sh", "shop_data.tar.gz", SHA, str(SIZE)]
    script = argv[argv.index("-c") + 1]
    assert script.index("tar xzf") < script.index('mv -t "$o"') < script.index("mv -t /data") < script.rindex('rm -rf "$o"')
    check = m.cmd_check("shop_data", FOLDER)
    assert "--name" not in check and check[check.index("-v") + 1] == "shop_data:/data:ro" and "gzip -dc" in check[check.index("-c") + 1]
    assert m.cmd_helper_state("shop_data") == ["docker", "container", "inspect", "--format", "{{.State.Running}}", "nodvard-zurueck-shop_data"]
    for bad in (("a;b", FOLDER), ("shop_data", "/h:/x"), ("shop_data", "relativ"), ("shop_data", "/home/../etc")):
        with pytest.raises(ValueError):
            m.cmd_restore(*bad, SHA, SIZE)
        with pytest.raises(ValueError):
            m.cmd_check(*bad)
    for sha, size in (("AB" * 32, SIZE), ("ab" * 31, SIZE), (SHA + "\n", SIZE), (SHA, -1), (SHA, "1234"), (SHA, True)):
        with pytest.raises(ValueError):
            m.cmd_restore("shop_data", FOLDER, sha, size)
    leftovers = m.cmd_leftovers("shop_data")
    assert leftovers[leftovers.index("-v") + 1] == "shop_data:/data:ro" and "--name" not in leftovers and "/backup" not in " ".join(leftovers)
    with pytest.raises(ValueError):
        m.cmd_leftovers("a;b")
    assert m.stamp_text(STAMP) == "2026-10-16T04:15:00+00:00"
    assert m.valid_stamp(STAMP) and not m.valid_stamp("99999999-999999") and not m.valid_stamp("20261301-000000")
    assert m.valid_stamp("20000101-000000") and m.valid_stamp("20991231-235959")
    for edge in ("00010101-000000", "19991231-235959", "21000101-000000", "99991231-235959"):
        assert not m.valid_stamp(edge), edge


def _fingerprint(path):
    data = path.read_bytes() if path.exists() else b""
    return [hashlib.sha256(data).hexdigest(), str(len(data))]


@pytest.mark.skipif(sys.platform == "win32" or not all(map(shutil.which, ("sh", "tar", "gzip"))), reason="braucht POSIX mit sh, tar und gzip")
def test_the_archive_leaves_out_restore_leftovers_and_no_half_file(tmp_path):
    from nodvard_deck_ext_docker import stack_backup as sb

    script = sb._ARCHIVE_SCRIPT.replace("/data", str(tmp_path / "data")).replace("/backup", str(tmp_path / "backup"))
    data, backup = tmp_path / "data", tmp_path / "backup"
    for folder in (data / ".nodvard-zurueck", data / ".nodvard-vorher", data / ".nodvard-zurueck-nicht", backup):
        folder.mkdir(parents=True)
    (data / "a.txt").write_text("a")
    owner = f"{os.getuid()}:{os.getgid()}"
    done = subprocess.run(["sh", "-c", script, "sh", "v.tar.gz", owner], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    listed = subprocess.run(["tar", "tzf", str(backup / "v.tar.gz")], capture_output=True, text=True, check=True).stdout.split()
    assert "./a.txt" in listed and "./.nodvard-zurueck-nicht/" in listed
    assert not any(name.startswith(("./.nodvard-zurueck/", "./.nodvard-vorher/")) for name in listed)
    assert sorted(p.name for p in backup.iterdir()) == ["v.tar.gz"] and (backup / "v.tar.gz").stat().st_mode & 0o777 == 0o600
    broken = subprocess.run(["sh", "-c", script, "sh", "w.tar.gz", "kein-nutzer-xyz"], capture_output=True, text=True, check=False)
    assert broken.returncode != 0 and sorted(p.name for p in backup.iterdir()) == ["v.tar.gz"]


@pytest.mark.skipif(sys.platform == "win32" or not all(map(shutil.which, ("sh", "tar", "gzip"))), reason="braucht POSIX mit sh, tar und gzip")
@pytest.mark.parametrize("shell", ["sh", BUSYBOX])
def test_the_script_swaps_only_after_a_good_archive(tmp_path, shell):
    m = _mod()
    script = m._RESTORE_SCRIPT.replace("/data", str(tmp_path / "data")).replace("/backup", str(tmp_path / "backup"))
    data, backup, source = tmp_path / "data", tmp_path / "backup", tmp_path / "source"
    for folder in (data, backup, source / "sub"):
        folder.mkdir(parents=True)
    (source / "a.txt").write_text("alt")
    (source / ".versteckt").write_text("v")
    (source / "sub" / "b").write_text("b")
    subprocess.run(["tar", "czf", str(backup / "v.tar.gz"), "-C", str(source), "."], check=True)
    whole = (backup / "v.tar.gz").read_bytes()
    (backup / "kaputt.tar.gz").write_bytes(whole[:-20])
    (data / "a.txt").write_text("neu")
    (data / "dazu.txt").write_text("seit der Sicherung")
    cmd, env = _shell(shell, tmp_path)

    def run(name):
        return subprocess.run([*cmd, "-c", script, "sh", name, *_fingerprint(backup / name)], capture_output=True, text=True, check=False,
                              env=env)

    assert run("kaputt.tar.gz").returncode == m.BROKEN_EXIT
    assert sorted(p.name for p in data.iterdir()) == ["a.txt", "dazu.txt"] and (data / "a.txt").read_text() == "neu"
    assert run("fehlt.tar.gz").returncode == m.BROKEN_EXIT
    (data / ".nodvard-vorher").mkdir()
    assert run("v.tar.gz").returncode == m.LEFTOVER_EXIT and (data / "a.txt").read_text() == "neu"
    (data / ".nodvard-vorher").rmdir()
    (data / ".nodvard-zurueck").mkdir()
    (data / ".nodvard-zurueck" / "alt").write_text("von einem Abbruch")
    done = run("v.tar.gz")
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in data.iterdir()) == [".versteckt", "a.txt", "sub"]
    assert (data / "a.txt").read_text() == "alt" and (data / "sub" / "b").read_text() == "b"


@pytest.mark.skipif(sys.platform == "win32" or not all(map(shutil.which, ("sh", "tar", "gzip"))), reason="braucht POSIX mit sh, tar und gzip")
def test_staging_left_in_an_archive_does_not_break_the_swap(tmp_path):
    m = _mod()
    script = m._RESTORE_SCRIPT.replace("/data", str(tmp_path / "data")).replace("/backup", str(tmp_path / "backup"))
    data, backup, source = tmp_path / "data", tmp_path / "backup", tmp_path / "source"
    for folder in (data, backup, source / ".nodvard-zurueck", source / ".nodvard-vorher"):
        folder.mkdir(parents=True)
    (source / "a.txt").write_text("alt")
    (source / ".nodvard-zurueck" / "rest").write_text("x")
    subprocess.run(["tar", "czf", str(backup / "v.tar.gz"), "-C", str(source), "."], check=True)
    (data / "a.txt").write_text("neu")
    done = subprocess.run(["sh", "-c", script, "sh", "v.tar.gz", *_fingerprint(backup / "v.tar.gz")], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in data.iterdir()) == ["a.txt"] and (data / "a.txt").read_text() == "alt"


@pytest.mark.skipif(sys.platform == "win32" or not all(map(shutil.which, ("sh", "tar", "gzip"))), reason="braucht POSIX mit sh, tar und gzip")
@pytest.mark.parametrize(("entries", "mode"), [(["a.txt"], 0o770), (["."], 0o750)], ids=["ohne-wurzel", "mit-wurzel"])
def test_the_volume_keeps_its_rights_unless_the_archive_brings_them(tmp_path, entries, mode):
    m = _mod()
    script = m._RESTORE_SCRIPT.replace("/data", str(tmp_path / "data")).replace("/backup", str(tmp_path / "backup"))
    data, backup, source = tmp_path / "data", tmp_path / "backup", tmp_path / "source"
    for folder in (data, backup, source):
        folder.mkdir()
    (source / "a.txt").write_text("alt")
    source.chmod(0o750)
    subprocess.run(["tar", "czf", str(backup / "v.tar.gz"), "-C", str(source), *entries], check=True)
    data.chmod(0o770)
    done = subprocess.run(["sh", "-c", script, "sh", "v.tar.gz", *_fingerprint(backup / "v.tar.gz")], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert (data / "a.txt").read_text() == "alt" and data.stat().st_mode & 0o777 == mode


@pytest.mark.skipif(sys.platform == "win32" or not all(map(shutil.which, ("sh", "tar", "gzip"))), reason="braucht POSIX mit sh, tar und gzip")
@pytest.mark.parametrize("refused", ["chown", "chmod"])
def test_refused_rights_on_the_staging_folder_leave_nothing_behind(tmp_path, refused):
    m = _mod()
    script = m._RESTORE_SCRIPT.replace("/data", str(tmp_path / "data")).replace("/backup", str(tmp_path / "backup"))
    data, backup, source, tools = tmp_path / "data", tmp_path / "backup", tmp_path / "source", tmp_path / "bin"
    for folder in (data, backup, source, tools):
        folder.mkdir()
    (source / "a.txt").write_text("alt")
    subprocess.run(["tar", "czf", str(backup / "v.tar.gz"), "-C", str(source), "."], check=True)
    (data / "a.txt").write_text("neu")
    (tools / refused).write_text("#!/bin/sh\nexit 1\n")
    (tools / refused).chmod(0o755)
    path = f"{tools}{os.pathsep}{os.environ.get('PATH', '')}"
    done = subprocess.run(["sh", "-c", script, "sh", "v.tar.gz", *_fingerprint(backup / "v.tar.gz")], capture_output=True, text=True,
                          check=False, env={**os.environ, "PATH": path})
    assert done.returncode == m.PREPARE_EXIT, done.stderr
    assert sorted(p.name for p in data.iterdir()) == ["a.txt"] and (data / "a.txt").read_text() == "neu"


@pytest.mark.skipif(sys.platform == "win32" or not all(map(shutil.which, ("bash", "tar", "gzip"))), reason="braucht POSIX mit bash, tar und gzip")
@pytest.mark.parametrize("shell", ["bash", BUSYBOX])
def test_the_check_reads_the_whole_archive_and_the_free_space(tmp_path, shell):
    m = _mod()
    script = m._CHECK_SCRIPT.replace("/data", str(tmp_path / "data")).replace("/backup", str(tmp_path / "backup"))
    data, backup = tmp_path / "data", tmp_path / "backup"
    data.mkdir()
    backup.mkdir()
    (data / "a.txt").write_text("x" * 5000)
    subprocess.run(["tar", "czf", str(backup / "v.tar.gz"), "-C", str(data), "."], check=True)
    (backup / "kaputt.tar.gz").write_bytes((backup / "v.tar.gz").read_bytes()[:-20])

    cmd, env = _shell(shell, tmp_path)

    def run(name):
        return subprocess.run([*cmd, "-c", script, "sh", name], capture_output=True, text=True, check=False, env=env)

    size, free, sha, archive = run("v.tar.gz").stdout.split()
    assert int(size) > 5000 and int(free) > 0 and [sha, archive] == _fingerprint(backup / "v.tar.gz")
    assert run("kaputt.tar.gz").returncode == m.BROKEN_EXIT
    with gzip.open(backup / "nur-gzip.tar.gz", "wb") as plain:
        plain.write(b"kein tar " * 400)
    assert run("nur-gzip.tar.gz").returncode == m.BROKEN_EXIT
    (data / ".nodvard-vorher").mkdir()
    assert run("v.tar.gz").returncode == m.LEFTOVER_EXIT


def _two_archives(tmp_path):
    data, backup = tmp_path / "data", tmp_path / "backup"
    for name, text in (("geprueft", "aus der Sicherung"), ("anders", "untergeschoben")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "a.txt").write_text(text)
        subprocess.run(["tar", "czf", str(tmp_path / f"{name}.tar.gz"), "-C", str(tmp_path / name), "."], check=True)
    data.mkdir()
    backup.mkdir()
    (data / "a.txt").write_text("jetzt")
    shutil.copy(tmp_path / "geprueft.tar.gz", backup / "v.tar.gz")
    return data, backup


@pytest.mark.skipif(sys.platform == "win32" or not all(map(shutil.which, ("sh", "tar", "gzip", "sha256sum"))),
                    reason="braucht POSIX mit sh, tar, gzip und sha256sum")
@pytest.mark.parametrize("change", ["inhalt", "gleich-gross", "groesse"])
@pytest.mark.parametrize("shell", ["sh", BUSYBOX])
def test_an_archive_other_than_the_checked_one_is_not_restored(tmp_path, change, shell):
    m = _mod()
    script = m._RESTORE_SCRIPT.replace("/data", str(tmp_path / "data")).replace("/backup", str(tmp_path / "backup"))
    data, backup = _two_archives(tmp_path)
    sha, size = _fingerprint(backup / "v.tar.gz")
    if change == "inhalt":
        shutil.copy(tmp_path / "anders.tar.gz", backup / "v.tar.gz")
    elif change == "gleich-gross":
        whole = bytearray((backup / "v.tar.gz").read_bytes())
        whole[len(whole) // 2] ^= 0xFF
        (backup / "v.tar.gz").write_bytes(bytes(whole))
        assert _fingerprint(backup / "v.tar.gz")[1] == size
    else:
        size = str(int(size) + 1)
    cmd, env = _shell(shell, tmp_path)
    done = subprocess.run([*cmd, "-c", script, "sh", "v.tar.gz", sha, size], capture_output=True, text=True, check=False, env=env)
    assert done.returncode == m.CHANGED_EXIT, done.stderr
    assert sorted(p.name for p in data.iterdir()) == ["a.txt"] and (data / "a.txt").read_text() == "jetzt"


@pytest.mark.skipif(sys.platform != "linux" or not all(map(shutil.which, ("sh", "tar", "gzip", "sha256sum"))),
                    reason="braucht Linux mit sh, tar, gzip und sha256sum")
def test_an_archive_swapped_after_opening_does_not_get_restored(tmp_path):
    m = _mod()
    script = m._RESTORE_SCRIPT.replace("/data", str(tmp_path / "data")).replace("/backup", str(tmp_path / "backup"))
    data, backup = _two_archives(tmp_path)
    tools = tmp_path / "bin"
    tools.mkdir()
    # Tauscht das Archiv unter seinem Namen aus, während das Skript die Prüfsumme bildet.
    (tools / "sha256sum").write_text(f'#!/bin/sh\nmv -f "{tmp_path / "anders.tar.gz"}" "{backup / "v.tar.gz"}"\nexec {shutil.which("sha256sum")} "$@"\n')
    (tools / "sha256sum").chmod(0o755)
    # Nur mit `sh`: Die BusyBox-Shell ruft ihr eigenes sha256sum auf und nie den Ersatz aus PATH.
    cmd, env = _shell("sh", tmp_path, tools)
    done = subprocess.run([*cmd, "-c", script, "sh", "v.tar.gz", *_fingerprint(backup / "v.tar.gz")], capture_output=True, text=True,
                          check=False, env=env)
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "anders.tar.gz").exists() and (data / "a.txt").read_text() == "aus der Sicherung"


@pytest.mark.skipif(sys.platform != "linux" or not all(map(shutil.which, ("bash", "tar", "gzip", "sha256sum"))),
                    reason="braucht Linux mit bash, tar, gzip und sha256sum")
def test_a_swap_during_the_check_does_not_mix_two_archives(tmp_path):
    m = _mod()
    script = m._CHECK_SCRIPT.replace("/data", str(tmp_path / "data")).replace("/backup", str(tmp_path / "backup"))
    _data, backup = _two_archives(tmp_path)
    checked = _fingerprint(backup / "v.tar.gz")
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "sha256sum").write_text(f'#!/bin/sh\nmv -f "{tmp_path / "anders.tar.gz"}" "{backup / "v.tar.gz"}"\nexec {shutil.which("sha256sum")} "$@"\n')
    (tools / "sha256sum").chmod(0o755)
    cmd, env = _shell("bash", tmp_path, tools)
    done = subprocess.run([*cmd, "-c", script, "sh", "v.tar.gz"], capture_output=True, text=True, check=False, env=env)
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "anders.tar.gz").exists() and done.stdout.split()[2:] == checked


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("sh"), reason="braucht eine POSIX-Shell")
@pytest.mark.parametrize(("folder", "code"), [(None, 0), (".nodvard-vorher", 1), (".nodvard-zurueck", 1)], ids=["sauber", "vorher", "zurueck"])
def test_leftovers_in_the_volume_are_found(tmp_path, folder, code):
    m = _mod()
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.txt").write_text("x")
    if folder:
        (data / folder).mkdir()
    script = m._LEFTOVER_SCRIPT.replace("/data", str(data))
    assert subprocess.run(["sh", "-c", script], capture_output=True, check=False).returncode == code


@pytest.mark.asyncio
async def test_the_list_names_volumes_sizes_and_who_uses_them(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    assert (await env.restore.backups(env.host, env.stack))["backups"] == []
    _backup(env.sftp, OLDER, ("shop_data.tar.gz", "alt_weg.tar.gz", "notiz.txt"))
    _backup(env.sftp)
    env.sftp.files[f"{FOLDER}/verweis.tar.gz"] = b""
    env.sftp.modes[f"{FOLDER}/verweis.tar.gz"] = 0o120777
    env.sftp.dirs.add(f"{HOME}/nodvard-sicherungen/shop/irgendwas")
    listing = await env.restore.backups(env.host, env.stack)
    assert listing["error"] is None and [b["stamp"] for b in listing["backups"]] == [STAMP, OLDER]
    assert listing["backups"][0]["volumes"] == [{"name": "shop_data", "size_bytes": 1234, "services": ["web"], "stopped_services": [],
                                                 "restorable": True}]
    assert [(v["name"], v["restorable"]) for v in listing["backups"][1]["volumes"]] == [("alt_weg", False), ("shop_data", True)]
    assert listing["backups"][0]["at"] == "2026-10-16T04:15:00+00:00"
    for backup in listing["backups"]:
        assert backup["unfinished"] == [] and backup["missing"] == [] and backup["contents_error"] is None


@pytest.mark.asyncio
async def test_the_contents_list_names_what_a_backup_is_missing(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp, files=("shop_data.tar.gz", "shop_more.tar.gz.part"))
    env.sftp.files[f"{FOLDER}/inhalt.json"] = b'{"volumes": ["shop_data", "shop_more", "shop_cache"]}'
    [backup] = (await env.restore.backups(env.host, env.stack))["backups"]
    assert [v["name"] for v in backup["volumes"]] == ["shop_data"]
    assert backup["missing"] == ["shop_cache", "shop_more"] and backup["unfinished"] == ["shop_more"] and backup["contents_error"] is None


@pytest.mark.asyncio
async def test_an_update_whose_backup_broke_off_leaves_a_backup_that_says_what_is_missing(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    env.docker.mounts.update({"web": ["shop_data", "shop_more"], "worker": []})
    env.update.volumes._resources = SimpleNamespace(forget=lambda host_id: None, snapshot=lambda host: _async(SimpleNamespace(
        volumes=[{"name": "shop_data", "size_bytes": 1024}, {"name": "shop_more", "size_bytes": 1024}])))
    inner = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        folder = next((a[: -len(":/backup")] for a in argv if a.endswith(":/backup")), None)
        if argv[:2] == ["docker", "run"] and folder:
            env.docker.calls.append(list(argv))
            if "shop_more:/data:ro" in argv:
                return result(stderr="Error: no space left on device\n", code=1)
            env.sftp.files[f"{folder}/{argv[-2]}"] = b"fertig"
            return result()
        return await inner(host, argv, **kwargs)

    env.docker.fake.run_argv = run_argv
    updater = env.m.stack_update.DockerStackUpdateExecutor(env.update._ctx, env.update, env.images)
    done = await updater.execute(_update_request(backup=True))
    assert done.success is False and "Schon gepackt: shop_data" in done.error
    [backup] = (await env.restore.backups(env.host, env.stack))["backups"]
    assert [v["name"] for v in backup["volumes"]] == ["shop_data"] and backup["missing"] == ["shop_more"] and backup["unfinished"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "mode", "says"), [
    (b'{"volumes": ["shop_data"', 0o100600, "ist beschädigt"),
    (b'{"volumes": "shopdata"}', 0o100600, "ist beschädigt"),
    (b'["shop_data"]', 0o100600, "ist beschädigt"),
    (b'{"volumes": ["a;b"]}', 0o100600, "ist beschädigt"),
    (b"\xff", 0o100600, "ist beschädigt"),
    (b'{"volumes": ["shop_data"]}' + b" " * 70 * 1024, 0o100600, "ist beschädigt"),
    (b"", 0o120777, "ließ sich nicht lesen (keine normale Datei)"),
], ids=["abgeschnitten", "kein-array", "keine-zuordnung", "ungueltig", "kein-utf8", "zu-gross", "verweis"])
async def test_a_contents_list_that_cannot_be_read_is_never_complete(tmp_path, monkeypatch, content, mode, says):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.sftp.files[f"{FOLDER}/inhalt.json"] = content
    env.sftp.modes[f"{FOLDER}/inhalt.json"] = mode
    listing = await env.restore.backups(env.host, env.stack)
    [backup] = listing["backups"]
    assert listing["error"] is None and backup["missing"] == []
    assert backup["contents_error"] == f"Die Inhaltsliste dieser Sicherung {says}; ob die Sicherung vollständig ist, lässt sich nicht sagen."


def test_the_contents_list_format():
    from nodvard_deck_ext_docker import stack_backup as sb

    assert sb.parse_contents(sb.contents(["shop_data", "shop.cache-2"])) == ["shop_data", "shop.cache-2"]
    with pytest.raises(ValueError):
        sb.contents(["a;b"])
    padded = b'{"volumes": ["shop_data"]}' + b" " * sb.MAX_CONTENTS_BYTES
    for raw in (b'["shop_data"]', b'{"volumes": "shopdata"}', b'{"volumes": [1]}', b'{"volumes": ["a;b"]}', b"{}", b"", padded):
        assert sb.parse_contents(raw) is None, raw[:30]


@pytest.mark.asyncio
async def test_an_unreadable_contents_list_names_the_reason(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.sftp.files[f"{FOLDER}/inhalt.json"] = b'{"volumes": ["shop_data"]}'
    opened = env.sftp.open

    def open_(path, mode, attrs=None):
        if path.endswith("/inhalt.json"):
            raise PermissionError(13, "Permission denied")
        return opened(path, mode, attrs)

    monkeypatch.setattr(env.sftp, "open", open_)
    [backup] = (await env.restore.backups(env.host, env.stack))["backups"]
    assert backup["contents_error"].startswith("Die Inhaltsliste dieser Sicherung ließ sich nicht lesen ([Errno 13] Permission denied)")


@pytest.mark.asyncio
async def test_a_half_packed_archive_is_named_and_goes_with_the_backup(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp, files=("shop_data.tar.gz", "shop_cache.tar.gz.part"))
    listing = await env.restore.backups(env.host, env.stack)
    assert [v["name"] for v in listing["backups"][0]["volumes"]] == ["shop_data"]
    assert listing["backups"][0]["unfinished"] == ["shop_cache"] and listing["busy"] is None
    with env.update.actions.locks.hold("h1", "shop", "Update"):
        assert (await env.restore.backups(env.host, env.stack))["busy"] == "Update"
    locks, before = env.update.actions.locks, env.update.actions.locks.outside
    locks.outside = lambda host_id, project: "Image-Update eines Containers" if project == "shop" else None
    assert (await env.restore.backups(env.host, env.stack))["busy"] == "Image-Update eines Containers"
    locks.outside = before
    done = await env.executor.execute(_request("docker.stack_backup_delete", risk="medium"))
    assert done.success is True and not any(p.startswith(FOLDER) for p in env.sftp.files) and FOLDER not in env.sftp.dirs


@pytest.mark.asyncio
@pytest.mark.parametrize("where", [f"{HOME}/nodvard-sicherungen/shop", FOLDER])
@pytest.mark.parametrize(("error", "reason"), [
    (lambda: PermissionError(13, "Permission denied"), "[Errno 13] Permission denied"),
    (lambda: pytest.importorskip("asyncssh").ChannelOpenError(2, "Kanal zu"), "Kanal zu"),
], ids=["rechte", "kanal-code-2"])
async def test_a_folder_that_cannot_be_read_is_an_error_not_an_empty_list(tmp_path, monkeypatch, where, error, reason):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    real = env.sftp.lstat

    async def lstat(path):
        if path == where:
            raise error()
        return await real(path)

    monkeypatch.setattr(env.sftp, "lstat", lstat)
    listing = await env.restore.backups(env.host, env.stack)
    assert listing["error"] == f"Die Sicherungen ließen sich nicht lesen: {reason}" and listing["backups"] == []


@pytest.mark.asyncio
async def test_a_stack_without_containers_says_why_nothing_can_be_restored(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.docker.fake.rows = []
    listing = await env.restore.backups(env.host, env.stack)
    assert listing["note"].startswith("Der Stack hat gerade keine Container") and listing["backups"][0]["volumes"][0]["restorable"] is False


@pytest.mark.asyncio
async def test_restore_stops_unpacks_and_starts_again(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert done.output.startswith("Stack „shop“: shop_data auf den Stand vom ") and "Die Dienste laufen wieder. Prüfung danach: läuft stabil." in done.output
    order = [next(v for v in ("stop", "restore", "start") if (v == "restore" and c in env.restored) or v in c)
             for c in env.docker.calls if c in env.restored or "stop" in c or "start" in c]
    assert order == ["stop", "restore", "start"] and env.checked == ["shop_data"]
    argv = env.restored[0]
    assert argv[argv.index("-v") + 1] == "shop_data:/data" and _verbs(env) == [("stop", ["web"]), ("start", ["web"])]
    assert argv[-2:] == [SHA, str(SIZE)], "das Zurückspielen prüft gegen das, was vorher geprüft wurde"


def _stopped(env):
    return any(c[:3] == ["docker", "compose", "--ansi"] and "stop" in c for c in env.docker.calls) or any("stop" in c for c in env.docker.calls)


def _started(env):
    return any("start" in c for c in env.docker.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(("check", "error"), [
    ((3, "", "gzip: unexpected end of file\n"), "Das Archiv von shop_data ist beschädigt oder unvollständig (gzip: unexpected end of file)."),
    ((5, "", ""), "Im Volume shop_data liegt noch .nodvard-vorher von einem früheren Zurückspielen"),
    ((0, f"{5 * 1024 ** 3} 1048576 {SHA} {SIZE}\n", ""), ("Für shop_data ist zu wenig Platz: Deck entpackt die Sicherung erst neben die "
                                                           "alten Daten und braucht dafür bis zu 5,2 GB, frei sind 1,0 GB.")),
    ((0, "1000 99999999\n", ""), "Das Archiv von shop_data ließ sich nicht prüfen (1000 99999999)."),
    ((0, f"1000 99999999 {'x' * 64} {SIZE}\n", ""), "Das Archiv von shop_data ließ sich nicht prüfen (1000 99999999"),
    ((125, "", "Unable to find image 'busybox:1.37' locally\n"), "Das Archiv von shop_data ließ sich nicht prüfen (Unable to find image"),
])
async def test_the_check_before_stopping_changes_nothing(tmp_path, monkeypatch, check, error):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.check["shop_data"] = check
    done = await env.executor.execute(_request())
    assert done.success is False and error in done.error and done.error.endswith("Zurückgespielt und gestoppt wurde nichts.")
    assert env.restored == [] and not _stopped(env)


@pytest.mark.asyncio
@pytest.mark.parametrize(("state", "error"), [
    ("true", "Für shop_data läuft auf dem Server noch ein Zurückspielen (Container nodvard-zurueck-shop_data)."),
    ("false", ("Auf dem Server steht noch der Hilfscontainer nodvard-zurueck-shop_data von einem früheren Zurückspielen. Sieh im Volume "
               "shop_data nach, ob dort .nodvard-vorher liegt, entferne den Container mit „docker rm nodvard-zurueck-shop_data“ und "
               "versuch es dann noch einmal. Zurückgespielt und gestoppt wurde nichts.")),
    ("fehler", "Ob für shop_data noch ein Zurückspielen läuft, ließ sich nicht feststellen."),
    ("komisch", "Ob für shop_data noch ein Zurückspielen läuft, ließ sich nicht feststellen."),
])
async def test_a_restore_still_running_on_the_server_blocks_a_new_one(tmp_path, monkeypatch, state, error):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.helper["shop_data"] = state
    done = await env.executor.execute(_request())
    assert done.success is False and error in done.error and env.restored == [] and not _stopped(env)


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "error"), [
    ((3, "tar: write error: No space left on device\n"),
     "Zurückspielen von shop_data gescheitert: Im Volume war zu wenig Platz (tar: write error: No space left on device). An shop_data hat sich nichts geändert."),
    ((3, "tar: short read\n"),
     "Zurückspielen von shop_data gescheitert: Das Archiv ist beschädigt oder unvollständig (tar: short read). An shop_data hat sich nichts geändert."),
    ((125, "docker: Error response from daemon: failed to create task\n"),
     "Zurückspielen von shop_data gescheitert (docker: Error response from daemon: failed to create task). An shop_data hat sich nichts geändert."),
    ((7, "mkdir: can't create directory '/data/.nodvard-zurueck': Read-only file system\n"),
     "Zurückspielen von shop_data gescheitert (mkdir: can't create directory '/data/.nodvard-zurueck': Read-only file system)."),
    ((8, ""), ("Das Archiv von shop_data ist nicht mehr das, das Deck vorher geprüft hat. Zurückgespielt wurde es nicht; an shop_data "
               "hat sich nichts geändert.")),
])
async def test_failures_before_the_swap_change_nothing_and_start_again(tmp_path, monkeypatch, answer, error):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.unpack["shop_data"] = answer
    done = await env.executor.execute(_request())
    assert done.success is False and done.error.startswith(error) and done.error.endswith("Die Dienste sind wieder gestartet.") and _started(env)


@pytest.mark.asyncio
async def test_a_failed_swap_says_where_the_data_is_and_keeps_the_services_stopped(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp, files=("shop_cache.tar.gz", "shop_data.tar.gz"))
    env.docker.mounts["web"] = ["shop_data", "shop_cache"]
    env.unpack["shop_data"] = (4, "mv: can't rename '/data/x': Operation not permitted\n")
    done = await env.executor.execute(_request(volumes=("shop_data", "shop_cache")))
    assert done.success is False and "Die bisherigen Daten liegen ganz oder zum Teil im Volume unter .nodvard-vorher" in done.error
    assert "Schon zurückgespielt: shop_cache." in done.error and "Die Dienste bleiben gestoppt (web, worker)" in done.error
    assert not _started(env)


@pytest.mark.asyncio
@pytest.mark.parametrize(("helper", "says"), [
    ("true", "Das Zurückspielen läuft auf dem Server noch (Container nodvard-zurueck-shop_data) und tauscht am Ende die Daten aus."),
    (None, "Ob es fertig wurde, ließ sich nicht feststellen."),
])
@pytest.mark.parametrize("answer", ["timeout", "lost", (137, "Killed\n")])
async def test_without_a_clear_answer_the_services_stay_stopped(tmp_path, monkeypatch, helper, says, answer):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.unpack["shop_data"] = answer
    original = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:2] == ["docker", "run"] and "--name" in argv and helper:
            env.helper["shop_data"] = helper
        return await original(host, argv, **kwargs)

    env.docker.fake.run_argv = run_argv
    done = await env.executor.execute(_request())
    assert done.success is False and "Zurückspielen von shop_data: keine klare Antwort" in done.error and says in done.error
    assert "Die Dienste bleiben gestoppt (web)" in done.error and not _started(env)


@pytest.mark.asyncio
async def test_old_data_that_could_not_be_removed_is_named(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.unpack["shop_data"] = (6, "rm: can't remove '/data/.nodvard-vorher/x': Operation not permitted\n")
    done = await env.executor.execute(_request())
    assert done.success is True and "Die alten Daten von shop_data liegen noch im Volume unter .nodvard-vorher" in done.output


@pytest.mark.asyncio
@pytest.mark.parametrize(("second", "ends"), [
    ((3, "tar: short read\n"), "Die Dienste sind wieder gestartet."),
    ((4, "mv: can't rename '/data/x': Operation not permitted\n"),
     "Die Dienste bleiben gestoppt (web, worker); starte den Stack im Reiter Stacks, wenn die Daten stimmen."),
], ids=["vor-dem-tausch", "beim-tausch"])
async def test_old_data_left_behind_is_named_when_a_later_volume_fails(tmp_path, monkeypatch, second, ends):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp, files=("shop_cache.tar.gz", "shop_data.tar.gz"))
    env.docker.mounts["web"] = ["shop_data", "shop_cache"]
    env.unpack["shop_cache"] = (6, "rm: can't remove '/data/.nodvard-vorher/x': Operation not permitted\n")
    env.unpack["shop_data"] = second
    done = await env.executor.execute(_request(volumes=("shop_data", "shop_cache")))
    assert done.success is False and "Schon zurückgespielt: shop_cache. Die alten Daten von shop_cache liegen noch im Volume unter .nodvard-vorher" in done.error
    assert done.error.endswith(ends)


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["unlesbar", "ohne-container", "schlecht"])
async def test_without_a_good_verdict_after_the_start_the_restore_is_not_clean(tmp_path, monkeypatch, why):
    from nodvard_deck_ext_docker.stacks import StackProblem

    env = await _env(tmp_path, monkeypatch, verdicts=(why != "schlecht",))
    _backup(env.sftp)
    real = env.update.running

    async def running(host, stack):
        if _started(env) and why == "unlesbar":
            raise StackProblem("Docker auf dem Server ließ sich nicht abfragen (die Verbindung zum Server ist abgebrochen).")
        return {} if _started(env) and why == "ohne-container" else await real(host, stack)

    monkeypatch.setattr(env.update, "running", running)
    done = await env.executor.execute(_request())
    says = {
        "unlesbar": "Prüfung danach: nicht möglich. Die Container ließen sich nicht lesen: Docker auf dem Server ließ sich nicht abfragen (die "
                    "Verbindung zum Server ist abgebrochen). Sieh im Reiter Stacks nach, ob sie laufen.",
        "ohne-container": "Prüfung danach: nicht möglich. Docker zeigt zu den Diensten keinen Container. Sieh im Reiter Stacks nach, ob sie laufen.",
        "schlecht": "Prüfung danach: Container startet immer wieder neu.",
    }[why]
    assert done.success is False and _started(env) and done.error.endswith(f"zurückgespielt. Die Dienste sind wieder gestartet. {says}")
    assert "laufen wieder" not in done.error


@pytest.mark.asyncio
async def test_a_stopped_service_on_the_same_volume_stays_stopped(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch, used_by={"shop_data": ["shop-web-1", "shop-worker-1"]})
    env.docker.mounts["worker"] = ["shop_data"]
    env.docker.stopped.add("worker")
    _backup(env.sftp)
    [volume] = (await env.restore.backups(env.host, env.stack))["backups"][0]["volumes"]
    assert volume["services"] == ["web", "worker"] and volume["stopped_services"] == ["worker"]
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert done.output.endswith(f"Die Dienste laufen wieder. Prüfung danach: läuft stabil.{WORKER_NOT_STARTED}")
    assert _verbs(env) == [("stop", ["web"]), ("start", ["web"])]


@pytest.mark.asyncio
@pytest.mark.parametrize("running", [False, True], ids=["gestoppt", "laeuft"])
async def test_a_container_whose_service_left_the_file(tmp_path, monkeypatch, running):
    from test_ext_docker_stack_update import OLD

    env = await _env(tmp_path, monkeypatch)
    env.docker.fake.rows.append(row(id="0ld0ld0ld0ld", name="shop-old-1", service="old", state="running" if running else "exited"))
    env.docker.containers["old"] = ["shop-old-1", "busybox:1.37", OLD]
    env.docker.mounts["old"] = ["shop_data"]
    env.orphans.add("old")
    if not running:
        env.docker.stopped.add("old")
    _backup(env.sftp)
    done = await env.executor.execute(_request())
    if running:
        assert done.error.startswith("Stoppen vor dem Zurückspielen gescheitert: no such service: old Ein laufender Container gehört zu einem "
                                     "Dienst, der nicht mehr in der Compose-Datei steht; halte ihn im Reiter Container an oder entferne ihn, "
                                     "dann versuch es noch einmal. Zurückgespielt wurde nichts;")
        assert env.restored == []
    else:
        assert done.success is True, done.error
        assert _verbs(env) == [("stop", ["web"]), ("start", ["web"])] and " old war gestoppt. Deck hat ihn nicht gestartet;" in done.output


@pytest.mark.asyncio
async def test_a_user_running_after_the_stop_stops_the_restore(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch, used_by={"shop_data": ["shop-web-1", "shop-worker-1"]})
    env.docker.mounts["worker"] = ["shop_data"]
    env.docker.stopped.add("worker")
    _backup(env.sftp)
    inner = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        answer = await inner(host, argv, **kwargs)
        if "stop" in argv:
            env.docker.stopped.discard("worker")  # von außen gestartet, gleich nach dem Stoppen
        return answer

    env.docker.fake.run_argv = run_argv
    done = await env.executor.execute(_request())
    assert done.error.startswith("Nach dem Stoppen laufen noch Container mit diesen Volumes: shop-worker-1. Halte sie im Reiter Container an, "
                                 "dann versuch es noch einmal. Zurückgespielt wurde nichts; die Dienste sind wieder gestartet.")
    assert env.restored == [] and _verbs(env) == [("stop", ["web"]), ("start", ["web"])]


@pytest.mark.asyncio
async def test_an_unreadable_state_after_the_start_says_only_what_was_stopped_before(tmp_path, monkeypatch):
    from nodvard_deck_ext_docker.stacks import StackProblem

    env = await _env(tmp_path, monkeypatch, used_by={"shop_data": ["shop-web-1", "shop-worker-1"]})
    env.docker.mounts["worker"] = ["shop_data"]
    env.docker.stopped.add("worker")
    _backup(env.sftp)
    real = env.update.running

    async def running(host, stack):
        if _started(env):
            raise StackProblem("Docker antwortet nicht.")
        return await real(host, stack)

    monkeypatch.setattr(env.update, "running", running)
    done = await env.executor.execute(_request())
    assert done.success is False and done.error.endswith("Sieh im Reiter Stacks nach, ob sie laufen. worker war vorher gestoppt.")


WORKER_NOT_STARTED = (" worker war gestoppt. Deck hat ihn nicht gestartet; Compose kann ihn aber mitstarten, wenn ein laufender Dienst ihn "
                      "braucht, etwa als Einmal-Dienst für Migrationen.")


def _verbs(env):
    return [(verb, c[c.index(verb) + 1:]) for c in env.docker.calls for verb in ("stop", "start") if verb in c]


def _during_the_check(env, change):
    """`change` läuft, während Deck das Archiv prüft, also nach dem Lesen der Liste und vor dem Stoppen."""
    inner = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:2] == ["docker", "run"] and any(a.endswith(":/data:ro") for a in argv):
            change()
        return await inner(host, argv, **kwargs)

    env.docker.fake.run_argv = run_argv


@pytest.mark.asyncio
async def test_a_service_started_during_the_check_is_stopped_for_the_swap(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch, used_by={"shop_data": ["shop-web-1", "shop-worker-1"]})
    env.docker.mounts["worker"] = ["shop_data"]
    env.docker.stopped.add("worker")
    _backup(env.sftp)
    _during_the_check(env, lambda: env.docker.stopped.discard("worker"))
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert _verbs(env) == [("stop", ["web", "worker"]), ("start", ["web", "worker"])] and "gestoppt" not in done.output


@pytest.mark.asyncio
async def test_a_service_stopped_during_the_check_is_not_started_again(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    _during_the_check(env, lambda: env.docker.stopped.add("web"))
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert _verbs(env) == [] and done.output.endswith("zurückgespielt. web war gestoppt und bleibt es.") and len(env.restored) == 1


@pytest.mark.asyncio
async def test_without_a_fresh_look_at_the_services_nothing_stops(tmp_path, monkeypatch):
    from nodvard_deck_ext_docker.stacks import StackProblem

    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    real = env.update.running

    async def running(host, stack):
        if env.checked:
            raise StackProblem("Docker auf dem Server ließ sich nicht abfragen (die Verbindung zum Server ist abgebrochen).")
        return await real(host, stack)

    monkeypatch.setattr(env.update, "running", running)
    done = await env.executor.execute(_request())
    assert done.error == ("Die Container ließen sich nicht lesen: Docker auf dem Server ließ sich nicht abfragen (die Verbindung zum Server "
                          "ist abgebrochen). Zurückgespielt und gestoppt wurde nichts.")
    assert _verbs(env) == [] and env.restored == []


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_volume", ["shop_data", "shop_cache"], ids=["gleiches-volume", "nur-abhaengigkeit"])
async def test_what_compose_starts_along_is_named(tmp_path, monkeypatch, worker_volume):
    env = await _env(tmp_path, monkeypatch, used_by={"shop_data": ["shop-web-1", "shop-worker-1"]})
    env.docker.mounts["worker"] = [worker_volume]
    env.docker.stopped.add("worker")
    _backup(env.sftp)
    inner = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if "start" in argv:
            env.docker.stopped.discard("worker")  # wie Compose bei einer Abhängigkeit von web
        return await inner(host, argv, **kwargs)

    env.docker.fake.run_argv = run_argv
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert done.output.endswith("Prüfung danach: läuft stabil. Vorher gestoppt, jetzt aber gestartet: shop-worker-1. Compose "
                                "startet Abhängigkeiten und alle Container eines Dienstes mit; soll das nicht laufen, stoppe es im Reiter Container.")
    assert "bleibt es" not in done.output and ("start", ["web"]) in _verbs(env)


@pytest.mark.asyncio
async def test_a_failed_stop_names_the_service_that_was_stopped_before(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch, used_by={"shop_data": ["shop-web-1", "shop-worker-1"]})
    env.docker.mounts["worker"] = ["shop_data"]
    env.docker.stopped.add("worker")
    env.docker.fail["stop"] = "Error: cannot stop\n"
    _backup(env.sftp)
    done = await env.executor.execute(_request())
    assert done.error == ("Stoppen vor dem Zurückspielen gescheitert: Error: cannot stop Zurückgespielt wurde nichts; die Dienste laufen weiter "
                          f"wie bisher.{WORKER_NOT_STARTED}")
    assert _verbs(env) == [("stop", ["web"]), ("start", ["web"])] and env.restored == []


@pytest.mark.asyncio
async def test_a_stopped_stack_is_restored_without_stopping_or_starting(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    env.docker.stopped.update({"web", "worker"})
    _backup(env.sftp)

    async def no_check(*_):
        raise AssertionError("ohne gestartete Dienste gibt es nichts zu prüfen")

    monkeypatch.setattr(env.restore, "check_after", no_check)
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert done.output.startswith("Stack „shop“: shop_data auf den Stand vom ") and done.output.endswith("zurückgespielt. web war gestoppt und bleibt es.")
    assert len(env.restored) == 1 and not _stopped(env) and not _started(env)
    env.unpack["shop_data"] = (3, "tar: short read\n")
    failed = await env.executor.execute(_request())
    assert failed.error.endswith("An shop_data hat sich nichts geändert. web war gestoppt und bleibt es.") and not _started(env)
    env.check["shop_data"] = (3, "", "gzip: unexpected end of file\n")
    refused = await env.executor.execute(_request())
    assert refused.error.endswith("Zurückgespielt und gestoppt wurde nichts.")


@pytest.mark.asyncio
async def test_a_stopped_stack_without_a_clear_answer_says_to_wait_before_starting(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    env.docker.stopped.update({"web", "worker"})
    _backup(env.sftp)
    env.unpack["shop_data"] = "lost"
    done = await env.executor.execute(_request())
    assert done.error.endswith("web war gestoppt und bleibt es. Starte den Stack im Reiter Stacks erst, wenn das Zurückspielen fertig ist "
                               "und die Daten stimmen.")
    assert "Die Dienste bleiben gestoppt" not in done.error and not _started(env)


@pytest.mark.asyncio
async def test_a_stopped_stack_names_a_user_that_runs_after_the_swap(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch, used_by={"shop_data": ["shop-web-1", "shop-worker-1"]})
    env.docker.mounts["worker"] = ["shop_data"]
    env.docker.stopped.update({"web", "worker"})
    _backup(env.sftp)
    listing = await env.restore.backups(env.host, env.stack)
    assert listing["backups"][0]["volumes"][0]["stopped_services"] == ["web", "worker"]
    done = await env.executor.execute(_request())
    assert done.success is True and done.output.endswith("zurückgespielt. web, worker waren gestoppt und bleiben es.")
    inner = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if "--name" in argv:
            env.docker.stopped.discard("web")  # von außen gestartet, während der Hilfscontainer tauscht
        return await inner(host, argv, **kwargs)

    env.docker.fake.run_argv = run_argv
    woke = await env.executor.execute(_request())
    assert woke.success is False and woke.error.endswith("zurückgespielt. Inzwischen läuft shop-web-1, obwohl Deck nichts gestartet hat; die "
                                                         "Daten können schon während des Tauschs benutzt worden sein. Sieh nach, ob sie stimmen.")
    assert _verbs(env) == []


@pytest.mark.asyncio
async def test_a_cancel_in_a_stopped_stack_starts_nothing(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    env.docker.stopped.update({"web", "worker"})
    _backup(env.sftp)
    env.unpack["hang"] = 1
    task = asyncio.create_task(env.executor.execute(_request()))
    await _until(lambda: env.restored)
    env.helper.clear()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not _started(env) and env.update.actions.locks.held("h1", "shop") is None


@pytest.mark.asyncio
async def test_a_failed_start_after_restore_is_not_hidden(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.docker.fail["start"] = "Error: port is already allocated\n"
    done = await env.executor.execute(_request())
    assert done.success is False and "auf den Stand vom" in done.error
    assert "Die Dienste ließen sich danach nicht wieder starten (Wieder starten gescheitert: Error: port is already allocated)" in done.error


@pytest.mark.asyncio
async def test_a_cancel_while_the_helper_runs_keeps_the_services_stopped(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.unpack["hang"] = 1
    task = asyncio.create_task(env.executor.execute(_request()))
    await _until(lambda: env.restored)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(20):
        await asyncio.sleep(0)
    assert not _started(env) and env.update.actions.locks.held("h1", "shop") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("leftovers", "starts"), [(0, True), (1, False), ("lost", False)], ids=["sauber", "reste", "keine-antwort"])
async def test_a_cancel_after_the_helper_ended_starts_again_only_without_leftovers(tmp_path, monkeypatch, leftovers, starts):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    env.unpack["hang"] = 1
    env.leftovers["shop_data"] = leftovers
    task = asyncio.create_task(env.executor.execute(_request()))
    await _until(lambda: env.restored)
    env.helper.clear()  # der Hilfscontainer hat sich mit --rm schon weggeräumt
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(20):
        await asyncio.sleep(0)
    assert _started(env) is starts and any(c[-1] == _mod()._LEFTOVER_SCRIPT for c in env.docker.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("dirty", ["shop_data", "shop_cache"])
async def test_a_cancel_with_leftovers_in_one_of_two_volumes_keeps_the_stack_stopped(tmp_path, monkeypatch, caplog, dirty):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp, files=("shop_data.tar.gz", "shop_cache.tar.gz"))
    env.unpack["hang"] = 1
    env.leftovers[dirty] = 1
    task = asyncio.create_task(env.executor.execute(_request(volumes=("shop_data", "shop_cache"))))
    await _until(lambda: env.restored)
    env.helper.clear()
    task.cancel()
    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(20):
        await asyncio.sleep(0)
    assert not _started(env) and sum(c[-1] == _mod()._LEFTOVER_SCRIPT for c in env.docker.calls) == 2
    assert any("docker_restore_kept_stopped project=shop" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_user_from_outside_after_the_stop_stops_the_restore(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    inner = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        answer = await inner(host, argv, **kwargs)
        if "stop" in argv:
            env.users["shop_data"] = ["shop-web-1", "fremd-1"]
        return answer

    env.docker.fake.run_argv = run_argv
    done = await env.executor.execute(_request())
    assert done.error.startswith("Inzwischen nutzt ein Container außerhalb des Stacks die Volumes: shop_data nutzt auch fremd-1. Halte ihn "
                                 "an oder nimm ihn aus dem Volume, dann versuch es noch einmal. Zurückgespielt wurde nichts; die Dienste sind "
                                 "wieder gestartet.")
    assert env.restored == [] and _verbs(env) == [("stop", ["web"]), ("start", ["web"])]


@pytest.mark.asyncio
async def test_a_user_from_outside_during_the_check_of_a_stopped_stack_stops_the_restore(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    env.docker.stopped.update({"web", "worker"})
    _backup(env.sftp)
    _during_the_check(env, lambda: env.users.update({"shop_data": ["shop-web-1", "fremd-1"]}))
    done = await env.executor.execute(_request())
    assert done.error.endswith("shop_data nutzt auch fremd-1. Halte ihn an oder nimm ihn aus dem Volume, dann versuch es noch einmal. "
                               "Zurückgespielt wurde nichts.")
    assert env.restored == [] and _verbs(env) == []


@pytest.mark.asyncio
async def test_without_a_second_look_at_other_users_nothing_is_restored(tmp_path, monkeypatch):
    from nodvard_deck_ext_docker.resources import ResourceProblem

    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    first = env.restore.resources.snapshot

    def snapshot(host):
        if _stopped(env):
            raise ResourceProblem("Docker antwortet nicht.")
        return first(host)

    env.restore.resources.snapshot = snapshot
    done = await env.executor.execute(_request())
    assert done.error.startswith("Wer die Volumes sonst noch nutzt, ließ sich nicht lesen: Docker antwortet nicht. Zurückgespielt wurde "
                                 "nichts; die Dienste sind wieder gestartet.")
    assert env.restored == []


@pytest.mark.asyncio
async def test_a_folder_that_is_no_real_date_is_left_out(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    _backup(env.sftp, stamp="99999999-999999")
    listing = await env.restore.backups(env.host, env.stack)
    assert listing["error"] is None and [b["stamp"] for b in listing["backups"]] == [STAMP]
    for action in ("docker.stack_volume_restore", "docker.stack_backup_delete"):
        done = await env.executor.execute(_request(action=action, stamp="99999999-999999"))
        assert done.error == "Ungültige Sicherung: '99999999-999999'"
    assert env.restored == [] and f"{HOME}/nodvard-sicherungen/shop/99999999-999999" in env.sftp.dirs


@pytest.mark.asyncio
async def test_a_cancel_while_stopping_starts_the_services_again(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    original = env.docker.fake.run_argv
    stopping = asyncio.Event()

    async def run_argv(host, argv, **kwargs):
        if "stop" in argv:
            env.docker.calls.append(list(argv))
            stopping.set()
            await asyncio.sleep(3600)
        return await original(host, argv, **kwargs)

    env.docker.fake.run_argv = run_argv
    task = asyncio.create_task(env.executor.execute(_request()))
    await stopping.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(20):
        await asyncio.sleep(0)
    assert _started(env) and env.restored == []


@pytest.mark.asyncio
async def test_a_lost_connection_while_stopping_tries_to_start_again(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    original = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if "stop" in argv:
            raise ConnectionError("Verbindung verloren")
        return await original(host, argv, **kwargs)

    env.docker.fake.run_argv = run_argv
    done = await env.executor.execute(_request())
    assert done.error.startswith("Stoppen vor dem Zurückspielen gescheitert: Verbindung verloren Zurückgespielt wurde nichts")
    assert _started(env) and env.restored == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("request_", "prepare", "error"), [
    ({"actor": "ai"}, None, "Eine Volume-Sicherung spielt nur ein Mensch"),
    ({"risk": "medium"}, None, "Das Zurückspielen braucht eine Freigabe mit hohem Risiko."),
    ({"stamp": "gestern"}, None, "Ungültige Sicherung: 'gestern'"),
    ({"volumes": ()}, None, "Ungültige Auswahl der Volumes."),
    ({"volumes": ("a;b",)}, None, "Ungültige Auswahl der Volumes."),
    ({"stamp": OLDER}, None, "Diese Sicherung gibt es nicht mehr. Zurückgespielt wurde nichts."),
    ({"volumes": ("shop_cache",)}, None, "In dieser Sicherung fehlt shop_cache. Zurückgespielt wurde nichts."),
    ({}, "foreign", "shop_data nutzt auch backup-tool. Halte diese Container an"),
    ({}, "not_mounted", "shop_data nutzt der Stack heute nicht mehr"),
    ({}, "stop_fails", "Stoppen vor dem Zurückspielen gescheitert: Error: cannot stop Zurückgespielt wurde nichts; die Dienste laufen weiter"),
])
async def test_refusals_unpack_nothing(tmp_path, monkeypatch, request_, prepare, error):
    env = await _env(tmp_path, monkeypatch, used_by={"shop_data": ["shop-web-1", "backup-tool"]} if prepare == "foreign" else None)
    _backup(env.sftp)
    if prepare == "not_mounted":
        env.docker.mounts["web"] = []
    if prepare == "stop_fails":
        env.docker.fail["stop"] = "Error: cannot stop\n"
    done = await env.executor.execute(_request(**request_))
    assert done.success is False and error in done.error and env.restored == []
    if prepare == "stop_fails":
        assert _started(env)


@pytest.mark.asyncio
async def test_one_change_per_stack(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    with env.update.actions.locks.hold("h1", "shop", "Update"):
        done = await env.executor.execute(_request())
    assert done.error == "An diesem Stack läuft gerade schon etwas (Update). Versuch es danach noch einmal." and env.restored == []


@pytest.mark.asyncio
async def test_deleting_a_backup_takes_only_its_archives(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp, files=("shop_data.tar.gz", "shop_cache.tar.gz", "inhalt.json"))
    _backup(env.sftp, OLDER, ("shop_data.tar.gz", "notiz.txt"))
    removed = []
    remove = env.sftp.remove

    async def recorded(path):
        removed.append(path.rsplit("/", 1)[1])
        await remove(path)

    monkeypatch.setattr(env.sftp, "remove", recorded)
    done = await env.executor.execute(_request("docker.stack_backup_delete", risk="medium"))
    assert done.success is True and done.output.startswith("Sicherung vom ") and FOLDER not in env.sftp.dirs
    assert not any(p.startswith(FOLDER) for p in env.sftp.files) and removed[-1] == "inhalt.json"
    odd = await env.executor.execute(_request("docker.stack_backup_delete", stamp=OLDER, risk="medium"))
    assert odd.success is False and "liegt noch anderes (notiz.txt)" in odd.error
    assert f"{HOME}/nodvard-sicherungen/shop/{OLDER}/shop_data.tar.gz" in env.sftp.files
    gone = await env.executor.execute(_request("docker.stack_backup_delete", risk="medium"))
    assert gone.error == "Diese Sicherung gibt es nicht mehr."


@pytest.mark.asyncio
async def test_deleting_refuses_a_link_and_deletes_nothing(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp, files=("shop_data.tar.gz", "shop_cache.tar.gz"))
    env.sftp.modes[f"{FOLDER}/shop_cache.tar.gz"] = 0o120777
    done = await env.executor.execute(_request("docker.stack_backup_delete", risk="medium"))
    assert done.error == "shop_cache.tar.gz ist keine normale Datei. Gelöscht wurde nichts."
    assert {f"{FOLDER}/shop_data.tar.gz", f"{FOLDER}/shop_cache.tar.gz"} <= set(env.sftp.files)


@pytest.mark.asyncio
async def test_a_delete_that_breaks_off_keeps_the_contents_list(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp, files=("shop_data.tar.gz", "shop_cache.tar.gz", "inhalt.json"))
    remove = env.sftp.remove

    async def breaks_off(path):
        if path.endswith("shop_data.tar.gz"):
            raise ConnectionError("Verbindung verloren")
        await remove(path)

    monkeypatch.setattr(env.sftp, "remove", breaks_off)
    done = await env.executor.execute(_request("docker.stack_backup_delete", risk="medium"))
    assert done.error == "Die Sicherung ließ sich nicht ganz löschen: Verbindung verloren"
    assert f"{FOLDER}/inhalt.json" in env.sftp.files and FOLDER in env.sftp.dirs


@pytest.mark.asyncio
async def test_without_time_for_the_check_nothing_stops(tmp_path, monkeypatch):
    m = _mod()
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    monkeypatch.setattr(m, "RESTORE_UNTIL_S", -10**9)
    done = await env.executor.execute(_request())
    assert done.success is False and "Für das Prüfen der Archive reichte die Zeit nicht." in done.error
    assert not _stopped(env) and env.restored == []


@pytest.mark.asyncio
async def test_running_out_of_time_names_the_volumes_left_untouched(tmp_path, monkeypatch):
    m = _mod()
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp, files=("shop_cache.tar.gz", "shop_data.tar.gz"))
    env.docker.mounts["web"] = ["shop_data", "shop_cache"]
    original = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        answer = await original(host, argv, **kwargs)
        if env.restored:
            monkeypatch.setattr(m, "RESTORE_UNTIL_S", -10**9)
        return answer

    env.docker.fake.run_argv = run_argv
    done = await env.executor.execute(_request(volumes=("shop_data", "shop_cache")))
    assert done.success is False and "Für das Zurückspielen reichte die Zeit nicht; an shop_data hat sich nichts geändert." in done.error
    assert "Schon zurückgespielt: shop_cache." in done.error and len(env.restored) == 1 and _started(env)


@pytest.mark.asyncio
async def test_the_preview_names_what_would_happen(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    restore = await env.executor.dry_run(_request(volumes=("shop_data", "shop_cache")))
    drop = await env.executor.dry_run(_request("docker.stack_backup_delete", risk="medium"))
    assert restore.would_change and restore.summary == "Würde shop_data, shop_cache im Stack „shop“ zurückspielen."
    assert drop.would_change and drop.summary == f"Würde die Sicherung {STAMP} von „shop“ löschen."


# --- Durch den echten Kern -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_through_the_gate(client, docker_on, monkeypatch):
    from test_ext_docker_stack_update import _adopted

    docker = await _adopted(client, docker_on, monkeypatch)
    _backup(docker_on.exec.sftp_server)
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/backups"
    listing = await client.get(base, headers=docker_on.owner)
    assert listing.status_code == 200 and listing.json()["backups"][0]["volumes"][0]["name"] == "shop_data"
    assert (await client.post(f"{base}/{STAMP}/restore", json={"volumes": ["shop_cache"]}, headers=docker_on.owner)).status_code == 409
    assert (await client.post(f"{base}/jetzt/restore", json={"volumes": ["shop_data"]}, headers=docker_on.owner)).status_code == 400
    assert (await client.post(f"{base}/99999999-999999/restore", json={"volumes": ["shop_data"]}, headers=docker_on.owner)).status_code == 400
    proposed = await client.post(f"{base}/{STAMP}/restore", json={"volumes": ["shop_data"]}, headers=docker_on.owner)
    assert proposed.status_code == 202 and proposed.json()["risk"] == "high"
    assert not any(c[:2] == ["docker", "run"] or "stop" in c for c in docker.calls), "vor der Freigabe läuft nichts"
    action = await client.get(f"/api/v1/actions/{proposed.json()['action_id']}", headers=docker_on.owner)
    assert "Volumes von „shop“ auf den Stand vom" in action.text and "zurückspielen: shop_data." in action.text
    dropped = await client.post(f"{base}/{STAMP}/delete", headers=docker_on.owner)
    assert dropped.status_code == 202 and dropped.json()["risk"] == "medium"
    bad_volume = await client.post(f"{base}/{STAMP}/restore", json={"volumes": ["../etc"]}, headers=docker_on.owner)
    assert bad_volume.status_code == 400 and bad_volume.json()["detail"] == "Ungültiger Name eines Volumes."
    bad_stack = await client.get(f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/Shop!/backups", headers=docker_on.owner)
    assert bad_stack.status_code == 400 and bad_stack.json()["detail"] == "Ungültiger Stack-Name."


@pytest.mark.asyncio
async def test_routes_need_hosts_execute_and_are_not_on_the_server_page(client, docker_on):
    for action in ("docker.stack_volume_restore", "docker.stack_backup_delete"):
        refused = await client.post(f"/api/v1/hosts/{docker_on.host}/actions/{action}",
                                    json={"payload": {"project": "shop", "stamp": STAMP}, "reason": "Test"}, headers=docker_on.owner)
        assert refused.status_code == 404
    base = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/backups"
    for method, url, kwargs in (("get", base, {}), ("post", f"{base}/{STAMP}/restore", {"json": {"volumes": ["shop_data"]}}),
                                ("post", f"{base}/{STAMP}/delete", {})):
        assert (await getattr(client, method)(url, **kwargs)).status_code == 401
        assert (await getattr(client, method)(url, headers=docker_on.viewer, **kwargs)).status_code == 403


def _list_refused(monkeypatch, sftp):
    real = sftp.lstat

    async def lstat(path):
        if path == f"{HOME}/nodvard-sicherungen/shop":
            raise PermissionError(13, "Permission denied")
        return await real(path)

    monkeypatch.setattr(sftp, "lstat", lstat)


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "tail", "kwargs"), [
    ("get", "", {}),
    ("post", f"/{STAMP}/restore", {"json": {"volumes": ["shop_data"]}}),
    ("post", f"/{STAMP}/delete", {}),
], ids=["liste", "zurueckspielen", "loeschen"])
async def test_a_stack_that_is_not_adopted_answers_409(client, docker_on, method, tail, kwargs):
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/backups{tail}"
    refused = await getattr(client, method)(url, headers=docker_on.owner, **kwargs)
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Der Stack „shop“ ist nicht übernommen. Übernimm ihn zuerst im Reiter Stacks."
    assert (await client.get("/api/v1/actions", headers=docker_on.owner)).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("tail", "kwargs"), [("restore", {"json": {"volumes": ["shop_data"]}}), ("delete", {})])
@pytest.mark.parametrize(("unreadable", "detail"), [
    (False, "Diese Sicherung gibt es nicht mehr."),
    (True, "Die Sicherungen ließen sich nicht lesen: [Errno 13] Permission denied"),
], ids=["weg", "unlesbar"])
async def test_a_backup_that_is_gone_or_unreadable_answers_409(client, docker_on, monkeypatch, tail, kwargs, unreadable, detail):
    from test_ext_docker_stack_update import _adopted

    await _adopted(client, docker_on, monkeypatch)
    sftp = docker_on.exec.sftp_server
    if unreadable:
        _backup(sftp)
        _list_refused(monkeypatch, sftp)
    url = f"/api/v1/ext/docker/hosts/{docker_on.host}/stacks/shop/backups/{STAMP}/{tail}"
    refused = await client.post(url, headers=docker_on.owner, **kwargs)
    assert refused.status_code == 409 and refused.json()["detail"] == detail
    assert (await client.get("/api/v1/actions", headers=docker_on.owner)).json() == []


# --- Fehler und Grenzfälle beim Zurückspielen ------------------------------------------------------------------------


def _breaks_home(monkeypatch, env):
    async def realpath(path):
        raise OSError("Kanal zu")

    monkeypatch.setattr(env.sftp, "realpath", realpath)


def _aimed_at(req, *, project=None, host=None):
    payload = {**req.payload, "project": project or req.payload["project"]}
    return req.model_copy(update={"payload": payload, "host_ref": host or req.host_ref})


@pytest.mark.asyncio
async def test_without_a_readable_home_there_is_no_list(tmp_path, monkeypatch):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    _breaks_home(monkeypatch, env)
    assert await env.restore.backups(env.host, env.stack) == {
        "backups": [], "folder": None, "note": None, "busy": None,
        "error": "Der Heimatordner des Zugangs von Nodvard Deck ließ sich nicht lesen.",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "risk", "broken", "error"), [
    ("docker.stack_volume_restore", "high", "home",
     "Der Heimatordner des Zugangs von Nodvard Deck ließ sich nicht lesen. Zurückgespielt wurde nichts."),
    ("docker.stack_volume_restore", "high", "liste",
     "Die Sicherungen ließen sich nicht lesen: [Errno 13] Permission denied Zurückgespielt wurde nichts."),
    ("docker.stack_backup_delete", "medium", "home", "Der Heimatordner des Zugangs von Nodvard Deck ließ sich nicht lesen."),
], ids=["zurueckspielen-ohne-home", "zurueckspielen-ohne-liste", "loeschen-ohne-home"])
async def test_a_list_that_cannot_be_read_changes_nothing(tmp_path, monkeypatch, action, risk, broken, error):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    if broken == "home":
        _breaks_home(monkeypatch, env)
    else:
        _list_refused(monkeypatch, env.sftp)
    done = await env.executor.execute(_request(action, risk=risk))
    assert done.success is False and done.error == error
    assert env.restored == [] and not _stopped(env) and f"{FOLDER}/shop_data.tar.gz" in env.sftp.files


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "message", "shown"), [
    ("StackProblem", "Docker antwortet nicht.", "Docker antwortet nicht."),
    ("OSError", "Verbindung weg", "Verbindung weg"),
    ("OSError", "", "OSError"),
], ids=["stack-problem", "os-error", "ohne-text"])
async def test_containers_that_cannot_be_read_make_the_list_an_error(tmp_path, monkeypatch, kind, message, shown):
    from nodvard_deck_ext_docker.stacks import StackProblem

    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)

    async def running(host, stack):
        raise (StackProblem if kind == "StackProblem" else OSError)(message)

    monkeypatch.setattr(env.update, "running", running)
    assert await env.restore.backups(env.host, env.stack) == {
        "backups": [], "folder": f"{HOME}/nodvard-sicherungen/shop/", "note": None, "busy": None,
        "error": f"Die Container des Stacks ließen sich nicht lesen: {shown}",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [0o100644, 0o120777], ids=["datei", "verweis"])
async def test_a_stamp_that_is_not_a_folder_is_not_a_backup(tmp_path, monkeypatch, mode):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    odd = f"{HOME}/nodvard-sicherungen/shop/{OLDER}"
    env.sftp.files[odd] = b""
    env.sftp.modes[odd] = mode
    listing = await env.restore.backups(env.host, env.stack)
    assert listing["error"] is None and [b["stamp"] for b in listing["backups"]] == [STAMP]
    done = await env.executor.execute(_request(stamp=OLDER))
    assert done.error == "Diese Sicherung gibt es nicht mehr. Zurückgespielt wurde nichts."
    assert env.restored == [] and not _stopped(env)
    dropped = await env.executor.execute(_request("docker.stack_backup_delete", stamp=OLDER, risk="medium"))
    assert dropped.error == "Diese Sicherung gibt es nicht mehr." and odd in env.sftp.files


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "risk"), [("docker.stack_volume_restore", "high"), ("docker.stack_backup_delete", "medium")])
@pytest.mark.parametrize(("aim", "error"), [
    ({"project": "media"}, "Der Stack „media“ ist nicht übernommen. Übernimm ihn zuerst im Reiter Stacks."),
    ({"host": "fremd"}, "Server nicht gefunden."),
], ids=["nicht-uebernommen", "fremder-server"])
async def test_a_target_that_is_refused_changes_nothing(tmp_path, monkeypatch, action, risk, aim, error):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    done = await env.executor.execute(_aimed_at(_request(action, risk=risk), **aim))
    assert done.success is False and done.error == error
    assert env.docker.calls == [] and env.restored == [] and f"{FOLDER}/shop_data.tar.gz" in env.sftp.files


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConnectionError("Verbindung verloren"), TimeoutError()], ids=["abbruch", "zeit"])
async def test_a_helper_that_cannot_be_asked_blocks_the_restore(tmp_path, monkeypatch, error):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    inner = env.docker.fake.run_argv

    async def run_argv(host, argv, **kwargs):
        if argv[:3] == ["docker", "container", "inspect"] and argv[-1].startswith("nodvard-zurueck-"):
            raise error
        return await inner(host, argv, **kwargs)

    env.docker.fake.run_argv = run_argv
    done = await env.executor.execute(_request())
    assert done.error == ("Ob für shop_data noch ein Zurückspielen läuft, ließ sich nicht feststellen. "
                          "Zurückgespielt und gestoppt wurde nichts.")
    assert env.checked == [] and env.restored == [] and not _stopped(env)


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "lost"), [
    (ConnectionError("Verbindung verloren"), "Verbindung verloren"),
    (TimeoutError(), "nach 15 Minuten ohne Ende abgebrochen"),
    (RuntimeError(), "RuntimeError"),
], ids=["abbruch", "zeit", "ohne-text"])
async def test_an_archive_check_without_an_answer_changes_nothing(tmp_path, monkeypatch, error, lost):
    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)

    def breaks():
        raise error

    _during_the_check(env, breaks)
    done = await env.executor.execute(_request())
    assert done.error == f"Das Archiv von shop_data ließ sich nicht prüfen: {lost}. Zurückgespielt und gestoppt wurde nichts."
    assert env.restored == [] and not _stopped(env)


@pytest.mark.asyncio
@pytest.mark.parametrize(("why", "error"), [
    ("snapshot", "Wer die Volumes sonst noch nutzt, ließ sich nicht lesen: Docker ließ sich nicht abfragen."),
    ("container", "Die Container des Stacks ließen sich nicht lesen: Docker antwortet nicht."),
    ("ohne-text", "Die Container des Stacks ließen sich nicht lesen: OSError"),
], ids=["snapshot", "container", "ohne-text"])
async def test_unknown_other_users_of_the_volumes_stop_the_restore(tmp_path, monkeypatch, why, error):
    from nodvard_deck_ext_docker.resources import ResourceProblem
    from nodvard_deck_ext_docker.stacks import StackProblem

    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    resources, asked = env.restore.resources, []
    snapshot = resources.snapshot

    async def counting(host):
        asked.append(host.id)
        if why == "snapshot":
            raise ResourceProblem("Docker ließ sich nicht abfragen.")
        return await snapshot(host)

    resources.snapshot = counting
    real = env.update.running

    async def running(host, stack):
        if asked and why != "snapshot":
            raise StackProblem("Docker antwortet nicht.") if why == "container" else OSError()
        return await real(host, stack)

    monkeypatch.setattr(env.update, "running", running)
    done = await env.executor.execute(_request())
    assert done.success is False and done.error == f"{error} Zurückgespielt wurde nichts."
    assert asked and env.checked == [] and env.restored == [] and not _stopped(env)


@pytest.mark.asyncio
async def test_an_unreadable_state_after_the_stop_does_not_unpack(tmp_path, monkeypatch):
    from nodvard_deck_ext_docker.stacks import StackProblem

    env = await _env(tmp_path, monkeypatch)
    _backup(env.sftp)
    real = env.update.running

    async def running(host, stack):
        if "web" in env.docker.stopped:
            raise StackProblem("Docker antwortet nicht.")
        return await real(host, stack)

    monkeypatch.setattr(env.update, "running", running)
    done = await env.executor.execute(_request())
    assert done.error == ("Ob nach dem Stoppen noch etwas läuft, ließ sich nicht prüfen. Die Container ließen sich nicht lesen: "
                          "Docker antwortet nicht. Zurückgespielt wurde nichts; die Dienste sind wieder gestartet.")
    assert env.restored == [] and _verbs(env) == [("stop", ["web"]), ("start", ["web"])]


@pytest.mark.asyncio
async def test_an_unreadable_state_after_the_swap_of_a_stopped_stack_still_counts_as_restored(tmp_path, monkeypatch):
    from nodvard_deck_ext_docker.stacks import StackProblem

    env = await _env(tmp_path, monkeypatch)
    env.docker.stopped.update({"web", "worker"})
    _backup(env.sftp)
    real = env.update.running

    async def running(host, stack):
        if env.restored:
            raise StackProblem("Docker antwortet nicht.")
        return await real(host, stack)

    monkeypatch.setattr(env.update, "running", running)
    done = await env.executor.execute(_request())
    assert done.success is True, done.error
    assert len(env.restored) == 1 and not _stopped(env) and not _started(env)
