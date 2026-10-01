#!/usr/bin/env python3
"""Waechter fuer die Umbenennung Lattice -> Nodvard Deck: der alte Name `lattice` kommt nicht zurueck.

Das Projekt hiess `lattice` (Python-Pakete `lattice` und `lattice_sdk`). Seit der Umbenennung heissen sie
`nodvard_deck` und `nodvard_sdk`; die alten Namen leben nur noch als winzige Alias-Pakete (`backend/src/lattice/`,
`sdk/python/lattice_sdk/`), damit Fremd-Extensions weiter laufen (`sdk/python/_nodvard_alias.py`). Dieser Waechter
sorgt dafuer, dass im eigenen Code niemand versehentlich wieder den alten Namen benutzt.

    python scripts/check_legacy_names.py          # Exit 0 = sauber, 1 = Fund
    python scripts/check_legacy_names.py --list   # alle Funde samt Datei und Zeile, auch die erlaubten (Uebersicht)

**Regel 1 (hart; die Positivliste gilt hier nicht):** kein `import lattice`/`from lattice ...` und kein `lattice_sdk`-Import, auch nicht
als Text in `patch("lattice....")`, `python -m lattice....`, `python -c "import lattice"`, `uvicorn lattice.main:app`,
`getLogger("lattice")` oder `import_module(f"lattice.{name}")`, und die alten Klassennamen
`LatticeExtension`/`LatticeError` stehen nirgends (ausser in den Alias-Dateien und ihren Tests). Dasselbe gilt fuer die alten
Paketnamen der eingebauten Extensions (`lattice_ext_proxmox` heisst jetzt `nodvard_deck_ext_proxmox`): Sie stehen nur in den
Kompatibilitaetstests (`test_shim_*`) und in der Doku; nur der nackte Praefix `"lattice_ext_"` darf in den Tests stehen
(Positivliste, fuer das Aufraeumen von `sys.modules`).

**Regel 2 (Positivliste):** jedes andere Vorkommen von `lattice` bzw. `LATTICE` muss zu einem Eintrag von `KEPT_NAMES`
passen -- den Namen, die **bewusst bleiben** (mit Begruendung dort). Alles andere ist ein Fund. So faellt auf, wenn
jemand einen neuen alten Namen einfuehrt. Wird ein Name spaeter umbenannt (die Eintraege "kommt in PR n" sagen, wann),
fliegt sein Eintrag hier raus, und der Waechter schuetzt das Ergebnis.

Nicht geprueft: Markdown (Doku wird in einem eigenen Schritt umgestellt), `docs/`, `reference/`, `.github/`, gebaute
Bundles (`extensions/*/frontend/dist/`), Lock-Dateien, veroeffentlichte Aenderungsprotokolle (`changelog/versions/`; was
in einer veroeffentlichten Version stand, wird nicht umgeschrieben) und die Alias-Pakete samt ihrer Tests (`test_shim_*`).
Fliesstext "Lattice" mit grossem L (Produktname in Kommentaren) gehoert zur Umbenennung der sichtbaren Namen, nicht
hierher: geprueft werden nur die technischen Schreibweisen `lattice` und `LATTICE`.

Der Waechter laeuft als pytest-Test (`backend/tests/test_legacy_names.py`), also mit `pytest backend/tests` und in der CI.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------------- was nicht geprueft wird

EXEMPT_PREFIXES = (
    # Die Alias-Pakete selbst: sie TRAGEN den alten Namen.
    "backend/src/lattice/",
    "sdk/python/lattice_sdk/",
    "sdk/python/_nodvard_alias.py",
    # Dateien, die die Alias-Namen DEFINIEREN bzw. die Alias-Pakete in die Distribution aufnehmen.
    "sdk/python/nodvard_sdk/__init__.py",  # `LatticeExtension`/`LatticeError` als Aliase (Modul-__getattr__)
    "sdk/python/nodvard_sdk/_legacy.py",
    "sdk/python/nodvard_sdk/errors.py",
    "sdk/python/nodvard_sdk/extension.py",
    "sdk/python/pyproject.toml",  # packages.find: lattice_sdk*
    "backend/pyproject.toml",  # packages.find: lattice*
    "backend/tests/contract/contract_lib.py",  # SDK_PACKAGES: der Vertrags-Schnappschuss prueft auch den alten Importnamen
    # Dieser Waechter und sein Test (sie nennen die alten Namen als Muster und Beispiele).
    "scripts/check_legacy_names.py",
    "backend/tests/test_legacy_names.py",
    # Doku und Fremdmaterial.
    "docs/",
    "reference/",
    ".github/",
    "THIRD_PARTY_LICENSES",
    # Veroeffentlichte Aenderungsprotokolle: was in einer Version stand, bleibt stehen.
    "backend/src/nodvard_deck/changelog/versions/",
    # Der Vertrags-Schnappschuss nennt `lattice_sdk.*` absichtlich: der alte Importname muss dieselbe Oberflaeche
    # liefern, und ein Entfernen faellt dort als Bruch auf (backend/tests/contract/contract_lib.py).
    "backend/tests/contract/sdk_public.json",
)
EXEMPT_SUFFIXES = (".md", ".lock", "package-lock.json", ".png", ".ico", ".svg", ".jpg", ".woff2", ".pdf", ".gz", ".tar", ".whl", ".pyc")
EXEMPT_FILE_RE = re.compile(r"(^|/)test_shim_[^/]*\.py$")  # Tests der Alias-Pakete
EXEMPT_DIST_RE = re.compile(r"^extensions/[^/]+/frontend/dist/")  # gebaute Bundles (kommen aus dem Quellcode)


def is_exempt(rel: str) -> bool:
    return (
        rel.startswith(EXEMPT_PREFIXES)
        or rel.endswith(EXEMPT_SUFFIXES)
        or bool(EXEMPT_FILE_RE.search(rel))
        or bool(EXEMPT_DIST_RE.match(rel))
    )


# --------------------------------------------------------------------------- Regel 1 (hart)

# `import lattice`, `from lattice import ...`, `from lattice.x import ...`, `import lattice_sdk`, `from lattice_sdk...`;
# auch hinter `;` und in Anfuehrungszeichen (`python -c "import lattice"` in Shell-Skripten, `x = 1; import lattice`).
IMPORT_RE = re.compile(r"(?:^|[;\"'`])\s*(?:from|import)\s+lattice(?:_sdk)?(?![\w-])", re.MULTILINE)
# Modulpfade als Text: patch("lattice.core.x"), "lattice.main:app", python -m lattice.migrate, lattice_sdk.x
MODULE_TEXT_RE = re.compile(
    r"\blattice_sdk\b"
    r"|\blattice\.(?:api|boot|admin|branding|changelog|config|core|ext|main|migrate|models|rescue|services|version)\b"
    r"|\blattice\.db\.(?!reverting\b)[a-z_]+"  # Modul unter lattice.db (nicht der Dateiname `lattice.db.reverting`)
    r"|-m\s+[\"']?lattice\b"
    r"|\bsrc/lattice\b(?!_)"
    r"|\bsdk/python/lattice"
    # Die alten Paketnamen der eingebauten Extensions: `lattice_ext_proxmox` heisst jetzt `nodvard_deck_ext_proxmox`.
    r"|\blattice_ext_\w+"
    # Der Paketname als Text fuer Import oder Logger: import_module("lattice"), getLogger("lattice"), auch als f-String.
    r"|\b(?:import_module|__import__|find_spec|getLogger|get_logger)\(\s*[rf]?[\"']lattice(?![\w-])"
    # Zusammengesetzte Modulpfade: f"lattice.{name}", "lattice." + name, "lattice.%s" % name
    # (nicht "lattice.$U": das ist der Dateiname der sudo-Regel, siehe Positivliste)
    r"|[\"']lattice\.(?=[\"'{%])"
)
OLD_CLASS_RE = re.compile(r"\bLattice(?:Extension|Error)\b")

# --------------------------------------------------------------------------- Regel 2 (Positivliste)

TOKEN_RE = re.compile(r"lattice|LATTICE")


@dataclass(frozen=True)
class Kept:
    name: str
    pattern: re.Pattern[str]
    reason: str
    paths: re.Pattern[str] | None = None
    """Nur in Dateien, deren Pfad dazu passt (sonst gilt der Eintrag ueberall)."""

    def applies_to(self, rel: str) -> bool:
        return self.paths is None or bool(self.paths.search(rel))


def _k(name: str, pattern: str, reason: str, paths: str | None = None) -> Kept:
    return Kept(name, re.compile(pattern), reason, re.compile(paths) if paths else None)


# Reihenfolge: lange, spezielle Muster zuerst (die Treffer werden aus der Zeile "ausgeschwaerzt").
KEPT_NAMES: tuple[Kept, ...] = (
    # --- Daten und Datei-/Ordnernamen auf Platte und in Backups -----------------------------------------------------
    _k("Datenbankdatei", r"lattice\.db(?:-wal|-shm|-journal|\.reverting)?\b|lattice-file\.db|lattice-db-snapshot\.db",
       "Die Datenbank heisst weiter `lattice.db` (auch im Sicherungsformat `db/lattice.db`): eine Umbenennung braeuchte eine "
       "Migration, und der Rueckweg aufs alte Image oeffnete eine leere Datenbank. Dazu Beispiel-Dateinamen in Tests."),
    _k("Volume", r"(?:deploy_)?lattice_data\b",
       "Das Docker-Volume `deploy_lattice_data` (Schluessel `lattice_data` in Compose) bleibt: sonst startet das Dashboard leer."),
    # --- Python-Pakete der Extensions --------------------------------------------------------------------------------
    _k("Extension-Paket-Praefix in Test-Aufraeumcode", r"[\"']lattice_ext_[\"']",
       "Die Python-Pakete der eingebauten Extensions heissen `nodvard_deck_ext_<id>`. Der alte Praefix `lattice_ext_` bleibt als "
       "Konvention fuer Fremd-Extensions gueltig (ihr `entrypoint` wird frisch aus der extension.toml gelesen); die Tests, die nach "
       "dem Laden `sys.modules` aufraeumen, kennen deshalb beide Praefixe. Nur der nackte Praefix als Text, nur in den Tests: ein "
       "konkreter alter Paketname (`lattice_ext_proxmox`) ist ein harter Fund (Regel 1).",
       paths=r"^backend/tests/"),
    # --- Umgebungsvariablen -----------------------------------------------------------------------------------------
    _k("Umgebungsvariablen", r"LATTICE_\w+|\bLATTICE_",
       "`LATTICE_*` sind die alten Namen der Einstellungen (Rueckfall: `config.py`, deploy_pi.sh, Compose). Neue Namen: "
       "`NODVARD_DECK_*`; die alten gelten weiter, bis eine angekuendigte Major-Version sie entfernt."),
    _k("Umgebungsvariablen (klein geschrieben)", r"\blattice_env\b",
       "Test: Windows liest Umgebungsvariablen ohne Gross-/Kleinschreibung (`lattice_env` muss wie `LATTICE_ENV` wirken)."),
    # --- Cookie -----------------------------------------------------------------------------------------------------
    _k("Cookie", r"lattice_refresh\b",
       "Der alte Name des Refresh-Cookies bleibt als Rueckfall: der Server schreibt jeden Token in `nodvard_deck_refresh` UND "
       "`lattice_refresh` (gleicher Wert, gleiche Attribute), liest beide und loescht beide beim Abmelden. So meldet der Deploy "
       "niemanden ab, und nach einem Rollback aufs alte Image (es kennt nur `lattice_refresh`) bleibt man angemeldet. Faellt in "
       "einem Aufraeum-PR weg, fruehestens 30 Tage (Laufzeit des Refresh-Tokens) nach dem Deploy und wenn kein Rollback aufs "
       "alte Image mehr noetig ist."),
    # --- Frontend-Vertrag (PR 5) ------------------------------------------------------------------------------------
    _k("Frontend: globales Objekt", r"__lattice\b|\bwindow\.lattice\b|KEY_LATTICE\b",
       "`window.__lattice` ist der Vertrag zwischen Shell und Extension-Bundles; beide Namen laufen parallel (Teil B, PR 5)."),
    _k("Frontend: Ereignis", r"lattice:(?:navigate|chunk-reload-at|timezone|\.\.\.)(?![\w-])|[\"']lattice:[\"']",
       "Browser-Ereignisse und sessionStorage-Schluessel des Frontends (Teil B, PR 5); dazu der alte Praefix `lattice:` "
       "selbst (`deckGlobal.ts`), unter dem Shell und Kit im Uebergang weiter melden bzw. hoeren."),
    _k("Frontend: Speicherschluessel", r"lattice\.(?:changelogSeen|console\.layout)\b",
       "localStorage-Schluessel; `migrateLegacyStorage()` kopiert sie in Teil B, PR 5."),
    _k("Frontend: CSS", r"lattice-(?:focus|glow|grid|pulse)\b",
       "CSS-Klassen; `.lattice-focus` ist Teil des Vertrags mit den Bundles (Teil B, PR 5)."),
    _k("Frontend: Shim-URL", r"lattice-shim\b",
       "URL `/lattice-shim/*.js` (Import-Map fuer die Bundles) bleibt als URL bestehen (Teil B, PR 5)."),
    _k("Frontend: Paketname und Typ", r"lattice-frontend\b|@lattice/extension-sdk\b|\bLatticeTokenRefreshResult\b|\blattice\.(?:refreshAccessToken(?:Result)?|getAccessToken|navigateEvents|hasPermission|confirmDialog|promptDialog|timezone|fetch|React\w*)\b",
       "Das Shell-Objekt heisst im Frontend-Code lokal `lattice`, der TypeScript-Typ `LatticeTokenRefreshResult`, das npm-Paket "
       "`lattice-frontend` (Teil B, PR 5)."),
    # --- Namen auf anderen Rechnern und im Container ----------------------------------------------------------------
    _k("Linux-Benutzer im Container und SSH-Benutzer auf verwalteten Servern", r"lattice:lattice\b|lattice@|!lattice\b|/etc/sudoers\.d/lattice[-.]|lattice[-.]\$U|lattice-\{",
       "Der Linux-Benutzer `lattice` (UID 1000) im Container bleibt, ebenso der Standard-SSH-Benutzer `lattice` auf den verwalteten "
       "Servern (Schluesselkommentar `lattice@<Name>`, sudoers-Datei `lattice-<Benutzer>`, Proxmox-Token `...!lattice`, "
       "Proxmox-Snapshot `lattice-<Zeitstempel>`): sie stehen auf fremden Rechnern, dort aendern wir nichts."),
    _k("Systemd-Einheit des alten Dienstes", r"lattice(?:-backend)?\.service\b",
       "Die alte Dienst-Einheit `lattice.service` bleibt in der Sperrliste der system-Extension (das Dashboard darf sich nicht "
       "selbst anhalten, auch nicht unter dem alten Namen)."),
    _k("Entry-Point-Gruppe", r"lattice\.extensions\b",
       "Die alte Entry-Point-Gruppe `lattice.extensions` wird weiter gelesen (neu: `nodvard_deck.extensions`); Fremd-Extensions "
       "tragen sie noch ein."),
    _k("Namen auf verwalteten Servern", r"lattice-(?:rollback|image-updates|upgrade-|backup\w*|sudo|pi-load\w*|pi-loadtest\w*|tools|test-|ext-bundle-check-)\w*|lattice_ed25519\w*|\.lattice-export|lattice-public|lattice-name|lattice-hub|lattice-previous|lattice-eintr\w*|\ba-lattice\b|\bmylattice\b|x-lattice-file-entry",
       "Namen auf fremden Hosts (Rollback-Tags, Zustandsordner der Image-Updates, systemd-Einheiten `lattice-upgrade-*`) und "
       "Skript-Ordner/-Dateien; Fortsetzung und Rollback haengen daran."),
    _k("Deploy-Namen", r"(?:deploy-)?lattice-(?:1|deploy-test)\b|lattice-deploy-test\b|lattice\.tar\b|\blattice:(?:latest|previous|pi-[\w-]+|dev|1(?:\.2)?|timezone|\*)",
       "Der alte Container `deploy-lattice-1`, die alten Image-Tags `lattice:latest`/`lattice:previous` (gestoppt bzw. "
       "gesichert, bis der Aufraeum-Schritt sie entfernt) und der Zielordner `~/lattice-deploy-test` (`DEPLOY_ROOT`) bleiben."),
    _k("Alter Image-Name", r"/lattice\b(?![\w.-])|\blattice\b(?=/)",
       "Der alte Image-Name `lattice` (`nico/lattice`, `docker.io/library/lattice`): die Service-Matrix erkennt das Dashboard "
       "unter dem alten und dem neuen Namen und bekommt dafuer Beispielwerte in den Tests.",
       paths=r"service[-_]matrix"),
    _k("Einzelnes Wort als Wert", r"(?<![\w.])lattice(?![\w@:-]|\.\w|/\w)",
       "Das einzelne Wort `lattice` als Wert: Linux-/SSH-Benutzer, Host-Markierung (Tag), alter Image-Name, Beispielname in "
       "Tests, API-Wert `source: \"lattice\"` (dokumentierter Altwert, bleibt) und Suchwort. Imports faengt Regel 1 vorher ab."),
)


# --------------------------------------------------------------------------- Dateien einsammeln


def tracked_files(root: Path = REPO_ROOT) -> list[str]:
    """Alle Dateien im Git-Arbeitsbaum (versioniert oder neu und nicht ignoriert); ohne Git: Ordner durchlaufen."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, check=True,
        ).stdout.decode("utf-8", "replace")
        return sorted({p for p in out.split("\0") if p})
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", "node_modules", ".venv", "__pycache__", "dist", "build", ".pytest_cache"}
        return sorted(
            str(p.relative_to(root)).replace("\\", "/")
            for p in root.rglob("*")
            if p.is_file() and not (set(p.relative_to(root).parts[:-1]) & skip) and not p.name.endswith(".egg-info")
        )


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


