"""Nodvard Shield: Quarantäne, Wiederherstellen, Löschen, Fail2ban und Installieren über das Hilfsskript `shield-root`
(ab Version 3, Quarantäne und Wiederherstellen in der Form ab Version 8).

Gleichlauf: Der bisherige Befehl und das Skript laufen in gleich vorbereiteten Ordnern. Ausgabe (nach `helper_text`),
Rückgabewert und der Zustand der Dateien danach müssen gleich sein. Das Skript meldet Sätze als `@@msg=<kennung>` (nur
ASCII), das Modul setzt denselben Satz ein wie bei seinem eigenen Befehl. Bewusst anders: Das Skript verschiebt nur, was
ClamAV selbst findet (geprüft im Ordner `.pruefen`, in dem nur root schreibt), und legt nur an den Ort zurück, den es
dabei hinterlegt hat (das Dashboard gilt ihm nicht als vertrauenswürdig)."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

EXT_DIR = Path(__file__).resolve().parents[2] / "extensions" / "shield"
SCRIPT = EXT_DIR / "root-helper" / "shield-root.sh"
SH = shutil.which("sh") or "sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="braucht eine echte POSIX-Shell")


@pytest.fixture(autouse=True)
def _ext_shield_on_path(monkeypatch):
    monkeypatch.syspath_prepend(str(EXT_DIR / "src"))
    yield
    for name in list(sys.modules):
        if name.startswith("nodvard_deck_ext_shield"):
            del sys.modules[name]


def _av():
    from nodvard_deck_ext_shield import antivirus

    return antivirus


def _helper(base: Path, *, path: str = "/usr/bin:/bin") -> Path:
    """Kopie des echten Skripts mit eigener Quarantäne unter `base/q` und dem angegebenen festen PATH."""
    text = SCRIPT.read_text(encoding="ascii")
    text = text.replace("PATH=/usr/sbin:/usr/bin:/sbin:/bin", f"PATH={path}")
    text = re.sub(r"(?m)^QDIR=.*$", f"QDIR={base / 'q'}", text)
    helper = base / "shield-root"
    helper.write_text(text)
    helper.chmod(0o755)
    return helper


# Attrappen für ClamAV: Eine Datei mit "eicar" im Inhalt ist ein Fund. `clamd`: "an" (antwortet auf --ping), "aus";
# `clamscan`: "ok" oder "fehler" (Rückgabewert 2). Jeder Aufruf landet in `aufrufe.log`.
_CLAMSCAN = """#!/bin/sh
echo "clamscan $*" >> {log}
[ {mode} = fehler ] && exit 2
for a; do f=$a; done
if grep -q eicar "$f" 2>/dev/null; then echo "$f: Eicar-Test-Signature FOUND"; exit 1; fi
echo "$f: OK"
"""
_CLAMDSCAN = """#!/bin/sh
echo "clamdscan $*" >> {log}
[ {clamd} = an ] || exit 2
[ "$1" = --ping ] && exit 0
for a; do f=$a; done
if [ {clamd_finds} = ja ] && grep -q eicar "$f" 2>/dev/null; then echo "$f: Eicar-Test-Signature FOUND"; exit 1; fi
echo "$f: OK"
"""


def _scanner(base: Path, *, clamscan: str = "ok", clamd: str = "aus", clamd_finds: str = "ja") -> str:
    """Ordner mit den Attrappen (neben `base`, damit `_state` sie nicht sieht) und der PATH für das Skript dazu."""
    tools = base.parent / f"{base.name}-werkzeug"
    tools.mkdir(parents=True, exist_ok=True)
    log = tools / "aufrufe.log"
    for name, body in (("clamscan", _CLAMSCAN), ("clamdscan", _CLAMDSCAN)):
        (tools / name).write_text(body.format(log=log, mode=clamscan, clamd=clamd, clamd_finds=clamd_finds))
        (tools / name).chmod(0o755)
    return f"{tools}:/usr/bin:/bin"


def _calls(base: Path) -> list[str]:
    log = base.parent / f"{base.name}-werkzeug" / "aufrufe.log"
    return log.read_text().splitlines() if log.exists() else []


def _run_old(command: str, *, env_path: str = "/usr/bin:/bin") -> tuple[int, str]:
    done = subprocess.run([SH, "-c", command], capture_output=True, text=True, timeout=60, check=False, env={"PATH": env_path, "LC_ALL": "C"})
    return done.returncode, done.stdout


def _run_new(helper: Path, *args: str) -> tuple[int, str]:
    done = subprocess.run([SH, str(helper), *args], capture_output=True, text=True, timeout=60, check=False, env={"LC_ALL": "C"})
    return done.returncode, _av().helper_text(done.stdout)


def _state(base: Path) -> dict[str, str]:
    """Was unter `base` liegt (ohne das Skript): Art, Rechte und Inhalt, mit Pfaden relativ zu `base`."""
    out = {}
    for path in sorted(base.rglob("*")):
        rel = str(path.relative_to(base))
        if rel == "shield-root":
            continue
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode):
            out[rel] = "link->" + os.readlink(path).replace(str(base), "BASE")
        elif stat.S_ISDIR(info.st_mode):
            out[rel] = f"dir {stat.S_IMODE(info.st_mode):o}"
        elif stat.S_ISFIFO(info.st_mode):
            out[rel] = "fifo"
        else:
            # Ohne root ist eine Datei mit Rechten 000 nicht lesbar; verglichen wird dann nur, dass sie es nicht ist.
            raw = path.read_bytes() if os.access(path, os.R_OK) else b"<nicht lesbar>"
            data = raw.replace(str(base).encode(), b"BASE")
            out[rel] = f"file {stat.S_IMODE(info.st_mode):o} {info.st_nlink} {data!r}"
    return out


def _scene(base: Path, kind: str) -> str:
    """Legt eine Ausgangslage unter `base` an und gibt den Pfad der gefundenen Datei zurück."""
    work = base / "work"
    work.mkdir(parents=True)
    victim = work / "evil.sh"
    if kind == "normal":
        victim.write_text("x5o!p%@ap[4\\pzx54(p^)7cc)7}$eicar")
        victim.chmod(0o750)
    elif kind == "symlink":
        (base / "ziel").write_text("geheim")
        victim.symlink_to(base / "ziel")
    elif kind == "path_link":
        real = base / "echt"
        real.mkdir()
        (real / "evil.sh").write_text("x")
        work.rmdir()
        work.symlink_to(real, target_is_directory=True)
    elif kind == "hardlink":
        victim.write_text("x")
        os.link(victim, base / "zweiter-name")
    elif kind == "gone":
        pass
    return str(victim)


@pytest.mark.parametrize("kind", ["normal", "symlink", "path_link", "hardlink", "gone"])
def test_quarantine_behaves_like_the_old_command(tmp_path, monkeypatch, kind):
    av = _av()
    old_base, new_base = tmp_path / "alt", tmp_path / "neu"
    old_victim, new_victim = _scene(old_base, kind), _scene(new_base, kind)
    monkeypatch.setattr(av, "QUARANTINE_DIR", str(old_base / "q"))
    old = _run_old(av.quarantine_command(old_victim, "f1_evil.sh"))
    new = _run_new(_helper(new_base, path=_scanner(new_base)), *av.quarantine_args(new_victim, "f1_evil.sh"))
    assert new == old
    assert _state(new_base) == _state(old_base)
    if kind == "normal":
        state = _state(new_base)
        assert old == (0, "@@mode=750\n") and state["q/f1_evil.sh"].startswith("file 0 1 ")
        assert state["q"] == state["q/.herkunft"] == state["q/.pruefen"] == "dir 700"
        assert state["q/.herkunft/f1_evil.sh"] == "file 600 1 b'750\\nBASE/work/evil.sh\\n'"


def _quarantined(base: Path, monkeypatch, *, mode: str = "640", origin: bool = True) -> str:
    """Eine Datei, die schon in der Quarantäne liegt (wie nach `quarantine`), mit Rechten und Ursprung in der Ablage."""
    (base / "q" / ".herkunft").mkdir(parents=True)
    (base / "q").chmod(0o700)
    (base / "q" / ".herkunft").chmod(0o700)
    (base / "work").mkdir()
    target = base / "q" / "f1_evil.sh"
    target.write_text("inhalt")
    target.chmod(0)
    original = str(base / "work" / "evil.sh")
    if origin:
        (base / "q" / ".herkunft" / "f1_evil.sh").write_text(f"{mode}\n{original}\n")
    return original


@pytest.mark.parametrize(("kind", "expected"), [
    ("quarantined", "@@state=quarantined\n@@mode=640\n"),
    ("no_origin", "@@state=quarantined\n"),
    ("checking", "@@state=checking\n"),
    ("absent", "@@state=absent\n"),
    ("no_quarantine_yet", "@@state=absent\n"),
    ("link", "@@state=absent\n"),
    ("origin_link", "@@state=quarantined\n"),
    ("origin_fifo", "@@state=quarantined\n"),
])
def test_quarantine_state_behaves_like_the_old_command_and_changes_nothing(tmp_path, monkeypatch, kind, expected):
    av = _av()
    results = []
    for side in ("alt", "neu"):
        base = tmp_path / side
        if kind != "no_quarantine_yet":
            _quarantined(base, monkeypatch, origin=kind != "no_origin")
        if kind in ("checking", "absent", "link"):
            (base / "q" / "f1_evil.sh").unlink()
        if kind == "checking":
            (base / "q" / ".pruefen").mkdir(mode=0o700)
            (base / "q" / ".pruefen" / "f1_evil.sh").write_text("inhalt")
        if kind == "link":
            (base / "fremd").write_text("geheim")
            (base / "q" / "f1_evil.sh").symlink_to(base / "fremd")
        if kind in ("origin_link", "origin_fifo"):
            (base / "q" / ".herkunft" / "f1_evil.sh").unlink()
            if kind == "origin_link":
                (base / "fremd").write_text("644\nfremde Datei\n")
                (base / "q" / ".herkunft" / "f1_evil.sh").symlink_to(base / "fremd")
            else:
                os.mkfifo(base / "q" / ".herkunft" / "f1_evil.sh")
        before = _state(base) if base.exists() else {}
        monkeypatch.setattr(av, "QUARANTINE_DIR", str(base / "q"))
        if side == "alt":
            result = _run_old(av.quarantine_state_command("f1_evil.sh"))
        else:
            base.mkdir(exist_ok=True)
            result = _run_new(_helper(base), *av.quarantine_state_args("f1_evil.sh"))
        assert {k: v for k, v in _state(base).items() if k != "shield-root"} == before
        results.append(result)
    assert results[0] == results[1] == (0, expected)


@pytest.mark.parametrize("name", ["", ".herkunft", "../x", "a/b", "f1 evil"])
def test_quarantine_state_refuses_odd_names(tmp_path, name):
    av = _av()
    with pytest.raises(ValueError):
        av.quarantine_state_args(name)
    code, _out = _run_new(_helper(tmp_path), "quarantine-state", name)
    assert code != 0


@pytest.mark.parametrize("kind", ["roundtrip", "occupied", "folder_missing", "folder_link", "not_in_quarantine"])
def test_restore_behaves_like_the_old_command(tmp_path, monkeypatch, kind):
    av = _av()
    results = []
    for side in ("alt", "neu"):
        base = tmp_path / side
        original = _quarantined(base, monkeypatch)
        if kind == "occupied":
            Path(original).write_text("neu angelegt")
        elif kind == "folder_missing":
            shutil.rmtree(base / "work")
        elif kind == "folder_link":
            shutil.rmtree(base / "work")
            (base / "anderswo").mkdir()
            (base / "work").symlink_to(base / "anderswo", target_is_directory=True)
        elif kind == "not_in_quarantine":
            (base / "q" / "f1_evil.sh").unlink()
        qpath = str(base / "q" / "f1_evil.sh")
        monkeypatch.setattr(av, "QUARANTINE_DIR", str(base / "q"))
        if side == "alt":
            result = _run_old(av.restore_command(qpath, original, "640"))
        else:
            result = _run_new(_helper(base), *av.restore_args(qpath, original, "640"))
        results.append((result, _state(base)))
    assert results[1] == results[0]
    if kind == "roundtrip":
        (code, out), state = results[0]
        assert (code, out) == (0, "ok\n") and state["work/evil.sh"] == "file 640 1 b'inhalt'"


def test_delete_behaves_like_the_old_command(tmp_path, monkeypatch):
    av = _av()
    results = []
    for side in ("alt", "neu"):
        base = tmp_path / side
        _quarantined(base, monkeypatch)
        monkeypatch.setattr(av, "QUARANTINE_DIR", str(base / "q"))
        qpath = str(base / "q" / "f1_evil.sh")
        result = _run_old(av.delete_command(qpath)) if side == "alt" else _run_new(_helper(base), *av.delete_args(qpath))
        results.append((result, _state(base)))
    assert results[1] == results[0] and results[0][0] == (0, "ok\n")


def _stubs(base: Path, scripts: dict[str, str]) -> Path:
    bin_dir = base / "bin"
    bin_dir.mkdir(parents=True)
    for name, body in scripts.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + body)
        (bin_dir / name).chmod(0o755)
    return bin_dir


@pytest.mark.parametrize(("ip", "unban"), [("203.0.113.9", False), ("2001:db8::1", True)])
def test_ban_behaves_like_the_old_command(tmp_path, ip, unban):
    from nodvard_deck_ext_shield import intrusion as ix

    bin_dir = _stubs(tmp_path, {"fail2ban-client": 'echo "fail2ban-client $*"\n'})
    old = _run_old(ix.ban_command(ip, "sshd", unban=unban), env_path=f"{bin_dir}:/usr/bin:/bin")
    new = _run_new(_helper(tmp_path, path=f"{bin_dir}:/usr/bin:/bin"), *ix.ban_args(ip, "sshd", unban=unban))
    assert new == old == (0, f"fail2ban-client set sshd {'unbanip' if unban else 'banip'} {ip}\n")


@pytest.mark.parametrize(
    ("package", "tools"),
    [
        ("clamav", {"apt-get": 'echo "apt-get $* $DEBIAN_FRONTEND"\n'}),
        ("lynis", {"dnf": 'echo "dnf $*"\n'}),
        ("lynis", {}),
        ("signatures", {"systemctl": 'echo "systemctl $*"\n', "freshclam": "echo freshclam; exit 3\n"}),
        ("unattended", {}),
    ],
    ids=["clamav-apt", "lynis-dnf", "ohne-paketmanager", "signaturen", "unattended-ohne-apt"],
)
def test_install_behaves_like_the_old_command(tmp_path, package, tools):
    """Nur Attrappen im PATH, damit nichts wirklich installiert oder unter /etc geschrieben wird."""
    av = _av()
    bin_dir = _stubs(tmp_path, tools)
    old = _run_old(av.INSTALL_COMMANDS[package], env_path=str(bin_dir))
    new = _run_new(_helper(tmp_path, path=str(bin_dir)), "install", package)
    assert new == old


@pytest.mark.parametrize(
    "args",
    [
        ["quarantine", "relativ/x", "f1_x"], ["quarantine", "/tmp/x", "a/b"], ["quarantine", "/tmp/x", ".."],
        ["quarantine", "/tmp/", "f1_x"], ["quarantine", "/tmp/x"], ["quarantine", "/tmp/x", ".herkunft"],
        ["quarantine", "/tmp/x", ".pruefen"], ["restore", ".pruefen"], ["delete", ".pruefen"],
        ["restore", "f1_x", "/tmp/x", "644"], ["restore", "f1_x", "/etc/cron.d/x"], ["restore", "../x"],
        ["restore", ".herkunft"], ["restore"], ["delete", "../../etc/passwd"], ["delete", ".herkunft"],
        ["delete", ""], ["ban", "1.2.3.4;id", "sshd"], ["ban", "1.2.3.4", "-x"], ["ban", "1.2.3.4"], ["unban", "", "sshd"],
        ["install", "evil"], ["install"],
    ],
)
def test_strange_arguments_are_refused(tmp_path, args):
    done = subprocess.run([SH, str(_helper(tmp_path)), *args], capture_output=True, text=True, timeout=30, check=False)
    assert (done.returncode, done.stdout) == (64, ""), (args, done.stderr)


def test_helper_text_puts_back_the_sentences_of_the_module():
    av = _av()
    assert av.helper_text("@@msg=symlink\n") == av.SYMLINK_REFUSED + "\n"
    assert av.helper_text("x\n@@msg=restore_stuck /srv/a b\n") == f"x\n{av.RESTORE_STUCK} /srv/a b\n"
    assert av.helper_text("@@msg=unbekannt\n@@mode=640\n") == "@@msg=unbekannt\n@@mode=640\n"
    assert set(av.HELPER_MESSAGES) >= {
        m for m in re.findall(r"@@msg=([a-z_]+)", SCRIPT.read_text(encoding="ascii"))
    }, "jede Kennung im Skript hat ihren Satz"


def test_only_paths_directly_in_the_quarantine_go_to_the_helper():
    av = _av()
    q = av.QUARANTINE_DIR
    assert av.restore_args(f"{q}/f1_x", "/tmp/x", None) == ["restore", "f1_x"]
    assert av.restore_args(f"{q}/f1_x", "/tmp/x", "4755") == ["restore", "f1_x"]
    assert av.delete_args(f"{q}/f1_x") == ["delete", "f1_x"]
    for bad in ("/anderswo/f1_x", f"{q}/a b", f"{q}/.herkunft", f"{q}/.f1_x", f"{q}//f1_x", f"{q}/./f1_x"):
        with pytest.raises(ValueError):
            av.restore_args(bad, "/tmp/x", "644")
        with pytest.raises(ValueError):
            av.delete_args(bad)


# --- Was das Skript dem Dashboard nicht glaubt ----------------------------------------------------------------------


@pytest.mark.parametrize(("content", "clamscan", "expected"), [
    ("nur text", "ok", (6, "QUARANTINE_NOT_FOUND")),
    ("x5o eicar", "fehler", (7, "QUARANTINE_UNCHECKED")),
])
def test_quarantine_moves_only_what_clamav_finds_itself(tmp_path, content, clamscan, expected):
    """Ein Aufruf mit einer sauberen Datei (etwa /etc/shadow) verschiebt nichts; ohne Prüfung auch nicht."""
    av = _av()
    victim = tmp_path / "work" / "sauber.conf"
    victim.parent.mkdir()
    victim.write_text(content)
    victim.chmod(0o640)
    code, out = _run_new(_helper(tmp_path, path=_scanner(tmp_path, clamscan=clamscan)), "quarantine", str(victim), "f1_x")
    assert (code, out) == (expected[0], getattr(av, expected[1]) + "\n")
    assert victim.read_text() == content and (victim.stat().st_mode & 0o777) == 0o640
    assert sorted(str(p.relative_to(tmp_path / "q")) for p in (tmp_path / "q").rglob("*")) == [".herkunft", ".pruefen"]


@pytest.mark.parametrize(("clamd_finds", "clamscan_runs"), [("ja", False), ("nein", True)])
def test_quarantine_asks_clamd_first_and_clamscan_without_size_limit(tmp_path, clamd_finds, clamscan_runs):
    """clamd zuerst; findet er nichts (etwa wegen seiner Größengrenze), prüft clamscan bis 4000 MB. Geprüft wird erst am
    Ort und nach dem Verschieben im Prüfordner noch einmal, beide Male gleich."""
    victim = tmp_path / "work" / "evil.sh"
    victim.parent.mkdir()
    victim.write_text("x5o eicar")
    helper = _helper(tmp_path, path=_scanner(tmp_path, clamd="an", clamd_finds=clamd_finds))
    assert _run_new(helper, "quarantine", str(victim), "f1_evil.sh")[0] == 0
    checked = tmp_path / "q" / ".pruefen" / "f1_evil.sh"

    def check(path: str) -> list[str]:
        clamscan = [f"clamscan --no-summary --max-filesize=4000M --max-scansize=4000M {path}"] if clamscan_runs else []
        return ["clamdscan --ping 1", f"clamdscan --fdpass --no-summary {path}", *clamscan]

    assert _calls(tmp_path) == [*check("./evil.sh"), *check(str(checked))]


def test_restore_goes_back_only_to_the_place_recorded_at_quarantine(tmp_path):
    """Früher nahm `restore` Ort und Rechte vom Aufrufer: eine Datei aus der Quarantäne landete so z. B. mit 0644 in
    /etc/cron.d. Jetzt nur noch der Name; Ort und Rechte kommen aus der Ablage, die `quarantine` angelegt hat."""
    victim = tmp_path / "work" / "evil.sh"
    victim.parent.mkdir()
    victim.write_text("x5o eicar")
    victim.chmod(0o750)
    elsewhere = tmp_path / "cron.d"
    elsewhere.mkdir()
    helper = _helper(tmp_path, path=_scanner(tmp_path))
    assert _run_new(helper, "quarantine", str(victim), "f1_evil.sh") == (0, "@@mode=750\n")
    refused = subprocess.run([SH, str(helper), "restore", "f1_evil.sh", str(elsewhere / "x"), "644"],
                             capture_output=True, text=True, timeout=30, check=False)
    assert (refused.returncode, refused.stdout) == (64, "")
    assert list(elsewhere.iterdir()) == [] and (tmp_path / "q" / "f1_evil.sh").exists()
    assert _run_new(helper, "restore", "f1_evil.sh") == (0, "ok\n")
    assert victim.read_text() == "x5o eicar" and (victim.stat().st_mode & 0o777) == 0o750
    assert sorted(p.name for p in (tmp_path / "q").rglob("*")) == [".herkunft", ".pruefen"]


@pytest.mark.parametrize("record", [None, "abc\n/tmp/x\n", "644\n"])
def test_restore_without_a_usable_record_leaves_the_file_in_quarantine(tmp_path, monkeypatch, record):
    av = _av()
    original = _quarantined(tmp_path, monkeypatch, origin=False)
    if record is not None:
        (tmp_path / "q" / ".herkunft" / "f1_evil.sh").write_text(record)
    code, out = _run_new(_helper(tmp_path), "restore", "f1_evil.sh")
    if record == "644\n":
        assert code == 64  # leerer Pfad: kein Ort, das Skript bricht ab
    else:
        assert (code, out) == (3, av.RESTORE_NO_ORIGIN + "\n")
    assert not Path(original).exists() and (tmp_path / "q" / "f1_evil.sh").exists()


@pytest.mark.parametrize("name", ["zeilen\numbruch.sh", "ende mit umbruch\n", "-n evil.sh"])
def test_the_record_keeps_odd_file_names_exactly(tmp_path, name):
    folder = tmp_path / "ordner mit\numbruch"
    folder.mkdir()
    victim = folder / name
    victim.write_text("x5o eicar")
    victim.chmod(0o750)
    av = _av()
    qname = av.quarantine_name("f1", str(victim))
    helper = _helper(tmp_path, path=_scanner(tmp_path))
    assert _run_new(helper, "quarantine", str(victim), qname) == (0, "@@mode=750\n")
    assert not os.path.lexists(victim)
    assert _run_new(helper, "restore", qname) == (0, "ok\n")
    assert victim.read_text() == "x5o eicar" and (victim.stat().st_mode & 0o777) == 0o750
    assert [p.name for p in folder.iterdir()] == [name]


# --- Prüfordner und Besitz der Quarantäne ---------------------------------------------------------------------------


def _scanner_doing(base: Path, during: str) -> str:
    """Attrappe für clamscan (clamd aus), die während der Prüfung `during` ausführt: wie jemand, dem der Ordner der
    Datei gehört und der dort in diesem Moment etwas austauscht. Fund, wenn die geprüfte Datei "eicar" enthält."""
    tools = base.parent / f"{base.name}-werkzeug"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "clamscan").write_text(
        '#!/bin/sh\nfor a; do f=$a; done\nif grep -q eicar "$f" 2>/dev/null; then r=1; else r=0; fi\n'
        f"{during}\nexit $r\n"
    )
    (tools / "clamdscan").write_text("#!/bin/sh\nexit 2\n")
    for name in ("clamscan", "clamdscan"):
        (tools / name).chmod(0o755)
    return f"{tools}:/usr/bin:/bin"


def test_a_file_swapped_in_during_the_check_stays_where_it_is(tmp_path):
    """Früher prüfte ClamAV die Datei nur an ihrem Ort, verschoben wurde danach: Wem der Ordner gehört, der konnte sie in
    der Zwischenzeit (ohne clamd gut eine Minute) gegen eine andere tauschen, und die wanderte als root in die
    Quarantäne. Jetzt prüft ClamAV einen Fund im Prüfordner, in dem nur root schreibt, noch einmal: Die untergeschobene
    Datei fällt dort durch und geht zurück an ihren Ort."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "evil.sh").write_text("x5o eicar")
    (work / "evil.sh").chmod(0o750)
    (work / "anna.txt").write_text("annas notizen")
    helper = _helper(tmp_path, path=_scanner_doing(tmp_path, f"mv -f '{work}/anna.txt' '{work}/evil.sh' 2>/dev/null"))
    av = _av()
    assert _run_new(helper, "quarantine", str(work / "evil.sh"), "f1_evil.sh") == (6, av.QUARANTINE_NOT_FOUND + "\n")
    assert not (tmp_path / "q" / "f1_evil.sh").exists()
    assert (work / "evil.sh").read_text() == "annas notizen"
    assert list((tmp_path / "q" / ".pruefen").iterdir()) == []


