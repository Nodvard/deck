"""Server-Zugaenge: was eine Extension auf einem Server braucht, SSH-Schluessel
erzeugen, Einrichtungsbefehl, Zugang als Standard setzen (docs/04-API.md §3).

Der Kern kennt hier keine Extension und kein Werkzeug (Docker, ...) beim Namen: was auf
einem Server gebraucht wird, melden die Extensions ueber `ctx.ui.register_host_requirement()`.

**Private Schluessel verlassen den Vault nie.** `generate-key` legt den privaten Schluessel
direkt im Vault ab und antwortet nur mit dem oeffentlichen Teil und dem Fingerabdruck;
`setup` entschluesselt ihn serverseitig, leitet den oeffentlichen Teil ab und verwirft den
Klartext. Weder Antworten noch Fehlertexte (auch keine 422) noch Protokoll noch Log
enthalten Schluesselmaterial -- Fehlertexte sind feste deutsche Saetze, nie eine
weitergereichte Ausnahme.
"""

from __future__ import annotations

import logging
from typing import Annotated

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from nodvard_sdk import HostRequirementSpec
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ...core import rate_limit, vault
from ...core.vault import VaultError
from ...ext.runtime import get_extension_runtime
from ...models import Host, HostCredential, KnownHostKey
from ...services import audit as audit_service
from ...services import extensions as extensions_service
from ...services import host_check, host_setup
from ...services import hosts as hosts_service
from ...services.host_check import ConnectionCheckOut
from ...services.host_setup import GROUP_RE, SetupError
from ..deps import SessionDep, SettingsDep, require_permission
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

UNIX_GROUP_RE = GROUP_RE
"""Gueltiger Gruppenname -- der Name kommt von einer Extension und landet spaeter in einem
Shell-Befehl, deshalb gilt nur diese enge Form."""

PRIVILEGED_GROUPS = frozenset({
    "root", "sudo", "wheel", "admin", "shadow", "disk", "adm", "staff", "lxd", "libvirt", "kvm",
})
"""Gruppen, die einer Erweiterung nie einen Benutzer-Eintrag im Einrichtungsbefehl erlauben: sie
bedeuten Root-Rechte oder lesenden Zugriff auf alles. Die Ausnahme ist `docker` (ebenfalls
praktisch root, aber genau dafuer gedacht -- die Hinweise im Befehl sagen es)."""

KEY_GENERATION_LIMIT = 10
KEY_GENERATION_WINDOW_S = 5 * 60
_key_generation = rate_limit.SlidingWindow(KEY_GENERATION_LIMIT, KEY_GENERATION_WINDOW_S)

_SSH_CREDENTIAL_KINDS = ("ssh_key", "ssh_password")


async def _load_host(session: SessionDep, host_id: str) -> Host:
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    return host


# ---------------------------------------------------------------------------
# Was Erweiterungen auf dem Server brauchen
# ---------------------------------------------------------------------------


class HostRequirementOut(BaseModel):
    ext_id: str
    id: str
    label: str
    check_command: str | None
    ok_text: str
    fail_hint: str
    """Darf `{user}` enthalten (der SSH-Benutzer); die Oberflaeche setzt ihn ein."""
    unix_group: str | None
    needs_root: bool
    root_reason: str | None
    order: int


def matching_requirements(host: Host) -> list[tuple[str, HostRequirementSpec]]:
    """Die zum Host passenden Meldungen der Extensions (`HostRequirementSpec`), sortiert nach
    `order`, dann Bezeichnung. Passt: `tags` leer oder mindestens ein Tag gemeinsam, und der
    `os_family` des Hosts steht in `os_families` (leer = jedes System)."""
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


def valid_group(name: str | None, ext_id: str | None = None) -> str | None:
    """Der Gruppenname, wenn er gueltig und nicht privilegiert ist, sonst `None` (und bei einer
    Sperrlisten-Gruppe ein Eintrag im Log). `docker` ist bewusst erlaubt."""
    if name is None or not UNIX_GROUP_RE.fullmatch(name):
        return None
    if name in PRIVILEGED_GROUPS:
        log.warning("Erweiterung %s verlangt die privilegierte Gruppe '%s' -- ignoriert.", ext_id or "?", name)
        return None
    return name


