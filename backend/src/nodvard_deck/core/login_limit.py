"""Drosselung fehlgeschlagener Anmeldungen in gleitenden Zeitfenstern, bewusst im Speicher: kein Schreibzugriff pro
Versuch, und die Prüfung läuft vor der teuren Argon2-Verifikation."""

from __future__ import annotations

import hashlib
import ipaddress
import math
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

WINDOW_SECONDS = 5 * 60
MAX_FAILURES_PER_USER = 10
"""Fehlversuche je (IP, Benutzername) im Fenster, danach 429."""
MAX_FAILURES_PER_IP = 30
"""Fehlversuche je IP über alle Benutzernamen im Fenster, danach 429; bremst das Durchprobieren vieler Namen."""
MAX_MFA_FAILURES = 5
"""Falsche 2FA-Codes je `mfa_token`, danach ist es verbraucht; sie zählen auch für (IP, Benutzername), sonst ließe
sich über immer neue Token weiter raten."""
MFA_ENTRY_TTL_SECONDS = 60 * 60
"""So lange wird der Zähler je `mfa_token` aufgehoben, länger als jedes Token lebt."""

MAX_MFA_FAILURES_PER_ACCOUNT = 10
"""Falsche 2FA-Codes je Konto in `ACCOUNT_WINDOW_SECONDS`, egal von welcher IP, danach 429; wer zum Code kommt, kennt
das Passwort schon, und mit vielen Adressen ließe er sich sonst durchprobieren."""
ACCOUNT_WINDOW_SECONDS = 15 * 60
MAX_MFA_FAILURES_PER_ACCOUNT_DAY = 20
"""Dasselbe über 24 Stunden; bremst auch langsames, über den Tag verteiltes Raten."""
ACCOUNT_DAY_SECONDS = 24 * 60 * 60
_ACCOUNT_LIMITS = (
    (MAX_MFA_FAILURES_PER_ACCOUNT, ACCOUNT_WINDOW_SECONDS),
    (MAX_MFA_FAILURES_PER_ACCOUNT_DAY, ACCOUNT_DAY_SECONDS),
)
TOTP_STEP_SECONDS = 30
"""Länge eines TOTP-Zeitschritts (Standard, wie bei pyotp)."""
RESTORE_FUTURE_SECONDS = ACCOUNT_DAY_SECONDS
"""Fehlversuche, die laut Protokoll höchstens so weit in der Zukunft liegen (Uhr ging beim Schreiben vor oder beim Start
nach, z. B. Raspberry Pi vor dem Zeitabgleich), zählen beim Wiederherstellen als gerade eben. Was weiter voraus liegt,
zählt nicht, sonst sperrte eine falsch gestellte Uhr jedes Konto einen Tag lang."""

_SWEEP_THRESHOLD = 1024
_UNKNOWN_IP = "unbekannt"

SCOPE_USER = "user"
SCOPE_IP = "ip"
SCOPE_ACCOUNT = "account"


def _now() -> float:
    return time.monotonic()


def _wall_now() -> float:
    return time.time()


