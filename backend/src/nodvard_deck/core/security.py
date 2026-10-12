"""Passwort-Hashing, JWT-Access-Token und opake Token; der Token-Zustand liegt in `services/auth.py`."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import weakref
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Das Standardprofil von argon2-cffi ist bereits Argon2id mit vernünftigen Kosten, nicht selbst anpassen.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """False bei falschem Passwort; wirft nie, auch nicht bei einem unbrauchbaren Hash-Text."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Ungültiger oder fremder Hash-Text (z. B. leere Migration) zählt als falsch, nicht als Serverfehler.
        return False


# Argon2 läuft abseits des Event-Loops: Eine Prüfung braucht 64 MiB und auf einem Pi einige hundert ms, im Loop stünde
# währenddessen alles still. Davon laufen nur wenige gleichzeitig (eine Anmelde-Flut füllt so weder Thread-Pool noch
# Speicher), und wer dahinter wartet, wird ab einer Grenze abgewiesen statt unbegrenzt aufgestaut.
MAX_PARALLEL_HASHES = 2
"""Höchstens so viele Argon2-Vorgänge laufen gleichzeitig."""

MAX_WAITING_HASHES = 16
"""So viele weitere dürfen warten; jeder darüber hinaus bekommt sofort `HashingBusy`."""


class HashingBusy(Exception):
    """Gerade rechnen zu viele Passwort-Prüfungen; ein neuer Versuch in ein paar Sekunden hilft (API: 503)."""


class _Gate:
    def __init__(self) -> None:
        self.semaphore = asyncio.Semaphore(MAX_PARALLEL_HASHES)
        self.waiting = 0


# Ein Semaphore hängt an dem Loop, in dem er zuerst gebraucht wird, daher ein Tor je Loop (Tests starten mehrere).
_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Gate] = weakref.WeakKeyDictionary()


def _gate() -> _Gate:
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = _gates[loop] = _Gate()
    return gate


async def run_hashing[T](func: Callable[..., T], *args: Any, signed_in: bool = False) -> T:
    """Führt `func` in einem Thread aus, höchstens `MAX_PARALLEL_HASHES` gleichzeitig; wirft `HashingBusy`, wenn schon
    `MAX_WAITING_HASHES` warten (nicht bei `signed_in=True`)."""
    gate = _gate()
    if gate.semaphore.locked():
        # Arbeit eines angemeldeten Nutzers (Passwortabfrage, Passwort setzen, neue Wiederherstellungs-Codes) wartet
        # immer: Eine Anmelde-Flut von außen soll ihn nicht aussperren, und begrenzt ist sie ohnehin (Anmeldung nötig).
        if gate.waiting >= MAX_WAITING_HASHES and not signed_in:
            raise HashingBusy
        gate.waiting += 1
        try:
            await gate.semaphore.acquire()
        finally:
            gate.waiting -= 1
    else:
        await gate.semaphore.acquire()
    try:
        return await asyncio.to_thread(func, *args)
    finally:
        gate.semaphore.release()


async def hash_password_async(password: str, *, signed_in: bool = False) -> str:
    """Wie `hash_password`, aber in einem Thread und begrenzt (`run_hashing`)."""
    return await run_hashing(lambda: hash_password(password), signed_in=signed_in)


async def verify_password_async(password: str, password_hash: str, *, signed_in: bool = False) -> bool:
    """Wie `verify_password`, aber in einem Thread und begrenzt (`run_hashing`)."""
    return await run_hashing(lambda: verify_password(password, password_hash), signed_in=signed_in)


def needs_rehash(password_hash: str) -> bool:
    """True, wenn die Standardparameter von argon2-cffi seit dem Erstellen des Hashes verschärft wurden."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


# Opake Token (Refresh-Token, API-Token): Klartext gibt es nur einmal, bei der Ausgabe; gespeichert wird der Hash.
def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_opaque_token(raw_token: str) -> str:
    """SHA-256 genügt: Der Token hat schon 256 Bit Zufall, Argon2 wäre hier nur unnötig teuer."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# Wiederherstellungs-Codes (Zwei-Faktor): `XXXXX-XXXXX` aus 32 eindeutigen Zeichen (ohne I, O, 0, 1) = 50 Bit,
# gehasht wie ein Passwort.
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_recovery_code() -> str:
    left = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(5))
    right = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(5))
    return f"{left}-{right}"


def normalize_recovery_code(code: str) -> str:
    """Groß, ohne Striche und Leerzeichen; so wird gehasht und geprüft."""
    return "".join(ch for ch in code if ch.isalnum()).upper()


def is_totp_code(code: str) -> bool:
    """Sechs Ziffern (Leerzeichen egal) sind ein Authenticator-Code, alles andere ein Wiederherstellungs-Code."""
    compact = "".join(code.split())
    return len(compact) == 6 and compact.isascii() and compact.isdigit()


# JWT: Der `typ`-Claim trennt Access- von MFA-Zwischentoken, damit ein MFA-Token nie als Access-Token durchgeht,
# obwohl beide mit demselben Schlüssel signiert sind.
JWT_ALGORITHM = "HS256"
TokenType = Literal["access", "mfa"]


class TokenError(Exception):
    """Ungültiges oder abgelaufenes JWT; eigene Klasse, damit Aufrufer nicht gegen `jwt.PyJWTError` programmieren."""


def create_jwt(
    *,
    subject: str,
    token_type: TokenType,
    secret: str,
    ttl_seconds: int,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "typ": token_type,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
        "jti": secrets.token_hex(16),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str, *, secret: str, expected_type: TokenType) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if payload.get("typ") != expected_type:
        raise TokenError(f"Token-Typ '{payload.get('typ')}' erwartet: '{expected_type}'")
    return payload