def test_a_clean_file_goes_back_unchanged_after_the_check(tmp_path):
    av = _av()
    work = tmp_path / "work"
    work.mkdir()
    victim = work / "sauber.conf"
    victim.write_text("nur text")
    victim.chmod(0o640)
    before = victim.stat()
    helper = _helper(tmp_path, path=_scanner(tmp_path))
    assert _run_new(helper, "quarantine", str(victim), "f1_x") == (6, av.QUARANTINE_NOT_FOUND + "\n")
    after = victim.stat()
    assert victim.read_text() == "nur text"
    assert (after.st_ino, after.st_mode, after.st_mtime_ns) == (before.st_ino, before.st_mode, before.st_mtime_ns)
    assert [p.name for p in work.iterdir()] == ["sauber.conf"]  # kein Zwischenordner bleibt liegen
    assert list((tmp_path / "q" / ".pruefen").iterdir()) == []


@pytest.mark.parametrize("taken", ["file", "link"])
def test_a_clean_file_whose_place_was_taken_meanwhile_stays_in_the_check_folder(tmp_path, taken):
    """Am Ort ein Fund, im Prüfordner sauber (dazwischen getauscht): Die Datei geht zurück. Ist ihr Platz inzwischen
    belegt, nichts überschreiben und keiner Verknüpfung folgen: Sie bleibt im Prüfordner, die Meldung sagt wo."""
    av = _av()
    work = tmp_path / "work"
    work.mkdir()
    victim = work / "sauber.conf"
    victim.write_text("x5o eicar")
    (tmp_path / "ziel").write_text("nicht anfassen")
    marker = tmp_path / "erste-pruefung"
    take = f"echo fremd > '{victim}'" if taken == "file" else f"ln -s '{tmp_path}/ziel' '{victim}'"
    swap = f"touch '{marker}'; printf 'nur text' > '{work}/neu' && mv -f '{work}/neu' '{victim}'"
    during = f"if [ -e '{marker}' ]; then {take}; else {swap}; fi"
    helper = _helper(tmp_path, path=_scanner_doing(tmp_path, during))
    checked = tmp_path / "q" / ".pruefen" / "f1_x"
    assert _run_new(helper, "quarantine", str(victim), "f1_x") == (4, f"{av.QUARANTINE_KEPT} {checked}\n")
    assert checked.read_text() == "nur text" and (tmp_path / "ziel").read_text() == "nicht anfassen"
    assert victim.is_symlink() if taken == "link" else victim.read_text() == "fremd\n"
    assert [p.name for p in work.iterdir()] == ["sauber.conf"]


