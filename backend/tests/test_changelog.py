"""Änderungsprotokoll: Laden, Prüfen und Versionsnummern im Gleichschritt."""

from __future__ import annotations

import json
import logging
import re
import tomllib
from pathlib import Path

import pytest
from nodvard_deck import __version__, changelog
from nodvard_deck.changelog import ChangelogError

REPO_ROOT = Path(__file__).resolve().parents[2]

ENTRY = '[[entries]]\nkind = "neu"\ntext = "Etwas ist neu."\n'


def _version_file(root: Path, version: str, body: str = ENTRY, *, date: str = "2026-10-01", extra: str = "") -> Path:
    path = root / "versions" / f"{version}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'version = "{version}"\ndate = "{date}"\n{extra}\n{body}', encoding="utf-8")
    return path


def _fragment(root: Path, name: str, body: str = ENTRY) -> Path:
    path = root / "unreleased" / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# --- Die mitgelieferten Daten ---------------------------------------------------------


def test_shipped_files_are_all_valid():
    """Streng geprueft (das Laden selbst waere nachsichtig): ein Tippfehler in einer
    Datei faellt hier in der CI auf statt erst als stiller Eintrag, der im Dashboard fehlt."""
    for path in sorted((changelog.CHANGELOG_DIR / "versions").glob("*.toml")):
        changelog.read_release(path)
    for path in sorted((changelog.CHANGELOG_DIR / "unreleased").glob("*.toml")):
        changelog.read_fragment(path)
    loaded = changelog.load_changelog()
    assert loaded.versions, "es muss mindestens eine Version geben"
    assert len(loaded.versions) == len(list((changelog.CHANGELOG_DIR / "versions").glob("*.toml")))


def test_unreleased_fragments_have_no_audit_numbers():
    for path in (changelog.CHANGELOG_DIR / "unreleased").glob("*.toml"):
        content = path.name + "\n" + path.read_text(encoding="utf-8")
        assert not re.search(r"\bbefund[-_ ]*\d+\b", content, re.IGNORECASE), path.name


def test_fragment_names_have_no_review_labels():
    for path in (changelog.CHANGELOG_DIR / "unreleased").glob("*.toml"):
        assert "befund" not in path.stem.lower(), path.name


def test_all_versions_are_strictly_descending_and_unique():
    versions = [r.version for r in changelog.load_changelog().versions]
    keys = [changelog.parse_version(v) for v in versions]
    assert keys == sorted(set(keys), reverse=True)


def test_git_network_policy_is_documented_once():
    loaded = changelog.load_changelog()
    entries = [*loaded.unreleased, *(entry for release in loaded.versions for entry in release.entries)]
    texts = [entry.text for entry in entries if entry.kind == "sicherheit" and "Git" in entry.text
             and ("Adresse" in entry.text or "Proxy" in entry.text)]
    assert texts == [
        (
            "Stacks aus Git: Nodvard Deck prüft vor dem Holen, wohin der Name des Git-Servers wirklich zeigt, "
            "und verbindet sich nur mit genau dieser Adresse. Deck selbst und die internen Dienste von Cloud-Anbietern "
            "sind immer gesperrt."
        ),
        (
            "Läuft Nodvard Deck über einen Proxy (HTTPS_PROXY), holt es aus Git nur noch von Adressen aus Zahlen, "
            "weil der Proxy einen Namen selbst nachschlägt. Einen Git-Server mit Namen trägst du unter NO_PROXY ein, "
            "dann verbindet sich Deck direkt."
        ),
    ]


@pytest.mark.parametrize("text,name", [
    ("SFTP-Lesezugriffe erlauben", "deck-ssh-freigaben.toml"),
    ("Deck und die Notseite verlangen beim Einbinden", "fix-fremde-inhalte.toml"),
])
def test_unreleased_security_fragments_have_descriptive_names(text, name):
    for path in (changelog.CHANGELOG_DIR / "unreleased").glob("*.toml"):
        if text in path.read_text(encoding="utf-8"):
            assert path.name == name


