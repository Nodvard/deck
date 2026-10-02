"""Virenschutz in nexus-soc: Befehle bauen und ClamAV-/Lynis-Ausgaben auswerten."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "nexus-soc" / "src"))

from nodvard_deck.core.deny_patterns import match_deny_patterns  # noqa: E402
from nodvard_deck_ext_nexus_soc import antivirus as av  # noqa: E402

# Eine feste Marke fuer die Tests, die Befehl und Auswertung selbst verbinden (im Betrieb zieht der
# Defender je Lauf eine neue: `av.new_rc_mark()`).
MARK = "@@scan-rc-0123456789abcdef="

CLAM_INFECTED = f"""/tmp/eicar.com: Win.Test.EICAR_HC-1 FOUND
/home/user/x.sh: Unix.Trojan.Mirai-123 FOUND

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
    assert r.findings == [("/tmp/eicar.com", "Win.Test.EICAR_HC-1"), ("/home/user/x.sh", "Unix.Trojan.Mirai-123")]


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


# --- Wurzeln: Funde und Fehlermeldungen muessen zu dem passen, was der Befehl gescannt hat ----------------
# Im clamd-Modus gibt es keine Zeile `Infected files:`: eine gefaelschte absolute Fund-Zeile liesse sich dort sonst
# nicht von einer echten unterscheiden.

def test_a_finding_outside_the_scanned_roots_is_never_reliable():
    out = f"/etc/passwd: {EICAR} FOUND\nScanned files: 5\n{MARK}1\n"  # wie im clamd-Modus: ohne Zusammenfassung
    # Ohne Wurzeln (Standard) ist die Auswertung wie bisher.
    assert av.parse_scan_output(out, MARK).unreliable is False
    assert av.parse_scan_output(out, MARK, roots=None).unreliable is False
    # Mit Wurzeln: ausserhalb ist nie eindeutig, innerhalb schon -- der Fund bleibt in beiden Faellen sichtbar.
    outside = av.parse_scan_output(out, MARK, roots=["/tmp", "/home"])
    assert (outside.status, outside.findings, outside.unreliable) == ("infected", [("/etc/passwd", EICAR)], True)
    inside = av.parse_scan_output(f"/home/user/x.sh: {EICAR} FOUND\nScanned files: 5\n{MARK}1\n", MARK, roots=["/tmp", "/home"])
    assert (inside.status, inside.unreliable) == ("infected", False)


@pytest.mark.parametrize(("path", "roots", "reliable"), [
    ("/tmp/a", ["/tmp"], True),
    ("/tmp", ["/tmp"], True),
    ("/tmp/x/../y", ["/tmp"], True),  # bleibt innerhalb
    ("/tmp/../etc/passwd", ["/tmp"], False),  # sieht nach /tmp aus, ist /etc/passwd
    ("/tmp/a/../../etc/passwd", ["/tmp"], False),
    ("/tmpfoo/a", ["/tmp"], False),  # gleicher Anfang, anderer Ordner
    ("//etc/passwd", ["/tmp"], False),
    ("//tmp/a", ["/tmp"], True),
    ("/tmp/a", ["/tmp/"], True),  # Wurzel mit Schraegstrich am Ende
    ("/tmp/a", ["/"], True),  # Tiefenscan: alles liegt darunter
    ("/etc/passwd", ["/"], True),
    ("/home/a", ["/tmp", "/home"], True),
    ("/opt/a", ["/tmp", "/home"], False),
    ("tmp/a", ["/tmp"], False),  # nicht absolut
    ("/tmp/a", ["tmp"], False),  # unbrauchbare Wurzel: nie ein Freibrief
    ("/tmp/a", [], False),
])
def test_roots_are_compared_by_path_not_by_text(path, roots, reliable):
    res = av.parse_scan_output(f"{path}: {EICAR} FOUND\nScanned files: 5\n{MARK}1\n", MARK, roots=roots)
    if path.startswith("/"):
        assert (res.status, res.unreliable) == ("infected", not reliable)
    else:  # kein lesbarer Fund (Pfad nicht absolut): Code 1 sagt trotzdem "Fund", nie "sauber"
        assert (res.status, res.findings, res.unreliable) == ("infected", [], True)


def test_findings_in_a_run_with_roots_are_still_reliable_when_everything_fits():
    res = av.parse_scan_output(CLAM_INFECTED, MARK, roots=av.QUICK_PATHS)
    assert (res.status, res.infected, res.unreliable) == ("infected", 2, False)
    # Eine Meldung ueber eine verschwundene Datei innerhalb der Wurzeln aendert daran nichts.
    noisy = f"ERROR: Can't access file /tmp/gone\n/tmp/a: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    assert av.parse_scan_output(noisy, MARK, roots=["/tmp"]).unreliable is False


def test_random_names_with_roots_never_pass_a_forged_path_as_reliable():
    """Wie `test_random_file_names_never_hide_or_forge_a_finding`, aber ohne Zusammenfassung (clamd-Modus) und mit
    Wurzel: Ist ein Lauf nicht `unreliable`, liegt jeder gelesene Fund wirklich unter /tmp."""
    import posixpath
    import random

    pieces = ["a", "b", "/", "/etc/passwd", "\n", "\n", "..", "/..", "/tmp/", ": ", ": Sig FOUND", "\r", ".", "/home/x"]
    rng = random.Random(20261001)
    reliable = unreliable = 0
    for _ in range(4000):
        names = ["/tmp/" + "".join(rng.choice(pieces) for _ in range(rng.randint(1, 5))) for _ in range(rng.randint(1, 3))]
        out = "".join(f"{n}: Test.Sig FOUND\n" for n in names) + f"Scanned files: 5\n{MARK}1\n"
        res = av.parse_scan_output(out, MARK, roots=["/tmp"])
        assert res.status == "infected"
        if res.unreliable:
            unreliable += 1
        else:
            reliable += 1
            for path, _sig in res.findings:
                norm = posixpath.normpath(path)
                assert norm == "/tmp" or norm.startswith("/tmp/"), (names, path)
    assert reliable > 100 and unreliable > 100


@pytest.mark.parametrize("error", [
    "ERROR: Can't access file name",  # Bruchstueck eines Namens mit Zeilenumbruch: nicht absolut
    "ERROR: Can't access file /etc/passwd",  # ... oder ein Pfad ausserhalb der gescannten Ordner
    "ERROR: Can't access file /tmp/../etc/passwd",
    "/etc/passwd: No such file or directory. ERROR",
    "/tmp/../etc/passwd: Permission denied. ERROR",
])
def test_code_2_with_a_path_this_run_never_scanned_is_not_clean(error):
    """Kein bedingungsloses "sauber" bei Code 2: Meldet ClamAV eine nicht lesbare Datei, die gar nicht zu den gescannten Ordnern gehoert,
    ist das ein Dateiname, der die Liste zerrissen hat -- dahinter steckt eine Datei, die nie geprueft wurde."""
    out = f"{error}\nScanned files: 3\nInfected files: 0\n{MARK}2\n"
    assert av.parse_scan_output(out, MARK).status == "clean"  # ohne Wurzeln wie bisher
    res = av.parse_scan_output(out, MARK, roots=["/tmp"])
    assert (res.status, res.error) == ("error", av.AMBIGUOUS_OUTPUT_MESSAGE)


@pytest.mark.parametrize("error", [
    "ERROR: Can't access file /tmp/gone.tmp",  # in /tmp Alltag: Die Datei war zwischen find und Scan weg
    "ERROR: Can't open file /tmp/secret: Permission denied",
    "/tmp/gone.tmp: No such file or directory. ERROR",
    "/tmp/x: Permission denied. ERROR",
    "ERROR: Can't open directory /tmp/private: Permission denied",
    "LibClamAV Error: cl_load(): out of memory",  # kein Pfad, nichts zu vergleichen
])
def test_code_2_with_unreadable_files_inside_the_roots_stays_clean(error):
    res = av.parse_scan_output(f"{error}\nScanned files: 3\nInfected files: 0\n{MARK}2\n", MARK, roots=["/tmp"])
    assert (res.status, res.files_scanned, res.error) == ("clean", 3, None)


def test_a_foreign_error_path_makes_an_infected_run_unreliable_too():
    out = f"ERROR: Can't access file name\n/tmp/a: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    assert av.parse_scan_output(out, MARK).unreliable is False
    assert av.parse_scan_output(out, MARK, roots=["/tmp"]).unreliable is True


# --- Zeile "Unusual file names: N" (Waechter hat Dateien mit Zeilenumbruch im Namen separat geprueft) ----

