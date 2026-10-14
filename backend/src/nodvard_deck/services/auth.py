"""Anmeldung, Token-Ausgabe und -Erneuerung, Zwei-Faktor-Einrichtung und Owner-Einrichtung.

Endpunkte in `api/v1/auth.py` und `api/v1/me.py` rufen nur diese Funktionen auf und übersetzen deren Fehler in HTTP."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from ..config import Settings
from ..core import login_limit, security, setup_code, vault
from ..core.rbac import BUILTIN_ROLES, has_permission
from ..db import new_id, refresh_relationships, utcnow
from ..models import AuditEntry, RecoveryCode, RefreshToken, Role, RolePermission, User
from . import audit as audit_service
from . import settings as settings_service

_log = logging.getLogger("nodvard_deck.auth")

RECOVERY_CODE_COUNT = 10
"""So viele Wiederherstellungs-Codes bekommt man je Satz."""

# Bei unbekanntem Namen oder deaktiviertem Konto wird trotzdem gegen diesen Hash geprüft (siehe `login()`), damit die
# Antwortzeit nicht verrät, ob der Name existiert.
_DUMMY_PASSWORD_HASH = security.hash_password(security.generate_opaque_token())


class AuthError(Exception):
    """Basis für alle Auth-Fehler, die als 4xx an die API-Schicht durchgereicht werden."""


class InvalidCredentials(AuthError):
    pass


class MfaRequired(AuthError):
    def __init__(self, mfa_token: str) -> None:
        super().__init__("Zwei-Faktor-Code erforderlich")
        self.mfa_token = mfa_token


class InvalidMfaCode(AuthError):
    pass


class TooManyAttempts(AuthError):
    """Zu viele Fehlversuche (HTTP 429); `retry_after` in Sekunden für den `Retry-After`-Header.

    `None`, wenn Warten nicht hilft: Das `mfa_token` ist verbraucht, man muss sich neu anmelden."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


REFRESH_GRACE_SECONDS = 30
"""Gnadenfrist: Ein gerade durch Rotation widerrufener Refresh-Token bekommt noch einen frischen Access-Token
(zwei Tabs, die fast gleichzeitig aktualisieren)."""

SENSITIVE_ATTEMPT_KEY = "passwortabfrage"
"""Pseudo-Benutzername für die Drosselung falscher Passwörter bei empfindlichen Aktionen."""

RECOVERY_ATTEMPT_KEY = "wiederherstellungscode"
"""Pseudo-Benutzername für falsche Wiederherstellungs-Codes bei Bestätigungen (`require_current_totp`), je Konto.
Eigener Zähler wie beim Anmelden: Den der Passwortabfrage setzt das richtige Passwort davor jedes Mal zurück."""


def _account_key(user_id: str) -> str:
    """Pseudo-Adresse `konto:<id>` für Zähler, die an einem Konto hängen statt an einer Adresse (Passwortabfrage,
    Wiederherstellungs-Codes bei Bestätigungen)."""
    return f"konto:{user_id}"


SETUP_ATTEMPT_KEY = "einrichtung"
"""Pseudo-Benutzername für die Drosselung falscher Einrichtungscodes (je IP)."""

MFA_EXHAUSTED_MESSAGE = "Zu viele falsche Zwei-Faktor-Codes. Bitte melde dich erneut an."
MFA_TOKEN_USED_MESSAGE = "Dieser Anmeldeschritt wurde schon abgeschlossen. Bitte melde dich erneut an."
TOTP_CODE_USED_MESSAGE = "Dieser Code wurde schon benutzt. Warte, bis die App einen neuen Code anzeigt."


def _begin_attempt(
    ip: str | None,
    username: str,
    *,
    mfa_token: str | None = None,
    account: str | None = None,
) -> login_limit.Attempt:
    """Prüft die Drosselung vor jeder Passwort- und Code-Prüfung (kein Argon2-Aufwand für gesperrte Versuche) und zählt
    den Versuch sofort mit."""
    try:
        return login_limit.begin(ip, username, mfa_token=mfa_token, account=account)
    except login_limit.MfaTokenUsed:
        raise InvalidMfaCode(MFA_TOKEN_USED_MESSAGE) from None
    except login_limit.MfaExhausted:
        raise TooManyAttempts(MFA_EXHAUSTED_MESSAGE) from None
    except login_limit.Locked as exc:
        raise TooManyAttempts(str(exc), retry_after=exc.retry_after) from None


UNKNOWN_ACTOR = "unbekannt"
"""Akteur im Protokoll, wenn der eingegebene Benutzername zu keinem Konto passt."""


def unknown_login_audit(settings: Settings, typed: str, *, length: int | None = None) -> dict:
    """Was das Protokoll über eine Anmeldung mit unbekanntem Namen festhält: nicht den Text, nur Kennung und Länge.

    Die Kennung ist ein HMAC mit dem Server-Schlüssel (nicht umkehrbar); gleiche Eingaben haben dieselbe."""
    # Wer sein Passwort ins Namensfeld tippt, soll es im Protokoll nicht wiederfinden; das lesen auch Konten mit „Nur
    # ansehen“.
    key = settings.get_or_create_jwt_secret().encode("utf-8")
    digest = hmac.new(key, b"login-username-ref\0" + typed.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"username_ref": digest[:12], "username_length": len(typed) if length is None else length}


async def _verify_password(
    attempt: login_limit.Attempt | None, password: str, password_hash: str, *, signed_in: bool = False
) -> bool:
    """Argon2-Prüfung im Thread, begrenzt (`security.verify_password_async`)."""
    try:
        return await security.verify_password_async(password, password_hash, signed_in=signed_in)
    except security.HashingBusy:
        # Bei Überlast zählt der Versuch nicht als Fehlversuch: Der Nutzer kann nichts dafür, und ein Angreifer soll so
        # keine fremden Konten sperren können.
        if attempt is not None:
            login_limit.release(attempt)
        raise


def _lock_windows(scopes: list[str]) -> dict[str, int]:
    """Fenster der Grenzen, die eine Sperre ausgelöst haben, für `detail` im Protokoll.

    Passwörter und Anmeldungen (Scopes `user`, `ip`) zählen in `WINDOW_SECONDS`."""
    # Die Grenze je Konto für den zweiten Faktor hat zwei Fenster; ist sie dabei, stehen beide immer drin, denn aus den
    # Scopes allein sieht man nicht, welches griff (auch zusammen mit einer anderen Grenze: Beim Anmelden erreichen
    # (Adresse, Name) und Konto ihre 10 mit demselben Versuch). Das kurze `window_seconds` gilt nur bei ihr allein.
    if login_limit.SCOPE_ACCOUNT not in scopes:
        return {"window_seconds": login_limit.WINDOW_SECONDS}
    short = login_limit.ACCOUNT_WINDOW_SECONDS if scopes == [login_limit.SCOPE_ACCOUNT] else login_limit.WINDOW_SECONDS
    return {
        "window_seconds": short,
        "account_window_seconds": login_limit.ACCOUNT_WINDOW_SECONDS,
        "day_window_seconds": login_limit.ACCOUNT_DAY_SECONDS,
    }


