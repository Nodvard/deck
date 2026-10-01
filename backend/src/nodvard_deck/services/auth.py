"""Login, Token-Ausgabe/-Rotation, TOTP-Setup/-Bestaetigung, Owner-Bootstrap.

Verdrahtet die reinen Bausteine aus `core.security`/`core.vault`/`core.rbac` mit der
Datenbank. Endpunkte in `api/v1/auth.py` und `api/v1/me.py` rufen nur noch diese
Funktionen auf und uebersetzen deren Ergebnis/Exceptions in HTTP.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyotp
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import login_limit, security, setup_code, vault
from ..core.rbac import BUILTIN_ROLES, has_permission
from ..db import new_id, refresh_relationships, utcnow
from ..models import RecoveryCode, RefreshToken, Role, RolePermission, User
from . import audit as audit_service

_log = logging.getLogger("nodvard_deck.auth")

RECOVERY_CODE_COUNT = 10
"""So viele Wiederherstellungs-Codes bekommt man je Satz."""

# Konstanter Dummy-Hash fuer die Timing-Angleichung bei unbekanntem Benutzernamen
# (siehe login() unten) -- ein echter Argon2id-Hash eines beliebigen Werts, gegen den
# im "Nutzer existiert nicht"-Fall trotzdem verifiziert wird, damit die Antwortzeit
# nicht verraet, ob der Benutzername existiert.
_DUMMY_PASSWORD_HASH = security.hash_password(security.generate_opaque_token())


class AuthError(Exception):
    """Basis fuer alle Auth-Fehler, die als 4xx an die API-Schicht durchgereicht werden."""


class InvalidCredentials(AuthError):
    pass


class MfaRequired(AuthError):
    def __init__(self, mfa_token: str) -> None:
        super().__init__("2FA-Code erforderlich")
        self.mfa_token = mfa_token


class InvalidMfaCode(AuthError):
    pass


class TooManyAttempts(AuthError):
    """Zu viele Fehlversuche -- die API antwortet mit 429. `retry_after` in
    Sekunden (fuer den `Retry-After`-Header); `None`, wenn Warten nicht hilft (das
    `mfa_token` ist verbraucht, man muss sich neu anmelden)."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


REFRESH_GRACE_SECONDS = 30
"""Gnadenfrist, in der ein gerade durch Rotation widerrufener Refresh-Token noch einen
frischen Access-Token bekommt (zwei Tabs, die fast gleichzeitig aktualisieren)."""

SENSITIVE_ATTEMPT_KEY = "passwortabfrage"
"""Pseudo-Benutzername fuer die Drosselung falscher Passwoerter bei empfindlichen Aktionen."""

SETUP_ATTEMPT_KEY = "einrichtung"
"""Pseudo-Benutzername fuer die Drosselung falscher Einrichtungscodes (je IP)."""

MFA_EXHAUSTED_MESSAGE = "Zu viele falsche 2FA-Codes. Bitte melde dich erneut an."


def _begin_attempt(
    ip: str | None, username: str, *, mfa_token: str | None = None
) -> login_limit.Attempt:
    """Prueft die Drosselung VOR jeder Passwort-/Code-Pruefung (kein Argon2-Aufwand
    fuer gesperrte Versuche) und zaehlt den Versuch sofort mit."""
    try:
        return login_limit.begin(ip, username, mfa_token=mfa_token)
    except login_limit.MfaExhausted:
        raise TooManyAttempts(MFA_EXHAUSTED_MESSAGE) from None
    except login_limit.Locked as exc:
        raise TooManyAttempts(str(exc), retry_after=exc.retry_after) from None