def test_unusual_file_names_count_as_checked_and_make_a_finding_unreliable():
    clean = av.parse_scan_output(f"Scanned files: 3\nInfected files: 0\nUnusual file names: 2\n{MARK}0\n", MARK)
    assert (clean.status, clean.files_scanned) == ("clean", 5)
    only = av.parse_scan_output(f"Scanned files: 0\nUnusual file names: 1\n{MARK}0\n", MARK)
    assert (only.status, only.files_scanned) == ("clean", 1)
    # Ein Fund ist dann nie eindeutig lesbar, auch wenn die Zeile fuer sich lesbar ist ...
    hit = av.parse_scan_output(f"/tmp/a\rb: {EICAR} FOUND\nScanned files: 1\nInfected files: 0\nUnusual file names: 1\n{MARK}1\n", MARK)
    assert (hit.status, hit.findings, hit.unreliable) == ("infected", [("/tmp/a\rb", EICAR)], True)
    # ... und ohne die Zeile (kein Sonderlauf) bleibt derselbe Fund eindeutig.
    plain = av.parse_scan_output(f"/tmp/a\rb: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK)
    assert plain.unreliable is False


def test_a_name_cannot_hide_the_unusual_file_names_line_or_forge_a_clean_run():
    """Der Name kann die Zeile nur dazuerfinden (mehr Vorsicht), nie verstecken: die echte steht als Letztes vor der Marke."""
    out = f"/tmp/x\nUnusual file names: 0\n/tmp/y: {EICAR} FOUND\n{SUMMARY_ONE}Unusual file names: 1\n{MARK}1\n"
    assert av.parse_scan_output(out, MARK).unreliable is True
    forged = f"/tmp/x\nUnusual file names: 7\nScanned files: 3\nInfected files: 0\n{MARK}0\n"
    assert av.parse_scan_output(forged, MARK).status == "clean"  # eine Zeile ohne Fund macht keinen Alarm


def test_a_broken_clamav_run_is_never_clean_just_because_a_file_with_an_odd_name_was_counted():
    """Die Zahl hinter `Unusual file names:` stammt von der Shell und macht aus einem Lauf ohne Zusammenfassung keinen
    Lauf ueber eine Datei: Datenbankfehler plus Code 2 bleibt ein Fehler, mit und ohne Sonderdatei."""
    error = "ERROR: Malformed database"
    plain = av.parse_scan_output(f"{error}\n{MARK}2\n", MARK, roots=["/tmp"])
    odd = av.parse_scan_output(f"{error}\nUnusual file names: 1\n{MARK}2\n", MARK, roots=["/tmp"])
    for res in (plain, odd):
        assert res.status == "error" and "Malformed database" in res.error, res
    # Nur Sonderdateien im Fenster: die Shell schreibt `Scanned files: 0`, der Hauptlauf lief gar nicht.
    only_odd = av.parse_scan_output(
        f"{error}\nScanned files: 0\nUnusual file names: 1\n{MARK}2\n", MARK, roots=["/tmp"])
    assert only_odd.status == "error" and "Malformed database" in only_odd.error
    # Der Hauptlauf war in Ordnung (Zusammenfassung da), der Fehler kam aus dem Lauf der Sonderdatei.
    mixed = av.parse_scan_output(
        f"{error}\nScanned files: 3\nInfected files: 0\nUnusual file names: 1\n{MARK}2\n", MARK, roots=["/tmp"])
    assert mixed.status == "error" and "Malformed database" in mixed.error
    # Nicht lesbare Dateien allein bleiben harmlos, auch zusammen mit einer Sonderdatei.
    unreadable = av.parse_scan_output(
        f"ERROR: Can't access file /tmp/gone\nScanned files: 3\nInfected files: 0\nUnusual file names: 1\n{MARK}2\n",
        MARK, roots=["/tmp"])
    assert (unreadable.status, unreadable.files_scanned) == ("clean", 4)


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


# `chmod` trifft den Inode, nicht den Namen: Ein Hardlink auf eine Systemdatei, im Rennen zwischen Pruefung und `mv`
# an den Namen der Fundstelle gehaengt, landet beim `mv` (gleiches Dateisystem) als weiterer Name derselben Datei im
# Tresor -- und `chmod 000` machte die Systemdatei unlesbar.
@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_quarantine_catches_a_file_swapped_for_a_hardlink_right_before_mv(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "QUARANTINE_DIR", str(tmp_path / "q"))
    system_file = tmp_path / "shadow"
    system_file.write_text("root:x:1\n")
    system_file.chmod(0o640)
    victim = tmp_path / "evil.sh"
    victim.write_text("echo boom")
    # Ein mv, das vorher (wie ein Angreifer im richtigen Moment) den Namen gegen einen Hardlink auf die Systemdatei tauscht.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "mv").write_text(f'#!/bin/sh\nrm -f "$2"; ln "$VICTIM" "$2"\nexec {shutil.which("mv")} "$@"\n')
    (bindir / "mv").chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "VICTIM": str(system_file)}
    out = subprocess.run(["sh", "-c", av.quarantine_command(str(victim), "f1_evil.sh")], capture_output=True, text=True,
                         env=env, check=False)
    assert out.returncode == 5, out.stdout + out.stderr
    assert av.HARDLINK_SWAPPED in out.stdout and "@@mode" not in out.stdout
    assert (system_file.stat().st_mode & 0o777) == 0o640 and system_file.read_text() == "root:x:1\n"  # nichts angefasst
    assert system_file.stat().st_nlink == 1  # der Name im Tresor ist wieder weg
    assert not os.path.lexists(tmp_path / "q" / "f1_evil.sh")


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_quarantine_refuses_a_file_that_already_has_a_second_name(tmp_path, monkeypatch):
    """Der harmlose Fall (zweiter Name schon vor der Quarantaene): Es wird nichts verschoben, nichts entfernt,
    nichts umgestellt -- nur die Meldung, dass die Datei weitere Namen hat."""
    monkeypatch.setattr(av, "QUARANTINE_DIR", str(tmp_path / "q"))
    system_file = tmp_path / "shadow"
    system_file.write_text("root:x:1\n")
    system_file.chmod(0o640)
    victim = tmp_path / "evil.sh"
    os.link(system_file, victim)
    out = subprocess.run(["sh", "-c", av.quarantine_command(str(victim), "f1_evil.sh")], capture_output=True, text=True, check=False)
    assert out.returncode == 5, out.stdout + out.stderr
    assert av.HARDLINK_REFUSED in out.stdout
    assert victim.exists() and system_file.exists() and system_file.stat().st_nlink == 2
    assert (system_file.stat().st_mode & 0o777) == 0o640
    assert not os.path.lexists(tmp_path / "q" / "f1_evil.sh")


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_quarantine_still_moves_a_file_with_a_single_name(tmp_path, monkeypatch):
    """Der Gegenpart: Eine normale Datei (ein Name) wird wie bisher verschoben und gesperrt, die Rechte gemerkt."""
    monkeypatch.setattr(av, "QUARANTINE_DIR", str(tmp_path / "q"))
    victim = tmp_path / "evil.sh"
    victim.write_text("echo boom")
    victim.chmod(0o751)
    out = subprocess.run(["sh", "-c", av.quarantine_command(str(victim), "f1_evil.sh")], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    assert av.parse_mode(out.stdout) == "751" and not victim.exists()
    assert ((tmp_path / "q" / "f1_evil.sh").stat().st_mode & 0o777) == 0


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


def _restore_setup(tmp_path, name="evil.sh"):
    qdir = tmp_path / "q"
    qdir.mkdir()
    qfile = qdir / f"f1_{name}"
    qfile.write_text("echo boom")
    qfile.chmod(0)
    home = tmp_path / "home"
    home.mkdir()
    return qfile, home


def _assert_still_quarantined(qfile):
    """Die Datei liegt unveraendert und mit Modus 000 im Tresor. Lesen geht erst nach `chmod`: Als normaler
    Benutzer (CI) scheitert `read_text()` auf einer Datei mit Modus 000, nur root darf sie trotzdem lesen."""
    assert (qfile.stat().st_mode & 0o777) == 0
    qfile.chmod(0o600)
    assert qfile.read_text() == "echo boom"


def _shim_bin(tmp_path, tool, body):
    """Ein eigenes Werkzeug vor dem echten im PATH: `body` laeuft in der Shell, das echte steht in $REAL."""
    real = shutil.which(tool)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / tool
    script.write_text(f'#!/bin/sh\nREAL={real}\n{body}\n')
    script.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def _run_restore(qfile, target, env=None, mode="755"):
    return subprocess.run(["sh", "-c", av.restore_command(str(qfile), str(target), mode)],
                          capture_output=True, text=True, check=False, env=env)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_restore_never_follows_a_link_planted_between_check_and_move(tmp_path):
    """Der Benutzer legt `./name` als Verknuepfung auf einen Ordner an, nachdem die Shell geprueft hat
    (hier: im chmod davor). Ohne `mv -T` laege die Datei als root verschoben im fremden Ordner."""
    qfile, home = _restore_setup(tmp_path)
    profile_d = tmp_path / "etc" / "profile.d"
    profile_d.mkdir(parents=True)
    env = _shim_bin(tmp_path, "chmod", f'"$REAL" "$@"; ln -sn {profile_d} {home}/evil.sh 2>/dev/null; true')
    out = _run_restore(qfile, home / "evil.sh", env)
    assert out.returncode == 4, out.stdout + out.stderr
    assert "nichts überschrieben" in out.stdout
    assert list(profile_d.iterdir()) == []
    assert (home / "evil.sh").is_symlink() and [p.name for p in home.iterdir()] == ["evil.sh"]
    _assert_still_quarantined(qfile)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("plant", ["ln -s /nonexistent/ziel {home}/evil.sh", "echo fremd > {home}/evil.sh"])
def test_restore_does_not_overwrite_what_appeared_in_the_gap(tmp_path, plant):
    qfile, home = _restore_setup(tmp_path)
    env = _shim_bin(tmp_path, "chmod", f'"$REAL" "$@"; {plant.format(home=home)} 2>/dev/null; true')
    out = _run_restore(qfile, home / "evil.sh", env)
    assert out.returncode == 4, out.stdout + out.stderr
    assert "nichts überschrieben" in out.stdout
    assert os.path.lexists(home / "evil.sh")
    if (home / "evil.sh").is_file():
        assert (home / "evil.sh").read_text() == "fremd\n"
    _assert_still_quarantined(qfile)
    assert [p.name for p in home.iterdir()] == ["evil.sh"]


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("fake_mv", ["exit 0", "echo 'mv: unrecognized option' >&2; exit 1"])
def test_restore_reports_failure_when_mv_moved_nothing(tmp_path, fake_mv):
    """Aeltere mv und BusyBox melden bei `-n` Erfolg, ohne zu verschieben, oder kennen die Optionen nicht."""
    qfile, home = _restore_setup(tmp_path)
    env = _shim_bin(tmp_path, "mv", fake_mv)
    out = _run_restore(qfile, home / "evil.sh", env)
    assert out.returncode == 4 and "ok" not in out.stdout.split()
    assert qfile.exists() and (qfile.stat().st_mode & 0o777) == 0
    assert list(home.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_restore_does_not_overwrite_an_existing_file(tmp_path):
    qfile, home = _restore_setup(tmp_path)
    (home / "evil.sh").write_text("meins")
    out = _run_restore(qfile, home / "evil.sh")
    assert out.returncode == 4, out.stdout + out.stderr
    assert (home / "evil.sh").read_text() == "meins" and qfile.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_restore_does_not_create_folders_behind_a_linked_parent(tmp_path):
    """`mkdir -p` folgte der Verknuepfung und legte als root Ordner an fremden Stellen an."""
    qfile, home = _restore_setup(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (home / "d").symlink_to(elsewhere)
    out = _run_restore(qfile, home / "d" / "neu" / "evil.sh")
    assert out.returncode == 3, out.stdout + out.stderr
    assert list(elsewhere.iterdir()) == [] and qfile.exists()
    # Auch ohne Verknuepfung wird ein fehlender Ursprungsordner nicht angelegt.
    out = _run_restore(qfile, home / "fehlt" / "evil.sh")
    assert out.returncode == 3 and "Ursprungsordner" in out.stdout
    assert not (home / "fehlt").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_restore_refuses_a_linked_parent_with_an_existing_subfolder(tmp_path, shell):
    """Der Verknuepfung `home/d` folgt `cd -P` ohne Fehler, wenn dahinter schon ein Unterordner liegt
    (`elsewhere/sub`). Nur der Vergleich mit `pwd -P` faengt das ab: nichts angelegt, nichts verschoben."""
    qfile, home = _restore_setup(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "sub").mkdir(parents=True)
    (home / "d").symlink_to(elsewhere)
    target = home / "d" / "sub" / "evil.sh"
    out = subprocess.run([shell, "-c", av.restore_command(str(qfile), str(target), "755")],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 5, out.stdout + out.stderr
    assert "Verknüpfung" in out.stdout
    assert list((elsewhere / "sub").iterdir()) == [] and [p.name for p in elsewhere.iterdir()] == ["sub"]
    assert [p.name for p in home.iterdir()] == ["d"]
    _assert_still_quarantined(qfile)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_restore_sets_the_mode_and_keeps_the_content(tmp_path):
    qfile, home = _restore_setup(tmp_path)
    out = _run_restore(qfile, home / "evil.sh", mode="640")
    assert out.returncode == 0 and out.stdout.split() == ["ok"], out.stdout + out.stderr
    assert (home / "evil.sh").read_text() == "echo boom" and ((home / "evil.sh").stat().st_mode & 0o777) == 0o640
    assert not qfile.exists() and [p.name for p in home.iterdir()] == ["evil.sh"]


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("name", [
    "mit leerzeichen.sh",
    "zeilen\numbruch.sh",
    "ende mit umbruch\n",
    "-rf",
    "--help",
    "-n evil.sh",
    "'; touch pwned #.sh",
    "$(touch pwned).sh",
    "`touch pwned`",
    "a*b?[c].sh",
])
def test_quarantine_and_restore_keep_odd_file_and_folder_names_intact(tmp_path, monkeypatch, name):
    """Leerzeichen, Zeilenumbrueche, ein fuehrender Bindestrich und Shell-Zeichen stehen in Datei- und Ordnername
    nur als Text im Befehl: weder Quarantaene noch Wiederherstellen fuehren sie aus oder halten sie fuer Optionen."""
    monkeypatch.setattr(av, "QUARANTINE_DIR", str(tmp_path / "q"))
    folder = tmp_path / "ordner mit -x und\numbruch"
    folder.mkdir()
    victim = folder / name
    victim.write_text("echo boom")
    victim.chmod(0o750)
    bystander = folder / "daneben.txt"
    bystander.write_text("bleibt")
    for shell in ("sh", "bash"):
        qname = av.quarantine_name("f1", str(victim))
        out = subprocess.run([shell, "-c", av.quarantine_command(str(victim), qname)],
                             capture_output=True, text=True, check=False, cwd=tmp_path)
        assert out.returncode == 0, out.stdout + out.stderr
        assert av.parse_mode(out.stdout) == "750"
        assert not os.path.lexists(victim) and bystander.read_text() == "bleibt"
        qfile = tmp_path / "q" / qname
        back = subprocess.run([shell, "-c", av.restore_command(str(qfile), str(victim), "750")],
                              capture_output=True, text=True, check=False, cwd=tmp_path)
        assert back.returncode == 0 and back.stdout.split() == ["ok"], back.stdout + back.stderr
        assert victim.read_text() == "echo boom" and (victim.stat().st_mode & 0o777) == 0o750
        assert not qfile.exists()
        assert sorted(p.name for p in folder.iterdir()) == sorted([name, "daneben.txt"])
    assert not (tmp_path / "pwned").exists() and not (folder / "pwned").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("swap, left", [
    # Zwischenordner umbenennen und eine Verknuepfung auf einen anderen Ordner an seine Stelle legen
    # (die Verknuepfung gehoert dem Benutzer und bleibt, rmdir loest keine Verknuepfung auf)
    ('mv "$D" "$D.weg"; ln -s {elsewhere} "$D"', 2),
    # einen eigenen Ordner an seine Stelle legen (gehoert nicht root bzw. hat andere Rechte):
    # der leere Ordner wird wieder entfernt, nur das umbenannte Original bleibt
    ('mv "$D" "$D.weg"; mkdir -m 755 "$D"', 1),
])
def test_restore_refuses_a_swapped_stage_folder(tmp_path, swap, left):
    qfile, home = _restore_setup(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env = _shim_bin(tmp_path, "mktemp", f'D=$("$REAL" "$@") || exit 1; {swap.format(elsewhere=elsewhere)}; echo "$D"')
    out = _run_restore(qfile, home / "evil.sh", env)
    assert out.returncode == 5 and "Zwischenordner" in out.stdout, out.stdout + out.stderr
    assert list(elsewhere.iterdir()) == [] and not os.path.lexists(home / "evil.sh")
    assert len(list(home.iterdir())) == left, sorted(p.name for p in home.iterdir())
    _assert_still_quarantined(qfile)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("chmod_to", ["755", "775", "2770", "1700"])
def test_restore_removes_the_stage_folder_when_its_check_fails(tmp_path, chmod_to):
    """Scheitert die Pruefung des Zwischenordners (hier: andere Rechte als 700 bzw. 2700), darf im
    Ursprungsordner kein leerer `.nodvard-wiederherstellen.*` zurueckbleiben, auch nicht bei jedem Versuch ein neuer."""
    qfile, home = _restore_setup(tmp_path)
    env = _shim_bin(tmp_path, "mktemp", f'D=$("$REAL" "$@") || exit 1; chmod {chmod_to} "$D"; echo "$D"')
    for _ in range(2):
        out = _run_restore(qfile, home / "evil.sh", env)
        assert out.returncode == 5 and "Zwischenordner" in out.stdout, out.stdout + out.stderr
        assert list(home.iterdir()) == []
    _assert_still_quarantined(qfile)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("shell", ["sh", "bash"])
def test_restore_works_in_a_setgid_folder(tmp_path, shell):
    """In einem Ordner mit gesetztem Gruppen-Bit (setgid, z. B. Gruppen-Freigaben) erbt auch `mktemp -d` das Bit:
    `stat` meldet dann 2700 statt 700. Das ist derselbe Ordner nur fuer root und muss durchgehen."""
    qfile, home = _restore_setup(tmp_path)
    home.chmod(0o2755)
    probe = home / "probe"
    probe.mkdir(mode=0o700)
    inherited = bool(probe.stat().st_mode & 0o2000)
    probe.rmdir()
    if not inherited:
        pytest.skip("dieses Dateisystem vererbt das Gruppen-Bit nicht")
    out = subprocess.run([shell, "-c", av.restore_command(str(qfile), str(home / "evil.sh"), "750")],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0 and out.stdout.split() == ["ok"], out.stdout + out.stderr
    assert (home / "evil.sh").read_text() == "echo boom" and ((home / "evil.sh").stat().st_mode & 0o777) == 0o750
    assert not qfile.exists() and [p.name for p in home.iterdir()] == ["evil.sh"]


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_restore_works_across_file_systems(tmp_path):
    """Liegt die Quarantaene auf einem anderen Dateisystem, ist `mv` ein Kopieren und Loeschen."""
    shm = Path("/dev/shm")
    if not shm.is_dir() or not os.access(shm, os.W_OK) or shm.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("kein zweites Dateisystem zur Hand")
    qdir = Path(tempfile.mkdtemp(dir=shm))
    try:
        qfile = qdir / "f1_evil.sh"
        qfile.write_text("echo boom")
        qfile.chmod(0)
        home = tmp_path / "home"
        home.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        (home / "link.sh").symlink_to(other)
        out = _run_restore(qfile, home / "evil.sh", mode="750")
        assert out.returncode == 0, out.stdout + out.stderr
        assert (home / "evil.sh").read_text() == "echo boom" and ((home / "evil.sh").stat().st_mode & 0o777) == 0o750
        assert not qfile.exists()
        # Eine Verknuepfung am Ziel wird auch beim Kopieren nicht durchlaufen.
        qfile.write_text("echo zwei")
        qfile.chmod(0)
        out = _run_restore(qfile, home / "link.sh")
        assert out.returncode == 4 and list(other.iterdir()) == [] and qfile.exists()
    finally:
        shutil.rmtree(qdir, ignore_errors=True)


_UNSAFE_MV = """#!{python}
# Ein mv wie BusyBox oder uutils vor 0.10: Zwischen Dateisystemen wird ueber den Pfad kopiert (folgt einer
# Verknuepfung, Rechte per chmod auf den Pfad). Als anderes Dateisystem gilt hier alles zwischen {qdir} und dem Rest.
# Beim ersten Kopieren legt "der Benutzer" genau in der Luecke nach der Pruefung {plant} an.
import os, sys
args = [a for a in sys.argv[1:] if not a.startswith("-")]
src, dst = args
if os.path.lexists(dst):
    sys.exit(0)  # -n: nichts tun, trotzdem Erfolg
if src.startswith({qdir!r}) == os.path.abspath(dst).startswith({qdir!r}):
    os.rename(src, dst)
    sys.exit(0)
if not os.path.exists({marker!r}):
    open({marker!r}, "w").close()
    os.symlink({link_to!r}, {plant!r})
mode = os.stat(src).st_mode & 0o7777
with open(src, "rb") as r, open(dst, "wb") as w:
    w.write(r.read())
os.chmod(dst, mode)
os.unlink(src)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_restore_is_safe_with_an_mv_that_copies_through_links(tmp_path):
    """Liegt die Quarantaene auf einem anderen Dateisystem, kopiert `mv`. BusyBox und uutils (Ubuntu 26.04 ohne
    Updates) legen dabei ueber den Pfad an bzw. setzen die Rechte ueber den Pfad: Legt der Benutzer in seinem Ordner
    `./name` als Verknuepfung an, schrieb root den Inhalt ins Linkziel. Kopiert wird deshalb nur in einen Ordner,
    der allein root gehoert."""
    qfile, home = _restore_setup(tmp_path)
    profile_d = tmp_path / "etc" / "profile.d"
    profile_d.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "mv"
    shim.write_text(_UNSAFE_MV.format(python=sys.executable, qdir=str(qfile.parent) + "/", plant=str(home / "evil.sh"),
                                      link_to=str(profile_d / "evil.sh"), marker=str(tmp_path / "geplant")))
    shim.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    out = _run_restore(qfile, home / "evil.sh", env, mode="666")
    assert (tmp_path / "geplant").exists()
    assert list(profile_d.iterdir()) == []
    assert out.returncode == 4, out.stdout + out.stderr
    assert (home / "evil.sh").is_symlink() and sorted(p.name for p in home.iterdir()) == ["evil.sh"]
    _assert_still_quarantined(qfile)


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
        'for a in "$@"; do case "$a" in --file-list=*) list="${a#--file-list=}";; --no-summary) nosum=1;; esac; last="$a"; done\n'
        'if [ -n "$list" ]; then\n'
        '  while IFS= read -r f; do printf \'%s: Win.Test.EICAR_HC-1 FOUND\\n\' "$f"; done < "$list"\n'
        '  n=$(wc -l < "$list" | tr -d \' \')\n'
        "else\n"
        "  find \"$last\" -type f -exec sh -c 'printf \"%s: Win.Test.EICAR_HC-1 FOUND\\n\" \"$1\"' _ {} \\;\n"
        "  n=$(find \"$last\" -type f -exec printf x \\; | wc -c)\n"
        "fi\n"
        '[ -n "$nosum" ] || { echo; echo "----------- SCAN SUMMARY -----------"; echo "Scanned files: $n"; echo "Infected files: $n"; }\n'
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
    # Wagenruecklauf im Namen: ein Scan liest die Fund-Zeile eindeutig. Der Waechter gibt solche Dateien aber nicht in
    # die Liste (ClamAV schneidet dort ein CR am Zeilenende ab), sondern prueft sie einzeln: der Fund ist da,
    # aber nicht eindeutig lesbar (keine automatische Quarantaene, siehe `test_watch_checks_files_with_a_line_break...`).
    unreliable = kind == "watch" and char == "\r"
    assert (res.status, res.unreliable, res.findings) == (
        "infected", unreliable, [(str(evil), "Win.Test.EICAR_HC-1")]), out


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


# --- Waechter: Dateinamen mit Zeilenumbruch oder Wagenruecklauf ------------------------------------------
# `--file-list` kennt nur "ein Pfad je Zeile". Ein Name mit Zeilenumbruch zerriss in der Liste in
# Bruchstuecke, ClamAV meldete "Can't access file" (Code 2), und der Lauf galt als sauber -- die echte Datei
# wurde nie geprueft.

_CLAMAV_LIKE = r"""#!/bin/sh
# clamscan/clamdscan wie das echte ClamAV (Quelltext clamscan/manager.c, clamdscan/proto.c, common/misc.c), soweit es hier
# zaehlt: --file-list liest zeilenweise mit `fgets(buff, 1024, ...)` (hoechstens 1023 Zeichen je Lesevorgang, der Rest einer
# laengeren Zeile ist ein eigener Eintrag) und schneidet am Zeilenende CR/LF ab (Dateien auf der Kommandozeile werden dann
# ignoriert). Eine nicht vorhandene Datei gibt bei clamscan zuerst `perror` auf stderr ("<Pfad>: No such file or directory"),
# dann "WARNING: <Pfad>: Can't access file" (Code 2), bei clamdscan nur "ERROR: Can't access file <Pfad>" (Code 2).
# Inhalt "EICAR" gibt "<Pfad>: Win.Test.EICAR_HC-1 FOUND" und Code 1. Gibt es die Datei `@LOG@.resolve`, meldet der
# Fund den aufgeloesten Pfad (Symlinks ersetzt), wie ClamAV 1.0 und clamdscan.
if [ "${0##*/}" = clamdscan ] && [ "$1" = --ping ]; then exit 0; fi
nosum=; list=
for a in "$@"; do case "$a" in --file-list=*) list="${a#--file-list=}";; --no-summary) nosum=1;; esac; done
printf '%s\n' "${0##*/} $*" | sed 's/ \/[^ ]*$//' >> "@LOG@.calls"
n=0; inf=0; err=0
scan() {
  if [ -f "$1" ]; then
    n=$((n+1))
    if grep -q EICAR "$1" 2>/dev/null; then
      p=$1; [ -e "@LOG@.resolve" ] && p=$(readlink -f "$1")
      printf '%s: Win.Test.EICAR_HC-1 FOUND\n' "$p"; inf=$((inf+1))
    fi
  elif [ "${0##*/}" = clamdscan ]; then
    printf "ERROR: Can't access file %s\n" "$1"; err=1
  else
    printf '%s: No such file or directory\n' "$1" >&2
    printf "WARNING: %s: Can't access file\n" "$1"; err=1
  fi
}
if [ -n "$list" ]; then
  CR=$(printf '\r')
  LC_ALL=C awk '{ s = $0; while (length(s) >= 1023) { print substr(s, 1, 1023); s = substr(s, 1024) } print s }' "$list" > "@LOG@.cut"
  while IFS= read -r f || [ -n "$f" ]; do f=${f%"$CR"}; printf '%s\0' "$f" >> "@LOG@.list"; scan "$f"; done < "@LOG@.cut"
else
  for f in "$@"; do case "$f" in -*) ;; *) printf '%s\0' "$f" >> "@LOG@.args"; scan "$f";; esac; done
fi
if [ -z "$nosum" ]; then echo; echo "----------- SCAN SUMMARY -----------"; echo "Scanned files: $n"; echo "Infected files: $inf"; fi
[ "$inf" -gt 0 ] && exit 1
[ "$err" -gt 0 ] && exit 2
exit 0
"""


def _clamav_like(tmp_path: Path, *, clamd: bool = False) -> Path:
    """Attrappen `clamscan` (und mit `clamd` auch ein antwortendes `clamdscan`) in `tmp_path/bin`; die Dateiliste und
    die Argumente jedes Aufrufs landen NUL-getrennt in `tmp_path/log.list` und `tmp_path/log.args`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in ("clamscan", "clamdscan") if clamd else ("clamscan",):
        (bin_dir / name).write_text(_CLAMAV_LIKE.replace("@LOG@", str(tmp_path / "log")))
        (bin_dir / name).chmod(0o755)
    return bin_dir


def _logged(tmp_path: Path, kind: str) -> list[str]:
    path = tmp_path / f"log.{kind}"
    return path.read_bytes().decode("utf-8", "replace").split("\0")[:-1] if path.exists() else []


def _run_watch_raw(tmp_path: Path, watched: Path, *, clamd: bool = False) -> tuple[str, str, av.ScanResult]:
    mark = av.new_rc_mark()
    cmd = av.build_watch_command([str(watched)], minutes=5, use_clamd=clamd, mark=mark)
    out = _run_raw(tmp_path, cmd)
    return out, mark, av.parse_scan_output(out, mark, roots=[str(watched)])


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("clamd", [False, True], ids=["clamscan", "clamdscan"])
@pytest.mark.parametrize("name", ["evil\nname", "evil\r", "dir\n/evil"], ids=["LF", "trailing-CR", "LF-in-folder"])
def test_watch_checks_files_with_a_line_break_in_the_name_and_never_lists_them(tmp_path, clamd, name):
    """Die Datei mit Umbruch oder CR im Namen steht nie in der Liste (kein Bruchstueck kann ins Leere zeigen),
    sondern geht als Argument an einen eigenen clamscan. Schadsoftware darin wird gefunden: nicht sauber, und
    weil die Fund-Zeile roh im Text steht, nie als eindeutig lesbar (keine automatische Quarantaene)."""
    watched = tmp_path / "watched"
    (watched / "dir\n").mkdir(parents=True)
    normal = watched / "ok.sh"
    normal.write_text("echo hi")
    evil = watched / name
    evil.write_text("EICAR")
    _clamav_like(tmp_path, clamd=clamd)
    out, _mark, res = _run_watch_raw(tmp_path, watched, clamd=clamd)
    assert _logged(tmp_path, "list") == [str(normal)], out  # kein Bruchstueck, nur die normale Datei
    assert _logged(tmp_path, "args") == [str(evil)], out
    assert (res.status, res.unreliable) == ("infected", True), out
    assert res.files_scanned == 2 and not res.errors, out
    assert "Can't access" not in out
    # Der Fund-Lauf ist rot: Rueckgabecode 1.
    assert out.rstrip().endswith("=1"), out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("clamd", [False, True], ids=["clamscan", "clamdscan"])
def test_watch_does_not_report_clean_when_the_only_new_file_has_a_line_break_in_its_name(tmp_path, clamd):
    """Vorher: nur eine Datei, Name mit Umbruch -> Liste aus zwei Bruchstuecken, `clamscan` meldete Code 2 mit "Can't access",
    die Auswertung machte daraus "sauber" (clamdscan sogar bei genau einer Datei). Jetzt: Fund."""
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "only\nfile").write_text("EICAR")
    _clamav_like(tmp_path, clamd=clamd)
    out, _mark, res = _run_watch_raw(tmp_path, watched, clamd=clamd)
    assert res.status == "infected" and res.unreliable, out
    assert _logged(tmp_path, "list") == []  # die Liste blieb leer, es gab nichts Gewoehnliches zu pruefen


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("clamd", [False, True], ids=["clamscan", "clamdscan"])
def test_watch_with_a_harmless_line_break_name_stays_clean(tmp_path, clamd):
    """Der Gegenpart: eine harmlose Datei mit Umbruch im Namen (z. B. `Icon<CR>` von macOS) macht keinen Alarm
    und keinen Fehler -- sie wird geprueft und zaehlt mit."""
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "ok.sh").write_text("echo hi")
    (watched / "Icon\r").write_text("harmlos")
    _clamav_like(tmp_path, clamd=clamd)
    out, _mark, res = _run_watch_raw(tmp_path, watched, clamd=clamd)
    assert (res.status, res.findings, res.errors, res.files_scanned) == ("clean", [], [], 2), out
    assert _logged(tmp_path, "args") == [str(watched / "Icon\r")]


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_without_odd_names_runs_exactly_as_before(tmp_path):
    """Ohne Sondernamen kein zweiter Lauf, keine Zusatzzeile und derselbe Code wie bisher: normale Funde und
    echte Rechteprobleme/verschwundene Dateien bleiben, was sie waren."""
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "ok.sh").write_text("echo hi")
    (watched / "evil.sh").write_text("EICAR")
    _clamav_like(tmp_path)
    out, mark, res = _run_watch_raw(tmp_path, watched)
    assert _logged(tmp_path, "args") == [] and "Unusual file names" not in out
    assert (res.status, res.unreliable, res.findings, res.files_scanned) == (
        "infected", False, [(str(watched / "evil.sh"), "Win.Test.EICAR_HC-1")], 2), out
    assert out.rstrip().endswith(f"{mark}1")
    assert (tmp_path / "log.calls").read_text().count("clamscan") == 1  # genau ein clamscan-Start


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_a_normal_unreadable_file_is_not_an_error_with_or_without_odd_names(tmp_path):
    """Code 2 mit einer Datei, die ClamAV nicht lesen konnte (verschwunden, keine Rechte): der Lauf bleibt sauber,
    die Meldung sichtbar -- auch neben einer Datei mit Sondernamen."""
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "ok.sh").write_text("echo hi")
    bin_dir = _clamav_like(tmp_path)
    # Eine Attrappe, die die Liste um eine Datei erweitert, die nicht (mehr) da ist.
    real = bin_dir / "clamscan"
    (bin_dir / "clamscan-real").write_text(real.read_text())
    (bin_dir / "clamscan-real").chmod(0o755)
    real.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do case "$a" in --file-list=*) echo "' + str(watched) + '/gone.tmp" >> "${a#--file-list=}";; esac; done\n'
        f'exec "{bin_dir}/clamscan-real" "$@"\n'
    )
    out, _mark, res = _run_watch_raw(tmp_path, watched)
    assert (res.status, res.files_scanned) == ("clean", 1), out
    assert res.errors == [f"WARNING: {watched}/gone.tmp: Can't access file"]  # so schreibt clamscan es wirklich
    # Mit Sonderdatei daneben: derselbe Lauf, nur eine gepruefte Datei mehr.
    (watched / "Icon\r").write_text("harmlos")
    out, _mark, res = _run_watch_raw(tmp_path, watched)
    assert (res.status, res.files_scanned, res.unreliable) == ("clean", 2, False), out
    assert res.errors == [f"WARNING: {watched}/gone.tmp: Can't access file"]  # so schreibt clamscan es wirklich


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_with_odd_names_and_no_clamav_reports_a_missing_clamav(tmp_path):
    """Kein ClamAV: derselbe Fehler "nicht installiert" wie immer, auch wenn nur eine Datei mit Sondernamen neu ist."""
    watched = tmp_path / "watched"
    watched.mkdir()
    (tmp_path / "bin").mkdir()
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"}
    if shutil.which("clamscan", path=env["PATH"]):
        pytest.skip("auf diesem Rechner ist ein echtes clamscan installiert")
    mark = av.new_rc_mark()
    cmd = av.build_watch_command([str(watched)], minutes=5, mark=mark)
    for names in ([], ["only\nfile"], ["ok.sh", "only\nfile"]):
        for name in names:
            (watched / name).write_text("x")
        out = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, env=env, timeout=60, check=False).stdout.decode()
        res = av.parse_scan_output(out, mark, roots=[str(watched)])
        assert (res.status, res.error) == ("error" if names else "clean", av.NOT_INSTALLED_MESSAGE if names else None), (names, out)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize(("main_rc", "odd_rc", "expected"), [
    (0, 0, 0), (0, 1, 1), (1, 0, 1), (1, 1, 1), (2, 0, 2), (0, 2, 2), (2, 2, 2), (2, 1, 1), (1, 2, 1),
    (127, 0, 127), (127, 1, 127), (0, 127, 127), (0, 139, 139), (2, 139, 139), (1, 139, 1),
])
def test_the_return_codes_of_both_runs_become_one(tmp_path, main_rc, odd_rc, expected):
    """127 (nicht installiert) vor 1 (Fund) vor allem anderen; Code 2 bleibt Code 2, ein Absturz (139) zaehlt
    mehr als Code 2 und weniger als ein Fund."""
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "ok.sh").write_text("echo hi")
    (watched / "odd\nname").write_text("x")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "clamscan").write_text(
        "#!/bin/sh\n"
        f'for a in "$@"; do case "$a" in --file-list=*) exit {main_rc};; esac; done\nexit {odd_rc}\n'
    )
    (bin_dir / "clamscan").chmod(0o755)
    mark = av.new_rc_mark()
    out = _run_raw(tmp_path, av.build_watch_command([str(watched)], minutes=5, mark=mark))
    assert out.rstrip().splitlines()[-1] == f"{mark}{expected}", out
    assert "Unusual file names: 1" in out


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
    assert on.index("clamdscan --ping 1") < on.index("clamdscan --fdpass") < on.index('clamscan -i --stdout --file-list="$L"')
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
                    out = out.replace(MARK, used.group(0)).replace(ROOTS, "@@scan-roots-" + used.group(0)[len("@@scan-rc-"):])
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
async def test_a_finding_outside_the_scanned_folders_is_never_quarantined_automatically():
    """Selbst wenn alle Zahlen stimmen (wie im clamd-Modus ohne Zusammenfassung): Der Schnellscan prueft /tmp, /home ... --
    ein Fund fuer /etc/passwd kann nicht von ihm stammen. Nichts wird verschoben, der Mensch bekommt Meldung und Notiz."""
    out = f"/etc/passwd: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    ctx = _FakeCtx(None, {"clamscan -r": out, "mv -f": "@@mode=644\n"})
    scans, findings = await _scan_once(ctx)
    assert not any("mv -f" in c for c in ctx.commands), ctx.commands
    assert [(f["path"], f["status"]) for f in findings] == [("/etc/passwd", "detected")]
    assert findings[0]["note"].startswith("Der Pfad ist nicht gesichert")
    assert "nicht eindeutig lesbar" in ctx.notes[0].body and "/etc/passwd" not in ctx.notes[0].body
    assert [s["status"] for s in scans] == ["infected"] * 2


