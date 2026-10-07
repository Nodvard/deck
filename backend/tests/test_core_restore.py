"""Wiederherstellen (core/backup/restore.py): Staging einer FREMDEN Datei und Einspielen.

Zwei Haelften:
* **Staging/Haertung** -- jede Art boesartiges Archiv (Pfadtraversal, Links, Hardlinks, Geraete,
  fremde Pfade, uebergrosse Eintraege, Bomben, kaputte oder boesartige SQLite-Datei, zu neue
  Migrationsstaende) wird abgelehnt, und danach liegt NICHTS mehr im Staging.
* **Einspielen** -- Verschieben, Rueckweg bei jedem Fehler (auch mitten im Verschieben und nach
  einem Absturz), Anmeldungen beenden, Einrichtungscode entfernen, Aufraeumen.
"""

from __future__ import annotations

import calendar
import gzip
import io
import json
import os
import sqlite3
import tarfile
import time
from pathlib import Path

import pytest
from nodvard_deck.core.backup import crypto, restore
from nodvard_deck.core.backup import format as fmt
from nodvard_deck.core.backup.errors import (
    BackupTooLarge,
    DamagedBackup,
    NotEnoughSpace,
    TooExpensive,
    UnusableBackup,
    WrongSecret,
)
from nodvard_deck.migrate import known_revisions
from restore_helpers import (
    PASSWORD,
    REPO_ROOT,
    craft_backup,
    current_heads,
    fill_live,
    limits,
    make_backup,
    make_db,
    make_layout,
    snapshot_tree,
    stage,
    tar_member,
)

KNOWN = known_revisions(REPO_ROOT)[0]


@pytest.fixture
def layout(tmp_path):
    lay = make_layout(tmp_path)
    restore.make_private_dir(lay.restore_dir)
    return lay


@pytest.fixture
def good_backup(tmp_path):
    db = make_db(tmp_path / "quelle.db", owner="anna", users=3, hosts=4, extensions=[("proxmox", "1.2.0", "enabled"), ("gone", "0.1", "disabled")])
    return make_backup(
        tmp_path / "gut.ndbak", db=db,
        files={
            "master.key": b"MASTER-NEU", "vault_keyring.json": b'{"v": 1}', "jwt_secret.key": b"JWT-NEU",
            "ext/beispiel/dokument.txt": b"ext-neu", "branding/logo.png": b"logo-neu", "runs/lauf.log": b"lauf-neu",
        },
    )


def _only_staging_removed(layout: restore.Layout, rid: str) -> None:
    assert not (layout.restore_dir / rid / restore.STAGING_NAME).exists(), "bei einem Fehler darf kein Staging uebrig bleiben"


# ---------------------------------------------------------------------------
# Staging: der gute Fall
# ---------------------------------------------------------------------------


def test_good_backup_is_staged_and_summarised(layout, good_backup):
    rid, staged = stage(layout, good_backup, has_accounts=True, instance="andere-instanz")
    base = layout.restore_dir / rid / restore.STAGING_NAME
    assert (base / "files" / "master.key").read_bytes() == b"MASTER-NEU"
    assert (base / "files" / "ext" / "beispiel" / "dokument.txt").read_bytes() == b"ext-neu"
    assert (base / "db" / "lattice.db").is_file()
    s = staged.summary
    assert (s["owner_name"], s["users"], s["hosts"], s["app_version"], s["instance_id"]) == ("anna", 3, 4, "0.5.0", "inst-1")
    assert s["created_at"] == "2026-10-01T03:00:00Z" and s["mode"] == "passwort"
    assert [e["id"] for e in s["extensions"]] == ["gone", "proxmox"]
    assert s["includes"] == {"runs": True, "branding": True, "jwt_secret": True}
    assert any("anderen Installation" in w for w in s["warnings"])
    assert staged.files["db/lattice.db"][1] == (base / "db" / "lattice.db").stat().st_size
    assert "manifest.json" not in staged.files
    assert oct(base.stat().st_mode & 0o777) == "0o700"


def test_same_instance_has_no_other_installation_warning(layout, good_backup):
    _rid, staged = stage(layout, good_backup, instance="inst-1")
    assert not any("anderen Installation" in w for w in staged.summary["warnings"])
    # Im Assistenten (noch kein Konto) gibt es nichts zu vergleichen.
    _rid, staged = stage(layout, good_backup, instance="fremd", has_accounts=False)
    assert not any("anderen Installation" in w for w in staged.summary["warnings"])


def test_key_mode_backup_opens_with_password_and_recovery_key(layout, tmp_path):
    db = make_db(tmp_path / "q.db")
    path = make_backup(tmp_path / "k.ndbak", db=db, mode="schluessel", secret="sicherungs-passwort-1")
    stage(layout, path, secret="sicherungs-passwort-1")
    key = crypto.derive_key("sicherungs-passwort-1", crypto.KdfParams(t=1, m_kib=1024, p=1, salt=b"s" * 16))
    stage(layout, path, secret=key.identity)
    with pytest.raises(WrongSecret):
        stage(layout, path, secret="falsches-passwort-123")


def test_wrong_password_leaves_nothing_behind(layout, good_backup):
    rid = restore.new_id()
    with pytest.raises(WrongSecret):
        stage(layout, good_backup, secret="falsches-passwort-123", restore_id=rid)
    _only_staging_removed(layout, rid)


# ---------------------------------------------------------------------------
# Staging: boesartige Archive
# ---------------------------------------------------------------------------

GOOD_DB = b"SQLite format 3\x00" + b"\x00" * 100


@pytest.mark.parametrize("name", [
    "../evil.txt", "files/../../evil.txt", "files/ext/../../../evil.txt", "<ABSOLUT>", "files//ext/x",
    "files/ext/./x", "files\\ext\\x", "db/lattice.db/../x", "files/ext/\x01evil",
])
def test_path_traversal_and_odd_names_are_rejected(layout, tmp_path, name):
    absolute = tmp_path / "absolut-evil.txt"
    name = str(absolute) if name == "<ABSOLUT>" else name
    path = craft_backup(tmp_path / "evil.ndbak", [tar_member(name, b"pwned")])
    rid = restore.new_id()
    with pytest.raises(DamagedBackup, match="unerwarteter Eintrag"):
        stage(layout, path, restore_id=rid)
    _only_staging_removed(layout, rid)
    # Nirgends sonst etwas angelegt: weder neben dem Staging, im Datenordner noch am absoluten Ziel.
    assert not absolute.exists()
    for found in (*layout.data_dir.rglob("evil*"), *tmp_path.rglob("evil.txt")):
        pytest.fail(f"unerwartet angelegt: {found}")


@pytest.mark.parametrize("name", [
    "files/other.txt", "files/setup_code.txt", "files/backups/x", "files/restore/pending.json", "files/.boot/state.json",
    "db/other.db", "db/lattice.db-wal", "evil.sh", "files/master.key/extra", "files/ext", "files/runs",
])
def test_foreign_paths_are_rejected(layout, tmp_path, name):
    path = craft_backup(tmp_path / "evil.ndbak", [tar_member(name, b"x")])
    rid = restore.new_id()
    with pytest.raises(DamagedBackup, match="unerwarteter Eintrag"):
        stage(layout, path, restore_id=rid)
    _only_staging_removed(layout, rid)


