from pathlib import Path
import tomllib


REPO = Path(__file__).resolve().parents[2]


def test_license_addendum_covers_company_evaluation_in_both_languages():
    text = (REPO / "LICENSE-ZUSATZ.md").read_text(encoding="utf-8")
    for phrase in (
        "PolyForm Noncommercial License 1.0.0",
        "Nodvard Deck und Dock",
        "bis zu 30 Tage intern zur Bewertung",
        "kein Produktivbetrieb und kein Einsatz für Kunden",
        "Nodvard Deck and Dock",
        "internally for evaluation for up to 30 days",
        "no production use and no use for customers",
        "kontakt@nodvard.com",
    ):
        assert phrase in text


def test_license_sections_link_to_the_addendum():
    for name, heading, link in (
        ("README.md", "## Lizenz", "](LICENSE-ZUSATZ.md)"),
        ("README.en.md", "## License", "](LICENSE-ZUSATZ.md)"),
        ("docs/FAQ-LIZENZ.md", "## 2.", "[LICENSE-ZUSATZ.md](../LICENSE-ZUSATZ.md)"),
    ):
        text = (REPO / name).read_text(encoding="utf-8")
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        assert link in section


def _changelog_entries():
    # Findet Eintraege in unreleased/ ODER versions/: ein Release rollt die Bruchstuecke in die Versionsdatei.
    base = REPO / "backend/src/nodvard_deck/changelog"
    entries = []
    for teil in ("unreleased", "versions"):
        for datei in sorted((base / teil).glob("*.toml")):
            entries.extend(tomllib.loads(datei.read_text(encoding="utf-8")).get("entries", []))
    return entries


def test_license_addendum_is_in_the_changelog():
    entries = _changelog_entries()
    assert any("Deck und Dock bis zu 30 Tage intern zur Bewertung" in entry["text"] for entry in entries)
