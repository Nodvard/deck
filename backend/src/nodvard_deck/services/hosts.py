"""Verwaltung von Servern, Gruppen und Zugangsdaten (reine DB-Verdrahtung, keine HTTP-Kenntnis)."""

from __future__ import annotations

from datetime import datetime

from nodvard_sdk import Host as SdkHost
from nodvard_sdk import HostStatus
from nodvard_sdk.actions import ActionStatus
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import ssh, vault
from ..db import utcnow
from ..models import (
    Action,
    Host,
    HostCredential,
    HostGroup,
    HostTag,
    KnownHostKey,
    Setting,
    host_group_members,
)
from . import custom_apps
from . import settings as settings_service

_HOST_STATUS_VALUES = {s.value for s in HostStatus}


CONFIRM_NEW_KEYS_SETTING = "ssh.confirm_new_host_keys"
"""Globale Einstellung (Tabelle `settings`): neue Server-Schlüssel nur nach Bestätigung merken.
Der Bootstrap des ersten Kontos schaltet sie bei neuen Installationen ein; fehlt sie (bestehende Installationen), gilt `False`."""

# `core/ssh.connect` prüft den Merker zusätzlich direkt vor dem Verbindungsaufbau.
HOST_KEY_CONFIRM_REQUIRED = ssh.HOST_KEY_CONFIRM_REQUIRED
"""Merker je Server (Tabelle `settings`, `scope="host"`, `user_id=<host_id>`): Nach "Schlüssel vergessen" bekommt der
Server nie wieder still einen Schlüssel untergeschoben, erst die Bestätigung (`POST /hosts/{id}/known-hosts`) nimmt ihn
weg. Nicht in `Host.host_metadata`: Das überschreibt der Abgleich der Anbieter-Erweiterungen, und Erweiterungen sehen es."""
_HOST_SCOPE = ssh.HOST_KEY_CONFIRM_SCOPE


LOGIN_OK_KEY = "login_ok"
"""Schlüssel in `Host.host_metadata`: die letzte erfolgreiche SSH-Anmeldung. Der Erreichbarkeits-Job
setzt `status`/`last_seen_at` schon, wenn nur der Port antwortet -- belegt ist eine Anmeldung erst
hiermit."""


def record_login_ok(host: Host, credential: HostCredential, at: datetime | None = None) -> None:
    """Hält dauerhaft fest: die Anmeldung mit `credential` an `host` hat geklappt (`at`: wann, sonst jetzt).
    Es gibt nur diesen einen Beleg je Server, er gehört zum Standard-Zugang -- ein anderer Zugang
    darf ihn nicht überschreiben (`login_confirmed_at` vergleicht den Zugang)."""
    record = {
        "credential_id": credential.id, "address": host.address, "port": credential.port,
        "at": (at or utcnow()).isoformat(),
    }
    host.host_metadata = {**(host.host_metadata or {}), LOGIN_OK_KEY: record}


def clear_login_ok(host: Host, credential_id: str | None = None) -> None:
    """Die Anmeldung hat zuletzt nicht geklappt: der gespeicherte Beleg gilt nicht mehr. Mit `credential_id`
    nur, wenn der Beleg zu genau diesem Zugang gehört -- scheitert ein anderer Zugang, bleibt der
    Beleg des funktionierenden stehen."""
    record = (host.host_metadata or {}).get(LOGIN_OK_KEY)
    if record is None:
        return
    if credential_id is not None and not (isinstance(record, dict) and record.get("credential_id") == credential_id):
        return
    host.host_metadata = {k: v for k, v in host.host_metadata.items() if k != LOGIN_OK_KEY}


def login_confirmed_at(host: Host, credential: HostCredential | None) -> datetime | None:
    """Wann hat sich das Dashboard zuletzt per SSH angemeldet, mit genau diesem Zugang, an dieser Adresse und diesem Port?
    Sonst `None` (nie, oder der Zugang bzw. die Adresse wurde seitdem geändert)."""
    record = (host.host_metadata or {}).get(LOGIN_OK_KEY)
    if credential is None or not isinstance(record, dict):
        return None
    if record.get("credential_id") != credential.id or record.get("address") != host.address or record.get("port") != credential.port:
        return None
    try:
        return datetime.fromisoformat(str(record.get("at")))
    except ValueError:
        return None


