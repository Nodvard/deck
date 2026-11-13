from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("doc", "heading"), [
    ("docs/13-INSTALLATION.md", "### Zugriff auf diesen Rechner oder das LAN beschränken"),
    ("docs/en/INSTALLATION.md", "### Restrict access to this host or the LAN"),
])
def test_installation_keeps_data_migration_and_access_restrictions(doc, heading):
    text = (ROOT / doc).read_text(encoding="utf-8")
    migration = (
        "docker compose stop nodvard-deck\n"
        "docker compose run --rm --no-deps --user 0:0 --cap-add CHOWN --cap-add DAC_OVERRIDE "
        "--entrypoint /usr/local/bin/migrate-data nodvard-deck\n"
        "docker compose up -d nodvard-deck"
    )
    assert migration in text
    assert text.index(migration) < text.index(heading)
    assert '"127.0.0.1:8080:8080"' in text
    assert '"192.168.1.10:8080:8080"' in text
    assert "http://nodvard-deck:8080" in text
    for marker in ("<" * 7, "=" * 7, ">" * 7):
        assert not any(line.startswith(marker) for line in text.splitlines())


@pytest.mark.parametrize("doc", [
    "docs/13-INSTALLATION.md",
    "docs/en/INSTALLATION.md",
    "docs/12-REVERSE-PROXY.md",
    "docs/en/REVERSE-PROXY.md",
])
def test_restricting_access_keeps_the_selected_host_port(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    notes = [paragraph for paragraph in text.split("\n\n") if "`127.0.0.1:8081:8080`" in paragraph]
    assert len(notes) == 1
    for example in (
        "8081:8080", "127.0.0.1:8081:8080", "192.168.1.10:8081:8080",
        "http://127.0.0.1:8081", "http://192.168.1.10:8081", "docker run", "podman run",
    ):
        assert f"`{example}`" in notes[0]