@pytest.mark.parametrize("label,member", [
    ("symlink", tar_member("files/ext/link", None, type_=tarfile.SYMTYPE, linkname="/etc/passwd")),
    ("symlink-relativ", tar_member("files/ext/link", None, type_=tarfile.SYMTYPE, linkname="../../master.key")),
    ("hardlink", tar_member("files/ext/hart", None, type_=tarfile.LNKTYPE, linkname="files/master.key")),
    ("geraet-zeichen", tar_member("files/ext/dev", None, type_=tarfile.CHRTYPE)),
    ("geraet-block", tar_member("files/ext/blk", None, type_=tarfile.BLKTYPE)),
    ("fifo", tar_member("files/ext/fifo", None, type_=tarfile.FIFOTYPE)),
    ("ordner-ausserhalb", tar_member("etc", None, type_=tarfile.DIRTYPE)),
    ("ordner-fremd", tar_member("files/other", None, type_=tarfile.DIRTYPE)),
])
def test_links_devices_and_foreign_directories_are_rejected(layout, tmp_path, label, member):
    path = craft_backup(tmp_path / f"{label}.ndbak", [member])
    rid = restore.new_id()
    with pytest.raises(DamagedBackup, match="unerwarteter Eintrag"):
        stage(layout, path, restore_id=rid)
    _only_staging_removed(layout, rid)


def test_allowed_directory_entries_are_accepted_and_regular_files_still_hash_checked(layout, tmp_path):
    db = make_db(tmp_path / "q.db")
    good = make_backup(tmp_path / "g.ndbak", db=db, files={"ext/a/b.txt": b"b"})
    # Ein von Hand gebautes Archiv mit Ordner-Eintraegen, wie ein anderes tar sie schreiben wuerde.
    out = tmp_path / "mit-ordnern.ndbak"
    members = [tar_member("files", None, type_=tarfile.DIRTYPE), tar_member("files/ext", None, type_=tarfile.DIRTYPE),
               tar_member("files/ext/a", None, type_=tarfile.DIRTYPE), tar_member("db", None, type_=tarfile.DIRTYPE)]
    # Inhalt der guten Sicherung entpacken und samt Ordnern neu verpacken.
    rid, staged = stage(layout, good)
    base = layout.restore_dir / rid / restore.STAGING_NAME
    members += [tar_member("db/lattice.db", (base / "db/lattice.db").read_bytes()), tar_member("files/ext/a/b.txt", b"b")]
    manifest = {
        "format": 1, "app_version": "0.5.0", "created_at": "2026-10-01T03:00:00Z", "instance_id": "i", "jwt_from_env": False, "build": None,
        "header": fmt.build_header(created_at=__import__("restore_helpers").CREATED, app_version="0.5.0", mode="passwort"),
        "db": {"dialect": "sqlite", "sha256": staged.files["db/lattice.db"][0], "size": staged.files["db/lattice.db"][1], "alembic_heads": current_heads()},
        "extensions": [], "files": [{"path": "files/ext/a/b.txt", "sha256": staged.files["files/ext/a/b.txt"][0], "size": 1, "mode": 420}],
    }
    craft_backup(out, members, manifest=manifest)
    rid2, staged2 = stage(layout, out)
    assert (layout.restore_dir / rid2 / "staging" / "files" / "ext" / "a" / "b.txt").read_bytes() == b"b"
    assert set(staged2.files) == {"db/lattice.db", "files/ext/a/b.txt"}


def test_oversized_entry_is_rejected_before_it_is_written(layout, tmp_path):
    path = craft_backup(tmp_path / "gross.ndbak", [tar_member("files/ext/riesig.bin", b"a" * 5000)])
    rid = restore.new_id()
    with pytest.raises(BackupTooLarge):
        stage(layout, path, restore_id=rid, lim=limits(max_file_bytes=1000))
    _only_staging_removed(layout, rid)


def test_entry_claiming_more_than_it_delivers_is_damaged(layout, tmp_path):
    info, _ = tar_member("files/ext/luege.bin", b"kurz", size=10_000_000_000)
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
        gz.write(info.tobuf(tarfile.PAX_FORMAT) + b"kurz")  # ein Kopf, der Gigabyte verspricht, danach nur 4 Byte
    path = craft_backup(tmp_path / "luege.ndbak", [], raw_gz=buffer.getvalue())
    rid = restore.new_id()
    with pytest.raises((DamagedBackup, BackupTooLarge)):
        stage(layout, path, restore_id=rid, lim=limits(max_file_bytes=1 << 20))
    _only_staging_removed(layout, rid)


def test_gnu_sparse_entries_are_rejected(layout, tmp_path):
    # Ein "duenner" Eintrag, der sich beim Entpacken zu riesigen Nullen ausdehnen koennte.
    info, _ = tar_member("files/ext/duenn.bin", b"x", type_=tarfile.GNUTYPE_SPARSE, size=1)
    path = craft_backup(tmp_path / "sparse.ndbak", [(info, b"x")])
    rid = restore.new_id()
    with pytest.raises(DamagedBackup, match="unerwarteter Eintrag"):
        stage(layout, path, restore_id=rid)
    _only_staging_removed(layout, rid)


def test_declared_sizes_add_up_against_the_total_limit(layout, tmp_path):
    members = [tar_member(f"files/ext/g{i}.bin", b"a" * 400) for i in range(10)]
    path = craft_backup(tmp_path / "summe.ndbak", members)
    with pytest.raises(BackupTooLarge, match="größer als erlaubt"):
        stage(layout, path, lim=limits(max_total_bytes=3000, max_file_bytes=1000))


def test_integrity_check_on_a_wal_mode_file_and_without_trusting_the_schema(tmp_path):
    from nodvard_deck.core.backup import snapshot

    db = make_db(tmp_path / "wal.db", wal=True)
    snapshot.integrity_check(db)  # mode=ro&immutable=1: auch eine WAL-Datei ohne -wal/-shm daneben ist lesbar
    junk = tmp_path / "kein.db"
    junk.write_bytes(b"x" * 5000)
    with pytest.raises(DamagedBackup):
        snapshot.integrity_check(junk)


def test_too_many_entries_are_rejected(layout, tmp_path):
    members = [tar_member(f"files/ext/d{i}.txt", b"") for i in range(30)]
    path = craft_backup(tmp_path / "viele.ndbak", members)
    rid = restore.new_id()
    with pytest.raises(BackupTooLarge, match="mehr als 10 Einträge"):
        stage(layout, path, restore_id=rid, lim=limits(max_entries=10))
    _only_staging_removed(layout, rid)


def test_gzip_bomb_is_stopped_by_the_unpacked_size_limit(layout, tmp_path):
    # 40 MiB Nullen sind als gzip nur ~40 KiB gross.
    zeros = b"\0" * (40 * 1024 * 1024)
    path = craft_backup(tmp_path / "bombe.ndbak", [tar_member("files/ext/null.bin", zeros)])
    assert path.stat().st_size < 1_000_000
    rid = restore.new_id()
    with pytest.raises(BackupTooLarge, match="größer als erlaubt"):
        stage(layout, path, restore_id=rid, lim=limits(max_total_bytes=1024 * 1024, max_file_bytes=1 << 40))
    _only_staging_removed(layout, rid)
    # Dasselbe als Platzgrenze: andere Meldung, anderer Fehlertyp (-> 507 in der API).
    with pytest.raises(NotEnoughSpace, match="Zu wenig freier Speicher"):
        stage(layout, path, restore_id=restore.new_id(), lim=limits(max_total_bytes=1024 * 1024, max_file_bytes=1 << 40, space_limited=True))


def test_tar_overhead_counts_too(layout, tmp_path):
    # Viele winzige Dateien: die Kopfzeilen (512 Byte je Eintrag) zaehlen zur entpackten Menge.
    members = [tar_member(f"files/ext/k{i}", b"") for i in range(200)]
    path = craft_backup(tmp_path / "klein.ndbak", members)
    with pytest.raises(BackupTooLarge):
        stage(layout, path, lim=limits(max_total_bytes=50 * 512, max_entries=10_000))