@pytest.mark.asyncio
async def test_a_custom_scan_folder_is_the_root_for_its_findings():
    """Ein Scan eines eigenen Ordners: Funde darunter sind in Ordnung, andere nicht."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    out = f"/srv/data/a.sh: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    ctx = _FakeCtx(async_sessionmaker(engine, expire_on_commit=False), {"clamscan -r": out, "mv -f": "@@mode=755\n"})
    defender = Defender(ctx)
    import asyncio

    for folder, moved in (("/srv/data", True), ("/srv/other", False)):
        ctx.commands.clear()
        await defender.start_scans(await defender.target_hosts(), "custom", paths=[folder], trigger="manual")
        for _ in range(100):
            await asyncio.sleep(0.02)
            if not defender.is_running("h1", "custom"):
                break
        assert any("mv -f" in c for c in ctx.commands) is moved, folder
        for f in await defender.list_findings():
            await defender.apply_action_result(f["id"], "delete", True, "ok")  # fuer den naechsten Durchlauf freiraeumen
    await engine.dispose()


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
                    # Sauberer Scan, aber Signaturen veraltet: die Kachel darf nicht "sauber" melden.
                    {"host_name": "alt-pve", "clamav_installed": True, "clamav_version": "1.0.7", "signature_stale": True,
                     "last_scan": {"status": "clean"}},
                    # Unbekanntes Signatur-Alter ist kein Beleg fuer "veraltet": bleibt "sauber".
                    {"host_name": "neu-pve", "clamav_installed": True, "clamav_version": "1.4.3", "signature_stale": None,
                     "last_scan": {"status": "clean"}},
                    # Neuestes Audit gescheitert: die Haertung kommt aus dem letzten erfolgreichen Audit.
                    {"host_name": "audit-pve", "clamav_installed": True, "clamav_version": "1.4.3", "signature_stale": False,
                     "last_scan": {"status": "clean"},
                     "last_audit": {"status": "error", "hardening_index": None, "error": "Lynis nicht gefunden"},
                     "last_ok_audit": {"status": "ok", "hardening_index": 71}},
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
    assert (rows[3]["title"], rows[3]["label"], rows[3]["tone"]) == ("alt-pve", "Signaturen veraltet", "warn")
    assert rows[3]["subtitle"] == "ClamAV 1.0.7"
    assert (rows[4]["label"], rows[4]["tone"]) == ("sauber", "good")
    assert (rows[5]["label"], rows[5]["subtitle"]) == ("sauber", "ClamAV 1.4.3 · Härtung 71")


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


# --- Lagebericht ("Briefing senden"): ehrliche Rueckmeldung, Titel ohne Tageszeit ---------


def test_briefing_title_names_no_time_of_day() -> None:
    """Der Bericht kommt auch auf Knopfdruck um 21:45 Uhr -- "Guten Morgen" waere dann falsch."""
    from nodvard_deck_ext_nexus_soc.defender import build_briefing

    host = SimpleNamespace(name="a", display_name=None, status=SimpleNamespace(value="up"))
    ruhig = {"summary": {"score": 90, "protected": 1, "hosts": 1, "open_threats": 0, "quarantined": 0, "findings_30d": 0, "avg_hardening": None},
             "hosts": [{"host_name": "a", "reachable": True, "clamav_installed": True, "freshclam_active": True, "last_scan": None}]}
    assert build_briefing(ruhig, [host])[0] == "Lagebericht – alles im grünen Bereich"
    offen = {**ruhig, "hosts": [{"host_name": "a", "reachable": True, "clamav_installed": False}]}
    assert build_briefing(offen, [host])[0] == "Lagebericht – es gibt etwas zu tun"
    bedroht = {**ruhig, "summary": {**ruhig["summary"], "open_threats": 1}}
    assert build_briefing(bedroht, [host])[0] == "Lagebericht – Bedrohung offen!"
    for title in (build_briefing(x, [host])[0] for x in (ruhig, offen, bedroht)):
        assert "Morgen" not in title


def test_briefing_without_any_server_does_not_rate_or_cheer() -> None:
    from nodvard_deck_ext_nexus_soc.defender import build_briefing

    leer = {"summary": {"score": 0, "protected": 0, "hosts": 0, "open_threats": 0, "quarantined": 0, "findings_30d": 0, "avg_hardening": None}, "hosts": []}
    title, body, level = build_briefing(leer, [])
    assert title == "Lagebericht – noch kein Server eingerichtet" and level == "info"
    assert "Schutzwert" not in body and "0/0" not in body
    assert "noch keiner eingerichtet" in body and "Server & Zugänge" in body


def test_briefing_with_servers_but_none_checkable_does_not_cheer() -> None:
    """Server sind eingerichtet, aber keiner ist pruefbar (z. B. ohne SSH-Zugang): nichts wurde geprueft."""
    from nodvard_deck_ext_nexus_soc.defender import build_briefing

    host = SimpleNamespace(name="a", display_name=None, status=SimpleNamespace(value="up"))
    nichts = {"summary": {"score": 0, "protected": 0, "hosts": 0, "hosts_known": 1, "open_threats": 0, "quarantined": 0,
                          "findings_30d": 0, "avg_hardening": None}, "hosts": []}
    title, body, level = build_briefing(nichts, [host])
    assert title == "Lagebericht – noch nichts geprüft" and level == "info"
    assert "grünen Bereich" not in title and "Schutzwert" not in body and "0/0" not in body
    assert "noch nichts zu prüfen" in body and "Linux-Server mit SSH-Zugang" in body and "Markierung" in body


def test_briefing_keeps_open_threats_when_no_server_is_checkable() -> None:
    """Die Funde stehen weiter offen (Server entfernt oder SSH-Zugang weg): Titel und Stufe sagen "Bedrohung",
    also muss der Text sie auch nennen."""
    from nodvard_deck_ext_nexus_soc.defender import build_briefing

    host = SimpleNamespace(name="a", display_name=None, status=SimpleNamespace(value="up"))
    bedroht = {"summary": {"score": 0, "protected": 0, "hosts": 0, "hosts_known": 1, "open_threats": 2, "quarantined": 1,
                           "findings_30d": 3, "avg_hardening": None}, "hosts": []}
    title, body, level = build_briefing(bedroht, [host])
    assert title == "Lagebericht – Bedrohung offen!" and level == "critical"
    assert "Bedrohungen: 2 offen, 1 in Quarantäne, 3 Funde in 30 Tagen" in body
    assert "Schutzwert" not in body, "bewertet wird trotzdem nichts"
    _, body_gone, level_gone = build_briefing(bedroht, [])
    assert level_gone == "critical" and "Bedrohungen: 2 offen" in body_gone, "auch ganz ohne Server"


def test_push_state_tells_the_truth_about_the_phone() -> None:
    from nodvard_sdk import NotifyResult

    from nodvard_deck_ext_nexus_soc.defender import push_state

    assert push_state(NotifyResult(notification_id="n", delivered=True)) == "sent"
    assert push_state(NotifyResult(notification_id="n", delivered=False)) == "not_delivered"
    assert push_state(NotifyResult(notification_id="n", delivered=False, suppressed=True)) == "suppressed"
    assert push_state(NotifyResult(notification_id="n")) == "unknown", "ältere Kerne wissen es nicht"
    assert push_state(None) == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(("delivered", "suppressed", "expected"), [(True, False, "sent"), (False, False, "not_delivered"), (False, True, "suppressed")])
async def test_send_briefing_reports_whether_the_push_arrived(delivered, suppressed, expected):
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import Base
    from nodvard_sdk import NotifyResult

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx = _FakeCtx(async_sessionmaker(engine, expire_on_commit=False), {})

    async def send(n):
        ctx.notes.append(n)
        return NotifyResult(notification_id="n1", delivered=delivered, suppressed=suppressed)

    ctx.notify = SimpleNamespace(send=send)
    result = await Defender(ctx).send_briefing()
    assert result["push"] == expected and result["title"].startswith("Lagebericht")
    assert len(ctx.notes) == 1, "die Meldung steht in jedem Fall unter Meldungen"
    await engine.dispose()


@pytest.mark.asyncio
async def test_briefing_endpoint_refuses_a_second_click_while_one_is_running():
    import asyncio

    from fastapi import HTTPException

    from nodvard_deck_ext_nexus_soc.defender_api import build_routers

    started, release = asyncio.Event(), asyncio.Event()
    calls: list[int] = []

    class _Def:
        async def send_briefing(self, updates=None, guard=None):
            calls.append(1)
            started.set()
            await release.wait()
            return {"title": "T", "body": "B", "level": "info", "push": "sent"}

    _, manage = build_routers(SimpleNamespace(api=SimpleNamespace(current_actor=lambda: None)), _Def())
    endpoint = next(r.endpoint for r in manage.routes if r.path == "/defender/briefing")
    first = asyncio.create_task(endpoint())
    await started.wait()
    with pytest.raises(HTTPException) as err:
        await endpoint()
    assert err.value.status_code == 409 and "gerade schon" in err.value.detail
    release.set()
    assert (await first)["push"] == "sent"
    assert (await endpoint())["push"] == "sent", "danach geht es wieder"
    assert len(calls) == 2, "der abgewiesene Klick hat kein zweites Briefing angelegt"


def test_protection_widget_without_servers_says_nothing_to_rate():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from nodvard_deck_ext_nexus_soc.defender_api import build_routers

    class _Def:
        async def overview(self):
            return {"summary": {"score": 0, "protected": 0, "hosts": 0, "quarantined": 0, "open_threats": 0}, "hosts": []}

    read, _ = build_routers(SimpleNamespace(api=SimpleNamespace(current_actor=lambda: None)), _Def())
    app = FastAPI()
    app.include_router(read)
    rows = TestClient(app).get("/defender/widgets/protection").json()["data"]
    assert len(rows) == 1 and "Schutzwert" not in rows[0]["title"]
    assert rows[0]["title"] == "Noch kein Server" and "Füge zuerst einen Server hinzu" in rows[0]["subtitle"]
    assert rows[0]["tone"] == "neutral"


def test_protection_widget_with_servers_but_none_checkable_asks_for_access():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from nodvard_deck_ext_nexus_soc.defender_api import build_routers

    class _Def:
        async def overview(self):
            return {"summary": {"score": 0, "protected": 0, "hosts": 0, "hosts_known": 2, "quarantined": 0, "open_threats": 0}, "hosts": []}

    read, _ = build_routers(SimpleNamespace(api=SimpleNamespace(current_actor=lambda: None)), _Def())
    app = FastAPI()
    app.include_router(read)
    rows = TestClient(app).get("/defender/widgets/protection").json()["data"]
    assert len(rows) == 1 and rows[0]["title"] == "Noch kein Server prüfbar"
    assert "Füge zuerst einen Server hinzu" not in rows[0]["subtitle"] and "SSH-Zugang" in rows[0]["subtitle"]
    assert rows[0]["tone"] == "neutral"


def test_protection_widget_names_every_requirement_for_a_checkable_server():
    """Prüfbar sind nur Linux-Server mit SSH-Zugang (und, falls eingestellt, der passenden Markierung) --
    der Text nennt nicht nur einen der Gründe."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from nodvard_deck_ext_nexus_soc.defender_api import build_routers

    class _Def:
        async def overview(self):
            return {"summary": {"score": 0, "protected": 0, "hosts": 0, "hosts_known": 2, "quarantined": 0, "open_threats": 0}, "hosts": []}

    read, _ = build_routers(SimpleNamespace(api=SimpleNamespace(current_actor=lambda: None)), _Def())
    app = FastAPI()
    app.include_router(read)
    subtitle = TestClient(app).get("/defender/widgets/protection").json()["data"][0]["subtitle"]
    assert "Linux-Server mit SSH-Zugang" in subtitle and "Markierung" in subtitle
    assert "Deine Server haben noch keinen SSH-Zugang" not in subtitle