async def _record_failure(
    session: AsyncSession,
    attempt: login_limit.Attempt,
    *,
    actor_id: str,
    user_agent: str | None,
    sensitive_ip: str | None = None,
    sensitive_user_id: str | None = None,
) -> None:
    """Fehlversuch verbuchen; beginnt dadurch gerade eine Sperre, genau EIN
    `login.locked`-Audit-Eintrag (committet wird danach wie beim `login.failed`).

    Mit `sensitive_user_id` (Passwortabfrage bei empfindlichen Konto-Aktionen, siehe
    `require_current_password`) heisst der Eintrag stattdessen `auth.password_check_locked`,
    mit passendem Text und der echten Client-IP `sensitive_ip` -- die Drosselung selbst
    haengt dort an der Nutzerkennung, nicht an einer IP/einem Benutzernamen."""
    scopes = login_limit.failed(attempt)
    if not scopes:
        return
    if sensitive_user_id is not None:
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=actor_id,
            action="auth.password_check_locked",
            outcome="failure",
            target_type="user",
            target_id=sensitive_user_id,
            reason=(
                "Sicherheitsabfragen (Passwort, Zwei-Faktor-Code) vorübergehend gesperrt: "
                f"{login_limit.MAX_FAILURES_PER_USER} Fehlversuche in {login_limit.WINDOW_SECONDS // 60} Minuten."
            ),
            detail={"window_seconds": login_limit.WINDOW_SECONDS},
            ip=sensitive_ip,
            user_agent=user_agent,
        )
        return
    minutes = login_limit.WINDOW_SECONDS // 60
    reasons = {
        login_limit.SCOPE_USER: (
            f"{login_limit.MAX_FAILURES_PER_USER} Fehlversuche für diesen Benutzernamen "
            f"in {minutes} Minuten"
        ),
        login_limit.SCOPE_IP: (
            f"{login_limit.MAX_FAILURES_PER_IP} Fehlversuche von dieser IP in {minutes} Minuten"
        ),
    }
    await audit_service.log(
        session,
        actor_type="user",
        actor_id=actor_id,
        action="login.locked",
        outcome="failure",
        reason="Anmeldung vorübergehend gesperrt: " + "; ".join(reasons[s] for s in scopes) + ".",
        detail={
            "scopes": scopes,
            "username": attempt.username,
            "window_seconds": login_limit.WINDOW_SECONDS,
        },
        ip=attempt.ip,
        user_agent=user_agent,
    )


class UsersAlreadyExist(AuthError):
    pass


class InvalidSetupCode(AuthError):
    """Einrichtungscode fehlt oder stimmt nicht -- die API antwortet mit 403."""


async def _commit_before_raising(session: AsyncSession) -> None:
    """**Live gefunden beim Schreiben der ersten Audit-Tests, nicht beim Lesen:** jede
    hier ausgeloeste `AuthError` wird in `api/v1/auth.py` in eine `HTTPException`
    uebersetzt und propagiert damit durch `db.session.get_session()` nach oben.
    `session_scope()` behandelt JEDE durchgereichte Exception -- auch eine erwartete
    401er -- als Grund fuer `session.rollback()` (docs/00 D-02: eine Session pro
    Request, ein gemeinsamer Commit am Ende). Ohne dieses explizite Zwischen-Commit
    waere der gerade geschriebene `login.failed`/`mfa.failed`-Audit-Eintrag NIE in der
    Datenbank angekommen -- der Retention-Job haette nichts zu loeschen gehabt, weil
    nie etwas committet wurde. Sicher, weil an dieser Stelle in `login()`/`verify_mfa()`
    ausser dem Audit-Eintrag (und ggf. einer bereits gewollten Lazy-Key-Migration aus
    `vault.read_secret_plaintext`) nichts anderes im Transaktionspuffer steht."""
    await session.commit()


@dataclass
class IssuedTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str | None
    """Klartext, EINMAL sichtbar -- die API-Schicht entscheidet, ob das in einen
    HttpOnly-Cookie (Web) oder in den JSON-Body (Android/CLI) wandert. `None` nur in
    der Gnadenfrist von `refresh_access_token()`: dort gibt es bewusst keinen neuen
    Refresh-Token, der Client behaelt den, den der erste Aufruf schon geliefert hat."""
    refresh_expires_at: datetime | None
    user: User
    client_type: str
    """Vom Aufrufer (Login/MFA/Refresh) durchgereicht, damit die API-Schicht die
    Cookie-vs-Body-Entscheidung aus EINER Quelle trifft, statt sie aus JWT-Claims oder
    der Anwesenheit eines Body-Felds zurueckzuraten."""
    session_id: str | None = None
    """Kennung der neu angelegten Refresh-Token-Zeile (= `sid` im Access-Token)."""