class HostServiceError(Exception):
    pass


class HostBusyError(HostServiceError):
    """Auf dem Host läuft gerade eine Aktion; Löschen wäre mitten im Lauf."""


async def drop_pooled_connections(session: AsyncSession, host_id: str) -> None:
    """Schließt offene SSH-Verbindungen zu `host_id` sofort und nach dem Commit noch einmal (`ssh.drop_host_after_commit`)."""
    await ssh.get_ssh_pool().drop_host(host_id)
    ssh.drop_host_after_commit(session, host_id)


async def forget_address_bound_access(session: AsyncSession, host: Host, *, spare_pinned: bool = False) -> int:
    """Löscht nach einer geänderten Adresse die gespeicherten SSH-Passwörter und schließt offene Verbindungen; liefert die
    Zahl der gelöschten Passwörter. Mit `spare_pinned` bleiben sie, wenn für den Server schon ein Schlüssel gemerkt ist."""
    from ..db import refresh_relationships

    removed = 0
    # Ein Passwort gehört zu dem Server, bei dem es eingegeben wurde; unter einer neuen Adresse würde es sonst bei jemand
    # anderem landen. Schlüssel bleiben (der Server prüft ihn, das Dashboard verrät ihn nicht). Mit `spare_pinned` (für
    # Adressen, die der Anbieter selbst führt, z. B. DHCP) bleiben auch Passwörter: Die Anmeldung beginnt erst nach dem
    # Schlüsseltausch (`core/ssh.connect`), ein anderer Server an der neuen Adresse zeigt einen anderen Schlüssel und
    # bekommt nie ein Passwort zu sehen.
    if not (spare_pinned and await ssh.load_known_fingerprints(session, host.id)):
        await refresh_relationships(session, host, "credentials")
        for credential in [c for c in host.credentials if c.kind == "ssh_password"]:
            if await delete_credential(session, host.id, credential.id):
                removed += 1
    # Eine gepoolte Verbindung ginge sonst weiter an die alte Adresse.
    await drop_pooled_connections(session, host.id)
    return removed


def host_to_sdk(host: Host) -> SdkHost:
    """Kern-ORM -> SDK-Vertragstyp; die einzige Stelle, die das tut, damit `ext/context.py` und `api/v1/terminal.py` dieselbe
    Umwandlung benutzen."""
    status = HostStatus(host.status) if host.status in _HOST_STATUS_VALUES else HostStatus.UNKNOWN
    return SdkHost(
        id=host.id,
        name=host.name,
        display_name=host.display_name,
        address=host.address,
        os_family=host.os_family,
        kind=host.kind,
        status=status,
        tags=[t.tag for t in host.tags],
        provider_ext_id=host.provider_ext_id,
        provider_ref=host.provider_ref,
        is_managed=host.is_managed,
        metadata=host.host_metadata or {},
        # `host.credentials` ist wie `host.tags` `lazy="selectin"`, der Zugriff braucht keine neue Query. Erweiterungen
        # sehen nie den Wert eines Zugangs, nur ob einer existiert (damit z. B. eine Datei- oder Terminal-Quelle vorab
        # entscheiden kann, ob sie nutzbar ist).
        has_credential=any(c.is_default for c in host.credentials),
        # Nur der Benutzername (kein Geheimnis): z. B. erlischt eine Dauerfreigabe der
        # Skripte, wenn ein Server danach unter einem anderen Konto angesprochen wird.
        credential_username=next((c.username for c in host.credentials if c.is_default), None),
        # Ebenso der Port: ein anderer Port ist ein anderer Dienst (gleiche Adresse, anderes Ziel).
        credential_port=next((c.port for c in host.credentials if c.is_default), None),
    )


