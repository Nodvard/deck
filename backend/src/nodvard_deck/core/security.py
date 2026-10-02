"""Passwort-Hashing, JWT-Access-Token, opake Refresh-/API-Token.

Reine Funktionen ohne DB-Zugriff -- vollstaendig ohne Fixture testbar. Der eigentliche
Zustand (wer welchen Token hat, wann er widerrufen wurde) lebt in `services/auth.py`
und den Modellen, nicht hier.

Siehe docs/00-DECISIONS.md D-07.
"""

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

_hasher = PasswordHasher()

# ---------------------------------------------------------------------------
# Passwoerter -- Argon2id (D-07). argon2-cffi's Default-Profil ist bereits Argon2id
# mit vernuenftigen Zeit-/Speicherkosten; kein Grund, das selbst zu parametrisieren.
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """False bei falschem Passwort -- wirft nie, damit Aufrufer nicht per try/except
    auf eine spezifische Exception angewiesen sind."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Ungueltiger/fremder Hash-Text (z. B. leere Migration) -- auch das ist "falsch",
        # nicht "Serverfehler".
        return False


# ---------------------------------------------------------------------------
# Argon2 abseits des Event-Loops. Eine Pruefung braucht 64 MiB und einige hundert ms (auf einem
# Pi); liefe sie im Loop, stuende waehrenddessen alles still (Anfragen, Terminals, Zeitplaene).
# Deshalb laeuft sie in einem Thread, und davon gleichzeitig nur wenige -- eine Anmelde-Flut
# fuellt so weder den Thread-Pool noch den Speicher. Wer dahinter noch wartet, wird ab einer
# Grenze abgewiesen statt unbegrenzt aufgestaut.
# ---------------------------------------------------------------------------

MAX_PARALLEL_HASHES = 2
"""So viele Argon2-Vorgaenge laufen hoechstens gleichzeitig."""

MAX_WAITING_HASHES = 16
"""So viele weitere duerfen warten; jeder darueber hinaus bekommt sofort `HashingBusy`."""


class HashingBusy(Exception):
    """Gerade rechnen schon zu viele Passwort-Pruefungen; es hilft, in ein paar Sekunden
    erneut zu versuchen (API: 503)."""


class _Gate:
    def __init__(self) -> None:
        self.semaphore = asyncio.Semaphore(MAX_PARALLEL_HASHES)
        self.waiting = 0


# Je Event-Loop ein eigenes Tor: ein Semaphore haengt am Loop, in dem er zuerst gebraucht wird
# (Tests starten mehrere Loops nacheinander).
_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Gate] = weakref.WeakKeyDictionary()


def _gate() -> _Gate:
    loop = asyncio.get_running_loop()
    gate = _gates.get(loop)
    if gate is None:
        gate = _gates[loop] = _Gate()
    return gate


async def run_hashing[T](func: Callable[..., T], *args: Any, signed_in: bool = False) -> T:
    """Fuehrt eine rechenintensive Passwort-Arbeit in einem Thread aus, hoechstens
    `MAX_PARALLEL_HASHES` gleichzeitig. Wirft `HashingBusy`, wenn schon `MAX_WAITING_HASHES`
    warten.

    `signed_in=True` fuer Arbeit, die nur ein angemeldeter Nutzer ausloest (Passwortabfrage bei
    empfindlichen Aktionen, Passwort setzen, neue Wiederherstellungs-Codes): die wartet in jedem
    Fall, statt abgewiesen zu werden. Eine Anmelde-Flut von aussen soll niemanden, der schon
    drin ist, aus seinen Einstellungen aussperren; begrenzt ist diese Arbeit ohnehin (Anmeldung
    noetig, Drosselung je Konto)."""
    gate = _gate()
    if gate.semaphore.locked():
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
    """Wie `hash_password`, aber in einem Thread und begrenzt (siehe `run_hashing`)."""
    return await run_hashing(lambda: hash_password(password), signed_in=signed_in)


async def verify_password_async(password: str, password_hash: str, *, signed_in: bool = False) -> bool:
    """Wie `verify_password`, aber in einem Thread und begrenzt (siehe `run_hashing`)."""
    return await run_hashing(lambda: verify_password(password, password_hash), signed_in=signed_in)


def needs_rehash(password_hash: str) -> bool:
    """True, wenn argon2-cffis Default-Parameter seit dem Erstellen des Hashes
    verschaerft wurden. Aufrufer, die das pruefen, sollten nach erfolgreichem Login
    einmalig neu hashen."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Opake Token (Refresh-Token, API-Token) -- Klartext existiert nur einmal, im Moment
# der Ausgabe an den Client. Gespeichert wird ausschliesslich der Hash (D-07:
# "Refresh-Token: opaker Zufallswert, gehasht in der DB").
# ---------------------------------------------------------------------------


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_opaque_token(raw_token: str) -> str:
    """SHA-256 reicht hier: der Token selbst hat schon 256 Bit Zufall (kein Passwort,
    keine Brute-Force-Flaeche wie bei niedrigentropischen Werten -- Argon2 waere
    fuer diesen Zweck nur unnoetig teuer)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Wiederherstellungs-Codes (Zwei-Faktor). `XXXXX-XXXXX` aus 32 eindeutigen Zeichen
# (ohne I, O, 0, 1) = 50 Bit. Gehasht wird mit Argon2id (`hash_password`), siehe
# models.identity.RecoveryCode.
# ---------------------------------------------------------------------------

RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_recovery_code() -> str:
    left = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(5))
    right = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(5))
    return f"{left}-{right}"


def normalize_recovery_code(code: str) -> str:
    """Gross, ohne Striche/Leerzeichen -- so wird gehasht und geprueft."""
    return "".join(ch for ch in code if ch.isalnum()).upper()


def is_totp_code(code: str) -> bool:
    """Sechs Ziffern (Leerzeichen egal) = Authenticator-Code; alles andere wird als
    Wiederherstellungs-Code behandelt (der nie nur aus sechs Ziffern besteht)."""
    compact = "".join(code.split())
    return len(compact) == 6 and compact.isascii() and compact.isdigit()


# ---------------------------------------------------------------------------
# JWT-Access-Token. `typ`-Claim unterscheidet Access- von MFA-Zwischentoken, damit ein
# MFA-Token nicht versehentlich als Access-Token akzeptiert werden kann, selbst wenn
# beide mit demselben Schluessel signiert sind.
# ---------------------------------------------------------------------------

JWT_ALGORITHM = "HS256"
TokenType = Literal["access", "mfa"]


class TokenError(Exception):
    """Ungueltiges oder abgelaufenes JWT. Absichtlich eine eigene Klasse, damit
    Aufrufer nicht gegen `jwt.PyJWTError` direkt programmieren muessen."""


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
