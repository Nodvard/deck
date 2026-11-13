"""Vorgaben für Deck und Dock im Container."""

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    "deploy/docker-compose.yml", "deploy/compose.standalone.yml",
    "deploy/compose.standalone-mit-helfer.yml", "deploy/nodvard-dock/compose.yml",
)


def _assert_service(service):
    assert service["user"] == "1000:1000"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert not service.get("cap_add")
    assert not service.get("privileged")
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["tmpfs"] == ["/tmp:rw,noexec,nosuid,nodev,mode=1777"]
    assert any(mount.endswith(":/app/data") for mount in service["volumes"])
    assert "/api/v1/health" in " ".join(service["healthcheck"]["test"])
    assert service["healthcheck"]["start_period"] == "300s"


@pytest.mark.parametrize("filename", COMPOSE_FILES)
def test_compose_runs_without_root_or_capabilities(filename):
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
    name = "nodvard-dock" if "nodvard-dock" in filename else "nodvard-deck"
    _assert_service(compose["services"][name])


def test_dockerfile_runtime_rules():
    text = (ROOT / "deploy/Dockerfile").read_text(encoding="utf-8")
    runtime = text.split("FROM python:", 1)[1]
    users = re.findall(r"^USER (.+)$", runtime, re.MULTILINE)
    assert users == ["0:0"]
    entrypoint = (ROOT / "deploy/entrypoint.sh").read_text(encoding="utf-8")
    switch = 'exec setpriv --reuid="$uid" --regid="$gid" --clear-groups --no-new-privs "$0" "$@"'
    assert entrypoint.index(switch) < entrypoint.index("\npython -m nodvard_deck.boot")
    assert 'uid="$(id -u lattice 2>/dev/null || echo 1000)"' in entrypoint
    assert 'gid="$(id -g lattice 2>/dev/null || echo 1000)"' in entrypoint
    assert "--uid 1000 --gid lattice" in runtime and "--gid 1000 lattice" in runtime
    for instruction in ("CMD", "ENTRYPOINT"):
        [value] = re.findall(rf"^{instruction} (.+)$", runtime, re.MULTILINE)
        assert isinstance(json.loads(value), list), "DL3025: JSON-Form verwenden"
    assert "HOME=/app" in runtime and "PYTHONDONTWRITEBYTECODE=1" in runtime
    assert "chown lattice:lattice /app/data" in runtime
    assert "chmod 700 /app/data" in runtime
    assert "HEALTHCHECK " in runtime
    assert "COPY --chmod=755 deploy/migrate-data.sh /usr/local/bin/migrate-data" in runtime


@pytest.mark.parametrize("filename", ["docs/11-ERST-EINRICHTUNG.md", "docs/en/FIRST-SETUP.md"])
def test_setup_examples_and_migration(filename):
    yaml = pytest.importorskip("yaml")
    text = (ROOT / filename).read_text(encoding="utf-8")
    examples = [yaml.safe_load(block) for block in re.findall(r"```ya?ml\n(.*?)```", text, re.DOTALL)]
    [compose] = [example for example in examples if isinstance(example, dict) and "services" in example]
    _assert_service(compose["services"]["nodvard-deck"])
    stop = text.index("docker compose stop nodvard-deck", text.index("### A.1"))
    migrate = text.index("--entrypoint /usr/local/bin/migrate-data nodvard-deck", stop)
    start = text.index("docker compose up -d nodvard-deck", migrate)
    assert stop < migrate < start
    assert "--cap-add CHOWN --cap-add DAC_OVERRIDE" in text
    assert "deploy_lattice_data" in text and "nodvard-deck-data" in text


@pytest.mark.parametrize("filename", ["README.md", "README.en.md"])
def test_dock_documents_the_user_and_data_migration(filename):
    text = (ROOT / "deploy/nodvard-dock" / filename).read_text(encoding="utf-8")
    assert "1000:1000" in text and "read_only" in text and "cap_drop" in text
    assert "docker compose stop nodvard-dock" in text
    assert "--entrypoint /usr/local/bin/migrate-data nodvard-dock" in text
    assert "nodvard-dock-data" in text