async def create_host(
    session: AsyncSession,
    *,
    name: str,
    display_name: str = "",
    address: str,
    os_family: str = "linux",
    kind: str | None = None,
    tags: list[str] | None = None,
) -> Host:
    existing = await session.execute(select(Host.id).where(Host.name == name))
    if existing.first() is not None:
        raise HostServiceError(f"Host '{name}' existiert bereits.")

    host = Host(
        name=name, display_name=display_name or name, address=address,
        os_family=os_family, kind=kind,
    )
    session.add(host)
    await session.flush()
    for tag in tags or []:
        session.add(HostTag(host_id=host.id, tag=tag))
    await session.flush()
    # Immer neu laden, auch ohne Tags: Ein frisch angelegter Host hat `.tags` nie geladen, sonst gibt es beim ersten
    # `HostOut.from_model()` ein MissingGreenlet.
    from ..db import refresh_relationships

    await refresh_relationships(session, host, "tags", "credentials")
    return host


async def list_hosts(
    session: AsyncSession, *, tag: str | None = None, group: str | None = None, status: str | None = None
) -> list[Host]:
    stmt = select(Host)
    if tag:
        stmt = stmt.join(HostTag).where(HostTag.tag == tag)
    if group:
        stmt = stmt.join(host_group_members).where(host_group_members.c.group_id == group)
    if status:
        stmt = stmt.where(Host.status == status)
    result = await session.execute(stmt.order_by(Host.name))
    return list(result.scalars().unique().all())


async def update_host(session: AsyncSession, host_id: str, **fields: object) -> Host | None:
    from ..db import refresh_relationships

    host = await session.get(Host, host_id)
    if host is None:
        return None
    tags = fields.pop("tags", None)
    old_address = host.address
    for key, value in fields.items():
        if value is not None and hasattr(host, key):
            setattr(host, key, value)
    address_changed = host.address != old_address
    if tags is not None:
        await refresh_relationships(session, host, "tags")
        wanted = set(tags)  # type: ignore[arg-type]
        # Nur manuelle Tags (managed_by_ext_id None) anfassen; Tags einer Erweiterung bleiben und werden nicht doppelt angelegt.
        taken = {t.tag for t in host.tags if t.managed_by_ext_id is not None}
        for row in list(host.tags):
            if row.managed_by_ext_id is None and row.tag not in wanted:
                host.tags.remove(row)
        present = {t.tag for t in host.tags}
        for tag in sorted(wanted - taken - present):
            host.tags.append(HostTag(tag=tag))
    await session.flush()
    # Immer beide neu laden: `HostOut` zeigt Markierungen und Zugangs-Zusammenfassung, und ein Zugang, der erst nach dem
    # Laden des Hosts angelegt wurde, steht sonst nicht in der gecachten Collection.
    await refresh_relationships(session, host, "tags", "credentials")
    if address_changed:
        await forget_address_bound_access(session, host)
    return host


async def delete_host(session: AsyncSession, host_id: str) -> bool:
    """Löscht den Server samt Zugangsdaten und Markierungen; `False`, wenn er unbekannt ist, `HostBusyError`, wenn auf ihm
    gerade eine Aktion läuft."""
    from ..db import refresh_relationships

    host = await session.get(Host, host_id)
    if host is None:
        return False
    # Erst einen wirkungslosen Schreibzugriff, dann prüfen: SQLite vergibt die Schreibsperre nur einmal, so kann zwischen
    # Prüfung und Löschen keine Aktion mehr auf "executing" wechseln (TOCTOU). Nach dem Löschen fände der Check nichts
    # mehr, weil `actions.host_id` dann NULL ist.
    await session.execute(update(Host).where(Host.id == host_id).values(name=Host.name))
    running = await session.execute(
        select(Action.id)
        .where(Action.host_id == host_id, Action.status == ActionStatus.EXECUTING.value)
        .limit(1)
    )
    if running.first() is not None:
        raise HostBusyError("Auf diesem Server läuft gerade eine Aktion. Bitte warten, bis sie fertig ist.")
    # Frisch nachladen: Wurde ein Zugang per Fremdschlüssel einem anderen Server zugeordnet, kennt die gecachte Collection
    # (`lazy="selectin"`, kein `back_populates`) ihn noch hier, und die Kaskade würde ihn mitlöschen.
    await refresh_relationships(session, host, "credentials", "tags")
    # Die Secret-IDs vor dem Löschen merken: `secrets` hat einen RESTRICT-Fremdschlüssel von den Zugangsdaten aus, das
    # Secret darf also erst nach den Zugangsdaten weg.
    secret_ids = [c.secret_id for c in host.credentials]
    # Eigene App-Kacheln mit diesem Server als Bezug bleiben stehen, nur ohne Server (nicht vom SQLite-Pragma abhängig).
    await custom_apps.unlink_host(session, host_id)
    await host_key_was_confirmed(session, host_id)
    await session.delete(host)
    # Ohne Flush bliebe `host` in der Identity-Map auffindbar, und ein zweiter `delete_host()`-Aufruf im selben Request
    # fände ihn noch einmal.
    await session.flush()
    for secret_id in secret_ids:
        await vault.delete_secret(session, secret_id)
    await session.flush()
    await drop_pooled_connections(session, host_id)
    return True