# --------------------------------------------------------------------------- Pruefen


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str  # "import" | "modul" | "klasse" | "name"
    text: str
    kept: str = ""  # Name der Positivliste, wenn erlaubt (nur --list)

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.text.strip()[:200]}"


def mask_kept(line: str, rel: str = "") -> str:
    """Zeile, in der alle Positivlisten-Treffer durch Leerzeichen ersetzt sind."""
    for kept in KEPT_NAMES:
        if kept.applies_to(rel):
            line = kept.pattern.sub(lambda m: " " * len(m.group(0)), line)
    return line


def kept_names_in(line: str, rel: str = "") -> list[str]:
    found = []
    for kept in KEPT_NAMES:
        if not kept.applies_to(rel):
            continue
        if kept.pattern.search(line):
            found.append(kept.name)
        line = kept.pattern.sub(lambda m: " " * len(m.group(0)), line)
    return found


def check_text(rel: str, text: str) -> list[Finding]:
    """Fuer eine Datei (Pfad relativ zum Repo, Inhalt): alle Verstoesse."""
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not TOKEN_RE.search(line) and "Lattice" not in line:
            continue
        if IMPORT_RE.search(line):
            findings.append(Finding(rel, lineno, "import", line))
            continue
        if OLD_CLASS_RE.search(line):
            findings.append(Finding(rel, lineno, "klasse", line))
            continue
        if MODULE_TEXT_RE.search(line):
            findings.append(Finding(rel, lineno, "modul", line))
            continue
        if TOKEN_RE.search(mask_kept(line, rel)):
            findings.append(Finding(rel, lineno, "name", line))
    return findings