async def _record_failure(
    session: AsyncSession,
    attempt: login_limit.Attempt,
    *,
    actor_id: str,
    user_agent: str | None,
    sensitive_ip: str | None = None,
    sensitive_user_id: str | None = None,
    actor_type: str = "user",
    username_detail: dict | None = None,
) -> list[str]:
    """Verbucht einen Fehlversuch; beginnt dadurch eine Sperre, steht genau ein `login.locked`-Eintrag im Protokoll
    (`auth.password_check_locked` mit `sensitive_user_id`, der Passwortabfrage bei empfindlichen Aktionen). Der
    eingegebene Name kommt nur ins Protokoll, wenn er zu einem Konto gehört, sonst `username_detail` und `anonymous`."""
    scopes = login_limit.failed(attempt)
    if not scopes:
        return []
    if sensitive_user_id is not None:
        # Je greifender Grenze ein Satz. Der Zähler je Nutzer (Passwörter bzw. Wiederherstellungs-Codes) hat 10
        # Versuche, der für alle Abfragen des Kontos zusammen (Scope `ip`, Schlüssel `konto:<id>`) eine höhere Grenze;
        # ihn setzt kein richtiges Passwort zurück.
        minutes = login_limit.WINDOW_SECONDS // 60
        parts: list[str] = []
        if login_limit.SCOPE_USER in scopes:
            parts.append(f"{login_limit.MAX_FAILURES_PER_USER} Fehlversuche in {minutes} Minuten")
        elif login_limit.SCOPE_IP in scopes:
            parts.append(f"{login_limit.MAX_FAILURES_PER_IP} Fehlversuche in {minutes} Minuten")
        if login_limit.SCOPE_ACCOUNT in scopes:
            parts.append(
                "zu viele falsche Zwei-Faktor-Codes für dieses Konto "
                f"({login_limit.MAX_MFA_FAILURES_PER_ACCOUNT} in {login_limit.ACCOUNT_WINDOW_SECONDS // 60} Minuten "
                f"oder {login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY} in 24 Stunden, Anmeldung und Bestätigung zusammen)"
            )
        why = "; ".join(parts)
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=actor_id,
            action="auth.password_check_locked",
            outcome="failure",
            target_type="user",
            target_id=sensitive_user_id,
            reason=f"Sicherheitsabfragen (Passwort, Zwei-Faktor-Code) vorübergehend gesperrt: {why}.",
            detail={"scopes": scopes, **_lock_windows(scopes)},
            ip=sensitive_ip,
            user_agent=user_agent,
        )
        return scopes
    minutes = login_limit.WINDOW_SECONDS // 60
    reasons = {
        login_limit.SCOPE_USER: (
            f"{login_limit.MAX_FAILURES_PER_USER} Fehlversuche für diesen Benutzernamen "
            f"in {minutes} Minuten"
        ),
        login_limit.SCOPE_IP: (
            f"{login_limit.MAX_FAILURES_PER_IP} Fehlversuche "
            f"{'aus diesem Netz (IPv6 zählt je /64)' if attempt.bucket != attempt.ip else 'von dieser IP'} in {minutes} Minuten"
        ),
        login_limit.SCOPE_ACCOUNT: (
            f"zu viele falsche Zwei-Faktor-Codes für dieses Konto "
            f"({login_limit.MAX_MFA_FAILURES_PER_ACCOUNT} in {login_limit.ACCOUNT_WINDOW_SECONDS // 60} Minuten "
            f"oder {login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY} in 24 Stunden, von beliebigen Adressen)"
        ),
    }
    await audit_service.log(
        session,
        actor_type=actor_type,
        actor_id=actor_id,
        action="login.locked",
        outcome="failure",
        reason="Anmeldung vorübergehend gesperrt: " + "; ".join(reasons[s] for s in scopes) + ".",
        detail={
            "scopes": scopes,
            **(username_detail if username_detail is not None else {"username": attempt.username}),
            **_lock_windows(scopes),
            **({"network": attempt.bucket} if attempt.bucket != attempt.ip else {}),
        },
        ip=attempt.ip,
        user_agent=user_agent,
    )
    return scopes


class UsersAlreadyExist(AuthError):
    pass


class InvalidSetupCode(AuthError):
    """Einrichtungscode fehlt oder stimmt nicht (HTTP 403)."""


async def _commit_before_raising(session: AsyncSession) -> None:
    """Schreibt den Protokolleintrag fest, bevor die `AuthError` zu einer HTTP-Antwort wird."""
    # `session_scope()` rollt bei jeder durchgereichten Exception zurück, auch bei einem erwarteten 401; ohne dieses
    # Zwischen-Commit ginge der gerade geschriebene `login.failed`-/`mfa.failed`-Eintrag verloren. Sicher, weil sonst
    # nur eine gewollte Lazy-Key-Migration (`vault.read_secret_plaintext`) im Puffer steht.
    await session.commit()


async def _reload_unchanged(
    session: AsyncSession, user_id: str, *, password_hash: str, two_factor: bool = False
) -> User | None:
    """Liest das Konto nach einer langen Prüfung frisch und gibt es nur zurück, wenn es noch so ist wie geprüft.

    Gleich heißt: vorhanden, aktiv, derselbe `password_hash` (nie der Dummy) und mit `two_factor` weiter an."""
    # Ändert sich das Konto erst nach diesem Lesen, ist das dasselbe Rennen wie bei jeder anderen Anfrage.
    current = await session.get(User, user_id, populate_existing=True)
    if current is None or not current.is_active or current.password_hash != password_hash:
        return None
    if two_factor and not two_factor_enabled(current):
        return None
    return current


async def _store_rehash(session: AsyncSession, user: User, *, old_hash: str, new_hash: str) -> None:
    """Speichert den neuen Hash nur, wenn das Passwort unverändert ist (`WHERE password_hash = <alter Hash>`).

    Sonst würde ein gleichzeitiger Passwortwechsel mit dem Hash des alten Passworts überschrieben."""
    result = await session.execute(
        update(User)
        .where(User.id == user.id, User.password_hash == old_hash)
        .values(password_hash=new_hash)
        .execution_options(synchronize_session=False)
    )
    if (result.rowcount or 0) == 1:
        set_committed_value(user, "password_hash", new_hash)


@dataclass
class IssuedTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str | None
    """Klartext, nur einmal sichtbar: Die API-Schicht legt ihn in einen HttpOnly-Cookie (Web) oder den JSON-Body (App).
    `None` nur in der Gnadenfrist von `refresh_access_token()`, der Client behält den Token vom ersten Aufruf."""
    refresh_expires_at: datetime | None
    user: User
    client_type: str
    """Vom Aufrufer durchgereicht: Ob Cookie oder Body, entscheidet die API-Schicht allein daraus."""
    session_id: str | None = None
    """Kennung der neu angelegten Refresh-Token-Zeile (= `sid` im Access-Token)."""


# ---------------------------------------------------------------------------
# Grenzen für Zwei-Faktor-Codes nach einem Neustart
# ---------------------------------------------------------------------------


CODE_FAILURE_ACTIONS = ("mfa.failed", "auth.totp_check_failed")
"""Protokolleinträge für falsche Zwei-Faktor-Codes (Anmeldung, `require_current_totp`) für die Grenze je Konto:
je einer mit dem Konto als `target_id`, ohne `detail.via` (Wiederherstellungs-Codes tragen `via: "recovery_code"`).
Ein neuer Weg mit Code muss sich daran halten, sonst vergisst die Grenze seine Fehlversuche bei jedem Neustart."""


async def _recent_code_failures(
    session: AsyncSession,
    *,
    actions: Sequence[str],
    recovery: bool,
    window: float,
    per_account: int,
    now_wall: float,
) -> dict[str, list[float]]:
    """Konto -> Zeitpunkte (Sekunden seit 1970) der jüngsten `per_account` falschen Codes der letzten `window` Sekunden.

    `recovery` wählt Wiederherstellungs-Codes oder alle anderen."""
    via = func.coalesce(AuditEntry.detail["via"].as_string(), "")
    # Begrenzt in der Abfrage selbst (je Konto), damit auch ein sehr volles Protokoll den Start nicht aufhält.
    ranked = (
        select(
            AuditEntry.target_id.label("account"),
            AuditEntry.ts.label("ts"),
            func.row_number()
            .over(partition_by=AuditEntry.target_id, order_by=(AuditEntry.ts.desc(), AuditEntry.id.desc()))
            .label("rank"),
        )
        .where(
            AuditEntry.action.in_(actions),
            AuditEntry.outcome == "failure",
            AuditEntry.target_type == "user",
            AuditEntry.target_id.is_not(None),
            AuditEntry.ts >= datetime.fromtimestamp(now_wall - window, UTC),
            AuditEntry.ts <= datetime.fromtimestamp(now_wall + login_limit.RESTORE_FUTURE_SECONDS, UTC),
            (via == VIA_RECOVERY) if recovery else (via != VIA_RECOVERY),
        )
        .subquery()
    )
    rows = await session.execute(select(ranked.c.account, ranked.c.ts).where(ranked.c.rank <= per_account))
    found: dict[str, list[float]] = {}
    for account, ts in rows:
        found.setdefault(account, []).append(ts.timestamp())
    return found


async def restore_code_limits(session: AsyncSession, *, now_wall: float | None = None) -> int:
    """Trägt beim Start die Zähler für falsche Zwei-Faktor-Codes aus dem Protokoll ein (gibt deren Zahl zurück)."""
    # Ohne das finge die Grenze nach jedem Neustart von vorn an: Wer Sitzung und Passwort hat, könnte raten, neu starten
    # und weiter raten. Es zählt, was im Protokoll steht (nach einer Sicherung deren Stand); die Aufbewahrung von
    # mindestens 7 Tagen löscht nichts aus den 24 Stunden.
    #
    # Bewusst flüchtig bleiben die Zähler je Adresse und Name, je `mfa_token` und die benutzten Codes
    # (`login_limit._totp_steps`): Die Grenze je Konto deckt das Raten des zweiten Faktors ab, egal von wo.
    if now_wall is None:
        now_wall = time.time()
    restored = 0
    # Grenze je Konto: falsche Codes der letzten 24 Stunden ohne `via: "recovery_code"`, Anmeldung und Bestätigung
    # zusammen wie im Speicher.
    per_account = await _recent_code_failures(
        session,
        actions=CODE_FAILURE_ACTIONS,
        recovery=False,
        window=login_limit.ACCOUNT_DAY_SECONDS,
        per_account=login_limit.MAX_MFA_FAILURES_PER_ACCOUNT_DAY,
        now_wall=now_wall,
    )
    # Wiederherstellungs-Codes bei Bestätigungen (`RECOVERY_ATTEMPT_KEY`): die letzten 5 Minuten. Ein richtiger Code
    # setzt den Zähler im Speicher zurück; nach einem Neustart zählen die Fehlversuche davor bis zum Ende ihres Fensters
    # weiter (strenger, nie lockerer).
    recovery = await _recent_code_failures(
        session,
        actions=("auth.totp_check_failed",),
        recovery=True,
        window=login_limit.WINDOW_SECONDS,
        per_account=login_limit.MAX_FAILURES_PER_USER,
        now_wall=now_wall,
    )
    for account, times in per_account.items():
        restored += login_limit.restore_account_failures(account, times, now_wall=now_wall)
    for account, times in recovery.items():
        restored += login_limit.restore_failures(_account_key(account), RECOVERY_ATTEMPT_KEY, times, now_wall=now_wall)
    return restored