async def add_credential(
    session: AsyncSession,
    settings: Settings,
    *,
    host_id: str,
    kind: str,
    username: str,
    port: int,
    secret_value: str,
    is_default: bool = True,
    created_by_user_id: str | None = None,
    description: str | None = None,
) -> HostCredential:
    host = await session.get(Host, host_id)
    if host is None:
        raise HostServiceError(f"Host '{host_id}' existiert nicht.")

    keyring = vault.load_keyring(settings)
    secret = await vault.create_secret(
        session, keyring,
        label=f"host-cred:{host_id}:{security_suffix()}",
        kind=kind,
        plaintext=secret_value,
        description=(description or f"SSH-Zugangsdaten für Host '{host.name}'")[:255],
        created_by_user_id=created_by_user_id,
    )

    if is_default:
        existing = await session.execute(
            select(HostCredential).where(HostCredential.host_id == host_id, HostCredential.is_default.is_(True))
        )
        for row in existing.scalars().all():
            row.is_default = False

    credential = HostCredential(
        host_id=host_id, kind=kind, username=username, port=port,
        secret_id=secret.id, is_default=is_default,
    )
    session.add(credential)
    await session.flush()
    # Die schon geladene Collection des Hosts nachziehen (kein back_populates), sonst
    # zeigt `HostOut`/`has_credential` im selben Session-Kontext weiter "kein Zugang".
    from ..db import refresh_relationships

    await refresh_relationships(session, host, "credentials")
    return credential


def security_suffix() -> str:
    from ..core import security

    return security.generate_opaque_token()[:8]


async def list_credentials(session: AsyncSession, host_id: str) -> list[HostCredential]:
    result = await session.execute(select(HostCredential).where(HostCredential.host_id == host_id))
    return list(result.scalars().all())


async def delete_credential(session: AsyncSession, host_id: str, credential_id: str) -> bool:
    credential = await session.get(HostCredential, credential_id)
    if credential is None or credential.host_id != host_id:
        return False
    await vault.delete_secret(session, credential.secret_id)
    await session.delete(credential)
    # Siehe delete_host(): Ohne Flush wären Secret und Zugangszeile noch über die Identity-Map auffindbar.
    await session.flush()
    host = await session.get(Host, host_id)
    if host is not None:
        from ..db import refresh_relationships

        await refresh_relationships(session, host, "credentials")
    # Eine noch offene Verbindung mit dem gelöschten Zugang darf nicht weiterlaufen.
    await drop_pooled_connections(session, host_id)
    return True