def test_a_clean_file_stays_in_its_place_while_it_is_checked(tmp_path):
    """Früher wanderte jede Datei vor der Prüfung in den Prüfordner, auch eine saubere Systemdatei, und bei einem Abbruch
    blieb sie dort liegen. Jetzt prüft ClamAV zuerst am Ort; verschoben wird nur ein Fund."""
    av = _av()
    work = tmp_path / "work"
    work.mkdir()
    victim = work / "sauber.conf"
    victim.write_text("nur text")
    seen = tmp_path / "gesehen.log"
    helper = _helper(tmp_path, path=_scanner_doing(tmp_path, f"if [ -f '{victim}' ]; then echo da; else echo weg; fi >> '{seen}'"))
    assert _run_new(helper, "quarantine", str(victim), "f1_x") == (6, av.QUARANTINE_NOT_FOUND + "\n")
    assert seen.read_text() == "da\n", "einmal geprüft, die ganze Zeit am Ort"
    assert victim.read_text() == "nur text"
    assert list((tmp_path / "q" / ".pruefen").iterdir()) == []


def test_an_abort_during_the_second_check_puts_the_file_back(tmp_path):
    """Bricht der Aufruf ab, während die Datei im Prüfordner liegt (etwa die Zeitgrenze), legt die Falle sie zurück."""
    work = tmp_path / "work"
    work.mkdir()
    victim = work / "evil.sh"
    victim.write_text("x5o eicar")
    victim.chmod(0o750)
    marker = tmp_path / "erste-pruefung"
    during = f"if [ -e '{marker}' ]; then kill -TERM $PPID; else touch '{marker}'; fi"
    helper = _helper(tmp_path, path=_scanner_doing(tmp_path, during))
    assert _run_new(helper, "quarantine", str(victim), "f1_evil.sh")[0] == 143
    assert victim.read_text() == "x5o eicar" and stat.S_IMODE(victim.stat().st_mode) == 0o750
    assert not (tmp_path / "q" / "f1_evil.sh").exists()
    assert list((tmp_path / "q" / ".pruefen").iterdir()) == []
    assert [p.name for p in work.iterdir()] == ["evil.sh"]