def test_deploy_guide_describes_non_root_starts():
    text = (ROOT / "deploy/README.md").read_text(encoding="utf-8")
    assert "Das Image startet als root" in text
    assert "vor Migration und Anwendung zu `1000:1000`" in text
    assert "Ohne `user:` läuft der Check als root" in text
    assert "/usr/local/bin/migrate-data" in text


def test_healthcheck_docs_use_the_container_start_user():
    text = (ROOT / "deploy/README.md").read_text(encoding="utf-8")
    section = " ".join(text.split("- **Healthcheck:**", 1)[1].split("\n- **", 1)[0].split())
    assert (
        'Der Check läuft als Startbenutzer des Containers: ohne `user:` als root (`USER 0:0` im Dockerfile), '
        'mit `user: "1000:1000"` als UID 1000.'
    ) in section
    assert "Auch ohne `user:` läuft der Check als UID 1000" not in text


@pytest.mark.parametrize(("filename", "api_policy", "image_policy"), [
    (
        "docs/11-ERST-EINRICHTUNG.md",
        "CORP `same-origin` gilt für API-Antworten. Ausgenommen ist `/api/v1/health` mit CORP `cross-origin`, "
        "damit Startseiten wie Homarr oder Homepage den Zustand von Deck direkt aus dem Browser abfragen können.",
        "Öffentliche Logos, Favicons und App-Symbole erhalten bei GET/HEAD CORP `cross-origin` "
        "und können auf fremden Webseiten eingebunden werden.",
    ),
    (
        "docs/en/FIRST-SETUP.md",
        "CORP `same-origin` applies to API responses. The exception is `/api/v1/health` with CORP `cross-origin`, "
        "so that start pages such as Homarr or Homepage can query Deck's status directly from the browser.",
        "Public logos, favicons and app icons receive CORP `cross-origin` for GET/HEAD "
        "and can be embedded on other websites.",
    ),
])
def test_setup_docs_explain_public_image_embedding(filename, api_policy, image_policy):
    text = " ".join((ROOT / filename).read_text(encoding="utf-8").split())
    assert api_policy in text
    assert image_policy in text
    for path in ("/api/v1/branding/logo", "/api/v1/branding/favicon", "/api/v1/app/icon-*.png"):
        assert f"`{path}`" in text
    assert "180, 192" in text and "512" in text and "`-maskable`" in text


@pytest.mark.parametrize(("filename", "data", "ugreen"), [
    ("docs/13-INSTALLATION.md", "Daten", "### UGREEN (UGOS Pro, App „Docker“)"),
    ("docs/en/INSTALLATION.md", "Data", '### UGREEN (UGOS Pro, "Docker" app)'),
])
def test_platform_guides_explain_updates_with_root_owned_data(filename, data, ugreen):
    text = (ROOT / filename).read_text(encoding="utf-8")
    overview = text.split(f"- **{data}:**", 1)[1].split("\n##", 1)[0]
    assert 'user: "1000:1000"' in overview and "root" in overview
    assert "cap_drop: ALL" in overview
    assert "FEHLER: /app/data ist für Benutzer … nicht beschreibbar." in overview
    stop = overview.index("docker compose stop nodvard-deck")
    migrate = overview.index("docker compose run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE "
                             "--entrypoint /usr/local/bin/migrate-data nodvard-deck")
    assert stop < migrate < overview.index("docker compose up -d nodvard-deck")
    for heading in ("### Synology (", ugreen):
        section = text.split(heading, 1)[1].split("\n### ", 1)[0]
        assert 'user: "1000:1000"' in section and "root" in section and "SSH" in section
        assert "**Everyone**" not in section