async def make_default_credential(
    session: AsyncSession, host_id: str, credential_id: str, *, delete_previous: bool = False
) -> tuple[HostCredential, HostCredential | None] | None:
    """Macht einen Zugang zum Standard (Schlüsselwechsel: erst den neuen prüfen, dann umstellen). Mit `delete_previous`
    fällt der bisherige Standard samt Geheimnis in derselben Transaktion weg. Rückgabe: `(Zugang, gelöschter alter
    Standard oder None)`, `None` bei unbekanntem Zugang."""
    from ..db import refresh_relationships

    credential = await session.get(HostCredential, credential_id)
    if credential is None or credential.host_id != host_id:
        return None
    host = await session.get(Host, host_id)
    assert host is not None  # der Zugang verweist per Fremdschlüssel darauf
    others = (
        await session.execute(
            select(HostCredential).where(
                HostCredential.host_id == host_id, HostCredential.is_default.is_(True), HostCredential.id != credential.id
            )
        )
    ).scalars().all()
    previous = others[0] if others else None
    if credential.is_default and not others:
        # Schon der Standard: nichts ändern, auch `delete_previous` löscht dann nichts.
        return credential, None
    for row in others:
        row.is_default = False
    credential.is_default = True
    deleted: HostCredential | None = None
    if delete_previous and previous is not None:
        # Erst die Zugangszeile, dann das Geheimnis (RESTRICT-Fremdschlüssel, siehe delete_host).
        deleted = previous
        secret_id = previous.secret_id
        await session.delete(previous)
        await session.flush()
        await vault.delete_secret(session, secret_id)
    await session.flush()
    await refresh_relationships(session, host, "credentials")
    if deleted is not None:
        # Der gelöschte Zugang darf keine Verbindung mehr offenhalten.
        await drop_pooled_connections(session, host_id)
    else:
        # Nichts wurde gelöscht: neue Aufrufer verbinden mit dem neuen Standard, laufende Terminals und Live-Protokolle
        # dürfen in Ruhe zu Ende laufen (Gnadenfrist).
        ssh.get_ssh_pool().retire_host(host_id)
    return credential, deleted


async def default_credential(session: AsyncSession, host_id: str) -> HostCredential | None:
    result = await session.execute(
        select(HostCredential).where(HostCredential.host_id == host_id, HostCredential.is_default.is_(True))
    )
    return result.scalars().first()


async def resolve_connection_target(
    session: AsyncSession, settings: Settings, host: Host, credential: HostCredential
) -> ssh.ConnectionTarget:
    """Löst die Zugangsdaten-Referenz über den Vault auf; der Klartext lebt nur kurz im Speicher dieses Aufrufs."""
    # Stand des Pool-Zählers festhalten: Wird der Host danach geändert (drop_host), erkennt `SshPool.get()` dieses Ziel
    # als veraltet. Host und Zugang hat der Aufrufer schon vorher gelesen; dieses kleine Fenster deckt der Drop nach dem
    # Commit ab (`drop_pooled_connections`).
    generation = ssh.get_ssh_pool().generation(host.id)
    keyring = vault.load_keyring(settings)
    plaintext = await vault.read_secret_plaintext(session, keyring, credential.secret_id)
    return ssh.ConnectionTarget(
        host_id=host.id, address=host.address, port=credential.port,
        username=credential.username, kind=credential.kind, secret_value=plaintext,
        generation=generation,
        # Muss der Schlüssel bestätigt werden, merken Hintergrundjobs, Terminal und Status keinen neuen Server-Schlüssel
        # mehr still, nur "Verbindung prüfen" (Bestätigen) tut es.
        allow_tofu=not await host_key_confirmation_required(session, settings, host.id),
    )


async def host_key_confirmation_required(session: AsyncSession, settings: Settings, host_id: str) -> bool:
    """Darf für diesen Server ein unbekannter Schlüssel nicht still gemerkt werden?"""
    # Die erste Antwort gilt: Merker am Server (nach "Schlüssel vergessen", auch gegen die Umgebungsvariable), dann
    # `NODVARD_DECK_SSH_CONFIRM_NEW_HOST_KEYS` (`true`/`false`), dann die Einstellung `ssh.confirm_new_host_keys`
    # (neue Installationen: an), sonst nein.
    if await ssh.host_key_confirm_pending(session, host_id):
        return True
    if settings.ssh_confirm_new_host_keys is not None:
        return settings.ssh_confirm_new_host_keys
    return await settings_service.get_global(session, CONFIRM_NEW_KEYS_SETTING, False) is True


async def require_host_key_confirmation(session: AsyncSession, host_id: str) -> None:
    """Setzt den Merker (siehe `HOST_KEY_CONFIRM_REQUIRED`); in derselben Sitzung wie das Vergessen."""
    if await session.get(Setting, (HOST_KEY_CONFIRM_REQUIRED, _HOST_SCOPE, host_id)) is None:
        session.add(Setting(key=HOST_KEY_CONFIRM_REQUIRED, scope=_HOST_SCOPE, user_id=host_id, value={"value": True}))
        await session.flush()