def _scanner_script(base: Path, body: str) -> str:
    """Attrappe für clamscan (clamd aus) mit eigenem Ablauf `body`; `$f` ist die geprüfte Datei, `$n` zählt die Aufrufe
    mit (1, 2, …) und `$found` sagt, ob sie "eicar" enthält. Gibt den PATH für das Skript zurück."""
    tools = base.parent / f"{base.name}-werkzeug"
    tools.mkdir(parents=True, exist_ok=True)
    count = tools / "anzahl"
    (tools / "clamscan").write_text(
        '#!/bin/sh\nfor a; do f=$a; done\n'
        f'n=$(($(cat "{count}" 2>/dev/null || echo 0) + 1)); echo "$n" > "{count}"\n'
        'if grep -q eicar "$f" 2>/dev/null; then found=1; else found=0; fi\n'
        f"{body}\n"
        '[ "$found" = 1 ] && exit 1\nexit 0\n'
    )
    (tools / "clamdscan").write_text("#!/bin/sh\nexit 2\n")
    for name in ("clamscan", "clamdscan"):
        (tools / name).chmod(0o755)
    return f"{tools}:/usr/bin:/bin"


def _wait_for(path: Path, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path.name} kam nicht"
        time.sleep(0.02)


def test_two_calls_with_the_same_name_keep_the_record_of_the_one_that_moved_the_file(tmp_path):
    """Version 7 verwarf einen Fehler von `mv` (`|| true`): Zwei gleichzeitige Aufrufe mit demselben Namen prüften beide
    am Ort, der schnellere verschob die Datei, der andere machte mit dessen Datei im Prüfordner weiter. Wer dann als
    Zweiter fertig wurde, scheiterte am letzten `mv` und löschte dabei die Herkunft; „Wiederherstellen“ meldete danach
    `restore_no_origin`. Jetzt hört der langsamere Aufruf auf, sobald sein `mv` scheitert, und fasst nichts an."""
    av = _av()
    work = tmp_path / "work"
    work.mkdir()
    victim = work / "evil.sh"
    victim.write_text("x5o eicar")
    victim.chmod(0o750)
    sync = tmp_path.parent / f"{tmp_path.name}-takt"
    sync.mkdir()
    helper = tmp_path / "shield-root"
    second = f"LC_ALL=C sh '{helper}' quarantine '{victim}' f1_evil.sh > '{sync}/b.out' 2>&1; echo $? > '{sync}/b.rc'"
    # 1: Prüfung am Ort von Aufruf A, startet Aufruf B und wartet, bis B die Datei verschoben hat.
    # 2: Prüfung am Ort von B. 3: Prüfung von B im Prüfordner, wartet auf das Zeichen "weiter".
    body = (
        f'if [ "$n" = 1 ]; then ( {second} ) </dev/null >/dev/null 2>&1 &\n'
        f"  while [ ! -e '{sync}/b-verschoben' ]; do sleep 0.02; done\n"
        f"elif [ \"$n\" = 3 ]; then touch '{sync}/b-verschoben'; while [ ! -e '{sync}/weiter' ]; do sleep 0.02; done; fi"
    )
    _helper(tmp_path, path=_scanner_script(tmp_path, body))
    first = _run_new(helper, "quarantine", str(victim), "f1_evil.sh")
    (sync / "weiter").touch()
    _wait_for(sync / "b.rc")
    assert first == (3, av.HELPER_MESSAGES["gone"] + "\n")
    assert (sync / "b.rc").read_text().strip() == "0" and "@@mode=750" in (sync / "b.out").read_text()
    assert not victim.exists()
    origin = tmp_path / "q" / ".herkunft" / "f1_evil.sh"
    assert origin.read_text() == f"750\n{victim}\n"
    assert (tmp_path / "q" / "f1_evil.sh").exists()
    assert list((tmp_path / "q" / ".pruefen").iterdir()) == []
    assert _run_new(helper, "restore", "f1_evil.sh")[0] == 0
    assert victim.read_text() == "x5o eicar" and stat.S_IMODE(victim.stat().st_mode) == 0o750