# ---------------------------------------------------------------------------
# Rollen-Seeding
# ---------------------------------------------------------------------------


async def ensure_builtin_roles(session: AsyncSession) -> dict[str, Role]:
    """Legt admin/operator/viewer an, falls sie fehlen; idempotent, bei jedem Start aufrufbar.

    `owner` ist keine Rolle: Die Owner-Eigenschaft ist `User.is_owner` und umgeht RBAC (docs/03-DATA-MODEL.md §1).
    """
    # Relationship-Attribute eines geflushten Objekts sind in Async-SQLAlchemy weder lesbar noch per `.append()`
    # beschreibbar, solange sie nicht ausdrücklich geladen wurden (`MissingGreenlet`), auch bei neu angelegten. Darum
    # Kind-Zeilen per `session.add()` als eigene Objekte anlegen und danach `refresh_relationships` rufen.
    existing = (await session.execute(select(Role))).scalars().all()
    by_name = {r.name: r for r in existing}
    needs_refresh: set[str] = set()

    for role_name, permissions in BUILTIN_ROLES.items():
        role = by_name.get(role_name)
        if role is None:
            role = Role(
                name=role_name,
                is_builtin=True,
                description=f"Eingebaute Rolle '{role_name}'",
            )
            session.add(role)
            await session.flush()
            by_name[role_name] = role
            current_perms: set[str] = set()
        else:
            current_perms = {p.permission for p in role.permissions}

        missing = set(permissions) - current_perms
        for perm in missing:
            session.add(RolePermission(role_id=role.id, permission=perm))
        if missing:
            needs_refresh.add(role.id)

    await session.flush()

    for role in by_name.values():
        if role.id in needs_refresh:
            await refresh_relationships(session, role, "permissions")

    return by_name


# ---------------------------------------------------------------------------
# Berechtigungen eines Nutzers
# ---------------------------------------------------------------------------


def user_permissions(user: User) -> list[str]:
    """Der Owner bekommt den Wildcard-Scope `*` direkt, wie ihn `core.rbac.has_permission` ohnehin versteht."""
    if user.is_owner:
        return ["*"]
    perms: set[str] = set()
    for role in user.roles:
        for rp in role.permissions:
            perms.add(rp.permission)
    return sorted(perms)


def user_has_permission(user: User, permission: str) -> bool:
    if user.is_owner:
        return True
    return has_permission(user_permissions(user), permission)


# ---------------------------------------------------------------------------
# Owner-Bootstrap: Erstinbetriebnahme
# ---------------------------------------------------------------------------


async def needs_bootstrap(session: AsyncSession) -> bool:
    """Gibt es noch keinen Nutzer? Dieselbe Prüfung wie in `bootstrap_owner()`, aber nur lesend, ohne Seiteneffekt."""
    any_user = (await session.execute(select(User.id).limit(1))).first()
    return any_user is None


MAX_USERNAME_LENGTH = 64
"""Länger darf kein Benutzername sein (beim Anlegen und bei der Einrichtung)."""
MAX_LOGIN_PASSWORD_LENGTH = 1024
"""Großzügig über den 255 Zeichen beim Setzen; alles darüber ist beim Login nie richtig."""


def normalize_username(username: str) -> str:
    """Benutzernamen werden immer klein und ohne Leerraum gespeichert und verglichen."""
    return username.strip().lower()


USERNAME_MIN_LENGTH = 3
_USERNAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")
USERNAME_RULE_TEXT = (
    "Benutzername: Nur Kleinbuchstaben, Ziffern sowie . - und _ erlaubt, ohne Leerzeichen; "
    "er muss mit einem Buchstaben oder einer Ziffer beginnen."
)
USERNAME_TOO_SHORT_TEXT = f"Benutzername: Mindestens {USERNAME_MIN_LENGTH} Zeichen."


def validate_new_username(username: str) -> str:
    """Prüft den Namen eines neuen Kontos: wie `normalize_username`, aber nur `a-z 0-9 . - _`; sonst `ValueError`.

    Gilt nur beim Anlegen, damit sich bestehende Konten mit Leerzeichen weiter anmelden und zurücksetzen lassen."""
    # Sonst entstünde aus „Kollege Max“ das Konto „kollege max“, und jeder Notfall-Befehl bräuchte Anführungszeichen.
    name = normalize_username(username)
    if len(name) < USERNAME_MIN_LENGTH:
        raise ValueError(USERNAME_TOO_SHORT_TEXT)
    if not _USERNAME_PATTERN.fullmatch(name):
        raise ValueError(USERNAME_RULE_TEXT)
    return name


SETUP_CODE_HINT = "Der Einrichtungscode steht im Protokoll des Containers (beim Start ausgegeben)."


async def prepare_setup_code(session: AsyncSession, settings: Settings) -> str | None:
    """Beim Start: Ohne Nutzer den Einrichtungscode bereitstellen und ins Protokoll schreiben, sonst eine
    übrig gebliebene Code-Datei löschen. Gibt den Code zurück (`None` = nichts zu tun)."""
    if not await needs_bootstrap(session):
        setup_code.clear(settings.data_dir)
        return None
    if settings.setup_code and setup_code.valid_preset(settings.setup_code) is None:
        print(
            "Hinweis: NODVARD_DECK_SETUP_CODE ist zu kurz (mindestens "
            f"{setup_code.MIN_PRESET_LEN} Zeichen) und wird ignoriert.",
            flush=True,
        )
    code, _created = setup_code.ensure(settings.data_dir, settings.setup_code)
    # `print` statt Logging: Der Code muss sichtbar sein, auch wenn niemand Logging konfiguriert hat (uvicorn tut das
    # nur für seine eigenen Logger).
    print(setup_code.banner(code), flush=True)
    return code


async def verify_setup_code(
    session: AsyncSession,
    settings: Settings,
    *,
    code: str,
    ip: str | None,
    user_agent: str | None = None,
) -> None:
    """Prüft den Einrichtungscode, gedrosselt wie der Login (je IP); wirft `InvalidSetupCode` bzw. `TooManyAttempts`.

    Fehlversuche stehen im Protokoll."""
    attempt = _begin_attempt(ip, SETUP_ATTEMPT_KEY)
    # Fehlt der Code (z. B. nach einem abgebrochenen Start), wird einer angelegt und ausgegeben; die Anfrage scheitert
    # dann, denn der Anrufer kennt ihn nicht.
    created_code, created = setup_code.ensure(settings.data_dir, settings.setup_code)
    if created:
        print(setup_code.banner(created_code), flush=True)
    if not setup_code.verify(settings.data_dir, code, settings.setup_code):
        who = ip or "unbekannt"
        await audit_service.log(
            session,
            actor_type="anonymous",
            actor_id=who,
            action="setup.failed",
            outcome="failure",
            reason="Falscher Einrichtungscode." if code.strip() else "Kein Einrichtungscode angegeben.",
            ip=ip,
            user_agent=user_agent,
        )
        await _record_failure(session, attempt, actor_id=who, user_agent=user_agent)
        await _commit_before_raising(session)
        if code.strip():
            raise InvalidSetupCode(f"Der Einrichtungscode stimmt nicht. {SETUP_CODE_HINT}")
        raise InvalidSetupCode(f"Bitte den Einrichtungscode eingeben. {SETUP_CODE_HINT}")
    login_limit.succeeded(attempt)