def test_ssh_release_notes_explain_the_approval_limit():
    loaded = changelog.load_changelog()
    entries = [*loaded.unreleased, *(entry for release in loaded.versions for entry in release.entries)]
    entry = next(entry for entry in entries if "SFTP-Lesezugriffe erlauben" in entry.text)
    assert "keine Sicherheitsgrenze" in entry.text
    assert "geplante Jobs" in entry.text
    assert any("Für Erweiterungsautoren:" in entry.text and "Freigabekontext" in entry.text for entry in entries)


def test_files_view_improvements_are_documented():
    loaded = changelog.load_changelog()
    entries = [*loaded.unreleased, *(entry for release in loaded.versions for entry in release.entries)]
    assert any(
        entry.kind == "verbessert"
        and 695 in entry.prs
        and all(word in entry.text for word in ("Dateiansicht", "Link", "Ordnersymbole"))
        for entry in entries
    )


def test_unreleased_fragments_have_no_review_numbers():
    for path in (changelog.CHANGELOG_DIR / "unreleased").glob("*.toml"):
        assert not re.search(r"befund[\s-]*\d+", path.stem, re.IGNORECASE), path.name
        for entry in changelog.read_fragment(path):
            assert not re.search(r"befund[\s-]*\d+", entry.text, re.IGNORECASE), path.name


def test_documentation_fragments_do_not_replace_each_other(tmp_path):
    texts = ("Dock erklärt den ersten Start.", "Dock erklärt das Sichern.")
    for name, text in zip(("a-start", "b-sicherung"), texts, strict=True):
        _fragment(tmp_path, name, f'[[entries]]\nkind = "dokumentation"\ntext = "{text}"\n')
    entries = changelog.load_changelog(tmp_path).unreleased
    assert entries == tuple(changelog.Entry("dokumentation", text, ()) for text in texts)


def test_security_notice_retry_is_limited_to_missing_delivery():
    loaded = changelog.load_changelog()
    entries = [*loaded.unreleased, *(entry for release in loaded.versions for entry in release.entries)]
    texts = [entry.text for entry in entries if 589 in entry.prs and entry.text.startswith("Sicherheitsupdates werden")]
    assert texts == [
        (
            "Sicherheitsupdates werden bei der nächsten Suche erneut gemeldet, wenn weder eine In-App-Meldung gespeichert "
            "noch eine Meldung erfolgreich über einen Meldungskanal zugestellt wurde."
        )
    ]


def test_extension_permissions_note_describes_changes_to_the_own_list():
    loaded = changelog.load_changelog()
    entries = [*loaded.unreleased, *(entry for release in loaded.versions for entry in release.entries)]
    assert [(entry.kind, entry.text) for entry in entries if 692 in entry.prs] == [
        (
            "sicherheit",
            (
                "Ändert eine Erweiterung nach dem Laden ihre eigene Liste erlaubter Rechte, erhält sie dadurch "
                "keine zusätzlichen Rechte oder Hinweise auf fremde Geheimnisse."
            ),
        )
    ]


def test_git_queries_explain_waiting_across_stacks_and_servers():
    loaded = changelog.load_changelog()
    entries = [*loaded.unreleased, *(entry for release in loaded.versions for entry in release.entries)]
    texts = [entry.text for entry in entries if 688 in entry.prs and entry.text.startswith("Git-Abfragen")]
    assert texts == [
        (
            "Git-Abfragen für Stacks laufen nacheinander. Antwortet ein Git-Server langsam, "
            "warten auch Abfragen für andere Stacks und Server."
        )
    ]


def test_pagination_errors_are_listed_as_bug_fix():
    loaded = changelog.load_changelog()
    entries = [*loaded.unreleased, *(entry for release in loaded.versions for entry in release.entries)]
    fixes = [entry for entry in entries if 685 in entry.prs]
    assert len(fixes) == 1
    assert fixes[0].kind == "behoben"


