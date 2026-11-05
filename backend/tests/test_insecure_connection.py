"""Warnung vor unverschlüsseltem Zugriff im Heimnetz."""

import tomllib
from pathlib import Path

import pytest
from nodvard_deck.core import trusted_proxies


@pytest.mark.parametrize(("url", "proxy", "proto", "forwarded_for", "expected"), [
    ("http://192.168.2.10:8080", False, None, None, True),
    ("http://deck.local:8080", False, None, None, True),
    ("https://deck.local", False, None, None, False),
    ("http://localhost:8080", False, None, None, False),
    ("http://LOCALHOST:8080", False, None, None, False),
    ("http://localhost.:8080", False, None, None, False),
    ("http://127.0.0.1:8080", False, None, None, False),
    ("http://[::1]:8080", False, None, None, False),
    ("http://[fd00::10]:8080", False, None, None, True),
    ("http://localhost.example.org", False, None, None, True),
    ("http://deck.local", False, "https", None, True),
    ("http://deck.local", True, "https", None, False),
    ("http://deck.local", True, "http", None, True),
    ("http://deck.local", True, None, None, True),
    ("http://deck.local", True, "invalid", None, True),
    ("http://deck.local", True, "https, http", None, True),
    ("http://deck.local", True, "http, https", None, False),
    ("http://localhost:8080", True, None, None, False),
    ("http://localhost:8080", True, "http", None, True),
    ("http://localhost:8080", True, "http", "unknown", True),
    ("http://localhost:8080", True, "http", "192.168.2.40, unknown", True),
    ("http://localhost:8080", True, None, "unknown", True),
    ("http://localhost:8080", True, "http", "127.0.0.1", False),
    ("http://localhost:8080", True, "http", "::1", False),
    ("http://localhost:8080", True, "https", None, False),
    ("http://127.0.0.1:8080", True, "http", "192.168.2.40", True),
    ("http://localhost:8080", True, "http", "192.168.2.40", True),
    ("http://[::1]:8080", True, "http", "192.168.2.40", True),
    ("http://127.0.0.1:8080", True, "https", "192.168.2.40", False),
    ("http://localhost:8080", True, "https", "192.168.2.40", False),
    ("http://[::1]:8080", True, "https", "192.168.2.40", False),
    ("http://127.0.0.1:8080", False, "http", "192.168.2.40", True),
    ("http://localhost:8080", False, "http", "192.168.2.40", True),
    ("http://[::1]:8080", False, "http", "192.168.2.40", True),
    ("http://localhost:8080", False, "https", "192.168.2.40", True),
    ("http://localhost:8080", False, None, "192.168.2.40", True),
    ("http://localhost:8080", False, "http", None, True),
    ("https://localhost:8080", False, "http", "192.168.2.40", False),
])
async def test_warning_in_existing_endpoints(client, url, proxy, proto, forwarded_for, expected):
    if proxy:
        trusted_proxies.apply(["127.0.0.1"])
    headers = {"X-Forwarded-Proto": proto} if proto else {}
    if forwarded_for:
        headers["X-Forwarded-For"] = forwarded_for
    status = await client.get(f"{url}/api/v1/auth/bootstrap", headers=headers)
    assert status.status_code == 200
    assert status.json()["insecure_connection"] is expected
    assert status.json()["needed"] is True

    created = await client.post("/api/v1/auth/bootstrap", json={
        "username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345",
    })
    assert created.status_code == 201
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
    headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    info = await client.get(f"{url}/api/v1/system/connection", headers=headers)
    assert info.status_code == 200
    assert info.json()["insecure_connection"] is expected
    status = await client.get(f"{url}/api/v1/auth/bootstrap", headers=headers)
    assert status.json()["needed"] is False
    assert status.json()["insecure_connection"] is expected


def test_warning_has_security_changelog_entry():
    changelog_dir = Path(__file__).resolve().parents[1] / "src/nodvard_deck/changelog"
    _assert_warning_security_changelog_entry(changelog_dir)


def _assert_warning_security_changelog_entry(changelog_dir):
    entries = [
        row
        for directory in ("unreleased", "versions")
        for path in (changelog_dir / directory).glob("*.toml")
        for row in tomllib.loads(path.read_text(encoding="utf-8"))["entries"]
    ]
    assert any(row["kind"] == "sicherheit" and "unverschlüsselt" in row["text"] for row in entries)


def test_warning_security_changelog_entry_survives_release(tmp_path):
    fragment = tmp_path / "unreleased/befund-47.toml"
    fragment.parent.mkdir()
    content = '[[entries]]\nkind = "sicherheit"\ntext = "Warnung bei unverschlüsseltem Zugriff."\n'
    fragment.write_text(content, encoding="utf-8")
    _assert_warning_security_changelog_entry(tmp_path)

    published = tmp_path / "versions/0.13.0.toml"
    published.parent.mkdir()
    published.write_text('version = "0.13.0"\ndate = "2026-11-02"\n\n' + content, encoding="utf-8")
    fragment.unlink()

    _assert_warning_security_changelog_entry(tmp_path)
