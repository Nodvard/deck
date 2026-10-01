"""Drosselung fehlgeschlagener Anmeldungen.

Ohne Begrenzung liessen sich gegen `POST /auth/login` beliebig viele Passwoerter und
gegen `POST /auth/mfa` beliebig viele 6-stellige Codes durchprobieren. Hier werden
Fehlversuche in einem gleitenden Zeitfenster gezaehlt (Idee wie `core/flap.py`, aber
bewusst im Speicher statt in der Datenbank: kein Schreibzugriff pro Versuch, und die
Pruefung passiert VOR der teuren Argon2-Verifikation):

- je (Client-IP, Benutzername): nach `MAX_FAILURES_PER_USER` Fehlversuchen gesperrt,
- je Client-IP ueber alle Benutzernamen: nach `MAX_FAILURES_PER_IP` (bremst das
  Durchprobieren vieler Benutzernamen),
- je `mfa_token`: nach `MAX_MFA_FAILURES` falschen Codes ist das Token verbraucht,
  man muss sich neu anmelden. Falsche Codes zaehlen ausserdem als Fehlversuch fuer
  (IP, Benutzername) -- sonst liesse sich mit dem richtigen Passwort ueber immer neue
  Tokens doch beliebig oft raten.

Ein Versuch wird schon BEIM START gezaehlt (`begin`), nicht erst wenn er gescheitert
ist: sonst kaemen viele gleichzeitig abgeschickte Anfragen alle an der Pruefung vorbei,
bevor der erste Fehlschlag eingetragen ist. Klappt die Anmeldung, wird der Eintrag
wieder zurueckgenommen (`succeeded`/`release`).

Alle Funktionen sind synchron und enthalten kein `await` -- im Event-Loop laufen sie
damit am Stueck; das Lock schuetzt zusaetzlich gegen Aufrufe aus Threads. Der Zustand
gilt pro Prozess und ist nach einem Neustart weg (der Container startet uvicorn mit
einem Worker). Die Client-IP kommt aus `request.client.host`; hinter einem spaeteren
Reverse-Proxy saehen alle Anfragen gleich aus -- dann muss die Proxy-IP-Erkennung
(uvicorn `--proxy-headers`/`--forwarded-allow-ips`) passend gesetzt werden.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import deque
from dataclasses import dataclass

WINDOW_SECONDS = 5 * 60
MAX_FAILURES_PER_USER = 10
"""Fehlversuche je (IP, Benutzername) im Fenster, danach 429."""
MAX_FAILURES_PER_IP = 30
"""Fehlversuche je IP ueber alle Benutzernamen im Fenster, danach 429."""
MAX_MFA_FAILURES = 5
"""Falsche 2FA-Codes je `mfa_token`, danach ist das Token verbraucht."""
MFA_ENTRY_TTL_SECONDS = 60 * 60
"""Wie lange der Zaehler je `mfa_token` aufgehoben wird -- laenger als jedes Token lebt."""

_SWEEP_THRESHOLD = 1024
_UNKNOWN_IP = "unbekannt"

SCOPE_USER = "user"
SCOPE_IP = "ip"


def _now() -> float:
    return time.monotonic()


class Locked(Exception):
    """Zu viele Fehlversuche -- `retry_after` Sekunden bis zum naechsten erlaubten Versuch."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(lockout_message(retry_after))
        self.retry_after = retry_after


class MfaExhausted(Exception):
    """Dieses `mfa_token` hat schon `MAX_MFA_FAILURES` falsche Codes gesehen."""


def lockout_message(retry_after: int) -> str:
    minutes = max(1, math.ceil(retry_after / 60))
    unit = "Minute" if minutes == 1 else "Minuten"
    return f"Zu viele Fehlversuche. Bitte in {minutes} {unit} erneut versuchen."


@dataclass
class Attempt:
    ip: str
    username: str
    ts: float
    mfa_key: str | None = None


_lock = threading.Lock()
_by_user: dict[tuple[str, str], deque[float]] = {}
_by_ip: dict[str, deque[float]] = {}
_announced: set[tuple[str, ...]] = set()
"""Schluessel, deren laufende Sperre schon gemeldet wurde (ein Audit-Eintrag je Sperre)."""
_mfa: dict[str, tuple[int, float]] = {}
"""Hash des `mfa_token` -> (falsche Codes, erster Versuch)."""


def reset() -> None:
    """Vergisst alles (fuer Tests)."""
    with _lock:
        _by_user.clear()
        _by_ip.clear()
        _announced.clear()
        _mfa.clear()


def _pruned(store: dict, key, now: float) -> deque[float]:
    dq = store.get(key)
    if dq is None:
        return deque()
    cutoff = now - WINDOW_SECONDS
    while dq and dq[0] <= cutoff:
        dq.popleft()
    if not dq:
        del store[key]
    return dq


