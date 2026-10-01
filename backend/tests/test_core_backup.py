"""Sicherungsformat (core/backup): Krypto, Kopf, Manifest, Online-Kopie, Ausschlussliste.

Schnelle KDF-Parameter fuer die meisten Tests (`fast_kdf`), die echten Werte (Argon2id
64 MiB) prueft `test_default_kdf_parameters_are_the_planned_ones`.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pyrage
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from nodvard_deck.core.backup import container, crypto, snapshot
from nodvard_deck.core.backup import format as fmt
from nodvard_deck.core.backup.errors import (
    DamagedBackup,
    NewerBackup,
    TooExpensive,
    WrongSecret,
)

PASSWORD = "ein-sehr-langes-passwort"


@pytest.fixture
def fast_kdf(monkeypatch):
    monkeypatch.setattr(crypto, "ARGON2_T", 1)
    monkeypatch.setattr(crypto, "ARGON2_M_KIB", 1024)
    monkeypatch.setattr(crypto, "SCRYPT_WRITE_LOG_N", 10)


# ---------------------------------------------------------------------------
# Bech32 und Schluessel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valid", ["A12UEL5L", "a12uel5l", "abcdef1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw",
                                   "split1checkupstagehandshakeupstreamerranterredcaperred2y9e3w", "?1ezyfcl"])
def test_bech32_accepts_bip173_vectors(valid):
    hrp, data = crypto.bech32_decode(valid)
    assert crypto.bech32_encode(hrp, data) == valid.lower()


@pytest.mark.parametrize("invalid", ["A12uEL5L", "a12uel5m", "1nwldj5", "pzry9x0s0muk", "abc1rzg"])
def test_bech32_rejects_invalid_strings(invalid):
    with pytest.raises(ValueError):
        crypto.bech32_decode(invalid)


def test_identity_from_raw_bytes_matches_x25519_public_key():
    raw = os.urandom(32)
    identity = crypto.identity_string(raw)
    assert identity.startswith("AGE-SECRET-KEY-1") and crypto.is_recovery_key(identity)
    public = X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert crypto.recipient_of(identity) == crypto.bech32_encode("age", public)
    # pyrage liest dieselbe Zeichenkette wie wir
    assert str(pyrage.x25519.Identity.from_str(identity)) == identity


def test_identity_roundtrip_with_pyrage_generated_key():
    generated = str(pyrage.x25519.Identity.generate())
    hrp, raw = crypto.bech32_decode(generated)
    assert hrp == "age-secret-key-" and len(raw) == 32
    assert crypto.identity_string(raw) == generated


def test_derive_key_is_deterministic_and_salted(fast_kdf):
    kdf = crypto.KdfParams.new()
    a = crypto.derive_key(PASSWORD, kdf)
    b = crypto.derive_key(PASSWORD, kdf)
    c = crypto.derive_key(PASSWORD)  # neues Salz
    assert a.recipient == b.recipient and a.identity == b.identity
    assert c.recipient != a.recipient
    assert a.recipient.startswith("age1") and len(a.key_id) == 16
    assert a.identity not in repr(a)


def test_derive_key_normalizes_unicode(fast_kdf):
    kdf = crypto.KdfParams.new()
    composed = crypto.derive_key("Grüße-aus-Köln-123", kdf)
    decomposed = crypto.derive_key("Grüße-aus-Köln-123", kdf)
    assert composed.recipient == decomposed.recipient


def test_default_kdf_parameters_are_the_planned_ones():
    kdf = crypto.KdfParams.new()
    assert (kdf.t, kdf.m_kib, kdf.p, len(kdf.salt)) == (3, 65536, 1, 16)
    assert crypto.KdfParams.from_dict(kdf.to_dict()) == kdf


@pytest.mark.parametrize(
    "change,error",
    [
        ({"m_kib": 128 * 1024 + 1}, TooExpensive),
        ({"m_kib": 256 * 1024}, TooExpensive),
        ({"t": 11}, TooExpensive),
        ({"p": 5}, TooExpensive),
        ({"t": 0}, TooExpensive),
        ({"alg": "pbkdf2"}, DamagedBackup),
        ({"salt": "kurz"}, DamagedBackup),
        ({"t": "3"}, DamagedBackup),
    ],
)
def test_kdf_parameters_are_capped_when_reading(change, error):
    raw = {**crypto.KdfParams.new().to_dict(), **change}
    with pytest.raises(error):
        crypto.KdfParams.from_dict(raw)


# ---------------------------------------------------------------------------
# Passwort-Modus (eigenes age-scrypt) gegen die Referenz
# ---------------------------------------------------------------------------

SIZES = [0, 1, crypto.CHUNK_SIZE - 1, crypto.CHUNK_SIZE, crypto.CHUNK_SIZE + 1, 3 * crypto.CHUNK_SIZE]


def _scrypt_encrypt(data: bytes, password: str = PASSWORD, log_n: int = 10) -> bytes:
    out = io.BytesIO()
    crypto.encrypt_with_passphrase(io.BytesIO(data), out, password, log_n=log_n)
    return out.getvalue()


def _scrypt_decrypt(blob: bytes, password: str = PASSWORD, **kw) -> bytes:
    out = io.BytesIO()
    crypto.decrypt_with_passphrase(io.BufferedReader(io.BytesIO(blob)), out, password, **kw)
    return out.getvalue()


@pytest.mark.parametrize("size", SIZES)
def test_own_scrypt_files_open_with_the_reference(size):
    data = os.urandom(size)
    assert pyrage.passphrase.decrypt(_scrypt_encrypt(data), PASSWORD) == data


@pytest.mark.parametrize("size", [0, crypto.CHUNK_SIZE, 2 * crypto.CHUNK_SIZE + 1])
def test_reference_scrypt_files_open_with_our_reader(size):
    # Die Referenz waehlt ihren Arbeitsfaktor selbst (~1 s, auf schnellen Rechnern auch 19 oder 20),
    # deshalb nur die Grenzfaelle -- und hier ohne unseren Lesedeckel: es geht um das Format.
    data = os.urandom(size)
    assert _scrypt_decrypt(pyrage.passphrase.encrypt(data, PASSWORD), max_log_n=22) == data


def test_scrypt_wrong_password_is_reported_as_such():
    with pytest.raises(WrongSecret):
        _scrypt_decrypt(_scrypt_encrypt(b"geheim"), "falsches-passwort-123")


@pytest.mark.parametrize("where", ["kopf", "mac", "rumpf", "ende"])
def test_scrypt_flipped_byte_is_detected(where):
    blob = bytearray(_scrypt_encrypt(os.urandom(3 * crypto.CHUNK_SIZE + 10)))
    header_end = blob.index(b"\n---") + 4
    pos = {"kopf": 30, "mac": header_end + 3, "rumpf": header_end + 40 + crypto.CHUNK_SIZE, "ende": len(blob) - 3}[where]
    blob[pos] ^= 0x01
    with pytest.raises((DamagedBackup, WrongSecret)):
        _scrypt_decrypt(bytes(blob))


@pytest.mark.parametrize("cut", [1, 17, crypto.CHUNK_SIZE + 16])
def test_scrypt_truncated_file_is_detected(cut):
    blob = _scrypt_encrypt(os.urandom(2 * crypto.CHUNK_SIZE + 100))
    with pytest.raises(DamagedBackup):
        _scrypt_decrypt(blob[:-cut])


def test_scrypt_truncated_at_chunk_boundary_is_detected():
    data = os.urandom(2 * crypto.CHUNK_SIZE)
    blob = _scrypt_encrypt(data)
    # Nach dem ersten vollen Block abschneiden: der erste Block war nicht als letzter markiert.
    header_len = blob.index(b"\n---") + 4
    header_len = blob.index(b"\n", header_len) + 1 + 16
    with pytest.raises(DamagedBackup):
        _scrypt_decrypt(blob[: header_len + crypto.CHUNK_SIZE + 16])


def test_scrypt_appended_data_is_detected():
    blob = _scrypt_encrypt(os.urandom(100))
    with pytest.raises(DamagedBackup):
        _scrypt_decrypt(blob + b"x" * 40)


def test_scrypt_work_factor_is_capped_before_any_work():
    blob = _scrypt_encrypt(b"x", log_n=10)
    tampered = blob.replace(b" 10\n", b" 21\n", 1)
    assert tampered != blob
    with pytest.raises(TooExpensive):
        _scrypt_decrypt(tampered)


def test_read_caps_are_what_the_restore_plan_demands():
    # Lesen: scrypt hoechstens 2^18 (256 MiB; 2^20 waeren 1 GiB und werfen einen Pi mit 1 GB aus dem
    # Speicher), Argon2 hoechstens 128 MiB. Geschrieben wird darunter, sonst oeffnete sich die
    # eigene Sicherung nicht mehr.
    assert crypto.SCRYPT_MAX_LOG_N == 18 and crypto.ARGON2_MAX_M_KIB == 128 * 1024
    assert crypto.SCRYPT_WRITE_LOG_N <= crypto.SCRYPT_MAX_LOG_N
    assert crypto.ARGON2_M_KIB <= crypto.ARGON2_MAX_M_KIB


@pytest.mark.parametrize("log_n,ok", [(18, True), (19, False), (20, False)])
def test_scrypt_read_cap_is_18(log_n, ok):
    blob = _scrypt_encrypt(b"x", log_n=10).replace(b" 10\n", f" {log_n}\n".encode(), 1)
    if ok:
        # Der Deckel greift VOR der Berechnung; mit 18 geht es bis zur Schluesselpruefung
        # (das veraenderte Salz-Wort passt dann nicht mehr zum Kopf -> falsches Passwort).
        with pytest.raises(WrongSecret):
            _scrypt_decrypt(blob)
    else:
        with pytest.raises(TooExpensive, match=f"log2\\(N\\)={log_n}, erlaubt bis 18"):
            _scrypt_decrypt(blob)


def test_scrypt_file_written_with_the_write_factor_opens_at_the_cap():
    # Der echte Schreibwert (18) muss sich mit dem Lesedeckel (18) wieder oeffnen lassen.
    assert _scrypt_decrypt(_scrypt_encrypt(b"daten", log_n=crypto.SCRYPT_WRITE_LOG_N)) == b"daten"


# ---------------------------------------------------------------------------
# Kopf
# ---------------------------------------------------------------------------


def _header(mode: str = "passwort", **kw):
    if mode == "schluessel":
        kw.setdefault("key_id", "0123456789abcdef")
        kw.setdefault("kdf", crypto.KdfParams.new().to_dict())
    return fmt.build_header(created_at=datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc), app_version="0.5.0", mode=mode, **kw)


def test_header_roundtrip_leaves_stream_at_age_start():
    stream = io.BytesIO(fmt.encode_header(_header("schluessel")) + b"age-encryption.org/v1\n")
    header = fmt.read_header(stream)
    assert header["mode"] == "schluessel" and header["format"] == 1
    assert stream.read() == b"age-encryption.org/v1\n"


def test_header_of_newer_format_is_rejected_with_a_hint():
    with pytest.raises(NewerBackup, match="neueren Version"):
        fmt.read_header(io.BytesIO(b"NODVARD-DECK-BACKUP/2\n{}\n"))
    data = fmt.encode_header({**_header(), "format": 7})
    with pytest.raises(NewerBackup):
        fmt.read_header(io.BytesIO(data))


@pytest.mark.parametrize("blob", [b"", b"PK\x03\x04", b"NODVARD-DECK-BACKUP/1\n" + b"{" * 5000 + b"\n", b"NODVARD-DECK-BACKUP/1\n[]\n"])
def test_garbage_header_is_damaged(blob):
    with pytest.raises(DamagedBackup):
        fmt.read_header(io.BytesIO(blob))


@pytest.mark.parametrize("name,ok", [
    ("manifest.json", True), ("db/lattice.db", True), ("files/master.key", True), ("files/ext/a/b.txt", True),
    ("../etc/passwd", False), ("/etc/passwd", False), ("files/../x", False), ("files//x", False),
    ("files/a\\b", False), ("db/other.db", False), ("files/", False),
])
def test_member_names_are_restricted(name, ok):
    assert fmt.is_safe_member(name) is ok


def test_backup_names():
    name = fmt.backup_name(datetime(2026, 10, 1, 3, 4, 5, tzinfo=timezone.utc))
    assert name == "nodvard-deck-sicherung-20261001-030405.ndbak" and fmt.is_backup_name(name)
    assert not fmt.is_backup_name("../nodvard-deck-sicherung-20261001-030405.ndbak")
    assert not fmt.is_backup_name("nodvard-deck-sicherung-20261001-030405.ndbak.json")


# ---------------------------------------------------------------------------
# Online-Kopie und Ausschlussliste
# ---------------------------------------------------------------------------


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("CREATE TABLE b (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
    conn.execute("INSERT INTO alembic_version VALUES ('kern0001'), ('ext0001')")
    conn.execute("CREATE TABLE extensions (id TEXT, version TEXT)")
    conn.execute("INSERT INTO extensions VALUES ('beispiel', '1.2.0')")
    conn.commit()
    conn.close()


def test_online_copy_is_consistent_while_someone_writes(tmp_path):
    src = tmp_path / "live.db"
    _make_db(src)
    stop = threading.Event()
    written = [0]

    def writer() -> None:
        conn = sqlite3.connect(src, timeout=30)
        conn.execute("PRAGMA synchronous=OFF")
        i = 0
        while not stop.is_set():
            i += 1
            with conn:  # eine Transaktion: beide Tabellen oder keine
                conn.execute("INSERT INTO a VALUES (?, ?)", (i, "x" * 500))
                conn.execute("INSERT INTO b VALUES (?, ?)", (i, "y" * 500))
            written[0] = i
        conn.close()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        while written[0] < 200:
            time.sleep(0.005)
        copies = []
        for n in range(3):
            dst = tmp_path / f"copy{n}.db"
            snapshot.online_copy(src, dst)
            copies.append(dst)
    finally:
        stop.set()
        thread.join()
    counts = []
    for dst in copies:
        conn = sqlite3.connect(dst)
        a = conn.execute("SELECT count(*), coalesce(max(id), 0) FROM a").fetchone()
        b = conn.execute("SELECT count(*), coalesce(max(id), 0) FROM b").fetchone()
        assert a == b and a[0] >= 200
        counts.append(a[0])
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        conn.close()
        assert not Path(f"{dst}-wal").exists()
    # Der Schreiber lief waehrend der Kopien weiter (sonst waere der Test wertlos).
    assert written[0] > counts[0]


def test_failed_integrity_check_writes_no_backup(tmp_path, monkeypatch):
    src = tmp_path / "live.db"
    _make_db(src)
    monkeypatch.setattr(snapshot, "_integrity_result", lambda conn: ["*** in database main ***", "Page 3: btree"])
    out = io.BytesIO()
    with pytest.raises(snapshot.IntegrityCheckFailed, match="keine Sicherung"):
        container.write_backup(
            out, header=_header(), sources=snapshot.Sources(db_path=src), tmp_dir=tmp_path,
            encrypt=container.encryptor_for_password(PASSWORD, log_n=10),
            build=None, instance_id="i", jwt_from_env=False, extensions_dir=None,
        )
    assert out.getvalue() == b""


def _data_dir(tmp_path: Path) -> tuple[Path, snapshot.Sources]:
    data = tmp_path / "data"
    (data / "ext" / "beispiel" / "sub").mkdir(parents=True)
    (data / "runs").mkdir()
    (data / "branding").mkdir()
    _make_db(data / "lattice.db")
    (data / "master.key").write_bytes(b"MASTERKEY")
    (data / "vault_keyring.json").write_text("{}")
    (data / "setup_code.txt").write_text("CODE")
    (data / "ext" / "beispiel" / "doc.pdf").write_bytes(os.urandom(200_000))
    (data / "ext" / "beispiel" / "sub" / "notiz.txt").write_text("hallo")
    (data / "ext" / "beispiel" / "cache.db-wal").write_bytes(b"wal")
    (data / "ext" / "beispiel" / "x.db-shm").write_bytes(b"shm")
    (data / "ext" / "beispiel" / "upload.part").write_bytes(b"part")
    (data / "ext" / "beispiel" / "setup_code.txt").write_text("nein")
    (data / "ext" / "beispiel" / "link").symlink_to(data / "master.key")
    (data / "ext" / "beispiel" / "linkdir").symlink_to(data)
    (data / "runs" / "r1.log").write_text("lauf")
    (data / "branding" / "logo.png").write_bytes(b"PNG")
    sources = snapshot.Sources(
        db_path=data / "lattice.db",
        single_files=[("master.key", data / "master.key"), ("vault_keyring.json", data / "vault_keyring.json"),
                      ("jwt_secret.key", data / "jwt_secret.key")],
        trees=[("ext", data / "ext"), ("branding", data / "branding")],
    )
    return data, sources


def test_exclusion_list_and_symlinks(tmp_path):
    _, sources = _data_dir(tmp_path)
    names = {e.arcname for e in snapshot.collect(sources)}
    assert names == {
        "files/master.key", "files/vault_keyring.json", "files/ext/beispiel/doc.pdf",
        "files/ext/beispiel/sub/notiz.txt", "files/branding/logo.png",
    }


def test_skipped_symlinks_are_reported(tmp_path):
    data, sources = _data_dir(tmp_path)
    collected = snapshot.collect_all(sources)
    assert collected.skipped_links == ["ext/beispiel/linkdir", "ext/beispiel/link"]
    assert [e.arcname for e in collected.entries] == [e.arcname for e in snapshot.collect(sources)]
    # Auch eine Quelldatei, die selbst ein Link ist (hier master.key), wird gemeldet.
    (data / "master.key").rename(data / "master.echt")
    (data / "master.key").symlink_to(data / "master.echt")
    again = snapshot.collect_all(sources)
    assert "master.key" in again.skipped_links
    assert "files/master.key" not in {e.arcname for e in again.entries}
    # Ausgeschlossene Namen (z. B. .part) melden nichts, auch nicht als Link.
    (data / "ext" / "beispiel" / "rest.part").symlink_to(data / "master.echt")
    assert "ext/beispiel/rest.part" not in snapshot.collect_all(sources).skipped_links


def test_write_backup_hands_skipped_links_to_the_caller(tmp_path):
    _, sources = _data_dir(tmp_path)
    seen: list[list[str]] = []
    header = fmt.build_header(created_at=datetime(2026, 10, 1, tzinfo=timezone.utc), app_version="0.5.0", mode="passwort")
    out = io.BytesIO()
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    container.write_backup(
        out, header=header, sources=sources, tmp_dir=tmp, encrypt=container.encryptor_for_password("ein-langes-passwort", log_n=10),
        build="abc123", instance_id="inst-1", jwt_from_env=False, extensions_dir=None, on_skipped_links=seen.append,
    )
    assert seen == [["ext/beispiel/linkdir", "ext/beispiel/link"]]


def _write(tmp_path: Path, sources, header, encrypt) -> bytes:
    out = io.BytesIO()
    tmp = tmp_path / "tmp"
    tmp.mkdir(exist_ok=True)
    container.write_backup(
        out, header=header, sources=sources, tmp_dir=tmp, encrypt=encrypt,
        build="abc123", instance_id="inst-1", jwt_from_env=False, extensions_dir=None,
    )
    return out.getvalue()


def _read(tmp_path: Path, blob: bytes, secret: str) -> tuple[dict, Path]:
    dest = tmp_path / f"aus-{os.urandom(4).hex()}"
    dest.mkdir()
    manifest = container.read_backup(io.BufferedReader(io.BytesIO(blob)), secret, dest)
    return manifest, dest


def _key_header(key: crypto.DerivedKey) -> dict:
    return _header("schluessel", key_id=key.key_id, kdf=key.kdf.to_dict())


def test_roundtrip_password_mode(tmp_path, fast_kdf):
    _, sources = _data_dir(tmp_path)
    blob = _write(tmp_path, sources, _header(), container.encryptor_for_password(PASSWORD))
    assert blob.startswith(fmt.MAGIC)
    manifest, dest = _read(tmp_path, blob, PASSWORD)
    assert (dest / "files" / "master.key").read_bytes() == b"MASTERKEY"
    assert (dest / "files" / "ext" / "beispiel" / "sub" / "notiz.txt").read_text() == "hallo"
    assert manifest["db"]["alembic_heads"] == ["ext0001", "kern0001"]
    assert manifest["extensions"] == [{"id": "beispiel", "version": "1.2.0", "alembic_heads": []}]
    assert manifest["instance_id"] == "inst-1" and manifest["build"] == "abc123"
    assert {f["path"] for f in manifest["files"]} >= {"files/master.key"}
    assert not (dest / "files" / "setup_code.txt").exists()
    # Auch ohne Nodvard Deck lesbar: Kopf abschneiden, Rest ist eine normale age-Datei.
    age_part = blob.split(b"\n", 2)[2]
    assert pyrage.passphrase.decrypt(age_part, PASSWORD)[:2] == b"\x1f\x8b"  # gzip


def test_roundtrip_key_mode_with_password_and_recovery_key(tmp_path, fast_kdf):
    _, sources = _data_dir(tmp_path)
    key = crypto.derive_key(PASSWORD)
    blob = _write(tmp_path, sources, _key_header(key), container.encryptor_for_recipient(key.recipient))
    manifest_pw, _ = _read(tmp_path, blob, PASSWORD)
    manifest_key, dest = _read(tmp_path, blob, key.identity)
    assert manifest_pw == manifest_key
    # abgetippt in Kleinbuchstaben und mit Leerzeichen drumherum geht auch
    manifest_lower, _ = _read(tmp_path, blob, f"  {key.identity.lower()}\n")
    assert manifest_lower == manifest_key
    assert (dest / "files" / "master.key").read_bytes() == b"MASTERKEY"
    # age -d -i schluessel.txt: der Rest ist eine normale age-Datei fuer diesen Schluessel
    age_part = blob.split(b"\n", 2)[2]
    assert pyrage.decrypt(age_part, [pyrage.x25519.Identity.from_str(key.identity)])[:2] == b"\x1f\x8b"


@pytest.mark.parametrize("mode", ["passwort", "schluessel"])
def test_wrong_password(tmp_path, fast_kdf, mode):
    _, sources = _data_dir(tmp_path)
    key = crypto.derive_key(PASSWORD)
    if mode == "passwort":
        blob = _write(tmp_path, sources, _header(), container.encryptor_for_password(PASSWORD))
    else:
        blob = _write(tmp_path, sources, _key_header(key), container.encryptor_for_recipient(key.recipient))
    with pytest.raises(WrongSecret):
        _read(tmp_path, blob, "ganz-anderes-passwort")
    with pytest.raises(WrongSecret):
        _read(tmp_path, blob, crypto.derive_key("noch-ein-anderes-pw").identity)


@pytest.mark.parametrize("mode", ["passwort", "schluessel"])
@pytest.mark.parametrize("where", ["kopfzeile", "age-kopf", "rumpf", "ende"])
def test_flipped_byte_anywhere_is_detected(tmp_path, fast_kdf, mode, where):
    _, sources = _data_dir(tmp_path)
    key = crypto.derive_key(PASSWORD)
    if mode == "passwort":
        blob = bytearray(_write(tmp_path, sources, _header(), container.encryptor_for_password(PASSWORD)))
    else:
        blob = bytearray(_write(tmp_path, sources, _key_header(key), container.encryptor_for_recipient(key.recipient)))
    age_start = blob.index(b"age-encryption.org/v1")
    pos = {"kopfzeile": len(fmt.MAGIC) + 5, "age-kopf": age_start + 30, "rumpf": len(blob) // 2, "ende": len(blob) - 2}[where]
    blob[pos] ^= 0x01
    with pytest.raises((DamagedBackup, WrongSecret)):
        _read(tmp_path, bytes(blob), PASSWORD)


@pytest.mark.parametrize("mode", ["passwort", "schluessel"])
@pytest.mark.parametrize("cut", [1, 100, 70_000])
def test_truncated_file_is_detected(tmp_path, fast_kdf, mode, cut):
    _, sources = _data_dir(tmp_path)
    key = crypto.derive_key(PASSWORD)
    if mode == "passwort":
        blob = _write(tmp_path, sources, _header(), container.encryptor_for_password(PASSWORD))
    else:
        blob = _write(tmp_path, sources, _key_header(key), container.encryptor_for_recipient(key.recipient))
    with pytest.raises(DamagedBackup):
        _read(tmp_path, blob[:-cut], PASSWORD)


def test_header_and_manifest_must_agree(tmp_path, fast_kdf):
    _, sources = _data_dir(tmp_path)
    key = crypto.derive_key(PASSWORD)
    header = _key_header(key)
    blob = _write(tmp_path, sources, header, container.encryptor_for_recipient(key.recipient))
    # Kopf gueltig, aber anders (aeltere Version vorgegaukelt) -> passt nicht zum Manifest
    rest = blob.split(b"\n", 2)[2]
    forged = fmt.encode_header({**header, "app_version": "0.1.0"}) + rest
    with pytest.raises(DamagedBackup, match="Kopf passt nicht"):
        _read(tmp_path, forged, PASSWORD)


def test_overpriced_kdf_in_header_is_rejected(tmp_path, fast_kdf):
    _, sources = _data_dir(tmp_path)
    key = crypto.derive_key(PASSWORD)
    header = _key_header(key)
    blob = _write(tmp_path, sources, header, container.encryptor_for_recipient(key.recipient))
    rest = blob.split(b"\n", 2)[2]
    expensive = {**header, "kdf": {**header["kdf"], "m_kib": 4 * 1024 * 1024}}
    with pytest.raises(TooExpensive):
        _read(tmp_path, fmt.encode_header(expensive) + rest, PASSWORD)


def test_extract_rejects_path_traversal_member(tmp_path):
    import gzip
    import tarfile

    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb") as gz, tarfile.open(fileobj=gz, mode="w|") as tar:
        info = tarfile.TarInfo("files/../../boese.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"boes"))
    raw.seek(0)
    dest = tmp_path / "ziel"
    dest.mkdir()
    with pytest.raises(DamagedBackup):
        snapshot.extract_archive(raw, dest, _header())
    assert not (tmp_path / "boese.txt").exists()


@pytest.mark.parametrize("order", [("files/a", "files/a/b"), ("files/a/b", "files/a")])
def test_extract_rejects_file_and_folder_with_the_same_name(tmp_path, order):
    import gzip
    import tarfile

    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb") as gz, tarfile.open(fileobj=gz, mode="w|") as tar:
        for name in order:
            info = tarfile.TarInfo(name)
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
    raw.seek(0)
    dest = tmp_path / "ziel"
    dest.mkdir()
    with pytest.raises(DamagedBackup):
        snapshot.extract_archive(raw, dest, _header())


def test_manifest_is_valid_json_with_header_copy(tmp_path, fast_kdf):
    _, sources = _data_dir(tmp_path)
    blob = _write(tmp_path, sources, _header(), container.encryptor_for_password(PASSWORD))
    manifest, _ = _read(tmp_path, blob, PASSWORD)
    assert manifest["header"] == json.loads(blob.split(b"\n", 2)[1])


def test_rotation_sorts_by_creation_time_not_by_local_name(tmp_path):
    """Nach einem Zonenwechsel nach Westen hat die neuere Sicherung den kleineren Namen."""
    from nodvard_deck.core.backup import store

    def _write(name: str, created: datetime) -> None:
        header = fmt.build_header(created_at=created, app_version="0", mode="passwort")
        (tmp_path / name).write_bytes(fmt.encode_header(header) + b"age...")

    _write("nodvard-deck-sicherung-20261001-090000.ndbak", datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc))
    _write("nodvard-deck-sicherung-20261001-033000.ndbak", datetime(2026, 10, 1, 7, 30, tzinfo=timezone.utc))
    assert store.rotate(tmp_path, 1) == ["nodvard-deck-sicherung-20261001-090000.ndbak"]
    assert (tmp_path / "nodvard-deck-sicherung-20261001-033000.ndbak").exists()


def test_sweep_parts_only_removes_our_leftovers(tmp_path):
    from nodvard_deck.core.backup import store

    ours = [".nodvard-deck-sicherung-20261001-030000.ndbak.part",
            ".nodvard-deck-sicherung-20261001-030000.ndbak.json.part",
            f".{'a' * 32}.ndbak.part"]
    foreign = [".film.mkv.part", ".urlaub.zip.part", "nodvard-deck-sicherung-20261001-030000.ndbak"]
    for name in ours + foreign:
        (tmp_path / name).write_bytes(b"x")
    store.sweep_parts(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(foreign)
