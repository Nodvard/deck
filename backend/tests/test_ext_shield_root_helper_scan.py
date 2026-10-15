"""Nodvard Shield: Virenscan und Wächter über das Hilfsskript `shield-root` (ab Version 6).

Gleichlauf: Der bisherige Befehl (`antivirus.guard_command` mit `build_scan_command` bzw. `build_watch_command`) und
das Skript laufen gegen dieselben Attrappen über denselben Ordnerbaum; nur Sperre und Hilfsdateien liegen je Seite
woanders. Die Ausgabe muss gleich sein, auch die Argumente, mit denen `clamscan` aufgerufen wird (die Attrappe gibt sie
aus)."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from shield_helper_kit import EXT_DIR, SCRIPT, SH, helper_copy, run_sh, stubs

MARK = "@@scan-rc-0123456789abcdef="
HEX = "0123456789abcdef"

needs_sh = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None or shutil.which("find") is None,
    reason="braucht eine echte POSIX-Shell und find",
)
# Ohne root nimmt der alte Befehl die Sperre im Heimatordner (im Test ohne HOME: gar keine), das Skript läuft immer als
# root. Nur die Tests zur Sperre selbst brauchen deshalb root.
needs_root = pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="braucht root")


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


# Gibt jedes Argument aus (die Dateiliste ohne ihren zufälligen Namen, dafür ihren Inhalt). Ein Name mit "evil" ist ein
# Fund (Code 1).
CLAMSCAN = r'''rc=0
for a in "$@"; do
    case $a in
        --file-list=*) echo "opt:--file-list"; while IFS= read -r f; do echo "list:$f"; case $f in *evil*) rc=1;; esac; done < "${a#--file-list=}" ;;
        -*) echo "opt:$a" ;;
        *) echo "file:$a"; case $a in *evil*) rc=1;; esac ;;
    esac
done
echo "Scanned files: 2"
exit $rc
'''


def _tree(tmp_path: Path) -> Path:
    """Ein Baum mit frisch geänderten Dateien: sauber, Fund, ausgenommener Ordner (auch mit Sonderzeichen), ungewöhnlicher
    Name (Zeilenumbruch), ein Ordner als Verknüpfung."""
    tree = tmp_path / "baum"
    (tree / "a" / "sub").mkdir(parents=True)
    (tree / "a" / "sauber.txt").write_text("x")
    (tree / "a" / "sub" / "evil.sh").write_text("x")
    (tree / "a" / "aus").mkdir()
    (tree / "a" / "aus" / "evil-ausgenommen.sh").write_text("x")
    (tree / "a" / "we[i]rd*").mkdir()
    (tree / "a" / "we[i]rd*" / "evil-auch-aus.sh").write_text("x")
    (tree / "a" / "zeile\numbruch-evil.sh").write_text("x")
    (tree / "b").mkdir()
    (tree / "b" / "evil2.sh").write_text("x")
    (tree / "link").symlink_to(tree / "b", target_is_directory=True)
    return tree


def _scan_both(tmp_path: Path, paths: list[str], *, exclude: list[str], tools: dict[str, str] | None = None,
               limit_s: int = 600, wait_s: int = 30, prepare=None) -> list[tuple[int, str]]:
    av = _av()
    outs = []
    for name in ("alt", "neu"):
        base = tmp_path / name
        bin_dir = stubs(base, {"clamscan": CLAMSCAN, **(tools or {})})
        path = f"{bin_dir}:/usr/bin:/bin"
        if prepare is not None:
            prepare(base)
        if name == "alt":
            command = av.guard_command(av.build_scan_command(paths, max_filesize_mb=50, exclude=exclude, mark=MARK),
                                       mark=MARK, limit_s=limit_s, wait_s=wait_s, root_dir=str(base / "lock"))
            outs.append(run_sh([SH, "-c", command], path))
        else:
            args = av.scan_helper_args(paths, max_filesize_mb=50, exclude=exclude, mark=MARK, limit_s=limit_s, wait_s=wait_s)
            outs.append(run_sh([SH, str(helper_copy(base, path=path, sdmark=base / "sd")), *args], "/nicht-da"))
    return outs


EXCLUDES = ["/srv/a+b", "/data/x[1]/", "relativ", "/home/../etc", "/" + "y" * 600, "/tmp/**", "/a//b/./c"]


@needs_sh
@pytest.mark.parametrize("kind", ["quick", "deep", "custom"])
def test_a_scan_behaves_like_the_old_command(tmp_path, kind):
    av = _av()
    tree = _tree(tmp_path)
    paths = {"quick": [str(tree / "a"), str(tree / "link"), str(tree / "fehlt")], "deep": ["/"],
             "custom": [str(tree / "a" / "sub")]}[kind]
    old, new = _scan_both(tmp_path, paths, exclude=EXCLUDES if kind == "custom" else [])
    assert new == old
    out = old[1]
    assert out.startswith(f"@@scan-roots-{HEX}=")
    assert ("opt:--cross-fs=no" in out) == (kind == "deep")
    assert f"opt:--exclude-dir={av.scan_exclude_regex('/proc')}" in out
    if kind == "custom":
        assert "opt:--exclude-dir=^/srv/a\\+b(/|$)" in out and "opt:--exclude-dir=^/tmp(/|$)" in out
    assert out.endswith(f"Scanned files: 2\n{MARK}0\n") and old[0] == 0


@needs_sh
@needs_root
def test_a_busy_server_makes_the_scan_wait_and_give_up_like_before(tmp_path):
    held = []

    def hold(base: Path) -> None:
        (base / "lock").mkdir(mode=0o700)
        fh = open(base / "lock" / "scan.lock", "a")  # noqa: SIM115 - bleibt bis zum Ende offen
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(fh)

    try:
        old, new = _scan_both(tmp_path, ["/tmp"], exclude=[], limit_s=10, wait_s=1, prepare=hold)
    finally:
        for fh in held:
            fh.close()
    assert new == old and old[1] == f"@@scan-busy-{HEX}\n"


@needs_sh
def test_the_time_limit_ends_the_scan_like_before(tmp_path):
    old, new = _scan_both(tmp_path, ["/tmp"], exclude=[], tools={"clamscan": "sleep 20\n"}, limit_s=4)
    assert new == old and old[1].endswith(f"@@scan-timeout-{HEX}\n")


def _watch_both(tmp_path: Path, paths: list[str], *, skip: list[str], use_clamd: bool = False,
                tools: dict[str, str] | None = None, minutes: int = 10) -> list[tuple[int, str]]:
    av = _av()
    outs = []
    for name in ("alt", "neu"):
        base = tmp_path / name
        bin_dir = stubs(base, {"clamscan": CLAMSCAN, **(tools or {})})
        path = f"{bin_dir}:/usr/bin:/bin"
        if name == "alt":
            command = av.build_watch_command(paths, minutes=minutes, max_filesize_mb=50, use_clamd=use_clamd, mark=MARK,
                                             skip_folders=skip)
            command = av.guard_command(command, mark=MARK, limit_s=300, wait_s=None, root_dir=str(base / "lock"))
            outs.append(run_sh([SH, "-c", command], path))
        else:
            args = av.watch_helper_args(paths, minutes=minutes, max_filesize_mb=50, use_clamd=use_clamd, mark=MARK,
                                        skip_folders=skip, limit_s=300)
            helper = helper_copy(base, path=path, sdmark=base / "sd")
            outs.append(run_sh([SH, str(helper), *args], "/nicht-da"))
            assert list((base / "run").iterdir()) == [], "keine Hilfsdateien bleiben liegen"
    return outs


@needs_sh
@pytest.mark.parametrize("skip", [[], ["/x"]], ids=["ohne-ausnahmen", "mit-ausnahmen"])
def test_the_watch_behaves_like_the_old_command(tmp_path, skip):
    tree = _tree(tmp_path)
    if skip:
        skip = [str(tree / "a" / "aus") + "/", str(tree / "a" / "we[i]rd*"), "relativ"]
    paths = [str(tree / "a"), str(tree / "link"), str(tree / "fehlt")]
    old, new = _watch_both(tmp_path, paths, skip=skip)
    assert new == old
    out = old[1]
    assert f"list:{tree / 'a' / 'sub' / 'evil.sh'}" in out and f"list:{tree / 'link' / 'evil2.sh'}" in out
    assert f"file:{tree / 'a' / 'zeile'}\numbruch-evil.sh" in out and "Unusual file names: 1\n" in out
    assert ("ausgenommen" in out) == (not skip) and ("auch-aus" in out) == (not skip)
    assert out.endswith(f"{MARK}1\n")


@needs_sh
@pytest.mark.parametrize("clamd", [
    'case "$1" in --ping) exit 0;; esac; echo "/x/evil: Eicar FOUND"; exit 1\n',
    'case "$1" in --ping) exit 0;; esac; echo "ERROR: Verbindung weg"; exit 2\n',
    "exit 1\n",
], ids=["clamd-antwortet", "clamd-fehler", "clamd-aus"])
def test_the_watch_over_clamd_behaves_like_the_old_command(tmp_path, clamd):
    tree = _tree(tmp_path)
    old, new = _watch_both(tmp_path, [str(tree / "b")], skip=[], use_clamd=True, tools={"clamdscan": clamd})
    assert new == old
    assert ("Scanned files: 1\n" in old[1]) == ("FOUND" in clamd)


@needs_sh
def test_the_watch_checks_at_most_2000_files_like_before(tmp_path):
    many = tmp_path / "viele"
    many.mkdir()
    for i in range(2003):
        (many / f"f{i:04d}").write_text("x")
    old, new = _watch_both(tmp_path, [str(many)], skip=[])
    assert new == old and "Skipped files: 3\n" in old[1] and old[1].count("list:") == 2000


@needs_sh
def test_old_files_are_not_checked_by_the_watch(tmp_path):
    tree = _tree(tmp_path)
    for f in (tree / "b").iterdir():
        os.utime(f, (1, 1))
    old, new = _watch_both(tmp_path, [str(tree / "b")], skip=[], minutes=1)
    # Die Änderungszeit des Eintrags (ctime) ist frisch: Der Wächter prüft sie trotzdem, wie bisher.
    assert new == old


@pytest.mark.parametrize(
    "args",
    [
        ["scan"], ["scan", MARK, "600", "30", "50", "--"], ["scan", MARK, "600", "30", "50", "^/a", "/b"],
        ["scan", "@@scan-rc-0123=", "600", "30", "50", "--", "/b"], ["scan", MARK, "0", "30", "50", "--", "/b"],
        ["scan", MARK, "600", "x", "50", "--", "/b"], ["scan", MARK, "600", "30", "1234567", "--", "/b"],
        ["scan", MARK, "600", "30", "050", "--", "/b"], ["scan", MARK, "600", "30", "50", "/proc", "--", "/b"],
        ["scan", MARK, "600", "30", "50", "^", "--", "/b"], ["scan", MARK, "600", "30", "50", "--", "relativ"],
        ["scan", MARK, "600", "30", "50", "--", "-r"],
        ["watch", MARK, "300", "10", "50", "2", "--", "/b"], ["watch", MARK, "300", "10", "50", "0", "^/x", "--", "/b"],
        ["watch", MARK, "300", "10", "50", "0", "/", "--", "/b"], ["watch", MARK, "300", "10", "50", "0", "--", "b"],
        ["watch", MARK, "300", "-1", "50", "0", "--", "/b"],
        ["scan-run", "x", MARK, "50", "--", "/b"], ["scan-run", "w0", MARK, "50", "--", "/b"],
        ["watch-run", "n", MARK, "10", "50", "1", "--", "-x"],
        ["odd-scan"], ["odd-scan", "/etc/passwd", "/etc/shadow", "/tmp/x"],
    ],
)
def test_strange_arguments_are_refused(tmp_path, args):
    helper = helper_copy(tmp_path, path="/nicht-da", sdmark=tmp_path / "sd")
    done = subprocess.run([SH, str(helper), *args], capture_output=True, text=True, timeout=30, check=False)
    assert (done.returncode, done.stdout) == (64, ""), (args, done.stderr)
    assert not (tmp_path / "lock").exists()


@needs_sh
def test_odd_scan_writes_only_into_its_own_files(tmp_path):
    """`odd-scan` hängt an zwei Dateien an: nur an eigene, echte Dateien von mktemp im Ordner für Hilfsdateien."""
    bin_dir = stubs(tmp_path, {"clamscan": CLAMSCAN})
    helper = helper_copy(tmp_path, path=f"{bin_dir}:/usr/bin:/bin", sdmark=tmp_path / "sd")
    run = tmp_path / "run"
    out, rc = (run / "tmp.AAAAAAAAAA"), (run / "tmp.BBBBBBBBBB")
    out.write_text("")
    rc.write_text("")
    (run / "tmp.CCCCCCCCCC").symlink_to(tmp_path / "ziel")
    (tmp_path / "ziel").write_text("")
    for args in ([str(run / "tmp.CCCCCCCCCC"), str(rc)], [str(out), str(tmp_path / "ziel")], [str(out), str(run / "tmp.kurz")],
                 [str(out), str(rc), "relativ"]):
        done = subprocess.run([SH, str(helper), "odd-scan", *args, "/b/evil"][: 5 if len(args) == 2 else 4], capture_output=True,
                              text=True, timeout=30, check=False)
        assert done.returncode == 64, args
    assert out.read_text() == rc.read_text() == (tmp_path / "ziel").read_text() == ""
    done = subprocess.run([SH, str(helper), "odd-scan", str(out), str(rc), "/b/evil", "/b/gut"], capture_output=True, text=True,
                          timeout=30, check=False)
    assert done.returncode == 0 and rc.read_text() == "1 2\n" and "file:/b/evil\n" in out.read_text()


def test_the_dashboard_passes_the_same_settings():
    av = _av()
    args = av.scan_helper_args(["/srv", "/home"], max_filesize_mb=70, exclude=["/srv/a+b", "relativ"], mark=MARK,
                               limit_s=3600, wait_s=360)
    regexes = [av.scan_exclude_regex(p) for p in ["/proc", "/sys", "/dev", "/run", av.QUARANTINE_DIR, "/srv/a+b"]]
    assert args == ["scan", MARK, "3600", "360", "70", *regexes, "--", "/srv", "/home"]
    args = av.watch_helper_args(["/tmp"], minutes=12, max_filesize_mb=70, use_clamd=True, mark=MARK,
                                skip_folders=["/srv/x*/", "relativ", "/"], limit_s=300)
    assert args == ["watch", MARK, "300", "12", "70", "1", "/srv/x\\*", "--", "/tmp"]
    for bad in ({"paths": []}, {"paths": ["relativ"]}, {"paths": ["/a\x00b"]}, {"mark": "@@scan-rc-x="}, {"limit_s": 0},
                {"limit_s": 1_000_000}):
        kwargs = {"paths": ["/tmp"], "mark": MARK, "limit_s": 300, **bad}
        paths = kwargs.pop("paths")
        assert av.scan_helper_args(paths, max_filesize_mb=50, exclude=[], wait_s=30, **kwargs) is None, bad
        assert av.watch_helper_args(paths, minutes=10, max_filesize_mb=50, use_clamd=False, skip_folders=[], **kwargs) is None
    assert av.watch_helper_args(["/tmp"], minutes=0, max_filesize_mb=50, use_clamd=False, mark=MARK, skip_folders=[],
                                limit_s=300) is None


def test_the_script_uses_the_places_and_limits_of_the_module():
    av = _av()
    manifest = tomllib.loads((EXT_DIR / "extension.toml").read_text(encoding="utf-8"))["extension"]
    assert av.HELPER_SCAN_VERSION <= manifest["root_helper"]["version"]
    text = SCRIPT.read_text(encoding="ascii")
    assert f"LOCKDIR={av.LOCK_DIR_ROOT}\n" in text and f"N={av.LOCK_FILE}\n" in text
    assert f'T="timeout -k {av.KILL_AFTER_S} $limit"' in text and f"limit=$((limit_s - {av.TIMEOUT_MARGIN_S}))" in text
    assert f"-gt {2 * av.TIMEOUT_MARGIN_S} ]" in text
    assert f"NR <= {av.WATCH_MAX_FILES} " in text and f"printf '%0{av._LONG_PATH_BYTES}d' 0" in text
    assert av.WATCH_EXCLUDED == ["/dev/shm/qb-*"] and "-not -path '/dev/shm/qb-*'" in text
    assert f"'{av._CLAMD_SKIP_OK}'" in text and f'echo "{av._ODD_NAME_LABEL}: $T"' in text
    assert f'echo "{av._SKIPPED_LABEL}: $S"' in text