async def host_key_was_confirmed(session: AsyncSession, host_id: str) -> None:
    """Nimmt den Merker weg. Nur der Bestätigen-Weg (`POST /hosts/{id}/known-hosts`) ruft das auf."""
    row = await session.get(Setting, (HOST_KEY_CONFIRM_REQUIRED, _HOST_SCOPE, host_id))
    if row is not None:
        await session.delete(row)
        await session.flush()


async def clear_known_host_key(session: AsyncSession, host_id: str, key_type: str) -> bool:
    """Admin-Weg, eine Host-Key-Änderung nach manueller Prüfung erneut zuzulassen.

    Das Vertrauen wiederherzustellen ist eine bewusste, protokollierte Handlung, kein automatischer Fallback."""
    result = await session.execute(
        select(KnownHostKey).where(KnownHostKey.host_id == host_id, KnownHostKey.key_type == key_type)
    )
    row = result.scalars().first()
    if row is None:
        return False
    await session.delete(row)
    # Ab jetzt darf kein Hintergrunddienst den nächsten Schlüssel still merken, auch wenn die Einstellung es sonst erlaubt;
    # erst die Bestätigung nimmt den Merker weg. Im selben Commit wie das Löschen.
    await require_host_key_confirmation(session, host_id)
    # Das Vertrauen ist weg: Eine offene Verbindung, die auf dem alten Schlüssel beruht, wird geschlossen; die nächste
    # muss den Schlüssel neu bestätigen.
    await drop_pooled_connections(session, host_id)
    return True


async def create_group(session: AsyncSession, *, name: str, description: str = "") -> HostGroup:
    group = HostGroup(name=name, description=description)
    session.add(group)
    await session.flush()
    return group


async def list_groups(session: AsyncSession) -> list[HostGroup]:
    result = await session.execute(select(HostGroup).order_by(HostGroup.name))
    return list(result.scalars().all())


async def update_group(
    session: AsyncSession, group_id: str, *, name: str | None = None, description: str | None = None
) -> HostGroup | None:
    group = await session.get(HostGroup, group_id)
    if group is None:
        return None
    if name is not None and name != group.name:
        clash = await session.execute(select(HostGroup.id).where(HostGroup.name == name, HostGroup.id != group_id))
        if clash.first() is not None:
            raise HostServiceError("Gruppenname bereits vergeben.")
        group.name = name
    if description is not None:
        group.description = description
    await session.flush()
    return group


async def delete_group(session: AsyncSession, group_id: str) -> bool:
    from sqlalchemy import delete

    group = await session.get(HostGroup, group_id)
    if group is None:
        return False
    # Zuordnungen ausdrücklich mitlöschen, statt sich nur auf den FK-Cascade zu verlassen.
    await session.execute(delete(host_group_members).where(host_group_members.c.group_id == group_id))
    await session.delete(group)
    await session.flush()
    return True


async def add_group_member(session: AsyncSession, group_id: str, host_id: str) -> bool:
    """Idempotent: Ist der Host schon in der Gruppe, passiert nichts (Rückgabe False).
    Unbekannte Gruppe oder unbekannter Host -> `HostServiceError` (API: 404)."""
    if await session.get(HostGroup, group_id) is None:
        raise HostServiceError("Unbekannte Gruppe.")
    if await session.get(Host, host_id) is None:
        raise HostServiceError("Unbekannter Host.")
    present = await session.execute(
        select(host_group_members.c.host_id).where(
            host_group_members.c.group_id == group_id, host_group_members.c.host_id == host_id
        )
    )
    if present.first() is not None:
        return False
    await session.execute(host_group_members.insert().values(group_id=group_id, host_id=host_id))
    return True


async def remove_group_member(session: AsyncSession, group_id: str, host_id: str) -> bool:
    """Idempotent; `True` nur, wenn der Host wirklich in der Gruppe war."""
    from sqlalchemy import delete

    result = await session.execute(
        delete(host_group_members).where(
            host_group_members.c.group_id == group_id, host_group_members.c.host_id == host_id
        )
    )
    return (result.rowcount or 0) > 0
