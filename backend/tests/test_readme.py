import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parents[2] / "README.md"
SECURITY = "## Sicherheit und Datenschutz auf einen Blick"
README_EN = README.with_name("README.en.md")
SECURITY_EN = "## Security and privacy at a glance"


@pytest.fixture(autouse=True, params=("unreleased", "released"))
def _changelog_state(request, monkeypatch):
    if request.param == "unreleased":
        return
    fragment_dir = README.parent / "backend/src/nodvard_deck/changelog/unreleased"
    _hide_changelog_fragments(fragment_dir, monkeypatch)


def _hide_changelog_fragments(fragment_dir, monkeypatch):
    original_open = Path.open
    original_is_file = Path.is_file

    def open_after_release(path, *args, **kwargs):
        if path.parent == fragment_dir and path.suffix == ".toml":
            raise FileNotFoundError(path)
        return original_open(path, *args, **kwargs)

    def is_file_after_release(path):
        if path.parent == fragment_dir and path.suffix == ".toml":
            return False
        return original_is_file(path)

    monkeypatch.setattr(Path, "open", open_after_release)
    monkeypatch.setattr(Path, "is_file", is_file_after_release)


@pytest.mark.parametrize(
    "anchor",
    (
        "6-root-rights-without-a-password-and-the-docker-group",
        "105-finding-and-installing-a-new-version",
    ),
)
def test_english_setup_reference_matches_target_section(anchor):
    text = README_EN.read_text(encoding="utf-8")
    guide = (README.parent / "docs/en/FIRST-SETUP.md").read_text(encoding="utf-8")
    link = re.search(
        rf"\[([^\]]+)\]\(docs/en/FIRST-SETUP\.md#{re.escape(anchor)}\)", text
    )
    assert link is not None
    section = re.search(r"\bsection ([A-Za-z0-9.]+)", link.group(1))
    assert section is not None
    marker = f'<a id="{anchor}"></a>'
    assert marker in guide
    heading = re.findall(r"^#{1,6} (.+)$", guide.split(marker, 1)[0], re.MULTILINE)[-1]
    assert section.group(1) == heading.split()[0].removesuffix("."), heading


def test_security_section_appears_once_after_setup():
    text = README.read_text(encoding="utf-8")
    assert text.count(SECURITY) == 1
    assert text.index("## Schnellstart in 5 Minuten") < text.index(SECURITY) < text.index("## Aufbau")


def test_english_security_section_appears_once_after_setup():
    text = README_EN.read_text(encoding="utf-8")
    assert text.count(SECURITY_EN) == 1
    assert text.index("## Quick start in 5 minutes") < text.index(SECURITY_EN) < text.index("## Architecture")


def test_security_section_describes_outgoing_connections():
    text = README.read_text(encoding="utf-8")
    section = text.split("## Schnellstart in 5 Minuten", 1)[1].split(SECURITY, 1)[1].split("## Aufbau", 1)[0]
    for host in (
        "ghcr.io",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    ):
        assert f"`{host}`" in section
    assert "ausgehende Git-Verbindungen zum eingerichteten Repository-Server" in section
    assert "Discord, Telegram, Slack, Pushover und ntfy.sh" in " ".join(section.split())
    assert "Token-Schutz kann jeder lesen" in section


def test_module_table_has_unique_current_descriptions():
    text = README.read_text(encoding="utf-8")
    expected = {
        "Netzwerk (Beta)": (
            "Wegweiser zu den Modulen Pi-hole und Nginx Proxy Manager. "
            "Wer die beiden nutzt, kann dieses Modul ausschalten."
        ),
        "Skripte": (
            "Versionierte Skript-Bibliothek, auf einem oder vielen Servern ausführen, "
            "zeitgesteuert, auf Wunsch ohne Klick, solange sich nichts ändert"
        ),
        "Push-Nachrichten": "Push-Benachrichtigungen über ntfy und weitere Kanäle",
    }
    for name, description in expected.items():
        rows = [line for line in text.splitlines() if line.startswith(f"| {name} |")]
        assert rows == [f"| {name} | {description} |"]
    assert "| ntfy |" not in text


@pytest.mark.parametrize(
    ("readme", "dock", "guide", "back"),
    [
        (README, "deploy/nodvard-dock/README.md", "docs/dock/README.md", "README.md"),
        (README_EN, "deploy/nodvard-dock/README.en.md", "docs/en/dock/README.md", "README.en.md"),
    ],
    ids=["deutsch", "englisch"],
)
def test_deck_and_dock_link_to_the_matching_language(readme, dock, guide, back):
    text = readme.read_text(encoding="utf-8")
    assert f"]({dock})" in text
    assert f"]({guide})" in text
    dock_text = (README.parent / dock).read_text(encoding="utf-8")
    assert f"](../../{back})" in dock_text
    assert f"](../../{guide})" in dock_text
    index = (README.parent / "docs/README.md").read_text(encoding="utf-8")
    assert f"]({guide.removeprefix('docs/')})" in index


