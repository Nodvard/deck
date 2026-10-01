"""Virenschutz in nexus-soc: Befehle bauen und ClamAV-/Lynis-Ausgaben auswerten."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "nexus-soc" / "src"))

from nodvard_deck.core.deny_patterns import match_deny_patterns  # noqa: E402
from nodvard_deck_ext_nexus_soc import antivirus as av  # noqa: E402

# Eine feste Marke fuer die Tests, die Befehl und Auswertung selbst verbinden (im Betrieb zieht der
# Defender je Lauf eine neue: `av.new_rc_mark()`).
MARK = "@@scan-rc-0123456789abcdef="

CLAM_INFECTED = f"""/tmp/eicar.com: Win.Test.EICAR_HC-1 FOUND
/home/nico/x.sh: Unix.Trojan.Mirai-123 FOUND

----------- SCAN SUMMARY -----------
Known viruses: 8700000
Engine version: 1.0.7
Scanned directories: 120
Scanned files: 3412
Infected files: 2
{MARK}1
"""


def test_parse_infected_scan():
    r = av.parse_scan_output(CLAM_INFECTED, MARK)
    assert r.status == "infected"
    assert r.files_scanned == 3412
    assert r.findings == [("/tmp/eicar.com", "Win.Test.EICAR_HC-1"), ("/home/nico/x.sh", "Unix.Trojan.Mirai-123")]


def test_parse_clean_missing_and_error():
    assert av.parse_scan_output(f"Scanned files: 10\nInfected files: 0\n{MARK}0", MARK).status == "clean"
    missing = av.parse_scan_output(f"sh: 1: clamscan: not found\n{MARK}127", MARK)
    assert (missing.status, missing.error) == ("error", "ClamAV ist auf diesem Server nicht installiert.")
    broken = av.parse_scan_output(f"ERROR: Can't open file or directory\n{MARK}2", MARK)
    assert broken.status == "error" and "Can't open" in broken.error


FORGED = "@@scan-rc-ffffffffffffffff="  # was ein Angreifer als Marke raten koennte
MARK_RE = re.compile(r"@@scan-rc-[0-9a-f]{16}=")


def test_the_mark_is_random_per_run_and_has_the_agreed_format():
    """Fix N1: je Lauf eine neue Zufallsmarke `@@scan-rc-<16 hex>=`, nicht mehr die feste `@@nexus-rc=`."""
    marks = {av.new_rc_mark() for _ in range(50)}
    assert len(marks) == 50 and all(MARK_RE.fullmatch(m) for m in marks)

    # Auch ohne Angabe zieht jeder Befehl seine eigene Marke -- genau eine, und nicht die alte.
    commands = [
        av.build_scan_command(["/tmp"]), av.build_scan_command(["/tmp"]),
        av.build_watch_command(["/tmp"], minutes=5), av.build_watch_command(["/tmp"], minutes=5, use_clamd=True),
    ]
    found = [MARK_RE.findall(c) for c in commands]
    assert all(len(f) == 1 for f in found), found
    assert len({f[0] for f in found}) == len(commands)
    assert not any("nexus-rc" in c for c in commands)


@pytest.mark.parametrize("bad", ["@@nexus-rc=", "@@scan-rc-XYZ=", "@@scan-rc-0123456789abcdef", 'x"; rm -rf /; echo "', ""])
def test_a_malformed_mark_is_refused_before_it_reaches_a_shell(bad):
    with pytest.raises(ValueError):
        av.build_scan_command(["/tmp"], mark=bad)
    with pytest.raises(ValueError):
        av.build_watch_command(["/tmp"], minutes=5, mark=bad)
    with pytest.raises(ValueError):
        av.parse_scan_output("Scanned files: 1\n", bad)


def test_a_file_name_with_a_mark_in_it_cannot_hide_a_finding():
    """Fix N1: Die Ausgabe wurde an der ERSTEN Marke abgeschnitten -- ein Schadprogramm namens
    `/tmp/x@@nexus-rc=0` verschwand samt seinem Fund. Jetzt zaehlt nur der letzte Treffer am Zeilenanfang."""
    # Die alte feste Marke im Dateinamen ist harmlos ...
    old = f"/tmp/x@@nexus-rc=0: Win.Test.EICAR_HC-1 FOUND\n@@nexus-rc=0\nScanned files: 5\nInfected files: 1\n{MARK}1\n"
    res = av.parse_scan_output(old, MARK)
    assert (res.status, res.files_scanned) == ("infected", 5)
    assert res.findings == [("/tmp/x@@nexus-rc=0", "Win.Test.EICAR_HC-1")]
    # ... ebenso eine geratene neue Marke im Dateinamen, auch wenn sie eine Zeile fuer sich bekommt.
    forged = (f"/tmp/y{FORGED}0: Win.Test.EICAR_HC-1 FOUND\n/tmp/a\n{FORGED}0\n/tmp/b: Unix.Trojan.Mirai FOUND\n"
              f"Scanned files: 5\nInfected files: 2\n{MARK}1\n")
    res = av.parse_scan_output(forged, MARK)
    assert res.status == "infected"
    assert res.findings == [(f"/tmp/y{FORGED}0", "Win.Test.EICAR_HC-1"), ("/tmp/b", "Unix.Trojan.Mirai")]
    # Selbst die richtige Marke mitten in der Ausgabe verschiebt nichts: der letzte Treffer gilt.
    early = f"/tmp/a\n{MARK}0\n/tmp/b: Unix.Trojan.Mirai FOUND\nScanned files: 5\n{MARK}1\n"
    res = av.parse_scan_output(early, MARK)
    assert (res.status, res.findings) == ("infected", [("/tmp/b", "Unix.Trojan.Mirai")])
    # Ohne die Marke des Laufs zu kennen gilt dieselbe Regel (letzter Treffer, jede Marke im Format).
    assert av.parse_scan_output(forged).status == "infected" and av.parse_scan_output(early).status == "infected"


def test_only_the_mark_of_this_run_counts_as_a_return_code():
    # Eine Marke mit anderem Zufallsteil (oder die alte feste) ist kein Rueckgabecode: kein "sauber" durch Faelschung.
    for other in (f"{FORGED}0", "@@nexus-rc=0"):
        res = av.parse_scan_output(f"ERROR: Can't open file or directory\n{other}\n", MARK)
        assert res.status == "error", other
    # Zeichen hinter dem Code machen aus einer Fund-Zeile keine Marke. Die Zeile beginnt nicht mit "/" und
    # ist kein lesbarer Fund -- aber der echte Code 1 sagt: ClamAV hat etwas gefunden. Nie "sauber".
    res = av.parse_scan_output(f"{FORGED}0: Win.Test.EICAR_HC-1 FOUND\nScanned files: 1\n{MARK}1\n", MARK)
    assert (res.status, res.findings, res.infected, res.unreliable) == ("infected", [], 1, True)


# --- Dateinamen mit Steuerzeichen: Auswertung nicht steuerbar (gleiche Fehlerart wie N1) --------------
# ClamAV schreibt Dateinamen roh in die Ausgabe (mit ClamAV 1.0.5 geprueft).

EICAR = "Win.Test.EICAR_HC-1"
SUMMARY_ONE = "\n----------- SCAN SUMMARY -----------\nScanned files: 5\nInfected files: 1\n"


@pytest.mark.parametrize(
    "char", ["\r", "\x0b", "\x0c", "\x1c", "\x1e", "\x85", "\u2028", "\u2029", "\x1b[2J", "\t", "\x07"],
    ids=["CR", "VT", "FF", "FS", "RS", "NEL", "LS", "PS", "ESC", "TAB", "BEL"],
)
def test_a_control_character_in_a_file_name_cannot_hide_a_finding(char):
    """`str.splitlines()` trennt bei CR, VT, FF, FS-RS, NEL, U+2028 und U+2029 -- ein Schadprogramm mit
    so einem Zeichen im Namen zerfiel in zwei Zeilen, keine passte mehr auf `/pfad: Signatur FOUND`, und der
    Lauf (Code 1) galt als sauber. Jetzt bleibt es eine Zeile."""
    path = f"/tmp/x{char}yy"
    res = av.parse_scan_output(f"{path}: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK)
    assert (res.status, res.findings, res.infected, res.unreliable) == ("infected", [(path, EICAR)], 1, False)
    assert res.files_scanned == 5


def test_a_line_break_in_a_file_name_is_never_clean_and_never_trusted():
    """Zeilenumbruch im Namen: Das letzte Bruchstueck (`yy: ... FOUND`) beginnt nicht mit "/" und gab keinen
    Fund her -- der Lauf galt als sauber. Jetzt: Fund (Code 1), aber nicht verlaesslich."""
    res = av.parse_scan_output(f"/tmp/x\nyy: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK)
    assert (res.status, res.findings, res.infected, res.unreliable) == ("infected", [], 1, True)
    # Auch ohne Zusammenfassung (Waechter ueber clamdscan) und ohne die Zahl daraus.
    res = av.parse_scan_output(f"/tmp/x\nyy: {EICAR} FOUND\nScanned files: 5\n{MARK}1\n", MARK)
    assert (res.status, res.infected, res.unreliable) == ("infected", 1, True)


def test_a_line_break_in_a_file_name_cannot_forge_a_finding_for_another_path():
    """Der Name `q<LF>/etc/passwd` (Ordner `q<LF>`, darin `etc/passwd`) ergibt die Zeilen `/tmp/q` und
    `/etc/passwd: Sig FOUND`: Die zweite sah wie ein Fund fuer /etc/passwd aus, und die Quarantaene
    haette ihn als root verschoben. Der Lauf ist nicht verlaesslich (Bruchstueck `/tmp/q`)."""
    res = av.parse_scan_output(f"/tmp/q\n/etc/passwd: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK)
    assert res.status == "infected" and res.unreliable is True
    assert res.findings == [("/etc/passwd", EICAR)]  # bleibt sichtbar, aber mit Warnung

    # Das erste Bruchstueck darf selbst wie ein Fund aussehen: dann stimmt die Zahl nicht mehr (2 Zeilen, 1 Fund).
    res = av.parse_scan_output(f"/tmp/a: Fake.Sig FOUND\n/etc/passwd: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK)
    assert res.status == "infected" and res.unreliable is True

    # ... oder wie eine Fehlermeldung: "ERROR: ..." beginnt nie mit "/" und gehoert nicht in einen Namen am Anfang.
    res = av.parse_scan_output(f"/tmp/a\nERROR: x\n/etc/passwd: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK)
    assert res.unreliable is True


def test_a_name_cannot_fake_the_summary_either():
    """Name mit `Scanned files:` / `Infected files:` am Zeilenanfang: die echte Zusammenfassung steht
    nach allen Namen, ihr letzter Treffer gilt."""
    out = (f"/tmp/x\nScanned files: 9999\nInfected files: 0\n/tmp/real: {EICAR} FOUND\n"
           f"\n----------- SCAN SUMMARY -----------\nScanned files: 7\nInfected files: 2\n{MARK}1\n")
    res = av.parse_scan_output(out, MARK)
    assert res.files_scanned == 7 and res.status == "infected" and res.unreliable is True
    assert res.infected == 2  # laut ClamAV: zwei Funde, aber nur einer ist lesbar


def test_a_normal_infected_run_is_reliable():
    res = av.parse_scan_output(CLAM_INFECTED, MARK)
    assert (res.status, res.infected, res.unreliable) == ("infected", 2, False)
    # Meldungen von ClamAV (Warnungen, Fehler, fehlende Dateien) machen einen Lauf nicht unsicher.
    noisy = (f"LibClamAV Warning: ***  The virus database is older than 7 days!  ***\nWARNING: x\n"
             f"ERROR: Can't access file /tmp/gone\n/tmp/a: {EICAR} FOUND\n{SUMMARY_ONE}Total errors: 1\n{MARK}1\n")
    res = av.parse_scan_output(noisy, MARK)
    assert (res.status, res.unreliable, res.errors) == ("infected", False, ["ERROR: Can't access file /tmp/gone"])


def test_clamav_ending_with_code_1_is_never_clean_whatever_the_output_says():
    for out in (f"{MARK}1\n", f"Scanned files: 3\nInfected files: 0\n{MARK}1\n", f"Killed\n{MARK}1\n"):
        res = av.parse_scan_output(out, MARK)
        assert (res.status, res.findings, res.infected, res.unreliable) == ("infected", [], 1, True), out
    # Die Zahl aus der Zusammenfassung zaehlt auch ohne Code (und ohne lesbare Zeile).
    res = av.parse_scan_output(f"Scanned files: 3\nInfected files: 2\n{MARK}0\n", MARK)
    assert (res.status, res.infected, res.unreliable) == ("infected", 2, True)


def test_a_file_named_like_the_shell_error_cannot_fake_a_missing_clamav():
    """`"clamscan: not found" in body` machte aus einem Fund in `/tmp/clamscan: not found` den Fehler
    "ClamAV nicht installiert" -- der Fund war weg."""
    res = av.parse_scan_output(f"/tmp/clamscan: not found: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK)
    assert (res.status, res.findings) == ("infected", [("/tmp/clamscan: not found", EICAR)])
    res = av.parse_scan_output(f"/tmp/x\nclamscan: not found: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK)
    assert res.status == "infected"
    # Auch ein Name, dessen Teilstueck wie die Shell-Meldung aussieht ("/tmp/sh: 1: clamscan: not found"),
    # versteckt nichts -- der Rueckgabecode 1 und die Zusammenfassung sagen: es gab einen Fund.
    res = av.parse_scan_output(f"/tmp/sh: 1: clamscan: not found\na: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK)
    assert (res.status, res.infected, res.unreliable) == ("infected", 1, True)
    # Die echte Meldung der Shell bleibt erkannt (mit und ohne Rueckgabecode 127).
    for shell in ("sh: 1: clamscan: not found", "bash: line 1: clamscan: command not found", "/bin/sh: clamscan: not found"):
        assert av.parse_scan_output(f"{shell}\n{MARK}127\n", MARK).error == av.NOT_INSTALLED_MESSAGE
        assert av.parse_scan_output(f"{shell}\n{MARK}2\n", MARK).error == av.NOT_INSTALLED_MESSAGE


def test_random_file_names_never_hide_or_forge_a_finding():
    """Zufallsnamen aus gefaehrlichen Bausteinen (Zeilenumbruch, Wagenruecklauf, `: ... FOUND`, Marken,
    Shell-Meldung, Zusammenfassungszeilen ...): Der Lauf ist IMMER ein Fund (nie sauber, nie Fehler), und ist
    er nicht `unreliable`, dann sind die gelesenen Funde genau die echten -- kein verstecktes, kein
    vorgetaeuschtes Ergebnis. Feste Startzahl, damit ein Fehlschlag sich wiederholen laesst."""
    import random

    pieces = ["a", "b", "x y", "/", "/etc/passwd", "\n", "\n", "\r", "\x0c", "\u2028", ": ", ": Sig FOUND", " FOUND",
              "ERROR: x", "Scanned files: 99", "Infected files: 0", "----------- SCAN SUMMARY -----------",
              MARK + "0", FORGED + "1", "@@nexus-rc=0", "clamscan: not found", "sh: 1: clamscan: not found",
              "  ", ".", "..", "LibClamAV Warning: x", "/tmp/z"]
    rng = random.Random(20261001)
    reliable = unreliable = 0
    for _ in range(4000):
        names = ["/tmp/" + "".join(rng.choice(pieces) for _ in range(rng.randint(1, 5))) for _ in range(rng.randint(1, 3))]
        out = "".join(f"{n}: Test.Sig FOUND\n" for n in names)
        out += rng.choice(["", "ERROR: Can't access file /tmp/gone\n", "LibClamAV Warning: old db\n"])
        out += f"\n----------- SCAN SUMMARY -----------\nScanned files: 5\nInfected files: {len(names)}\n{MARK}1\n"
        res = av.parse_scan_output(out, MARK)
        assert res.status == "infected" and res.infected >= len(names), (names, res)
        if res.unreliable:
            unreliable += 1
        else:
            reliable += 1
            assert res.findings == [(n, "Test.Sig") for n in names], (names, res)
    assert reliable > 500 and unreliable > 500  # beide Zweige werden wirklich geprueft


def test_a_run_without_its_mark_is_never_clean():
    """Fehlt die Marke (Lauf abgebrochen, Ausgabe abgeschnitten), gilt der Lauf nicht als sauber, auch
    wenn eine Zusammenfassung dasteht. Frueher: Zusammenfassung da, Marke weg = sauber."""
    res = av.parse_scan_output("Scanned files: 3\nInfected files: 0\n", MARK)
    assert res.status == "error" and "unvollständig" in res.error
    assert av.parse_scan_output("", MARK).status == "error"
    # Funde bleiben Funde, auch ohne Marke -- aber unsicher.
    res = av.parse_scan_output(f"/tmp/a: {EICAR} FOUND\nScanned files: 3\n", MARK)
    assert (res.status, res.findings, res.unreliable) == ("infected", [("/tmp/a", EICAR)], True)
    # Eine falsche Marke (anderer Lauf, geraten) ersetzt sie nicht.
    assert av.parse_scan_output(f"Scanned files: 3\n{FORGED}0\n", MARK).status == "error"


def test_parse_status_and_lynis():
    st = av.parse_status(
        "@@clam\nClamAV 1.0.7/27410/Wed Sep 24 08:23:12 2026\n@@fresh\nactive\n@@lynis\n3.0.8\n@@quarantine\n2\n@@os\ndebian\n@@end\n"
    )
    assert (st.clamav_installed, st.clamav_version, st.signature_version, st.freshclam_active) == (True, "1.0.7", "27410", True)
    assert (st.lynis_installed, st.quarantine_files, st.os_id) == (True, 2, "debian")
    assert av.parse_status("@@clam\nnone\n@@lynis\nnone\n@@end").clamav_installed is False

    res = av.parse_lynis(
        "warning[]=SSH-7408|Consider hardening SSH configuration|-|-|\n"
        "suggestion[]=AUTH-9286|Configure maximum password age|-|-|\nhardening_index=68\n@@done"
    )
    assert (res.status, res.hardening_index) == ("ok", 68)
    assert res.warnings == ["SSH-7408: Consider hardening SSH configuration"]
    assert av.parse_lynis("@@nolynis").error == "Lynis ist auf diesem Server nicht installiert."


@pytest.mark.parametrize(("fresh", "expected"), [
    ("active", True), ("inactive", False), ("failed", False), ("", None), ("activating", None),
    # Alte Ausgabe (`|| echo unknown` haengte bei rc 3 eine zweite Zeile an): trotzdem erkannt.
    ("inactive\nunknown", False),
])
def test_parse_status_reads_the_freshclam_state(fresh, expected):
    st = av.parse_status(f"@@clam\nClamAV 1.0.7/27410/Wed Sep 24 08:23:12 2026\n@@fresh\n{fresh}\n@@lynis\nnone\n@@end\n")
    assert st.freshclam_active is expected


def _status_stubs(tmp_path: Path, *, freshclam: str, rc: int) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in {
        "systemctl": f"echo {freshclam}\nexit {rc}\n",
        "clamscan": "echo 'ClamAV 1.0.7/28111/Tue Sep  2 08:23:12 2026'\n",
    }.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + body)
        (bin_dir / name).chmod(0o755)
    return bin_dir


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize(("state", "rc", "expected"), [("inactive", 3, False), ("failed", 3, False), ("active", 0, True)])
def test_status_command_detects_disabled_signature_updates_in_a_real_shell(tmp_path, state, rc, expected):
    """`systemctl is-active ... || echo unknown` gab bei einem gestoppten
    Dienst (rc 3) 'inactive' UND 'unknown' aus -- Nodvard Deck warnte nie vor alten Signaturen."""
    bin_dir = _status_stubs(tmp_path, freshclam=state, rc=rc)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    out = subprocess.run(["/bin/sh", "-c", av.STATUS_COMMAND], capture_output=True, text=True, env=env, timeout=60, check=False)
    st = av.parse_status(out.stdout)
    assert st.clamav_installed and st.freshclam_active is expected, out.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_status_command_never_touches_the_lynis_report(tmp_path):
    """`lynis show version` laeuft erst nach PID-Pruefung und Anlegen des
    Berichts -- jede Status-Abfrage leerte /var/log/lynis-report.dat (auch mitten im
    Audit). `lynis --version` beendet sich vorher."""
    bin_dir = _status_stubs(tmp_path, freshclam="active", rc=0)
    report = tmp_path / "lynis-report.dat"
    report.write_text("warning[]=SSH-7408|x|-|-|\nhardening_index=68\n")
    (bin_dir / "lynis").write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --version ]; then echo 3.1.4; exit 0; fi\n"
        f"echo '# Lynis Report' > '{report}'\necho 3.1.4\n"
    )
    (bin_dir / "lynis").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    out = subprocess.run(["/bin/sh", "-c", av.STATUS_COMMAND], capture_output=True, text=True, env=env, timeout=60, check=False)
    st = av.parse_status(out.stdout)
    assert (st.lynis_installed, st.lynis_version) == (True, "3.1.4"), out.stdout
    assert "hardening_index=68" in report.read_text()
    assert "show version" not in av.STATUS_COMMAND

    # Ohne Lynis: "none" -> nicht installiert.
    (bin_dir / "lynis").unlink()
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    out = subprocess.run(["/bin/sh", "-c", av.STATUS_COMMAND], capture_output=True, text=True, env=env, timeout=60, check=False)
    st = av.parse_status(out.stdout)
    assert (st.lynis_installed, st.lynis_version) == (False, None), out.stdout


def test_signature_update_switches_the_auto_update_on():
    cmd = av.INSTALL_COMMANDS["signatures"]
    assert "systemctl enable --now clamav-freshclam" in cmd
    assert match_deny_patterns(cmd) is None


def test_commands_pass_the_deny_list_and_quote_paths():
    evil = "/tmp/a'; rm -rf / #"
    commands = [
        av.build_scan_command(["/tmp", evil]),
        av.build_scan_command(["/"]),
        av.build_watch_command(["/tmp"], minutes=12),
        av.quarantine_command("/tmp/eicar.com", "f_1_eicar.com"),
        av.restore_command("/var/lib/nexus-quarantine/f_1_eicar.com", "/tmp/eicar.com", "644"),
        av.delete_command("/var/lib/nexus-quarantine/f_1_eicar.com"),
        av.LYNIS_COMMAND,
        av.STATUS_COMMAND,
    ]
    for cmd in commands[1:]:
        assert match_deny_patterns(cmd) is None, cmd
    # Ein boesartiger Pfad bleibt ein Argument (gequotet) -- und die Sperrliste greift trotzdem.
    assert "'/tmp/a'\"'\"'; rm -rf / #'" in commands[0]
    assert match_deny_patterns(commands[0]) is not None
    with pytest.raises(ValueError):
        av.delete_command("/etc/passwd")


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell (Git-Bash-sh scheitert an Windows-Pfaden)")
def test_quarantine_and_restore_roundtrip_on_a_real_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "QUARANTINE_DIR", str(tmp_path / "q"))
    victim = tmp_path / "evil.sh"
    victim.write_text("echo boom")
    victim.chmod(0o750)
    out = subprocess.run(["sh", "-c", av.quarantine_command(str(victim), "f1_evil.sh")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert av.parse_mode(out.stdout) == "750"
    assert not victim.exists()
    qfile = tmp_path / "q" / "f1_evil.sh"
    assert qfile.exists() and (qfile.stat().st_mode & 0o777) == 0

    back = subprocess.run(["sh", "-c", av.restore_command(str(qfile), str(victim), "750")], capture_output=True, text=True)
    assert back.returncode == 0, back.stderr
    assert victim.read_text() == "echo boom" and (victim.stat().st_mode & 0o777) == 0o750


# `[ -f ]` und `chmod` folgen symbolischen Links, `mv` verschiebt nur den
# Link -- als root landete chmod 000 so auf dem Linkziel (z. B. /etc/shadow).
@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_quarantine_refuses_a_symlink_and_leaves_its_target_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "QUARANTINE_DIR", str(tmp_path / "q"))
    system_file = tmp_path / "shadow"
    system_file.write_text("root:x:1\n")
    system_file.chmod(0o640)
    link = tmp_path / "evil.sh"
    link.symlink_to(system_file)
    out = subprocess.run(["sh", "-c", av.quarantine_command(str(link), "f1_evil.sh")], capture_output=True, text=True, check=False)
    assert out.returncode == 5
    assert "Verknüpfung" in out.stdout
    assert (system_file.stat().st_mode & 0o777) == 0o640 and system_file.read_text() == "root:x:1\n"
    assert not os.path.lexists(tmp_path / "q" / "f1_evil.sh")


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_quarantine_catches_a_file_swapped_for_a_symlink_right_before_mv(tmp_path, monkeypatch):
    import shutil

    monkeypatch.setattr(av, "QUARANTINE_DIR", str(tmp_path / "q"))
    system_file = tmp_path / "sudo"
    system_file.write_text("#!/bin/sh\n")
    system_file.chmod(0o755)
    victim = tmp_path / "evil.sh"
    victim.write_text("echo boom")
    # Ein mv, das vorher (wie ein Angreifer im richtigen Moment) die Datei gegen einen Link tauscht.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "mv").write_text(f'#!/bin/sh\nrm -f "$2"; ln -s "$VICTIM" "$2"\nexec {shutil.which("mv")} "$@"\n')
    (bindir / "mv").chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "VICTIM": str(system_file)}
    out = subprocess.run(["sh", "-c", av.quarantine_command(str(victim), "f1_evil.sh")], capture_output=True, text=True,
                         env=env, check=False)
    assert out.returncode == 5, out.stdout + out.stderr
    assert "Verknüpfung" in out.stdout
    assert (system_file.stat().st_mode & 0o777) == 0o755
    assert not os.path.lexists(tmp_path / "q" / "f1_evil.sh")


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_quarantine_refuses_when_a_parent_folder_became_a_symlink(tmp_path, monkeypatch):
    """Tausch des ORDNERS nach dem Scan (/home/u/d -> /etc): die Pruefung auf den
    letzten Pfadteil sieht dann eine echte Datei -- verschoben wuerde /etc/shadow."""
    monkeypatch.setattr(av, "QUARANTINE_DIR", str(tmp_path / "q"))
    etc = tmp_path / "etc"
    etc.mkdir()
    shadow = etc / "shadow"
    shadow.write_text("root:x:1\n")
    shadow.chmod(0o640)
    home = tmp_path / "home"
    home.mkdir()
    (home / "d").symlink_to(etc)
    found = home / "d" / "shadow"  # so hat clamscan den Fund gemeldet, als d noch ein Ordner war
    for shell in ("sh", "bash"):
        out = subprocess.run([shell, "-c", av.quarantine_command(str(found), "f1_shadow")], capture_output=True, text=True, check=False)
        assert out.returncode == 5, out.stdout + out.stderr
        assert "Verknüpfung" in out.stdout
        assert shadow.read_text() == "root:x:1\n" and (shadow.stat().st_mode & 0o777) == 0o640
        assert not os.path.lexists(tmp_path / "q" / "f1_shadow")


def test_quarantine_command_rejects_paths_without_a_file_name():
    for bad in ("/tmp/", "/tmp/.", "/tmp/..", "tmp/x"):
        with pytest.raises(ValueError):
            av.quarantine_command(bad, "f1_x")


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_restore_refuses_when_the_original_folder_became_a_symlink(tmp_path):
    """Wiederherstellen laeuft als root und behaelt Besitzer und Rechte: zeigt der
    Ordner inzwischen z. B. auf /etc/profile.d, laege die Datei sonst dort."""
    qdir = tmp_path / "q"
    qdir.mkdir()
    qfile = qdir / "f1_evil.sh"
    qfile.write_text("echo boom")
    qfile.chmod(0)
    profile_d = tmp_path / "etc" / "profile.d"
    profile_d.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    (home / "d").symlink_to(profile_d)
    for shell in ("sh", "bash"):
        out = subprocess.run([shell, "-c", av.restore_command(str(qfile), str(home / "d" / "evil.sh"), "755")],
                             capture_output=True, text=True, check=False)
        assert out.returncode == 5, out.stdout + out.stderr
        assert "Verknüpfung" in out.stdout
        assert list(profile_d.iterdir()) == [] and qfile.exists()


def test_restore_command_rejects_paths_without_a_file_name():
    for bad in ("/tmp/", "/tmp/..", "tmp/x"):
        with pytest.raises(ValueError):
            av.restore_command("/var/lib/nexus-quarantine/f1_x", bad, "644")


def test_watch_command_runs_without_clamav_hits(tmp_path):
    out = subprocess.run(["sh", "-c", av.build_watch_command([str(tmp_path)], minutes=5, mark=MARK)], capture_output=True, text=True)
    assert "Scanned files: 0" in out.stdout and f"{MARK}0" in out.stdout


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("kind", ["scan", "watch"])
def test_malware_with_a_mark_in_its_file_name_is_found_in_a_real_shell(tmp_path, kind):
    """Fix N1 mit echter Shell (Schnell-/Tiefenscan und Waechter): Dateien mit der alten festen
    Marke und mit einer geratenen neuen Marke im Namen werden trotzdem als Fund gemeldet."""
    watched = tmp_path / "watched"
    watched.mkdir()
    old = watched / "evil@@nexus-rc=0"
    forged = watched / f"evil2{FORGED}0"
    for f in (old, forged):
        f.write_text("x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # clamscan-Attrappe: meldet jede Datei (aus --file-list oder unter dem letzten Argument) als Fund.
    (bin_dir / "clamscan").write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do case "$a" in --file-list=*) list="${a#--file-list=}";; esac; last="$a"; done\n'
        'if [ -n "$list" ]; then files=$(cat "$list"); else files=$(find "$last" -type f); fi\n'
        'n=0; for f in $files; do echo "$f: Win.Test.EICAR_HC-1 FOUND"; n=$((n+1)); done\n'
        'echo; echo "----------- SCAN SUMMARY -----------"; echo "Scanned files: $n"; echo "Infected files: $n"\n'
        "exit 1\n"
    )
    (bin_dir / "clamscan").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    mark = av.new_rc_mark()
    cmd = (av.build_scan_command([str(watched)], mark=mark) if kind == "scan"
           else av.build_watch_command([str(watched)], minutes=5, mark=mark))
    out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60, check=False)
    assert out.stdout.rstrip().splitlines()[-1] == f"{mark}1", out.stdout + out.stderr
    res = av.parse_scan_output(out.stdout, mark)
    assert res.status == "infected" and res.files_scanned == 2, out.stdout + out.stderr
    assert sorted(path for path, _sig in res.findings) == sorted([str(old), str(forged)])


def _raw_name_clamscan(bin_dir: Path) -> None:
    """clamscan-Attrappe wie das echte ClamAV: Fund-Zeilen mit dem Dateinamen UNVERAENDERT (auch mit
    Zeilenumbruch, Wagenruecklauf, ...), aus `--file-list` (je Zeile ein Name) oder rekursiv unter dem
    letzten Argument."""
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "clamscan").write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do case "$a" in --file-list=*) list="${a#--file-list=}";; esac; last="$a"; done\n'
        'if [ -n "$list" ]; then\n'
        '  while IFS= read -r f; do printf \'%s: Win.Test.EICAR_HC-1 FOUND\\n\' "$f"; done < "$list"\n'
        '  n=$(wc -l < "$list" | tr -d \' \')\n'
        "else\n"
        "  find \"$last\" -type f -exec sh -c 'printf \"%s: Win.Test.EICAR_HC-1 FOUND\\n\" \"$1\"' _ {} \\;\n"
        "  n=$(find \"$last\" -type f -exec printf x \\; | wc -c)\n"
        "fi\n"
        'echo; echo "----------- SCAN SUMMARY -----------"; echo "Scanned files: $n"; echo "Infected files: $n"\n'
        "exit 1\n"
    )
    (bin_dir / "clamscan").chmod(0o755)


def _run_raw(tmp_path: Path, cmd: str) -> str:
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, env=env, timeout=60, check=False)
    return out.stdout.decode("utf-8", "replace")  # wie `core.ssh.run`: Bytes, kein Newline-Umbau


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("kind", ["scan", "watch"])
@pytest.mark.parametrize("char", ["\r", "\x0c", "\u2028", "\x85", "\x1b[2J"], ids=["CR", "FF", "LS", "NEL", "ESC"])
def test_malware_with_a_control_character_in_its_name_is_found_in_a_real_shell(tmp_path, kind, char):
    """Der Befund zu N1, zweiter Teil: Ein Schadprogramm mit Wagenruecklauf, Seitenvorschub, U+2028 oder
    ESC im Namen galt frueher als sauber (`splitlines()` riss die Fund-Zeile entzwei)."""
    watched = tmp_path / "watched"
    watched.mkdir()
    evil = watched / f"evil{char}name"
    evil.write_text("x")
    _raw_name_clamscan(tmp_path / "bin")
    mark = av.new_rc_mark()
    cmd = (av.build_scan_command([str(watched)], mark=mark) if kind == "scan"
           else av.build_watch_command([str(watched)], minutes=5, mark=mark))
    out = _run_raw(tmp_path, cmd)
    res = av.parse_scan_output(out, mark)
    assert (res.status, res.unreliable, res.findings) == (
        "infected", False, [(str(evil), "Win.Test.EICAR_HC-1")]), out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_a_line_break_in_a_directory_name_cannot_forge_a_finding_in_a_real_shell(tmp_path):
    """Ordner `q<LF>` mit darin `<Pfad der Opferdatei>`: In der Ausgabe steht dann eine Zeile
    `<Opferdatei>: Sig FOUND`. Der Lauf ist `infected`, aber nicht verlaesslich."""
    watched = tmp_path / "watched"
    victim = tmp_path / "etc" / "victim.conf"
    victim.parent.mkdir()
    victim.write_text("wichtig")
    trick = Path(f"{watched}/q\n{victim}")  # Ordner "q\n", darin der Pfad der Opferdatei
    trick.parent.mkdir(parents=True)
    trick.write_text("x")
    other = watched / "x\nyy"  # ... und ein Name, dessen Ende nach keinem Pfad aussieht
    other.write_text("x")
    _raw_name_clamscan(tmp_path / "bin")
    mark = av.new_rc_mark()
    out = _run_raw(tmp_path, av.build_scan_command([str(watched)], mark=mark))
    assert f"\n{victim}: Win.Test.EICAR_HC-1 FOUND\n" in out  # die Faelschung steht wirklich so in der Ausgabe
    res = av.parse_scan_output(out, mark)
    assert (res.status, res.unreliable, res.infected) == ("infected", True, 2), out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("kind", ["scan", "watch"])
def test_a_clean_run_is_still_clean_with_a_random_mark_in_a_real_shell(tmp_path, kind):
    """Der Gegenpart: Auch Dateien mit Marken im Namen machen einen sauberen Lauf nicht rot."""
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "harmlos@@nexus-rc=0").write_text("x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "clamscan").write_text("#!/bin/sh\necho 'Scanned files: 1'\necho 'Infected files: 0'\nexit 0\n")
    (bin_dir / "clamscan").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    mark = av.new_rc_mark()
    cmd = (av.build_scan_command([str(watched)], mark=mark) if kind == "scan"
           else av.build_watch_command([str(watched)], minutes=5, mark=mark))
    out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60, check=False)
    res = av.parse_scan_output(out.stdout, mark)
    assert (res.status, res.files_scanned, res.findings) == ("clean", 1, []), out.stdout + out.stderr


def _watch_stubs(tmp_path: Path, *, run_writable: bool) -> tuple[Path, Path]:
    """clamscan-Attrappe (merkt sich Aufruf und Dateiliste) und optional ein mktemp,
    das /run verweigert (Nodvard Deck ohne root) -- sonst das echte mktemp."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "clamscan.log"
    clam = bin_dir / "clamscan"
    clam.write_text(
        "#!/bin/sh\n"
        f"echo \"called $*\" >> '{log}'\n"
        "for a in \"$@\"; do case \"$a\" in --file-list=*) cat \"${a#--file-list=}\" >> " f"'{log}';; esac; done\n"
        "echo 'Scanned files: 1'\n"
    )
    clam.chmod(0o755)
    if not run_writable:
        real = shutil.which("mktemp")
        fake = bin_dir / "mktemp"
        fake.write_text(f"#!/bin/sh\ncase \"$*\" in *-p*) exit 1;; esac\nexec '{real}' \"$@\"\n")
        fake.chmod(0o755)
    return bin_dir, log


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("run_writable", [True, False])
def test_watch_command_does_not_find_its_own_file_list(tmp_path, run_writable):
    """mktemp legte die Liste in /tmp an (TMPDIR ist ueber SSH/sudo nicht
    gesetzt), /tmp wird ueberwacht -- find fand die eigene Liste, und clamscan lud bei
    jedem Lauf die komplette Datenbank, auch auf einem voellig ruhigen Server."""
    watched = tmp_path / "watched"
    watched.mkdir()
    bin_dir, log = _watch_stubs(tmp_path, run_writable=run_writable)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}", "TMPDIR": str(watched)}
    cmd = av.build_watch_command([str(watched)], minutes=5, mark=MARK)
    out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60, check=False)
    assert "Scanned files: 0" in out.stdout and f"{MARK}0" in out.stdout, out.stdout + out.stderr
    assert not log.exists(), log.read_text()
    assert list(watched.iterdir()) == []  # Liste wieder weg

    # Eine wirklich neue Datei wird geprueft -- die eigene Liste steht nicht darin.
    (watched / "neu.sh").write_text("echo hi\n")
    out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60, check=False)
    assert f"{MARK}0" in out.stdout, out.stdout + out.stderr
    lines = log.read_text().splitlines()
    assert lines[0].startswith("called -i --stdout --file-list=")
    assert lines[1:] == [str(watched / "neu.sh")]
    assert [p.name for p in watched.iterdir()] == ["neu.sh"]


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_command_ignores_proxmox_ipc_files_in_dev_shm(tmp_path):
    """pmxcfs/corosync schreiben staendig /dev/shm/qb-* -- der Waechter
    loeste auf Proxmox deshalb bei jedem Lauf einen vollen clamscan aus. Schnell- und
    Tiefenscan pruefen /dev/shm weiter."""
    assert "-not -path '/dev/shm/qb-*'" in av.build_watch_command(av.WATCH_PATHS, minutes=12)
    quick = av.build_scan_command(av.QUICK_PATHS)
    assert "/dev/shm" in quick and "qb-" not in quick

    # Ein Ordner steht fuer /dev/shm: qb-Datei loest nicht aus, andere neue Dateien schon.
    shm = tmp_path / "shm"
    shm.mkdir()
    (shm / "qb-1234-5678-pve2-data").write_text("ipc")
    bin_dir, log = _watch_stubs(tmp_path, run_writable=True)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    cmd = av.build_watch_command([str(shm)], minutes=5, exclude=[f"{shm}/qb-*"], mark=MARK)
    out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60, check=False)
    assert "Scanned files: 0" in out.stdout and f"{MARK}0" in out.stdout, out.stdout + out.stderr
    assert not log.exists(), log.read_text()
    (shm / "neu.sh").write_text("echo hi\n")
    subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60, check=False)
    assert log.read_text().splitlines()[1:] == [str(shm / "neu.sh")]

    # Mit dem echten /dev/shm und dem Standard-Ausschluss, wenn beschreibbar.
    real = Path("/dev/shm")
    if not (real.is_dir() and os.access(real, os.W_OK)):
        return
    qb, other = real / f"qb-lattice-test-{os.getpid()}-data", real / f"lattice-test-{os.getpid()}"
    try:
        qb.write_text("ipc")
        other.write_text("neu")
        log.unlink()
        out = subprocess.run(["/bin/sh", "-c", av.build_watch_command([str(real)], minutes=5)],
                             capture_output=True, text=True, env=env, timeout=60, check=False)
        listed = log.read_text().splitlines()[1:]
        assert str(other) in listed and str(qb) not in listed, out.stdout + out.stderr
    finally:
        qb.unlink(missing_ok=True)
        other.unlink(missing_ok=True)


