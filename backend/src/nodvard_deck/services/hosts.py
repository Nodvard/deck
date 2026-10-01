"""Host-, Gruppen- und Zugangsdaten-Verwaltung (docs/03-DATA-MODEL.md §2,
docs/04-API.md §3).

Der Kern besitzt die Host-Identitaet (docs/01-ARCHITECTURE.md §5) -- diese Datei ist
die Orchestrierung dafuer, analog zu `services/auth.py`/`services/extensions.py`:
reine DB-Verdrahtung, keine HTTP-Kenntnis.
"""

from __future__ import annotations

from nodvard_sdk import Host as SdkHost, HostStatus
from nodvard_sdk.actions import ActionStatus
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..core import ssh, vault
from ..models import Action, Host, HostCredential, HostGroup, HostTag, KnownHostKey, host_group_members
from . import custom_apps

_HOST_STATUS_VALUES = {s.value for s in HostStatus}


class HostServiceError(Exception):
    pass


class HostBusyError(HostServiceError):
    """Auf dem Host laeuft gerade eine Aktion -- Loeschen waere mitten im Lauf."""


async def drop_pooled_connections(session: AsyncSession, host_id: str) -> None:
    """Offene SSH-Verbindungen zu `host_id` sofort schliessen UND nach dem Commit der
    Session nochmals -- siehe `ssh.drop_host_after_commit`."""
    await ssh.get_ssh_pool().drop_host(host_id)
    ssh.drop_host_after_commit(session, host_id)


def host_to_sdk(host: Host) -> SdkHost:
    """Kern-ORM -> SDK-Vertragstyp -- die einzige Stelle, die das tut, damit
    `ext/context.py` (Extension-facing) und `api/v1/terminal.py` (core-facing, fuer
    die WS-Terminal-Bruecke) dieselbe Umwandlung benutzen."""
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
        # `host.credentials`
        # ist wie `host.tags` (oben) `lazy="selectin"` -- synchroner Zugriff hier ist
        # sicher, keine neue Query noetig. Der WERT eines Credentials bleibt weiterhin
        # unsichtbar fuer Extensions (docs/03 §3 Invariante 1) -- nur OB ueberhaupt
        # eines existiert, damit eine Datei-/Terminal-Quelle vorab entscheiden kann,
        # ob sie sich als nutzbar anbietet.
        has_credential=any(c.is_default for c in host.credentials),
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
    # docs/00-DECISIONS.md D-12: IMMER refreshen, nicht nur wenn `tags` nicht leer
    # war -- ein frisch angelegter Host OHNE Tags hat `.tags` genauso wenig geladen
    # wie einer MIT welchen. Live gefunden: `if tags:` liess genau den (haeufigeren)
    # taglosen Fall durchrutschen, MissingGreenlet beim ersten `HostOut.from_model()`.
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
        # Nur MANUELLE Tags (managed_by_ext_id None) anfassen; Tags einer Extension bleiben
        # und werden nicht doppelt angelegt.
        taken = {t.tag for t in host.tags if t.managed_by_ext_id is not None}
        for row in list(host.tags):
            if row.managed_by_ext_id is None and row.tag not in wanted:
                host.tags.remove(row)
        present = {t.tag for t in host.tags}
        for tag in sorted(wanted - taken - present):
            host.tags.append(HostTag(tag=tag))
    await session.flush()
    # Immer beide neu laden: `HostOut` zeigt Markierungen UND die Zugangs-Zusammenfassung,
    # und ein Zugang, der erst NACH dem Laden des Hosts angelegt wurde, steht sonst nicht
    # in der gecachten Collection (D-12).
    await refresh_relationships(session, host, "tags", "credentials")
    if address_changed:
        # Eine gepoolte Verbindung geht sonst weiter an die ALTE Adresse.
        await drop_pooled_connections(session, host.id)
    return host


