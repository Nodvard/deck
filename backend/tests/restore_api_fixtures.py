"""Fixtures fuer die API-Tests zum Wiederherstellen (Owner-Weg und Assistent-Weg)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from nodvard_deck.core import security
from nodvard_deck.core.backup import restore
from nodvard_deck.models import User
from nodvard_deck.services import auth as auth_service
from nodvard_deck.services import backups as backups_service
from nodvard_deck.services import restore as restore_service
from restore_helpers import PASSWORD, REPO_ROOT, make_backup, make_db

OWNER_PW = "correct-horse-battery"
SETUP_CODE = "TEST-CODE-2345"
OCTET = {"Content-Type": "application/octet-stream"}


@pytest.fixture
def rapi(client, test_settings, tmp_path, monkeypatch):
    """Einstellungen mit Datei-Datenbank (nur fuer den Ort; die Anfragen laufen gegen die
    In-Memory-Datenbank des `client`), frischer Prozesszustand, Arbeitsordner = Repo-Wurzel."""
    from nodvard_deck import config

    test_settings.database_url = f"sqlite+aiosqlite:///{tmp_path / 'live.db'}"
    monkeypatch.setattr(config, "_settings", test_settings)
    monkeypatch.chdir(REPO_ROOT)
    from nodvard_deck.main import app

    restore_service.reset_for_tests()
    backups_service.reset_for_tests()
    app.state.restart_requested = False  # `app` ist ein Modul-Singleton ueber alle Tests hinweg
    layout = restore.Layout.from_settings(test_settings)
    yield SimpleNamespace(settings=test_settings, layout=layout, tmp=tmp_path)
    app.state.restart_requested = False  # sonst wuerde ein spaeterer Lifespan-Test den Prozess beenden
    restore_service.reset_for_tests()
    backups_service.reset_for_tests()


async def make_owner(client) -> dict:
    created = await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": OWNER_PW, "setup_code": SETUP_CODE})
    assert created.status_code == 201, created.text
    login = await client.post("/api/v1/auth/login", json={"username": "owner1", "password": OWNER_PW})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def make_role_user(client, db_session, role: str, username: str) -> dict:
    roles = await auth_service.ensure_builtin_roles(db_session)
    user = User(username=username, password_hash=security.hash_password("whatever-1234"), is_active=True)
    user.roles.append(roles[role])
    db_session.add(user)
    await db_session.commit()
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": "whatever-1234"})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def backup_bytes(tmp_path, *, name: str = "gut", secret: str = PASSWORD, owner: str = "anna", users: int = 2, **kw) -> bytes:
    db = make_db(tmp_path / f"{name}.db", owner=owner, users=users, hosts=3)
    files = kw.pop("files", {"master.key": b"MASTER-NEU", "ext/beispiel/dokument.txt": b"ext-neu"})
    path = make_backup(tmp_path / f"{name}.ndbak", db=db, secret=secret, files=files, **kw)
    return path.read_bytes()


async def chunks(data: bytes, size: int = 64 * 1024) -> AsyncIterator[bytes]:
    for offset in range(0, len(data), size):
        yield data[offset : offset + size]


class Tripwire:
    """Ein Datenstrom, der nie gelesen werden darf (Pruefung muss VOR dem Body liegen)."""

    def __init__(self) -> None:
        self.consumed = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        self.consumed = True
        yield b"x" * 1024


def enc(password: str) -> str:
    from urllib.parse import quote

    return quote(password, safe="")