@pytest.mark.parametrize("hosts_known", [0, 2])
def test_protection_widget_shows_open_threats_when_no_server_is_checkable(hosts_known):
    """Offene Funde bleiben offen, auch wenn ihr Server entfernt wurde oder der SSH-Zugang fehlt: die Kachel
    sagt dann nicht "nichts zu bewerten"."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from nodvard_deck_ext_nexus_soc.defender_api import build_routers

    class _Def:
        async def overview(self):
            return {"summary": {"score": 0, "protected": 0, "hosts": 0, "hosts_known": hosts_known, "quarantined": 0, "open_threats": 3}, "hosts": []}

    read, _ = build_routers(SimpleNamespace(api=SimpleNamespace(current_actor=lambda: None)), _Def())
    app = FastAPI()
    app.include_router(read)
    rows = TestClient(app).get("/defender/widgets/protection").json()["data"]
    assert len(rows) == 1 and "Schutzwert" not in rows[0]["title"]
    assert (rows[0]["label"], rows[0]["tone"]) == ("3 Bedrohung(en)", "danger")


@pytest.mark.asyncio
async def test_overview_counts_servers_that_are_not_checkable():
    """`hosts` zaehlt nur pruefbare Server; `hosts_known` alle eingerichteten (Server ohne SSH-Zugang gehoeren dazu)."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx = _FakeCtx(async_sessionmaker(engine, expire_on_commit=False), {})
    ctx._host = Host(id="h9", name="ohne-zugang", display_name="Ohne Zugang", address="10.0.0.9", has_credential=False)
    summary = (await Defender(ctx).overview())["summary"]
    assert summary["hosts"] == 0 and summary["hosts_known"] == 1
    await engine.dispose()


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


