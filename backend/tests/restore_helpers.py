"""Bausteine fuer die Tests zum Wiederherstellen: gueltige Datenbanken, echte Sicherungen und
absichtlich boesartige Archive (Pfadtraversal, Links, Geraete, Bomben ...)."""

from __future__ import annotations

import gzip
import io
import json
import os
import sqlite3
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nodvard_deck.core.backup import container, crypto, snapshot
from nodvard_deck.core.backup import format as fmt
from nodvard_deck.core.backup import restore
from nodvard_deck.migrate import known_revisions
from nodvard_deck.models import Base, RefreshToken, User
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

PASSWORD = "ein-sehr-langes-passwort"
REPO_ROOT = Path(__file__).resolve().parents[2]
CREATED = datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone.utc)


def current_heads() -> list[str]:
    return sorted(known_revisions(REPO_ROOT)[1])


def make_db(path: Path, *, owner: str | None = "nico", users: int = 1, hosts: int = 2, versions: list[str] | None = None,
            tokens: int = 2, extensions: list[tuple[str, str, str]] | None = None, wal: bool = False) -> Path:
    """Eine gueltige Datenbank (Tabellen aus den Modellen, `alembic_version` auf den echten
    Staenden dieses Repos), mit Nutzern, Servern und Anmeldungen (`refresh_tokens`)."""
    engine = create_engine(URL.create("sqlite", database=str(path)))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        made: list[User] = []
        for index in range(users):
            name = owner if (index == 0 and owner) else f"nutzer{index}"
            user = User(username=name, password_hash="x", is_owner=(index == 0 and owner is not None), is_active=True)
            session.add(user)
            made.append(user)
        session.flush()
        for _ in range(tokens if made else 0):
            session.add(RefreshToken(user_id=made[0].id, token_hash=os.urandom(32).hex(), expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc)))
        session.commit()
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
    for version in (versions if versions is not None else current_heads()):
        conn.execute("INSERT INTO alembic_version VALUES (?)", (version,))
    conn.commit()
    conn.close()
    _add_rows(path, hosts, extensions or [])
    if wal:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
    return path


def _add_rows(path: Path, hosts: int, extensions: list[tuple[str, str, str]]) -> None:
    from nodvard_deck.models import ExtensionRecord, Host

    engine = create_engine(URL.create("sqlite", database=str(path)))
    with Session(engine) as session:
        for index in range(hosts):
            session.add(Host(name=f"server{index}", address=f"10.0.0.{index + 1}"))
        for ext_id, version, state in extensions:
            session.add(ExtensionRecord(id=ext_id, version=version, api_version="1", state=state))
        session.commit()
    engine.dispose()


def make_layout(root: Path, *, extensions_dir: Path | None = None) -> restore.Layout:
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    return restore.Layout(
        data_dir=data, db_path=data / "lattice.db", master_key=data / "master.key", keyring=data / "vault_keyring.json",
        jwt_secret=data / "jwt_secret.key", ext_dir=data / "ext", branding_dir=data / "branding", runs_dir=data / "runs",
        extensions_dir=extensions_dir, repo_root=REPO_ROOT,
    )


def settings_for(layout: restore.Layout):
    from nodvard_deck import config

    return config.Settings(
        env="dev", data_dir=layout.data_dir, database_url=f"sqlite+aiosqlite:///{layout.db_path}",
        master_key_path=layout.master_key, vault_keyring_path=layout.keyring, jwt_secret_path=layout.jwt_secret,
        extensions_dir=layout.data_dir.parent / "extensions", ext_data_dir=layout.ext_dir,
    )


def fill_live(layout: restore.Layout, marker: str = "alt", *, sidecars: bool = True) -> None:
    """Ein 'laufender' Stand mit Marker, damit man sieht, was ersetzt wurde und was zurueckkam."""
    make_db(layout.db_path, owner=f"{marker}-owner", users=1, hosts=1, tokens=3)
    layout.master_key.write_bytes(f"master-{marker}".encode())
    layout.keyring.write_text(json.dumps({"marker": marker}))
    layout.jwt_secret.write_text(f"jwt-{marker}")
    (layout.ext_dir / "beispiel").mkdir(parents=True)
    (layout.ext_dir / "beispiel" / "dokument.txt").write_text(f"ext-{marker}")
    layout.runs_dir.mkdir(parents=True)
    (layout.runs_dir / "lauf.log").write_text(f"lauf-{marker}")
    layout.branding_dir.mkdir(parents=True)
    (layout.branding_dir / "logo.png").write_bytes(f"logo-{marker}".encode())
    (layout.data_dir / "setup_code.txt").write_text("ABCD-EFGH-JKLM\n")
    if sidecars:
        Path(str(layout.db_path) + "-wal").write_bytes(b"alter-wal")
        Path(str(layout.db_path) + "-shm").write_bytes(b"alter-shm")