@pytest.mark.parametrize("readme", [README, README_EN], ids=["deutsch", "englisch"])
def test_license_wording_describes_source_availability(readme):
    text = readme.read_text(encoding="utf-8")
    assert "PolyForm Noncommercial License 1.0.0" in text
    assert not re.search(r"open[\s-]+source", text, re.IGNORECASE)


LANGUAGES = (
    (README, "Self-hosted, Quelltext einsehbar (PolyForm Noncommercial), kostenlos für private Nutzung",
     "Warum eine nichtkommerzielle Lizenz?", "Schnellstart in 5 Minuten",
     "Was Deck kann und was nicht", "Sicherheit und Datenschutz auf einen Blick",
     "docs/11-ERST-EINRICHTUNG.md", "schnellstart-in-5-minuten"),
    (README_EN, "Self-hosted, source available to read (PolyForm Noncommercial), free for personal use",
     "Why a noncommercial license?", "Quick start in 5 minutes",
     "What Deck can and cannot do", "Security and privacy at a glance",
     "docs/en/FIRST-SETUP.md", "quick-start-in-5-minutes"),
)


def _section(text, heading):
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_english_license_section_does_not_repeat_the_explanation():
    section = _section(README_EN.read_text(encoding="utf-8"), "License")
    assert "PolyForm Noncommercial License 1.0.0" in section
    assert "The source code is available to read." in section
    assert section.count("](THIRD_PARTY_LICENSES)") == 1
    assert "**Why this license?**" not in section


@pytest.mark.parametrize(
    "readme,heading,statement,label",
    (
        (README, SECURITY,
         "Eigene App-Kacheln speichern nur Links; Deck baut keine automatischen Verbindungen zu ihren Zieladressen auf",
         "Eigene App-Links"),
        (README_EN, SECURITY_EN,
         "Custom app tiles only store links; Deck makes no automatic connections to their target addresses",
         "Custom app links"),
    ),
)
def test_security_describes_custom_apps_as_links(readme, heading, statement, label):
    section = _section(readme.read_text(encoding="utf-8"), heading.removeprefix("## "))
    assert statement in " ".join(section.split())
    assert f"[{label}](backend/src/nodvard_deck/services/custom_apps.py)" in section


@pytest.mark.parametrize("readme,header,why,quick,promises,security,guide,anchor", LANGUAGES)
def test_readmes_explain_license_before_features(readme, header, why, quick, promises, security, guide, anchor):
    text = readme.read_text(encoding="utf-8")
    assert text.split("# Nodvard Deck\n", 1)[1].lstrip().splitlines()[0] == header
    paragraph = _section(text, why).strip()
    assert len(re.split(r"(?<=[.!?])\s+", paragraph)) == 3
    assert "[LICENSE](LICENSE)" in paragraph
    assert "kontakt@nodvard.com" in paragraph
    assert "https://discord.gg/gTTs7qMabt" in paragraph
    for pattern in (
        r"Einzelentwickler|single developer",
        r"kommerziell|commercial",
        r"nichtkommerziell|noncommercial",
        r"verändern|modify",
        r"weitergeben|redistribute",
        r"Erlaubnis|permission",
    ):
        assert re.search(pattern, paragraph), pattern
    assert text.index(f"## {why}") < text.index(f"## {promises}") < text.index(f"## {quick}")
    assert re.search(r"open\s+source", text, re.IGNORECASE) is None
    assert "\u2013" not in text and "\u2014" not in text


@pytest.mark.parametrize("readme,header,why,quick,promises,security,guide,anchor", LANGUAGES)
def test_quick_start_reaches_a_working_module(readme, header, why, quick, promises, security, guide, anchor):
    text = readme.read_text(encoding="utf-8")
    section = _section(text, quick).split("<details>", 1)[0]
    assert f"](#{anchor})" in text
    assert "curl -fsSL -o compose.yml https://raw.githubusercontent.com/nodvard/deck/main/deploy/compose.standalone.yml" in section
    assert "docker compose up -d" in section
    assert "docker compose logs nodvard-deck" in section
    assert "http://localhost:8080" in section
    assert re.findall(r"^([1-4])\. \*\*", section, re.MULTILINE) == ["1", "2", "3", "4"]
    for label in ("Einrichtungscode", "Server & Zugänge", "Verbindung prüfen", "Server-Zustand"):
        assert label in section
    for fragment in ("#2-", "#3-", "#4-"):
        assert f"]({guide}{fragment}" in section
    assert (README.parent / guide).is_file()