# --- Waechter ueber clamdscan, wenn clamd laeuft --------------------------


def test_watch_command_uses_clamdscan_only_when_asked_for():
    """Aus (Standard): Zeichen fuer Zeichen der bisherige Befehl, kein clamdscan. An: clamdscan
    mit --fdpass/--no-summary/-i/--file-list hinter `clamdscan --ping 1`, clamscan als Rueckfall."""
    off = av.build_watch_command(av.WATCH_PATHS, minutes=12, mark=MARK)
    assert off == av.build_watch_command(av.WATCH_PATHS, minutes=12, use_clamd=False, mark=MARK)
    assert "clamdscan" not in off
    assert 'if [ -s "$L" ]; then clamscan -i --stdout --file-list="$L" 2>&1; R=$?; else' in off

    on = av.build_watch_command(av.WATCH_PATHS, minutes=12, use_clamd=True, mark=MARK)
    assert "clamdscan --ping 1" in on
    assert 'clamdscan --fdpass --no-summary -i --file-list="$L"' in on
    assert 'clamscan -i --stdout --file-list="$L" 2>&1; R=$?' in on  # der Rueckfall
    assert on.index("clamdscan --ping 1") < on.index("clamdscan --fdpass") < on.index("clamscan -i --stdout")
    # Suche, Ausschluesse und Rueckgabecode-Marke sind dieselben.
    assert on.split("; if [ -s")[0] == off.split("; if [ -s")[0]
    assert on.endswith(f'echo "{MARK}$R"') and off.endswith(f'echo "{MARK}$R"')
    assert match_deny_patterns(on) is None
    # Schnell- und Tiefenscan kennen clamdscan nie.
    for cmd in (av.build_scan_command(av.QUICK_PATHS), av.build_scan_command(av.DEEP_PATHS)):
        assert "clamdscan" not in cmd and "clamscan -r" in cmd