async def delete_host(session: AsyncSession, host_id: str) -> bool:
    """Live gefunden (admin-seitige Bereinigung eines doppelt
    entdeckten Hosts): `Host.credentials`/`Host.tags` sind `cascade="all,
    delete-orphan"`, `lazy="selectin"` -- die Collection wird beim Laden von `host`
    EINMAL eifrig abgefragt und danach NICHT automatisch synchronisiert, wenn ein
    Credential per direkter FK-Zuweisung (`cred.host_id = anderer_host.id`) einem
    ANDEREN Host zugeordnet wird (kein `back_populates`, also keine automatische
    Pflege der Gegenseite). `session.delete(host)` kaskadierte deshalb ueber die
    NOCH ALTE, im Speicher gecachte Collection und loeschte ein Credential mit, das
    laengst einem anderen Host gehoerte. `refresh_relationships()` (docs/00-
    DECISIONS.md D-12) erzwingt hier einen frischen Nachlade-Versuch direkt vor dem
    Loeschen, damit die Kaskade nur noch trifft, was WIRKLICH noch zu `host`
    gehoert."""
    from ..db import refresh_relationships

    host = await session.get(Host, host_id)
    if host is None:
        return False
    # Erst einen (wirkungslosen) Schreibzugriff, DANN pruefen: SQLite vergibt die
    # Schreibsperre nur einmal -- zwischen Pruefung und Loeschen kann so keine Aktion mehr
    # auf "executing" wechseln (TOCTOU). Nach dem Loeschen selbst wuerde der Check nichts
    # mehr finden, weil `actions.host_id` dann auf NULL gesetzt ist.
    await session.execute(update(Host).where(Host.id == host_id).values(name=Host.name))
    running = await session.execute(
        select(Action.id)
        .where(Action.host_id == host_id, Action.status == ActionStatus.EXECUTING.value)
        .limit(1)
    )
    if running.first() is not None:
        raise HostBusyError("Auf diesem Server läuft gerade eine Aktion. Bitte warten, bis sie fertig ist.")
    await refresh_relationships(session, host, "credentials", "tags")
    # Die Secret-IDs VOR dem Loeschen merken: `secrets` hat einen RESTRICT-Fremdschluessel
    # von den Zugangsdaten aus, das Secret darf also erst NACH den Zugangsdaten weg.
    secret_ids = [c.secret_id for c in host.credentials]
    # Eigene App-Kacheln mit diesem Server als Bezug bleiben stehen, nur ohne Server (nicht vom SQLite-Pragma abhaengig).
    await custom_apps.unlink_host(session, host_id)
    await session.delete(host)
    # Ohne Flush bleibt `host` im Identity-Map als "zum Loeschen vorgemerkt", aber
    # noch auffindbar -- ein zweiter delete_host()-Aufruf im selben Request wuerde
    # ihn faelschlich nochmal finden (live gefunden ueber den Regressionstest).
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
    # Siehe delete_host(): ohne Flush waere sowohl das Secret als auch die
    # Credential-Zeile noch ueber die Identity-Map auffindbar.
    await session.flush()
    host = await session.get(Host, host_id)
    if host is not None:
        from ..db import refresh_relationships

        await refresh_relationships(session, host, "credentials")
    # Eine noch offene Verbindung mit dem geloeschten Zugang darf nicht weiterlaufen.
    await drop_pooled_connections(session, host_id)
    return True