def snapshot_tree(root: Path, *, include_boot: bool = False, ignore_sidecars: bool = False) -> dict[str, bytes | None]:
    """Alles unter `root` (ohne `restore/` und, wenn nicht gewuenscht, ohne `.boot/`, den Zustand von
    `nodvard_deck.boot`) als Pfad -> Inhalt, zum Vergleichen vorher/nachher. `ignore_sidecars`: ohne `-wal`/`-shm`
    der Datenbank -- SQLite raeumt kaputte oder leere Nebendateien beim Oeffnen selbst weg (`nodvard_deck.boot` liest die
    Datenbank), bei Tests mit Attrappen-Nebendateien (`fill_live`) bleiben sie also nicht Byte fuer Byte erhalten."""
    out: dict[str, bytes | None] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            for skip in ("restore", *(() if include_boot else (".boot",))):
                if skip in dirnames:
                    dirnames.remove(skip)
        for name in filenames:
            if ignore_sidecars and name.endswith(("-wal", "-shm")):
                continue
            path = Path(dirpath) / name
            out[str(path.relative_to(root))] = path.read_bytes() if not path.is_symlink() else None
        for name in dirnames:
            out.setdefault(str((Path(dirpath) / name).relative_to(root)) + "/", None)
    return out


def make_backup(
    out: Path, *, db: Path, secret: str = PASSWORD, files: dict[str, bytes] | None = None, instance_id: str = "inst-1",
    app_version: str = "0.5.0", extensions_dir: Path | None = None, mode: str = "passwort", jwt_from_env: bool = False,
) -> Path:
    """Eine echte Sicherung ueber die echten Funktionen (`container.write_backup`)."""
    work = out.parent / f"work-{out.name}"
    work.mkdir()
    data = work / "data"
    data.mkdir()
    singles = []
    trees = []
    for rel, content in (files or {}).items():
        target = data / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        top = rel.split("/")[0]
        if "/" not in rel:
            singles.append((rel, target))
        elif (top, data / top) not in trees:
            trees.append((top, data / top))
    sources = snapshot.Sources(db_path=db, single_files=singles, trees=trees)
    if mode == "passwort":
        header = fmt.build_header(created_at=CREATED, app_version=app_version, mode="passwort")
        encrypt = container.encryptor_for_password(secret, log_n=10)
    else:
        key = crypto.derive_key(secret, crypto.KdfParams(t=1, m_kib=1024, p=1, salt=b"s" * 16))
        header = fmt.build_header(created_at=CREATED, app_version=app_version, mode="schluessel", key_id=key.key_id, kdf=key.kdf.to_dict())
        encrypt = container.encryptor_for_recipient(key.recipient)
    tmp = work / "tmp"
    tmp.mkdir()
    with open(out, "wb") as fh:
        container.write_backup(
            fh, header=header, sources=sources, tmp_dir=tmp, encrypt=encrypt, build="test", instance_id=instance_id,
            jwt_from_env=jwt_from_env, extensions_dir=extensions_dir,
        )
    return out


def tar_member(name: str, data: bytes | None = b"x", *, type_: bytes = tarfile.REGTYPE, linkname: str = "", size: int | None = None, mode: int = 0o644) -> tuple[tarfile.TarInfo, bytes | None]:
    info = tarfile.TarInfo(name)
    info.type = type_
    info.linkname = linkname
    info.mode = mode
    if type_ in (tarfile.REGTYPE, tarfile.AREGTYPE):
        info.size = len(data or b"") if size is None else size
    return info, data


def craft_backup(
    out: Path, members: list[tuple[tarfile.TarInfo, bytes | None]], *, secret: str = PASSWORD, manifest: dict | None = None,
    raw_gz: bytes | None = None,
) -> Path:
    """Baut absichtlich eine beliebige Sicherungsdatei (gueltige Verschluesselung, Inhalt nach Wunsch).
    Ohne `manifest` kommt keines dazu; `raw_gz` ersetzt den ganzen tar.gz-Inhalt."""
    header = fmt.build_header(created_at=CREATED, app_version="0.5.0", mode="passwort")
    if raw_gz is None:
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            for info, data in members:
                tar.addfile(info, io.BytesIO(data) if data is not None else None)
            if manifest is not None:
                payload = json.dumps(manifest).encode()
                info = tarfile.TarInfo(fmt.ARC_MANIFEST)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
        raw_gz = buffer.getvalue()
    sealed = io.BytesIO()
    crypto.encrypt_with_passphrase(io.BytesIO(raw_gz), sealed, secret, log_n=10)
    out.write_bytes(fmt.encode_header(header) + sealed.getvalue())
    return out


def limits(**kw: Any) -> snapshot.ExtractLimits:
    base = {"max_entries": 1000, "max_total_bytes": 64 * 1024 * 1024, "max_file_bytes": 64 * 1024 * 1024}
    base.update(kw)
    return snapshot.ExtractLimits(**base)


def stage(layout: restore.Layout, upload: Path, secret: str = PASSWORD, *, restore_id: str | None = None, known: set[str] | None = None,
          has_accounts: bool | None = True, lim: snapshot.ExtractLimits | None = None, instance: str | None = "inst-1") -> tuple[str, restore.Staged]:
    rid = restore_id or restore.new_id()
    staged = restore.stage_backup(
        upload, secret, layout.restore_dir / rid / restore.STAGING_NAME, layout=layout, limits=lim or limits(),
        known=known if known is not None else known_revisions(REPO_ROOT)[0], current_version="0.5.0",
        current_instance_id=instance, has_accounts=has_accounts,
    )
    return rid, staged
