"""Notfall-Werkzeug auf dem Server (`python -m nodvard_deck.admin`): Zugang wiederherstellen, ohne dass jemand angemeldet ist."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import shutil
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import Settings, get_settings
from .core import bootstate, security
from .core.backup import restore as restore_core
from .core.backup.errors import BackupError, WrongSecret
from .db.session import create_engine_for
from .models import User
from .services import audit as audit_service
from .services import auth as auth_service

# Aufruf im Container: `docker compose exec nodvard-deck python -m nodvard_deck.admin <Befehl>`, die Befehle zeigt `--help`.
# Gedacht für den Fall, dass der Inhaber Passwort oder Handy (Zwei-Faktor) verloren hat. Wer den Befehl ausführen kann, hat
# ohnehin Zugriff auf den Server und das Datenverzeichnis (Datenbank, Schlüssel); er braucht deshalb keine Anmeldung, jede
# Änderung kommt aber ins Audit-Protokoll (Akteur `system/cli`). Arbeitet gegen die konfigurierte Datenbank wie die Anwendung.
ACTOR_TYPE = "system"
ACTOR_ID = "cli"

_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
"""Ohne l, i, o, 0, 1: Das Passwort wird oft von einem Bildschirm abgetippt."""


class CliError(Exception):
    """Erwarteter Fehler (z. B. Benutzer nicht gefunden): Meldung ausgeben, Exit-Code 1."""


def generate_temporary_password() -> str:
    """16 Zeichen in vier Gruppen, z. B. `k7mq-x2vd-h9pa-tn4c` (rund 79 Bit)."""
    groups = ("".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(4)) for _ in range(4))
    return "-".join(groups)


async def _find_user(session: AsyncSession, username: str) -> User:
    name = auth_service.normalize_username(username)
    user = (
        await session.execute(select(User).where(func.lower(User.username) == name))
    ).scalar_one_or_none()
    if user is None:
        raise CliError(f"Benutzer „{name}“ gibt es nicht. „list-users“ zeigt alle Benutzer.")
    return user


async def _audit(session: AsyncSession, action: str, user: User, detail: dict | None = None) -> None:
    await audit_service.log(
        session,
        actor_type=ACTOR_TYPE,
        actor_id=ACTOR_ID,
        action=action,
        outcome="success",
        target_type="user",
        target_id=user.id,
        detail=detail,
    )


def _two_factor_on(user: User) -> bool:
    return user.totp_secret_id is not None and user.totp_confirmed_at is not None


async def list_users(session: AsyncSession) -> list[str]:
    users = (await session.execute(select(User).order_by(User.username))).scalars().all()
    if not users:
        return ["Es gibt noch keine Benutzer (die Einrichtung im Browser ist noch nicht gemacht)."]
    lines = [f"{'Benutzername':<24} {'Rolle':<10} {'Status':<10} Zwei-Faktor"]
    for user in users:
        lines.append(
            f"{user.username:<24} {'Inhaber' if user.is_owner else 'Benutzer':<10} "
            f"{'aktiv' if user.is_active else 'gesperrt':<10} "
            f"{'an' if _two_factor_on(user) else 'aus'}"
        )
    return lines


async def reset_password(session: AsyncSession, username: str) -> list[str]:
    user = await _find_user(session, username)
    password = generate_temporary_password()
    user.password_hash = security.hash_password(password)
    await session.flush()
    revoked = await auth_service.revoke_refresh_tokens(session, user.id)
    await _audit(session, "auth.password_reset_cli", user, {"signed_out_sessions": revoked})
    return [
        f"Neues Passwort für „{user.username}“ gesetzt. Es gilt ab sofort:",
        "",
        f"    {password}",
        "",
        f"{revoked} bestehende Anmeldung(en) wurden beendet.",
        "Bitte nach der Anmeldung unter Einstellungen → Mein Konto ein eigenes Passwort setzen.",
        "Die Zwei-Faktor-Anmeldung bleibt, wie sie war.",
    ]


async def disable_two_factor(session: AsyncSession, username: str) -> list[str]:
    user = await _find_user(session, username)
    if not _two_factor_on(user) and user.totp_secret_id is None:
        return [f"„{user.username}“ hat keine Zwei-Faktor-Anmeldung – nichts zu tun."]
    await auth_service.disable_totp(session, user)
    revoked = await auth_service.revoke_refresh_tokens(session, user.id)
    await _audit(session, "auth.2fa_disabled_cli", user, {"signed_out_sessions": revoked})
    return [
        f"Zwei-Faktor-Anmeldung für „{user.username}“ abgeschaltet (auch die Wiederherstellungs-Codes sind gelöscht).",
        f"{revoked} bestehende Anmeldung(en) wurden beendet.",
        "Die Anmeldung geht jetzt nur mit dem Passwort; unter Mein Konto lässt sich die Zwei-Faktor-Anmeldung neu einrichten.",
    ]


def _read_secret() -> str:
    """Passwort oder Wiederherstellungsschlüssel: am Terminal ohne Anzeige, sonst die erste Zeile
    der Standardeingabe (für Skripte wie `restore.sh`)."""
    if sys.stdin.isatty():
        return getpass.getpass("Passwort der Sicherung (oder Wiederherstellungsschlüssel): ")
    line = sys.stdin.readline()
    return line.rstrip("\r\n")


def restore_backup(settings: Settings, file: str, *, assume_yes: bool = False) -> list[str]:
    """Prüft eine Sicherung und merkt sie zum Einspielen vor, ohne Oberfläche und ohne Anmeldung. Ersetzt wird erst beim
    nächsten Start, mit denselben Prüfungen und demselben Rückweg wie bei der Oberfläche."""
    from . import migrate
    from .version import __version__

    source = Path(file)
    if not source.is_file():
        raise CliError(f"Die Datei {file} gibt es nicht (oder sie ist im Container nicht sichtbar).")
    try:
        layout = restore_core.Layout.from_settings(settings)
    except BackupError as exc:
        raise CliError(str(exc)) from exc
    if restore_core.pending_exists(layout):
        raise CliError("Es ist schon eine Wiederherstellung vorgemerkt. Bitte erst neu starten (sie wird eingespielt) oder in den Einstellungen abbrechen.")
    try:
        header = restore_core.read_upload_header(source)
    except BackupError as exc:
        raise CliError(str(exc)) from exc
    secret = _read_secret()
    if not secret:
        raise CliError("Kein Passwort angegeben.")

    restore_core.make_private_dir(layout.restore_dir)
    if bootstate.is_locked(settings.data_dir):
        # Läuft die Anwendung (`compose exec`), kann in `restore/` gerade ein Upload von ihr liegen: nur aufräumen, was
        # längst abgelaufen ist.
        restore_core.sweep(layout)
    else:
        restore_core.cleanup_restore_dir(layout)
    rid = restore_core.new_id()
    try:
        limits = restore_core.extract_limits(
            layout, max_unpacked=settings.restore_max_unpacked_bytes, max_entries=settings.restore_max_entries
        )
        known, _heads = migrate.known_revisions(layout.repo_root)
        print("Prüfe die Sicherung (das kann etwas dauern) …", flush=True)
        staged = restore_core.stage_backup(
            source, secret, layout.restore_dir / rid / restore_core.STAGING_NAME, layout=layout, limits=limits, known=known,
            current_version=__version__, current_instance_id=None, has_accounts=None,
        )
    except WrongSecret as exc:
        shutil.rmtree(layout.restore_dir / rid, ignore_errors=True)
        raise CliError(str(exc)) from exc
    except BackupError as exc:
        shutil.rmtree(layout.restore_dir / rid, ignore_errors=True)
        raise CliError(str(exc)) from exc
    summary = staged.summary
    lines = [
        "Inhalt der Sicherung:",
        f"  erstellt:        {summary.get('created_at') or '–'} (Version {summary.get('app_version') or '–'})",
        f"  Installation:    {summary.get('instance_id') or '–'}",
        f"  Owner:           {summary.get('owner_name') or '–'}",
        f"  Nutzer / Server: {summary.get('users', '–')} / {summary.get('hosts', '–')}",
        "  Erweiterungen:   " + (", ".join(e["id"] for e in summary.get("extensions", [])) or "–"),
        "",
        "Hinweis: age prüft nicht, WER eine Sicherung erstellt hat. Spiele nur Sicherungen ein, die du selbst erstellt hast.",
        *[f"Warnung: {w}" for w in summary.get("warnings", [])],
        "",
        (
            "ACHTUNG: Das Einspielen ersetzt ALLES (Konten, Server, Zugangsdaten, Erweiterungsdaten). "
            f"Der alte Stand bleibt unter {layout.restore_dir}/replaced-… liegen."
        ),
    ]
    if not assume_yes:
        if not sys.stdin.isatty():
            shutil.rmtree(layout.restore_dir / rid, ignore_errors=True)
            raise CliError("Ohne Terminal bitte --yes angeben (und das Passwort über die Standardeingabe schicken).")
        print("\n".join(lines), flush=True)
        if input("Zum Einspielen vormerken? Tippe „ja“: ").strip().lower() not in ("ja", "j", "yes", "y"):
            shutil.rmtree(layout.restore_dir / rid, ignore_errors=True)
            raise CliError("Abgebrochen. Es wurde nichts vorgemerkt.")
        lines = []
    restore_core.write_json_atomic(
        layout.restore_dir / rid / "files.json", {"files": staged.files, "compat": staged.compat, "unpacked_bytes": staged.unpacked_bytes}
    )
    restore_core.write_meta(layout, rid, {"id": rid, "scope": "cli", "created_at": time.time(), "state": "scheduled",
                                          "header": {"mode": header["mode"]}, "summary": summary})
    restore_core.write_pending(
        layout, restore_id=rid, source="cli", actor={"type": "cli", "id": None, "label": "admin (Befehlszeile)", "ip": None},
        sign_out_all=True, staged=staged,
    )
    return [
        *lines,
        "Vorgemerkt. Starte Nodvard Deck jetzt neu (den Container bzw. Dienst neu starten):",
        "Beim Start wird die Sicherung eingespielt, danach melden sich alle neu an.",
        f"Die Vormerkung gilt {restore_core.PENDING_TTL_S // 60} Minuten.",
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nodvard_deck.admin",
        description="Notfall-Werkzeug für den Zugang zu Nodvard Deck (läuft auf dem Server, ohne Anmeldung).",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="BEFEHL")
    sub.add_parser("list-users", help="alle Benutzer anzeigen")
    reset = sub.add_parser("reset-password", help="neues, zufälliges Passwort für einen Benutzer setzen")
    reset.add_argument("username", help="Benutzername")
    off = sub.add_parser("disable-2fa", help="Zwei-Faktor-Anmeldung eines Benutzers abschalten")
    off.add_argument("username", help="Benutzername")
    back = sub.add_parser(
        "restore-backup",
        help="eine Sicherung (.ndbak) prüfen und zum Einspielen vormerken (eingespielt wird beim nächsten Start)",
    )
    back.add_argument("file", help="Pfad der Sicherungsdatei (im Container sichtbar)")
    back.add_argument("--yes", action="store_true", help="ohne Rückfrage vormerken (nur wenn Passwort aus der Standardeingabe kommt)")
    return parser


async def _run(args: argparse.Namespace, settings: Settings) -> list[str]:
    engine = create_engine_for(settings)
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            try:
                if args.command == "list-users":
                    lines = await list_users(session)
                elif args.command == "reset-password":
                    lines = await reset_password(session, args.username)
                else:
                    lines = await disable_two_factor(session, args.username)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            return lines
    finally:
        await engine.dispose()


def drop_root_to_data_owner(settings: Settings) -> None:
    """Als root gestartet (z. B. `docker compose exec` ohne `-u`): zum Besitzer des Datenordners wechseln, gehört er
    root, zum Benutzer `lattice`; gibt es den nicht, Abbruch mit Hinweis statt als root weiterzulaufen."""
    # Das Image gibt die Rechte im Entrypoint ab, `compose exec` läuft aber als root. Ohne Wechsel könnte der Befehl
    # root-eigene Dateien im Datenordner anlegen (z. B. SQLite-Nebendateien), die die Anwendung später nicht schreiben kann.
    # Nicht als root (oder ohne `geteuid`, etwa unter Windows) bleibt alles, wie es ist.
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    try:
        owner = settings.data_dir.stat()
    except OSError:
        return
    uid, gid = owner.st_uid, owner.st_gid
    if uid == 0 or gid == 0:
        import pwd  # nur unter Unix da, dort wo `geteuid` existiert

        try:
            entry = pwd.getpwnam("lattice")
        except KeyError:
            entry = None
        if entry is None or entry.pw_uid == 0 or entry.pw_gid == 0:
            raise CliError(
                "Der Befehl läuft als root, der Datenordner gehört aber auch root, und einen Benutzer „lattice“ gibt es hier nicht. "
                "Bitte als Benutzer „lattice“ aufrufen, also mit `compose exec -u lattice …`."
            )
        uid, gid = entry.pw_uid, entry.pw_gid
    # Reihenfolge wichtig: Zusatzgruppen und Gruppe, erst zuletzt die Benutzernummer.
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)


def main(argv: Sequence[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = settings or get_settings()
        drop_root_to_data_owner(settings)
        if args.command == "restore-backup":
            lines = restore_backup(settings, args.file, assume_yes=args.yes)
        else:
            lines = asyncio.run(_run(args, settings))
    except CliError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - kaputte DB/Verbindung: verständlich melden
        if getattr(args, "command", "") == "restore-backup":
            print(f"Fehler: Die Sicherung ließ sich nicht prüfen oder vormerken ({type(exc).__name__}: {exc}).", file=sys.stderr)
            return 2
        print(
            f"Fehler: Die Datenbank ließ sich nicht öffnen oder ändern ({type(exc).__name__}: {exc}).\n"
            "Läuft der Befehl im Container von Nodvard Deck und wurde die Datenbank schon angelegt?",
            file=sys.stderr,
        )
        return 2
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    # Auch ohne den Entrypoint (`compose exec` erbt dessen umask nicht): neue Dateien nur für den Besitzer.
    os.umask(0o077)
    sys.exit(main())
