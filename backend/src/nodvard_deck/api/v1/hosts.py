"""Hosts, Gruppen, Zugangsdaten -- docs/04-API.md §3.

`/hosts/{id}/credentials` ist in docs/04 nicht explizit aufgefuehrt (das Dokument
zeigt keinen eigenen Zugangsdaten-Endpunkt) -- Gap-Fill-Entscheidung wie `client_type`
in WP-1: ohne IRGENDeinen Weg, Zugangsdaten anzulegen, waere kein Host je per SSH
erreichbar. Metadaten-only wie bei `/secrets` (docs/03 §3, Invariante 1) -- kein
Response transportiert je den Klartext.

`GET/POST /hosts/{id}/actions...` (WP-5) gehen durch dasselbe `core.gate` wie
`ctx.actions.propose()` -- der Unterschied ist nur, WER vorschlaegt (ein Nutzer statt
einer Extension) und woher die `ActionSpec` kommt (der generische
`ExtensionRuntime.actions`-Katalog, ergaenzt um den `HostProvider` des Hosts, falls
einer existiert -- in dieser Runde nur `terminal`s `shell.exec`, siehe Abnahmebericht)."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from nodvard_sdk import Actor, ActionRequest, GateOutcome, HostProvider, Risk
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ...core import gate as gate_service
from ...core import ssh
from ...core.metrics_history import resolve_metrics_provider
from ...ext.runtime import get_extension_runtime
from ...models import Action, Host, HostCredential, HostGroup, KnownHostKey, User
from ...services import audit as audit_service
from ...services import extensions as extensions_service
from ...services import hosts as hosts_service
from ...services.actor_labels import load_user_labels, raw_actor
from ...services.auth import user_has_permission
from ..deps import CurrentUser, SessionDep, SettingsDep, require_permission
from .actions import ActionOut, action_out, mark_running

router = APIRouter(tags=["hosts"])

CredentialKind = Literal["ssh_key", "ssh_password", "api_token"]

WriteUser = Annotated[User, Depends(require_permission("hosts.write"))]


async def _audit(
    session: SessionDep, user: User, action: str, *, target_id: str, target_type: str = "host",
    detail: dict[str, Any] | None = None,
) -> None:
    """Eintrag im Protokoll fuer eine erfolgreiche Aenderung. NIE Zugangsdaten, Schluessel
    oder Passwoerter in `detail` -- nur Kennungen und Metadaten."""
    await audit_service.log(
        session, actor_type="user", actor_id=user.id, action=action, outcome="success",
        target_type=target_type, target_id=target_id, detail=detail,
    )


class ActionSpecOut(BaseModel):
    action_type: str
    label: str
    description: str | None
    icon: str | None
    default_risk: str
    permissions: list[str]
    host_bound: bool
    confirm_text: str | None
    command_field: str | None
    params_schema: dict[str, Any] | None = None
    # "host": der HostProvider bietet sie genau fuer DIESEN Host an (VM starten, ...);
    # "global": fuer jeden Host registriert (Shell-Befehl, Container-Aktionen, ...).
    # Die Server-Seite zeigt "host" als Knoepfe, "global" unter "Weitere Aktionen".
    source: Literal["host", "global"] = "global"

    @classmethod
    def from_spec(cls, spec: Any, *, source: Literal["host", "global"] = "global") -> "ActionSpecOut":  # noqa: ANN401 - nodvard_sdk.actions.ActionSpec
        return cls(
            action_type=spec.action_type, label=spec.label, description=spec.description,
            icon=spec.icon, default_risk=spec.default_risk.value, permissions=list(spec.permissions),
            host_bound=spec.host_bound, confirm_text=spec.confirm_text, command_field=spec.command_field,
            params_schema=spec.params_schema, source=source,
        )


class TriggerActionIn(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1)
    correlation_id: str | None = None


class HostCredentialSummary(BaseModel):
    """Der Standard-Zugang eines Servers -- nur Art, Benutzer und Port, nie ein Geheimnis."""

    id: str
    kind: str
    username: str
    port: int


class HostOut(BaseModel):
    id: str
    name: str
    display_name: str
    address: str
    os_family: str
    kind: str | None
    tags: list[str]
    managed_tags: list[str] = Field(default_factory=list)
    """Die Markierungen, die eine Erweiterung verwaltet (Teilmenge von `tags`) -- von
    Hand nicht aenderbar."""
    credential: HostCredentialSummary | None = None
    """Der Standard-Zugang, `null` ohne Zugang."""
    is_managed: bool
    enabled: bool
    status: str
    last_seen_at: datetime | None
    provider_ext_id: str | None
    provider_ref: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, host: Host) -> "HostOut":
        default = next((c for c in host.credentials if c.is_default), None)
        return cls(
            managed_tags=[t.tag for t in host.tags if t.managed_by_ext_id is not None],
            credential=(
                HostCredentialSummary(id=default.id, kind=default.kind, username=default.username, port=default.port)
                if default is not None else None
            ),
            id=host.id, name=host.name, display_name=host.display_name, address=host.address,
            os_family=host.os_family, kind=host.kind, tags=[t.tag for t in host.tags],
            is_managed=host.is_managed, enabled=host.enabled, status=host.status,
            last_seen_at=host.last_seen_at, provider_ext_id=host.provider_ext_id,
            provider_ref=host.provider_ref, created_at=host.created_at, updated_at=host.updated_at,
        )


_TAG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
MAX_TAGS = 20


def _validate_tags(tags: list[str] | None) -> list[str] | None:
    """Getrimmt und kleingeschrieben, `[a-z0-9][a-z0-9_-]{0,31}`, höchstens 20, Duplikate entfallen."""
    if tags is None:
        return None
    out: list[str] = []
    for raw in tags:
        tag = raw.strip().lower()
        if not _TAG_RE.fullmatch(tag):
            raise ValueError(
                f"Ungültige Markierung {raw!r}: nur Kleinbuchstaben, Ziffern, - und _ (höchstens 32 Zeichen)."
            )
        if tag not in out:
            out.append(tag)
    if len(out) > MAX_TAGS:
        raise ValueError(f"Höchstens {MAX_TAGS} Markierungen erlaubt.")
    return out


_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_HOSTNAME_LABEL_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_NAME_ERROR = "Kurzname: nur Kleinbuchstaben, Ziffern, - und _ (höchstens 64 Zeichen)."
_ADDRESS_ERROR = "Adresse: nur IP-Adresse oder Rechnername, ohne http:// und ohne Port."


def _validate_name(value: str) -> str:
    name = value.strip().lower()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(_NAME_ERROR)
    return name


def _validate_address(value: str) -> str:
    """IP-Adresse (nicht `0.0.0.0`/`::`, keine Multicast-Adresse) oder Rechnername nach
    RFC 1123 (Labels aus Buchstaben, Ziffern, `-`, zusammen hoechstens 253 Zeichen). Kein
    Schema, kein `user@`, kein Pfad, kein Leerzeichen, kein Port."""
    # Ein einzelner Punkt am Ende ("pve.fritz.box.") ist ein vollqualifizierter Name -- weglassen.
    address = value.strip().lower().removesuffix(".")
    if not address or "%" in address:
        raise ValueError(_ADDRESS_ERROR)
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_unspecified or ip.is_multicast:
            raise ValueError(_ADDRESS_ERROR)
        return address
    labels = address.split(".")
    # Ein rein numerischer letzter Teil ("999.1.1.1", "1.2.3") ist eine kaputte IP-Adresse,
    # kein Rechnername.
    if len(address) > 253 or labels[-1].isdigit() or not all(_HOSTNAME_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError(_ADDRESS_ERROR)
    return address


class HostCreate(BaseModel):
    # Laenge prueft der Validator (mit deutscher Meldung), kein `Field(max_length=...)`.
    name: str
    display_name: str = ""
    address: str
    os_family: str = "linux"
    kind: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return _validate_name(value)

    @field_validator("address")
    @classmethod
    def _check_address(cls, value: str) -> str:
        return _validate_address(value)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        return _validate_tags(value) or []


class HostUpdate(BaseModel):
    display_name: str | None = None
    address: str | None = None
    os_family: str | None = None
    kind: str | None = None
    enabled: bool | None = None
    is_managed: bool | None = None
    tags: list[str] | None = None
    """Setzt die MANUELLEN Markierungen exakt auf diese Liste; von Extensions verwaltete
    bleiben unberührt."""

    @field_validator("address")
    @classmethod
    def _check_address(cls, value: str | None) -> str | None:
        return None if value is None else _validate_address(value)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str] | None) -> list[str] | None:
        return _validate_tags(value)


class HostStatusOut(BaseModel):
    status: str
    last_seen_at: datetime | None
    checked_live: bool
    detail: str | None = None


class HostMetricsOut(BaseModel):
    values: dict[str, float]
    sampled_at: datetime


class HostLatestMetricsOut(BaseModel):
    """Letzter Messwert eines Servers aus dem Verlauf (Cockpit): Prozentwerte fertig gerechnet, `None` = nicht gemessen."""

    cpu: float | None = None
    mem: float | None = None
    disk: float | None = None
    mem_used_bytes: float | None = None
    mem_total_bytes: float | None = None
    disk_used_bytes: float | None = None
    disk_total_bytes: float | None = None
    uptime_s: float | None = None
    at: datetime
    age_s: int
    stale: bool


class HostsLatestMetricsOut(BaseModel):
    hosts: dict[str, HostLatestMetricsOut]
    stale_after_s: int


class CredentialOut(BaseModel):
    id: str
    host_id: str
    kind: str
    username: str
    port: int
    is_default: bool
    created_at: datetime

    @classmethod
    def from_model(cls, c: HostCredential) -> "CredentialOut":
        return cls(
            id=c.id, host_id=c.host_id, kind=c.kind, username=c.username,
            port=c.port, is_default=c.is_default, created_at=c.created_at,
        )


# Auch Windows-Benutzer: "Max Mustermann", "user@domain.local", "DOMAIN\\user". Keine Steuerzeichen,
# Zeilenumbrueche oder Anfuehrungszeichen; beginnt nicht mit "-" (wuerde wie ein Schalter aussehen).
_SSH_USER_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@\\ -]{0,63}")


def validate_ssh_username(value: str) -> str:
    name = value.strip()
    if not _SSH_USER_RE.fullmatch(name):
        raise ValueError("Benutzername: nur Buchstaben, Ziffern, _, -, ., @, \\ und Leerzeichen (höchstens 64 Zeichen).")
    return name


def validate_ssh_port(value: int) -> int:
    if not 1 <= value <= 65535:
        raise ValueError("SSH-Port: eine Zahl von 1 bis 65535.")
    return value


def _validate_ssh_key(value: str) -> str:
    """Muss sich mit asyncssh als privater Schluessel ohne Passphrase einlesen lassen.
    Die Ausnahme selbst wird NIE weitergereicht (sie koennte Schluesselmaterial
    enthalten) -- nur eine der beiden festen Meldungen."""
    # Zeilenenden und Rand aufraeumen: Einfuegen aus Windows bringt \r\n mit.
    key = value.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    try:
        asyncssh.import_private_key(key)
    except asyncssh.KeyImportError as exc:
        if "passphrase" in str(exc).lower():
            raise ValueError(
                "Der Schlüssel ist mit einer Passphrase geschützt – Nodvard Deck braucht einen Schlüssel ohne Passphrase."
            ) from None
        raise ValueError("Das ist kein gültiger privater SSH-Schlüssel.") from None
    except Exception:  # noqa: BLE001 - egal was asyncssh wirft: nie den Text weitergeben
        raise ValueError("Das ist kein gültiger privater SSH-Schlüssel.") from None
    return key


class CredentialCreate(BaseModel):
    kind: CredentialKind
    username: str = "root"
    port: int = 22
    secret_value: str = Field(min_length=1)
    is_default: bool = True

    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        return validate_ssh_username(value)

    @field_validator("port")
    @classmethod
    def _check_port(cls, value: int) -> int:
        return validate_ssh_port(value)

    @field_validator("secret_value")
    @classmethod
    def _check_secret(cls, value: str, info: ValidationInfo) -> str:
        # `kind` steht in der Feldreihenfolge VOR `secret_value`; war es selbst ungueltig,
        # fehlt es hier und der Fehler dort reicht.
        if info.data.get("kind") == "ssh_key":
            return _validate_ssh_key(value)
        return value


class GroupOut(BaseModel):
    id: str
    name: str
    description: str

    @classmethod
    def from_model(cls, g: HostGroup) -> "GroupOut":
        return cls(id=g.id, name=g.name, description=g.description)


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field("", max_length=255)

    _strip_name = field_validator("name", mode="before")(_strip)


class GroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    description: str | None = Field(None, max_length=255)

    _strip_name = field_validator("name", mode="before")(_strip)


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------


@router.get("/hosts", dependencies=[Depends(require_permission("hosts.read"))])
async def list_hosts(
    session: SessionDep, tag: str | None = None, group: str | None = None, status_: str | None = None
) -> list[HostOut]:
    hosts = await hosts_service.list_hosts(session, tag=tag, group=group, status=status_)
    return [HostOut.from_model(h) for h in hosts]


@router.post("/hosts", status_code=status.HTTP_201_CREATED)
async def create_host(payload: HostCreate, session: SessionDep, user: WriteUser) -> HostOut:
    try:
        host = await hosts_service.create_host(
            session, name=payload.name, display_name=payload.display_name, address=payload.address,
            os_family=payload.os_family, kind=payload.kind, tags=payload.tags,
        )
    except hosts_service.HostServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _audit(
        session, user, "host.created", target_id=host.id,
        detail={
            "name": host.name, "address": host.address, "os_family": host.os_family, "kind": host.kind,
            "tags": [t.tag for t in host.tags],
        },
    )
    return HostOut.from_model(host)


@router.get("/hosts/{host_id}", dependencies=[Depends(require_permission("hosts.read"))])
async def get_host(host_id: str, session: SessionDep) -> HostOut:
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    return HostOut.from_model(host)


def _manual_tags(host: Host) -> list[str]:
    return sorted(t.tag for t in host.tags if t.managed_by_ext_id is None)


@router.patch("/hosts/{host_id}")
async def patch_host(host_id: str, payload: HostUpdate, session: SessionDep, user: WriteUser) -> HostOut:
    fields = payload.model_dump(exclude_unset=True)
    before = await session.get(Host, host_id)
    if before is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    old = {key: getattr(before, key) for key in ("display_name", "address", "os_family", "kind", "enabled", "is_managed")}
    old["tags"] = _manual_tags(before)
    host = await hosts_service.update_host(session, host_id, **fields)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    new = {key: getattr(host, key) for key in ("display_name", "address", "os_family", "kind", "enabled", "is_managed")}
    new["tags"] = _manual_tags(host)
    changed = {key: {"from": old[key], "to": new[key]} for key in old if old[key] != new[key]}
    if changed:
        await _audit(session, user, "host.updated", target_id=host.id, detail={"changed": changed})
    return HostOut.from_model(host)


@router.delete("/hosts/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_host(host_id: str, session: SessionDep, user: WriteUser) -> None:
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    detail = {"name": host.name, "address": host.address, "provider_ext_id": host.provider_ext_id}
    try:
        deleted = await hosts_service.delete_host(session, host_id)
    except hosts_service.HostBusyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    await _audit(session, user, "host.deleted", target_id=host_id, detail=detail)


class KnownHostKeyOut(BaseModel):
    key_type: str
    fingerprint: str
    first_seen_at: datetime
    accepted_by_user_id: str | None
    accepted_by_label: str | None
    """Name dessen, der den Schluessel bestaetigt hat; `null` bei automatischem Merken
    (erster Kontakt). Fremde Namen sieht nur, wer `users.read` oder Aktionen freigeben darf
    -- sonst steht hier `user/<id>` (wie bei den Aktionen)."""


@router.get("/hosts/{host_id}/known-hosts", dependencies=[Depends(require_permission("hosts.read"))])
async def list_known_host_keys(host_id: str, session: SessionDep, user: CurrentUser) -> list[KnownHostKeyOut]:
    """Die gemerkten Server-Schluessel (Fingerabdruecke) dieses Servers -- Grundlage, um
    einen geaenderten Schluessel bewusst zu vergessen (`DELETE .../known-hosts/{key_type}`)."""
    if await session.get(Host, host_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    rows = (
        await session.execute(select(KnownHostKey).where(KnownHostKey.host_id == host_id).order_by(KnownHostKey.key_type))
    ).scalars().all()
    names = await load_user_labels(
        session, [r.accepted_by_user_id for r in rows if r.accepted_by_user_id], viewer=user
    )
    return [
        KnownHostKeyOut(
            key_type=r.key_type, fingerprint=r.fingerprint, first_seen_at=r.first_seen_at,
            accepted_by_user_id=r.accepted_by_user_id,
            accepted_by_label=(
                None if r.accepted_by_user_id is None
                else names.get(r.accepted_by_user_id) or raw_actor("user", r.accepted_by_user_id)
            ),
        )
        for r in rows
    ]


@router.get("/hosts/{host_id}/status", dependencies=[Depends(require_permission("hosts.read"))])
async def get_host_status(host_id: str, session: SessionDep, settings: SettingsDep) -> HostStatusOut:
    """Versucht bei vorhandenen Zugangsdaten eine echte, kurze SSH-Pruefung; ohne
    Zugangsdaten (z. B. ein per `HostProvider` entdeckter Host, der ueber dessen
    eigene API statt SSH erreichbar ist -- WP-8) wird stattdessen dessen
    `HostProvider.host_status()` gefragt, falls einer registriert ist. Erst ganz ohne
    beides bleibt es beim zuletzt gespeicherten Status."""
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")

    credential = await hosts_service.default_credential(session, host_id)
    if credential is None:
        if host.provider_ext_id and host.provider_ref:
            provider = get_extension_runtime().capabilities.provided_by(HostProvider, host.provider_ext_id)
            if provider is not None:
                from ...db import utcnow

                try:
                    live_status = await provider.host_status(host.provider_ref)
                except Exception as exc:  # noqa: BLE001 - ein Provider-Fehler ist kein Server-Fehler, nur unbekannter Status
                    return HostStatusOut(
                        status=host.status, last_seen_at=host.last_seen_at, checked_live=False, detail=str(exc)
                    )
                host.status = live_status.value
                host.last_seen_at = utcnow()
                await session.flush()
                return HostStatusOut(status=host.status, last_seen_at=host.last_seen_at, checked_live=True)
        return HostStatusOut(status=host.status, last_seen_at=host.last_seen_at, checked_live=False)

    from ...db import utcnow

    try:
        target = await hosts_service.resolve_connection_target(session, settings, host, credential)
        conn = await ssh.get_ssh_pool().get(
            session, target, credential_id=credential.id, connect_timeout_s=settings.ssh_connect_timeout_s
        )
        exit_code, _, _ = await ssh.run(conn, "true", timeout_s=5.0)
        host.status = "up" if exit_code == 0 else "down"
        host.last_seen_at = utcnow()
        await session.flush()
        return HostStatusOut(status=host.status, last_seen_at=host.last_seen_at, checked_live=True)
    except (ssh.HostKeyMismatch, ssh.HostKeyUnknown) as exc:
        # Geaenderter oder (bei `ssh_confirm_new_host_keys`) noch nicht bestaetigter Server-Schluessel:
        # weder "oben" noch "unten", sondern ein Fall fuer "Verbindung pruefen".
        host.status = "unknown"
        await session.flush()
        return HostStatusOut(status=host.status, last_seen_at=host.last_seen_at, checked_live=True, detail=str(exc))
    except ssh.SshTargetChanged as exc:
        # Die Verbindungsdaten wurden gerade geaendert -- das heisst nicht "Server down".
        return HostStatusOut(status=host.status, last_seen_at=host.last_seen_at, checked_live=False, detail=str(exc))
    except (ssh.SshError, asyncssh.Error, OSError) as exc:
        # Eine tote gepoolte Verbindung wirft beim Befehl TimeoutError
        # (ein OSError), ConnectionLost oder ChannelOpenError statt SshError -- das
        # heisst genauso "nicht erreichbar", kein Serverfehler.
        host.status = "down"
        await session.flush()
        detail = str(exc) or "Der Server hat nicht rechtzeitig geantwortet."
        return HostStatusOut(status=host.status, last_seen_at=host.last_seen_at, checked_live=True, detail=detail)


@router.get("/hosts/{host_id}/metrics", dependencies=[Depends(require_permission("hosts.read"))])
async def get_host_metrics(host_id: str, session: SessionDep) -> HostMetricsOut:
    """Nicht in docs/04-API.md explizit gelistet -- Gap-Fill wie `/pages` in WP-7:
    das symmetrische Gegenstueck zu `/hosts/{id}/actions` (dort `HostProvider`,
    hier `MetricsProvider`), ohne das `MetricsProvider` erst mit WP-8 einen einzigen
    echten Aufrufer haette (kein "proxmox" im Kern: dieser Endpunkt kennt nur das
    Protokoll, nie eine konkrete Extension). Reine
    Momentaufnahme, keine Historie (docs/00-DECISIONS.md D-02)."""
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    sdk_host = hosts_service.host_to_sdk(host)
    provider = await resolve_metrics_provider(sdk_host, host.provider_ext_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein MetricsProvider für diesen Host registriert.")

    from ...db import utcnow

    values = await provider.sample(sdk_host)
    return HostMetricsOut(values=values, sampled_at=utcnow())


def _percent(used: float | None, total: float | None) -> float | None:
    if used is None or not total or total <= 0:
        return None
    return round(min(100.0, max(0.0, 100 * used / total)), 1)


@router.get("/hosts/metrics/latest", dependencies=[Depends(require_permission("hosts.read"))])
async def get_hosts_latest_metrics(session: SessionDep) -> HostsLatestMetricsOut:
    """Letzte Messwerte ALLER Server in einer Abfrage -- fuer die Auslastungs-Karten im Cockpit.

    Liest nur den schon gefuehrten Verlauf (`metrics.db`, Sammler alle 30 s), nie per SSH: Das Cockpit
    loest damit keine Messung aus und wird mit mehr Servern nicht langsamer. Nur Server mit einem Wert
    der letzten `LATEST_WINDOW_S` Sekunden stehen drin; `stale` ab `STALE_AFTER_S`. Server, deren Anbieter
    eine eigene Historie fuehrt (Hypervisor-Knoten), kommen hier nicht vor -- sie werden live gefragt."""
    import time as _time

    from ...core.metrics_history import LATEST_WINDOW_S, STALE_AFTER_S, get_metrics_collector

    host_ids = list((await session.execute(select(Host.id).where(Host.enabled.is_(True)))).scalars().all())
    now = int(_time.time())
    found = await asyncio.to_thread(get_metrics_collector().store.latest, host_ids, now, max_age_s=LATEST_WINDOW_S)
    out: dict[str, HostLatestMetricsOut] = {}
    for host_id, values in found.items():
        at = max(ts for ts, _ in values.values())
        v = {name: value for name, (_, value) in values.items()}
        cpu = v.get("cpu_percent")
        age = max(0, now - at)
        out[host_id] = HostLatestMetricsOut(
            cpu=None if cpu is None else round(min(100.0, max(0.0, cpu)), 1),
            mem=_percent(v.get("mem_used_bytes"), v.get("mem_total_bytes")),
            disk=_percent(v.get("root_used_bytes"), v.get("root_total_bytes")),
            mem_used_bytes=v.get("mem_used_bytes"), mem_total_bytes=v.get("mem_total_bytes"),
            disk_used_bytes=v.get("root_used_bytes"), disk_total_bytes=v.get("root_total_bytes"),
            uptime_s=v.get("uptime_s"),
            at=datetime.fromtimestamp(at, UTC), age_s=age, stale=age > STALE_AFTER_S,
        )
    return HostsLatestMetricsOut(hosts=out, stale_after_s=STALE_AFTER_S)


class HostMetricsHistoryOut(BaseModel):
    range: str
    source: Literal["provider", "lattice"]
    step_s: int
    timestamps: list[int]
    series: dict[str, list[float | None]]
    max: dict[str, list[float | None]] = Field(default_factory=dict)


@router.get("/hosts/{host_id}/metrics/history", dependencies=[Depends(require_permission("hosts.read"))])
async def get_host_metrics_history(host_id: str, session: SessionDep, range: str = "1h") -> HostMetricsHistoryOut:  # noqa: A002 - Query-Name
    """Verlauf fuer die Diagramme der Server-Seite. Hat der Anbieter eigene Historie
    (`MetricsProvider.history()`, z. B. Proxmox-RRD), kommt sie von dort -- sonst aus
    dem eigenen Sammler von Nodvard Deck (core/metrics_history.py, D-02-Nachtrag)."""
    import time as _time

    from ...core.metrics_history import RANGES, get_metrics_collector

    if range not in RANGES:
        raise HTTPException(status_code=422, detail=f"Zeitraum: {', '.join(RANGES)}.")
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    sdk_host = hosts_service.host_to_sdk(host)
    provider = await resolve_metrics_provider(sdk_host, host.provider_ext_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein MetricsProvider für diesen Host registriert.")
    history = getattr(provider, "history", None)
    if history is not None:
        try:
            data = await history(sdk_host, range)
        except Exception as exc:  # noqa: BLE001 - Anbieter-Fehler ist kein Server-Fehler
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Verlauf nicht abrufbar: {str(exc) or type(exc).__name__}") from exc
        return HostMetricsHistoryOut(range=range, source="provider", **data)
    collector = get_metrics_collector()
    data = await asyncio.to_thread(
        collector.store.query, host.id, range, int(_time.time()),
        interval_s=max(collector.interval_s, 1), raw_retention_s=collector.raw_retention_s,
    )
    return HostMetricsHistoryOut(range=range, source="lattice", **data)


# ---------------------------------------------------------------------------
# Aktionen -- WP-5, gehen durch dasselbe core.gate wie ctx.actions.propose()
# ---------------------------------------------------------------------------


async def _resolve_action_spec(host: Host, action_type: str) -> Any | None:
    """Generischer Katalog zuerst (`terminal`s `shell.exec` steht hier), dann der
    `HostProvider` des Hosts, falls einer existiert -- in dieser Runde erfuellt keine
    Extension `HostProvider` wirklich (kein Proxmox/Docker-Discovery in Phase 1), der
    Pfad ist trotzdem verdrahtet und getestet (mit einem Test-Double), nicht nur
    angenommen."""
    runtime = get_extension_runtime()
    entry = runtime.actions.get(action_type)
    if entry is not None:
        return entry[1] if _offered_for(entry[1], host) else None
    if host.provider_ext_id:
        provider = runtime.capabilities.provided_by(HostProvider, host.provider_ext_id)
        if provider is not None:
            for spec in await provider.host_actions(host.provider_ref):
                if spec.action_type == action_type:
                    return spec
    return None


def _offered_for(spec: Any, host: Host) -> bool:  # noqa: ANN401 - nodvard_sdk.actions.ActionSpec
    """Liste UND Ausloesen pruefen dasselbe: `host_bound=False` heisst "nur
    ueber die Extension selbst" (ctx.actions.propose, z. B. mit Befehlen, die sie aus
    eigenen Feldern baut) -- nie als Formular auf der Server-Seite, nie per POST hier.
    `ActionSpec.host_tags`: nur fuer Hosts mit mindestens einem dieser Tags."""
    if not getattr(spec, "host_bound", True):
        return False
    wanted = getattr(spec, "host_tags", None) or []
    return not wanted or bool({t.tag for t in host.tags} & set(wanted))


@router.get("/hosts/{host_id}/actions", dependencies=[Depends(require_permission("hosts.read"))])
async def list_host_actions(host_id: str, session: SessionDep) -> list[ActionSpecOut]:
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")

    runtime = get_extension_runtime()
    specs = [ActionSpecOut.from_spec(s) for s in runtime.actions.all_host_bound() if _offered_for(s, host)]
    if host.provider_ext_id:
        provider = runtime.capabilities.provided_by(HostProvider, host.provider_ext_id)
        if provider is not None:
            known = {s.action_type for s in specs}
            specs.extend(
                ActionSpecOut.from_spec(s, source="host")
                for s in await provider.host_actions(host.provider_ref)
                if s.action_type not in known
            )
    return specs


CATEGORY_ORDER = {"control": 0, "monitoring": 1, "services": 2, "data": 3, "settings": 4}


class HostToolOut(BaseModel):
    ext_id: str
    id: str
    title: str
    description: str | None
    icon: str | None
    category: str
    href: str
    order: int


@router.get("/hosts/{host_id}/tools", dependencies=[Depends(require_permission("hosts.read"))])
async def list_host_tools(host_id: str, session: SessionDep, user: CurrentUser) -> list[HostToolOut]:
    """Werkzeug-Kacheln der Server-Seite (Plesk-Stil): was Extensions per
    `ctx.ui.register_host_tool()` fuer passende Hosts anbieten -- gefiltert nach
    Host-Art, Tags, Herkunft und den Rechten des Nutzers. Der Kern kennt dabei keine
    Extension namentlich (docs/02-EXTENSION-API.md)."""
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")
    tags = {t.tag for t in host.tags}
    tools: list[HostToolOut] = []
    for ext_id, spec in get_extension_runtime().ui.all_host_tools():
        if spec.kinds and host.kind not in spec.kinds:
            continue
        if spec.tags and not tags.intersection(spec.tags):
            continue
        if spec.own_hosts_only and host.provider_ext_id != ext_id:
            continue
        if spec.os_families and host.os_family not in spec.os_families:
            continue
        if any(not user_has_permission(user, perm) for perm in spec.permissions):
            continue
        tools.append(HostToolOut(
            ext_id=ext_id, id=spec.id, title=spec.title, description=spec.description, icon=spec.icon,
            category=spec.category, href=f"/ext/{ext_id}{spec.path.replace('{host_id}', host.id)}", order=spec.order,
        ))
    tools.sort(key=lambda t: (CATEGORY_ORDER.get(t.category, 9), t.order, t.title))
    return tools


@router.post("/hosts/{host_id}/actions/{action_type}")
async def trigger_host_action(
    host_id: str,
    action_type: str,
    payload: TriggerActionIn,
    session: SessionDep,
    user: CurrentUser,
    response: Response,
    wait: float | None = Query(None, ge=0, le=gate_service.WAIT_S),
) -> ActionOut:
    """Ein Nutzer loest eine Aktion direkt aus (docs/04-API.md §3) -- `proposed_by`
    ist `Actor.user(...)`, sonst identisch zu `ctx.actions.propose()`: dieselbe
    Sperrliste, dasselbe Anti-Flapping, dieselbe Autonomie-Entscheidung (die bei einem
    Nutzer nur relevant waere, wenn `autonomy.mode=full` UND niemand die Bestaetigung
    erzwingt -- praktisch heisst das: ein Nutzerklick fuehrt bei low-risk-Aktionen im
    Vollautonomie-Modus sofort aus, sonst landet er wie ein KI-Vorschlag als
    `proposed`).

    Wird sofort ausgefuehrt, laeuft das im Hintergrund: fertig innerhalb
    von `wait` Sekunden (Standard 20) -> 200, sonst 202 mit Status 'executing'."""
    host = await session.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannter Host.")

    spec = await _resolve_action_spec(host, action_type)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aktion '{action_type}' ist auf diesem Host nicht verfügbar.",
        )

    required_permissions = spec.permissions or ["hosts.execute"]
    for perm in required_permissions:
        if not user_has_permission(user, perm):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Berechtigung '{perm}' fehlt.")

    request = ActionRequest(
        action_type=action_type,
        payload=payload.payload,
        host_ref=host_id,
        risk=spec.default_risk if isinstance(spec.default_risk, Risk) else Risk(spec.default_risk),
        proposed_by=Actor.user(user.id, user.username),
        reason=payload.reason,
        correlation_id=payload.correlation_id,
    )
    decision = await gate_service.propose(
        session, ext_id="core", request=request, command_field=spec.command_field,
        wait_s=gate_service.WAIT_S if wait is None else min(wait, gate_service.WAIT_S),
    )

    action = await session.get(Action, decision.action_id)
    assert action is not None  # core.gate legt IMMER eine Zeile an, auch bei Ablehnung
    if decision.outcome == GateOutcome.DENY:
        response.status_code = status.HTTP_403_FORBIDDEN
    elif decision.outcome == GateOutcome.ALLOW:
        response.status_code = status.HTTP_200_OK
        mark_running(response, action)
    else:
        response.status_code = status.HTTP_202_ACCEPTED
    return await action_out(session, action, user)


# ---------------------------------------------------------------------------
# Zugangsdaten
# ---------------------------------------------------------------------------


@router.get("/hosts/{host_id}/credentials", dependencies=[Depends(require_permission("hosts.read"))])
async def list_credentials(host_id: str, session: SessionDep) -> list[CredentialOut]:
    return [CredentialOut.from_model(c) for c in await hosts_service.list_credentials(session, host_id)]


@router.post(
    "/hosts/{host_id}/credentials",
    status_code=status.HTTP_201_CREATED,
)
async def create_credential(
    host_id: str,
    payload: CredentialCreate,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    user: WriteUser,
) -> CredentialOut:
    try:
        credential = await hosts_service.add_credential(
            session, settings, host_id=host_id, kind=payload.kind, username=payload.username,
            port=payload.port, secret_value=payload.secret_value, is_default=payload.is_default,
            created_by_user_id=user.id,
        )
    except hosts_service.HostServiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await _audit(
        session, user, "host.credential_added", target_id=host_id,
        detail={
            "credential_id": credential.id, "kind": credential.kind, "username": credential.username,
            "port": credential.port, "is_default": credential.is_default,
        },
    )
    if credential.kind in ("ssh_key", "ssh_password"):
        await extensions_service.auto_enable_for(request.app, session, settings, "host_credential", user_id=user.id)
    return CredentialOut.from_model(credential)


@router.delete("/hosts/{host_id}/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(host_id: str, credential_id: str, session: SessionDep, user: WriteUser) -> None:
    credential = await session.get(HostCredential, credential_id)
    if credential is None or credential.host_id != host_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Zugangsdaten.")
    detail = {
        "credential_id": credential.id, "kind": credential.kind, "username": credential.username,
        "port": credential.port,
    }
    if not await hosts_service.delete_credential(session, host_id, credential_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Zugangsdaten.")
    await _audit(session, user, "host.credential_deleted", target_id=host_id, detail=detail)


@router.delete("/hosts/{host_id}/known-hosts/{key_type}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_known_host_key(host_id: str, key_type: str, session: SessionDep, user: WriteUser) -> None:
    """Admin-Weg, eine per `HostKeyMismatch` sichtbar gewordene Aenderung NACH
    manueller Pruefung erneut zuzulassen (docs/00 D-05)."""
    known = (
        await session.execute(
            select(KnownHostKey).where(KnownHostKey.host_id == host_id, KnownHostKey.key_type == key_type)
        )
    ).scalars().first()
    detail = {"key_type": key_type, "fingerprint": known.fingerprint if known is not None else None}
    if not await hosts_service.clear_known_host_key(session, host_id, key_type):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kein gespeicherter Schlüssel dieses Typs.")
    await _audit(session, user, "host.known_key_forgotten", target_id=host_id, detail=detail)


# ---------------------------------------------------------------------------
# Gruppen
# ---------------------------------------------------------------------------


@router.get("/host-groups", dependencies=[Depends(require_permission("hosts.read"))])
async def list_groups(session: SessionDep) -> list[GroupOut]:
    return [GroupOut.from_model(g) for g in await hosts_service.list_groups(session)]


@router.post("/host-groups", status_code=status.HTTP_201_CREATED)
async def create_group(payload: GroupCreate, session: SessionDep, user: WriteUser) -> GroupOut:
    try:
        group = await hosts_service.create_group(session, name=payload.name, description=payload.description)
    except IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Gruppenname bereits vergeben.") from exc
    await _audit_group(session, user, group.id, "created", name=group.name)
    return GroupOut.from_model(group)


async def _audit_group(session: SessionDep, user: User, group_id: str, change: str, **detail: Any) -> None:
    await _audit(
        session, user, "host.group_changed", target_id=group_id, target_type="host_group",
        detail={"change": change, **detail},
    )


@router.patch("/host-groups/{group_id}")
async def patch_group(group_id: str, payload: GroupUpdate, session: SessionDep, user: WriteUser) -> GroupOut:
    existing = await session.get(HostGroup, group_id)
    old_name = existing.name if existing is not None else None
    old_description = existing.description if existing is not None else None
    try:
        group = await hosts_service.update_group(session, group_id, **payload.model_dump(exclude_unset=True))
    except hosts_service.HostServiceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Gruppe.")
    if group.name != old_name:
        # Wurde die Beschreibung im selben Zug geaendert, steht das im selben Eintrag.
        extra = {"description_changed": True} if group.description != old_description else {}
        await _audit_group(session, user, group.id, "renamed", **{"from": old_name, "to": group.name}, **extra)
    elif group.description != old_description:
        await _audit_group(session, user, group.id, "description_changed", name=group.name)
    return GroupOut.from_model(group)


@router.delete("/host-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group_id: str, session: SessionDep, user: WriteUser) -> None:
    """Loescht nur die Gruppe und ihre Zuordnungen, nie die Server darin. Skripte, die
    die Gruppe als Ziel hatten, loesen sie danach zu einer leeren Liste auf."""
    existing = await session.get(HostGroup, group_id)
    name = existing.name if existing is not None else None
    if not await hosts_service.delete_group(session, group_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unbekannte Gruppe.")
    await _audit_group(session, user, group_id, "deleted", name=name)


@router.post("/host-groups/{group_id}/members/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_group_member(group_id: str, host_id: str, session: SessionDep, user: WriteUser) -> None:
    try:
        added = await hosts_service.add_group_member(session, group_id, host_id)
    except hosts_service.HostServiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if added:
        await _audit_group(session, user, group_id, "member_added", host_id=host_id)


@router.delete("/host-groups/{group_id}/members/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_group_member(group_id: str, host_id: str, session: SessionDep, user: WriteUser) -> None:
    if await hosts_service.remove_group_member(session, group_id, host_id):
        await _audit_group(session, user, group_id, "member_removed", host_id=host_id)