async def bootstrap_owner(session: AsyncSession, *, username: str, password: str) -> User:
    """Legt den ersten Nutzer als Owner an, nur wenn noch keiner existiert.

    Der Endpunkt braucht deshalb keine Anmeldung und ist selbstlimitierend: Jeder weitere Aufruf schlägt fehl."""
    if not await needs_bootstrap(session):
        raise UsersAlreadyExist("Es existiert bereits mindestens ein Nutzer.")

    await ensure_builtin_roles(session)

    user = User(
        username=normalize_username(username),
        password_hash=await security.hash_password_async(password),
        is_owner=True,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    # Neue Installation: neue Server-Schlüssel nur nach Bestätigung merken (Einstellung `ssh.confirm_new_host_keys`).
    # Bestehende Installationen haben den Wert nicht und behalten das alte Verhalten; die Umgebungsvariable übersteuert
    # beides.
    await settings_service.set_global(session, "ssh.confirm_new_host_keys", True, updated_by_user_id=user.id)
    return user


# ---------------------------------------------------------------------------
# Token-Ausgabe (gemeinsam für Login, Zwei-Faktor und Erneuerung)
# ---------------------------------------------------------------------------


def _create_access_token(settings: Settings, user: User, session_id: str) -> str:
    """Access-Token für die Anmeldung `session_id` (Refresh-Token-Zeile, siehe `sid`)."""
    return security.create_jwt(
        subject=user.id,
        token_type="access",
        secret=settings.get_or_create_jwt_secret(),
        ttl_seconds=settings.access_token_ttl_seconds,
        extra_claims={"perms": user_permissions(user), "sid": session_id},
    )


async def _issue_tokens(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    client_type: str,
    device_name: str | None,
    user_agent: str | None,
    ip: str | None,
) -> IssuedTokens:
    # `sid` im Access-Token = Kennung der Anmeldung (Refresh-Token-Zeile), zu der es gehört. Damit weiß z. B. der
    # Passwortwechsel, welche Sitzung die aktuelle ist und angemeldet bleiben darf; der Refresh-Cookie kommt dort nicht
    # an (Cookie-Pfad /api/v1/auth), und die App hat gar keinen.
    refresh_id = new_id()
    access_token = _create_access_token(settings, user, refresh_id)

    raw_refresh = security.generate_opaque_token()
    expires_at = utcnow() + timedelta(days=settings.refresh_token_ttl_days)
    session.add(
        RefreshToken(
            id=refresh_id,
            user_id=user.id,
            token_hash=security.hash_opaque_token(raw_refresh),
            client_type=client_type,
            device_name=device_name,
            user_agent=user_agent,
            ip=ip,
            expires_at=expires_at,
        )
    )

    user.last_login_at = utcnow()
    await session.flush()

    return IssuedTokens(
        access_token=access_token,
        access_expires_in=settings.access_token_ttl_seconds,
        refresh_token=raw_refresh,
        refresh_expires_at=expires_at,
        user=user,
        client_type=client_type,
        session_id=refresh_id,
    )


# ---------------------------------------------------------------------------
# Login / MFA / Refresh / Logout
# ---------------------------------------------------------------------------


async def login(
    session: AsyncSession,
    settings: Settings,
    *,
    username: str,
    password: str,
    client_type: str = "web",
    device_name: str | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
) -> IssuedTokens:
    # Benutzernamen sind immer klein; ältere Konten mit Großbuchstaben finden wir trotzdem, damit niemand ausgesperrt
    # wird.
    username = normalize_username(username)
    typed_length = len(username)
    # Zu lange Angaben sind nie gültig. Sie laufen wie ein unbekannter Benutzer durch, ohne die ganze Eingabe zu
    # speichern, in die Datenbank zu schreiben oder zu hashen; sonst ließen sich Speicher und Platte ohne Anmeldung
    # füllen.
    too_long = len(username) > MAX_USERNAME_LENGTH or len(password) > MAX_LOGIN_PASSWORD_LENGTH
    if too_long:
        username = username[:MAX_USERNAME_LENGTH]
        password = password[:MAX_LOGIN_PASSWORD_LENGTH]
    attempt = _begin_attempt(ip, username)
    user = None
    if not too_long:
        result = await session.execute(select(User).where(func.lower(User.username) == username))
        user = result.scalar_one_or_none()
    # Ab hier wartet die Anfrage auf die Passwort-Prüfung (Warteschlange, Argon2: auf einem Pi Sekunden). Die
    # Datenbankverbindung gehört in der Zeit nicht ihr, sonst füllt eine Anmelde-Flut den Verbindungs-Pool, und wer
    # schon angemeldet ist, wartet auf eine freie Verbindung. Das Konto wird danach frisch gelesen.
    await session.commit()

    if user is None:
        # Auch bei unbekanntem Namen läuft eine Argon2-Prüfung, sonst verriete die Antwortzeit, ob der Benutzer
        # existiert (Username-Enumeration).
        await _verify_password(attempt, password, _DUMMY_PASSWORD_HASH)
        # Der eingegebene Text kommt nicht ins Protokoll (es könnte ein ins Namensfeld getipptes Passwort sein), nur
        # Kennung und Länge (`unknown_login_audit`).
        unknown = unknown_login_audit(settings, username, length=typed_length)
        await audit_service.log(
            session,
            actor_type="anonymous",
            actor_id=UNKNOWN_ACTOR,
            action="login.failed",
            outcome="failure",
            reason="Eingabe zu lang." if too_long else "Unbekannter Benutzername.",
            detail=unknown,
            ip=ip,
            user_agent=user_agent,
        )
        await _record_failure(
            session, attempt, actor_id=UNKNOWN_ACTOR, user_agent=user_agent,
            actor_type="anonymous", username_detail=unknown,
        )
        await _commit_before_raising(session)
        raise InvalidCredentials("Ungültiger Benutzername oder Passwort.")

    # Argon2 läuft in jedem Fall genau einmal, auch bei einem deaktivierten Konto, sonst antwortete es spürbar schneller
    # und verriete, dass es existiert. Es wird wie ein unbekannter Name gegen den Dummy-Hash geprüft, sein echter Hash
    # bleibt unangetastet. Rehash und `_reload_unchanged` laufen nur für aktive Konten und mit dem echten Hash.
    real_hash = user.password_hash
    checked_hash = real_hash if user.is_active else _DUMMY_PASSWORD_HASH
    password_ok = await _verify_password(attempt, password, checked_hash)
    new_hash: str | None = None
    if password_ok and user.is_active and security.needs_rehash(real_hash):
        # Nur nebenbei; ist gerade zu viel los, geschieht es beim nächsten Mal.
        with contextlib.suppress(security.HashingBusy):
            new_hash = await security.hash_password_async(password)
    failure: str | None = None
    if not user.is_active:
        failure = "Konto deaktiviert."
    elif not password_ok:
        failure = "Falsches Passwort."
    elif await _reload_unchanged(session, user.id, password_hash=real_hash) is None:
        # Die Prüfung hat gedauert: Wurde das Konto inzwischen deaktiviert, gelöscht oder das Passwort geändert, darf
        # daraus keine Sitzung mehr werden (die Abmeldung aller Geräte ist dann schon gelaufen).
        failure = "Konto oder Passwort wurden während der Anmeldung geändert."
    if failure is not None:
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=user.id,
            action="login.failed",
            outcome="failure",
            reason=failure,
            target_type="user",
            target_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )
        await _record_failure(session, attempt, actor_id=user.id, user_agent=user_agent)
        await _commit_before_raising(session)
        raise InvalidCredentials("Ungültiger Benutzername oder Passwort.")

    if new_hash is not None:
        await _store_rehash(session, user, old_hash=real_hash, new_hash=new_hash)

    if user.totp_secret_id is not None and user.totp_confirmed_at is not None:
        secret = settings.get_or_create_jwt_secret()
        mfa_token = security.create_jwt(
            subject=user.id,
            token_type="mfa",
            secret=secret,
            ttl_seconds=settings.mfa_token_ttl_seconds,
            extra_claims={
                "client_type": client_type,
                "device_name": device_name,
                "user_agent": user_agent,
                "ip": ip,
            },
        )
        # Passwort stimmt: Dieser Versuch zählt nicht, die alten Fehlversuche bleiben aber stehen, bis auch der zweite
        # Faktor stimmt.
        login_limit.release(attempt)
        raise MfaRequired(mfa_token)

    tokens = await _issue_tokens(
        session,
        settings,
        user,
        client_type=client_type,
        device_name=device_name,
        user_agent=user_agent,
        ip=ip,
    )
    await audit_service.log(
        session,
        actor_type="user",
        actor_id=user.id,
        action="login.succeeded",
        outcome="success",
        target_type="user",
        target_id=user.id,
        ip=ip,
        user_agent=user_agent,
    )
    login_limit.succeeded(attempt)
    return tokens