# --- Echtes clamscan-Format fuer "Can't access file" ----------------------------------------------------------
# clamscan (manager.c, 0.103 bis 1.4): `perror` ("<Pfad>: No such file or directory"), dann "WARNING: <Pfad>: Can't access
# file", Code 2. Vorher erkannte die Auswertung nur "ERROR: Can't access file <Pfad>" (das ist clamdscan), und die
# Absicherung "Code 2 mit fremdem Pfad ist nicht sauber" griff bei clamscan nie.

@pytest.mark.parametrize("error", [
    "WARNING: name: Can't access file",  # Bruchstueck eines Namens mit Zeilenumbruch: nicht absolut
    "WARNING: /etc/passwd: Can't access file",
    "/etc/passwd: No such file or directory",
    "/etc/passwd: No such file or directory\nWARNING: /etc/passwd: Can't access file",
    "WARNING: /tmp/../etc/passwd: Can't access file",
    "WARNING: Can't open file /etc/passwd: Permission denied",
])
def test_code_2_with_a_real_clamscan_message_for_a_foreign_path_is_not_clean(error):
    out = f"{error}\nScanned files: 3\nInfected files: 0\n{MARK}2\n"
    assert av.parse_scan_output(out, MARK).status == "clean"  # ohne Wurzeln wie bisher
    res = av.parse_scan_output(out, MARK, roots=["/tmp"])
    assert (res.status, res.error) == ("error", av.AMBIGUOUS_OUTPUT_MESSAGE)