def test_a_call_whose_move_was_skipped_leaves_the_other_file_in_the_check_folder_alone(tmp_path):
    """`mv -n` meldet ein Überspringen nicht überall als Fehler (je nach coreutils-Version). Liegt dieselbe Datei danach
    noch am Ort, gehört, was im Prüfordner liegt, einem anderen Aufruf: aufhören, nichts verschieben, nichts löschen."""
    av = _av()
    work = tmp_path / "work"
    work.mkdir()
    victim = work / "evil.sh"
    victim.write_text("x5o eicar")
    checks = tmp_path / "q" / ".pruefen"
    # Der andere Aufruf legt seine Datei genau zwischen der Prüfung am Ort und dem Verschieben in den Prüfordner.
    body = f"if [ \"$n\" = 1 ]; then echo 'von nebenan' > '{checks}/f1_evil.sh'; fi"
    tools = tmp_path.parent / f"{tmp_path.name}-werkzeug"
    tools.mkdir(parents=True)
    (tools / "mv").write_text('#!/bin/sh\nfor a; do last=$a; done\n[ -e "$last" ] && exit 0\nexec /bin/mv "$@"\n')
    (tools / "mv").chmod(0o755)
    helper = _helper(tmp_path, path=_scanner_script(tmp_path, body))
    assert _run_new(helper, "quarantine", str(victim), "f1_evil.sh") == (5, av.QUARANTINE_CHECK_BUSY + "\n")
    assert victim.read_text() == "x5o eicar"
    assert (checks / "f1_evil.sh").read_text() == "von nebenan\n"
    assert not (tmp_path / "q" / "f1_evil.sh").exists() and not (tmp_path / "q" / ".herkunft" / "f1_evil.sh").exists()