async def verify_mfa(
    session: AsyncSession,
    settings: Settings,
    *,
    mfa_token: str,
    code: str,
    ip: str | None = None,
) -> IssuedTokens:
    """`ip` ist die IP dieser Anfrage (für die Drosselung); das Protokoll nennt die IP aus dem Anmeldeschritt."""
    secret = settings.get_or_create_jwt_secret()
    try:
        payload = security.decode_jwt(mfa_token, secret=secret, expected_type="mfa")
    except security.TokenError as exc:
        raise InvalidMfaCode(str(exc)) from exc

    user = await session.get(User, payload["sub"])
    if (
        user is None
        or not user.is_active
        or user.totp_secret_id is None
        or user.totp_confirmed_at is None
    ):
        raise InvalidMfaCode("Zwei-Faktor ist für dieses Konto nicht aktiv.")

    # Sechs Ziffern = Authenticator-Code, alles andere = Wiederherstellungs-Code.
    via_recovery = not security.is_totp_code(code)
    # Die Sperre je Konto gilt nur für sechsstellige Codes: Nur die lassen sich mit vielen Adressen durchprobieren,
    # Wiederherstellungs-Codes (zehn Zeichen aus 32) sind dafür zu lang. So kommt der Besitzer mit einem davon hinein,
    # während jemand mit seinem Passwort das Konto über falsche Codes sperrt (sonst bis zu 24 Stunden ohne Ausweg).
    attempt = _begin_attempt(
        ip or payload.get("ip"),
        normalize_username(user.username),
        mfa_token=mfa_token,
        account=None if via_recovery else user.id,
    )
    totp_step: int | None = None
    if via_recovery:
        try:
            valid = await consume_recovery_code(session, user, code)
        except security.HashingBusy:
            login_limit.release(attempt)
            raise
    else:
        keyring = vault.load_keyring(settings)
        totp_secret = await vault.read_secret_plaintext(session, keyring, user.totp_secret_id)
        totp_step = match_totp_step(totp_secret, "".join(code.split()))
        valid = totp_step is not None
    # Ein Code gilt nur einmal, ein mfa_token nur für eine Anmeldung (siehe `login_limit.claim_success`).
    replayed = valid and not login_limit.claim_success(attempt, totp_step=totp_step)
    if replayed:
        valid = False
    if not valid:
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=user.id,
            action="mfa.failed",
            outcome="failure",
            target_type="user",
            target_id=user.id,
            detail=({"via": "recovery_code"} if via_recovery else {})
            | ({"replayed": True} if replayed else {}),
            ip=payload.get("ip"),
            user_agent=payload.get("user_agent"),
        )
        scopes = await _record_failure(
            session, attempt, actor_id=user.id, user_agent=payload.get("user_agent")
        )
        if login_limit.SCOPE_ACCOUNT in scopes:
            await _report_mfa_guessing(session, user)
        await _commit_before_raising(session)
        if login_limit.mfa_exhausted(attempt):
            raise TooManyAttempts(MFA_EXHAUSTED_MESSAGE)
        if replayed and not via_recovery:
            raise InvalidMfaCode(TOTP_CODE_USED_MESSAGE)
        raise InvalidMfaCode(
            "Ungültiger Wiederherstellungs-Code." if via_recovery else "Ungültiger Zwei-Faktor-Code."
        )

    tokens = await _issue_tokens(
        session,
        settings,
        user,
        client_type=payload.get("client_type") or "web",
        device_name=payload.get("device_name"),
        user_agent=payload.get("user_agent"),
        ip=payload.get("ip"),
    )
    await audit_service.log(
        session,
        actor_type="user",
        actor_id=user.id,
        action="login.succeeded",
        outcome="success",
        target_type="user",
        target_id=user.id,
        detail={"via": "recovery_code" if via_recovery else "mfa"},
        ip=payload.get("ip"),
        user_agent=payload.get("user_agent"),
    )
    login_limit.succeeded(attempt)
    if via_recovery:
        await report_recovery_use(session, user, ip=payload.get("ip"), user_agent=payload.get("user_agent"))
    return tokens


async def _report_mfa_guessing(session: AsyncSession, user: User) -> None:
    """Meldet dem Besitzer, dass ein Konto durchprobiert wird (wer so oft rät, kennt das Passwort schon).

    Zugestellt wird nur nebenbei; ein Fehler dabei darf die Antwort an den Anrufer nie kippen."""
    try:
        from . import notifications as notifications_service

        await notifications_service.send(
            session,
            title="Zwei-Faktor-Code wird durchprobiert",
            body=(
                f"Für „{user.username}“ wurden zu viele falsche Zwei-Faktor-Codes eingegeben; weitere "
                "Versuche sind für eine Weile gesperrt. Wer so oft rät, kennt vermutlich das "
                "Passwort. Wenn du das nicht warst, ändere gleich dein Passwort. Dafür und fürs Anmelden brauchst "
                "du in der Zeit einen Wiederherstellungs-Code. Bestätigungen mit dem Code aus der App (zum Beispiel "
                "für ein Update) gehen erst wieder, wenn die Sperre vorbei ist."
            ),
            severity="warning",
            payload={"path": "/settings/account", "tags": ["warning", "key"]},
        )
    except Exception:  # noqa: BLE001 - die Antwort an den Anrufer geht vor
        _log.warning("Meldung zum Durchprobieren des 2FA-Codes nicht zugestellt", exc_info=True)


def match_totp_step(totp_secret: str, code: str) -> int | None:
    """Der Zeitschritt, zu dem `code` passt (aktueller, einer davor und einer danach wegen Uhrabweichung), sonst `None`.

    Wer einen Code nur einmal gelten lassen will, merkt sich den Schritt (`login_limit.claim_success`)."""
    totp = pyotp.TOTP(totp_secret)
    current = int(_totp_now() // login_limit.TOTP_STEP_SECONDS)
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp.at(step * login_limit.TOTP_STEP_SECONDS), code):
            return step
    return None


def _totp_now() -> float:
    return time.time()


RECOVERY_USE_LOGIN = "anmeldung"
"""`action` von `report_recovery_use` für die Anmeldung; sonst die `action` der Bestätigung (`require_current_totp`)."""

_RECOVERY_USE_TEXTS = {
    RECOVERY_USE_LOGIN: ("bei der Anmeldung", None, "ändere sofort das Passwort und erzeuge neue Codes"),
    "codes_erneuern": (
        "beim Erzeugen neuer Wiederherstellungs-Codes",
        "Es gibt jetzt einen neuen Satz, die bisherigen Codes gelten nicht mehr.",
        "ändere sofort das Passwort und erzeuge mit dem Code aus deiner App neue Codes",
    ),
    "2fa_abschalten": (
        "beim Abschalten der Zwei-Faktor-Anmeldung",
        "Die Zwei-Faktor-Anmeldung ist damit aus.",
        "ändere sofort das Passwort und schalte die Zwei-Faktor-Anmeldung wieder ein",
    ),
    "passwort_aendern": (
        "beim Ändern des Passworts",
        None,
        (
            "lass dein Passwort sofort zurücksetzen („Passwort vergessen?“ auf der Anmeldeseite), denn deine "
            "anderen Anmeldungen sind damit beendet, und erzeuge danach neue Codes"
        ),
    ),
}


async def report_recovery_use(
    session: AsyncSession,
    user: User,
    *,
    ip: str | None,
    user_agent: str | None,
    action: str = RECOVERY_USE_LOGIN,
) -> None:
    """Meldet die Nutzung eines Wiederherstellungs-Codes: Er ersetzt den zweiten Faktor und soll nie unbemerkt bleiben.

    Erst aufrufen, wenn die Aktion durch ist, damit `remaining` stimmt; die Meldung darf sie nie kippen."""
    where, outcome_text, advice = _RECOVERY_USE_TEXTS.get(action, _RECOVERY_USE_TEXTS[RECOVERY_USE_LOGIN])
    remaining = await recovery_codes_remaining(session, user.id)
    await audit_service.log(
        session,
        actor_type="user",
        actor_id=user.id,
        action="mfa.recovery_used",
        outcome="success",
        target_type="user",
        target_id=user.id,
        detail={"remaining": remaining} | ({} if action == RECOVERY_USE_LOGIN else {"aktion": action}),
        ip=ip,
        user_agent=user_agent,
    )
    try:
        from . import notifications as notifications_service

        if outcome_text is None:
            outcome_text = (
                f"Es sind noch {remaining} Codes übrig."
                if remaining
                else "Es sind keine Codes mehr übrig – bitte unter Mein Konto neue erzeugen."
            )
        await notifications_service.send(
            session,
            title="Wiederherstellungs-Code benutzt",
            body=(
                f"Für „{user.username}“ wurde {where} ein Wiederherstellungs-Code "
                f"statt des Authenticator-Codes verwendet. {outcome_text} "
                f"Warst du das nicht, {advice}."
            ),
            severity="warning",
            payload={"path": "/settings/account", "tags": ["warning", "key"]},
        )
    except Exception:  # noqa: BLE001 - die Aktion selbst ist längst gelungen
        _log.warning("Meldung zum Wiederherstellungs-Code nicht zugestellt", exc_info=True)