def _clamd_stubs(tmp_path: Path, *, ping_ok: bool, scan_rc: int = 0, scan_out: str = "") -> tuple[Path, Path]:
    """clamdscan- und clamscan-Attrappen, die jeden Aufruf mit Dateiliste mitschreiben."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    out_file = tmp_path / "clamdscan.out"
    out_file.write_text(scan_out, encoding="utf-8")  # als Datei: die Ausgabe darf Apostrophe enthalten
    (bin_dir / "clamdscan").write_text(
        "#!/bin/sh\n"
        f"case \"$1\" in --ping) echo \"clamdscan $*\" >> '{log}'; exit {0 if ping_ok else 1};; esac\n"
        f"echo \"clamdscan $*\" >> '{log}'\n"
        "for a in \"$@\"; do case \"$a\" in --file-list=*) cat \"${a#--file-list=}\" >> " f"'{log}';; esac; done\n"
        f"cat '{out_file}'\n"
        f"exit {scan_rc}\n"
    )
    (bin_dir / "clamscan").write_text(
        "#!/bin/sh\n"
        f"echo \"clamscan $*\" >> '{log}'\n"
        "for a in \"$@\"; do case \"$a\" in --file-list=*) cat \"${a#--file-list=}\" >> " f"'{log}';; esac; done\n"
        "echo 'Scanned files: 1'\n"
    )
    for name in ("clamdscan", "clamscan"):
        (bin_dir / name).chmod(0o755)
    return bin_dir, log


def _run_watch(tmp_path: Path, bin_dir: Path, *, use_clamd: bool, new_file: bool = True) -> tuple[str, av.ScanResult]:
    watched = tmp_path / "watched"
    watched.mkdir(exist_ok=True)
    if new_file:
        (watched / "neu.sh").write_text("echo hi\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    cmd = av.build_watch_command([str(watched)], minutes=5, use_clamd=use_clamd, mark=MARK)
    out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60, check=False)
    return out.stdout + out.stderr, av.parse_scan_output(out.stdout, MARK)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_uses_clamdscan_when_clamd_answers(tmp_path):
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=True)
    out, res = _run_watch(tmp_path, bin_dir, use_clamd=True)
    calls = log.read_text().splitlines()
    assert calls[0] == "clamdscan --ping 1"
    assert calls[1].startswith("clamdscan --fdpass --no-summary -i --file-list=")
    assert calls[2:] == [str(tmp_path / "watched" / "neu.sh")]  # clamdscan bekam genau die Dateiliste
    assert not any(c.startswith("clamscan") for c in calls)  # clamscan wurde nie gestartet
    # Dieselbe Auswertung wie bei clamscan: sauber, mit Dateizahl aus der Liste.
    assert (res.status, res.files_scanned, res.findings) == ("clean", 1, []), out
    assert f"{MARK}0" in out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_clamdscan_findings_are_parsed_like_clamscan(tmp_path):
    hit = f"{tmp_path}/watched/neu.sh: Win.Test.EICAR_HC-1 FOUND\n"
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=True, scan_rc=1, scan_out=hit)
    out, res = _run_watch(tmp_path, bin_dir, use_clamd=True)
    assert (res.status, res.findings) == ("infected", [(f"{tmp_path}/watched/neu.sh", "Win.Test.EICAR_HC-1")]), out
    assert res.files_scanned == 1 and f"{MARK}1" in out
    assert not any(c.startswith("clamscan") for c in log.read_text().splitlines())


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_falls_back_to_clamscan_when_clamd_does_not_answer(tmp_path):
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=False)
    out, res = _run_watch(tmp_path, bin_dir, use_clamd=True)
    calls = log.read_text().splitlines()
    assert calls[0] == "clamdscan --ping 1"
    assert calls[1].startswith("clamscan -i --stdout --file-list=")  # nur noch der Ping, dann clamscan
    assert not any(c.startswith("clamdscan --fdpass") for c in calls)
    assert calls[2:] == [str(tmp_path / "watched" / "neu.sh")]
    assert (res.status, res.files_scanned) == ("clean", 1), out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_falls_back_when_clamdscan_is_not_installed(tmp_path):
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=True)
    (bin_dir / "clamdscan").unlink()
    # Ohne clamdscan auf dem PATH (nur die Stub-clamscan und die Grundwerkzeuge).
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    if shutil.which("clamdscan", path=env["PATH"]):
        pytest.skip("auf diesem Rechner ist ein echtes clamdscan installiert")
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "neu.sh").write_text("echo hi\n")
    cmd = av.build_watch_command([str(watched)], minutes=5, use_clamd=True, mark=MARK)
    out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, env=env, timeout=60, check=False)
    res = av.parse_scan_output(out.stdout, MARK)
    assert (res.status, res.files_scanned) == ("clean", 1), out.stdout + out.stderr
    assert log.read_text().splitlines()[0].startswith("clamscan -i --stdout --file-list=")


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_a_failed_clamdscan_run_is_repeated_with_clamscan_and_never_reported_clean(tmp_path):
    """clamd antwortet auf den Ping, faellt aber waehrend des Scans aus (Rueckgabecode 2):
    dieser Lauf zaehlt nicht -- clamscan prueft dieselbe Liste noch einmal."""
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=True, scan_rc=2, scan_out="ERROR: Could not connect to clamd\n")
    out, res = _run_watch(tmp_path, bin_dir, use_clamd=True)
    calls = log.read_text().splitlines()
    assert [c.split()[0] for c in calls if c.startswith(("clamd", "clams"))] == ["clamdscan", "clamdscan", "clamscan"]
    assert "Could not connect" not in out  # die verworfene Ausgabe taucht nirgends auf
    assert (res.status, res.files_scanned, res.errors) == ("clean", 1, []), out
    assert f"{MARK}0" in out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_a_file_that_vanished_before_the_scan_does_not_force_the_slow_clamscan_run(tmp_path):
    """Echtes ClamAV 1.0.5 gibt schon Code 2 aus, wenn nur EINE Datei der Liste nicht mehr
    existiert ("ERROR: Can't access file ..."); die uebrigen wurden trotzdem geprueft. In /tmp
    und /dev/shm passiert das dauernd -- ein clamscan-Neustart wuerde die Einstellung sinnlos machen."""
    gone = "ERROR: Can't access file /tmp/gone.tmp\n"
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=True, scan_rc=2, scan_out=gone)
    out, res = _run_watch(tmp_path, bin_dir, use_clamd=True)
    calls = log.read_text().splitlines()
    assert [c.split()[0] for c in calls if c.startswith(("clamd", "clams"))] == ["clamdscan", "clamdscan"], calls
    assert (res.status, res.files_scanned) == ("clean", 1), out
    assert res.errors == ["ERROR: Can't access file /tmp/gone.tmp"]  # bleibt sichtbar, kein stiller Verlust
    assert f"{MARK}2" in out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_findings_next_to_a_vanished_file_are_kept_without_fallback(tmp_path):
    hit = f"{tmp_path}/watched/neu.sh: Win.Test.EICAR_HC-1 FOUND\nERROR: Can't access file /tmp/gone.tmp\n"
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=True, scan_rc=2, scan_out=hit)
    out, res = _run_watch(tmp_path, bin_dir, use_clamd=True)
    assert (res.status, res.findings) == ("infected", [(f"{tmp_path}/watched/neu.sh", "Win.Test.EICAR_HC-1")]), out
    assert not any(c.startswith("clamscan") for c in log.read_text().splitlines())


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("scan_out", [
    "",  # Code 2 ohne jede Erklaerung
    "ERROR: Could not connect to clamd on LocalSocket /run/clamav/clamd.ctl: No such file or directory\n",
    # eine verschwundene Datei UND ein echter Fehler: nicht schoenreden, noch einmal mit clamscan
    "ERROR: Can't access file /tmp/gone.tmp\nERROR: Could not connect to clamd\n",
    "ERROR: Can't access file /tmp/gone.tmp\nLibClamAV Error: cl_load(): out of memory\n",
])
def test_code_2_with_anything_but_vanished_files_still_falls_back_to_clamscan(tmp_path, scan_out):
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=True, scan_rc=2, scan_out=scan_out)
    out, res = _run_watch(tmp_path, bin_dir, use_clamd=True)
    calls = log.read_text().splitlines()
    assert [c.split()[0] for c in calls if c.startswith(("clamd", "clams"))] == ["clamdscan", "clamdscan", "clamscan"], calls
    assert "Could not connect" not in out and "gone.tmp" not in out  # die verworfene Ausgabe taucht nirgends auf
    assert (res.status, res.files_scanned) == ("clean", 1) and f"{MARK}0" in out, out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("scan_out", [
    # Fund, dann faellt clamd weg: clamdscan meldet Code 1 (Fund vor Fehler), die uebrigen Dateien sind ungeprueft
    "{p}/watched/neu.sh: Win.Test.EICAR_HC-1 FOUND\nERROR: Could not connect to clamd on LocalSocket /run/clamav/clamd.ctl\n",
    "{p}/watched/neu.sh: Win.Test.EICAR_HC-1 FOUND\n/tmp/b.sh: no reply from clamd\n",
    "ERROR: Could not connect to clamd\n",  # Code 1 ganz ohne Fund
    "",
])
def test_code_1_with_a_connection_error_falls_back_to_clamscan(tmp_path, scan_out):
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=True, scan_rc=1, scan_out=scan_out.format(p=tmp_path))
    out, res = _run_watch(tmp_path, bin_dir, use_clamd=True)
    calls = log.read_text().splitlines()
    assert [c.split()[0] for c in calls if c.startswith(("clamd", "clams"))] == ["clamdscan", "clamdscan", "clamscan"], calls
    assert "Could not connect" not in out and "no reply" not in out  # die verworfene Ausgabe taucht nirgends auf
    assert f"{MARK}0" in out


def test_clamdscan_setting_names_the_clamd_size_limits():
    """clamd prueft nur bis MaxFileSize aus clamd.conf -- groessere Dateien gelten dort als
    sauber, auch wenn "Maximale Dateigroesse" hoeher steht. Das muss in der Einstellung stehen."""
    import json

    schema = json.loads((Path(__file__).resolve().parents[2] / "extensions/nexus-soc/settings.schema.json").read_text(encoding="utf-8"))
    text = schema["properties"]["watch_use_clamdscan"]["description"]
    assert "clamd.conf" in text and "MaxFileSize" in text and "25 MB" in text


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_without_new_files_starts_neither_scanner(tmp_path):
    bin_dir, log = _clamd_stubs(tmp_path, ping_ok=True)
    out, res = _run_watch(tmp_path, bin_dir, use_clamd=True, new_file=False)
    assert not log.exists(), log.read_text()  # nicht einmal der Ping
    assert (res.status, res.files_scanned) == ("clean", 0) and f"{MARK}0" in out


# --- Ablauf mit Fake-Kontext: Scan -> Fund -> automatische Quarantaene -> Meldung -----

from contextlib import asynccontextmanager  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from nodvard_sdk import Host  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402


class _FakeCtx:
    def __init__(self, sessionmaker, outputs):
        self._sm = sessionmaker
        self.outputs = outputs
        self.commands: list[str] = []
        self.audits: list[dict] = []
        self.notes: list = []
        host = Host(id="h1", name="pi", display_name="Raspberry Pi", address="10.0.0.2")
        self.settings = SimpleNamespace(get=self._settings)
        self.hosts = SimpleNamespace(list=self._list, get=self._get)
        self.exec = SimpleNamespace(run=self._run)
        self.db = SimpleNamespace(session=self._session)
        self.audit = SimpleNamespace(log=self._audit)
        self.notify = SimpleNamespace(send=self._notify)
        self.ws = SimpleNamespace(broadcast=self._ws)
        self._host = host

    async def _settings(self):
        return {"auto_quarantine": True}

    async def _list(self, tag=None):
        return [self._host]

    async def _get(self, host_id):
        return self._host if host_id == "h1" else None

    async def _run(self, host, command, timeout_s=60):
        self.commands.append(command)
        # Der Defender zieht je Lauf eine eigene Marke: die Attrappe antwortet mit der aus dem Befehl.
        used = re.search(r"@@scan-rc-[0-9a-f]{16}=", command)
        for key, out in self.outputs.items():
            if key in command:
                if used:
                    out = out.replace(MARK, used.group(0))
                return SimpleNamespace(exit_code=0, stdout=out, stderr="", duration_ms=5)
        return SimpleNamespace(exit_code=0, stdout="", stderr="", duration_ms=5)

    @asynccontextmanager
    async def _session(self):
        async with self._sm() as s:
            yield s
            await s.commit()

    async def _audit(self, **kw):
        self.audits.append(kw)

    async def _notify(self, n):
        self.notes.append(n)

    async def _ws(self, *a, **k):
        return None


@pytest.mark.asyncio
async def test_scan_finding_is_quarantined_notified_and_audited():
    import asyncio

    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx = _FakeCtx(async_sessionmaker(engine, expire_on_commit=False), {
        "clamscan -r": CLAM_INFECTED,
        "mv -f": "@@mode=755\n",
    })
    defender = Defender(ctx)
    ids = await defender.start_scans(await defender.target_hosts(), "quick", paths=None, trigger="manual")
    assert len(ids) == 1
    for _ in range(50):
        await asyncio.sleep(0.02)
        if not defender.is_running("h1", "quick"):
            break

    scans = await defender.list_scans()
    assert (scans[0]["status"], scans[0]["infected"], scans[0]["files_scanned"]) == ("infected", 2, 3412)
    findings = await defender.list_findings()
    assert {f["status"] for f in findings} == {"quarantined"}
    assert all(f["quarantine_path"].startswith("/var/lib/nexus-quarantine/") for f in findings)
    assert sum("mv -f" in c for c in ctx.commands) == 2
    assert [a["action"] for a in ctx.audits].count("nexus_soc.quarantine") == 2
    assert "nexus_soc.malware_found" in [a["action"] for a in ctx.audits]
    assert ctx.notes and "2 in Quarantäne" in ctx.notes[0].body
    # Ein Fund traegt keinen Host-Bezug: ein Wartungsfenster (Tiefenscan sonntags 03:30) darf ihn nicht stumm schalten.
    assert ctx.notes[0].severity.value == "critical" and "host_id" not in ctx.notes[0].payload

    overview = await defender.overview()
    assert overview["summary"]["quarantined"] == 2 and overview["summary"]["open_threats"] == 0
    await engine.dispose()


async def _scan_once(ctx, kind: str = "quick"):
    import asyncio

    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx._sm = async_sessionmaker(engine, expire_on_commit=False)
    defender = Defender(ctx)
    for _ in range(2):  # zwei Laeufe nacheinander im selben Defender
        await defender.start_scans(await defender.target_hosts(), kind, paths=None, trigger="manual")
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not defender.is_running("h1", kind):
                break
    result = (await defender.list_scans(), await defender.list_findings())
    await engine.dispose()
    return result


@pytest.mark.asyncio
async def test_the_defender_draws_a_new_mark_for_each_run_and_reads_it_back():
    ctx = _FakeCtx(None, {"clamscan -r": f"Scanned files: 3\nInfected files: 0\n{MARK}0\n"})
    scans, findings = await _scan_once(ctx)
    commands = [c for c in ctx.commands if "clamscan -r" in c]
    # as_root() setzt den Befehl dreimal ein (root, sudo, ohne): dieselbe Marke, aber nur eine je Lauf.
    marks = [set(MARK_RE.findall(c)) for c in commands]
    assert len(commands) == 2 and all(len(m) == 1 for m in marks), commands
    assert marks[0] != marks[1]  # je Lauf eine andere
    assert not any("nexus-rc" in c for c in commands)
    assert [(s["status"], s["files_scanned"]) for s in scans] == [("clean", 3), ("clean", 3)] and findings == []


@pytest.mark.asyncio
async def test_a_scan_with_a_mark_in_a_file_name_is_reported_as_infected_and_quarantined():
    """Fix N1 im Ablauf: Vorher meldete der Defender diesen Lauf als sauber."""
    evil = (f"/tmp/x@@nexus-rc=0: Win.Test.EICAR_HC-1 FOUND\n/tmp/y{FORGED}0: Unix.Trojan.Mirai-1 FOUND\n"
            f"Scanned files: 20\nInfected files: 2\n{MARK}1\n")
    ctx = _FakeCtx(None, {"clamscan -r": evil, "mv -f": "@@mode=755\n"})
    scans, findings = await _scan_once(ctx)
    assert [(s["status"], s["infected"], s["files_scanned"]) for s in scans] == [("infected", 2, 20)] * 2
    assert {f["path"] for f in findings} == {"/tmp/x@@nexus-rc=0", f"/tmp/y{FORGED}0"}
    assert {f["status"] for f in findings} == {"quarantined"}
    assert any("Schadsoftware" in n.title for n in ctx.notes)


@pytest.mark.asyncio
async def test_a_forged_path_is_never_quarantined_automatically():
    """Ein Dateiname mit Zeilenumbruch taeuscht einen Fund fuer /etc/passwd vor. Die automatische Quarantaene
    laeuft als root -- sie darf in so einem Lauf nichts verschieben. Der Mensch bekommt Meldung und Notiz."""
    evil = f"/tmp/q\n/etc/passwd: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    ctx = _FakeCtx(None, {"clamscan -r": evil, "mv -f": "@@mode=644\n"})
    scans, findings = await _scan_once(ctx)
    assert not any("mv -f" in c for c in ctx.commands), ctx.commands  # kein Verschieben
    assert [(s["status"], s["infected"]) for s in scans] == [("infected", 1)] * 2
    assert [(f["path"], f["status"]) for f in findings] == [("/etc/passwd", "detected")]
    assert findings[0]["note"].startswith("Der Pfad ist nicht gesichert")
    assert len(ctx.notes) == 2 and ctx.notes[0].severity.value == "critical"
    assert "nicht eindeutig lesbar" in ctx.notes[0].body and "nichts automatisch verschoben" in ctx.notes[0].body
    assert "nicht eindeutig lesbar" in scans[0]["error"]  # auch in der Scan-Liste sichtbar
    assert "/etc/passwd" not in ctx.notes[0].body  # der vorgetaeuschte Pfad kommt nicht in die Push-Meldung
    assert next(a for a in ctx.audits if a["action"] == "nexus_soc.malware_found")["detail"]["unreliable"] is True


@pytest.mark.asyncio
async def test_a_finding_without_a_readable_line_still_raises_the_alarm():
    """ClamAV endet mit Code 1, aber keine Zeile ist lesbar (Name mit Zeilenumbruch): Vorher "sauber" und
    still. Jetzt ein Fund ohne Pfad, mit Meldung."""
    ctx = _FakeCtx(None, {"clamscan -r": f"/tmp/x\nyy: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", "mv -f": "@@mode=644\n"})
    scans, findings = await _scan_once(ctx)
    assert [(s["status"], s["infected"]) for s in scans] == [("infected", 1)] * 2 and findings == []
    assert len(ctx.notes) == 2 and "Schadsoftware" in ctx.notes[0].title
    assert not any("mv -f" in c for c in ctx.commands)


@pytest.mark.asyncio
async def test_a_control_character_in_a_name_is_quarantined_like_any_other_finding():
    """Wagenruecklauf im Namen ist eindeutig lesbar: normale automatische Quarantaene, wie bisher."""
    evil = f"/tmp/a\rb: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    ctx = _FakeCtx(None, {"clamscan -r": evil, "mv -f": "@@mode=644\n"})
    _scans, findings = await _scan_once(ctx)
    # Zwei Laeufe nacheinander: jeder findet die Datei neu (die erste liegt schon im Tresor) und verschiebt sie.
    assert [(f["path"], f["status"], f["note"]) for f in findings] == [("/tmp/a\rb", "quarantined", None)] * 2
    assert sum("mv -f" in c for c in ctx.commands) == 2 and "1 in Quarantäne" in ctx.notes[0].body


LYNIS_WARNING = "warning[]=SSH-7408|Consider hardening SSH configuration|-|-|\nhardening_index=68\n@@done"


@pytest.mark.asyncio
async def test_audit_notice_names_the_servers_it_covers_and_the_briefing_none():
    """Das Wartungsfenster des Kerns liest nur `payload.host_id` (ein Server)
    oder `payload.host_ids` (Sammelbericht: still nur, wenn alle im Fenster liegen). Das
    Haertungs-Audit nennt seine Server, das Morgen-Briefing nie."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import Base
    from nodvard_sdk import Host

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx = _FakeCtx(async_sessionmaker(engine, expire_on_commit=False), {"lynis audit": LYNIS_WARNING})
    defender = Defender(ctx)
    pi = ctx._host
    pve2 = Host(id="h2", name="pve2", display_name="pve2", address="10.0.0.3")

    await defender.run_audits([pi])
    await defender.run_audits([pi, pve2])
    assert [n.title for n in ctx.notes] == [
        "Härtungs-Audit: 1 Warnung(en), 0 Fehler", "Härtungs-Audit: 2 Warnung(en), 0 Fehler",
    ]
    assert ctx.notes[0].payload["host_id"] == "h1" and "host_ids" not in ctx.notes[0].payload
    assert ctx.notes[1].payload["host_ids"] == ["h1", "h2"] and "host_id" not in ctx.notes[1].payload

    ctx.notes.clear()
    await defender.send_briefing()
    assert ctx.notes and "host_id" not in ctx.notes[0].payload and "host_ids" not in ctx.notes[0].payload
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("setting", "clamd"), [({}, False), ({"watch_use_clamdscan": False}, False), ({"watch_use_clamdscan": True}, True)])
async def test_only_the_watch_reads_the_clamdscan_setting(setting, clamd):
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx = _FakeCtx(async_sessionmaker(engine, expire_on_commit=False), {})
    ctx._settings = lambda: _async_value({"auto_quarantine": True, **setting})
    ctx.settings = SimpleNamespace(get=ctx._settings)
    defender = Defender(ctx)
    host = ctx._host
    for kind in ("watch", "quick", "deep"):
        ids = await defender.start_scans([host], kind, paths=None, trigger="manual")
        assert len(ids) == 1
        await _wait_idle(defender, host.id, kind)
    watch_cmd, quick_cmd, deep_cmd = ctx.commands
    assert ("clamdscan" in watch_cmd) is clamd
    assert "clamdscan" not in quick_cmd and "clamdscan" not in deep_cmd  # Schnell-/Tiefenscan unveraendert
    assert "clamscan -r" in quick_cmd and "clamscan -r" in deep_cmd
    await engine.dispose()


