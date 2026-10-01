"""Verschluesselung der Sicherungen im age-Format (https://age-encryption.org/v1).

Zwei Arten, beide ergeben eine normale age-Datei, die auch `age -d` / `rage -d` oeffnet:

* **Sicherungsschluessel (Modus "schluessel")**, fuer automatische Sicherungen und den
  Download "mit meinem Sicherungsschluessel": verschluesselt wird an einen X25519-
  Empfaenger (`age1...`) ueber pyrage (Rust-`rage`, Lesen und Schreiben). Der private
  Schluessel wird NIE gespeichert. Er entsteht bei Bedarf aus dem Sicherungspasswort:

      Passwort (Unicode NFC, UTF-8) --Argon2id(t=3, m=64 MiB, p=1, Salz 16 Byte)--> 32 Byte
      32 Byte = X25519-Geheimnis = age-Identitaet "AGE-SECRET-KEY-1..." (Bech32, unten)

  Gespeichert werden nur Empfaenger, Salz, KDF-Parameter und `key_id`. Die Identitaet
  selbst ist der **Wiederherstellungsschluessel**, den der Owner einmal zu sehen bekommt.

* **Einmal-Passwort (Modus "passwort")**, nur fuer den Download: age-scrypt. pyrage kann
  scrypt nur komplett im Speicher (`pyrage.passphrase`), eine Sicherung kann aber gross
  sein. Deshalb ist die scrypt-Variante hier selbst geschrieben (Header, HMAC, STREAM nach
  der age-Spezifikation, Bausteine aus `cryptography`) und arbeitet in 64-KiB-Bloecken.
  Die Tests pruefen beide Richtungen gegen die Referenz (pyrage.passphrase).

Deckel beim LESEN (eine fremde Datei darf den Rechner nicht minutenlang beschaeftigen und
einen kleinen Rechner nicht aus dem Speicher werfen -- scrypt mit log2(N)=20 braucht 1 GiB):
scrypt log2(N) <= 18 (geschrieben wird mit 18, 256 MiB), Argon2id m <= 128 MiB (geschrieben
mit 64 MiB), t <= 10, p <= 4.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import BinaryIO

import pyrage
from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .errors import DamagedBackup, TooExpensive, WrongSecret

MIN_PASSWORD_LENGTH = 12

ARGON2_T = 3
ARGON2_M_KIB = 64 * 1024
ARGON2_P = 1
SALT_LENGTH = 16

ARGON2_MAX_T = 10
ARGON2_MAX_M_KIB = 128 * 1024
ARGON2_MAX_P = 4

SCRYPT_WRITE_LOG_N = 18
"""Arbeitsfaktor beim Schreiben (N = 2^18, r = 8: 256 MiB, auf einem Pi etwa 1-2 s).
`age` selbst waehlt ~1 s auf dem schreibenden Rechner; ein fester Wert ist hier besser
vorhersehbar und liegt sicher unter dem Lesedeckel."""
SCRYPT_MAX_LOG_N = 18

CHUNK_SIZE = 64 * 1024
_TAG = 16
_AGE_VERSION_LINE = b"age-encryption.org/v1\n"
_SCRYPT_LABEL = b"age-encryption.org/v1/scrypt"
_MAX_HEADER_BYTES = 64 * 1024
_MAX_LINE = 1024
_MAX_STANZAS = 16


# ---------------------------------------------------------------------------
# Bech32 (BIP-173), wie age es fuer Schluessel nutzt -- ohne die 90-Zeichen-Grenze
# ---------------------------------------------------------------------------

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_GEN = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


def _bech32_polymod(values: list[int]) -> int:
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ value
        for i, gen in enumerate(_BECH32_GEN):
            if (top >> i) & 1:
                chk ^= gen
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convert_bits(data: bytes | list[int], from_bits: int, to_bits: int, *, pad: bool) -> list[int]:
    acc = bits = 0
    out: list[int] = []
    maxv = (1 << to_bits) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            raise ValueError("Wert ausserhalb des Bereichs")
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        raise ValueError("ungueltiges Auffuellen")
    return out


def bech32_encode(hrp: str, data: bytes) -> str:
    """Kleingeschrieben; age schreibt Identitaeten danach komplett gross."""
    hrp = hrp.lower()
    values = _convert_bits(data, 8, 5, pad=True)
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + values + [0] * 6) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32_CHARSET[v] for v in values + checksum)


def bech32_decode(text: str) -> tuple[str, bytes]:
    """Gegenstueck zu `bech32_encode`; prueft Pruefsumme und gemischte Schreibweise."""
    if text.lower() != text and text.upper() != text:
        raise ValueError("gemischte Gross-/Kleinschreibung")
    text = text.lower()
    pos = text.rfind("1")
    if pos < 1 or pos + 7 > len(text):
        raise ValueError("kein Bech32")
    hrp, rest = text[:pos], text[pos + 1:]
    if any(not (33 <= ord(c) <= 126) for c in hrp) or any(c not in _BECH32_CHARSET for c in rest):
        raise ValueError("ungueltige Zeichen")
    values = [_BECH32_CHARSET.index(c) for c in rest]
    if _bech32_polymod(_bech32_hrp_expand(hrp) + values) != 1:
        raise ValueError("Pruefsumme falsch")
    return hrp, bytes(_convert_bits(values[:-6], 5, 8, pad=False))


# ---------------------------------------------------------------------------
# Sicherungsschluessel: Argon2id -> X25519
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KdfParams:
    t: int
    m_kib: int
    p: int
    salt: bytes

    def to_dict(self) -> dict:
        return {
            "alg": "argon2id", "t": self.t, "m_kib": self.m_kib, "p": self.p,
            "salt": base64.b64encode(self.salt).decode("ascii"),
        }

    @classmethod
    def new(cls) -> KdfParams:
        return cls(t=ARGON2_T, m_kib=ARGON2_M_KIB, p=ARGON2_P, salt=os.urandom(SALT_LENGTH))

    @classmethod
    def from_dict(cls, raw: object) -> KdfParams:
        """Liest Parameter aus Kopf oder Einstellung und setzt die Deckel durch."""
        if not isinstance(raw, dict) or raw.get("alg") != "argon2id":
            raise DamagedBackup("unbekanntes Schlüsselverfahren")
        t, m, p, salt = raw.get("t"), raw.get("m_kib"), raw.get("p"), raw.get("salt")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (t, m, p)) or not isinstance(salt, str):
            raise DamagedBackup("Schlüsselparameter ungültig")
        try:
            salt_bytes = base64.b64decode(salt, validate=True)
        except ValueError as exc:
            raise DamagedBackup("Salz ungültig") from exc
        if not 16 <= len(salt_bytes) <= 64:
            raise DamagedBackup("Salz ungültig")
        if not 1 <= t <= ARGON2_MAX_T:
            raise TooExpensive(f"Argon2 t={t}, erlaubt bis {ARGON2_MAX_T}")
        if not 1 <= p <= ARGON2_MAX_P:
            raise TooExpensive(f"Argon2 p={p}, erlaubt bis {ARGON2_MAX_P}")
        if not 8 * p <= m <= ARGON2_MAX_M_KIB:
            raise TooExpensive(f"Argon2 m={m} KiB, erlaubt bis {ARGON2_MAX_M_KIB} KiB")
        return cls(t=t, m_kib=m, p=p, salt=salt_bytes)


def _password_bytes(password: str) -> bytes:
    """NFC, damit dasselbe Passwort auf jedem Geraet dieselben Bytes ergibt (ein "ä" kann
    als ein Zeichen oder als "a" plus Punkte ankommen)."""
    return unicodedata.normalize("NFC", password).encode("utf-8")


def derive_secret(password: str, kdf: KdfParams) -> bytes:
    return hash_secret_raw(
        secret=_password_bytes(password), salt=kdf.salt, time_cost=kdf.t, memory_cost=kdf.m_kib,
        parallelism=kdf.p, hash_len=32, type=Type.ID,
    )


def identity_string(secret: bytes) -> str:
    """32 Byte X25519-Geheimnis -> `AGE-SECRET-KEY-1...` (so liest es auch `age -i`)."""
    if len(secret) != 32:
        raise ValueError("X25519-Geheimnis muss 32 Byte lang sein")
    return bech32_encode("age-secret-key-", secret).upper()


def recipient_of(identity: str) -> str:
    try:
        return str(pyrage.x25519.Identity.from_str(identity).to_public())
    except Exception as exc:
        raise WrongSecret() from exc


def key_id_of(recipient: str) -> str:
    """Kurzer Fingerabdruck des Empfaengers (nicht geheim), z. B. fuer die Liste."""
    return hashlib.sha256(recipient.encode("ascii")).hexdigest()[:16]


@dataclass(frozen=True)
class DerivedKey:
    recipient: str
    key_id: str
    kdf: KdfParams
    identity: str
    """Der Wiederherstellungsschluessel. Nur im Speicher, nie speichern oder protokollieren."""

    def __repr__(self) -> str:  # nie das Geheimnis in Tracebacks/Logs
        return f"DerivedKey(key_id={self.key_id!r})"


def derive_key(password: str, kdf: KdfParams | None = None) -> DerivedKey:
    kdf = kdf or KdfParams.new()
    identity = identity_string(derive_secret(password, kdf))
    recipient = recipient_of(identity)
    return DerivedKey(recipient=recipient, key_id=key_id_of(recipient), kdf=kdf, identity=identity)


def is_recovery_key(text: str) -> bool:
    """Auch kleingeschrieben (abgetippt): Bech32 kennt keine Gross-/Kleinschreibung."""
    return bool(re.fullmatch(r"AGE-SECRET-KEY-1[0-9A-Z]{58}", text.strip().upper()))


# ---------------------------------------------------------------------------
# Schluessel-Modus: X25519 ueber pyrage
# ---------------------------------------------------------------------------


def encrypt_to_recipient(reader: BinaryIO, writer: BinaryIO, recipient: str) -> None:
    pyrage.encrypt_io(reader, writer, [pyrage.x25519.Recipient.from_str(recipient)])


def decrypt_with_identity(reader: BinaryIO, writer: BinaryIO, identity: str) -> None:
    try:
        parsed = pyrage.x25519.Identity.from_str(identity.strip())
    except Exception as exc:
        raise WrongSecret() from exc
    try:
        pyrage.decrypt_io(reader, writer, [parsed])
    except pyrage.DecryptError as exc:
        if "no matching keys" in str(exc).lower():
            raise WrongSecret() from exc
        raise DamagedBackup("Entschlüsselung fehlgeschlagen") from exc
    except OSError as exc:
        # rage meldet einen veraenderten oder abgeschnittenen Rumpf als E/A-Fehler
        # "decryption error"; echte Schreibfehler (volle Platte) bleiben, was sie sind.
        if "decrypt" in str(exc).lower():
            raise DamagedBackup("Inhalt verändert oder abgeschnitten") from exc
        raise


# ---------------------------------------------------------------------------
# Passwort-Modus: age-scrypt, Streaming
# ---------------------------------------------------------------------------


def _b64(data: bytes) -> bytes:
    return base64.b64encode(data).rstrip(b"=")


def _unb64(text: bytes) -> bytes:
    """Kanonisches Base64 ohne Auffuellung (age-Regel), sonst `DamagedBackup`."""
    if b"=" in text or not re.fullmatch(rb"[A-Za-z0-9+/]*", text):
        raise DamagedBackup("Kopf ungültig")
    try:
        data = base64.b64decode(text + b"=" * (-len(text) % 4), validate=True)
    except ValueError as exc:
        raise DamagedBackup("Kopf ungültig") from exc
    if _b64(data) != text:
        raise DamagedBackup("Kopf ungültig")
    return data


def _hkdf(ikm: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt or None, info=info).derive(ikm)


def _scrypt_key(password: str, salt: bytes, log_n: int) -> bytes:
    n = 1 << log_n
    return hashlib.scrypt(
        _password_bytes(password), salt=_SCRYPT_LABEL + salt, n=n, r=8, p=1, dklen=32,
        maxmem=128 * 8 * n + (32 << 20),
    )


def _read_full(reader: BinaryIO, size: int) -> bytes:
    """Liest genau `size` Byte oder bis Dateiende (Pipes liefern auch kuerzere Stuecke)."""
    parts: list[bytes] = []
    remaining = size
    while remaining > 0:
        part = reader.read(remaining)
        if not part:
            break
        parts.append(part)
        remaining -= len(part)
    return b"".join(parts)


def _chunk_nonce(counter: int, last: bool) -> bytes:
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def _wrap_body(encoded: bytes) -> bytes:
    lines = [encoded[i:i + 64] for i in range(0, len(encoded), 64)]
    if not lines or len(lines[-1]) == 64:
        lines.append(b"")
    return b"".join(line + b"\n" for line in lines)


def encrypt_with_passphrase(reader: BinaryIO, writer: BinaryIO, password: str, *, log_n: int | None = None) -> None:
    log_n = SCRYPT_WRITE_LOG_N if log_n is None else log_n
    file_key = os.urandom(16)
    salt = os.urandom(16)
    wrapped = ChaCha20Poly1305(_scrypt_key(password, salt, log_n)).encrypt(b"\x00" * 12, file_key, None)
    header = (
        _AGE_VERSION_LINE
        + b"-> scrypt " + _b64(salt) + b" " + str(log_n).encode("ascii") + b"\n"
        + _wrap_body(_b64(wrapped))
        + b"---"
    )
    mac = hmac.new(_hkdf(file_key, b"", b"header"), header, hashlib.sha256).digest()
    writer.write(header + b" " + _b64(mac) + b"\n")
    nonce = os.urandom(16)
    writer.write(nonce)
    aead = ChaCha20Poly1305(_hkdf(file_key, nonce, b"payload"))
    counter = 0
    current = _read_full(reader, CHUNK_SIZE)
    while True:
        following = _read_full(reader, CHUNK_SIZE) if len(current) == CHUNK_SIZE else b""
        last = not following
        writer.write(aead.encrypt(_chunk_nonce(counter, last), current, None))
        if last:
            return
        current = following
        counter += 1


def _readline(reader: BinaryIO, budget: list[int]) -> bytes:
    line = reader.readline(_MAX_LINE)
    budget[0] -= len(line)
    if not line.endswith(b"\n") or budget[0] < 0:
        raise DamagedBackup("Kopf ungültig")
    return line


def _read_age_header(reader: BinaryIO) -> tuple[bytes, list[tuple[list[bytes], bytes]], bytes]:
    """-> (Kopf bis einschliesslich '---', [(Argumente, Rumpf)], MAC)."""
    budget = [_MAX_HEADER_BYTES]
    raw = _readline(reader, budget)
    if raw != _AGE_VERSION_LINE:
        raise DamagedBackup("keine age-Datei")
    stanzas: list[tuple[list[bytes], bytes]] = []
    while True:
        line = _readline(reader, budget)
        if line.startswith(b"---"):
            if not line.startswith(b"--- "):
                raise DamagedBackup("Kopf ungültig")
            mac = _unb64(line[4:-1])
            if len(mac) != 32:
                raise DamagedBackup("Kopf ungültig")
            return raw + b"---", stanzas, mac
        if not line.startswith(b"-> ") or len(stanzas) >= _MAX_STANZAS:
            raise DamagedBackup("Kopf ungültig")
        args = line[3:-1].split(b" ")
        if not args or any(not a for a in args):
            raise DamagedBackup("Kopf ungültig")
        raw += line
        body = b""
        while True:
            body_line = _readline(reader, budget)
            raw += body_line
            content = body_line[:-1]
            if len(content) > 64:
                raise DamagedBackup("Kopf ungültig")
            body += content
            if len(content) < 64:
                break
        stanzas.append((args, _unb64(body)))


def decrypt_with_passphrase(
    reader: BinaryIO, writer: BinaryIO, password: str, *, max_log_n: int | None = None
) -> None:
    max_log_n = SCRYPT_MAX_LOG_N if max_log_n is None else max_log_n
    header, stanzas, mac = _read_age_header(reader)
    if not any(args[0] == b"scrypt" for args, _ in stanzas):
        raise WrongSecret()  # mit einem Schluessel verschluesselt, nicht mit einem Passwort
    if len(stanzas) != 1:
        raise DamagedBackup("scrypt muss allein stehen")
    args, body = stanzas[0]
    if len(args) != 3 or not re.fullmatch(rb"[1-9][0-9]{0,1}", args[2]):
        raise DamagedBackup("Kopf ungültig")
    salt = _unb64(args[1])
    log_n = int(args[2])
    if len(salt) != 16 or len(body) != 32:
        raise DamagedBackup("Kopf ungültig")
    if log_n > max_log_n:
        raise TooExpensive(f"scrypt log2(N)={log_n}, erlaubt bis {max_log_n}")
    try:
        file_key = ChaCha20Poly1305(_scrypt_key(password, salt, log_n)).decrypt(b"\x00" * 12, body, None)
    except InvalidTag as exc:
        raise WrongSecret() from exc
    expected = hmac.new(_hkdf(file_key, b"", b"header"), header, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, mac):
        raise DamagedBackup("Kopf verändert")
    nonce = _read_full(reader, 16)
    if len(nonce) != 16:
        raise DamagedBackup("abgeschnitten")
    aead = ChaCha20Poly1305(_hkdf(file_key, nonce, b"payload"))
    counter = 0
    current = _read_full(reader, CHUNK_SIZE + _TAG)
    while True:
        if len(current) < _TAG:
            raise DamagedBackup("abgeschnitten")
        following = _read_full(reader, CHUNK_SIZE + _TAG) if len(current) == CHUNK_SIZE + _TAG else b""
        last = not following
        try:
            plain = aead.decrypt(_chunk_nonce(counter, last), current, None)
        except InvalidTag as exc:
            raise DamagedBackup("Inhalt verändert oder abgeschnitten") from exc
        if last and not plain and counter > 0:
            raise DamagedBackup("leerer Schlussblock")
        writer.write(plain)
        if last:
            return
        current = following
        counter += 1