# ---------------------------------------------------------------------------
# Rollen-Seeding
# ---------------------------------------------------------------------------


async def ensure_builtin_roles(session: AsyncSession) -> dict[str, Role]:
    """Legt admin/operator/viewer an, falls sie fehlen. Idempotent -- sicher bei jedem
    Start aufzurufen (main.py Lifespan). `owner` ist bewusst KEINE Rolle hier: die
    Owner-Eigenschaft ist `User.is_owner` und umgeht RBAC vollstaendig
    (docs/03-DATA-MODEL.md §1).

    **Live gefunden, nicht beim Lesen -- drei Anlaeufe, bis die Regel klar war:**
    Ein `relationship()`-Attribut auf einem bereits GEFLUSHTEN (persistenten) Objekt ist
    in dieser Async-SQLAlchemy-Konfiguration WEDER lesbar NOCH per `.append()`
    beschreibbar, ohne vorher explizit geladen worden zu sein -- selbst wenn das Objekt
    in DERSELBEN Funktion gerade erst neu angelegt wurde und die Collection logisch leer
    sein MUSS. `lazy="selectin"` eager-laedt nur, wenn das Objekt ueber eine awaitete
    Query kam; jeder andere Zugriffsversuch (Lesen *und* Anhaengen) loest einen
    synchronen Fallback-Lazy-Load aus, der im Async-Kontext mit `MissingGreenlet`
    crasht. Der einzige empirisch bestaetigte sichere Weg: Kind-Zeilen als eigenstaendige
    Objekte per `session.add()` anlegen (NIE ueber `role.permissions` selbst), danach
    `db.refresh_relationships(session, role, "permissions")` -- eine explizit awaitete
    Operation, die die Collection korrekt und sicher (nach)laedt, bevor ein Aufrufer
    (z. B. `user_permissions()`) sie spaeter synchron liest. Diese Regel ist seit ihrem
    dritten Fund (WP-3, `HostsHandle.upsert_discovered`) als docs/00-DECISIONS.md D-12
    festgehalten -- dort auch die Begruendung, warum kein automatischer Check dafuer
    existiert.
    """
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
    """`is_owner` liefert den Wildcard-Scope direkt -- dieselbe Semantik, die
    `core.rbac.has_permission` fuer `"*"` ohnehin schon versteht."""
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
# Owner-Bootstrap -- Erstinbetriebnahme
# ---------------------------------------------------------------------------


async def needs_bootstrap(session: AsyncSession) -> bool:
    """Dieselbe Existenz-Pruefung wie `bootstrap_owner()`, aber lesend und ohne
    Seiteneffekt -- WP-13: der Erstinbetriebnahme-Assistent im Web-Frontend muss VOR
    jedem Login-Versuch wissen, ob er sich selbst (Konto anlegen) oder die normale
    Login-Seite zeigen soll, ohne dafuer `POST /auth/bootstrap` blind zu versuchen."""
    any_user = (await session.execute(select(User.id).limit(1))).first()
    return any_user is None


def normalize_username(username: str) -> str:
    """Benutzernamen werden immer klein und ohne Leerraum gespeichert und verglichen."""
    return username.strip().lower()


SETUP_CODE_HINT = "Der Einrichtungscode steht im Protokoll des Containers (beim Start ausgegeben)."