def find_violations(root: Path = REPO_ROOT, files: list[str] | None = None) -> list[Finding]:
    out: list[Finding] = []
    for rel in files if files is not None else tracked_files(root):
        if is_exempt(rel):
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        out += check_text(rel, text)
    return out


def list_kept(root: Path = REPO_ROOT) -> list[Finding]:
    """Alle erlaubten Vorkommen (Uebersicht fuer `--list`)."""
    out: list[Finding] = []
    for rel in tracked_files(root):
        if is_exempt(rel):
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if TOKEN_RE.search(line):
                names = kept_names_in(line, rel)
                if names:
                    out.append(Finding(rel, lineno, "bleibt", line, kept=", ".join(names)))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--list", action="store_true", help="auch die erlaubten Vorkommen (Positivliste) auflisten")
    args = parser.parse_args(argv)

    if not (REPO_ROOT / "backend" / "src" / "nodvard_deck").is_dir():
        print(f"FEHLER: {REPO_ROOT} sieht nicht wie das Repository aus.", file=sys.stderr)
        return 2

    findings = find_violations()
    if args.list:
        kept = list_kept()
        for item in kept:
            print(f"{item.path}:{item.line}: [{item.kept}] {item.text.strip()[:160]}")
        print(f"\n{len(kept)} erlaubte Vorkommen.\n")
    if not findings:
        print("OK: Der alte Name `lattice` kommt ausserhalb der Alias-Pakete und der Positivliste nicht vor.")
        return 0

    print("Der alte Name `lattice` ist (wieder) im Code:\n")
    for item in findings:
        print(f"  {item}")
    print(
        f"\n{len(findings)} Fund(e). Der Kern heisst `nodvard_deck`, das SDK `nodvard_sdk` (`NodvardExtension`, `NodvardError`). "
        "Gehoert der Name wirklich zu den bewusst belassenen (Daten, Namen auf fremden Rechnern, Frontend-Vertrag), "
        "gehoert er mit Begruendung in KEPT_NAMES in scripts/check_legacy_names.py."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