@router.get("/hosts/{host_id}/requirements", dependencies=[Depends(require_permission("hosts.read"))])
async def list_host_requirements(host_id: str, session: SessionDep) -> list[HostRequirementOut]:
    """Was Extensions auf diesem Server brauchen: root ohne Passwort, eine Gruppe (z. B. fuer
    Container), ein lesender Pruefbefehl. Grundlage des Einrichtungsbefehls."""
    host = await _load_host(session, host_id)
    return [
        HostRequirementOut(
            ext_id=ext_id, id=spec.id, label=spec.label, check_command=spec.check_command, ok_text=spec.ok_text,
            fail_hint=spec.fail_hint, unix_group=valid_group(spec.unix_group, ext_id), needs_root=spec.needs_root,
            root_reason=spec.root_reason, order=spec.order,
        )
        for ext_id, spec in matching_requirements(host)
    ]


# ---------------------------------------------------------------------------
# SSH-Schluessel erzeugen
# ---------------------------------------------------------------------------


class GenerateKeyIn(BaseModel):
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
    """`ssh-ed25519 AAAA... lattice@<Kurzname>` -- gehoert in `authorized_keys` auf dem Server."""
    fingerprint: str
    """`SHA256:...` wie bei `ssh-keygen -l`."""


@router.post("/hosts/{host_id}/credentials/generate-key", status_code=status.HTTP_201_CREATED)
async def generate_key(
    host_id: str, payload: GenerateKeyIn, request: Request, session: SessionDep, settings: SettingsDep, user: WriteUser
) -> GeneratedKeyOut:
    """Erzeugt einen eigenen ed25519-Schluessel nur fuer diesen Server. Der private Teil geht
    direkt in den Vault und erscheint nirgends sonst (nicht in der Antwort, nicht im Protokoll,
    nicht im Log). Er wird nur dann Standard-Zugang, wenn der Server noch keinen SSH-Zugang
    hat -- sonst bleibt der bisherige Standard, bis der neue geprueft und bewusst umgestellt
    ist (`make-default`)."""
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


# ---------------------------------------------------------------------------
# Einrichtungsbefehl
# ---------------------------------------------------------------------------


class SetupOut(BaseModel):
    username: str
    public_key: str
    fingerprint: str
    one_liner: str
    """Der Befehl zum Einfuegen auf dem Server (als root direkt, sonst ueber sudo)."""
    script: str
    """Dasselbe lesbar, eine Anweisung je Zeile."""
    notes: list[str]
    groups: list[str]
    """Die Gruppen, die der Befehl wirklich eintraegt (nur solche, die Erweiterungen verlangen)."""
    sudo: bool
    """Ob der Befehl root ohne Passwort einrichtet (nie fuer `root` selbst)."""


