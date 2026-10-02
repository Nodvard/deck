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

- je Konto, IP-unabhaengig, nur fuer den zweiten Faktor (`MAX_MFA_FAILURES_PER_ACCOUNT`):
  Nur wer das Passwort schon kennt, kommt zum Code -- mit vielen verschiedenen
  Adressen liesse sich der Code sonst trotz der Sperren oben durchprobieren,
- ein 2FA-Code gilt je Konto nur einmal (`claim_success`: der Zeitschritt wird gemerkt) und
  ein `mfa_token` nur fuer eine einzige Anmeldung.

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

MAX_MFA_FAILURES_PER_ACCOUNT = 10
"""Falsche 2FA-Codes je Konto in `ACCOUNT_WINDOW_SECONDS` (egal von welcher IP), danach 429."""
ACCOUNT_WINDOW_SECONDS = 15 * 60
MAX_MFA_FAILURES_PER_ACCOUNT_DAY = 20
"""Dasselbe ueber 24 Stunden -- bremst auch langsames, ueber den Tag verteiltes Raten."""
ACCOUNT_DAY_SECONDS = 24 * 60 * 60
_ACCOUNT_LIMITS = (
    (MAX_MFA_FAILURES_PER_ACCOUNT, ACCOUNT_WINDOW_SECONDS),
    (MAX_MFA_FAILURES_PER_ACCOUNT_DAY, ACCOUNT_DAY_SECONDS),
)
TOTP_STEP_SECONDS = 30
"""Laenge eines TOTP-Zeitschritts (Standard, wie bei pyotp)."""

_SWEEP_THRESHOLD = 1024
_UNKNOWN_IP = "unbekannt"

SCOPE_USER = "user"
SCOPE_IP = "ip"
SCOPE_ACCOUNT = "account"


def _now() -> float:
    return time.monotonic()