@pytest.mark.parametrize("error", [
    "WARNING: /tmp/gone.tmp: Can't access file",
    "/tmp/gone.tmp: No such file or directory\nWARNING: /tmp/gone.tmp: Can't access file",
    "WARNING: Can't open file /tmp/secret: Permission denied",
])
def test_code_2_with_a_real_clamscan_message_inside_the_roots_stays_clean_and_visible(error):
    res = av.parse_scan_output(f"{error}\nScanned files: 3\nInfected files: 0\n{MARK}2\n", MARK, roots=["/tmp"])
    assert (res.status, res.files_scanned, res.error) == ("clean", 3, None)
    assert any(e.startswith("WARNING:") and "/tmp/" in e for e in res.errors)  # kein stiller Verlust


def test_a_real_clamscan_message_for_a_foreign_path_makes_an_infected_run_unreliable():
    out = f"WARNING: /etc/passwd: Can't access file\n/tmp/a: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    assert av.parse_scan_output(out, MARK).unreliable is False
    assert av.parse_scan_output(out, MARK, roots=["/tmp"]).unreliable is True


# --- Waechter: sehr lange Pfade --------------------------------------------------------------------------
# `clamscan --file-list` liest je Eintrag hoechstens 1023 Zeichen. Ein laengerer Pfad zerfiel in Stuecke (das zweite konnte
# sogar wieder mit der Wurzel beginnen), die echte Datei blieb ungeprueft, und der Lauf galt als sauber.

def _file_with_path_length(base: Path, total: int) -> Path:
    """Pfad einer (noch nicht angelegten) Datei unter `base`, genau `total` Zeichen lang; die Ordner werden angelegt."""
    cur = base
    while True:
        room = total - len(str(cur)) - 1  # Platz fuer "/" plus naechsten Namen
        if room <= 255:
            assert room >= 1
            return cur / ("f" * room)
        cur = cur / ("d" * 200)
        cur.mkdir(parents=True, exist_ok=True)


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("clamd", [False, True], ids=["clamscan", "clamdscan"])
def test_watch_checks_a_path_of_1023_bytes_or_more_as_an_argument_never_from_the_list(tmp_path, clamd):
    watched = tmp_path / "watched"
    watched.mkdir()
    normal = watched / "ok.sh"
    normal.write_text("echo hi")
    just_fits = _file_with_path_length(watched, 1022)  # passt noch in einen Lesevorgang von fgets
    just_fits.write_text("harmlos")
    too_long = _file_with_path_length(watched / "x", 1023)
    too_long.write_text("EICAR")
    _clamav_like(tmp_path, clamd=clamd)
    out, _mark, res = _run_watch_raw(tmp_path, watched, clamd=clamd)
    assert sorted(_logged(tmp_path, "list")) == sorted([str(normal), str(just_fits)]), out
    assert _logged(tmp_path, "args") == [str(too_long)], out
    assert (res.status, res.unreliable, res.files_scanned) == ("infected", True, 3), out
    assert [p for p, _s in res.findings] == [str(too_long)]
    assert "Can't access" not in out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("clamd", [False, True], ids=["clamscan", "clamdscan"])
def test_watch_does_not_report_clean_for_a_harmless_or_infected_long_path_alone(tmp_path, clamd):
    """Genau eine neue Datei, ihr Pfad ist zu lang fuer die Liste: vorher zwei Stuecke, "Can't access file" und ein Lauf
    ohne geprueften Inhalt, der als sauber galt. Jetzt wird sie geprueft (zaehlt mit) und ein Fund bleibt ein Fund."""
    watched = tmp_path / "watched"
    watched.mkdir()
    long_file = _file_with_path_length(watched, 1200)
    long_file.write_text("EICAR")
    _clamav_like(tmp_path, clamd=clamd)
    out, _mark, res = _run_watch_raw(tmp_path, watched, clamd=clamd)
    assert (res.status, res.files_scanned) == ("infected", 1), out
    assert _logged(tmp_path, "list") == []
    long_file.write_text("harmlos")
    (tmp_path / "log.args").unlink()
    out, _mark, res = _run_watch_raw(tmp_path, watched, clamd=clamd)
    assert (res.status, res.files_scanned, res.unreliable, res.errors) == ("clean", 1, False, []), out


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_with_a_long_path_whose_second_piece_starts_with_the_root_again_is_not_clean(tmp_path):
    """Ein denkbarer Angriff: Die Ordnernamen sind so gewaehlt, dass das Stueck nach Byte 1023 wieder mit der
    Wurzel beginnt (`<1023 Zeichen>/tmp/.../watched/evil.sh`). Ein Stueck unter der Wurzel galt nicht als fremd -- darum
    reicht eine bessere Regex allein nicht: Der Pfad muss gar nicht erst in die Liste."""
    watched = tmp_path / "watched"
    watched.mkdir()
    chain = _file_with_path_length(watched, 1023)  # genau 1023 Zeichen, hier als Ordner
    chain.mkdir()
    sneaky_dir = Path(str(chain) + str(watched))  # ab Byte 1023 beginnt der Pfad wieder mit der Wurzel
    sneaky_dir.mkdir(parents=True)
    sneaky = sneaky_dir / "evil.sh"
    sneaky.write_text("EICAR")
    _clamav_like(tmp_path)
    out, _mark, res = _run_watch_raw(tmp_path, watched)
    assert _logged(tmp_path, "args") == [str(sneaky)], out
    assert _logged(tmp_path, "list") == []
    assert res.status == "infected" and [p for p, _s in res.findings] == [str(sneaky)], out
    assert res.unreliable is True