@pytest.mark.parametrize(("filename", "heading", "data_dir", "start"), [
    ("docs/13-INSTALLATION.md", "Unraid", "/mnt/user/appdata/nodvard-deck", "**Apply**"),
    ("docs/en/INSTALLATION.md", "Unraid", "/mnt/user/appdata/nodvard-deck", "**Apply**"),
    ("docs/13-INSTALLATION.md", "ZimaOS und CasaOS", "/DATA/AppData/nodvard-deck", "**Install**"),
    ("docs/en/INSTALLATION.md", "ZimaOS and CasaOS", "/DATA/AppData/nodvard-deck", "**Install**"),
])
def test_platform_guides_prepare_bind_mount_permissions_before_start(filename, heading, data_dir, start):
    text = (ROOT / filename).read_text(encoding="utf-8")
    section = text.split(f"### {heading}\n", 1)[1].split("\n##", 1)[0]
    commands = re.findall(r"```bash\n(.*?)```", section, re.DOTALL)
    [prepare] = [block for block in commands if f"mkdir -p {data_dir}" in block]
    assert prepare.index(f"mkdir -p {data_dir}") < prepare.index(f"chown -R 1000:1000 {data_dir}")
    assert prepare.index(f"chown -R 1000:1000 {data_dir}") < prepare.index(f"chmod 700 {data_dir}")
    assert section.index(prepare) < section.index(start)
    assert "Der Container macht beim Start den Benutzer 1000" not in section
    assert "At startup the container makes user 1000" not in section
    assert "ändert die Besitzrechte beim Start nicht" not in section
    assert "does not change ownership at startup" not in section
    if "en/" in filename:
        assert "starts as root" in section
        assert "1000:1000" in section
        assert "without root" in section
        assert "the container sets them again" in section
    else:
        assert "startet als root" in section
        assert "1000:1000" in section
        assert "ohne root weiter" in section
        assert "richtet der Container das beim" in section


def test_container_hardening_has_a_security_entry():
    folder = ROOT / "backend/src/nodvard_deck/changelog"
    entries = [
        entry
        for directory in ("unreleased", "versions")
        for path in (folder / directory).glob("*.toml")
        for entry in tomllib.loads(path.read_text(encoding="utf-8"))["entries"]
    ]
    assert any(entry["kind"] == "sicherheit" and "Container ohne root" in entry["text"]
               and "HTTPS" in entry["text"] for entry in entries)


@pytest.mark.parametrize(("filename", "note"), [
    ("docs/11-ERST-EINRICHTUNG.md", "Die Notseite sendet HSTS immer; Browser beachten es nur über HTTPS."),
    ("docs/en/FIRST-SETUP.md", "The rescue page always sends HSTS; browsers only apply it over HTTPS."),
])
def test_setup_docs_explain_hsts_on_the_rescue_page(filename, note):
    assert note in (ROOT / filename).read_text(encoding="utf-8")


def _entry_with(marker: str) -> dict:
    """Der Eintrag mit `marker` im Text, gleich ob noch in `unreleased/` oder schon in `versions/` (nach dem Release)."""
    folder = ROOT / "backend/src/nodvard_deck/changelog"
    found = [
        entry
        for directory in ("unreleased", "versions")
        for path in (folder / directory).glob("*.toml")
        for entry in tomllib.loads(path.read_text(encoding="utf-8"))["entries"]
        if marker in entry["text"]
    ]
    assert len(found) == 1, marker
    return found[0]


def test_security_entry_describes_image_start_and_health_embedding():
    entry = _entry_with("Ohne Benutzervorgabe startet das Image als root")
    assert entry["kind"] == "sicherheit"
    text = entry["text"]
    assert "Ohne Benutzervorgabe startet das Image als root" in text
    assert "vor Migration und Anwendung zu UID 1000" in text
    assert "Öffentliche Bilder und der Health-Endpunkt bleiben auf fremden Webseiten abrufbar" in text
    assert "nicht mehr auf fremden Webseiten einbinden" not in text
    assert "die Notseite sendet es immer" in text
    assert "strengere Referrer- und Ressourcenregeln" not in text


def test_deployment_fixes_have_a_changelog_entry():
    entry = _entry_with("Die gehärteten Compose-Vorlagen unterstützen")
    assert entry["kind"] == "behoben"
    for word in ("Einrichtungscode", "Datenmigration", "Zugriffsbeschränkung", "Dienstnamen"):
        assert word in entry["text"]