async def make_default_credential(
    session: AsyncSession, host_id: str, credential_id: str, *, delete_previous: bool = False
) -> tuple[HostCredential, HostCredential | None] | None:
    """Macht einen Zugang zum Standard des Servers (Schluesselwechsel: erst den neuen
    pruefen, dann umstellen). Mit `delete_previous` wird der bisherige Standard samt
    Geheimnis in DERSELBEN Transaktion geloescht (und seine Verbindungen sofort geschlossen);
    sonst wird der Pool nur ausgemustert (`retire_host`). Rueckgabe: `(Zugang, geloeschter alter
    Standard oder None)`; `None` bei unbekanntem Zugang. Ist der Zugang schon der Standard,
    aendert sich nichts -- auch `delete_previous` loescht dann nichts."""
    from ..db import refresh_relationships

    credential = await session.get(HostCredential, credential_id)
    if credential is None or credential.host_id != host_id:
        return None
    host = await session.get(Host, host_id)
    assert host is not None  # der Zugang verweist per Fremdschluessel darauf
    others = (
        await session.execute(
            select(HostCredential).where(
                HostCredential.host_id == host_id, HostCredential.is_default.is_(True), HostCredential.id != credential.id
            )
        )
    ).scalars().all()
    previous = others[0] if others else None
    if credential.is_default and not others:
        return credential, None
    for row in others:
        row.is_default = False
    credential.is_default = True
    deleted: HostCredential | None = None
    if delete_previous and previous is not None:
        # Erst die Zugangszeile, dann das Geheimnis (RESTRICT-Fremdschluessel, siehe delete_host).
        deleted = previous
        secret_id = previous.secret_id
        await session.delete(previous)
        await session.flush()
        await vault.delete_secret(session, secret_id)
    await session.flush()
    await refresh_relationships(session, host, "credentials")
    if deleted is not None:
        # Der geloeschte Zugang darf keine Verbindung mehr offenhalten.
        await drop_pooled_connections(session, host_id)
    else:
        # Nichts wurde geloescht: neue Aufrufer verbinden mit dem neuen Standard, laufende
        # Terminals und Live-Protokolle duerfen in Ruhe zu Ende laufen (Gnadenfrist).
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
    """Loest die Zugangsdaten-Referenz ueber den Vault auf -- der Klartext lebt nur
    kurzlebig im Speicher dieses Aufrufs (docs/03 §3, Invariante 2)."""
    # Stand des Pool-Zaehlers festhalten: wird der Host danach geaendert (drop_host),
    # erkennt `SshPool.get()` dieses Ziel als veraltet. Host und Zugang hat der Aufrufer
    # schon vorher gelesen -- dieses kleine Fenster deckt der Drop nach dem Commit ab
    # (`drop_pooled_connections`).
    generation = ssh.get_ssh_pool().generation(host.id)
    keyring = vault.load_keyring(settings)
    plaintext = await vault.read_secret_plaintext(session, keyring, credential.secret_id)
    return ssh.ConnectionTarget(
        host_id=host.id, address=host.address, port=credential.port,
        username=credential.username, kind=credential.kind, secret_value=plaintext,
        generation=generation,
        # Einstellung ssh_confirm_new_host_keys: dann merken Hintergrundjobs, Terminal und Status
        # keinen neuen Server-Schluessel mehr still, nur "Verbindung pruefen" (Bestaetigen) tut es.
        allow_tofu=not settings.ssh_confirm_new_host_keys,
    )


async def clear_known_host_key(session: AsyncSession, host_id: str, key_type: str) -> bool:
    """Admin-Weg, eine Host-Key-Aenderung NACH manueller Pruefung erneut zuzulassen
    (docs/03 §2: "Abweichung => Verbindung scheitert sichtbar", die Wiederherstellung
    des Vertrauens ist eine bewusste, protokollierte Handlung, kein automatischer
    Fallback)."""
    result = await session.execute(
        select(KnownHostKey).where(KnownHostKey.host_id == host_id, KnownHostKey.key_type == key_type)
    )
    row = result.scalars().first()
    if row is None:
        return False
    await session.delete(row)
    # Das Vertrauen ist weg -- eine offene Verbindung, die auf dem alten Schluessel
    # beruht, wird geschlossen; die naechste muss den Schluessel neu bestaetigen.
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
    # Zuordnungen ausdruecklich mitloeschen, statt sich nur auf den FK-Cascade zu verlassen.
    await session.execute(delete(host_group_members).where(host_group_members.c.group_id == group_id))
    await session.delete(group)
    await session.flush()
    return True


async def add_group_member(session: AsyncSession, group_id: str, host_id: str) -> bool:
    """Idempotent: ist der Host schon in der Gruppe, passiert nichts (Rueckgabe False).
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