def _retry_after(dq: deque[float], limit: int, now: float) -> int:
    """0, wenn nicht gesperrt; sonst Sekunden, bis wieder weniger als `limit` Eintraege
    im Fenster liegen."""
    if len(dq) < limit:
        return 0
    return max(1, math.ceil(dq[len(dq) - limit] + WINDOW_SECONDS - now))


def _sweep(now: float) -> None:
    for store in (_by_user, _by_ip):
        for key in list(store):
            _pruned(store, key, now)
    for key, (_count, first) in list(_mfa.items()):
        if now - first > MFA_ENTRY_TTL_SECONDS:
            del _mfa[key]
    _announced.intersection_update(
        {(SCOPE_USER, *k) for k in _by_user} | {(SCOPE_IP, k) for k in _by_ip}
    )


def _mfa_key(mfa_token: str) -> str:
    return hashlib.sha256(mfa_token.encode("utf-8")).hexdigest()


def begin(ip: str | None, username: str, *, mfa_token: str | None = None) -> Attempt:
    """Vor der Passwort-/Code-Pruefung aufrufen. Wirft `MfaExhausted` bzw. `Locked`
    und zaehlt den Versuch sonst sofort mit."""
    ip = ip or _UNKNOWN_IP
    user_key = (ip, username)
    with _lock:
        now = _now()
        mfa_key = None
        if mfa_token is not None:
            mfa_key = _mfa_key(mfa_token)
            if _mfa.get(mfa_key, (0, now))[0] >= MAX_MFA_FAILURES:
                raise MfaExhausted()

        user_dq = _pruned(_by_user, user_key, now)
        ip_dq = _pruned(_by_ip, ip, now)
        wait_user = _retry_after(user_dq, MAX_FAILURES_PER_USER, now)
        wait_ip = _retry_after(ip_dq, MAX_FAILURES_PER_IP, now)
        if not wait_user:
            _announced.discard((SCOPE_USER, *user_key))
        if not wait_ip:
            _announced.discard((SCOPE_IP, ip))
        if wait_user or wait_ip:
            raise Locked(max(wait_user, wait_ip))

        _by_user.setdefault(user_key, deque()).append(now)
        _by_ip.setdefault(ip, deque()).append(now)
        if mfa_key is not None:
            _mfa.setdefault(mfa_key, (0, now))
        if len(_by_user) + len(_by_ip) + len(_mfa) > _SWEEP_THRESHOLD:
            _sweep(now)
        return Attempt(ip=ip, username=username, ts=now, mfa_key=mfa_key)


def failed(attempt: Attempt) -> list[str]:
    """Der Versuch war falsch (er ist schon gezaehlt). Gibt die Bereiche
    (`SCOPE_USER`/`SCOPE_IP`) zurueck, deren Sperre GENAU JETZT beginnt -- jeder nur
    einmal je Sperre, damit der Aufrufer genau einen Audit-Eintrag schreibt."""
    started: list[str] = []
    with _lock:
        now = _now()
        if attempt.mfa_key is not None:
            count, first = _mfa.get(attempt.mfa_key, (0, now))
            _mfa[attempt.mfa_key] = (count + 1, first)
        checks = (
            (SCOPE_USER, _by_user, (attempt.ip, attempt.username), MAX_FAILURES_PER_USER),
            (SCOPE_IP, _by_ip, attempt.ip, MAX_FAILURES_PER_IP),
        )
        for scope, store, key, limit in checks:
            marker = (scope, *key) if isinstance(key, tuple) else (scope, key)
            if len(_pruned(store, key, now)) >= limit and marker not in _announced:
                _announced.add(marker)
                started.append(scope)
    return started


def mfa_exhausted(attempt: Attempt) -> bool:
    with _lock:
        if attempt.mfa_key is None:
            return False
        return _mfa.get(attempt.mfa_key, (0, 0.0))[0] >= MAX_MFA_FAILURES


def _discard(dq: deque[float] | None, ts: float) -> None:
    if dq is None:
        return
    try:
        dq.remove(ts)
    except ValueError:
        pass


def release(attempt: Attempt) -> None:
    """Der Versuch zaehlt nicht (Passwort richtig, der zweite Faktor folgt noch) --
    die bisherigen Fehlversuche bleiben aber stehen, bis die Anmeldung ganz klappt."""
    with _lock:
        _discard(_by_user.get((attempt.ip, attempt.username)), attempt.ts)
        _discard(_by_ip.get(attempt.ip), attempt.ts)


def succeeded(attempt: Attempt) -> None:
    """Anmeldung komplett: Zaehler fuer (IP, Benutzername) und das `mfa_token` weg; beim
    IP-Zaehler faellt nur dieser eine Versuch heraus (andere Namen bleiben gezaehlt)."""
    with _lock:
        _by_user.pop((attempt.ip, attempt.username), None)
        _announced.discard((SCOPE_USER, attempt.ip, attempt.username))
        _discard(_by_ip.get(attempt.ip), attempt.ts)
        if attempt.mfa_key is not None:
            _mfa.pop(attempt.mfa_key, None)
