"""GET /api/v1/app/changelog -- Aenderungsprotokoll fuer jeden angemeldeten Nutzer."""

from __future__ import annotations

import pytest

from nodvard_deck import __version__, changelog


async def _login(client, username="owner1", password="correct-horse-battery"):
    await client.post("/api/v1/auth/bootstrap", json={"username": username, "password": password, "setup_code": "TEST-CODE-2345"})
    r = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def own_dir(tmp_path, monkeypatch):
    """Eigener Ordner statt der echten Dateien, damit die Tests nicht mit jedem Release
    neu geschrieben werden muessen."""
    root = tmp_path / "changelog"
    (root / "versions").mkdir(parents=True)
    (root / "unreleased").mkdir()
    monkeypatch.setattr(changelog, "CHANGELOG_DIR", root)
    changelog.clear_cache()
    yield root
    changelog.clear_cache()


def _release(root, version, entries, date="2026-10-01", title=None):
    head = f'version = "{version}"\ndate = "{date}"\n' + (f'title = "{title}"\n' if title else "")
    (root / "versions" / f"{version}.toml").write_text(head + entries, encoding="utf-8")


@pytest.mark.asyncio
async def test_requires_login(client):
    assert (await client.get("/api/v1/app/changelog")).status_code == 401
    assert (await client.get("/api/v1/app/changelog", headers={"Authorization": "Bearer kaputt"})).status_code == 401


@pytest.mark.asyncio
async def test_content_versions_newest_first_and_unreleased(client, own_dir):
    _release(own_dir, "0.9.0", '[[entries]]\nkind = "neu"\ntext = "Alt."\n')
    _release(
        own_dir, "0.10.0",
        '[[entries]]\nkind = "sicherheit"\ntext = "Lücke zu."\nprs = [17]\n\n[[entries]]\nkind = "behoben"\ntext = "Fehler weg."\n',
        date="2026-10-05", title="Zehner",
    )
    (own_dir / "unreleased" / "feat-x.toml").write_text('[[entries]]\nkind = "verbessert"\ntext = "Schneller."\nprs = [50]\n', encoding="utf-8")
    headers = await _login(client)

    r = await client.get("/api/v1/app/changelog", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"] == __version__
    assert body["build"] is None
    assert [v["version"] for v in body["versions"]] == ["0.10.0", "0.9.0"]  # numerisch, nicht als Text
    newest = body["versions"][0]
    assert (newest["date"], newest["title"]) == ("2026-10-05", "Zehner")
    assert newest["entries"] == [
        {"kind": "sicherheit", "text": "Lücke zu.", "prs": [17]},
        {"kind": "behoben", "text": "Fehler weg.", "prs": []},
    ]
    assert body["versions"][1]["title"] is None
    assert body["unreleased"] == [{"kind": "verbessert", "text": "Schneller.", "prs": [50]}]


@pytest.mark.asyncio
async def test_any_logged_in_user_may_read_it_even_without_permissions(client, own_dir):
    _release(own_dir, "0.1.0", '[[entries]]\nkind = "neu"\ntext = "Los."\n')
    owner = await _login(client)
    created = await client.post("/api/v1/users", json={"username": "gast", "password": "correct-horse-battery"}, headers=owner)
    assert created.status_code == 201, created.text
    login = await client.post("/api/v1/auth/login", json={"username": "gast", "password": "correct-horse-battery"})
    guest = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.get("/api/v1/app/changelog", headers=guest)
    assert r.status_code == 200
    assert r.json()["versions"][0]["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_invalid_files_are_ignored_not_fatal(client, own_dir):
    _release(own_dir, "0.1.0", '[[entries]]\nkind = "neu"\ntext = "Gut."\n')
    (own_dir / "versions" / "0.2.0.toml").write_text('version = "0.2.0"\ndate = "morgen"\n', encoding="utf-8")
    (own_dir / "versions" / "kaputt.toml").write_text("][", encoding="utf-8")
    (own_dir / "unreleased" / "schlecht.toml").write_text('[[entries]]\nkind = "unsinn"\ntext = "x"\n', encoding="utf-8")
    (own_dir / "unreleased" / "gut.toml").write_text('[[entries]]\nkind = "neu"\ntext = "Bleibt."\n', encoding="utf-8")
    headers = await _login(client)
    r = await client.get("/api/v1/app/changelog", headers=headers)
    assert r.status_code == 200, r.text
    assert [v["version"] for v in r.json()["versions"]] == ["0.1.0"]
    assert [e["text"] for e in r.json()["unreleased"]] == ["Bleibt."]


@pytest.mark.asyncio
async def test_empty_changelog_still_reports_the_running_version(client, own_dir):
    headers = await _login(client)
    body = (await client.get("/api/v1/app/changelog", headers=headers)).json()
    assert body == {"current": __version__, "build": None, "unreleased": [], "versions": []}


@pytest.mark.asyncio
async def test_build_id_comes_from_settings(client, own_dir, test_settings):
    headers = await _login(client)
    test_settings.build = "  a156da5  "
    assert (await client.get("/api/v1/app/changelog", headers=headers)).json()["build"] == "a156da5"
    test_settings.build = "   "
    assert (await client.get("/api/v1/app/changelog", headers=headers)).json()["build"] is None


@pytest.mark.asyncio
async def test_shipped_data_is_served(client):
    """Ohne umgebogenen Ordner: die echten Dateien, neueste Version = laufende Version."""
    headers = await _login(client)
    body = (await client.get("/api/v1/app/changelog", headers=headers)).json()
    assert body["versions"][0]["version"] == body["current"] == __version__
    assert all(v["entries"] for v in body["versions"])


@pytest.mark.asyncio
async def test_openapi_reports_the_running_version(client):
    """Das OpenAPI-Dokument hängt an derselben Version wie alles andere (kein festes Alt-Wert)."""
    headers = await _login(client)
    body = (await client.get("/api/v1/system/openapi.json", headers=headers)).json()
    assert body["info"]["version"] == __version__