async def refresh_access_token(
    session: AsyncSession, settings: Settings, *, raw_refresh_token: str
) -> IssuedTokens:
    """Rotation: Der eingelöste Refresh-Token wird sofort widerrufen und ein neuer ausgegeben.

    Ein späterer Einlösungsversuch desselben Werts scheitert, statt ein zweites gültiges Sitzungspaar zu erzeugen."""
    # Vertrag für `refresh_with_candidates`: Hier gibt es keine Erkennung von Wiederverwendung mit Widerruf der ganzen
    # Kette, und ein ungültiger Token löst `InvalidCredentials` aus, bevor etwas geschrieben wurde. Wer das ändert, muss
    # `refresh_with_candidates` mitändern (sonst beendet ein veralteter Cookie-Wert die Sitzung im anderen).
    token_hash = security.hash_opaque_token(raw_refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    token_row = result.scalar_one_or_none()

    invalid = InvalidCredentials("Refresh-Token ungültig, widerrufen oder abgelaufen.")
    now = utcnow()
    if token_row is None or token_row.expires_at < now:
        raise invalid

    if token_row.revoked_at is not None:
        return await _grace_tokens(session, settings, token_row, now, invalid)

    user = await session.get(User, token_row.user_id)
    if user is None or not user.is_active:
        raise InvalidCredentials("Nutzer nicht mehr aktiv.")

    # Atomar einlösen: Nur wer die Zeile von „nicht widerrufen“ auf „widerrufen“ setzt, darf rotieren. Zwei
    # überlappende Aufrufe mit demselben Token erzeugen so nicht zwei gültige Nachfolger; der Verlierer fällt in die
    # Gnadenfrist (Rowcount 0, der Gewinner hat dann schon committet).
    claim = await session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == token_row.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, last_used_at=now)
        .execution_options(synchronize_session=False)
    )
    if (claim.rowcount or 0) == 0:
        await session.refresh(token_row)
        if token_row.revoked_at is None:
            raise invalid
        return await _grace_tokens(session, settings, token_row, now, invalid)

    tokens = await _issue_tokens(
        session,
        settings,
        user,
        client_type=token_row.client_type,
        device_name=token_row.device_name,
        user_agent=token_row.user_agent,
        ip=token_row.ip,
    )
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == token_row.id)
        .values(replaced_by_id=tokens.session_id)
        .execution_options(synchronize_session=False)
    )
    return tokens


async def refresh_with_candidates(
    session: AsyncSession, settings: Settings, *, raw_refresh_tokens: Sequence[str]
) -> IssuedTokens:
    """Wie `refresh_access_token`, aber mit mehreren möglichen Werten, nacheinander versucht; der erste Erfolg gilt.

    Scheitern alle, gilt der Fehler des ersten Versuchs. Gleiche Werte sollte der Aufrufer vorher entfernen."""
    # Gebraucht, solange der alte und der neue Cookie-Name im Browser nebeneinander stehen; nach einem Rollback aufs
    # alte Image kann der eine veraltet sein, der andere aktuell. Das schwächt die Erkennung von Wiederverwendung nicht:
    # Ein Fehlversuch ändert nichts in der Datenbank, und pro Aufruf wird nie mehr als ein Wert eingelöst.
    first_error: InvalidCredentials | None = None
    for raw in raw_refresh_tokens:
        try:
            return await refresh_access_token(session, settings, raw_refresh_token=raw)
        except InvalidCredentials as exc:
            if first_error is None:
                first_error = exc
    raise first_error or InvalidCredentials("Refresh-Token ungültig, widerrufen oder abgelaufen.")


async def _grace_tokens(
    session: AsyncSession,
    settings: Settings,
    token_row: RefreshToken,
    now: datetime,
    invalid: InvalidCredentials,
) -> IssuedTokens:
    """Access-Token der Nachfolger-Sitzung in der Gnadenfrist, ohne neuen Refresh-Token.

    So entsteht keine zweite Sitzungskette, die Laufzeit wächst nicht, und danach nützt der alte Token nichts."""
    successor = await _grace_successor(session, token_row, now)
    if successor is None:
        raise invalid
    user = await session.get(User, token_row.user_id)
    if user is None or not user.is_active:
        raise InvalidCredentials("Nutzer nicht mehr aktiv.")
    return IssuedTokens(
        access_token=_create_access_token(settings, user, successor.id),
        access_expires_in=settings.access_token_ttl_seconds,
        refresh_token=None,
        refresh_expires_at=None,
        user=user,
        client_type=token_row.client_type,
        session_id=successor.id,
    )


async def _grace_successor(
    session: AsyncSession, token_row: RefreshToken, now: datetime
) -> RefreshToken | None:
    """Nachfolger-Sitzung eines vor kurzem durch Rotation widerrufenen Tokens, sonst `None` (Gnadenfrist greift nicht).

    Das gilt bei Abmelden oder Passwortwechsel, abgelaufener Frist und widerrufenem oder abgelaufenem Nachfolger."""
    if token_row.replaced_by_id is None or token_row.revoked_at is None:
        return None
    if now - token_row.revoked_at > timedelta(seconds=REFRESH_GRACE_SECONDS):
        return None
    successor = await session.get(RefreshToken, token_row.replaced_by_id)
    if (
        successor is None
        or successor.user_id != token_row.user_id
        or successor.revoked_at is not None
        or successor.expires_at < now
    ):
        return None
    return successor


async def _live_session_head(session: AsyncSession, user_id: str, token_id: str) -> str:
    """Folgt der Rotationskette (`replaced_by_id`) von einer durch Erneuern widerrufenen Sitzung bis zur lebenden.

    Bleibt bei `token_id`, wenn die Zeile fehlt, einem anderen Nutzer gehört, noch gilt oder keinen Nachfolger hat."""
    # Bekannte Grenze: Die Kette sagt nicht, wer erneuert hat; der Nachfolger übernimmt Browser, Gerät und IP, und das
    # Refresh-Cookie (Pfad `/api/v1/auth`) kommt beim Passwortwechsel gar nicht an. Hat jemand mit einem abgegriffenen
    # Cookie die Kette weitergedreht, bleibt dessen Sitzung erhalten, solange der Wechsel noch mit dem alten
    # Zugangs-Token kommt (höchstens `access_token_ttl_seconds`); wer eine Übernahme vermutet, meldet sich vorher neu an
    # (die `sid` zeigt dann auf eine frische Sitzung, jede andere Kette endet).
    head = token_id
    for _ in range(20):  # Schutz vor Schleifen in kaputten Daten
        row = await session.get(RefreshToken, head)
        if (
            row is None
            or row.user_id != user_id
            or row.revoked_at is None
            or row.replaced_by_id is None
        ):
            break
        head = row.replaced_by_id
    return head


async def revoke_refresh_tokens(
    session: AsyncSession, user_id: str, *, except_token_id: str | None = None
) -> int:
    """Meldet alle Sitzungen eines Nutzers ab (sonst holten alte Refresh-Tokens nach einem Passwortwechsel noch 30 Tage
    neue Access-Tokens); `except_token_id` bleibt angemeldet. Gibt die Zahl der abgemeldeten Sitzungen zurück."""
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    if except_token_id is not None:
        # Hat ein anderer Tab inzwischen erneuert, zeigt die `sid` im Zugangs-Token auf eine schon widerrufene Zeile:
        # Dann gilt die Sitzung am Ende der Kette, sonst würde die tatsächlich aktuelle mit abgemeldet.
        # Grenze dieser Kulanz: siehe `_live_session_head`.
        except_token_id = await _live_session_head(session, user_id, except_token_id)
        stmt = stmt.where(RefreshToken.id != except_token_id)
        # Vorgänger der behaltenen Sitzung (durch Rotation widerrufen) sollen auch keine Gnadenfrist mehr bekommen,
        # sonst käme ein alter Token 30 s lang trotz Passwortwechsel noch an einen Access-Token.
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.replaced_by_id == except_token_id,
            )
            .values(replaced_by_id=None)
        )
    result = await session.execute(stmt)
    return result.rowcount or 0


async def logout(session: AsyncSession, *, raw_refresh_token: str) -> None:
    """Kein Fehler, wenn der Token schon widerrufen oder unbekannt ist: Abmelden gelingt aus Client-Sicht immer."""
    token_hash = security.hash_opaque_token(raw_refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    token_row = result.scalar_one_or_none()
    if token_row is not None and token_row.revoked_at is None:
        token_row.revoked_at = utcnow()
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=token_row.user_id,
            action="logout.succeeded",
            outcome="success",
            target_type="user",
            target_id=token_row.user_id,
        )


# ---------------------------------------------------------------------------
# TOTP: Setup / Confirm / Disable
# ---------------------------------------------------------------------------