class Locked(Exception):
    """Zu viele Fehlversuche; `retry_after` Sekunden bis zum nächsten erlaubten Versuch."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(lockout_message(retry_after))
        self.retry_after = retry_after


class MfaExhausted(Exception):
    """Dieses `mfa_token` hat schon `MAX_MFA_FAILURES` falsche Codes gesehen."""


class MfaTokenUsed(Exception):
    """Mit diesem `mfa_token` hat sich schon jemand angemeldet; es gilt nur einmal."""


def lockout_message(retry_after: int) -> str:
    minutes = max(1, math.ceil(retry_after / 60))
    unit = "Minute" if minutes == 1 else "Minuten"
    return f"Zu viele Fehlversuche. Bitte in {minutes} {unit} erneut versuchen."


IPV6_BUCKET_PREFIX = 64
"""IPv6-Adressen zählen je Netz dieser Größe: Ein Anschluss bekommt meist ein ganzes /64 und könnte sonst mit jeder
neuen Adresse von vorn raten."""


def ip_bucket(ip: str | None) -> str:
    """Wofür die Grenzen je Adresse zählen: IPv4 je Adresse, IPv6 je /64, `::ffff:192.0.2.1` als IPv4; Nicht-Adressen
    (`unbekannt`, `konto:<id>`) bleiben, wie sie sind. Ins Protokoll kommt weiter die echte Adresse."""
    if not ip:
        return _UNKNOWN_IP
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network((addr, IPV6_BUCKET_PREFIX), strict=False))
    return str(addr)


@dataclass
class Attempt:
    ip: str
    """Die echte Adresse (fürs Protokoll)."""
    username: str
    ts: float
    mfa_key: str | None = None
    account: str | None = None
    bucket: str = ""
    """Wofür gezählt wird (`ip_bucket`)."""

    def __post_init__(self) -> None:
        if not self.bucket:
            self.bucket = ip_bucket(self.ip)


# Der Zustand gilt je Prozess (uvicorn läuft mit einem Worker) und ist nach einem Neustart weg, Ausnahme: die
# `restore_*`-Funktionen unten. Alle Funktionen sind synchron und ohne `await`; das Lock schützt zusätzlich vor Threads.
_lock = threading.Lock()
_by_user: dict[tuple[str, str], deque[float]] = {}
_by_ip: dict[str, deque[float]] = {}
_announced: set[tuple[str, ...]] = set()
"""Schlüssel, deren laufende Sperre schon gemeldet wurde (ein Audit-Eintrag je Sperre)."""
_mfa: dict[str, tuple[int, float]] = {}
"""Hash des `mfa_token` -> (falsche Codes, erster Versuch)."""
_by_account: dict[str, deque[float]] = {}
"""Konto -> Zeitpunkte der 2FA-Versuche (länger als `WINDOW_SECONDS` aufgehoben)."""
_mfa_used: dict[str, float] = {}
"""Hash des `mfa_token` -> Zeitpunkt, an dem damit eine Anmeldung gelungen ist."""
_totp_steps: dict[str, int] = {}
"""Konto -> letzter angenommener TOTP-Zeitschritt; nach einem Neustart gilt ein Code noch höchstens für sein
Zeitfenster von rund 90 Sekunden erneut."""


def reset() -> None:
    """Vergisst alles (für Tests)."""
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
    """0, wenn nicht gesperrt; sonst Sekunden, bis wieder weniger als `limit` Einträge im Fenster liegen."""
    if len(dq) < limit:
        return 0
    return max(1, math.ceil(dq[len(dq) - limit] + WINDOW_SECONDS - now))


def _account_retry(dq: deque[float], now: float) -> int:
    """0, wenn das Konto für weitere 2FA-Versuche frei ist; sonst die längste Wartezeit über beide Fenster."""
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
    """Vor der Passwort-/Code-Prüfung aufrufen: wirft `MfaTokenUsed`, `MfaExhausted` oder `Locked`, sonst zählt der
    Versuch sofort; `account` nur beim zweiten Faktor angeben (zusätzliche IP-unabhängige Grenze je Konto)."""
    # `ip` ist `request.client.host`. Hinter einem Reverse-Proxy ist das die echte Adresse nur, wenn der Proxy unter
    # `system.trusted_proxies` steht (`api/proxy_headers.py`); sonst teilen sich alle Anfragen die Grenze je IP.
    ip = ip or _UNKNOWN_IP
    bucket = ip_bucket(ip)
    user_key = (bucket, username)
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
        ip_dq = _pruned(_by_ip, bucket, now)
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
            _announced.discard((SCOPE_IP, bucket))
        if wait_user or wait_ip or wait_account:
            raise Locked(max(wait_user, wait_ip, wait_account))

        # Schon beim Start zählen, nicht erst beim Scheitern: Sonst kämen viele gleichzeitige Anfragen alle an der
        # Prüfung vorbei, bevor der erste Fehlschlag eingetragen ist.
        _by_user.setdefault(user_key, deque()).append(now)
        _by_ip.setdefault(bucket, deque()).append(now)
        if account is not None:
            _by_account.setdefault(account, deque()).append(now)
        if mfa_key is not None:
            _mfa.setdefault(mfa_key, (0, now))
        if len(_by_user) + len(_by_ip) + len(_mfa) + len(_by_account) + len(_mfa_used) > _SWEEP_THRESHOLD:
            _sweep(now)
        return Attempt(ip=ip, username=username, ts=now, mfa_key=mfa_key, account=account, bucket=bucket)


def failed(attempt: Attempt) -> list[str]:
    """Der (schon gezählte) Versuch war falsch; gibt die Bereiche zurück, deren Sperre genau jetzt beginnt, je Sperre
    nur einmal, damit der Aufrufer einen Audit-Eintrag schreibt."""
    started: list[str] = []
    with _lock:
        now = _now()
        if attempt.mfa_key is not None:
            count, first = _mfa.get(attempt.mfa_key, (0, now))
            _mfa[attempt.mfa_key] = (count + 1, first)
        checks = (
            (SCOPE_USER, _by_user, (attempt.bucket, attempt.username), MAX_FAILURES_PER_USER),
            (SCOPE_IP, _by_ip, attempt.bucket, MAX_FAILURES_PER_IP),
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
    """Der Versuch zählt nicht (Passwort richtig, zweiter Faktor folgt), bisherige Fehlversuche bleiben aber stehen."""
    with _lock:
        _discard(_by_user.get((attempt.bucket, attempt.username)), attempt.ts)
        _discard(_by_ip.get(attempt.bucket), attempt.ts)
        if attempt.account is not None:
            _discard(_by_account.get(attempt.account), attempt.ts)


def succeeded(attempt: Attempt) -> None:
    """Anmeldung komplett: Zähler für (IP, Benutzername) und `mfa_token` fallen weg, vom IP-Zähler nur der Versuch."""
    with _lock:
        _by_user.pop((attempt.bucket, attempt.username), None)
        _announced.discard((SCOPE_USER, attempt.bucket, attempt.username))
        _discard(_by_ip.get(attempt.bucket), attempt.ts)
        if attempt.account is not None:
            _discard(_by_account.get(attempt.account), attempt.ts)
        if attempt.mfa_key is not None:
            _mfa.pop(attempt.mfa_key, None)


def claim_success(attempt: Attempt, *, totp_step: int | None = None) -> bool:
    """Der zweite Faktor stimmt (vor dem Ausgeben der Anmeldung aufrufen); `False`, wenn der Code (`totp_step`, nur beim
    Authenticator-Weg) oder das `mfa_token` schon benutzt ist; Wiederherstellungs-Codes verbraucht die Datenbank."""
    # Prüfen und Eintragen in einem Zug, damit zwei gleichzeitige Anfragen nicht beide durchkommen.
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


def remember_totp_step(account: str, step: int) -> None:
    """Ein Authenticator-Code wurde außerhalb von `claim_success` angenommen (Einrichtung bestätigt); sein Zeitschritt
    gilt danach als benutzt. Abgelehnt wird hier nichts, denn der Code gehört zu einem neuen Schlüssel."""
    with _lock:
        if step > _totp_steps.get(account, -1):
            _totp_steps[account] = step


def _restored(wall_times: Iterable[float], *, window: float, limit: int, now: float, now_wall: float) -> list[float]:
    """Zeitpunkte aus dem Protokoll (Wanduhr) als Zeitpunkte dieses Prozesses (monoton) mit demselben Alter, nur die im
    Fenster, davon die jüngsten `limit`, aufsteigend; Zukunft siehe `RESTORE_FUTURE_SECONDS`."""
    ages = sorted(
        max(0.0, now_wall - ts)
        for ts in wall_times
        if now_wall - ts < window and ts - now_wall <= RESTORE_FUTURE_SECONDS
    )[:limit]
    return sorted(now - age for age in ages)


def _merge(store: dict, key, times: list[float]) -> None:
    if times:
        store[key] = deque(sorted([*store.get(key, ()), *times]))


def restore_account_failures(account: str, wall_times: Iterable[float], *, now_wall: float | None = None) -> int:
    """Trägt falsche 2FA-Codes aus dem Protokoll (`wall_times`, Sekunden seit 1970) für die Grenze je Konto wieder
    ein und gibt ihre Zahl zurück; nur einmal beim Start aufrufen, bevor Anfragen kommen (sonst zählen sie doppelt)."""
    # Sonst finge die Grenze je Konto nach jedem Neustart von vorn an, und wer Sitzung und Passwort hat, könnte im
    # Wechsel raten und neu starten.
    with _lock:
        restored = _restored(
            wall_times,
            window=ACCOUNT_DAY_SECONDS,
            limit=MAX_MFA_FAILURES_PER_ACCOUNT_DAY,
            now=_now(),
            now_wall=_wall_now() if now_wall is None else now_wall,
        )
        _merge(_by_account, account, restored)
        return len(restored)


def restore_failures(ip: str, username: str, wall_times: Iterable[float], *, now_wall: float | None = None) -> int:
    """Wie `restore_account_failures`, für den Zähler je (`ip`, `username`); gedacht für Zähler, die mit einem
    Pseudo-Namen an einem Konto hängen (`services.auth.RECOVERY_ATTEMPT_KEY`); die je Adresse bleiben flüchtig."""
    with _lock:
        restored = _restored(
            wall_times,
            window=WINDOW_SECONDS,
            limit=MAX_FAILURES_PER_USER,
            now=_now(),
            now_wall=_wall_now() if now_wall is None else now_wall,
        )
        _merge(_by_user, (ip_bucket(ip), username), restored)
        return len(restored)
