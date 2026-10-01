"""Notfall-CLI `python -m nodvard_deck.admin` gegen eine echte temporaere Datei-Datenbank."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pyotp
import pytest
from nodvard_deck import admin
from nodvard_deck.config import Settings
from nodvard_deck.core import security
from nodvard_deck.db.session import create_engine_for
from nodvard_deck.models import AuditEntry, Base, RecoveryCode, RefreshToken, User
from nodvard_deck.services import auth as auth_service
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

OLD_PASSWORD = "altes-passwort-123"

_REAL_DROP = admin.drop_root_to_data_owner


@pytest.fixture(autouse=True)
def _no_privilege_switch(monkeypatch):
    """Die Tests laufen je nach Umgebung als root in einem root-eigenen tmp-Ordner: dort nie wirklich wechseln.
    Der Wechsel selbst wird unten mit `_REAL_DROP` und nachgebauten `os`-Aufrufen geprueft."""
    monkeypatch.setattr(admin, "drop_root_to_data_owner", lambda settings: None)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        env="dev",
        data_dir=tmp_path,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}",
        master_key_path=tmp_path / "master.key",
        vault_keyring_path=tmp_path / "vault_keyring.json",
        jwt_secret_path=tmp_path / "jwt_secret.key",
    )


async def _with_db(settings: Settings, fn):
    engine = create_engine_for(settings)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            result = await fn(session)
            await session.commit()
            return result
    finally:
        await engine.dispose()


def run_db(settings: Settings, fn):
    return asyncio.run(_with_db(settings, fn))


@pytest.fixture
def world(tmp_path):
    """Owner 'nico' (mit 2FA und Sitzung) und Benutzer 'anna' (ohne 2FA), frische DB."""
    settings = _settings(tmp_path)

    async def create(session):
        async with session.bind.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        owner = await auth_service.bootstrap_owner(session, username="nico", password=OLD_PASSWORD)
        secret, _ = await auth_service.start_totp_setup(session, settings, owner)
        await auth_service.confirm_totp_setup(session, settings, owner, code=pyotp.TOTP(secret).now())
        tokens = await auth_service._issue_tokens(
            session, settings, owner, client_type="web", device_name=None, user_agent=None, ip=None
        )
        anna = User(username="anna", password_hash=security.hash_password(OLD_PASSWORD), is_owner=False, is_active=True)
        session.add(anna)
        await session.flush()
        return {"owner_id": owner.id, "anna_id": anna.id, "session_id": tokens.session_id}

    ids = run_db(settings, create)
    return settings, ids


def test_list_users(world, capsys):
    settings, _ = world
    assert admin.main(["list-users"], settings) == 0
    out = capsys.readouterr().out
    assert "Benutzername" in out
    nico = next(line for line in out.splitlines() if line.startswith("nico"))
    anna = next(line for line in out.splitlines() if line.startswith("anna"))
    assert "Inhaber" in nico and nico.rstrip().endswith("an")
    assert "Benutzer" in anna and anna.rstrip().endswith("aus")


def test_list_users_on_empty_database(tmp_path, capsys):
    settings = _settings(tmp_path)

    async def create(session):
        async with session.bind.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    run_db(settings, create)
    assert admin.main(["list-users"], settings) == 0
    assert "noch keine Benutzer" in capsys.readouterr().out


def test_reset_password_prints_new_password_revokes_sessions_and_audits(world, capsys):
    settings, ids = world
    assert admin.main(["reset-password", "NICO"], settings) == 0  # Gross-/Kleinschreibung egal
    out = capsys.readouterr().out
    password = next(line.strip() for line in out.splitlines() if line.startswith("    "))
    assert len(password) == 19 and password.count("-") == 3

    async def check(session):
        user = await session.get(User, ids["owner_id"])
        session_row = await session.get(RefreshToken, ids["session_id"])
        entries = (await session.execute(select(AuditEntry))).scalars().all()
        return user, session_row.revoked_at, entries

    user, revoked_at, entries = run_db(settings, check)
    assert security.verify_password(password, user.password_hash)
    assert not security.verify_password(OLD_PASSWORD, user.password_hash)
    assert revoked_at is not None
    # "forces nothing else": 2FA und Rolle bleiben, Konto bleibt aktiv.
    assert user.totp_confirmed_at is not None and user.is_owner and user.is_active
    (entry,) = [e for e in entries if e.action == "auth.password_reset_cli"]
    assert (entry.actor_type, entry.actor_id) == ("system", "cli")
    assert entry.target_id == ids["owner_id"] and entry.outcome == "success"
    assert password not in str(entry.detail) + str(entry.reason)


def test_reset_password_gives_a_different_password_each_time(world, capsys):
    settings, _ = world
    admin.main(["reset-password", "anna"], settings)
    first = capsys.readouterr().out
    admin.main(["reset-password", "anna"], settings)
    second = capsys.readouterr().out
    assert first != second


def test_disable_2fa_removes_secret_codes_and_sessions(world, capsys):
    settings, ids = world
    assert admin.main(["disable-2fa", "nico"], settings) == 0
    assert "abgeschaltet" in capsys.readouterr().out

    async def check(session):
        user = await session.get(User, ids["owner_id"])
        codes = (await session.execute(select(RecoveryCode))).scalars().all()
        session_row = await session.get(RefreshToken, ids["session_id"])
        entries = (await session.execute(select(AuditEntry).where(AuditEntry.action == "auth.2fa_disabled_cli"))).scalars().all()
        return user, codes, session_row.revoked_at, entries

    user, codes, revoked_at, entries = run_db(settings, check)
    assert user.totp_secret_id is None and user.totp_confirmed_at is None
    assert codes == [] and revoked_at is not None
    assert [(e.actor_type, e.actor_id) for e in entries] == [("system", "cli")]
    # Passwort bleibt unveraendert.
    assert security.verify_password(OLD_PASSWORD, user.password_hash)


def test_disable_2fa_without_2fa_is_harmless(world, capsys):
    settings, _ = world
    assert admin.main(["disable-2fa", "anna"], settings) == 0
    assert "keine Zwei-Faktor-Anmeldung" in capsys.readouterr().out


def test_unknown_user_is_a_clear_error(world, capsys):
    settings, _ = world
    assert admin.main(["reset-password", "gibtsnicht"], settings) == 1
    captured = capsys.readouterr()
    assert "Fehler: Benutzer „gibtsnicht“ gibt es nicht" in captured.err
    assert captured.out == ""


def test_unreachable_database_is_a_clear_error(tmp_path, capsys):
    settings = _settings(tmp_path)  # keine Tabellen angelegt
    assert admin.main(["list-users"], settings) == 2
    assert "Datenbank" in capsys.readouterr().err


def test_missing_command_shows_usage():
    with pytest.raises(SystemExit) as exc:
        admin.main([])
    assert exc.value.code == 2


def test_module_entry_point_runs_against_configured_database(world, tmp_path):
    settings, _ = world
    env = {
        **os.environ,
        "LATTICE_DATA_DIR": str(settings.data_dir),
        "LATTICE_DATABASE_URL": settings.database_url,
    }
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # Als root wechselt der Befehl zum Besitzer des Datenordners (nie root): dann muss der jemandem gehoeren.
        for path in [tmp_path, *tmp_path.rglob("*")]:
            os.chown(path, 1000, 1000)
        for parent in tmp_path.parents[:2]:  # pytests tmp-Ordner sind 0700: fuer Benutzer 1000 durchgaengig machen
            parent.chmod(parent.stat().st_mode | 0o111)
    done = subprocess.run(
        [sys.executable, "-m", "nodvard_deck.admin", "list-users"],
        capture_output=True, text=True, env=env, timeout=60, cwd=tmp_path, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "nico" in done.stdout and "anna" in done.stdout


# --- als root gestartet (docker compose exec ohne -u): nie als root in die Datenbank schreiben ---------


def _fake_ids(monkeypatch, *, euid: int, owner: tuple[int, int]):
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(os, "geteuid", lambda: euid, raising=False)
    monkeypatch.setattr(os, "setgroups", lambda groups: calls.append(("groups", list(groups))), raising=False)
    monkeypatch.setattr(os, "setgid", lambda gid: calls.append(("gid", gid)), raising=False)
    monkeypatch.setattr(os, "setuid", lambda uid: calls.append(("uid", uid)), raising=False)
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self.name == "datenordner":
            return os.stat_result((result.st_mode, result.st_ino, result.st_dev, result.st_nlink, owner[0], owner[1], *result[6:]))
        return result

    monkeypatch.setattr(Path, "stat", stat)
    return calls


def test_as_root_the_cli_switches_to_the_owner_of_the_data_folder_before_touching_the_database(tmp_path, monkeypatch):
    folder = tmp_path / "datenordner"
    folder.mkdir()
    calls = _fake_ids(monkeypatch, euid=0, owner=(1000, 1000))
    _REAL_DROP(_settings(folder))
    # Zuerst Gruppen und Gruppe, zuletzt die Benutzernummer (danach waere kein Wechsel mehr erlaubt).
    assert calls == [("groups", []), ("gid", 1000), ("uid", 1000)]


def test_not_as_root_nothing_is_switched(tmp_path, monkeypatch):
    folder = tmp_path / "datenordner"
    folder.mkdir()
    calls = _fake_ids(monkeypatch, euid=1000, owner=(1000, 1000))
    _REAL_DROP(_settings(folder))
    assert calls == []


@pytest.mark.parametrize("owner", [(0, 0), (0, 1000), (1000, 0)], ids=["root:root", "root:gruppe", "benutzer:root"])
def test_root_owned_folder_falls_back_to_the_image_user(tmp_path, monkeypatch, owner):
    pwd = pytest.importorskip("pwd")
    folder = tmp_path / "datenordner"
    folder.mkdir()
    calls = _fake_ids(monkeypatch, euid=0, owner=owner)
    monkeypatch.setattr(pwd, "getpwnam", lambda name: pwd.struct_passwd(("lattice", "x", 1000, 1000, "", "/nonexistent", "/usr/sbin/nologin")) if name == "lattice" else (_ for _ in ()).throw(KeyError(name)))
    _REAL_DROP(_settings(folder))
    assert calls == [("groups", []), ("gid", 1000), ("uid", 1000)]


def test_root_owned_folder_without_an_image_user_aborts_with_a_clear_hint(tmp_path, monkeypatch, capsys):
    pwd = pytest.importorskip("pwd")
    folder = tmp_path / "datenordner"
    folder.mkdir()
    calls = _fake_ids(monkeypatch, euid=0, owner=(0, 0))

    def missing(name):
        raise KeyError(name)

    monkeypatch.setattr(pwd, "getpwnam", missing)
    with pytest.raises(admin.CliError) as info:
        _REAL_DROP(_settings(folder))
    assert "exec -u lattice" in str(info.value)
    assert calls == [], "nichts gewechselt, nichts als root weitergemacht"
    # Ueber main(): verstaendliche Meldung, Exit-Code 1, keine Datenbank angefasst.
    monkeypatch.setattr(admin, "drop_root_to_data_owner", _REAL_DROP)
    assert admin.main(["list-users"], settings=_settings(folder)) == 1
    assert "exec -u lattice" in capsys.readouterr().err
    assert not (folder / "cli.db").exists()


def test_an_image_user_that_is_root_is_not_accepted(tmp_path, monkeypatch):
    pwd = pytest.importorskip("pwd")
    folder = tmp_path / "datenordner"
    folder.mkdir()
    calls = _fake_ids(monkeypatch, euid=0, owner=(0, 0))
    monkeypatch.setattr(pwd, "getpwnam", lambda name: pwd.struct_passwd(("lattice", "x", 0, 0, "", "/", "/bin/sh")))
    with pytest.raises(admin.CliError):
        _REAL_DROP(_settings(folder))
    assert calls == []


def test_main_switches_before_it_opens_the_database(tmp_path, monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(admin, "drop_root_to_data_owner", lambda settings: order.append("wechsel"))

    async def fake_run(args, settings):
        order.append("datenbank")
        return ["ok"]

    monkeypatch.setattr(admin, "_run", fake_run)
    assert admin.main(["list-users"], settings=_settings(tmp_path)) == 0
    assert order == ["wechsel", "datenbank"]