async def start_totp_setup(
    session: AsyncSession, settings: Settings, user: User
) -> tuple[str, str]:
    """Legt ein neues Secret verschlüsselt (unbestätigt) am Nutzer ab; gibt `(secret_base32, otpauth_uri)` zurück.

    Der Base32-Wert ist nur jetzt im Klartext sichtbar, kein Endpunkt liefert ihn wieder aus."""
    if user.totp_secret_id is not None and user.totp_confirmed_at is not None:
        raise AuthError(
            "Zwei-Faktor ist schon aktiv. Schalte sie zuerst ab, dann kannst du sie neu einrichten."
        )

    keyring = vault.load_keyring(settings)
    raw_secret = pyotp.random_base32()

    secret_row = await vault.create_secret(
        session,
        keyring,
        label=f"totp:{user.id}:{security.generate_opaque_token()[:8]}",
        kind="totp",
        plaintext=raw_secret,
        created_by_user_id=user.id,
    )

    if user.totp_secret_id is not None:
        # Unbestätigtes Vorgänger-Secret aus einem abgebrochenen Einrichtungsversuch.
        await vault.delete_secret(session, user.totp_secret_id)

    user.totp_secret_id = secret_row.id
    user.totp_confirmed_at = None
    await session.flush()

    otpauth_uri = pyotp.TOTP(raw_secret).provisioning_uri(
        name=user.username, issuer_name="Nodvard Deck"
    )
    return raw_secret, otpauth_uri


async def confirm_totp_setup(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    code: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> list[str]:
    """Bestätigt die 2FA-Einrichtung und erzeugt dabei die ersten Wiederherstellungs-Codes (Klartext, einmalig).

    Falsche Codes werden wie falsche Passwörter je Nutzer gedrosselt."""
    # Nur für eine noch nicht bestätigte Einrichtung: Bei aktiver 2FA gäbe dieser Weg ohne Passwort einen frischen Satz
    # Codes aus (dafür gibt es `POST /me/recovery-codes` mit Passwort).
    if user.totp_secret_id is not None and user.totp_confirmed_at is not None:
        raise AuthError("Zwei-Faktor ist schon aktiv.")
    if user.totp_secret_id is None:
        raise AuthError(
            "Die Zwei-Faktor-Einrichtung wurde noch nicht gestartet."
        )

    attempt = _begin_attempt(_account_key(user.id), SENSITIVE_ATTEMPT_KEY)
    keyring = vault.load_keyring(settings)
    raw_secret = await vault.read_secret_plaintext(session, keyring, user.totp_secret_id)
    compact = "".join(code.split())
    totp_step = match_totp_step(raw_secret, compact) if security.is_totp_code(compact) else None
    if totp_step is None:
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=user.id,
            action="auth.totp_confirm_failed",
            outcome="failure",
            target_type="user",
            target_id=user.id,
            reason="Falscher Code beim Bestätigen der Zwei-Faktor-Einrichtung.",
            ip=ip,
            user_agent=user_agent,
        )
        await _record_failure(
            session, attempt, actor_id=user.id, user_agent=user_agent,
            sensitive_ip=ip, sensitive_user_id=user.id,
        )
        await _commit_before_raising(session)
        raise InvalidMfaCode("Ungültiger Code. Die Zwei-Faktor-Anmeldung ist noch nicht aktiv.")
    login_limit.succeeded(attempt)
    # Der Code, der die Einrichtung bestätigt, gilt danach nicht noch einmal: Sonst schaltete er die
    # Zwei-Faktor-Anmeldung gleich wieder ab.
    login_limit.remember_totp_step(user.id, totp_step)

    # Die Hashes der ersten Codes (auf einem Pi Sekunden) vor dem ersten Schreibzugriff rechnen: Ab dem flush hält die
    # Anfrage die Schreibsperre der Datenbank bis zu ihrem Ende.
    code_set = new_recovery_code_set()
    await code_set.hash()
    user.totp_confirmed_at = utcnow()
    await session.flush()
    return await issue_recovery_codes(session, user, code_set)


async def disable_totp(session: AsyncSession, user: User) -> None:
    if user.totp_secret_id is not None:
        await vault.delete_secret(session, user.totp_secret_id)
    user.totp_secret_id = None
    user.totp_confirmed_at = None
    # Ohne Zwei-Faktor sind die Wiederherstellungs-Codes sinnlos und dürfen bei einer späteren Neu-Einrichtung nicht
    # wieder gültig werden.
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await session.flush()


def two_factor_enabled(user: User) -> bool:
    return user.totp_secret_id is not None and user.totp_confirmed_at is not None


# ---------------------------------------------------------------------------
# Wiederherstellungs-Codes
# ---------------------------------------------------------------------------


WRONG_PASSWORD_MESSAGE = "Das aktuelle Passwort stimmt nicht."


class WrongPassword(AuthError):
    """Das zur Bestätigung eingegebene aktuelle Passwort stimmt nicht (API: 400)."""