def test_a_second_signal_while_putting_back_does_not_leave_the_file_halfway(tmp_path):
    """Die Falle legt die Datei in zwei Schritten zurück (Zwischenordner, dann umbenennen). Kam während dessen ein
    zweites Signal, lief die Falle ein zweites Mal los, fand den Platz im Zwischenordner belegt und hörte auf: Die Datei
    blieb im Zwischenordner liegen. Jetzt nimmt die Falle während des Zurücklegens kein Signal mehr an."""
    work = tmp_path / "work"
    work.mkdir()
    victim = work / "evil.sh"
    victim.write_text("x5o eicar")
    victim.chmod(0o750)
    tools = tmp_path.parent / f"{tmp_path.name}-werkzeug"
    tools.mkdir(parents=True)
    armed, sent = tools / "scharf", tools / "gesendet"
    # Das erste Signal kommt in der zweiten Prüfung, das zweite beim ersten mv der Falle (Prüfordner → Zwischenordner).
    (tools / "mv").write_text(
        f"#!/bin/sh\nif [ -e '{armed}' ] && [ ! -e '{sent}' ]; then touch '{sent}'; kill -TERM $PPID; fi\n"
        'exec /bin/mv "$@"\n'
    )
    (tools / "mv").chmod(0o755)
    body = f"if [ \"$n\" = 2 ]; then touch '{armed}'; kill -TERM $PPID; fi"
    helper = _helper(tmp_path, path=_scanner_script(tmp_path, body))
    assert _run_new(helper, "quarantine", str(victim), "f1_evil.sh")[0] == 143
    assert sent.exists(), "das zweite Signal kam an"
    assert victim.read_text() == "x5o eicar" and stat.S_IMODE(victim.stat().st_mode) == 0o750
    assert [p.name for p in work.iterdir()] == ["evil.sh"], "kein Zwischenordner bleibt liegen"
    assert list((tmp_path / "q" / ".pruefen").iterdir()) == []
    assert not (tmp_path / "q" / "f1_evil.sh").exists()


