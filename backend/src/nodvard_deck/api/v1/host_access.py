"""Server-Zugänge: Anforderungen, SSH-Schlüssel, Einrichtungsbefehl, Standard-Zugang (docs/04-API.md §3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated, Any

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from nodvard_sdk import HostRequirementSpec
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select

from ...config import Settings
from ...core import rate_limit, vault
from ...core.vault import VaultError
from ...db.base import utcnow
from ...ext.root_helpers import RootHelperFile
from ...ext.runtime import get_extension_runtime
from ...models import Host, HostCredential, KnownHostKey, User
from ...services import audit as audit_service
from ...services import extensions as extensions_service
from ...services import helper_hints as helper_hints_service
from ...services import host_check, host_setup, one_time_password
from ...services import hosts as hosts_service
from ...services.host_check import ConnectionCheckOut
from ...services.host_setup import GROUP_RE, SetupError
from ..deps import SessionDep, SettingsDep, require_permission
from ..errors import CodedHTTPException
from .auth import confirm_stored_secret_change
from .hosts import (
    CredentialOut,
    KnownHostKeyOut,
    WriteUser,
    _audit,
    validate_ssh_port,
    validate_ssh_username,
)

router = APIRouter(tags=["hosts"])
log = logging.getLogger("nodvard_deck.host_access")

# Private Schlüssel verlassen den Vault nie: `generate-key` antwortet nur mit öffentlichem Teil und Fingerabdruck,
# `setup` leitet den öffentlichen Teil serverseitig ab und verwirft den Klartext. Fehlertexte sind feste Sätze ohne
# Schlüsselmaterial. Was ein Server braucht, melden Extensions über `ctx.ui.register_host_requirement()`.

# Der Gruppenname kommt von einer Extension und landet später in einem Shell-Befehl, daher nur diese enge Form.
UNIX_GROUP_RE = GROUP_RE

# Gruppen, die einer Erweiterung nie einen Benutzer-Eintrag im Einrichtungsbefehl erlauben (Root-Rechte oder lesender
# Zugriff auf alles). `docker` ist bewusst erlaubt: praktisch root, aber genau dafür gedacht, die Hinweise im Befehl
# sagen es.
PRIVILEGED_GROUPS = frozenset({
    "root", "sudo", "wheel", "admin", "shadow", "disk", "adm", "staff", "lxd", "libvirt", "kvm",
})

KEY_GENERATION_LIMIT = 10
KEY_GENERATION_WINDOW_S = 5 * 60
_key_generation = rate_limit.SlidingWindow(KEY_GENERATION_LIMIT, KEY_GENERATION_WINDOW_S)

_SSH_CREDENTIAL_KINDS = ("ssh_key", "ssh_password")


async def _load_host(session: SessionDep, host_id: str) -> Host:
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    return host


class HostRequirementOut(BaseModel):
    ext_id: str
    id: str
    label: str
    check_command: str | None
    ok_text: str
    fail_hint: str
    """Darf `{user}` enthalten (der SSH-Benutzer); die Oberfläche setzt ihn ein."""
    unix_group: str | None
    needs_root: bool
    root_reason: str | None
    order: int


def matching_requirements(host: Host) -> list[tuple[str, HostRequirementSpec]]:
    """Die zum Host passenden Meldungen der Extensions (`HostRequirementSpec`), sortiert nach `order`, dann Bezeichnung.
    Passt: `tags` leer oder mindestens ein Tag gemeinsam, und der `os_family` des Hosts steht in `os_families`
    (leer = jedes System)."""
    tags = {t.tag for t in host.tags}
    found: list[tuple[str, HostRequirementSpec]] = []
    for ext_id, spec in get_extension_runtime().ui.all_host_requirements():
        if spec.tags and not tags.intersection(spec.tags):
            continue
        if spec.os_families and host.os_family not in spec.os_families:
            continue
        found.append((ext_id, spec))
    found.sort(key=lambda item: (item[1].order, item[1].label, item[0], item[1].id))
    return found


def root_helpers_for(host: Host) -> list[RootHelperFile]:
    """Die Hilfsskripte der eingeschalteten Erweiterungen, die auf diesem Server root brauchen (`needs_root` in einer
    passenden Anforderung) und eines mitbringen, nach Kennung sortiert. Nur sie kommen per Einrichtungsbefehl auf den
    Server, und nur nach ihnen fragt „Verbindung prüfen“."""
    loaded = get_extension_runtime().loaded
    found: dict[str, RootHelperFile] = {}
    for ext_id, spec in matching_requirements(host):
        ext = loaded.get(ext_id)
        if spec.needs_root and ext is not None and ext.root_helper is not None:
            found[ext_id] = ext.root_helper
    return [found[ext_id] for ext_id in sorted(found)]


def valid_group(name: str | None, ext_id: str | None = None) -> str | None:
    """Der Gruppenname, wenn er gültig und nicht privilegiert ist, sonst `None` (bei einer Sperrlisten-Gruppe mit
    Eintrag im Log). `docker` ist bewusst erlaubt."""
    if name is None or not UNIX_GROUP_RE.fullmatch(name):
        return None
    if name in PRIVILEGED_GROUPS:
        log.warning("Erweiterung %s verlangt die privilegierte Gruppe '%s' -- ignoriert.", ext_id or "?", name)
        return None
    return name


@router.get("/hosts/{host_id}/requirements", dependencies=[Depends(require_permission("hosts.read"))])
async def list_host_requirements(host_id: str, session: SessionDep) -> list[HostRequirementOut]:
    """Was Extensions auf diesem Server brauchen: root ohne Passwort, eine Gruppe (z. B. für Container), ein lesender
    Prüfbefehl. Grundlage des Einrichtungsbefehls."""
    host = await _load_host(session, host_id)
    return [
        HostRequirementOut(
            ext_id=ext_id, id=spec.id, label=spec.label, check_command=spec.check_command, ok_text=spec.ok_text,
            fail_hint=spec.fail_hint, unix_group=valid_group(spec.unix_group, ext_id), needs_root=spec.needs_root,
            root_reason=spec.root_reason, order=spec.order,
        )
        for ext_id, spec in matching_requirements(host)
    ]


class GenerateKeyIn(BaseModel):
    # Die Oberfläche nennt neue Zugänge „nodvard“ und schickt den Namen immer mit. Der API-Standard bleibt bewusst
    # „lattice“: ältere Aufrufer (auch die App) schicken oft keinen Namen, ein anderer Standard änderte für sie still
    # den Benutzer auf dem Server. Ändern nur nach angekündigter Übergangszeit (docs/04-API.md, „Kompatibilität“).
    username: str = "lattice"
    port: int = 22

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return validate_ssh_username(value)

    @field_validator("port")
    @classmethod
    def _check_port(cls, value: int) -> int:
        return validate_ssh_port(value)


class GeneratedKeyOut(BaseModel):
    credential: CredentialOut
    public_key: str
    """`ssh-ed25519 AAAA... nodvard@<Kurzname>`, gehört in `authorized_keys` auf dem Server."""
    fingerprint: str
    """`SHA256:...` wie bei `ssh-keygen -l`."""


@router.post("/hosts/{host_id}/credentials/generate-key", status_code=status.HTTP_201_CREATED)
async def generate_key(
    host_id: str, payload: GenerateKeyIn, request: Request, session: SessionDep, settings: SettingsDep, user: WriteUser
) -> GeneratedKeyOut:
    """Erzeugt einen eigenen ed25519-Schlüssel nur für diesen Server (der private Teil geht direkt in den Vault). Er
    wird nur Standard-Zugang, wenn der Server noch keinen SSH-Zugang hat, sonst erst nach Prüfung per `make-default`."""
    host = await _load_host(session, host_id)
    wait = _key_generation.hit(user.id)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Zu viele Schlüssel erzeugt. Bitte in {rate_limit.wait_text(wait)} erneut versuchen.",
            headers={"Retry-After": str(wait)},
        )
    private_key = asyncssh.generate_private_key("ssh-ed25519").export_private_key("openssh").decode("ascii")
    info = host_setup.derive_public_key(private_key, comment=host_setup.key_comment(host.name))
    has_ssh_access = any(c.kind in _SSH_CREDENTIAL_KINDS for c in await hosts_service.list_credentials(session, host_id))
    credential = await hosts_service.add_credential(
        session, settings, host_id=host_id, kind="ssh_key", username=payload.username, port=payload.port,
        secret_value=private_key, is_default=not has_ssh_access, created_by_user_id=user.id,
        description=f"Von Nodvard Deck erzeugter SSH-Schlüssel für Host '{host.name}'",
    )
    del private_key
    await _audit(
        session, user, "host.key_generated", target_id=host_id,
        detail={
            "credential_id": credential.id, "username": credential.username, "port": credential.port,
            "fingerprint": info.fingerprint,
        },
    )
    await extensions_service.auto_enable_for(request.app, session, settings, "host_credential", user_id=user.id)
    return GeneratedKeyOut(
        credential=CredentialOut.from_model(credential), public_key=info.public_key, fingerprint=info.fingerprint
    )


class SetupHelperOut(BaseModel):
    ext_id: str
    module: str
    """Anzeigename der Erweiterung."""
    path: str
    """Ort auf dem Server, z. B. `/usr/local/lib/nodvard-deck/shield-root`."""
    version: int
    sha256: str
    text: str

    @classmethod
    def from_file(cls, helper: RootHelperFile) -> SetupHelperOut:
        return cls(
            ext_id=helper.ext_id, module=helper.ext_name, path=helper.install_path, version=helper.version,
            sha256=helper.sha256, text=helper.text,
        )


class SetupOut(BaseModel):
    username: str
    public_key: str
    fingerprint: str
    one_liner: str
    """Der Befehl zum Einfügen auf dem Server (als root direkt, sonst über sudo)."""
    script: str
    """Dasselbe lesbar, eine Anweisung je Zeile."""
    notes: list[str]
    groups: list[str]
    """Die Gruppen, die der Befehl wirklich einträgt (nur solche, die Erweiterungen verlangen)."""
    sudo: bool
    """Ob der Befehl root ohne Passwort einrichtet (nie für `root` selbst)."""
    helpers: list[SetupHelperOut] = Field(default_factory=list)
    """Die Hilfsskripte, die der Befehl mitbringt (nur mit `sudo`), samt Text: Im Befehl stehen sie base64-kodiert."""


def _split_groups(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in raw or []:
        out.extend(part.strip() for part in item.split(",") if part.strip())
    return out


@dataclass(frozen=True)
class _PreparedSetup:
    host: Host
    credential: HostCredential
    info: host_setup.PublicKeyInfo
    script: str
    one_liner: str
    groups: list[str]
    sudo: bool
    helpers: list[RootHelperFile]


async def _prepare_setup(
    session: SessionDep, settings: Settings, host_id: str, credential_id: str, *, sudo: bool, groups: list[str] | None,
    report_state: bool = False,
) -> _PreparedSetup:
    """Gemeinsame Vorbedingungen und das Skript für `GET .../setup` und `POST .../setup-with-password`. Nur Linux, nur
    Zugänge mit SSH-Schlüssel, nur übliche Linux-Benutzernamen; Gruppen nur, wenn Erweiterungen sie für diesen Server
    verlangen. Fehler sind feste Sätze (404/422)."""
    host = await _load_host(session, host_id)
    credential = await session.get(HostCredential, credential_id)
    if credential is None or credential.host_id != host_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Zugangsdaten.")
    if host.os_family != "linux":
        raise HTTPException(
            status_code=422, detail="Den Einrichtungsbefehl gibt es nur für Linux-Server."
        )
    if credential.kind != "ssh_key":
        raise HTTPException(
            status_code=422,
            detail="Den Einrichtungsbefehl gibt es nur für Zugänge mit SSH-Schlüssel, nicht für Passwörter.",
        )
    if not host_setup.USER_RE.fullmatch(credential.username):
        raise HTTPException(
            status_code=422,
            detail=(
                "Der Benutzer dieses Zugangs ist kein üblicher Linux-Benutzername (Kleinbuchstaben, Ziffern, _ und -, "
                "höchstens 32 Zeichen) – dafür gibt es keinen Einrichtungsbefehl."
            ),
        )
    offered = {g for ext, spec in matching_requirements(host) if (g := valid_group(spec.unix_group, ext))}
    wanted: list[str] = []
    for group in _split_groups(groups):
        if group in offered and group not in wanted:
            wanted.append(group)
    is_root = credential.username == "root"
    if is_root:
        wanted = []
    use_sudo = sudo and not is_root
    helpers = root_helpers_for(host) if use_sudo else []

    try:
        private_key = await vault.read_secret_plaintext(session, vault.load_keyring(settings), credential.secret_id)
        # Der öffentliche Teil wird aus dem privaten im Vault abgeleitet, mit eigenem Kommentar, nie dem im Schlüssel.
        info = host_setup.derive_public_key(private_key, comment=host_setup.key_comment(host.name))
        del private_key
        script, one_liner = host_setup.build_setup_script(
            credential.username, info.public_key, groups=wanted, sudo=use_sudo, helpers=helpers, report_state=report_state
        )
    except (SetupError, VaultError) as exc:
        detail = str(exc) if isinstance(exc, SetupError) else "Der gespeicherte Schlüssel lässt sich nicht lesen."
        raise HTTPException(status_code=422, detail=detail) from None
    return _PreparedSetup(
        host=host, credential=credential, info=info, script=script, one_liner=one_liner, groups=wanted, sudo=use_sudo,
        helpers=helpers,
    )


def _notes(prepared: _PreparedSetup) -> list[str]:
    return host_setup.setup_notes(
        prepared.credential.username, groups=prepared.groups, sudo=prepared.sudo,
        helpers=[f"{h.ext_id}-root" for h in prepared.helpers],
    )


@router.get("/hosts/{host_id}/credentials/{credential_id}/setup")
async def credential_setup(
    host_id: str,
    credential_id: str,
    session: SessionDep,
    settings: SettingsDep,
    _user: WriteUser,
    sudo: bool = False,
    groups: Annotated[list[str] | None, Query()] = None,
) -> SetupOut:
    """Der Befehl, den man auf dem Server ausführt, damit sich Nodvard Deck mit diesem Schlüssel anmelden kann. `groups`
    sind Wünsche (`?groups=docker` oder `?groups=a,b`), eingetragen werden nur Gruppen, die Erweiterungen verlangen.
    Ändert nichts, daher GET."""
    prepared = await _prepare_setup(session, settings, host_id, credential_id, sudo=sudo, groups=groups)
    username = prepared.credential.username
    return SetupOut(
        username=username, public_key=prepared.info.public_key, fingerprint=prepared.info.fingerprint,
        one_liner=prepared.one_liner, script=prepared.script, notes=_notes(prepared), groups=prepared.groups,
        sudo=prepared.sudo, helpers=[SetupHelperOut.from_file(h) for h in prepared.helpers],
    )


_LOGIN_USER_ERROR = (
    "Anmelde-Konto: nur Kleinbuchstaben, Ziffern, _ und - (höchstens 32 Zeichen, nicht mit einer Ziffer oder - beginnen)."
)


class SetupWithPasswordIn(BaseModel):
    login_user: str = "root"
    """Das Konto für die EINE Anmeldung (root oder ein Konto mit sudo), nicht der Benutzer des Zugangs."""
    password: SecretStr
    """Wird einmal benutzt und nie gespeichert, protokolliert oder zurückgegeben."""
    sudo: bool = False
    """Wie `GET .../setup`: root ohne Passwort für den Benutzer des Zugangs einrichten."""
    groups: list[str] = Field(default_factory=list, max_length=20)
    """Wie `GET .../setup`: nur Gruppen, die Erweiterungen für diesen Server verlangen, werden eingetragen."""
    sudo_password_same: bool = True
    """Nur ohne root: sudo bekommt dasselbe Passwort (über die Standardeingabe, nie in der Befehlszeile).
    `false`: `sudo -n`, geht nur, wenn das Konto sudo ohne Passwort darf."""

    @field_validator("login_user")
    @classmethod
    def _check_login_user(cls, value: str) -> str:
        if not host_setup.USER_RE.fullmatch(value):
            raise ValueError(_LOGIN_USER_ERROR)
        return value

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: SecretStr) -> SecretStr:
        # Feste Sätze ohne den Wert. Ein Zeilenumbruch würde bei `sudo -S` die Eingabe zerteilen.
        secret = value.get_secret_value()
        if not secret:
            raise ValueError("Bitte das Passwort eingeben.")
        if len(secret) > one_time_password.MAX_PASSWORD_LENGTH:
            raise ValueError(f"Das Passwort ist zu lang (höchstens {one_time_password.MAX_PASSWORD_LENGTH} Zeichen).")
        if any(c in secret for c in "\n\r\x00"):
            raise ValueError("Das Passwort darf keinen Zeilenumbruch enthalten.")
        try:
            secret.encode("utf-8")  # ein einzelnes Surrogat aus JSON („\\ud800“) lässt sich nicht senden
        except UnicodeEncodeError:
            raise ValueError("Das Passwort enthält ein Zeichen, das sich nicht senden lässt.") from None
        return value


class SetupWithPasswordOut(BaseModel):
    ok: bool
    """Der Befehl ist gelaufen, und die Prüfung mit dem neuen Schlüssel hat keinen Fehler gefunden."""
    check: ConnectionCheckOut | None
    """Ergebnis von „Verbindung prüfen“ mit dem neuen Schlüssel (wie `POST /hosts/{id}/check`); `null`, wenn die Prüfung
    gerade nicht laufen konnte (zu viele Prüfungen), dann steht ein Hinweis in `notes`."""
    notes: list[str]
    messages: list[str]
    """Meldungen des Einrichtungsbefehls (nur seine eigenen Zeilen, gekürzt)."""
    made_default: bool
    """Der Zugang ist jetzt der Standard (nur, wenn der Server noch keinen hatte)."""


async def _audit_check(session: SessionDep, user: User, host_id: str, result: ConnectionCheckOut) -> None:
    """Protokoll einer Prüfung: nur Kennungen und Ergebnisse, nie Banner, Server-Fehlertexte oder Geheimnisse."""
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="host.connection_checked",
        outcome="success" if result.ok else "failure", target_type="host", target_id=host_id,
        detail={"credential_id": result.credential_id, "items": {item.id: item.status for item in result.items}},
    )


@router.post(
    "/hosts/{host_id}/credentials/{credential_id}/setup-with-password",
    dependencies=[Depends(require_permission("hosts.execute"))],
)
async def credential_setup_with_password(
    host_id: str,
    credential_id: str,
    payload: SetupWithPasswordIn,
    session: SessionDep,
    settings: SettingsDep,
    user: WriteUser,
) -> SetupWithPasswordOut:
    """Richtet den Server für diesen Schlüssel-Zugang ein: Nodvard Deck meldet sich EINMAL mit `login_user`/`password`
    an, führt den Befehl von `GET .../setup` aus und prüft mit dem neuen Schlüssel wie `POST /hosts/{id}/check`. Fehler
    als `{detail, code}`: 400 Passwort/sudo, 409 Schlüssel unbestätigt (`host_key`) oder geändert, 429, 502, 504."""
    # Verlangt `hosts.write` UND `hosts.execute` (der Befehl läuft als root). Nicht durchs Aktions-Gate: ein Vorschlag
    # stünde samt Eingaben in `actions.payload`, dort darf das Passwort nie landen; der Mensch entscheidet mit dem
    # Absenden, jeder Versuch steht im Protokoll.
    #
    # Das Passwort geht nur an die gespeicherte Adresse und nur bei schon gemerktem Server-Schlüssel (sonst
    # `host_key_unconfirmed`). Grenzen: `services/one_time_password.py`.
    prepared = await _prepare_setup(
        session, settings, host_id, credential_id, sudo=payload.sudo, groups=payload.groups, report_state=True
    )
    host, credential = prepared.host, prepared.credential
    as_root = payload.login_user == "root"
    sudo_with_password = not as_root and payload.sudo_password_same
    command = host_setup.remote_setup_command(prepared.script, as_root=as_root, sudo_with_password=sudo_with_password)
    detail: dict[str, Any] = {
        "credential_id": credential.id, "username": credential.username, "login_user": payload.login_user,
        "sudo": prepared.sudo, "groups": prepared.groups,
        "helpers": {h.ext_id: h.version for h in prepared.helpers},
    }

    async def refuse(
        code: str, text: str, status_code: int, *, retry_after: int | None = None, audit: bool = True, **extra: Any
    ) -> CodedHTTPException:
        # Auch ein gescheiterter Versuch steht im Protokoll: vor dem Fehler festschreiben, sonst rollt `session_scope()`
        # die Zeile zurück. Eine schon protokollierte Sperre nicht bei jedem Klopfen erneut.
        if audit:
            await audit_service.log(
                session, actor_type="user", actor_id=user.id, action="host.setup_with_password", outcome="failure",
                target_type="host", target_id=host_id, detail={**detail, "result": code},
            )
            await session.commit()
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        return CodedHTTPException(status_code, text, code, headers=headers, extra=extra)

    started = utcnow()
    try:
        remote = await one_time_password.run_once(
            session, host=host, port=credential.port, user_id=user.id, login_user=payload.login_user,
            password=payload.password, command=command, password_on_stdin=sudo_with_password,
            connect_timeout_s=min(settings.ssh_connect_timeout_s, one_time_password.CONNECT_TIMEOUT_S),
            flags=host_setup.SETUP_FLAGS,
            extract=lambda stdout: host_setup.setup_report(stdout, user=credential.username),
        )
    except one_time_password.PasswordUseError as exc:
        extra = {"host_key": exc.host_key} if exc.host_key is not None else {}
        raise await refuse(
            exc.code, str(exc), exc.status, retry_after=exc.retry_after, audit=exc.audit, **extra
        ) from None

    # Der Befehl ist gelaufen (auch wenn er gleich als gescheitert gilt): für den Datei-Wächter festhalten, was er getan
    # hat. Fehlt der Bericht, merkt der Wächter nur das Zeitfenster und übernimmt nichts still.
    record = host_setup.setup_record(
        credential.username, remote.extracted or {}, sudo=prepared.sudo, helpers=prepared.helpers
    )
    hosts_service.record_setup_run(host, started, record=record)
    run = host_setup.evaluate_setup_run(
        remote.exit_status, remote.stdout, remote.stderr, via_sudo=not as_root, sudo_with_password=sudo_with_password,
        flags=remote.flags,
    )
    del remote
    if run.outcome != "ok":
        status_code = 400 if run.outcome == "sudo_failed" else 502
        raise await refuse(run.outcome, run.reason, status_code, messages=run.messages) from None

    notes = _notes(prepared)
    check: ConnectionCheckOut | None
    try:
        check = await host_check.run_check(
            session, settings, host, credential, list(matching_requirements(host)), user_id=user.id,
            helpers=root_helpers_for(host),
        )
    except host_check.CheckRefused:
        check = None
        notes.append("Die Prüfung mit dem neuen Schlüssel konnte gerade nicht laufen. Prüfe die Verbindung gleich selbst.")
    else:
        await _audit_check(session, user, host_id, check)
    login_ok = check is not None and any(i.id == "login" and i.status == "ok" for i in check.items)

    made_default = False
    if login_ok and not credential.is_default and await hosts_service.default_credential(session, host_id) is None:
        # Wie der Knopf „make-default“, wenn es keinen bisherigen Standard gibt.
        changed = await hosts_service.make_default_credential(session, host_id, credential.id)
        if changed is not None:
            host_check.carry_over_login_ok(host, changed[0])
            made_default = True
            await _audit(
                session, user, "host.credential_made_default", target_id=host_id,
                detail={"credential_id": credential.id, "previous_credential_id": None, "deleted_previous": False},
            )
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="host.setup_with_password", outcome="success",
        target_type="host", target_id=host_id,
        detail={**detail, "result": "ok", "check_ok": check.ok if check is not None else None, "made_default": made_default},
    )
    return SetupWithPasswordOut(
        ok=check is not None and check.ok, check=check, notes=notes, messages=run.messages, made_default=made_default
    )


class MakeDefaultOut(CredentialOut):
    notice: str | None = None
    """Hinweis, wenn der alte Zugang gelöscht wurde: sein öffentlicher Schlüssel steht weiter auf dem Server."""


class MakeDefaultIn(BaseModel):
    delete_previous: bool = False
    """Den bisherigen Standard-Zugang samt Geheimnis gleich mitlöschen (nur mit `current_password`, siehe unten)."""
    current_password: str = Field(default="", max_length=1024)
    totp_code: str = Field(default="", max_length=64)


@router.post("/hosts/{host_id}/credentials/{credential_id}/make-default", response_model=MakeDefaultOut)
async def make_default(
    host_id: str,
    credential_id: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: WriteUser,
    payload: MakeDefaultIn | None = None,
) -> MakeDefaultOut | JSONResponse:
    """Stellt den Standard-Zugang um (Schlüsselwechsel: neuen Zugang anlegen, prüfen, dann umstellen). Mit
    `delete_previous` verschwindet der alte Standard samt Geheimnis (409, solange der neue nicht kürzlich erfolgreich
    geprüft wurde; wie jedes Löschen gespeicherter Zugangsdaten nur mit Passwort und gegebenenfalls Code, sonst 403
    `confirm_required`); sein öffentlicher Schlüssel bleibt in authorized_keys auf dem Server (steht in `notice`)."""
    host = await _load_host(session, host_id)
    target = await session.get(HostCredential, credential_id)
    if target is None or target.host_id != host_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Zugangsdaten.")
    delete_previous = payload.delete_previous if payload is not None else False
    was_default = target.is_default
    previous = await hosts_service.default_credential(session, host_id)
    previous_id = previous.id if previous is not None and previous.id != target.id else None
    if delete_previous and previous_id is not None and not host_check.login_was_ok(host, target):
        # Den alten Zugang erst löschen, wenn der neue gerade nachweislich funktioniert hat, sonst sperrt man sich aus
        # (der alte Zugang samt Geheimnis wäre unwiederbringlich weg).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Bitte zuerst „Verbindung prüfen“ für den neuen Zugang ausführen: Die Anmeldung muss geklappt haben, "
                "und die Prüfung darf höchstens 10 Minuten her sein. Sonst wäre der alte Zugang weg, "
                "ohne dass der neue sicher funktioniert."
            ),
        )
    if delete_previous and previous_id is not None:
        # Erst nach der Prüfung oben: ein verbrauchter Code bei einem 409 hülfe niemandem.
        confirm = payload or MakeDefaultIn()
        refused = await confirm_stored_secret_change(
            request, session, settings, user, confirm.current_password, confirm.totp_code, "tresor_loeschen",
            "Der bisherige Zugang wird samt Geheimnis gelöscht. Bitte das aktuelle Passwort eingeben.",
        )
        if refused is not None:
            return refused
    previous_info = (
        {"credential_id": previous.id, "kind": previous.kind, "username": previous.username, "port": previous.port}
        if previous is not None and previous_id is not None else None
    )
    # Mit `delete_previous` in derselben Transaktion; offene Verbindungen mit dem alten Zugang werden geschlossen.
    result = await hosts_service.make_default_credential(session, host_id, credential_id, delete_previous=delete_previous)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Zugangsdaten.")
    credential, deleted = result
    if not was_default or previous_id is not None:
        # Wurde der neue Zugang gerade erst geprüft (Schlüsselwechsel), zählt das jetzt als Beleg des Servers.
        host_check.carry_over_login_ok(host, credential)
        await _audit(
            session, user, "host.credential_made_default", target_id=host_id,
            detail={
                "credential_id": credential.id, "previous_credential_id": previous_id,
                "deleted_previous": deleted is not None,
            },
        )
    if deleted is not None and previous_info is not None:
        await _audit(session, user, "host.credential_deleted", target_id=host_id, detail=previous_info)
    notice: str | None = None
    if deleted is not None:
        if previous_info is not None and previous_info["kind"] == "ssh_key":
            notice = (
                "Der alte Zugang ist in Nodvard Deck gelöscht. Sein öffentlicher Schlüssel bleibt in ~/.ssh/authorized_keys "
                "auf dem Server stehen – dort bei Bedarf selbst entfernen."
            )
        else:
            # Ein Passwort-Zugang hinterlässt auf dem Server nichts, was man aufräumen müsste.
            notice = "Der alte Zugang ist in Nodvard Deck gelöscht. Auf dem Server ändert sich dadurch nichts."
    return MakeDefaultOut(**CredentialOut.from_model(credential).model_dump(), notice=notice)


_SSH_KINDS = _SSH_CREDENTIAL_KINDS


class CheckIn(BaseModel):
    credential_id: str | None = None
    """Mit welchem Zugang geprüft wird; ohne Angabe der Standard-Zugang (ohne Zugang: nur Erreichbarkeit und
    Server-Schlüssel)."""


@router.post("/hosts/{host_id}/check")
async def check_connection(
    host_id: str, session: SessionDep, settings: SettingsDep, user: WriteUser, payload: CheckIn | None = None
) -> ConnectionCheckOut:
    """Prüft Schritt für Schritt, ob sich Nodvard Deck mit dem Server verbinden kann: erreichbar, Server-Schlüssel,
    Anmeldung, Root-Rechte, was Erweiterungen brauchen, Betriebssystem. Ist der Schlüssel unbestätigt, endet die Prüfung
    bei `host_key` (`confirm`, mit Fingerabdruck). 429 bei zu vielen Prüfungen (je Nutzer 12 in 5 Minuten)."""
    # An einen Server, dessen Schlüssel noch nicht bestätigt ist, gehen keine Zugangsdaten. Geprüft wird nur die
    # gespeicherte Adresse, nie ein Ziel aus der Anfrage.
    host = await _load_host(session, host_id)
    credential_id = payload.credential_id if payload is not None else None
    credential: HostCredential | None
    if credential_id is not None:
        credential = await session.get(HostCredential, credential_id)
        if credential is None or credential.host_id != host_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Zugangsdaten.")
        if credential.kind not in _SSH_KINDS:
            raise HTTPException(status_code=422, detail="Dieser Zugang ist keine SSH-Anmeldung und lässt sich nicht prüfen.")
    else:
        credential = await hosts_service.default_credential(session, host_id)
        if credential is not None and credential.kind not in _SSH_KINDS:
            credential = None
    try:
        result = await host_check.run_check(
            session, settings, host, credential, [(e, spec) for e, spec in matching_requirements(host)],
            user_id=user.id, helpers=root_helpers_for(host),
        )
    except host_check.CheckRefused as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc), headers=headers) from None
    await _audit_check(session, user, host_id, result)
    return result


class PinKeyIn(BaseModel):
    key_type: str = Field(min_length=1, max_length=32)
    fingerprint: str = Field(min_length=1, max_length=128)
    credential_id: str | None = None
    """Der Zugang, mit dem die Prüfung den Schlüssel gesehen hat (sein Port zählt); ohne Angabe der Standard-Zugang.
    Nötig, wenn ein weiterer Zugang einen anderen Port hat."""


@router.post(
    "/hosts/{host_id}/helper-hint/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("hosts.write"))],
)
async def dismiss_helper_hint(host_id: str, session: SessionDep) -> None:
    """Blendet den Hinweis „Server neu einrichten“ in der Übersicht für diesen Server aus, bis sich die fehlenden oder
    veralteten Hilfsskripte ändern (z. B. ein Update bringt neuere mit). 404 ohne Server, ohne offenen Hinweis 204."""
    await _load_host(session, host_id)
    await helper_hints_service.dismiss(session, host_id)


@router.post("/hosts/{host_id}/known-hosts", status_code=status.HTTP_201_CREATED)
async def pin_known_host_key(host_id: str, payload: PinKeyIn, session: SessionDep, user: WriteUser) -> KnownHostKeyOut:
    """Merkt den Server-Schlüssel, den „Verbindung prüfen“ gerade gezeigt hat. Typ, Fingerabdruck, Adresse und Port
    müssen der letzten Prüfung (höchstens 15 Minuten her) entsprechen; ein gezeigter Schlüssel gilt nur einmal.
    Sonst 409, auch wenn für den Server schon IRGENDEIN Schlüssel gemerkt ist (erst den alten vergessen)."""
    host = await _load_host(session, host_id)
    credential: HostCredential | None
    if payload.credential_id is not None:
        credential = await session.get(HostCredential, payload.credential_id)
        if credential is None or credential.host_id != host_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Zugangsdaten.")
        if credential.kind not in _SSH_KINDS:
            raise HTTPException(status_code=422, detail="Dieser Zugang ist keine SSH-Anmeldung.")
    else:
        credential = await hosts_service.default_credential(session, host_id)
    # Port: der des Zugangs `credential_id` (ein SSH-Zugang dieses Servers, sonst 404/422), sonst der des
    # Standard-Zugangs, ohne Zugang 22.
    port = credential.port if credential is not None and credential.kind in _SSH_KINDS else host_check.DEFAULT_SSH_PORT
    # Zuerst und ohne `await` dazwischen: der Merkzettel gilt nur einmal.
    if not host_check.consume_seen_key(host, port, payload.key_type, payload.fingerprint):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bitte zuerst „Verbindung prüfen“ – der Fingerabdruck muss frisch vom Server kommen.",
        )
    # IRGENDEIN gemerkter Schlüssel genügt: ein weiterer Typ wird nie per Bestätigen dazugenommen (sonst ließe sich ein
    # Schlüsseltyp-Wechsel durchwinken). Wer wirklich wechseln will, vergisst erst den alten
    # (`DELETE .../known-hosts/{key_type}`).
    existing = (await session.execute(select(KnownHostKey.id).where(KnownHostKey.host_id == host_id))).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Für diesen Server ist schon ein Schlüssel gemerkt. Erst den alten vergessen.",
        )
    row = KnownHostKey(
        host_id=host_id, key_type=payload.key_type, fingerprint=payload.fingerprint, accepted_by_user_id=user.id
    )
    session.add(row)
    await session.flush()
    # Die ausdrückliche Bestätigung hebt die Sperre nach "Schlüssel vergessen" auf (und nur sie).
    await hosts_service.host_key_was_confirmed(session, host_id)
    await _audit(
        session, user, "host.known_key_pinned", target_id=host_id,
        detail={"key_type": row.key_type, "fingerprint": row.fingerprint},
    )
    return KnownHostKeyOut(
        key_type=row.key_type, fingerprint=row.fingerprint, first_seen_at=row.first_seen_at,
        accepted_by_user_id=user.id, accepted_by_label=user.username,  # den eigenen Namen sieht jeder
    )