def _split_groups(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in raw or []:
        out.extend(part.strip() for part in item.split(",") if part.strip())
    return out


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
    """Der Befehl, den man auf dem Server ausfuehrt, damit sich Nodvard Deck mit diesem Schluessel
    anmelden kann. Der oeffentliche Teil wird hier aus dem privaten im Vault abgeleitet (mit
    einem eigenen Kommentar, nie dem im Schluessel eingetragenen). `groups` sind Wuensche
    (`?groups=docker` oder `?groups=a,b`); eingetragen werden nur Gruppen, die Erweiterungen
    fuer diesen Server verlangen. Aendert nichts -- daher GET."""
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

    try:
        private_key = await vault.read_secret_plaintext(session, vault.load_keyring(settings), credential.secret_id)
        info = host_setup.derive_public_key(private_key, comment=host_setup.key_comment(host.name))
        del private_key
        script, one_liner = host_setup.build_setup_script(
            credential.username, info.public_key, groups=wanted, sudo=use_sudo
        )
    except (SetupError, VaultError) as exc:
        detail = str(exc) if isinstance(exc, SetupError) else "Der gespeicherte Schlüssel lässt sich nicht lesen."
        raise HTTPException(status_code=422, detail=detail) from None
    return SetupOut(
        username=credential.username, public_key=info.public_key, fingerprint=info.fingerprint, one_liner=one_liner,
        script=script, notes=host_setup.setup_notes(credential.username, groups=wanted, sudo=use_sudo), groups=wanted,
        sudo=use_sudo,
    )


# ---------------------------------------------------------------------------
# Zugang zum Standard machen
# ---------------------------------------------------------------------------


class MakeDefaultOut(CredentialOut):
    notice: str | None = None
    """Hinweis, wenn der alte Zugang geloescht wurde: sein oeffentlicher Schluessel steht weiter auf dem Server."""


class MakeDefaultIn(BaseModel):
    delete_previous: bool = False
    """Den bisherigen Standard-Zugang samt Geheimnis gleich mitloeschen."""


@router.post("/hosts/{host_id}/credentials/{credential_id}/make-default")
async def make_default(
    host_id: str, credential_id: str, session: SessionDep, user: WriteUser, payload: MakeDefaultIn | None = None
) -> MakeDefaultOut:
    """Stellt den Standard-Zugang um (Schluesselwechsel: neuen Zugang anlegen, pruefen, dann
    umstellen). Mit `delete_previous` verschwindet der alte Standard samt Geheimnis in derselben
    Transaktion; offene Verbindungen mit dem alten Zugang werden geschlossen. **Vorher den neuen
    Zugang mit „Verbindung prüfen“ testen** -- und der alte oeffentliche Schluessel bleibt in
    authorized_keys auf dem Server (Nodvard Deck entfernt ihn dort nicht), das steht in `notice`."""
    host = await _load_host(session, host_id)
    target = await session.get(HostCredential, credential_id)
    if target is None or target.host_id != host_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Zugangsdaten.")
    delete_previous = payload.delete_previous if payload is not None else False
    was_default = target.is_default
    previous = await hosts_service.default_credential(session, host_id)
    previous_id = previous.id if previous is not None and previous.id != target.id else None
    if delete_previous and previous_id is not None and not host_check.login_was_ok(host, target):
        # Den alten Zugang erst loeschen, wenn der neue gerade nachweislich funktioniert hat --
        # sonst sperrt man sich aus (der alte Zugang samt Geheimnis waere unwiederbringlich weg).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Bitte zuerst „Verbindung prüfen“ für den neuen Zugang ausführen: Die Anmeldung muss geklappt haben, "
                "und die Prüfung darf höchstens 10 Minuten her sein. Sonst wäre der alte Zugang weg, "
                "ohne dass der neue sicher funktioniert."
            ),
        )
    previous_info = (
        {"credential_id": previous.id, "kind": previous.kind, "username": previous.username, "port": previous.port}
        if previous is not None and previous_id is not None else None
    )
    result = await hosts_service.make_default_credential(session, host_id, credential_id, delete_previous=delete_previous)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Zugangsdaten.")
    credential, deleted = result
    if not was_default or previous_id is not None:
        await _audit(
            session, user, "host.credential_made_default", target_id=host_id,
            detail={
                "credential_id": credential.id, "previous_credential_id": previous_id,
                "deleted_previous": deleted is not None,
            },
        )
    if deleted is not None and previous_info is not None:
        await _audit(session, user, "host.credential_deleted", target_id=host_id, detail=previous_info)
    notice = (
        "Der alte Zugang ist in Nodvard Deck gelöscht. Sein öffentlicher Schlüssel bleibt in ~/.ssh/authorized_keys "
        "auf dem Server stehen – dort bei Bedarf selbst entfernen."
        if deleted is not None else None
    )
    return MakeDefaultOut(**CredentialOut.from_model(credential).model_dump(), notice=notice)


# ---------------------------------------------------------------------------
# Verbindung pruefen
# ---------------------------------------------------------------------------

_SSH_KINDS = _SSH_CREDENTIAL_KINDS


class CheckIn(BaseModel):
    credential_id: str | None = None
    """Mit welchem Zugang geprueft wird; ohne Angabe der Standard-Zugang (ohne Zugang: nur
    Erreichbarkeit und Server-Schluessel)."""