def test_a_signal_right_after_the_move_puts_the_file_back(tmp_path):
    """Die Falle stand früher erst nach dem `mv` in den Prüfordner: Ein Signal genau dazwischen ließ die Datei dort liegen.
    Jetzt steht sie davor."""
    work = tmp_path / "work"
    work.mkdir()
    victim = work / "evil.sh"
    victim.write_text("x5o eicar")
    victim.chmod(0o640)
    tools = tmp_path.parent / f"{tmp_path.name}-werkzeug"
    tools.mkdir(parents=True)
    done = tools / "gesendet"
    # Signal am Ende des ersten mv (Ort → Prüfordner), bevor die Shell weitermacht.
    (tools / "mv").write_text(
        f"#!/bin/sh\n/bin/mv \"$@\" || exit $?\nif [ ! -e '{done}' ]; then touch '{done}'; kill -TERM $PPID; fi\n"
    )
    (tools / "mv").chmod(0o755)
    helper = _helper(tmp_path, path=_scanner_script(tmp_path, ""))
    assert _run_new(helper, "quarantine", str(victim), "f1_evil.sh")[0] == 143
    assert victim.read_text() == "x5o eicar" and stat.S_IMODE(victim.stat().st_mode) == 0o640
    assert [p.name for p in work.iterdir()] == ["evil.sh"]
    assert list((tmp_path / "q" / ".pruefen").iterdir()) == []