async def _async_value(value):
    return value


async def _wait_idle(defender, host_id: str, kind: str) -> None:
    import asyncio

    for _ in range(100):
        await asyncio.sleep(0.02)
        if not defender.is_running(host_id, kind):
            return
    raise AssertionError("Scan wurde nicht fertig")


class _SlowCtx(_FakeCtx):
    """Scans bleiben haengen, bis `gate` gesetzt ist -- so laufen sie nachweislich noch."""

    def __init__(self, sessionmaker):
        import asyncio

        super().__init__(sessionmaker, {})
        self.gate = asyncio.Event()

    async def _run(self, host, command, timeout_s=60):
        if "clamscan" in command:
            await self.gate.wait()
        return await super()._run(host, command, timeout_s)


@pytest.mark.asyncio
async def test_never_two_scans_at_once_on_the_same_host(caplog):
    """Waechter und Schnellscan (02:00) bzw. Tiefenscan (So 03:30) liefen
    gleichzeitig -- zwei clamscan mit je ~1 GB RAM auf einem Server."""
    import asyncio
    import logging

    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx = _SlowCtx(async_sessionmaker(engine, expire_on_commit=False))
    defender = Defender(ctx)
    hosts = await defender.target_hosts()

    async def start(kind):
        return await defender.start_scans(hosts, kind, paths=["/srv"] if kind == "custom" else None, trigger="manual")

    async def finish():
        ctx.gate.set()
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not defender._running:
                break
        assert not defender._running
        ctx.gate.clear()

    # Grosser Scan laeuft: kein zweiter grosser, und der Waechter setzt still aus.
    assert len(await start("quick")) == 1
    with caplog.at_level(logging.DEBUG, logger="nodvard_deck.ext.nexus-soc"):
        assert await start("quick") == [] and await start("deep") == [] and await start("custom") == []
        assert await start("watch") == []
    assert "nexus_soc_watch_skipped_big_scan_running" in caplog.text
    assert [s["kind"] for s in await defender.list_scans(include_watch=True)] == ["quick"]  # kein Datensatz-Spam
    await finish()

    # Laufender (kurzer) Waechter haelt einen grossen Scan nicht auf -- aber danach
    # startet kein weiterer Waechter, solange der grosse laeuft.
    assert len(await start("watch")) == 1
    assert len(await start("deep")) == 1
    assert await start("watch") == [] and await start("quick") == []
    await finish()
    assert len(await start("watch")) == 1  # alles fertig: Waechter laeuft wieder
    await finish()
    await engine.dispose()


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell (Git-Bash-sh scheitert an Windows-Pfaden)")
def test_as_root_runs_directly_or_via_sudo_and_reports_missing_rights():
    wrapped = av.as_root("echo \"hallo $(id -u)\"; echo 'mit '\"'\"'quote'\"'\"")
    out = subprocess.run(["sh", "-c", wrapped], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "hallo" in out.stdout and "mit 'quote'" in out.stdout
    # Ohne root und ohne sudo: Pflicht-Befehle brechen mit Hinweis ab, Scans laufen weiter.
    fake_id = "id() { echo 1000; }; sudo() { return 1; }; "
    strict = subprocess.run(["sh", "-c", fake_id + av.as_root("echo geheim")], capture_output=True, text=True)
    assert av.NO_ROOT in strict.stdout and "geheim" not in strict.stdout and strict.returncode == 126
    lenient = subprocess.run(["sh", "-c", fake_id + av.as_root("echo trotzdem", required=False)], capture_output=True, text=True)
    assert lenient.stdout.strip() == "trotzdem"
    assert av.parse_lynis(av.NO_ROOT).error == av.NO_ROOT_MESSAGE


def test_unreadable_folders_do_not_turn_a_scan_red():
    out = f"ERROR: Can't open file or directory /root/.cache\nScanned files: 812\nInfected files: 0\n{MARK}2"
    r = av.parse_scan_output(out, MARK)
    assert (r.status, r.files_scanned) == ("clean", 812)
    assert r.errors == ["ERROR: Can't open file or directory /root/.cache"]


def test_protection_widget_summarises_hosts():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from nodvard_deck_ext_nexus_soc.defender_api import build_routers

    class _Def:
        async def overview(self):
            return {
                "summary": {"score": 86, "protected": 1, "hosts": 2, "quarantined": 0, "open_threats": 0},
                "hosts": [
                    {"host_name": "pve2", "clamav_installed": True, "clamav_version": "1.4.3",
                     "last_scan": {"status": "clean"}, "last_audit": {"hardening_index": 65}},
                    {"host_name": "Pi", "clamav_installed": False},
                ],
            }

    ctx = SimpleNamespace(api=SimpleNamespace(current_actor=lambda: None))
    read, _ = build_routers(ctx, _Def())
    app = FastAPI()
    app.include_router(read)
    rows = TestClient(app).get("/defender/widgets/protection").json()["data"]
    assert rows[0] == {"title": "Schutzwert 86 / 100", "subtitle": "1/2 Server geschützt · 0 in Quarantäne", "label": "keine Bedrohung", "tone": "good"}
    assert rows[1]["label"] == "sauber" and rows[1]["subtitle"] == "ClamAV 1.4.3 · Härtung 65"
    assert (rows[2]["label"], rows[2]["tone"]) == ("ohne Virenschutz", "warn")


def test_morgen_briefing_fasst_zusammen() -> None:
    from types import SimpleNamespace

    from nodvard_deck_ext_nexus_soc.defender import build_briefing

    overview = {
        "summary": {"score": 86, "protected": 4, "hosts": 5, "open_threats": 0, "quarantined": 1, "findings_30d": 2, "avg_hardening": 64},
        "hosts": [
            {"host_name": "pi-host", "reachable": True, "clamav_installed": True, "freshclam_active": False, "last_scan": None},
            {"host_name": "docker", "reachable": True, "clamav_installed": True, "freshclam_active": True, "last_scan": {"status": "error"}},
        ],
    }
    hosts = [SimpleNamespace(name="a", display_name=None, status=SimpleNamespace(value="up")),
             SimpleNamespace(name="valheim", display_name="Valheim", status=SimpleNamespace(value="down"))]
    title, body, level = build_briefing(overview, hosts)
    assert level == "warning" and "etwas zu tun" in title
    assert "1/2 erreichbar" in body and "Valheim" in body
    assert "pi-host: Signatur-Update aus" in body and "docker: letzter Scan fehlgeschlagen" in body
    assert "Härtung: Ø 64/100" in body

    overview["summary"]["open_threats"] = 1
    assert build_briefing(overview, hosts)[2] == "critical"


def test_long_fragments_with_many_colons_do_not_stall_the_parser():
    """Bruchstueck eines Dateinamens mit Zeilenumbruch: lange Zeile mit vielen ": ", endet nicht auf FOUND.
    Die Fund-Regex darf darauf nicht quadratisch zuruecksetzen (vorher rund eine Minute fuer 3000 solche Zeilen)."""
    import time

    fragment = "/tmp/" + ": a" * 1300
    body = "\n".join([fragment] * 3000) + f"\nScanned files: 1\nInfected files: 0\n{MARK}0"
    start = time.monotonic()
    result = av.parse_scan_output(body, MARK)
    assert time.monotonic() - start < 2.0
    # Ohne FOUND-Zeile und mit Code 0 sind die Bruchstuecke nur Rauschen, kein Fund.
    assert result.status == "clean" and result.findings == []