def test_extract_limits_follow_the_free_space(layout, monkeypatch):
    monkeypatch.setattr(restore.store, "free_bytes", lambda path: 500 * 1024 * 1024)
    lim = restore.extract_limits(layout, max_unpacked=16 << 30, max_entries=100)
    assert lim.space_limited and lim.max_total_bytes == 500 * 1024 * 1024 - restore.SPACE_RESERVE
    lim = restore.extract_limits(layout, max_unpacked=100 * 1024 * 1024, max_entries=100)
    assert not lim.space_limited and lim.max_total_bytes == 100 * 1024 * 1024
    monkeypatch.setattr(restore.store, "free_bytes", lambda path: 100 * 1024 * 1024)
    with pytest.raises(NotEnoughSpace):
        restore.extract_limits(layout, max_unpacked=1 << 30, max_entries=100)


def test_duplicate_entry_and_entry_after_manifest_are_rejected(layout, tmp_path):
    path = craft_backup(tmp_path / "doppelt.ndbak", [tar_member("files/ext/a", b"1"), tar_member("files/ext/a", b"2")])
    with pytest.raises(DamagedBackup):
        stage(layout, path)
    path = craft_backup(tmp_path / "nach.ndbak", [], manifest={"x": 1})
    # Ein Eintrag NACH dem Manifest: das Manifest muss der letzte sein.
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb") as gz, tarfile.open(fileobj=gz, mode="w|") as tar:
        payload = b"{}"
        info = tarfile.TarInfo("manifest.json")
        info.size = 2
        tar.addfile(info, io.BytesIO(payload))
        tar.addfile(tarfile.TarInfo("files/ext/spaet"), io.BytesIO(b""))
    path = craft_backup(tmp_path / "nach2.ndbak", [], raw_gz=raw.getvalue())
    with pytest.raises(DamagedBackup):
        stage(layout, path)