@router.post("/hosts/{host_id}/check")
async def check_connection(
    host_id: str, session: SessionDep, settings: SettingsDep, user: WriteUser, payload: CheckIn | None = None
) -> ConnectionCheckOut:
    """Prueft Schritt fuer Schritt, ob sich Nodvard Deck mit dem Server verbinden kann: erreichbar,
    Server-Schluessel, Anmeldung, Root-Rechte, was Erweiterungen brauchen, Betriebssystem.

    **An einen Server, dessen Schluessel noch nicht bestaetigt ist, gehen keine Zugangsdaten** --
    die Pruefung meldet dann `host_key` als `confirm` mit dem Fingerabdruck und hoert auf
    (`POST /hosts/{id}/known-hosts` bestaetigt ihn). Geprueft wird nur die gespeicherte Adresse,
    nie ein Ziel aus der Anfrage. 429 bei zu vielen Pruefungen (je Nutzer 12 in 5 Minuten, je Server
    nur eine gleichzeitig)."""
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
            user_id=user.id,
        )
    except host_check.CheckRefused as exc:
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc), headers=headers) from None
    # Protokoll: nur Kennungen und Ergebnisse -- weder Banner noch Fehlerausgaben des Servers noch Geheimnisse.
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action="host.connection_checked",
        outcome="success" if result.ok else "failure", target_type="host", target_id=host_id,
        detail={"credential_id": result.credential_id, "items": {item.id: item.status for item in result.items}},
    )
    return result


class PinKeyIn(BaseModel):
    key_type: str = Field(min_length=1, max_length=32)
    fingerprint: str = Field(min_length=1, max_length=128)


@router.post("/hosts/{host_id}/known-hosts", status_code=status.HTTP_201_CREATED)
async def pin_known_host_key(host_id: str, payload: PinKeyIn, session: SessionDep, user: WriteUser) -> KnownHostKeyOut:
    """Merkt den Server-Schluessel, den „Verbindung pruefen“ gerade gezeigt hat (Fingerabdruck
    bestaetigen). Nur genau dieser: Typ und Fingerabdruck muessen dem entsprechen, was die letzte
    Pruefung dieses Servers (hoechstens 15 Minuten her) vom Server gesehen hat -- ein selbst
    mitgebrachter Fingerabdruck wird abgelehnt (409). Ein einmal gezeigter Schluessel laesst sich
    nur einmal bestaetigen. Ist fuer den Server schon IRGENDEIN Schluessel gemerkt (auch eines anderen
    Typs), gilt: erst den alten vergessen (409). Adresse und Port muessen die der letzten Pruefung
    sein (Port des Standard-Zugangs, ohne Zugang 22)."""
    host = await _load_host(session, host_id)
    credential = await hosts_service.default_credential(session, host_id)
    port = credential.port if credential is not None and credential.kind in _SSH_KINDS else host_check.DEFAULT_SSH_PORT
    # Zuerst und ohne `await` dazwischen: der Merkzettel gilt nur einmal.
    if not host_check.consume_seen_key(host, port, payload.key_type, payload.fingerprint):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bitte zuerst „Verbindung prüfen“ – der Fingerabdruck muss frisch vom Server kommen.",
        )
    # IRGENDEIN gemerkter Schluessel genuegt: ein weiterer Typ wird nie per Bestaetigen dazugenommen
    # (sonst liesse sich ein Schluesseltyp-Wechsel durchwinken). Wer wirklich wechseln will, vergisst
    # erst den alten (`DELETE .../known-hosts/{key_type}`).
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
    await _audit(
        session, user, "host.known_key_pinned", target_id=host_id,
        detail={"key_type": row.key_type, "fingerprint": row.fingerprint},
    )
    return KnownHostKeyOut(
        key_type=row.key_type, fingerprint=row.fingerprint, first_seen_at=row.first_seen_at,
        accepted_by_user_id=user.id, accepted_by_label=user.username,  # den eigenen Namen sieht jeder
    )