async def require_current_password(
    session: AsyncSession,
    user: User,
    password: str,
    *,
    action: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Bestätigt eine empfindliche Konto-Aktion (`action`, z. B. `2fa_abschalten`) mit dem aktuellen Passwort.

    Falsche Eingaben werden gedrosselt (wie beim Login, dann 429) und als `auth.password_check_failed` protokolliert."""
    # Ein Zähler je Nutzer für alle empfindlichen Aktionen, nicht je IP: Sonst ließe sich die Sperre mit wechselnden
    # Adressen umgehen, und ein Angreifer mit gestohlener Sitzung käme trotz Drosselung weiter.
    attempt = _begin_attempt(_account_key(user.id), SENSITIVE_ATTEMPT_KEY)
    if not await _verify_password(attempt, password, user.password_hash, signed_in=True):
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=user.id,
            action="auth.password_check_failed",
            outcome="failure",
            target_type="user",
            target_id=user.id,
            reason="Aktuelles Passwort falsch eingegeben.",
            detail={"aktion": action},
            ip=ip,
            user_agent=user_agent,
        )
        await _record_failure(
            session, attempt, actor_id=user.id, user_agent=user_agent,
            sensitive_ip=ip, sensitive_user_id=user.id,
        )
        await _commit_before_raising(session)
        raise WrongPassword(WRONG_PASSWORD_MESSAGE)
    login_limit.succeeded(attempt)


TOTP_REQUIRED_MESSAGE = "Bitte gib zusätzlich den aktuellen Zwei-Faktor-Code aus deiner App ein."
TOTP_OR_RECOVERY_REQUIRED_MESSAGE = (
    "Bitte gib zusätzlich den aktuellen Zwei-Faktor-Code aus deiner App oder einen Wiederherstellungs-Code ein."
)
PASSWORD_CHANGE_TOTP_REQUIRED_MESSAGE = (
    "Zum Ändern des Passworts brauchst du jetzt deinen Zwei-Faktor-Code. "
    "Nutzt du die Handy-App und siehst kein Feld dafür, aktualisiere zuerst die App."
)
"""Antwort auf `POST /me/password` ohne Code. Eine ältere App zeigt nur dieses `detail`, der Satz muss für sich
verständlich sein und sagen, was zu tun ist."""
_TOTP_REQUIRED_MESSAGE_BY_ACTION = {"passwort_aendern": PASSWORD_CHANGE_TOTP_REQUIRED_MESSAGE}
"""Eigener Satz je Vorgang (`action` von `require_current_totp`); alle anderen nehmen den allgemeinen."""
WRONG_TOTP_MESSAGE = "Der Zwei-Faktor-Code stimmt nicht."
WRONG_RECOVERY_MESSAGE = (
    "Der Code stimmt nicht. Gib den sechsstelligen Code aus deiner App oder einen noch unbenutzten "
    "Wiederherstellungs-Code ein."
)
RECOVERY_STILL_WORKS_HINT = "Mit einem noch unbenutzten Wiederherstellungs-Code geht es auch jetzt."

VIA_TOTP = "totp"
VIA_RECOVERY = "recovery_code"


class TotpRequired(AuthError):
    """Zwei-Faktor ist an, aber es kam kein Code mit (API: 403)."""


class WrongTotp(AuthError):
    """Der Zwei-Faktor-Code zur Bestätigung stimmt nicht oder wurde schon benutzt (API: 400). `code` ist `totp_used` für
    einen schon benutzten Code aus der App, sonst `totp_wrong`, auch für einen verbrauchten Wiederherstellungs-Code
    (den kennt die Datenbank nicht mehr)."""

    def __init__(self, message: str, *, used: bool = False) -> None:
        super().__init__(message)
        self.code = "totp_used" if used else "totp_wrong"


async def require_current_totp(
    session: AsyncSession,
    settings: Settings,
    user: User,
    code: str,
    *,
    action: str,
    ip: str | None = None,
    user_agent: str | None = None,
    allow_recovery: bool = False,
    before_claim: Callable[[], Awaitable[object]] | None = None,
) -> str | None:
    """Verlangt bei eingeschaltetem Zwei-Faktor für eine empfindliche Aktion (`action`) den aktuellen Code aus der App.

    Gibt `None` zurück, wenn Zwei-Faktor aus ist, sonst womit bestätigt wurde (`VIA_TOTP` bzw. `VIA_RECOVERY`)."""
    # Dieselben Bausteine wie beim Anmelden (`verify_mfa`): Ein Code gilt je Konto nur einmal, auch quer zwischen
    # Anmeldung und Bestätigung. Gedrosselt wird nur über die IP-unabhängige Grenze je Konto, die sich beide teilen; der
    # Zähler der Passwortabfrage taugt nicht dafür, das richtige Passwort davor setzt ihn zurück.
    if not two_factor_enabled(user):
        return None
    compact = "".join((code or "").split())
    if not compact:
        raise TotpRequired(
            _TOTP_REQUIRED_MESSAGE_BY_ACTION.get(action)
            or (TOTP_OR_RECOVERY_REQUIRED_MESSAGE if allow_recovery else TOTP_REQUIRED_MESSAGE)
        )
    # Wiederherstellungs-Codes nur mit `allow_recovery` (Passwort ändern, Zwei-Faktor abschalten, neue Codes erzeugen:
    # Wer das Handy verloren hat, soll das mit einem Code aus seiner Liste können); sonst gilt alles außer sechs Ziffern
    # als falscher Code, denn die Codes sind für den Notfall beim Anmelden da.
    via_recovery = allow_recovery and not security.is_totp_code(compact)
    # Ein Wiederherstellungs-Code zählt nicht auf die Grenze je Konto (sonst käme der Besitzer während einer von
    # jemandem mit seinem Passwort ausgelösten Sperre auch damit nicht weiter); falsche zählen auf einen eigenen Zähler
    # (`RECOVERY_ATTEMPT_KEY`, je Konto, vom Passwort nicht zurückgesetzt) und auf den aller Sicherheitsabfragen des
    # Kontos (`konto:<id>`).
    try:
        attempt = _begin_attempt(
            _account_key(user.id),
            RECOVERY_ATTEMPT_KEY if via_recovery else SENSITIVE_ATTEMPT_KEY,
            account=None if via_recovery else user.id,
        )
    except TooManyAttempts as exc:
        if not allow_recovery or via_recovery:
            raise
        # Die Passwortabfrage davor ging durch: Gesperrt hat die Grenze je Konto für Codes aus der App. Für
        # Wiederherstellungs-Codes gilt sie nicht (wie beim Anmelden), das soll der Besitzer erfahren.
        raise TooManyAttempts(f"{exc} {RECOVERY_STILL_WORKS_HINT}", retry_after=exc.retry_after) from None
    totp_step: int | None = None
    if via_recovery:
        try:
            valid = await consume_recovery_code(session, user, compact, before_claim=before_claim)
        except security.HashingBusy:
            login_limit.release(attempt)
            raise
    else:
        if security.is_totp_code(compact):
            keyring = vault.load_keyring(settings)
            totp_secret = await vault.read_secret_plaintext(session, keyring, user.totp_secret_id)
            totp_step = match_totp_step(totp_secret, compact)
        valid = totp_step is not None
    replayed = valid and not login_limit.claim_success(attempt, totp_step=totp_step)
    if replayed:
        valid = False
    if not valid:
        # Der Code selbst kommt nie ins Protokoll.
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=user.id,
            action="auth.totp_check_failed",
            outcome="failure",
            target_type="user",
            target_id=user.id,
            reason=(
                "Wiederherstellungs-Code zur Bestätigung falsch eingegeben."
                if via_recovery
                else "Zwei-Faktor-Code zur Bestätigung falsch eingegeben."
            ),
            detail={"aktion": action}
            | ({"via": VIA_RECOVERY} if via_recovery else {})
            | ({"replayed": True} if replayed else {}),
            ip=ip,
            user_agent=user_agent,
        )
        scopes = await _record_failure(
            session, attempt, actor_id=user.id, user_agent=user_agent,
            sensitive_ip=ip, sensitive_user_id=user.id,
        )
        if login_limit.SCOPE_ACCOUNT in scopes:
            await _report_mfa_guessing(session, user)
        await _commit_before_raising(session)
        if via_recovery:
            raise WrongTotp(WRONG_RECOVERY_MESSAGE)
        raise WrongTotp(TOTP_CODE_USED_MESSAGE if replayed else WRONG_TOTP_MESSAGE, used=replayed)
    login_limit.succeeded(attempt)
    return VIA_RECOVERY if via_recovery else VIA_TOTP


@dataclass
class RecoveryCodeSet:
    """Ein neuer, noch nicht gespeicherter Satz Wiederherstellungs-Codes (`new_recovery_code_set`).

    `hash()` rechnet die Hashes (Argon2, auf einem Pi Sekunden), `issue_recovery_codes` speichert sie."""

    # So lässt sich das Rechnen vor einen Schreibzugriff ziehen: Wer neue Codes mit einem Wiederherstellungs-Code
    # bestätigt, hält ab dessen Verbrauch die Schreibsperre der Datenbank (`before_claim` von `require_current_totp`),
    # beim Einschalten von Zwei-Faktor ab dem Schreiben der Bestätigung (`confirm_totp_setup`).
    codes: list[str]
    hashes: list[str] | None = None

    async def hash(self) -> list[str]:
        if self.hashes is None:
            codes = list(self.codes)
            # Nicht im Event-Loop rechnen, sonst steht währenddessen das ganze Dashboard.
            self.hashes = await security.run_hashing(
                lambda: [security.hash_password(security.normalize_recovery_code(c)) for c in codes],
                signed_in=True,
            )
        return self.hashes


def new_recovery_code_set() -> RecoveryCodeSet:
    codes: list[str] = []
    while len(codes) < RECOVERY_CODE_COUNT:
        candidate = security.generate_recovery_code()
        if candidate not in codes:
            codes.append(candidate)
    return RecoveryCodeSet(codes=codes)


async def issue_recovery_codes(
    session: AsyncSession, user: User, code_set: RecoveryCodeSet | None = None
) -> list[str]:
    """Speichert einen neuen Satz (`RECOVERY_CODE_COUNT` Codes) und ersetzt alle bisherigen, benutzte wie unbenutzte.

    Gibt die Klartext-Codes zurück: Das ist das einzige Mal, dass sie existieren."""
    if code_set is None:
        code_set = new_recovery_code_set()
    hashes = await code_set.hash()
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    for code_hash in hashes:
        session.add(RecoveryCode(user_id=user.id, code_hash=code_hash))
    await session.flush()
    return code_set.codes


async def recovery_codes_remaining(session: AsyncSession, user_id: str) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(RecoveryCode)
        .where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
    )
    return int(count or 0)


async def consume_recovery_code(
    session: AsyncSession,
    user: User,
    code: str,
    *,
    before_claim: Callable[[], Awaitable[object]] | None = None,
) -> bool:
    """Löst einen Wiederherstellungs-Code ein: `True`, wenn er zu einem unbenutzten Code des Nutzers passt.

    Achtung: Schreibt `session` vor der langen Prüfung fest (`commit()`); Ungespeichertes wird mitgespeichert."""
    normalized = security.normalize_recovery_code(code)
    if not normalized:
        return False
    rows = (
        await session.execute(
            select(RecoveryCode.id, RecoveryCode.code_hash).where(
                RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)
            )
        )
    ).all()
    if not rows:
        return False
    checked_hash = user.password_hash
    # Die Prüfung aller Codes wartet und rechnet (auf einem Pi Sekunden): die Datenbankverbindung nicht so lange
    # festhalten (siehe `login`).
    await session.commit()

    def _find() -> str | None:
        for row_id, code_hash in rows:
            if security.verify_password(normalized, code_hash):
                return row_id
        return None

    matched_id = await security.run_hashing(_find)
    if matched_id is None:
        return False
    # Das Markieren hält die Schreibsperre der Datenbank, bis der Aufrufer seine Anfrage festschreibt (scheitert sie,
    # ist der Code wieder frei). Lange Arbeit gehört darum davor: `before_claim` läuft bei passendem Code unmittelbar
    # vorher (z. B. die Hashes neuer Codes, `RecoveryCodeSet.hash`).
    if before_claim is not None:
        await before_claim()
    # Wurde das Konto in der Zwischenzeit deaktiviert, das Passwort geändert oder Zwei-Faktor ausgeschaltet, gilt der
    # Code nicht mehr und wird nicht verbraucht.
    if await _reload_unchanged(session, user.id, password_hash=checked_hash, two_factor=True) is None:
        return False
    # Atomar markieren: Nur wer die Zeile von „unbenutzt“ auf „benutzt“ setzt, gewinnt; zwei gleichzeitige Anmeldungen
    # mit demselben Code kommen nicht beide durch.
    claim = await session.execute(
        update(RecoveryCode)
        .where(RecoveryCode.id == matched_id, RecoveryCode.used_at.is_(None))
        .values(used_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    return (claim.rowcount or 0) == 1