def test_version_numbers_never_drift():
    """`version.py`, neueste Versionsdatei, `pyproject.toml` und `package.json` (samt
    Lockfile) nennen dieselbe Version. Das Release-Skript hebt alle zusammen an --
    von Hand vergessene Stellen fallen hier auf."""
    newest = changelog.load_changelog().latest
    pyproject = tomllib.loads((REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((REPO_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert newest is not None
    assert __version__ == newest
    assert pyproject["project"]["version"] == newest
    assert package["version"] == newest
    assert lock["version"] == newest
    assert lock["packages"][""]["version"] == newest


def test_changelog_files_are_declared_as_package_data():
    """Ohne diese Zeilen fehlen die Dateien im Wheel, und damit im Docker-Image
    (`pip install ./backend` ist nicht editierbar)."""
    pyproject = tomllib.loads((REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    patterns = package_data["nodvard_deck.changelog"]
    assert "versions/*.toml" in patterns
    assert "unreleased/*.toml" in patterns
    assert (
        any(Path("editions.toml").match(pattern) for pattern in patterns)
        or any(Path("changelog/editions.toml").match(pattern) for pattern in package_data.get("nodvard_deck", ()))
    )


# --- Versionsnummern ------------------------------------------------------------------


def test_parse_version_is_numeric():
    assert changelog.parse_version("0.10.2") == (0, 10, 2)
    assert changelog.parse_version("0.10.0") > changelog.parse_version("0.9.0")
    for bad in ("1.2", "1.2.3.4", "01.2.3", "a.b.c", "v1.2.3", "1.2.3-rc1", "", None, 5):
        with pytest.raises(ChangelogError):
            changelog.parse_version(bad)


def test_versions_sorted_numerically_not_alphabetically(tmp_path):
    for version in ("0.9.0", "0.10.0", "0.2.0", "1.0.0"):
        _version_file(tmp_path, version)
    assert [r.version for r in changelog.load_changelog(tmp_path).versions] == ["1.0.0", "0.10.0", "0.9.0", "0.2.0"]
    assert changelog.load_changelog(tmp_path).latest == "1.0.0"


# --- Laden ----------------------------------------------------------------------------


def test_load_reads_release_fields(tmp_path):
    body = (
        '[[entries]]\nkind = "sicherheit"\ntext = "  Lücke geschlossen.  "\nprs = [17, 13]\n\n'
        '[[entries]]\nkind = "behoben"\ntext = "Zweiter Eintrag."\n'
    )
    _version_file(tmp_path, "0.5.0", body, extra='title = "Ein Titel"')
    release = changelog.load_changelog(tmp_path).versions[0]
    assert (release.version, release.date, release.title) == ("0.5.0", "2026-10-01", "Ein Titel")
    assert release.entries == (
        changelog.Entry("sicherheit", "Lücke geschlossen.", (17, 13)),
        changelog.Entry("behoben", "Zweiter Eintrag.", ()),
    )


def test_title_is_optional_and_toml_dates_are_accepted(tmp_path):
    path = tmp_path / "versions" / "0.5.0.toml"
    path.parent.mkdir(parents=True)
    path.write_text(f'version = "0.5.0"\ndate = 2026-10-01\n\n{ENTRY}', encoding="utf-8")
    release = changelog.load_changelog(tmp_path).versions[0]
    assert (release.date, release.title) == ("2026-10-01", None)


def test_unreleased_fragments_are_merged_in_file_name_order(tmp_path):
    _fragment(tmp_path, "b-zweiter", '[[entries]]\nkind = "behoben"\ntext = "B."\n')
    _fragment(tmp_path, "a-erster", '[[entries]]\nkind = "neu"\ntext = "A1."\n\n[[entries]]\nkind = "neu"\ntext = "A2."\n')
    (tmp_path / "unreleased" / "README.md").write_text("# kein Eintrag\n", encoding="utf-8")
    assert [e.text for e in changelog.load_changelog(tmp_path).unreleased] == ["A1.", "A2.", "B."]


def test_missing_folders_give_an_empty_changelog(tmp_path):
    loaded = changelog.load_changelog(tmp_path / "gibt-es-nicht")
    assert (loaded.versions, loaded.unreleased, loaded.latest) == ((), (), None)


@pytest.mark.parametrize("released", [False, True], ids=["unreleased", "released"])
def test_documentation_fragment_is_valid_and_loaded(tmp_path, released):
    body = '[[entries]]\nkind = "dokumentation"\ntext = "Dock-Anleitungen ergänzt."\n'
    path = _fragment(tmp_path, "dock-doku", body)
    expected = (changelog.Entry("dokumentation", "Dock-Anleitungen ergänzt.", ()),)
    assert changelog.read_fragment(path) == expected
    if released:
        _version_file(tmp_path, "0.1.0", body)
        path.unlink()
    loaded = changelog.load_changelog(tmp_path)
    if released:
        assert loaded.unreleased == ()
        assert loaded.versions[0].entries == expected
    else:
        assert loaded.unreleased == expected


@pytest.mark.parametrize(
    "content, reason",
    [
        ('version = "0.5.0"\ndate = "2026-10-01"\n', "es fehlen"),  # keine Eintraege
        ('version = "0.5"\ndate = "2026-10-01"\n' + ENTRY, "Versionsnummer"),
        ('version = "0.5.0"\ndate = "1.10.2026"\n' + ENTRY, "Datum"),
        ('version = "0.5.0"\ndate = "2026-13-45"\n' + ENTRY, "Datum"),
        ('version = "0.5.0"\ndate = 2026-10-01T12:00:00\n' + ENTRY, "Uhrzeit"),
        ('version = "0.5.0"\ndate = "2026-10-01"\ntitel = "x"\n' + ENTRY, "unbekannte Felder"),
        ('version = "0.5.0"\ndate = "2026-10-01"\ntitle = ""\n' + ENTRY, "title"),
        ('version = "0.5.0"\ndate = "2026-10-01"\n[[entries]]\nkind = "neuigkeit"\ntext = "x"\n', "kind"),
        ('version = "0.5.0"\ndate = "2026-10-01"\n[[entries]]\nkind = "neu"\ntext = "   "\n', "text"),
        ('version = "0.5.0"\ndate = "2026-10-01"\n[[entries]]\nkind = "neu"\ntext = "a\\nb"\n', "Zeilenumbr"),
        ('version = "0.5.0"\ndate = "2026-10-01"\n[[entries]]\nkind = "neu"\ntext = "x"\npr = [1]\n', "unbekannte Felder"),
        ('version = "0.5.0"\ndate = "2026-10-01"\n[[entries]]\nkind = "neu"\ntext = "x"\nprs = [true]\n', "prs"),
        ('version = "0.5.0"\ndate = "2026-10-01"\n[[entries]]\nkind = "neu"\ntext = "x"\nprs = ["45"]\n', "prs"),
        ('version = "0.5.0"\ndate = "2026-10-01"\n[[entries]]\nkind = "neu"\ntext = "x"\nprs = [0]\n', "prs"),
        ("das ist kein toml ][", "0.5.0.toml"),
    ],
)
def test_invalid_release_is_rejected_strictly_and_skipped_when_loading(tmp_path, caplog, content, reason):
    good = _version_file(tmp_path, "0.4.0")
    bad = tmp_path / "versions" / "0.5.0.toml"
    bad.write_text(content, encoding="utf-8")
    with pytest.raises(ChangelogError, match=reason):
        changelog.read_release(bad)
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.changelog"):
        loaded = changelog.load_changelog(tmp_path)
    assert [r.version for r in loaded.versions] == ["0.4.0"]  # die gute Datei bleibt
    assert good.exists()
    assert "0.5.0.toml" in caplog.text


def test_file_name_must_match_version(tmp_path, caplog):
    path = _version_file(tmp_path, "0.5.0")
    path.rename(path.with_name("0.6.0.toml"))
    with pytest.raises(ChangelogError, match="Dateiname"):
        changelog.read_release(tmp_path / "versions" / "0.6.0.toml")
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.changelog"):
        assert changelog.load_changelog(tmp_path).versions == ()


def test_unreadable_file_is_skipped(tmp_path, caplog):
    _version_file(tmp_path, "0.4.0")
    (tmp_path / "versions" / "0.5.0.toml").write_bytes(b"\xff\xfe\x00 kein utf-8")
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.changelog"):
        assert [r.version for r in changelog.load_changelog(tmp_path).versions] == ["0.4.0"]


def test_invalid_fragment_is_skipped_but_others_stay(tmp_path, caplog):
    _fragment(tmp_path, "gut")
    _fragment(tmp_path, "kaputt", '[[entries]]\nkind = "kaputt"\ntext = "x"\n')
    _fragment(tmp_path, "mit-version", 'version = "0.5.0"\n' + ENTRY)
    with caplog.at_level(logging.WARNING, logger="nodvard_deck.changelog"):
        loaded = changelog.load_changelog(tmp_path)
    assert [e.text for e in loaded.unreleased] == ["Etwas ist neu."]
    assert "kaputt.toml" in caplog.text and "mit-version.toml" in caplog.text
    with pytest.raises(ChangelogError, match="nur \\[\\[entries\\]\\]"):
        changelog.read_fragment(tmp_path / "unreleased" / "mit-version.toml")


def test_get_changelog_is_cached_until_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(changelog, "CHANGELOG_DIR", tmp_path)
    changelog.clear_cache()
    try:
        _version_file(tmp_path, "0.1.0")
        assert changelog.get_changelog().latest == "0.1.0"
        _version_file(tmp_path, "0.2.0")
        assert changelog.get_changelog().latest == "0.1.0"  # gecacht
        changelog.clear_cache()
        assert changelog.get_changelog().latest == "0.2.0"
    finally:
        changelog.clear_cache()


@pytest.mark.parametrize("bereich", ["kern", "docker", "stacks-git", "image-updates", "proxmox", "home-assistant"])
def test_entry_accepts_an_optional_area(bereich):
    entry, = changelog.parse_entries([{"kind": "neu", "text": "Änderung.", "bereich": bereich}])
    assert entry.bereich == bereich


@pytest.mark.parametrize("bereich", [None, "", "Docker", " docker", "docker ", "a\nb", [], 42, True])
def test_invalid_area_is_rejected(bereich):
    with pytest.raises(ChangelogError, match="bereich"):
        changelog.parse_entries([{"kind": "neu", "text": "Änderung.", "bereich": bereich}])


def test_dock_filters_releases_and_fragments_and_keeps_unscoped_entries(tmp_path):
    body = ENTRY + "".join(
        f'[[entries]]\nkind = "verbessert"\ntext = "{bereich}."\nbereich = "{bereich}"\n'
        for bereich in ("kern", "docker", "stacks-git", "image-updates", "proxmox", "documents")
    )
    _version_file(tmp_path, "0.5.0", body)
    _version_file(tmp_path, "0.6.0", ENTRY + 'bereich = "proxmox"\n')
    _fragment(tmp_path, "bereiche", body)
    deck = changelog.load_changelog(tmp_path)
    dock = changelog.load_changelog(tmp_path, edition="dock")
    expected = ["Etwas ist neu.", "kern.", "docker.", "stacks-git.", "image-updates."]
    assert [e.text for e in dock.unreleased] == expected
    assert [r.version for r in dock.versions] == ["0.5.0"]
    assert [e.text for e in dock.versions[0].entries] == expected
    assert len(deck.unreleased) == 7
    assert [r.version for r in deck.versions] == ["0.6.0", "0.5.0"]
    assert len(deck.versions[1].entries) == 7


def test_area_filter_uses_declared_editions(monkeypatch):
    monkeypatch.setattr(changelog, "EDITION_BEREICHE", {"test": frozenset({"kern"})})
    entries = (
        changelog.Entry("neu", "Allgemein."),
        changelog.Entry("neu", "Kern.", bereich="kern"),
        changelog.Entry("neu", "Modul.", bereich="docker"),
    )
    assert changelog.filter_entries(entries, "test") == entries[:2]
    assert changelog.filter_entries(entries, "deck") == entries


def test_cache_separates_editions(tmp_path, monkeypatch):
    _fragment(tmp_path, "module", ENTRY + 'bereich = "proxmox"\n')
    monkeypatch.setattr(changelog, "CHANGELOG_DIR", tmp_path)
    changelog.clear_cache()
    try:
        for edition in ("deck", "dock", "deck", "dock"):
            assert len(changelog.get_changelog(edition).unreleased) == (0 if edition == "dock" else 1)
    finally:
        changelog.clear_cache()