def test_a_leftover_in_the_check_folder_is_never_overwritten(tmp_path):
    av = _av()
    victim = tmp_path / "work" / "evil.sh"
    victim.parent.mkdir()
    victim.write_text("x5o eicar")
    checks = tmp_path / "q" / ".pruefen"
    checks.mkdir(parents=True)
    for folder in (tmp_path / "q", checks):
        folder.chmod(0o700)
    (checks / "f1_evil.sh").write_text("von vorhin")
    helper = _helper(tmp_path, path=_scanner(tmp_path))
    assert _run_new(helper, "quarantine", str(victim), "f1_evil.sh") == (5, av.QUARANTINE_CHECK_BUSY + "\n")
    assert victim.read_text() == "x5o eicar" and (checks / "f1_evil.sh").read_text() == "von vorhin"
    assert not (tmp_path / "q" / "f1_evil.sh").exists()


def _unsafe_quarantine(base: Path, kind: str) -> Path:
    """Ordner der Quarantäne, die weder Skript noch Befehl annehmen dürfen; gibt einen Ordner zurück, der leer bleibt."""
    q = base / "q"
    elsewhere = base / "anderswo"
    elsewhere.mkdir(parents=True)
    if kind == "q_link":
        q.symlink_to(elsewhere, target_is_directory=True)
        return elsewhere
    q.mkdir()
    q.chmod(0o755 if kind == "q_open" else 0o700)
    if kind == "q_foreign":
        os.chown(q, 65534, 65534)
    elif kind == "herkunft_link":
        (q / ".herkunft").symlink_to(elsewhere, target_is_directory=True)
    elif kind == "pruefen_file":
        (q / ".herkunft").mkdir(mode=0o700)
        (q / ".pruefen").write_text("")
    return elsewhere


_NOT_ROOT = pytest.mark.skipif(os.geteuid() != 0 if hasattr(os, "geteuid") else True, reason="chown braucht root")


@pytest.mark.parametrize(
    "kind", ["q_link", "q_open", "herkunft_link", "pruefen_file", pytest.param("q_foreign", marks=_NOT_ROOT)],
)
def test_quarantine_takes_only_folders_of_root_alone(tmp_path, monkeypatch, kind):
    """Wie LOCKDIR beim Scan: Verknüpfung, fremder Besitzer oder andere Rechte als 700 → nichts verschoben, nichts
    angelegt (Skript und Befehl des Moduls gleich)."""
    av = _av()
    results = []
    for side in ("alt", "neu"):
        base = tmp_path / side
        victim = base / "work" / "evil.sh"
        victim.parent.mkdir(parents=True)
        victim.write_text("x5o eicar")
        elsewhere = _unsafe_quarantine(base, kind)
        if side == "alt":
            monkeypatch.setattr(av, "QUARANTINE_DIR", str(base / "q"))
            result = _run_old(av.quarantine_command(str(victim), "f1_evil.sh"))
        else:
            result = _run_new(_helper(base, path=_scanner(base)), "quarantine", str(victim), "f1_evil.sh")
        assert victim.read_text() == "x5o eicar" and list(elsewhere.iterdir()) == []
        results.append((result, _state(base)))
    assert results[1] == results[0]
    assert results[0][0] == (5, av.QUARANTINE_DIR_UNSAFE + "\n")


@pytest.mark.parametrize("kind", ["q_open", "herkunft_open", "herkunft_link", pytest.param("herkunft_foreign", marks=_NOT_ROOT)])
def test_restore_trusts_the_record_only_in_folders_of_root_alone(tmp_path, monkeypatch, kind):
    """Ort und Rechte kommen aus der Ablage; die zählt nur, wenn außer root niemand sie anlegen oder ändern kann."""
    av = _av()
    original = _quarantined(tmp_path, monkeypatch)
    q = tmp_path / "q"
    cron = tmp_path / "cron.d"
    cron.mkdir()
    if kind == "q_open":
        q.chmod(0o755)
    elif kind == "herkunft_open":
        (q / ".herkunft").chmod(0o755)
    elif kind == "herkunft_foreign":
        os.chown(q / ".herkunft", 65534, 65534)
    else:
        forged = tmp_path / "gefaelscht"
        forged.mkdir()
        (forged / "f1_evil.sh").write_text(f"644\n{cron}/x\n")
        shutil.rmtree(q / ".herkunft")
        (q / ".herkunft").symlink_to(forged, target_is_directory=True)
    assert _run_new(_helper(tmp_path), "restore", "f1_evil.sh") == (5, av.QUARANTINE_DIR_UNSAFE + "\n")
    assert not Path(original).exists() and list(cron.iterdir()) == [] and (q / "f1_evil.sh").exists()