# --- Waechter: Obergrenze von 2000 Dateien ---------------------------------------------------------------

_LIGHT_CLAMAV = r"""#!/bin/sh
# Schlanke Attrappe fuer Laeufe mit tausenden Dateien: zaehlt die Liste, ohne jede Datei zu lesen. Gibt es die Datei
# `@LOG@.firstbad`, gilt die ERSTE Zeile der Liste als Fund. Dateien auf der Kommandozeile werden einzeln gelesen
# (Inhalt "EICAR" ist ein Fund) und NUL-getrennt in `@LOG@.args` protokolliert.
list=; nosum=
for a in "$@"; do case "$a" in --file-list=*) list="${a#--file-list=}";; --no-summary) nosum=1;; esac; done
n=0; inf=0
if [ -n "$list" ]; then
  n=$(wc -l < "$list" | tr -d ' ')
  if [ -e "@LOG@.firstbad" ]; then printf '%s: Win.Test.EICAR_HC-1 FOUND\n' "$(head -n 1 "$list")"; inf=1; fi
else
  for f in "$@"; do
    case "$f" in -*) ;; *) printf '%s\0' "$f" >> "@LOG@.args"; n=$((n+1))
      if grep -q EICAR "$f" 2>/dev/null; then printf '%s: Win.Test.EICAR_HC-1 FOUND\n' "$f"; inf=$((inf+1)); fi;; esac
  done
fi
if [ -z "$nosum" ]; then echo; echo "----------- SCAN SUMMARY -----------"; echo "Scanned files: $n"; echo "Infected files: $inf"; fi
[ "$inf" -gt 0 ] && exit 1
exit 0
"""


def _light_clamav(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "clamscan").write_text(_LIGHT_CLAMAV.replace("@LOG@", str(tmp_path / "log")))
    (bin_dir / "clamscan").chmod(0o755)


def _many_files(folder: Path, count: int) -> None:
    folder.mkdir(exist_ok=True)
    for i in range(count):
        (folder / f"f{i}").touch()


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
@pytest.mark.parametrize("count, skipped", [(2000, 0), (2001, 1), (2003, 3), (4500, 2500)])
def test_watch_never_reports_clean_when_it_checked_only_the_first_2000_files(tmp_path, count, skipped):
    """Mehr als 2000 neue Dateien: Die Shell zaehlt den Rest und schreibt `Skipped files: N` direkt vor die Marke. Vorher
    verwarf `cat > /dev/null` den Rest ohne Spur, und der Lauf meldete "sauber"."""
    watched = tmp_path / "watched"
    _many_files(watched, count)
    _light_clamav(tmp_path)
    out, mark, res = _run_watch_raw(tmp_path, watched)
    assert res.files_scanned == min(count, 2000), out
    if skipped == 0:
        assert "Skipped files" not in out
        assert (res.status, res.skipped, res.unreliable) == ("clean", 0, False)
        return
    assert out.rstrip().splitlines()[-2:] == [f"Skipped files: {skipped}", f"{mark}0"], out[-300:]
    assert (res.status, res.skipped, res.unreliable) == ("error", skipped, True)
    assert res.error == f"Der Wächter hat nur 2000 von {2000 + skipped} neuen Dateien geprüft, der Rest blieb ungeprüft. " \
                        "Bitte einen Schnell- oder Tiefenscan starten."


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_a_finding_in_a_capped_watch_run_is_never_moved_automatically(tmp_path):
    watched = tmp_path / "watched"
    _many_files(watched, 2005)
    _light_clamav(tmp_path)
    (tmp_path / "log.firstbad").touch()
    out, _mark, res = _run_watch_raw(tmp_path, watched)
    assert (res.status, res.infected, res.skipped) == ("infected", 1, 5), out
    assert res.unreliable is True and res.paths_unsure is False  # der Pfad ist lesbar, aber der Lauf war unvollstaendig


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell")
def test_watch_still_checks_files_with_a_line_break_when_there_are_more_than_2000_new_files(tmp_path):
    """Die Zeile `cat`/Rest-Lesen haelt die Pipe leer, damit `find` nicht per SIGPIPE stirbt, bevor es die Dateien mit
    Sondernamen an den Sonderlauf uebergibt. Ohne sie wuerde eine Datei mit Umbruch im Namen (und Schadcode) bei mehr als
    2000 neuen Dateien still nicht geprueft: dieser Test wird dann rot."""
    watched = tmp_path / "watched"
    _many_files(watched, 2600)
    evil = watched / "evil\nname"
    evil.write_text("EICAR")
    _light_clamav(tmp_path)
    out, _mark, res = _run_watch_raw(tmp_path, watched)
    assert _logged(tmp_path, "args") == [str(evil)], out[-400:]
    assert (res.status, res.unreliable) == ("infected", True), out[-400:]
    assert res.skipped == 600 and res.files_scanned == 2001  # 2000 aus der Liste plus die Sonderdatei


def test_a_file_name_cannot_hide_the_skipped_files_line_and_the_last_one_counts():
    forged = f"/tmp/x\nSkipped files: 0\n/tmp/q: {EICAR} FOUND\n{SUMMARY_ONE}Skipped files: 4\n{MARK}1\n"
    res = av.parse_scan_output(forged, MARK, roots=["/tmp"])
    assert (res.status, res.skipped, res.unreliable) == ("infected", 4, True)
    # Eine Zeile "Skipped files: 9" in einem Namen, ohne echte Zeile dahinter, macht nur vorsichtiger
    res = av.parse_scan_output(f"/tmp/a: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n", MARK, roots=["/tmp"])
    assert (res.skipped, res.unreliable) == (0, False)


def test_a_clean_run_with_skipped_files_is_an_error_not_clean():
    out = f"Scanned files: 2000\nInfected files: 0\nSkipped files: 12\n{MARK}0\n"
    res = av.parse_scan_output(out, MARK, roots=["/tmp"])
    assert (res.status, res.files_scanned, res.skipped, res.unreliable) == ("error", 2000, 12, True)
    assert "2000 von 2012" in (res.error or "")
    # auch mit Code 2 (eine Datei war zwischen find und Scan verschwunden)
    res = av.parse_scan_output(f"WARNING: /tmp/g: Can't access file\nScanned files: 2000\n"
                               f"Skipped files: 12\n{MARK}2\n", MARK, roots=["/tmp"])
    assert (res.status, res.skipped) == ("error", 12)


# --- Aufgeloeste Wurzeln (Symlinks) ----------------------------------------------------------------------
# ClamAV 1.0 (clamscan) und clamdscan melden den aufgeloesten Pfad: Ist `/home` ein Link auf `/data/home`, kommt
# `/data/home/x: ... FOUND` zurueck. Die Shell loest die Wurzeln selbst auf (erste Zeilen der Ausgabe).

ROOTS = "@@scan-roots-0123456789abcdef="


def test_a_finding_under_the_resolved_root_is_reliable_and_one_outside_both_roots_is_not():
    ok = f"{ROOTS}/data/home\n/data/home/x.sh: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    res = av.parse_scan_output(ok, MARK, roots=["/home"])
    assert (res.status, res.unreliable, res.findings) == ("infected", False, [("/data/home/x.sh", EICAR)])
    # die eingetragene Wurzel gilt weiter
    again = f"{ROOTS}/data/home\n/home/x.sh: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    assert av.parse_scan_output(again, MARK, roots=["/home"]).unreliable is False
    # ... und was unter keiner von beiden liegt, nie
    other = f"{ROOTS}/data/home\n/etc/passwd: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    assert av.parse_scan_output(other, MARK, roots=["/home"]).unreliable is True
    # ohne die Zeilen der Shell (alte Ausgabe) bleibt der aufgeloeste Pfad fremd
    assert av.parse_scan_output(ok.split("\n", 1)[1], MARK, roots=["/home"]).unreliable is True


def test_a_link_inside_the_root_pointing_to_etc_does_not_make_a_finding_in_etc_reliable():
    out = f"{ROOTS}/srv/data\n/etc/passwd: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    assert av.parse_scan_output(out, MARK, roots=["/srv/data"]).unreliable is True


@pytest.mark.parametrize("resolved", ["/", "relative/dir", "", "/data/ho\rme"])
def test_a_resolved_root_that_widens_the_check_is_ignored(resolved):
    """Zeigt eine Wurzel per Link auf `/`, darf das die Pruefung nicht auf alles ausweiten."""
    out = f"{ROOTS}{resolved}\n/etc/passwd: {EICAR} FOUND\n{SUMMARY_ONE}{MARK}1\n"
    assert av.parse_scan_output(out, MARK, roots=["/home"]).unreliable is True
    # ist die eingetragene Wurzel selbst `/` (Tiefenscan), ist `/` in Ordnung
    assert av.parse_scan_output(out, MARK, roots=["/"]).unreliable is False


def test_only_the_first_lines_count_as_resolved_roots_a_file_name_cannot_add_one():
    out = (f"{ROOTS}/home\n/home/a\n{ROOTS}/\n/etc/passwd: {EICAR} FOUND\n"  # Zeile 3 stammt aus einem Dateinamen
           f"{SUMMARY_ONE}{MARK}1\n")
    assert av.parse_scan_output(out, MARK, roots=["/home"]).unreliable is True
    # zwei Wurzeln, zwei Zeilen am Anfang, die dritte zaehlt nicht mehr
    two = f"{ROOTS}/home\n{ROOTS}/data/tmp\n/data/tmp/x: {EICAR} FOUND\n{ROOTS}/\n{SUMMARY_ONE}{MARK}1\n"
    res = av.parse_scan_output(two, MARK, roots=["/home", "/tmp"])
    assert (res.status, res.unreliable) == ("infected", False)