def test_missing_manifest_and_wrong_hashes_and_truncation(layout, tmp_path, good_backup):
    path = craft_backup(tmp_path / "ohne.ndbak", [tar_member("files/ext/a", b"1")])
    with pytest.raises(DamagedBackup, match="Inhaltsverzeichnis fehlt"):
        stage(layout, path)
    blob = good_backup.read_bytes()
    cut = tmp_path / "abgeschnitten.ndbak"
    cut.write_bytes(blob[: len(blob) // 2])
    rid = restore.new_id()
    with pytest.raises(DamagedBackup):
        stage(layout, cut, restore_id=rid)
    _only_staging_removed(layout, rid)
    flipped = bytearray(blob)
    flipped[len(blob) // 2] ^= 0x01
    bad = tmp_path / "gekippt.ndbak"
    bad.write_bytes(bytes(flipped))
    with pytest.raises(DamagedBackup):
        stage(layout, bad, restore_id=restore.new_id())


def test_deeply_nested_manifest_is_damaged_not_a_crash(layout, tmp_path):
    deep = ("[" * 200_000 + "]" * 200_000).encode()
    info = tarfile.TarInfo("manifest.json")
    info.size = len(deep)
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb") as gz, tarfile.open(fileobj=gz, mode="w|") as tar:
        tar.addfile(info, io.BytesIO(deep))
    path = craft_backup(tmp_path / "tief.ndbak", [], raw_gz=raw.getvalue())
    with pytest.raises(DamagedBackup):
        stage(layout, path)


def test_not_a_backup_and_newer_format(layout, tmp_path):
    junk = tmp_path / "junk.ndbak"
    junk.write_bytes(b"das ist keine Sicherung")
    with pytest.raises(DamagedBackup):
        restore.read_upload_header(junk)
    newer = tmp_path / "neuer.ndbak"
    newer.write_bytes(b"NODVARD-DECK-BACKUP/2\n{}\n")
    with pytest.raises(Exception, match="neueren Version"):
        restore.read_upload_header(newer)


# ---------------------------------------------------------------------------
# Staging: die SQLite-Datei ist nicht vertrauenswuerdig
# ---------------------------------------------------------------------------


def _backup_with_db(tmp_path: Path, db: Path, name: str = "x.ndbak", **kw) -> Path:
    return make_backup(tmp_path / name, db=db, **kw)


@pytest.mark.parametrize("sql,what", [
    ("CREATE TRIGGER t AFTER INSERT ON users BEGIN UPDATE users SET is_owner = 1; END", "Trigger"),
    ("CREATE VIEW v AS SELECT username FROM users", "Ansicht"),
])
def test_database_with_triggers_or_views_is_refused(layout, tmp_path, sql, what):
    db = make_db(tmp_path / "boese.db")
    conn = sqlite3.connect(db)
    conn.execute(sql)
    conn.commit()
    conn.close()
    rid = restore.new_id()
    with pytest.raises(UnusableBackup, match=what):
        stage(layout, _backup_with_db(tmp_path, db), restore_id=rid)
    _only_staging_removed(layout, rid)


def test_database_with_virtual_table_is_refused(layout, tmp_path):
    db = make_db(tmp_path / "virtuell.db")
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE VIRTUAL TABLE v USING fts5(a)")
    except sqlite3.OperationalError:
        pytest.skip("dieses SQLite hat kein fts5")
    conn.commit()
    conn.close()
    with pytest.raises(UnusableBackup, match="virtuelle Tabelle"):
        stage(layout, _backup_with_db(tmp_path, db))


def test_untrusted_connection_is_read_only_and_denies_everything_but_reading(tmp_path):
    db = make_db(tmp_path / "x.db")
    with restore.open_untrusted(db) as conn:
        assert conn.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        for sql in ("CREATE TABLE neu (a)", "DELETE FROM users", "ATTACH DATABASE ':memory:' AS x", "PRAGMA journal_mode=WAL",
                    "PRAGMA writable_schema=ON", "INSERT INTO users (id) VALUES ('x')", "DROP TABLE users", "VACUUM"):
            with pytest.raises(sqlite3.DatabaseError):
                conn.execute(sql)
    assert sqlite3.connect(db).execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_wal_mode_database_can_be_inspected(tmp_path):
    db = make_db(tmp_path / "wal.db", wal=True)
    assert restore.inspect_db(db).users == 1
    assert not Path(f"{db}-wal").exists() or True  # immutable: es darf nichts danebenliegen, das wir angelegt haben


def test_database_path_with_special_characters(tmp_path):
    weird = tmp_path / "ein ordner mit % und ? und #"
    weird.mkdir()
    db = make_db(weird / "datenbank.db")
    assert restore.inspect_db(db).owner_name == "nico"


def test_corrupt_database_fails_integrity_check(layout, tmp_path):
    db = make_db(tmp_path / "kaputt.db")
    # Mitten in die Tabellenseiten schreiben: die Datei bleibt eine SQLite-Datei, der Check schlaegt an.
    blob = bytearray(db.read_bytes())
    for offset in range(4096 * 3, min(len(blob), 4096 * 12), 7):
        blob[offset] ^= 0xFF
    db.write_bytes(bytes(blob))
    rid = restore.new_id()
    with pytest.raises((DamagedBackup, Exception)) as exc:
        stage(layout, _backup_with_db(tmp_path, db), restore_id=rid)
    assert "Datenbank" in str(exc.value) or "Sicherung" in str(exc.value)
    _only_staging_removed(layout, rid)


def test_non_sqlite_file_as_database_is_damaged(layout, tmp_path):
    # Eine Sicherung, deren "Datenbank" gar keine ist; das Manifest stimmt zu den Bytes.
    junk = b"kein sqlite" * 100
    import hashlib

    manifest = {
        "format": 1, "app_version": "0.5.0", "created_at": "2026-10-01T03:00:00Z", "instance_id": "i", "jwt_from_env": False, "build": None,
        "header": fmt.build_header(created_at=__import__("restore_helpers").CREATED, app_version="0.5.0", mode="passwort"),
        "db": {"dialect": "sqlite", "sha256": hashlib.sha256(junk).hexdigest(), "size": len(junk), "alembic_heads": []},
        "extensions": [], "files": [],
    }
    path = craft_backup(tmp_path / "keine-db.ndbak", [tar_member("db/lattice.db", junk)], manifest=manifest)
    # integrity_check in extract_archive schlaegt zuerst an
    with pytest.raises(DamagedBackup):
        stage(layout, path)


def test_unknown_newer_revision_is_refused_with_a_clear_text(layout, tmp_path):
    db = make_db(tmp_path / "neu.db", versions=[*current_heads()[:-1], "ffffffffffff"])
    with pytest.raises(UnusableBackup, match="neueren .* Version von Nodvard Deck"):
        stage(layout, _backup_with_db(tmp_path, db, app_version="9.9.9"))


def test_database_without_version_table_is_refused(layout, tmp_path):
    db = make_db(tmp_path / "ohne.db", versions=[])
    with pytest.raises(UnusableBackup, match="kein Migrationsstand"):
        stage(layout, _backup_with_db(tmp_path, db))


def test_missing_extension_with_its_own_migrations_is_named(layout, tmp_path):
    # Die Quelle hatte eine Erweiterung "zusatz" mit eigenem Zweig (Kopf "ffffffffffff").
    ext_src = tmp_path / "ext-quelle"
    (ext_src / "zusatz" / "migrations" / "versions").mkdir(parents=True)
    (ext_src / "zusatz" / "migrations" / "versions" / "0001_x.py").write_text('revision = "ffffffffffff"\n')
    db = make_db(tmp_path / "zusatz.db", versions=[*current_heads(), "ffffffffffff"], extensions=[("zusatz", "1.0", "enabled")])
    backup = _backup_with_db(tmp_path, db, extensions_dir=ext_src)
    layout_without = make_layout(tmp_path / "ziel", extensions_dir=tmp_path / "leer")
    (tmp_path / "leer").mkdir()
    restore.make_private_dir(layout_without.restore_dir)
    with pytest.raises(UnusableBackup, match="Erweiterung „zusatz“, die in dieser Installation fehlt"):
        stage(layout_without, backup)
    # Ist die Erweiterung da, aber aelter: "neuere Version dieser Erweiterung".
    (tmp_path / "leer" / "zusatz").mkdir()
    with pytest.raises(UnusableBackup, match="Erweiterung „zusatz“ in dieser Sicherung stammen aus einer neueren Version"):
        stage(layout_without, backup)


def _renamed_extension_folder(root: Path, *, revision: str | None = None) -> Path:
    """Installierte Erweiterung `renamed-ext`, frueher `old-ext` (`legacy_ids`), optional mit eigenem Zweig."""
    ext_dir = root / "renamed-ext"
    ext_dir.mkdir(parents=True)
    (ext_dir / "extension.toml").write_text(
        '[extension]\nid = "renamed-ext"\nname = "x"\nlegacy_ids = ["old-ext"]\n', encoding="utf-8"
    )
    if revision:
        (ext_dir / "migrations" / "versions").mkdir(parents=True)
        (ext_dir / "migrations" / "versions" / "0001_x.py").write_text(f'revision = "{revision}"\n')
    return ext_dir


def test_backup_assigns_heads_of_a_renamed_extension_to_its_old_row(tmp_path):
    """Die Registry-Zeile einer umbenannten Erweiterung traegt noch die alte Kennung: das Inhaltsverzeichnis
    ordnet ihr trotzdem die Koepfe ihres Zweigs zu."""
    from nodvard_deck.core.backup import snapshot

    _renamed_extension_folder(tmp_path / "extensions", revision="ffffffffffff")
    db = make_db(tmp_path / "x.db", versions=[*current_heads(), "ffffffffffff"], extensions=[("old-ext", "1.0", "enabled")])

    _heads, extensions = snapshot.db_facts(db, tmp_path / "extensions")

    assert extensions == [{"id": "old-ext", "version": "1.0", "alembic_heads": ["ffffffffffff"]}]


def test_row_of_an_old_id_counts_as_installed_when_restoring(tmp_path):
    """Eine Sicherung mit der Zeile `old-ext` in einer Installation, in der die Erweiterung `renamed-ext` heisst:
    keine Warnung "gibt es hier nicht"."""
    _renamed_extension_folder(tmp_path / "extensions")
    target = make_layout(tmp_path / "ziel", extensions_dir=tmp_path / "extensions")
    restore.make_private_dir(target.restore_dir)
    db = make_db(tmp_path / "x.db", extensions=[("old-ext", "1.0", "enabled"), ("weg-ext", "1.0", "enabled")])

    _rid, staged = stage(target, _backup_with_db(tmp_path, db))

    absent = [w for w in staged.summary["warnings"] if "gibt es hier nicht" in w]
    assert len(absent) == 1 and "weg-ext" in absent[0] and "old-ext" not in absent[0], absent


def test_newer_data_of_a_renamed_extension_is_not_reported_as_missing(tmp_path):
    """Neuere Daten einer umbenannten Erweiterung: die Meldung sagt "neuere Version", nicht "fehlt"."""
    ext_src = tmp_path / "ext-quelle"
    _renamed_extension_folder(ext_src, revision="ffffffffffff")
    db = make_db(tmp_path / "neu.db", versions=[*current_heads(), "ffffffffffff"], extensions=[("old-ext", "1.0", "enabled")])
    backup = _backup_with_db(tmp_path, db, extensions_dir=ext_src)
    _renamed_extension_folder(tmp_path / "hier")  # installiert, aber ohne den neuen Kopf
    target = make_layout(tmp_path / "ziel", extensions_dir=tmp_path / "hier")
    restore.make_private_dir(target.restore_dir)

    with pytest.raises(UnusableBackup, match="Erweiterung „old-ext“ in dieser Sicherung stammen aus einer neueren Version"):
        stage(target, backup)


def test_installed_extension_ids_fall_back_to_the_folder_name(tmp_path):
    root = tmp_path / "extensions"
    _renamed_extension_folder(root)
    (root / "kaputt").mkdir()
    (root / "kaputt" / "extension.toml").write_text("kein TOML {{{", encoding="utf-8")
    (root / "ohne").mkdir()
    (root / "fremd").mkdir()
    (root / "fremd" / "extension.toml").write_text('[extension]\nid = "../boese"\nlegacy_ids = "x"\n', encoding="utf-8")
    (root / "_shared").mkdir()

    assert restore.installed_extension_ids(root) == {"renamed-ext", "old-ext", "kaputt", "ohne", "fremd"}


def test_older_known_revisions_are_fine_and_warn(layout, tmp_path):
    older = current_heads()[:2]
    db = make_db(tmp_path / "alt.db", versions=older)
    rid, staged = stage(layout, _backup_with_db(tmp_path, db, app_version="0.4.0"))
    assert any("Version 0.4.0, installiert ist 0.5.0" in w for w in staged.summary["warnings"])


def test_manifest_heads_must_match_the_database(layout, tmp_path, monkeypatch):
    db = make_db(tmp_path / "x.db")
    backup = _backup_with_db(tmp_path, db)
    from nodvard_deck.core.backup import snapshot

    real = snapshot.db_facts
    monkeypatch.setattr(snapshot, "db_facts", lambda *a, **k: (["aaaaaaaaaaaa"], real(*a, **k)[1]))
    other = _backup_with_db(tmp_path, db, name="y.ndbak")
    with pytest.raises(DamagedBackup, match="passt nicht zur Datenbank"):
        stage(layout, other)
    stage(layout, backup)


def test_backup_without_accounts_warns(layout, tmp_path):
    db = make_db(tmp_path / "leer.db", owner=None, users=0, tokens=0)
    _rid, staged = stage(layout, _backup_with_db(tmp_path, db))
    assert any("noch kein Konto" in w for w in staged.summary["warnings"])
    assert staged.summary["owner_name"] is None and staged.summary["users"] == 0


def test_summary_text_from_a_foreign_file_is_sanitised(layout, tmp_path):
    db = make_db(tmp_path / "x.db", owner="a" * 60)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE users SET username = ?", ("anna\n\x1b[31mrot" + "b" * 100,))
    conn.commit()
    conn.close()
    _rid, staged = stage(layout, _backup_with_db(tmp_path, db))
    name = staged.summary["owner_name"]
    assert "\n" not in name and "\x1b" not in name and len(name) <= 64


# ---------------------------------------------------------------------------
# Vormerkung
# ---------------------------------------------------------------------------


def _pending(layout, staged, rid, **kw):
    return restore.write_pending(
        layout, restore_id=rid, source=kw.pop("source", "owner"), actor={"type": "user", "id": "u1", "label": "anna"},
        sign_out_all=kw.pop("sign_out_all", True), staged=staged, **kw,
    )


def test_pending_roundtrip_and_strict_validation(layout, good_backup):
    rid, staged = stage(layout, good_backup)
    pending = _pending(layout, staged, rid)
    assert restore.read_pending(layout) == pending
    assert pending["backup"]["instance_id"] == "inst-1" and pending["compat"]["extensions"] is not None
    good = restore.read_json(layout.restore_dir / restore.PENDING_NAME)
    broken = [
        {**good, "id": "../../etc"}, {**good, "source": "irgendwer"}, {**good, "version": 2}, {**good, "scheduled_at": "gestern"},
        {**good, "sign_out_all": "ja"}, {**good, "files": {}}, {**good, "files": {"db/lattice.db": ["zzz", 1]}},
        {**good, "files": {**good["files"], "../x": ["0" * 64, 1]}}, {**good, "files": {**good["files"], "manifest.json": ["0" * 64, 1]}},
        {**good, "actor": []}, [], "text", None,
    ]
    for item in broken:
        assert restore.validate_pending(item) is None, item


# ---------------------------------------------------------------------------
# Einspielen
# ---------------------------------------------------------------------------


def _prepare(layout, backup, *, source="owner", sign_out_all=True, now=None):
    rid, staged = stage(layout, backup)
    _pending(layout, staged, rid, source=source, sign_out_all=sign_out_all, now=now)
    return rid, staged


def _apply(layout, **kw):
    return restore.apply_pending(layout, known=KNOWN, current_version="0.5.0", **kw)


def test_apply_replaces_everything_and_keeps_the_old_state(layout, good_backup):
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    rid, _ = _prepare(layout, good_backup)
    applied = _apply(layout)
    assert applied is not None
    # Neuer Stand liegt an den Orten des alten.
    assert layout.master_key.read_bytes() == b"MASTER-NEU" and layout.jwt_secret.read_bytes() == b"JWT-NEU"
    assert (layout.ext_dir / "beispiel" / "dokument.txt").read_bytes() == b"ext-neu"
    assert (layout.runs_dir / "lauf.log").read_bytes() == b"lauf-neu" and (layout.branding_dir / "logo.png").read_bytes() == b"logo-neu"
    assert restore.inspect_db(layout.db_path).owner_name == "anna"
    # Der alte Stand ist (noch) unter replaced-... zu finden, samt WAL/SHM, und die alten Nebendateien liegen nicht mehr an der DB.
    old = applied.replaced
    assert (old / "master.key").read_bytes() == b"master-alt" and (old / "lattice.db-wal").read_bytes() == b"alter-wal"
    assert (old / "ext" / "beispiel" / "dokument.txt").read_bytes() == b"ext-alt"
    assert not Path(str(layout.db_path) + "-wal").exists() and not Path(str(layout.db_path) + "-shm").exists()
    # Anmeldungen beendet; der Einrichtungscode bleibt bis zum endgueltigen Einspielen (Zurueckrollen!).
    assert (layout.data_dir / "setup_code.txt").exists()
    assert sqlite3.connect(layout.db_path).execute("SELECT count(*) FROM refresh_tokens").fetchone()[0] == 0
    # Noch nicht endgueltig: Journal liegt da, kein Ergebnis.
    assert (layout.restore_dir / restore.JOURNAL_NAME).exists() and restore.read_result(layout) is None
    applied.commit()
    assert not (layout.data_dir / "setup_code.txt").exists(), "es gibt Konten: der Einrichtungscode ist wertlos"
    result = restore.read_result(layout)
    assert result["ok"] is True and result["id"] == rid and result["replaced"] == old.name and result["audit_logged"] is False
    assert result["actor"]["label"] == "anna" and result["backup"]["instance_id"] == "inst-1"
    assert not (layout.restore_dir / rid).exists() and not (layout.restore_dir / restore.JOURNAL_NAME).exists()
    assert not restore.pending_exists(layout)
    # Alles andere, was nicht Teil der Sicherung ist, blieb: nichts ausserhalb der Ziele wurde angefasst.
    assert before.keys() >= {"setup_code.txt"} and (layout.restore_dir).is_dir()


def test_sign_out_all_false_keeps_sessions(layout, good_backup):
    fill_live(layout, "alt")
    _prepare(layout, good_backup, sign_out_all=False)
    applied = _apply(layout)
    assert sqlite3.connect(layout.db_path).execute("SELECT count(*) FROM refresh_tokens").fetchone()[0] == 2
    applied.commit()


def test_setup_code_stays_when_the_restored_database_has_no_accounts(layout, tmp_path):
    fill_live(layout, "alt")
    db = make_db(tmp_path / "leer.db", owner=None, users=0, tokens=0)
    _prepare(layout, make_backup(tmp_path / "leer.ndbak", db=db))
    applied = _apply(layout)
    assert (layout.data_dir / "setup_code.txt").exists()
    applied.commit()


def test_only_one_replaced_folder_is_kept(layout, tmp_path):
    fill_live(layout, "eins")
    for index in range(3):
        db = make_db(tmp_path / f"q{index}.db", owner=f"owner{index}")
        _prepare(layout, make_backup(tmp_path / f"b{index}.ndbak", db=db, files={"master.key": f"m{index}".encode()}), now=time.time() + index * 5)
        applied = _apply(layout, now=time.time() + index * 5)
        applied.commit()
        time.sleep(0.01)
    replaced = [e.name for e in os.scandir(layout.restore_dir) if e.name.startswith(restore.REPLACED_PREFIX)]
    assert len(replaced) == 1
    assert restore.replaced_info(layout)["name"] == replaced[0]
    assert restore.remove_replaced(layout) == 1 and restore.replaced_info(layout) is None


def test_backup_without_optional_parts_clears_those_parts_of_the_live_state(layout, tmp_path):
    fill_live(layout, "alt")
    db = make_db(tmp_path / "q.db")
    _prepare(layout, make_backup(tmp_path / "minimal.ndbak", db=db, files={"master.key": b"m"}))
    applied = _apply(layout)
    assert not layout.keyring.exists() and not layout.jwt_secret.exists() and not (layout.branding_dir).exists()
    assert layout.ext_dir.is_dir() and layout.runs_dir.is_dir() and not any(layout.ext_dir.iterdir())
    assert (applied.replaced / "vault_keyring.json").exists() and (applied.replaced / "branding" / "logo.png").exists()
    applied.commit()


def test_apply_on_a_fresh_install_without_old_files(layout, good_backup):
    # frischer Datenordner: keine DB, keine Schluessel -- nur das, was die Sicherung mitbringt
    _prepare(layout, good_backup)
    applied = _apply(layout)
    assert layout.master_key.read_bytes() == b"MASTER-NEU" and restore.inspect_db(layout.db_path).users == 3
    applied.commit()
    assert restore.read_result(layout)["ok"]


def test_nothing_pending_is_a_no_op(layout):
    assert _apply(layout) is None
    fresh = make_layout(layout.data_dir.parent / "andere")
    assert _apply(fresh) is None  # nicht einmal restore/ existiert


def _assert_untouched(layout: restore.Layout, before: dict) -> None:
    assert snapshot_tree(layout.data_dir) == before


@pytest.mark.parametrize("fail_on", list(range(1, 17)))
def test_failure_in_the_middle_of_moving_restores_the_old_state(layout, good_backup, monkeypatch, fail_on):
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    rid, _ = _prepare(layout, good_backup)
    calls = {"n": 0}
    real = restore._move

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == fail_on:
            raise OSError(18, "Invalid cross-device link")
        real(src, dst)

    monkeypatch.setattr(restore, "_move", flaky)
    try:
        applied = _apply(layout)
    except restore.RestoreFailed as exc:
        assert "Alles ist wie vorher" in str(exc)
        _assert_untouched(layout, before)
        result = restore.read_result(layout)
        assert result["ok"] is False and result["rolled_back"] is True and result["id"] == rid
        assert not (layout.restore_dir / rid).exists() and not restore.pending_exists(layout)
        assert not (layout.restore_dir / restore.JOURNAL_NAME).exists()
    else:
        # So viele Schritte hat das Einspielen nicht: dann ging alles durch.
        assert calls["n"] < fail_on
        applied.commit()


def test_failure_in_the_post_steps_restores_the_old_state(layout, good_backup, monkeypatch):
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    _prepare(layout, good_backup)
    monkeypatch.setattr(restore, "_post_restore", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("disk I/O error")))
    with pytest.raises(restore.RestoreFailed):
        _apply(layout)
    _assert_untouched(layout, before)


def test_failing_rollback_keeps_the_journal_and_a_later_recovery_finishes_the_job(layout, good_backup, monkeypatch):
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    rid, _ = _prepare(layout, good_backup)
    real = restore._move
    state = {"fail_apply_at": 4, "n": 0, "rollback_broken": True}

    def move(src, dst):
        state["n"] += 1
        if state["n"] == state["fail_apply_at"] or (state["n"] > state["fail_apply_at"] and state["rollback_broken"]):
            raise OSError(5, "Input/output error")
        real(src, dst)

    monkeypatch.setattr(restore, "_move", move)
    with pytest.raises(restore.RollbackFailed, match="nicht vollständig zurückgespielt"):
        _apply(layout)
    assert (layout.restore_dir / restore.JOURNAL_NAME).exists(), "ohne Rueckweg bleibt das Journal fuer den naechsten Start"
    assert restore.read_result(layout)["rolled_back"] is False
    # Naechster Start, jetzt funktioniert das Verschieben wieder: der Rest wird zurueckgenommen.
    monkeypatch.setattr(restore, "_move", real)
    recovered = restore.recover_interrupted(layout)
    assert recovered["ok"] is False
    _assert_untouched(layout, before)


def test_crash_before_commit_is_rolled_back_at_the_next_start(layout, good_backup):
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    rid, _ = _prepare(layout, good_backup)
    applied = _apply(layout)
    assert applied is not None  # ... und dann stirbt der Prozess (z. B. in der Migration), commit() kommt nie
    result = restore.recover_interrupted(layout)
    assert result["ok"] is False and "unterbrochen" in result["message"]
    _assert_untouched(layout, before)
    assert not (layout.restore_dir / restore.JOURNAL_NAME).exists() and not restore.pending_exists(layout)


def test_crash_after_commit_marker_is_finished_not_rolled_back(layout, good_backup):
    fill_live(layout, "alt")
    rid, _ = _prepare(layout, good_backup)
    applied = _apply(layout)
    journal = restore.read_json(layout.restore_dir / restore.JOURNAL_NAME)
    journal["state"] = "done"
    restore.write_json_atomic(layout.restore_dir / restore.JOURNAL_NAME, journal)  # commit() starb nach diesem Schritt
    result = restore.recover_interrupted(layout)
    assert result["ok"] is True and layout.master_key.read_bytes() == b"MASTER-NEU"
    assert not (layout.restore_dir / restore.JOURNAL_NAME).exists() and not (layout.restore_dir / rid).exists()
    assert applied.replaced.is_dir()


def test_unreadable_journal_is_never_guessed_at(layout):
    (layout.restore_dir / restore.JOURNAL_NAME).write_text("{kaputt")
    with pytest.raises(restore.RollbackFailed, match="unlesbar"):
        restore.recover_interrupted(layout)
    assert (layout.restore_dir / restore.JOURNAL_NAME).exists()


def test_journal_cannot_point_the_rollback_at_foreign_files(layout, good_backup, tmp_path):
    fill_live(layout, "alt")
    rid, _ = _prepare(layout, good_backup)
    applied = _apply(layout)
    victim = tmp_path / "fremd.txt"
    victim.write_text("nicht loeschen")
    journal = restore.read_json(layout.restore_dir / restore.JOURNAL_NAME)
    journal["plan"][0]["target"] = str(victim)
    restore.write_json_atomic(layout.restore_dir / restore.JOURNAL_NAME, journal)
    with pytest.raises(restore.RollbackFailed):
        restore.recover_interrupted(layout)
    assert victim.read_text() == "nicht loeschen"
    del applied


def test_expired_pending_is_not_applied(layout, good_backup):
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    rid, _ = _prepare(layout, good_backup, now=time.time() - restore.PENDING_TTL_S - 60)
    with pytest.raises(restore.RestoreFailed, match="abgelaufen"):
        _apply(layout)
    _assert_untouched(layout, before)
    assert not (layout.restore_dir / rid).exists() and restore.read_result(layout)["ok"] is False


@pytest.mark.parametrize("tamper", ["datei-aendern", "datei-dazu", "symlink-dazu", "datei-fehlt", "ordner-fremd", "staging-ist-link"])
def test_tampered_staging_is_refused_and_nothing_is_touched(layout, good_backup, tmp_path, tamper):
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    rid, _ = _prepare(layout, good_backup)
    staging = layout.restore_dir / rid / restore.STAGING_NAME
    if tamper == "datei-aendern":
        (staging / "files" / "master.key").write_bytes(b"MANIPULIERT")
    elif tamper == "datei-dazu":
        (staging / "files" / "ext" / "extra.txt").write_text("x")
    elif tamper == "symlink-dazu":
        (staging / "files" / "ext" / "link").symlink_to("/etc/passwd")
    elif tamper == "datei-fehlt":
        (staging / "files" / "vault_keyring.json").unlink()
    elif tamper == "ordner-fremd":
        (staging / "etc").mkdir()
    elif tamper == "staging-ist-link":
        target = tmp_path / "anderswo"
        staging.rename(target)
        staging.symlink_to(target)
    with pytest.raises(restore.RestoreFailed, match="verändert|fehlen"):
        _apply(layout)
    _assert_untouched(layout, before)
    assert restore.read_result(layout)["ok"] is False


def test_bootstrap_source_is_refused_once_an_account_exists(layout, good_backup):
    fill_live(layout, "alt", sidecars=False)  # hat ein Konto
    before = snapshot_tree(layout.data_dir)
    _prepare(layout, good_backup, source="bootstrap")
    with pytest.raises(restore.RestoreFailed, match="Inzwischen wurde ein Konto angelegt"):
        _apply(layout)
    _assert_untouched(layout, before)


def test_bootstrap_source_applies_on_an_empty_install(layout, good_backup):
    _prepare(layout, good_backup, source="bootstrap")
    applied = _apply(layout)
    assert applied is not None
    applied.commit()


def test_database_is_checked_again_at_boot_against_the_images_revisions(layout, good_backup):
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    _prepare(layout, good_backup)
    # Zwischen Vormerken und Neustart wurde das Image getauscht: es kennt die Staende nicht mehr.
    with pytest.raises(restore.RestoreFailed, match="Version"):
        restore.apply_pending(layout, known={"nichtsbekannt"}, current_version="0.4.0")
    _assert_untouched(layout, before)


def test_corrupt_pending_file_is_dropped(layout):
    (layout.restore_dir / restore.PENDING_NAME).write_text('{"id": "../../x"}')
    with pytest.raises(restore.RestoreFailed, match="beschädigt"):
        _apply(layout)
    assert not restore.pending_exists(layout)


def test_hash_tree_rejects_links_and_foreign_names(tmp_path):
    root = tmp_path / "s"
    (root / "files" / "ext").mkdir(parents=True)
    (root / "files" / "ext" / "a").write_text("1")
    assert restore.hash_tree(root) == {"files/ext/a": [__import__("hashlib").sha256(b"1").hexdigest(), 1]}
    (root / "files" / "ext" / "l").symlink_to("a")
    with pytest.raises(DamagedBackup):
        restore.hash_tree(root)
    (root / "files" / "ext" / "l").unlink()
    os.mkfifo(root / "files" / "ext" / "f")
    with pytest.raises(DamagedBackup):
        restore.hash_tree(root)


# ---------------------------------------------------------------------------
# Aufraeumen
# ---------------------------------------------------------------------------


def _fake_staging(layout, rid, age_s, state="uploaded"):
    directory = layout.restore_dir / rid
    (directory / "staging").mkdir(parents=True)
    restore.write_meta(layout, rid, {"id": rid, "created_at": time.time() - age_s, "state": state})
    return directory


def test_sweep_removes_abandoned_staging_after_an_hour_only(layout, good_backup):
    old, fresh = restore.new_id(), restore.new_id()
    _fake_staging(layout, old, restore.STAGING_TTL_S + 10)
    _fake_staging(layout, fresh, 60)
    assert restore.sweep(layout) == [old]
    assert not (layout.restore_dir / old).exists() and (layout.restore_dir / fresh).exists()


def test_sweep_keeps_the_staging_of_a_valid_pending_and_drops_an_expired_one(layout, good_backup):
    rid, staged = stage(layout, good_backup)
    restore.write_meta(layout, rid, {"id": rid, "created_at": time.time() - restore.STAGING_TTL_S - 5, "state": "ready"})
    _pending(layout, staged, rid)
    assert restore.sweep(layout) == []
    assert (layout.restore_dir / rid).exists() and restore.pending_exists(layout)
    removed = restore.sweep(layout, now=time.time() + restore.PENDING_TTL_S + 100)
    assert restore.PENDING_NAME in removed and rid in removed
    assert not (layout.restore_dir / rid).exists() and not restore.pending_exists(layout)


def test_sweep_spares_what_is_being_worked_on(layout):
    busy, idle = restore.new_id(), restore.new_id()
    _fake_staging(layout, busy, restore.STAGING_TTL_S + 10)
    _fake_staging(layout, idle, restore.STAGING_TTL_S + 10)
    assert restore.sweep(layout, keep={busy}) == [idle]
    assert (layout.restore_dir / busy).exists()


def _replaced(layout, age_days, *, suffix="", name_time=True):
    """Ein `replaced-...`-Ordner, der so alt ist (der Name traegt die Zeit, UTC)."""
    when = time.time() - age_days * 86400
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(when))
    path = layout.restore_dir / f"replaced-{stamp}{suffix}"
    path.mkdir()
    (path / "master.key").write_text("alt")
    return path


def test_sweep_never_touches_a_recent_replaced_folder_or_results(layout):
    recent = _replaced(layout, 2)
    restore.write_result(layout, {"ok": True})
    restore.sweep(layout, now=time.time() + 10 * restore.STAGING_TTL_S)
    assert (recent / "master.key").exists() and restore.read_result(layout)["ok"]


def test_the_old_state_is_removed_after_thirty_days_and_not_before(layout):
    assert restore.REPLACED_TTL_S == 30 * 86400
    young, old, older_suffix = _replaced(layout, 29), _replaced(layout, 31, suffix="x"), _replaced(layout, 90, suffix="xx")
    removed = restore.sweep_replaced(layout)
    assert sorted(removed) == sorted([old.name, older_suffix.name])
    assert young.is_dir() and not old.exists() and not older_suffix.exists()
    assert restore.sweep_replaced(layout) == [], "ein zweiter Lauf findet nichts mehr"


def test_sweep_also_clears_the_old_state(layout):
    old = _replaced(layout, 40)
    assert old.name in restore.sweep(layout)
    assert not old.exists()


def test_replaced_folders_with_odd_names_or_from_the_future_are_left_alone(layout):
    (layout.restore_dir / "replaced-future").mkdir()
    future = layout.restore_dir / "replaced-20990101T000000"
    future.mkdir()
    target = layout.restore_dir.parent / "wichtig"
    target.mkdir()
    (target / "datei").write_text("x")
    (layout.restore_dir / "replaced-20200101T000000x").symlink_to(target)  # ein Link: nicht verfolgen, nicht loeschen
    (layout.restore_dir / "replaced-20200101T000001").write_text("eine Datei, kein Ordner")
    assert restore.sweep_replaced(layout) == []
    assert future.is_dir() and (target / "datei").exists()


def test_a_replaced_folder_without_a_time_in_its_name_ages_by_its_modification_time(layout):
    odd = layout.restore_dir / "replaced-manuell"
    odd.mkdir()
    old = time.time() - 40 * 86400
    os.utime(odd, (old, old))
    assert restore.sweep_replaced(layout) == [odd.name]
    fresh = layout.restore_dir / "replaced-andere"
    fresh.mkdir()
    assert restore.sweep_replaced(layout) == []


def test_the_replaced_folder_of_an_unfinished_apply_is_never_swept(layout):
    old = _replaced(layout, 60)
    restore.write_json_atomic(layout.restore_dir / restore.JOURNAL_NAME, {"state": "applying", "replaced": str(old), "plan": [], "pending": {}})
    assert restore.sweep_replaced(layout) == [] and old.is_dir()


def test_replaced_info_says_when_it_will_be_removed(layout):
    path = _replaced(layout, 10)
    info = restore.replaced_info(layout)
    assert info["name"] == path.name and info["size"] == len("alt")
    expires = calendar.timegm(time.strptime(info["expires_at"], "%Y-%m-%dT%H:%M:%SZ"))
    created = calendar.timegm(time.strptime(path.name.removeprefix("replaced-"), "%Y%m%dT%H%M%S"))
    assert expires - created == 30 * 86400


def test_staging_survives_nothing_when_disk_fills_up(layout, good_backup, monkeypatch):
    rid = restore.new_id()
    real_open = os.fdopen

    def full(fd, *a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fdopen", full)
    with pytest.raises(NotEnoughSpace):
        stage(layout, good_backup, restore_id=rid)
    monkeypatch.setattr(os, "fdopen", real_open)
    _only_staging_removed(layout, rid)


def test_upload_size_cap_constants_are_sane():
    assert crypto.SCRYPT_MAX_LOG_N <= 18 and restore.STAGING_TTL_S == 3600 and restore.PENDING_TTL_S == 3600
    _ = TooExpensive
    _ = json


# ---------------------------------------------------------------------------
# Haertung aus dem Sicherheits-Review
# ---------------------------------------------------------------------------


def _hand_written_schema(db: Path, sql: str, params: tuple) -> None:
    """Traegt mit `writable_schema` von Hand etwas in `sqlite_master` ein -- so, wie es ein
    Angreifer mit seiner eigenen Datei tun kann (SQLite richtet sich beim Laden nach dem SQL-Text,
    nicht nach der Spalte `type`)."""
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute(sql, params)
    conn.commit()
    conn.close()


_HIDDEN_TRIGGER = "CREATE TRIGGER t AFTER INSERT ON users BEGIN UPDATE users SET is_owner = 1; END"


@pytest.mark.parametrize("kind,table,sql,what", [
    ("TRIGGER", "users", _HIDDEN_TRIGGER, "Trigger"),
    ("Trigger", "users", _HIDDEN_TRIGGER, "Trigger"),
    ("VIEW", "t", "CREATE VIEW t AS SELECT username FROM users", "Ansicht"),
])
def test_trigger_or_view_with_odd_type_spelling_is_refused(layout, tmp_path, kind, table, sql, what):
    db = make_db(tmp_path / "versteckt.db")
    _hand_written_schema(db, "INSERT INTO sqlite_master(type, name, tbl_name, rootpage, sql) VALUES (?, 't', ?, 0, ?)", (kind, table, sql))
    # Vorher: so ein Trigger lief in der echten Datenbank trotzdem.
    live = sqlite3.connect(db)
    assert live.execute("SELECT count(*) FROM sqlite_master WHERE type = ?", (kind,)).fetchone()[0] == 1
    live.close()
    rid = restore.new_id()
    with pytest.raises(UnusableBackup, match=what):
        stage(layout, _backup_with_db(tmp_path, db), restore_id=rid)
    _only_staging_removed(layout, rid)


def test_virtual_table_with_a_comment_in_its_sql_is_refused(layout, tmp_path):
    db = make_db(tmp_path / "virtuell-kommentar.db")
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE VIRTUAL TABLE v USING rtree(id, a, b)")
    except sqlite3.OperationalError:
        pytest.skip("dieses SQLite hat kein rtree")
    conn.commit()
    conn.close()
    _hand_written_schema(db, "UPDATE sqlite_master SET sql = ? WHERE name = 'v'", ("CREATE/**/VIRTUAL TABLE v USING rtree(id, a, b)",))
    with pytest.raises(UnusableBackup, match="virtuelle Tabelle"):
        stage(layout, _backup_with_db(tmp_path, db))


def test_huge_gnu_long_name_header_is_refused_without_reading_it_into_memory(layout, tmp_path):
    # Ein GNU-Langname-Kopf, der 8 MiB "Name" verspricht: tarfile laese ihn sonst ganz in den Speicher
    # (als gzip winzig; mit einem Gigabyte wirft das einen Pi aus dem Speicher).
    size = 8 * 1024 * 1024
    head = tarfile.TarInfo("././@LongLink")
    head.type = tarfile.GNUTYPE_LONGNAME
    head.size = size
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
        gz.write(head.tobuf(format=tarfile.GNU_FORMAT)[:512])
        gz.write(b"a" * size)
        gz.write(b"\0" * 1024)
    path = craft_backup(tmp_path / "langname.ndbak", [], raw_gz=buffer.getvalue())
    rid = restore.new_id()
    with pytest.raises(DamagedBackup, match="Kopf eines Eintrags"):
        stage(layout, path, restore_id=rid)
    _only_staging_removed(layout, rid)


@pytest.mark.parametrize("label,members", [
    ("name-zu-lang", [tar_member("files/ext/" + "a" * 300, b"x")]),
    ("ordner-nach-datei", [tar_member("files/ext/a", b"x"), tar_member("files/ext/a", None, type_=tarfile.DIRTYPE)]),
    ("datei-unter-datei", [tar_member("files/ext/a", b"x"), tar_member("files/ext/a/b", b"y")]),
])
def test_names_the_file_system_refuses_are_damaged_not_a_crash(layout, tmp_path, label, members):
    path = craft_backup(tmp_path / f"{label}.ndbak", members)
    rid = restore.new_id()
    with pytest.raises(DamagedBackup, match="unerwarteter Eintrag"):
        stage(layout, path, restore_id=rid)
    _only_staging_removed(layout, rid)


def test_names_that_are_not_utf8_are_not_allowed():
    assert fmt.is_restore_file("files/ext/\udcff.txt") is False
    assert fmt.is_restore_dir("files/ext/\udcff") is False
    assert fmt.is_restore_file("files/ext/ä.txt") is True


def test_bootstrap_source_is_refused_when_the_live_database_is_unreadable(layout, good_backup):
    layout.db_path.write_bytes(b"kein sqlite" * 1000)  # ob es Konten gibt, ist nicht feststellbar
    before = snapshot_tree(layout.data_dir)
    _prepare(layout, good_backup, source="bootstrap")
    with pytest.raises(restore.RestoreFailed, match="nicht feststellen"):
        _apply(layout)
    _assert_untouched(layout, before)


def test_pending_from_the_future_is_not_applied_and_expires(layout, good_backup):
    fill_live(layout, "alt")
    before = snapshot_tree(layout.data_dir)
    _prepare(layout, good_backup, now=time.time() + restore.PENDING_TTL_S + 600)
    restore.sweep(layout)
    assert not restore.pending_exists(layout)  # laeuft trotz falsch gestellter Uhr ab
    _prepare(layout, good_backup, now=time.time() + restore.PENDING_TTL_S + 600)
    with pytest.raises(restore.RestoreFailed, match="abgelaufen"):
        _apply(layout)
    _assert_untouched(layout, before)


@pytest.mark.parametrize("leftover", ["-wal", "-journal", "-shm"])
def test_sidecars_left_by_a_crashed_migration_never_end_up_next_to_the_old_database(layout, good_backup, leftover):
    # Der alte Stand wurde sauber beendet (keine -wal/-shm). Nach dem Einspielen stirbt die Migration der
    # eingespielten Datenbank mittendrin und hinterlaesst deren Nebendatei. Beim naechsten Start kommt die
    # alte Datenbank zurueck -- die fremde Nebendatei darf nicht neben ihr liegen bleiben (SQLite wuerde sie
    # auf die alte Datenbank anwenden).
    fill_live(layout, "alt", sidecars=False)
    before = snapshot_tree(layout.data_dir)
    _prepare(layout, good_backup)
    applied = _apply(layout)
    assert applied is not None
    Path(str(layout.db_path) + leftover).write_bytes(b"von der eingespielten Datenbank")
    result = restore.recover_interrupted(layout)
    assert result["ok"] is False
    assert not os.path.lexists(str(layout.db_path) + leftover)
    _assert_untouched(layout, before)


def test_rollback_after_a_failed_migration_also_removes_new_sidecars(layout, good_backup):
    fill_live(layout, "alt", sidecars=False)
    before = snapshot_tree(layout.data_dir)
    _prepare(layout, good_backup)
    applied = _apply(layout)
    Path(str(layout.db_path) + "-wal").write_bytes(b"fremde wal")
    applied.rollback("Migration gescheitert")
    _assert_untouched(layout, before)


# ---------------------------------------------------------------------------
# Kern-Job: alte Staende taeglich aufraeumen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_job_removes_old_states_every_day_without_touching_running_uploads(db_session, tmp_path, monkeypatch):
    from nodvard_deck import config
    from nodvard_deck.core.scheduler import CORE_SCHEDULER_EXT_ID, register_core_jobs
    from nodvard_deck.ext.runtime import get_extension_runtime, reset_extension_runtime
    from nodvard_deck.models import Job
    from sqlalchemy import select

    reset_extension_runtime()
    lay = make_layout(tmp_path)
    restore.make_private_dir(lay.restore_dir)
    old = _replaced(lay, 45)
    young = _replaced(lay, 3, suffix="x")
    upload = restore.new_id()
    (lay.restore_dir / upload).mkdir()
    restore.write_meta(lay, upload, {"id": upload, "created_at": time.time() - restore.STAGING_TTL_S - 100, "state": "uploading"})
    monkeypatch.setattr(config, "_settings", config.Settings(data_dir=lay.data_dir, database_url=f"sqlite+aiosqlite:///{lay.db_path}", env="dev"))
    await register_core_jobs(tmp_path / "runs")
    job = (await db_session.execute(select(Job).where(Job.ext_job_key == "restore-cleanup"))).scalar_one()
    assert job.kind == "core" and job.enabled and job.schedule.count(" ") == 4
    handler = get_extension_runtime().scheduler.get(CORE_SCHEDULER_EXT_ID, "restore-cleanup")
    result = await handler()
    assert result == {"removed": 1}
    assert not old.exists() and young.is_dir() and (lay.restore_dir / upload).is_dir(), "nur der alte Stand, kein Zwischenstand einer laufenden Wiederherstellung"
    reset_extension_runtime()