@pytest.mark.parametrize("readme,header,why,quick,promises,security,guide,anchor", LANGUAGES)
def test_promises_cover_capabilities_and_limits(readme, header, why, quick, promises, security, guide, anchor):
    section = _section(readme.read_text(encoding="utf-8"), promises)
    points = re.findall(r"^- (.+(?:\n  .+)*)", section, re.MULTILINE)
    assert 6 <= len(points) <= 8
    for pattern in (r"SSH", r"[Bb]eta", r"[Bb]ackup|Sicherung", r"[Rr]oot", r"24/7", r"[Gg]erman|Deutsch"):
        assert re.search(pattern, section), pattern


@pytest.mark.parametrize("readme,header,why,quick,promises,security,guide,anchor", LANGUAGES)
def test_security_covers_every_modules_access_and_links_to_code(readme, header, why, quick, promises, security, guide, anchor):
    section = _section(readme.read_text(encoding="utf-8"), security)
    for manifest in sorted((README.parent / "extensions").glob("*/extension.toml")):
        link = manifest.relative_to(README.parent).as_posix()
        assert f"]({link})" in section, manifest.parent.name
    for source in (
        "backend/src/nodvard_deck/config.py",
        "backend/src/nodvard_deck/core/vault.py",
        "backend/src/nodvard_deck/core/updates.py",
        "backend/src/nodvard_deck/services/update_check.py",
        "extensions/docker/src/nodvard_deck_ext_docker/git_fetch.py",
        "extensions/docker/src/nodvard_deck_ext_docker/image_updates.py",
        "extensions/ntfy/src/nodvard_deck_ext_ntfy/channels.py",
        "extensions/shield/src/nodvard_deck_ext_shield/ollama.py",
    ):
        assert f"]({source})" in section
        assert (README.parent / source).is_file()
    for token in ("master.key", "vault_keyring.json", "/app/data", "0600", "Ollama", "ntfy", "docker", "sudo", "administrators"):
        assert token in section
    assert "**Keine Telemetrie:**" in section or "**No telemetry:**" in section
    for host in ("ghcr.io", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"):
        assert f"`{host}`" in section


@pytest.mark.parametrize("readme", (README, README_EN))
def test_readme_images_and_local_links_exist(readme):
    text = readme.read_text(encoding="utf-8")
    images = re.findall(r"!\[[^\]]*\]\((docs/img/[^)]+)\)", text)
    assert set(images) == {f"docs/img/{name}.png" for name in ("uebersicht", "docker", "shield", "updates")}
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if link.startswith(("https://", "http://", "#", "mailto:")):
            continue
        assert (readme.parent / link.split("#", 1)[0]).is_file(), link


def test_readme_link_check_rejects_released_fragment(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    fragment_dir = tmp_path / "backend/src/nodvard_deck/changelog/unreleased"
    fragment_dir.mkdir(parents=True)
    fragment = fragment_dir / "linkziel.toml"
    fragment.write_text('[[entries]]\nkind = "behoben"\ntext = "Test."\n', encoding="utf-8")
    image_dir = tmp_path / "docs/img"
    image_dir.mkdir(parents=True)
    links = []
    for name in ("uebersicht", "docker", "shield", "updates"):
        image = image_dir / f"{name}.png"
        image.touch()
        links.append(f"![{name}]({image.relative_to(tmp_path).as_posix()})")
    link = fragment.relative_to(tmp_path).as_posix()
    readme.write_text("\n".join([*links, f"[Änderung]({link})"]), encoding="utf-8")

    test_readme_images_and_local_links_exist(readme)
    with monkeypatch.context() as released:
        _hide_changelog_fragments(fragment_dir, released)
        with pytest.raises(AssertionError, match=re.escape(link)):
            test_readme_images_and_local_links_exist(readme)
        with pytest.raises(FileNotFoundError):
            fragment.read_text(encoding="utf-8")
        assert readme.is_file()
    test_readme_images_and_local_links_exist(readme)


@pytest.mark.parametrize("readme", (README, README_EN))
def test_readme_local_anchors_match_headings_or_explicit_ids(readme):
    for link in re.findall(r"\]\(([^)]+)\)", readme.read_text(encoding="utf-8")):
        if link.startswith(("https://", "http://", "mailto:")) or "#" not in link:
            continue
        name, anchor = link.split("#", 1)
        target = (readme.parent / name if name else readme).read_text(encoding="utf-8")
        headings = re.findall(r"^#{1,6} (.+)$", target, re.MULTILINE)
        anchors = {
            re.sub(r"[^\w\-\s]", "", heading.strip().lower()).replace(" ", "-")
            for heading in headings
        }
        assert anchor in anchors or f'<a id="{anchor}"></a>' in target, link
