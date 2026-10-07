#!/usr/bin/env python3
"""Waechter fuer die Umbenennungen Lattice -> Nodvard Deck und `nexus-soc` -> `shield`: die alten Namen kommen nicht zurueck.

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

**Regel 3 (alte Shield-Kennung):** Nodvard Shield hiess bis 0.6 technisch `nexus-soc` (Paket `nodvard_deck_ext_nexus_soc`,
Aktionsarten `nexus_soc.*`); seit 0.7 heisst die Kennung `shield`. Zwei harte Verbote ohne Positivliste: das Paket
`nodvard_deck_ext_nexus_soc` steht nirgends, und die alten Adressen `/ext/nexus-soc` und `/extensions/nexus-soc` stehen nur in
Testdateien mit "legacy" im Namen (den Alias-Tests: `test_*legacy*.py`, `*Legacy*.test.ts(x)`). Jedes andere Vorkommen der klein
geschriebenen technischen Schreibweise (`nexus`, `nexus-soc`, `nexus_soc`, `nexus-quarantine` ...) muss zu einem Eintrag von
`KEPT_SHIELD` passen: Namen, die bewusst bleiben, weil sie in der Datenbank oder auf den Servern liegen und ein Rueckweg aufs alte
Image daran haengt (Tabellen, Aktionsarten, Geheimnis-Label, Server-Ordner). Anders als bei Regel 2 nennt jeder Eintrag die
Dateien, in denen er gilt (`where`); ein Test sorgt dafuer, dass jede dieser Angaben noch gebraucht wird. Nicht geprueft:
`docs/`, `reference/`, Markdown, veroeffentlichte Aenderungsprotokolle, gebaute Bundles, die beiden Vertrags-Schnappschuesse
`api_v1.json` und `alembic_revisions.json`, der eingefrorene Migrationsordner `extensions/shield/migrations/` (eine
ausgelieferte Revision aendert sich nie) sowie die Waechter selbst und ihre Tests. Der Produktname in Grossschreibung
(Fliesstext) gehoert zu `backend/tests/test_no_visible_nexus.py`.

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
    where: tuple[str, ...] = ()
    """Bei `KEPT_SHIELD`: die einzelnen Pfad-Muster, aus denen `paths` zusammengesetzt ist (jedes muss noch gebraucht werden)."""

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
    _k("Container-Benutzer und alter Schluesselkommentar und Proxmox-Namen", r"lattice:lattice\b|lattice@|!lattice\b|lattice-\{",
       "Der Linux-Benutzer `lattice` (UID 1000) im Container bleibt. Auf den verwalteten Servern heissen aeltere Zugaenge weiter "
       "`lattice`, und aeltere Schluessel tragen dort den Kommentar `lattice@<Name>`: neue Schluessel bekommen `nodvard@<Name>`, "
       "der Einrichtungsbefehl erkennt alte Eintraege aber an Art und Schluesseltext, nicht am Kommentar (die Tests pruefen "
       "genau das mit `lattice@...`). Dazu das Proxmox-Token `...!lattice` und der Proxmox-Snapshot `lattice-<Zeitstempel>`: "
       "sie stehen auf fremden Rechnern, dort aendern wir nichts."),
    _k("Alte sudo-Regel auf verwalteten Servern", r"/etc/sudoers\.d/lattice[-.]|lattice[-.]\$U|lattice-<Benutzer>|\blattice-lattice\b",
       "Neue Einrichtungen legen `/etc/sudoers.d/nodvard-<Benutzer>` an. Die alte Regel `/etc/sudoers.d/lattice-<Benutzer>` liegt auf "
       "bestehenden Servern und bleibt dort gueltig; der Einrichtungsbefehl entfernt sie nur, wenn sie genau der Regel entspricht, "
       "die Nodvard Deck frueher selbst angelegt hat, sonst bleibt sie mit Hinweis stehen. Deshalb nennen der Befehl (`$O`) und seine "
       "Tests den alten Dateinamen weiter (`lattice-lattice` = alte Regel fuer den Benutzer `lattice`). Nur dort: neuer Code "
       "darf keine Regel unter dem alten Namen anlegen.",
       paths=r"^backend/(?:src/nodvard_deck/services/host_setup\.py|tests/test_host_setup_script\.py)$"),
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


# --------------------------------------------------------------------------- Regel 3 (alte Shield-Kennung)

SHIELD_PACKAGE_RE = re.compile(r"nodvard_deck_ext_nexus_soc")
SHIELD_ADDRESS_RE = re.compile(r"/(?:ext|extensions)/nexus-soc(?![\w-])")
SHIELD_TOKEN_RE = re.compile(r"nexus|(?i:nexus[-_]soc)")
"""Alles, was nach der alten Kennung aussieht: klein geschriebenes `nexus` (technische Schreibweise) und `nexus_soc` in jeder
Gross-/Kleinschreibung. Den Produktnamen in Grossschreibung pruefen `test_no_visible_nexus.py` und die Sperrliste des Kerns."""

# Alias-Tests: Dateien, die das Verhalten der alten Kennung ausdruecklich pruefen (Weiterleitung, Namen, Rueckweg). Nur dort darf
# die alte Adresse stehen.
_LEGACY_TEST = r"(?:tests/(?:[^/]+/)*test_[^/]*legacy[^/]*\.py|[^/]*[Ll]egacy[^/]*\.test\.tsx?)"
LEGACY_TEST_RE = re.compile(rf"(?:^|/){_LEGACY_TEST}$")
LEGACY_TEST_WHERE = rf"(?:^|/){_LEGACY_TEST}$"

SHIELD_EXEMPT_PREFIXES = (
    "docs/",
    "reference/",
    # Veroeffentlichte Aenderungsprotokolle: was in einer Version stand, bleibt stehen.
    "backend/src/nodvard_deck/changelog/versions/",
    # Eingefroren: eine ausgelieferte Alembic-Revision aendert sich nie (Dateiname, Branch-Label und Tabellen bleiben alt).
    "extensions/shield/migrations/",
    # Schnappschuesse des Vertrags (gespeicherte Namen, Branch-Label); sie entstehen aus dem Code (scripts/update_api_contract.py).
    "backend/tests/contract/api_v1.json",
    "backend/tests/contract/alembic_revisions.json",
    # Die Waechter selbst und ihre Tests: sie nennen die alten Namen als Muster und Beispiele.
    "scripts/check_legacy_names.py",
    "backend/tests/test_legacy_names.py",
    "scripts/check_core_purity.py",  # Sperrliste des Kerns: das Wort steht dort als verbotenes Wort
    "backend/tests/test_no_visible_nexus.py",  # sucht das Wort in sichtbaren Texten
)


def is_shield_exempt(rel: str) -> bool:
    return (
        rel.startswith(SHIELD_EXEMPT_PREFIXES)
        or rel.endswith(EXEMPT_SUFFIXES)  # Markdown, Lock-Dateien, Bilder ...
        or bool(EXEMPT_DIST_RE.match(rel))  # gebaute Bundles
    )


def _ks(name: str, pattern: str, reason: str, where: list[str]) -> Kept:
    """Eintrag der Positivliste fuer Regel 3: Pfad-Angaben sind Pflicht, `paths` ist ihre Vereinigung."""
    paths = "|".join(f"(?:{w})" for w in where)
    return Kept(name, re.compile(pattern), reason, re.compile(paths), tuple(where))


def _f(*paths: str) -> list[str]:
    """Je Datei ein Pfad-Muster (so laesst sich pruefen, dass jede Datei der Liste ihren Eintrag noch braucht)."""
    return ["^" + re.escape(p) + "$" for p in paths]


_SHIELD_SRC = r"^extensions/shield/src/nodvard_deck_ext_shield/"
_SHIELD_OWN_TESTS = r"^backend/tests/test_ext_shield(?:_\w+)?\.py$"

# Reihenfolge wie bei KEPT_NAMES: lange, spezielle Muster zuerst.
KEPT_SHIELD: tuple[Kept, ...] = (
    _ks("Shield: Geheimnis-Label",
        r"nexus-soc-ollama-key|secrets\.read:nexus-soc-\*",
        "Der API-Schluessel fuer Nodvard KI liegt im Tresor unter dem Label `nexus-soc-ollama-key`; das Recht dazu steht im Manifest als "
        "`secrets.read:nexus-soc-*`. Das Geheimnis wird nur ueber sein Label gefunden: ein neues Label hiesse, der Schluessel ist weg, und "
        "das alte Image faende ihn nach einem Rueckweg nicht.",
        [*_f("extensions/shield/extension.toml", "extensions/shield/settings.schema.json",
            "extensions/shield/src/nodvard_deck_ext_shield/ollama.py",
            "frontend/src/preview/fixtures.ts",  # Vorschau-Testdaten: spiegeln das Schema
            "backend/tests/test_ext_secret_binding.py", "backend/tests/test_shield_legacy_ids.py")]),
    _ks("Shield: Tabellen und Indizes",
        r"(?:ix_)?ext_nexus_soc_\w*",
        "Die Tabellen `ext_nexus_soc_*` (8) und ihre Indizes `ix_ext_nexus_soc_*` behalten ihren Namen: Umbenennen braeuchte eine "
        "Migration, und dann ginge der Rueckweg aufs alte Image nur ueber die Notseite oder eine Kopie mit Datenverlust "
        "(wie bei `lattice.db`).",
        [_SHIELD_SRC, _SHIELD_OWN_TESTS, *_f("backend/tests/test_migrate.py", "backend/tests/test_shield_legacy_ids.py")]),
    _ks("Shield: Aktionsarten",
        r"nexus_soc\\?\.(?:restore|delete|install|upgrade|reboot|ban|\*)|ACTION_PREFIX\s*==?\s*\"nexus_soc\"",
        "Die Aktionsarten `nexus_soc.{restore,delete,install,upgrade,reboot,ban}` stehen in gespeicherten Vorschlaegen und Freigaben, "
        "und das Gate findet den Ausfuehrer nur ueber die Art. So bleiben offene Vorschlaege ueber das Update und einen Rueckweg "
        "ausfuehrbar; die Oberflaeche zeigt das Label der `ActionSpec` statt der Art. Der Name steht an einer Stelle (`ACTION_PREFIX` in "
        "`ids.py`), Tests nennen ihn als Eingabe und Erwartung.",
        [*_f("extensions/shield/src/nodvard_deck_ext_shield/ids.py", "frontend/src/lib/actionNames.test.ts",
            "frontend/src/routes/ActionsPage.test.tsx"),
         _SHIELD_OWN_TESTS, LEGACY_TEST_WHERE]),
    _ks("Shield: alte Audit-Namen",
        r"nexus_soc\.(?:incident(?:_status)?|proposal_rejected|\$\{\w+\}|<Vorgang>|\*)",
        "Aeltere Eintraege im Protokoll (`audit_log`) heissen `nexus_soc.incident`, `nexus_soc.incident_status` und "
        "`nexus_soc.proposal_rejected` und werden nicht umgeschrieben (Verlauf). Neue Eintraege heissen `shield.*`; nur die Beschriftung der "
        "Container-Wache kennt beide Namen.",
        [*_f("extensions/shield/frontend/src/ContainerWatch.tsx", "extensions/shield/frontend/src/ContainerWatch.test.tsx")]),
    _ks("Shield: Server-Ordner",
        r"lib\\?/nexus-(?:quarantine|updates)\b",
        "Auf den verwalteten Servern liegen `/var/lib/nexus-quarantine` (Quarantaene) und `/var/lib/nexus-updates` (laufende Updates). Der "
        "volle Pfad steht in `ext_nexus_soc_findings.quarantine_path`, die Loesch-Pruefung und die Wiederaufnahme laufender Updates "
        "haengen am Ordner: ein Umzug gaebe falsche \"geloescht\"-Faelle und abgebrochene Laeufe.",
        [*_f("extensions/shield/src/nodvard_deck_ext_shield/antivirus.py", "extensions/shield/src/nodvard_deck_ext_shield/detached.py",
             "backend/tests/test_shield_legacy_ids.py", "extensions/shield/frontend/src/SocPage.test.tsx"),
         _SHIELD_OWN_TESTS]),
    _ks("Shield: alte feste Scan-Marke",
        r"nexus-rc\b",
        "Frueher trug jeder Scan die feste Rueckgabecode-Marke `@@nexus-rc=`; ein Dateiname mit dieser Marke konnte einen Fund "
        "verstecken. Heute gilt je Lauf eine Zufallsmarke. Die Tests nennen die alte Marke als abgewehrte Eingabe und als Rest in "
        "gespeicherten Laeufen (die Oberflaeche blendet ihn aus).",
        [*_f("backend/tests/test_ext_shield_antivirus.py", "extensions/shield/frontend/src/SocPage.test.tsx")]),
    _ks("Shield: alter Branch-Label und Ordner im Alembic-Test",
        r"nexus-soc(?![\w-])",
        "Die Alembic-Revisionen der Erweiterung behalten Dateinamen und `branch_labels=(\"nexus-soc\",)`: ausgelieferte Revisionen aendern sich "
        "nie, und ein zweites Label wuerde alle Label-Mengen des Vertrags aendern. Die Tests nennen Label und den alten Ordner "
        "(`VERSCHOBEN`), um das festzuhalten.",
        [*_f("backend/tests/contract/test_alembic_revisions.py", "backend/tests/test_migrate.py")]),
    _ks("Shield: Manifest",
        r"legacy_ids\s*=\s*\[\"nexus-soc\"\]",
        "Das Manifest nennt die alte Kennung als `legacy_ids`: Darueber findet der Kern die Registry-Zeile einer bestehenden "
        "Installation (dort liegen die Einstellungen), haengt die alten Adressen als veraltet ein und kennt die Tabellen-Praefixe.",
        [*_f("extensions/shield/extension.toml")]),
    _ks("Shield: Herkunft der Kennung",
        r"nexus-soc(?![\w-])",
        "In `ids.py` und `ids.ts` steht, wie die Erweiterung bis 0.6 hiess: die eine Stelle, an der die Herkunft der Kennung "
        "festgehalten ist. Andere Kommentare verweisen darauf.",
        [*_f("extensions/shield/src/nodvard_deck_ext_shield/ids.py", "extensions/shield/frontend/src/ids.ts")]),
    _ks("Shield: alte Kennung als Wert in den Alias-Tests",
        r"nexus-soc(?![\w-])",
        "Gespeicherte Daten einer Installation mit 0.6 tragen die alte Kennung (Meldungen `source_ext_id`, Protokoll `actor_id`, "
        "Vorschlaege `ext_id`, Registry-Zeile, `legacy_ids` in der API). Die Tests stellen diese Daten nach und pruefen, dass Namen, "
        "Weiterleitung und Rueckweg stimmen. Die alte Adresse darf nur in Dateien mit \"legacy\" im Namen stehen (harte Regel); die "
        "uebrigen Dateien hier nutzen die Kennung nur als Wert.",
        [LEGACY_TEST_WHERE,
         *_f("backend/tests/test_ext_discovery.py", "frontend/src/components/NotificationBell.test.tsx",
            "frontend/src/routes/NotificationsPage.test.tsx", "frontend/src/routes/Cockpit.test.tsx",
            "frontend/src/lib/extensionNames.test.ts", "extensions/shield/frontend/src/ContainerWatch.test.tsx")]),
    _ks("Shield: Herkunftsangabe reference/nexus",
        r"reference/nexus\b",
        "Der Ordner `reference/nexus/` enthaelt das alte Skript, aus dem das Dashboard hervorging (Herkunft, mit echten Infrastrukturdaten); "
        "er ist von der oeffentlichen Ausgabe ausgenommen. Der Test der Ausgabe legt in seinem Wegwerf-Repository Dateien darin an.",
        [*_f("backend/tests/test_export_public.py")]),
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
    rule: str  # "import" | "modul" | "klasse" | "name" (lattice), "shield-paket" | "shield-adresse" | "shield-name" (Regel 3)
    text: str
    kept: str = ""  # Name der Positivliste, wenn erlaubt (nur --list)

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.text.strip()[:200]}"


def mask_kept(line: str, rel: str = "", entries: tuple[Kept, ...] = KEPT_NAMES) -> str:
    """Zeile, in der alle Positivlisten-Treffer durch Leerzeichen ersetzt sind."""
    for kept in entries:
        if kept.applies_to(rel):
            line = kept.pattern.sub(lambda m: " " * len(m.group(0)), line)
    return line


def kept_names_in(line: str, rel: str = "", entries: tuple[Kept, ...] = KEPT_NAMES) -> list[str]:
    found = []
    for kept in entries:
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


def check_shield_text(rel: str, text: str) -> list[Finding]:
    """Regel 3 fuer eine Datei: die alte Shield-Kennung (Pfad relativ zum Repo, Inhalt)."""
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not SHIELD_TOKEN_RE.search(line):
            continue
        if SHIELD_PACKAGE_RE.search(line):
            findings.append(Finding(rel, lineno, "shield-paket", line))
            continue
        if SHIELD_ADDRESS_RE.search(line) and not LEGACY_TEST_RE.search(rel):
            findings.append(Finding(rel, lineno, "shield-adresse", line))
            continue
        if SHIELD_TOKEN_RE.search(mask_kept(line, rel, KEPT_SHIELD)):
            findings.append(Finding(rel, lineno, "shield-name", line))
    return findings


def find_violations(root: Path = REPO_ROOT, files: list[str] | None = None) -> list[Finding]:
    """Alle Verstoesse gegen Regel 1 und 2 (`lattice`) und Regel 3 (alte Shield-Kennung)."""
    out: list[Finding] = []
    for rel in files if files is not None else tracked_files(root):
        check_lattice, check_shield = not is_exempt(rel), not is_shield_exempt(rel)
        if not (check_lattice or check_shield):
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        if check_lattice:
            out += check_text(rel, text)
        if check_shield:
            out += check_shield_text(rel, text)
    return out


def list_kept(root: Path = REPO_ROOT) -> list[Finding]:
    """Alle erlaubten Vorkommen (Uebersicht fuer `--list`): Regel 2 mit Art "bleibt", Regel 3 mit Art "shield-bleibt"."""
    out: list[Finding] = []
    for rel in tracked_files(root):
        check_lattice, check_shield = not is_exempt(rel), not is_shield_exempt(rel)
        if not (check_lattice or check_shield):
            continue
        text = read_text(root / rel)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if check_lattice and TOKEN_RE.search(line):
                names = kept_names_in(line, rel)
                if names:
                    out.append(Finding(rel, lineno, "bleibt", line, kept=", ".join(names)))
            if check_shield and SHIELD_TOKEN_RE.search(line):
                names = kept_names_in(line, rel, KEPT_SHIELD)
                if names:
                    out.append(Finding(rel, lineno, "shield-bleibt", line, kept=", ".join(names)))
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
    lattice_findings = [f for f in findings if not f.rule.startswith("shield-")]
    shield_findings = [f for f in findings if f.rule.startswith("shield-")]
    if not findings:
        print("OK: Der alte Name `lattice` kommt ausserhalb der Alias-Pakete und der Positivliste nicht vor.")
        print("OK: Die alte Shield-Kennung `nexus-soc` kommt ausserhalb der Positivliste und der Alias-Tests nicht vor.")
        return 0

    if lattice_findings:
        print("Der alte Name `lattice` ist (wieder) im Code:\n")
        for item in lattice_findings:
            print(f"  {item}")
        print(
            f"\n{len(lattice_findings)} Fund(e). Der Kern heisst `nodvard_deck`, das SDK `nodvard_sdk` (`NodvardExtension`, `NodvardError`). "
            "Gehoert der Name wirklich zu den bewusst belassenen (Daten, Namen auf fremden Rechnern, Frontend-Vertrag), "
            "gehoert er mit Begruendung in KEPT_NAMES in scripts/check_legacy_names.py.\n"
        )
    if shield_findings:
        print("Die alte Kennung von Nodvard Shield (`nexus-soc`) ist (wieder) im Code:\n")
        for item in shield_findings:
            print(f"  {item}")
        print(
            f"\n{len(shield_findings)} Fund(e). Die Erweiterung heisst `shield` (Ordner `extensions/shield`, Paket `nodvard_deck_ext_shield`); "
            "die Kennung steht in `ids.py` bzw. `ids.ts`. Alte Adressen gehoeren nur in Alias-Tests (Dateiname mit \"legacy\"). "
            "Gehoert der Name wirklich zu den bewusst belassenen (Tabellen, Aktionsarten, Geheimnis-Label, Server-Ordner), "
            "gehoert er mit Begruendung und Dateiliste in KEPT_SHIELD in scripts/check_legacy_names.py."
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