def _symlink_setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    real = tmp_path / "data" / "home"
    real.mkdir(parents=True)
    link = tmp_path / "home"
    link.symlink_to(real)
    other = tmp_path / "other"
    other.mkdir()
    return real, link, other


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell und Symlinks")
def test_the_command_resolves_each_root_in_its_first_lines(tmp_path):
    real, link, _other = _symlink_setup(tmp_path)
    a_file = real / "file.txt"
    a_file.write_text("x")
    odd = tmp_path / "odd\nname"
    odd.mkdir()
    gone = tmp_path / "gone"
    roots = [str(link), str(a_file), str(odd), str(gone)]
    mark = av.new_rc_mark()
    for cmd in (av.build_scan_command(roots, mark=mark), av.build_watch_command(roots, minutes=5, mark=mark)):
        _light_clamav(tmp_path)
        out = _run_raw(tmp_path, cmd)
        label = "@@scan-roots-" + mark[len("@@scan-rc-"):-1] + "="
        # Ein Pfad mit Zeilenumbruch ergibt eine leere Zeile; ein fehlender Pfad bleibt, wie er ist (`readlink -f`).
        assert out.split("\n")[:4] == [f"{label}{real.resolve()}", f"{label}{a_file.resolve()}", label, f"{label}{gone}"], out[:600]


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell und Symlinks")
@pytest.mark.parametrize("kind", ["scan", "watch"])
def test_a_root_that_is_a_symlink_is_scanned_and_its_finding_stays_reliable(tmp_path, kind):
    """ClamAV 1.0 meldet den aufgeloesten Pfad (`/data/home/...` statt `/home/...`). Vorher galt so ein Fund als unsicher
    und kam nie automatisch in Quarantaene; der Waechter ging an einer Wurzel, die selbst ein Link ist, sogar ohne
    Meldung vorbei (`find` folgt ihr ohne `-H` nicht)."""
    real, link, _other = _symlink_setup(tmp_path)
    (real / "evil.sh").write_text("EICAR")
    (tmp_path / "log.resolve").touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if kind == "scan":
        # `clamscan -r <Wurzel>` wie ClamAV 1.0: aufgeloester Pfad, Zusammenfassung
        (bin_dir / "clamscan").write_text(
            "#!/bin/sh\nfor a in \"$@\"; do last=\"$a\"; done\n"
            "hits=$(find -L \"$last\" -type f | while read -r f; do grep -q EICAR \"$f\" && "
            "printf '%s: Win.Test.EICAR_HC-1 FOUND\\n' \"$(readlink -f \"$f\")\"; done)\n"
            "[ -n \"$hits\" ] && printf '%s\\n\\nScanned files: 1\\nInfected files: 1\\n' \"$hits\" && exit 1\n"
            "echo 'Scanned files: 1'; exit 0\n")
        (bin_dir / "clamscan").chmod(0o755)
        mark = av.new_rc_mark()
        out = _run_raw(tmp_path, av.build_scan_command([str(link)], mark=mark))
    else:
        _clamav_like(tmp_path)
        mark = av.new_rc_mark()
        out = _run_raw(tmp_path, av.build_watch_command([str(link)], minutes=5, mark=mark))
    res = av.parse_scan_output(out, mark, roots=[str(link)])
    assert (res.status, res.unreliable, res.findings) == (
        "infected", False, [(str(real.resolve() / "evil.sh"), "Win.Test.EICAR_HC-1")]), out
    # Ohne die Zeilen der Shell (alter Befehl) waere derselbe Fund unsicher gewesen
    assert av.parse_scan_output(out.split("\n", 1)[1], mark, roots=[str(link)]).unreliable is True


@pytest.mark.skipif(sys.platform == "win32", reason="braucht eine echte POSIX-Shell und Symlinks")
def test_a_link_inside_the_root_to_another_folder_never_makes_its_findings_reliable(tmp_path):
    real, _link, other = _symlink_setup(tmp_path)
    (other / "secret.sh").write_text("EICAR")
    (real / "inner").symlink_to(other)
    (tmp_path / "log.resolve").touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "clamscan").write_text(
        "#!/bin/sh\nfor a in \"$@\"; do last=\"$a\"; done\n"
        "hits=$(find -L \"$last\" -type f | while read -r f; do grep -q EICAR \"$f\" && "
        "printf '%s: Win.Test.EICAR_HC-1 FOUND\\n' \"$(readlink -f \"$f\")\"; done)\n"
        "[ -n \"$hits\" ] && printf '%s\\n\\nScanned files: 1\\nInfected files: 1\\n' \"$hits\" && exit 1\n"
        "echo 'Scanned files: 1'; exit 0\n")
    (bin_dir / "clamscan").chmod(0o755)
    mark = av.new_rc_mark()
    out = _run_raw(tmp_path, av.build_scan_command([str(real)], mark=mark))
    res = av.parse_scan_output(out, mark, roots=[str(real)])
    assert res.status == "infected" and res.findings[0][0] == str(other.resolve() / "secret.sh"), out
    assert res.unreliable is True


# --- Defender: Obergrenze im Ablauf -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_capped_watch_run_without_findings_is_shown_as_an_error():
    out = f"Scanned files: 2000\nSkipped files: 7\n{MARK}0\n"
    ctx = _FakeCtx(None, {"--file-list": out})
    scans, findings = await _scan_once(ctx, "watch")
    # Zwei gedeckelte Laeufe nacheinander: eine Zeile (der zweite ersetzt den ersten), nicht zwei.
    assert [(s["status"], s["files_scanned"]) for s in scans] == [("error", 2000)]
    assert "2000 von 2007" in scans[0]["error"] and findings == []
    assert not ctx.notes  # keine Push-Meldung fuer einen unvollstaendigen, sonst sauberen Lauf


CAPPED = f"Scanned files: 2000\nSkipped files: 7\n{MARK}0\n"
CLEAN_WATCH = f"Scanned files: 4\nInfected files: 0\n{MARK}0\n"


async def _watch_runs(ctx, steps):
    """Fuehrt nacheinander Laeufe aus (je Schritt: (Server, Art, Ausgabe der Attrappe)) und gibt alle Scan-Zeilen zurueck,
    auch die sauberen des Waechters, aelteste zuerst."""
    from nodvard_deck_ext_nexus_soc.defender import Defender
    from nodvard_deck_ext_nexus_soc.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx._sm = async_sessionmaker(engine, expire_on_commit=False)
    defender = Defender(ctx)
    for host, kind, out in steps:
        ctx.outputs = {"--file-list": out, "clamscan -r": out}
        await defender.start_scans([host], kind, paths=None, trigger="schedule")
        await _wait_idle(defender, host.id, kind)
    rows = list(reversed(await defender.list_scans(include_watch=True)))
    await engine.dispose()
    return rows


@pytest.mark.asyncio
async def test_many_capped_watch_runs_in_a_row_leave_one_line_per_server():
    """Bei dauerhaft vielen neuen Dateien war jeder Waechter-Lauf eine eigene Fehlerzeile (bis 144 am Tag und Server);
    die Liste zeigt nur 100 Zeilen, echte Schnell- und Tiefenscans fielen heraus."""
    from nodvard_sdk import Host

    ctx = _FakeCtx(None, {})
    h1, h2 = ctx._host, Host(id="h2", name="pve2", display_name="pve2", address="10.0.0.3")
    rows = await _watch_runs(ctx, [(h1, "quick", CLEAN_WATCH)] + [(h1, "watch", CAPPED)] * 5 + [(h2, "watch", CAPPED)] * 2)
    assert [(r["host_id"], r["kind"], r["status"]) for r in rows] == [
        ("h1", "quick", "clean"), ("h1", "watch", "error"), ("h2", "watch", "error")]
    assert all("2000 von 2007" in r["error"] for r in rows[1:])


@pytest.mark.asyncio
async def test_a_clean_run_a_finding_or_another_error_between_capped_runs_keeps_both_lines():
    ctx = _FakeCtx(None, {})
    hit = f"/tmp/evil.sh: {EICAR} FOUND\nScanned files: 5\nInfected files: 1\n{MARK}1\n"
    broken = f"ERROR: Malformed database\n{MARK}2\n"
    h1 = ctx._host
    rows = await _watch_runs(ctx, [
        (h1, "watch", CAPPED), (h1, "watch", CLEAN_WATCH), (h1, "watch", CAPPED),   # sauber dazwischen: zwei Zeilen
        (h1, "watch", hit), (h1, "watch", CAPPED),                                  # Fund davor bleibt stehen
        (h1, "watch", broken), (h1, "watch", CAPPED),                               # anderer Fehler davor bleibt stehen
    ])
    assert [r["status"] for r in rows] == ["error", "clean", "error", "infected", "error", "error", "error"]
    assert [bool(r["error"] and r["error"].startswith("Der Wächter hat nur")) for r in rows] == [
        True, False, True, False, True, False, True]


@pytest.mark.asyncio
async def test_a_capped_watch_run_only_ever_replaces_a_watch_line():
    """Selbst eine Zeile mit demselben Text (hier: ein Scan, der nicht der Waechter ist) bleibt stehen."""
    ctx = _FakeCtx(None, {})
    h1 = ctx._host
    rows = await _watch_runs(ctx, [(h1, "quick", CAPPED), (h1, "watch", CAPPED)])
    assert [(r["kind"], r["status"]) for r in rows] == [("quick", "error"), ("watch", "error")]


@pytest.mark.asyncio
async def test_a_finding_in_a_capped_watch_run_is_not_quarantined_automatically_and_says_why():
    out = f"/tmp/evil.sh: {EICAR} FOUND\nScanned files: 2000\nInfected files: 1\nSkipped files: 7\n{MARK}1\n"
    ctx = _FakeCtx(None, {"--file-list": out, "mv -f": "@@mode=755\n"})
    scans, findings = await _scan_once(ctx, "watch")
    assert not any("mv -f" in c for c in ctx.commands), ctx.commands
    assert [(f["path"], f["status"]) for f in findings] == [("/tmp/evil.sh", "detected")]
    assert findings[0]["note"] is None  # der Pfad ist gesichert, nur der Lauf war unvollstaendig
    assert "2000 von 2007" in scans[0]["error"] and "nichts automatisch verschoben" in scans[0]["error"]
    assert "2000 von 2007" in ctx.notes[0].body and "nichts automatisch verschoben" in ctx.notes[0].body
    assert "nicht eindeutig lesbar" not in ctx.notes[0].body


@pytest.mark.asyncio
async def test_a_finding_in_a_capped_run_with_an_unsafe_path_names_both_reasons():
    """Obergrenze und Sondername zugleich: Hinweis am Scan und in der Push-Meldung nennen beides, der Fund bekommt die Notiz
    zum unsicheren Pfad, und nichts wird verschoben."""
    from nodvard_deck_ext_nexus_soc.defender import UNRELIABLE_NOTE

    out = (f"/tmp/evil.sh: {EICAR} FOUND\nScanned files: 2000\nInfected files: 1\n"
           f"Unusual file names: 1\nSkipped files: 7\n{MARK}1\n")
    ctx = _FakeCtx(None, {"--file-list": out, "mv -f": "@@mode=755\n"})
    scans, findings = await _scan_once(ctx, "watch")
    assert not any("mv -f" in c for c in ctx.commands), ctx.commands
    assert [(f["path"], f["status"], f["note"]) for f in findings] == [("/tmp/evil.sh", "detected", UNRELIABLE_NOTE)]
    for text in (scans[0]["error"], ctx.notes[0].body):
        assert "2000 von 2007" in text and "nicht eindeutig lesbar" in text and "nichts automatisch verschoben" in text
    assert scans[0]["status"] == "infected"