async def prepare_setup_code(session: AsyncSession, settings: Settings) -> str | None:
    """Beim Start aufrufen: Gibt es noch keinen Nutzer, wird der Einrichtungscode bereit-
    gestellt (neu erzeugt oder der vorhandene) und GROSS ins Protokoll geschrieben --
    bei jedem Start, bis die Einrichtung fertig ist. Gibt es schon einen Nutzer, wird eine
    uebrig gebliebene Code-Datei geloescht. Gibt den Code zurueck (`None` = nichts zu tun).
    Bestehende Installationen mit Owner merken davon nichts."""
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
    # print statt logging: der Code muss sichtbar sein, auch wenn niemand Logging
    # konfiguriert hat (uvicorn tut das nur fuer seine eigenen Logger).
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
    """Prueft den Einrichtungscode, gedrosselt wie der Login (je IP). Wirft
    `InvalidSetupCode` bzw. `TooManyAttempts`. Fehlversuche stehen im Audit-Protokoll.

    Gibt es (z. B. nach einem abgebrochenen Start) noch keinen Code, wird hier einer
    angelegt und ins Protokoll geschrieben -- die Anfrage selbst scheitert dann, denn der
    Anrufer kennt ihn ja nicht."""
    attempt = _begin_attempt(ip, SETUP_ATTEMPT_KEY)
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
    """Legt den ersten Nutzer als Owner an. Nur erlaubt, wenn noch KEIN Nutzer
    existiert -- der aufrufende Endpunkt ist deshalb ohne Authentifizierung erreichbar
    (api/v1/auth.py), aber selbstlimitierend: jeder weitere Aufruf schlaegt fehl."""
    if not await needs_bootstrap(session):
        raise UsersAlreadyExist("Es existiert bereits mindestens ein Nutzer.")

    await ensure_builtin_roles(session)

    user = User(
        username=normalize_username(username),
        password_hash=security.hash_password(password),
        is_owner=True,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------------------
# Token-Ausgabe (gemeinsamer Kern fuer Login/MFA/Refresh)
# ---------------------------------------------------------------------------


def _create_access_token(settings: Settings, user: User, session_id: str) -> str:
    """Access-Token fuer die Anmeldung `session_id` (Refresh-Token-Zeile, siehe `sid`)."""
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
    # `sid` im Access-Token = Kennung der Anmeldung (Refresh-Token-Zeile), zu der es
    # gehoert. Damit weiss z. B. der Passwortwechsel, welche Sitzung die aktuelle
    # ist und angemeldet bleiben darf -- der Refresh-Cookie selbst kommt
    # dort nicht an (Cookie-Pfad /api/v1/auth), und die App hat gar keinen.
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
    # Benutzernamen sind immer klein; aeltere Konten mit Grossbuchstaben finden wir
    # trotzdem, damit niemand ausgesperrt wird.
    username = normalize_username(username)
    attempt = _begin_attempt(ip, username)
    result = await session.execute(select(User).where(func.lower(User.username) == username))
    user = result.scalar_one_or_none()

    if user is None:
        # Timing-Angleichung: eine Argon2-Verifikation kostet spuerbar Zeit. Ohne
        # diesen Umweg waere "Nutzer existiert nicht" (sofortige Antwort) von
        # "Nutzer existiert, Passwort falsch" (Argon2-Laufzeit) unterscheidbar --
        # ein klassisches Username-Enumeration-Leck ueber die Antwortzeit.
        security.verify_password(password, _DUMMY_PASSWORD_HASH)
        # actor_id = der versuchte Benutzername, nicht "unknown": docs/03 §4 nennt
        # `login.failed` als Beispiel-Aktion, ohne eine unbekannte Identitaet
        # vorzusehen -- Gap-Fill-Entscheidung analog zu `client_type` aus WP-1.
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=username,
            action="login.failed",
            outcome="failure",
            reason="Unbekannter Benutzername.",
            ip=ip,
            user_agent=user_agent,
        )
        await _record_failure(session, attempt, actor_id=username, user_agent=user_agent)
        await _commit_before_raising(session)
        raise InvalidCredentials("Ungültiger Benutzername oder Passwort.")

    if not user.is_active or not security.verify_password(password, user.password_hash):
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=user.id,
            action="login.failed",
            outcome="failure",
            reason="Konto deaktiviert." if not user.is_active else "Falsches Passwort.",
            target_type="user",
            target_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )
        await _record_failure(session, attempt, actor_id=user.id, user_agent=user_agent)
        await _commit_before_raising(session)
        raise InvalidCredentials("Ungültiger Benutzername oder Passwort.")

    if security.needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)

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
        # Passwort stimmt: dieser Versuch zaehlt nicht, die alten Fehlversuche bleiben
        # aber stehen, bis auch der zweite Faktor stimmt.
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
    """`ip` ist die IP DIESER Anfrage (fuer die Drosselung); die Audit-Eintraege nennen
    weiter die IP aus dem Login-Schritt, die im `mfa_token` steht."""
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
        raise InvalidMfaCode("2FA nicht aktiv für diesen Nutzer.")

    attempt = _begin_attempt(
        ip or payload.get("ip"), normalize_username(user.username), mfa_token=mfa_token
    )
    # Sechs Ziffern = Authenticator-Code, alles andere = Wiederherstellungs-Code.
    via_recovery = not security.is_totp_code(code)
    if via_recovery:
        valid = await consume_recovery_code(session, user, code)
    else:
        keyring = vault.load_keyring(settings)
        totp_secret = await vault.read_secret_plaintext(session, keyring, user.totp_secret_id)
        valid = pyotp.TOTP(totp_secret).verify("".join(code.split()), valid_window=1)
    if not valid:
        await audit_service.log(
            session,
            actor_type="user",
            actor_id=user.id,
            action="mfa.failed",
            outcome="failure",
            target_type="user",
            target_id=user.id,
            detail={"via": "recovery_code"} if via_recovery else {},
            ip=payload.get("ip"),
            user_agent=payload.get("user_agent"),
        )
        await _record_failure(
            session, attempt, actor_id=user.id, user_agent=payload.get("user_agent")
        )
        await _commit_before_raising(session)
        if login_limit.mfa_exhausted(attempt):
            raise TooManyAttempts(MFA_EXHAUSTED_MESSAGE)
        raise InvalidMfaCode(
            "Ungültiger Wiederherstellungs-Code." if via_recovery else "Ungültiger 2FA-Code."
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
        await _report_recovery_use(session, user, ip=payload.get("ip"), user_agent=payload.get("user_agent"))
    return tokens


async def _report_recovery_use(
    session: AsyncSession, user: User, *, ip: str | None, user_agent: str | None
) -> None:
    """Ein Wiederherstellungs-Code ersetzt den zweiten Faktor -- das soll nie unbemerkt
    bleiben: eigener Audit-Eintrag und eine Meldung (Meldungen-Seite, bei eingerichtetem
    Push-Kanal auch aufs Handy). Die Meldung darf die Anmeldung nie kippen."""
    remaining = await recovery_codes_remaining(session, user.id)
    await audit_service.log(
        session,
        actor_type="user",
        actor_id=user.id,
        action="mfa.recovery_used",
        outcome="success",
        target_type="user",
        target_id=user.id,
        detail={"remaining": remaining},
        ip=ip,
        user_agent=user_agent,
    )
    try:
        from . import notifications as notifications_service

        rest = (
            f"Es sind noch {remaining} Codes übrig."
            if remaining
            else "Es sind keine Codes mehr übrig – bitte unter Mein Konto neue erzeugen."
        )
        await notifications_service.send(
            session,
            title="Wiederherstellungs-Code benutzt",
            body=(
                f"Für „{user.username}“ wurde bei der Anmeldung ein Wiederherstellungs-Code "
                f"statt des Authenticator-Codes verwendet. {rest} "
                "Warst du das nicht, ändere sofort das Passwort und erzeuge neue Codes."
            ),
            severity="warning",
            payload={"path": "/settings/account", "tags": ["warning", "key"]},
        )
    except Exception:  # noqa: BLE001 - die Anmeldung selbst ist laengst gelungen
        _log.warning("Meldung zum Wiederherstellungs-Code nicht zugestellt", exc_info=True)


async def refresh_access_token(
    session: AsyncSession, settings: Settings, *, raw_refresh_token: str
) -> IssuedTokens:
    """Rotation: der eingeloeste Refresh-Token wird sofort widerrufen, ein neuer
    ausgegeben. Ein spaeterer Einloesungsversuch desselben (z. B. gestohlenen und
    replizierten) Werts findet dadurch `revoked_at IS NOT NULL` und schlaegt fehl,
    statt still ein zweites gueltiges Sitzungspaar zu erzeugen.

    Gnadenfrist: Aktualisieren zwei Tabs (oder Tab und App) fast gleichzeitig, kommt
    der zweite Aufruf mit dem gerade rotierten Token an und wuerde abgemeldet. Deshalb
    wird ein durch ROTATION widerrufener Token (`replaced_by_id` gesetzt) fuer
    `REFRESH_GRACE_SECONDS` noch akzeptiert -- aber nur mit einem frischen
    Access-Token der Nachfolger-Sitzung, OHNE neuen Refresh-Token. So entsteht keine
    zweite Sitzungskette, die Laufzeit der Sitzung wird nicht verlaengert, und ein
    abgegriffener alter Token bringt nach der Frist nichts mehr. Durch Abmelden oder
    Passwortwechsel widerrufene Token haben kein `replaced_by_id` und bleiben fuer
    immer ungueltig; ebenso, wenn der Nachfolger inzwischen selbst widerrufen wurde.

    **Vertrag, auf den `refresh_with_candidates` baut:** Es gibt hier KEINE Erkennung von
    Wiederverwendung mit Widerruf der ganzen Kette. Ein unbekannter, abgelaufener oder
    veralteter Token (und der eines inaktiven Nutzers) loest `InvalidCredentials` aus, BEVOR
    irgendetwas geschrieben wurde. Wer das aendert (z. B. die Kette bei Wiederverwendung
    widerrufen), muss `refresh_with_candidates` mit aendern: dort wuerde sonst ein
    veralteter Wert in einem der beiden Cookies die gueltige Sitzung im anderen beenden."""
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

    # Atomar einloesen: nur wer die Zeile wirklich von "nicht widerrufen" auf
    # "widerrufen" setzt, darf rotieren. Zwei ueberlappende Aufrufe mit demselben
    # Token erzeugen so nicht mehr zwei gueltige Nachfolger -- der Verlierer faellt
    # in die Gnadenfrist (Rowcount 0, der Gewinner hat dann schon committet).
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
    """Wie `refresh_access_token`, aber mit mehreren moeglichen Werten, nacheinander versucht;
    der erste Erfolg gilt (auch eine Gnadenfrist-Antwort ohne neuen Refresh-Token).

    Gebraucht fuer die Uebergangszeit der Cookie-Umbenennung (`nodvard_deck_refresh` und
    `lattice_refresh` stehen im Browser nebeneinander; nach einem Rollback aufs alte Image kann
    der eine veraltet sein, der andere aktuell).

    **Warum das die Erkennung von Wiederverwendung nicht schwaecht:** ein fehlgeschlagener
    Versuch aendert nichts in der Datenbank (siehe Vertrag in `refresh_access_token`), und es
    wird nie mehr eingeloest als ein Wert pro Aufruf. Die Reihenfolge entscheidet nur, welcher
    Wert gewinnt. Wer einen alten Token vorlegt, bekommt dasselbe wie vorher (401 nach der
    Gnadenfrist, in der Frist nur einen Access-Token), egal in welchem Cookie er ankommt, und
    zwei Werte zu probieren verschafft einem Angreifer nichts: jeder Wert wird einzeln wie
    bisher geprueft. Gleiche Werte sollten vorher entfernt sein (der Aufrufer macht das).

    Scheitern alle, gilt der Fehler des ERSTEN Versuchs (bei nur einem Wert exakt wie vorher)."""
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
    """Access-Token der Nachfolger-Sitzung in der Gnadenfrist (ohne Refresh-Token)."""
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
    """Nachfolger-Sitzung eines vor kurzem durch Rotation widerrufenen Tokens -- oder
    `None`, wenn die Gnadenfrist nicht greift (Token per Abmelden/Passwortwechsel
    widerrufen, Frist abgelaufen, Nachfolger widerrufen oder abgelaufen)."""
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
    """Folgt der Rotationskette (`replaced_by_id`) von einer durch Erneuern widerrufenen
    Sitzung bis zur lebenden. Bleibt bei `token_id`, wenn die Zeile fehlt, einem anderen
    Nutzer gehoert, noch gilt oder keinen Nachfolger hat (z. B. per Abmelden widerrufen).

    Bekannte Grenze: Die Kette sagt nicht, WER erneuert hat. Der Nachfolger uebernimmt
    Browser, Geraet und IP des Vorgaengers (`refresh_access_token`), ein Vergleich dieser
    Felder unterscheidet also nichts; das Refresh-Cookie gilt nur fuer `/api/v1/auth` und
    kommt beim Passwortwechsel gar nicht an. Hat jemand mit einem abgegriffenen Cookie die
    Kette weitergedreht, bleibt dessen Sitzung daher erhalten, solange der Wechsel noch mit
    dem alten Zugangs-Token (hoechstens `access_token_ttl_seconds`) kommt. Wer eine
    Uebernahme vermutet, meldet sich vor dem Wechsel neu an: dann zeigt die `sid` auf eine
    frische Sitzung, und jede andere Kette endet."""
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
    """Meldet alle Sitzungen eines Nutzers ab (nach einem Passwortwechsel
    durften alte Refresh-Tokens sonst noch 30 Tage neue Access-Tokens holen).
    `except_token_id` ist die Sitzung, die angemeldet bleiben soll -- beim eigenen
    Passwortwechsel die aktuelle. Gibt die Zahl der abgemeldeten Sitzungen zurueck."""
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    if except_token_id is not None:
        # Hat ein anderer Tab inzwischen erneuert, zeigt die `sid` im Zugangs-Token auf
        # eine schon widerrufene Zeile: dann gilt die Sitzung am Ende der Kette,
        # sonst wuerde die tatsaechlich aktuelle mit abgemeldet.
        # Grenze dieser Kulanz: siehe `_live_session_head`.
        except_token_id = await _live_session_head(session, user_id, except_token_id)
        stmt = stmt.where(RefreshToken.id != except_token_id)
        # Vorgaenger der behaltenen Sitzung (durch Rotation widerrufen) sollen auch
        # keine Gnadenfrist mehr bekommen -- sonst kaeme ein alter Token 30 s lang
        # trotz Passwortwechsel noch an einen Access-Token.
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
    """Kein Fehler, wenn der Token schon widerrufen/unbekannt ist -- Logout ist aus
    Client-Sicht immer erfolgreich (der Zustand "abgemeldet" ist danach garantiert)."""
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
    """Erzeugt ein neues Secret, speichert es verschluesselt (unbestaetigt) am Nutzer.
    Gibt `(secret_base32, otpauth_uri)` zurueck -- der Base32-Wert ist NUR JETZT im
    Klartext sichtbar, kein Endpunkt liefert ihn je wieder aus."""
    if user.totp_secret_id is not None and user.totp_confirmed_at is not None:
        raise AuthError(
            "2FA ist bereits aktiv -- erst DELETE /me/totp, dann neu einrichten."
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
        # Unbestaetigtes Vorgaenger-Secret aus einem abgebrochenen Setup-Versuch.
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
    """Bestaetigt die 2FA-Einrichtung und erzeugt dabei die ersten Wiederherstellungs-Codes
    (Klartext, einmalig -- die API reicht sie an den Nutzer durch).

    Nur fuer eine NOCH NICHT bestaetigte Einrichtung: bei schon aktiver 2FA gaebe dieser Weg
    sonst ohne Passwort einen frischen Satz Codes aus (dafuer gibt es `POST /me/recovery-codes`
    mit Passwort). Falsche Codes werden wie falsche Passwoerter je Nutzer gedrosselt."""
    if user.totp_secret_id is not None and user.totp_confirmed_at is not None:
        raise AuthError("Zwei-Faktor ist schon aktiv.")
    if user.totp_secret_id is None:
        raise AuthError(
            "Kein 2FA-Setup in Arbeit -- zuerst POST /me/totp/setup aufrufen."
        )

    attempt = _begin_attempt(f"konto:{user.id}", SENSITIVE_ATTEMPT_KEY)
    keyring = vault.load_keyring(settings)
    raw_secret = await vault.read_secret_plaintext(session, keyring, user.totp_secret_id)
    if not pyotp.TOTP(raw_secret).verify("".join(code.split()), valid_window=1):
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
        raise InvalidMfaCode("Ungültiger 2FA-Code -- Setup nicht bestätigt.")
    login_limit.succeeded(attempt)

    user.totp_confirmed_at = utcnow()
    await session.flush()
    return await issue_recovery_codes(session, user)


async def disable_totp(session: AsyncSession, user: User) -> None:
    if user.totp_secret_id is not None:
        await vault.delete_secret(session, user.totp_secret_id)
    user.totp_secret_id = None
    user.totp_confirmed_at = None
    # Ohne Zwei-Faktor sind die Wiederherstellungs-Codes sinnlos -- und duerfen bei einer
    # spaeteren Neu-Einrichtung nicht wieder gueltig werden.
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    await session.flush()


def two_factor_enabled(user: User) -> bool:
    return user.totp_secret_id is not None and user.totp_confirmed_at is not None


# ---------------------------------------------------------------------------
# Wiederherstellungs-Codes
# ---------------------------------------------------------------------------


WRONG_PASSWORD_MESSAGE = "Das aktuelle Passwort stimmt nicht."


class WrongPassword(AuthError):
    """Das zur Bestaetigung eingegebene aktuelle Passwort stimmt nicht (API: 400)."""


async def require_current_password(
    session: AsyncSession,
    user: User,
    password: str,
    *,
    action: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Bestaetigt eine empfindliche Konto-Aktion (`action`, z. B. `2fa_abschalten`) mit dem
    aktuellen Passwort. Falsche Eingaben werden je NUTZER gedrosselt (alle empfindlichen
    Aktionen teilen sich einen Zaehler, wie beim Login: 10 Fehlversuche in 5 Minuten, dann
    429) und als `auth.password_check_failed` protokolliert. Der Zaehler haengt an der
    Nutzerkennung, nicht an der IP -- sonst liesse sich die Sperre mit wechselnden Adressen
    umgehen, und ein Angreifer mit gestohlener Sitzung kaeme trotz Drosselung weiter."""
    attempt = _begin_attempt(f"konto:{user.id}", SENSITIVE_ATTEMPT_KEY)
    if not security.verify_password(password, user.password_hash):
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


async def issue_recovery_codes(session: AsyncSession, user: User) -> list[str]:
    """Erzeugt einen neuen Satz (`RECOVERY_CODE_COUNT` Codes) und ersetzt alle bisherigen --
    benutzte wie unbenutzte werden geloescht, alte Codes sind damit sofort ungueltig. Gibt
    die Klartext-Codes zurueck: das ist das EINZIGE Mal, dass sie existieren."""
    codes: list[str] = []
    while len(codes) < RECOVERY_CODE_COUNT:
        candidate = security.generate_recovery_code()
        if candidate not in codes:
            codes.append(candidate)
    # Argon2 ist absichtlich langsam (10 Hashes: auf dem Pi Sekunden) -- nicht im
    # Event-Loop rechnen, sonst steht waehrenddessen das ganze Dashboard.
    hashes = await asyncio.to_thread(
        lambda: [security.hash_password(security.normalize_recovery_code(c)) for c in codes]
    )
    await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    for code_hash in hashes:
        session.add(RecoveryCode(user_id=user.id, code_hash=code_hash))
    await session.flush()
    return codes


async def recovery_codes_remaining(session: AsyncSession, user_id: str) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(RecoveryCode)
        .where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
    )
    return int(count or 0)


async def consume_recovery_code(session: AsyncSession, user: User, code: str) -> bool:
    """Loest einen Wiederherstellungs-Code ein: `True` und als benutzt markiert, wenn er zu
    einem noch unbenutzten Code des Nutzers passt. Das Markieren ist atomar (nur wer die Zeile
    von "unbenutzt" auf "benutzt" setzt, gewinnt) -- zwei gleichzeitige Anmeldungen mit
    demselben Code kommen nicht beide durch."""
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

    def _find() -> str | None:
        for row_id, code_hash in rows:
            if security.verify_password(normalized, code_hash):
                return row_id
        return None

    matched_id = await asyncio.to_thread(_find)
    if matched_id is None:
        return False
    claim = await session.execute(
        update(RecoveryCode)
        .where(RecoveryCode.id == matched_id, RecoveryCode.used_at.is_(None))
        .values(used_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    return (claim.rowcount or 0) == 1