class Locked(Exception):
    """Zu viele Fehlversuche -- `retry_after` Sekunden bis zum naechsten erlaubten Versuch."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(lockout_message(retry_after))
        self.retry_after = retry_after


class MfaExhausted(Exception):
    """Dieses `mfa_token` hat schon `MAX_MFA_FAILURES` falsche Codes gesehen."""


class MfaTokenUsed(Exception):
    """Mit diesem `mfa_token` hat sich schon jemand angemeldet -- es gilt nur einmal."""


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
    account: str | None = None


_lock = threading.Lock()
_by_user: dict[tuple[str, str], deque[float]] = {}
_by_ip: dict[str, deque[float]] = {}
_announced: set[tuple[str, ...]] = set()
"""Schluessel, deren laufende Sperre schon gemeldet wurde (ein Audit-Eintrag je Sperre)."""
_mfa: dict[str, tuple[int, float]] = {}
"""Hash des `mfa_token` -> (falsche Codes, erster Versuch)."""
_by_account: dict[str, deque[float]] = {}
"""Konto -> Zeitpunkte der 2FA-Versuche (laenger als `WINDOW_SECONDS` aufgehoben)."""
_mfa_used: dict[str, float] = {}
"""Hash des `mfa_token` -> Zeitpunkt, an dem damit eine Anmeldung gelungen ist."""
_totp_steps: dict[str, int] = {}
"""Konto -> letzter angenommener TOTP-Zeitschritt (nur im Speicher: nach einem Neustart
gilt ein Code noch hoechstens fuer sein Zeitfenster von rund 90 Sekunden erneut)."""


def reset() -> None:
    """Vergisst alles (fuer Tests)."""
    with _lock:
        _by_user.clear()
        _by_ip.clear()
        _announced.clear()
        _mfa.clear()
        _by_account.clear()
        _mfa_used.clear()
        _totp_steps.clear()


def _pruned(store: dict, key, now: float, window: float = WINDOW_SECONDS) -> deque[float]:
    dq = store.get(key)
    if dq is None:
        return deque()
    cutoff = now - window
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


def _account_retry(dq: deque[float], now: float) -> int:
    """0, wenn das Konto fuer weitere 2FA-Versuche frei ist; sonst Sekunden bis zum naechsten
    erlaubten Versuch (laengste Wartezeit ueber beide Fenster)."""
    return max(
        (_retry_after_window(dq, limit, window, now) for limit, window in _ACCOUNT_LIMITS),
        default=0,
    )


def _retry_after_window(dq: deque[float], limit: int, window: float, now: float) -> int:
    if len(dq) < limit:
        return 0
    newest_limit = dq[len(dq) - limit]
    if newest_limit <= now - window:
        return 0
    return max(1, math.ceil(newest_limit + window - now))


def _sweep(now: float) -> None:
    for store in (_by_user, _by_ip):
        for key in list(store):
            _pruned(store, key, now)
    for key in list(_by_account):
        _pruned(_by_account, key, now, ACCOUNT_DAY_SECONDS)
    for key, (_count, first) in list(_mfa.items()):
        if now - first > MFA_ENTRY_TTL_SECONDS:
            del _mfa[key]
    for key, used_at in list(_mfa_used.items()):
        if now - used_at > MFA_ENTRY_TTL_SECONDS:
            del _mfa_used[key]
    wall_step = int(time.time() // TOTP_STEP_SECONDS)
    for key, step in list(_totp_steps.items()):
        if step < wall_step - 10:
            del _totp_steps[key]
    _announced.intersection_update(
        {(SCOPE_USER, *k) for k in _by_user}
        | {(SCOPE_IP, k) for k in _by_ip}
        | {(SCOPE_ACCOUNT, k) for k in _by_account}
    )


def _mfa_key(mfa_token: str) -> str:
    return hashlib.sha256(mfa_token.encode("utf-8")).hexdigest()


def begin(
    ip: str | None,
    username: str,
    *,
    mfa_token: str | None = None,
    account: str | None = None,
) -> Attempt:
    """Vor der Passwort-/Code-Pruefung aufrufen. Wirft `MfaTokenUsed`, `MfaExhausted` bzw.
    `Locked` und zaehlt den Versuch sonst sofort mit. `account` (Konto-Kennung) nur beim
    zweiten Faktor angeben: dann gilt zusaetzlich die IP-unabhaengige Grenze je Konto."""
    ip = ip or _UNKNOWN_IP
    user_key = (ip, username)
    with _lock:
        now = _now()
        mfa_key = None
        if mfa_token is not None:
            mfa_key = _mfa_key(mfa_token)
            if mfa_key in _mfa_used:
                raise MfaTokenUsed()
            if _mfa.get(mfa_key, (0, now))[0] >= MAX_MFA_FAILURES:
                raise MfaExhausted()

        user_dq = _pruned(_by_user, user_key, now)
        ip_dq = _pruned(_by_ip, ip, now)
        wait_user = _retry_after(user_dq, MAX_FAILURES_PER_USER, now)
        wait_ip = _retry_after(ip_dq, MAX_FAILURES_PER_IP, now)
        wait_account = 0
        if account is not None:
            account_dq = _pruned(_by_account, account, now, ACCOUNT_DAY_SECONDS)
            wait_account = _account_retry(account_dq, now)
            if not wait_account:
                _announced.discard((SCOPE_ACCOUNT, account))
        if not wait_user:
            _announced.discard((SCOPE_USER, *user_key))
        if not wait_ip:
            _announced.discard((SCOPE_IP, ip))
        if wait_user or wait_ip or wait_account:
            raise Locked(max(wait_user, wait_ip, wait_account))

        _by_user.setdefault(user_key, deque()).append(now)
        _by_ip.setdefault(ip, deque()).append(now)
        if account is not None:
            _by_account.setdefault(account, deque()).append(now)
        if mfa_key is not None:
            _mfa.setdefault(mfa_key, (0, now))
        if len(_by_user) + len(_by_ip) + len(_mfa) + len(_by_account) + len(_mfa_used) > _SWEEP_THRESHOLD:
            _sweep(now)
        return Attempt(ip=ip, username=username, ts=now, mfa_key=mfa_key, account=account)


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
        if attempt.account is not None:
            marker = (SCOPE_ACCOUNT, attempt.account)
            dq = _pruned(_by_account, attempt.account, now, ACCOUNT_DAY_SECONDS)
            if _account_retry(dq, now) and marker not in _announced:
                _announced.add(marker)
                started.append(SCOPE_ACCOUNT)
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
        if attempt.account is not None:
            _discard(_by_account.get(attempt.account), attempt.ts)


def succeeded(attempt: Attempt) -> None:
    """Anmeldung komplett: Zaehler fuer (IP, Benutzername) und das `mfa_token` weg; beim
    IP-Zaehler faellt nur dieser eine Versuch heraus (andere Namen bleiben gezaehlt)."""
    with _lock:
        _by_user.pop((attempt.ip, attempt.username), None)
        _announced.discard((SCOPE_USER, attempt.ip, attempt.username))
        _discard(_by_ip.get(attempt.ip), attempt.ts)
        if attempt.account is not None:
            _discard(_by_account.get(attempt.account), attempt.ts)
        if attempt.mfa_key is not None:
            _mfa.pop(attempt.mfa_key, None)


def claim_success(attempt: Attempt, *, totp_step: int | None = None) -> bool:
    """Der zweite Faktor stimmt -- BEVOR die Anmeldung fertig ausgegeben wird aufrufen. Gilt den
    Code nur einmal (`totp_step`: der Zeitschritt des Authenticator-Codes, nur bei diesem Weg;
    Wiederherstellungs-Codes verbraucht schon die Datenbank) und das `mfa_token` nur fuer eine
    Anmeldung. `False` = schon benutzt (Codes wiederholt, Token verbraucht). Ohne `await`
    dazwischen, damit zwei gleichzeitige Anfragen nicht beide durchkommen."""
    with _lock:
        if attempt.mfa_key is not None and attempt.mfa_key in _mfa_used:
            return False
        if totp_step is not None and attempt.account is not None:
            if totp_step <= _totp_steps.get(attempt.account, -1):
                return False
            _totp_steps[attempt.account] = totp_step
        if attempt.mfa_key is not None:
            _mfa_used[attempt.mfa_key] = _now()
        return True
