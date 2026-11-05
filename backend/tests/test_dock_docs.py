"""Dock-Anleitungen bleiben vollständig, verlinkt und am vorhandenen Code ausgerichtet."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUIDES = (ROOT / "docs/dock", ROOT / "docs/en/dock")
TOPICS = ("stacks-from-git.md", "image-updates.md", "templates.md", "troubleshooting.md")
DOCKER = ROOT / "extensions/docker/src/nodvard_deck_ext_docker"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _anchor(title: str) -> str:
    return re.sub(r"[^\w\- ]", "", title.strip().lower()).replace(" ", "-")


@pytest.mark.parametrize("folder", GUIDES, ids=["deutsch", "englisch"])
def test_dock_has_the_same_complete_set_of_guides(folder):
    assert {path.name for path in folder.glob("*.md")} == {"README.md", *TOPICS}
    index = _read(folder / "README.md")
    for topic in TOPICS:
        assert f"]({topic})" in index
        guide = _read(folder / topic)
        assert "](README.md)" in guide
        assert guide.count("\n## ") >= 3
    for name in ("README.md", *TOPICS):
        text = _read(folder / name)
        other = GUIDES[1] if folder == GUIDES[0] else GUIDES[0]
        links = re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text)
        assert any((folder / link).resolve() == (other / name).resolve() for link in links)


@pytest.mark.parametrize("folder", GUIDES, ids=["deutsch", "englisch"])
def test_all_dock_links_and_section_anchors_resolve(folder):
    readme = ROOT / "deploy/nodvard-dock" / ("README.md" if folder == GUIDES[0] else "README.en.md")
    for path in [*folder.glob("*.md"), readme]:
        text = _read(path)
        assert not re.search(r"open[\s-]+source", text, re.IGNORECASE)
        assert not re.search(r"[\u2013\u2014]", text)
        for link in re.findall(r"\]\(([^)]+)\)", text):
            if link.startswith(("https://", "http://", "mailto:")):
                continue
            target, _, anchor = link.partition("#")
            resolved = (path.parent / target).resolve() if target else path
            assert resolved.is_file(), (path, link)
            if anchor:
                content = _read(resolved)
                headings = {_anchor(t) for t in re.findall(r"^#+ (.+)$", content, re.MULTILINE)}
                assert anchor in headings or f'<a id="{anchor}"></a>' in content, (path, link)


@pytest.mark.parametrize("folder", GUIDES, ids=["deutsch", "englisch"])
def test_edition_comparison_has_source_evidence(folder):
    text = _read(folder / "README.md")
    for fact in (
        "EDITION=dock", "NODVARD_DECK_EXTENSIONS=docker", "choose_modules=False", "choose_modules=True",
        "repo_tools=False", "repo_tools=True", "ghcr.io/nodvard/dock", "ghcr.io/nodvard/deck",
        "Proxmox", "Server-Zustand", "Nodvard Shield",
    ):
        assert fact in text
    for filename in ("deploy/Dockerfile", "deploy/select_extensions.sh", "backend/src/nodvard_deck/edition.py"):
        assert filename in text
    edition = ast.parse(_read(ROOT / "backend/src/nodvard_deck/edition.py"))
    definitions = {
        node.targets[0].id: {item.arg: ast.literal_eval(item.value) for item in node.value.keywords}
        for node in edition.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"DOCK", "DECK"}
    }
    assert definitions["DOCK"]["choose_modules"] is False
    assert definitions["DOCK"]["repo_tools"] is False
    assert definitions["DECK"]["choose_modules"] is True
    assert definitions["DECK"]["repo_tools"] is True
    assert definitions["DOCK"]["repository"] == "nodvard/dock"


@pytest.mark.parametrize("name", ["README.md", "README.en.md"], ids=["deutsch", "englisch"])
def test_quickstart_covers_account_timezone_and_optional_settings(name):
    source = _read(ROOT / "frontend/src/routes/SetupPage.tsx")
    definitions = source.split("const STEPS:", 1)[1].split("];", 1)[0]
    steps = re.findall(r'\{ id: "([^"]+)", title: "([^"]+)" \}', definitions)
    expected = [title for step, title in steps if step in {"account", "timezone", "totp", "branding", "done"}]
    assert len(expected) == 5
    readme = _read(ROOT / "deploy/nodvard-dock" / name)
    section = readme.split("### 2. ", 1)[1].split("### 3. ", 1)[0]
    assert re.findall(r"^\d+\. \*\*([^*]+)\*\*", section, re.MULTILINE) == expected


@pytest.mark.parametrize("name", ["README.md", "README.en.md"], ids=["deutsch", "englisch"])
def test_quickstart_chooses_a_writable_stack_folder_before_checking(name):
    readme = _read(ROOT / "deploy/nodvard-dock" / name)
    step = readme.split("### 4. ", 1)[1].split("\n2. ", 1)[0]
    actions = re.findall(r"\*\*([^*]+)\*\*", step)
    expected = ["Ändern", "Im Heimatordner", "Übernehmen", "Prüfen", "Anlegen"]
    assert [action for action in actions if action in expected] == expected
    assert "`/opt/stacks`" in step


def _templates() -> list[dict]:
    tree = ast.parse(_read(DOCKER / "templates.py"))
    catalog = next(node.value for node in tree.body if isinstance(node, ast.AnnAssign) and node.target.id == "TEMPLATES")
    return [{kw.arg: ast.literal_eval(kw.value) for kw in item.keywords} for item in catalog.elts]


@pytest.mark.parametrize("folder", GUIDES, ids=["deutsch", "englisch"])
def test_template_catalog_matches_names_ids_and_ports_in_code(folder):
    text = _read(folder / "templates.md")
    rows = [line.split("|")[1:-1] for line in text.splitlines() if line.startswith("| ") and "`" in line]
    templates = _templates()
    assert len(rows) == len(templates)
    for row, template in zip(rows, templates, strict=True):
        assert row[0].strip() == template["name"]
        assert row[1].strip() == f'`{template["id"]}`'
        assert re.findall(r"`([^`]+)`", row[2]) == list(template["ports"])
    for note in ("SIGNUPS_ALLOWED", "false", "HTTPS", "Prüfen", "Anlegen und starten", "compose.yaml", "/opt/stacks"):
        assert note in text
    assert "templates.py" in text and "new_stack.py" in text


@pytest.mark.parametrize("folder", GUIDES, ids=["deutsch", "englisch"])
def test_git_guide_covers_existing_workflow_and_limits(folder):
    text = _read(folder / "stacks-from-git.md")
    for label in (
        "Übernehmen", "Jetzt übernehmen", "Neuer Stack", "Prüfen", "Anlegen", "Anwenden", "Aus Git",
        "Eintragen und holen", "Holen und ansehen", "Einspielen", "Volumes vorher sichern", "Jetzt neu starten",
        "Zurück zum Stand davor", "Repository vergessen", "Auf neuen Stand prüfen", "Git-Zugänge", ".env",
        "git_watch_enabled", "300", "10 MiB", "256 KiB",
    ):
        assert label in text
    for source in ("git_source.py", "git_fetch.py", "git_apply.py", "git_watch.py"):
        assert source in text
    assert "nicht automatisch" in text or "not automatically" in text
    assert "genau einer Compose-Datei" in text or "exactly one Compose file" in text
    assert "keine Anwendungsdaten" in text or "not restore application data" in text
    limits = ast.parse(_read(DOCKER / "git_fetch.py"))
    cls = next(node for node in limits.body if isinstance(node, ast.ClassDef) and node.name == "Limits")
    values = {node.target.id: node.value for node in cls.body if isinstance(node, ast.AnnAssign)}
    assert ast.literal_eval(values["files"]) == 300


@pytest.mark.parametrize("folder", GUIDES, ids=["deutsch", "englisch"])
def test_image_guide_separates_checks_deployment_and_data_restore(folder):
    text = _read(folder / "image-updates.md")
    for fact in (
        "Image-Updates prüfen", "Digest" if folder == GUIDES[0] else "digest", "app:1", "app:2",
        "image_updates_enabled", "image_update_auto_rollback", "docker run", "Volumes vorher sichern",
        "Zurück zur vorigen Version", "Bind-Mounts" if folder == GUIDES[0] else "Bind mounts",
    ):
        assert fact in text
    assert "keinen automatischen Rückweg" in text or "excluded" in text
    assert "stellt keine Datenbankinhalte wieder her" in text or "does not restore database contents" in text
    for source in ("image_updates.py", "image_apply.py", "applier.py", "stack_update.py", "stack_restore.py"):
        assert source in text


@pytest.mark.parametrize("folder", GUIDES, ids=["deutsch", "englisch"])
def test_stopped_services_require_pulling_images_before_applying(folder):
    text = _read(folder / "image-updates.md")
    paragraph = next(
        paragraph for paragraph in text.split("\n\n")
        if paragraph.startswith(("Gestoppte Dienste", "Stopped services"))
    )
    assert paragraph.index("**Images holen**") < paragraph.index("**Anwenden**")
    assert "alte lokale Image" in paragraph or "old local image" in paragraph


@pytest.mark.parametrize("folder", GUIDES, ids=["deutsch", "englisch"])
def test_troubleshooting_offers_specific_recovery_steps(folder):
    text = _read(folder / "troubleshooting.md")
    for step in (
        "docker compose ps", "docker compose logs --tail=100 nodvard-dock", "NODVARD_DOCK_PORT=8090",
        "docker compose logs nodvard-dock", "docker compose version", "Verbindung prüfen", "localhost",
        "docker-bedienen", "Git-Server im eigenen Netz erlauben", "Anwenden", "Wiederherstellen",
        "Sicherung einspielen", "docker compose down -v",
    ):
        assert step in text
    assert "kein Reparaturschritt" in text or "not a repair step" in text
    assert "Wiederherstellungsschlüssel" in text or "recovery key" in text
    assert "Einmal-Passwort" in text or "one-time password" in text
