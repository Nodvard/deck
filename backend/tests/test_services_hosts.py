"""services.hosts: Host-/Gruppen-/Zugangsdaten-CRUD."""

from __future__ import annotations

import pytest

from nodvard_deck.models import KnownHostKey
from nodvard_deck.services import hosts as hosts_service


@pytest.mark.asyncio
async def test_create_host_with_tags(db_session):
    host = await hosts_service.create_host(
        db_session, name="docker", address="192.168.1.10", tags=["docker", "critical"]
    )
    assert host.id is not None
    assert {t.tag for t in host.tags} == {"docker", "critical"}


@pytest.mark.asyncio
async def test_create_host_duplicate_name_rejected(db_session):
    await hosts_service.create_host(db_session, name="docker", address="1.2.3.4")
    with pytest.raises(hosts_service.HostServiceError):
        await hosts_service.create_host(db_session, name="docker", address="5.6.7.8")


@pytest.mark.asyncio
async def test_list_hosts_filters_by_tag(db_session):
    await hosts_service.create_host(db_session, name="a", address="1.1.1.1", tags=["docker"])
    await hosts_service.create_host(db_session, name="b", address="2.2.2.2", tags=["berry"])

    docker_hosts = await hosts_service.list_hosts(db_session, tag="docker")
    assert [h.name for h in docker_hosts] == ["a"]


@pytest.mark.asyncio
async def test_update_and_delete_host(db_session):
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    updated = await hosts_service.update_host(db_session, host.id, display_name="Neuer Name")
    assert updated.display_name == "Neuer Name"

    assert await hosts_service.delete_host(db_session, host.id) is True
    assert await hosts_service.delete_host(db_session, host.id) is False


@pytest.mark.asyncio
async def test_deleting_a_host_does_not_cascade_delete_a_credential_moved_to_another_host(db_session, test_settings):
    """Live gefunden (admin-seitige Host-Bereinigung, ein doppelt entdeckter Host
    wurde manuell zusammengefuehrt): `Host.credentials` ist `cascade="all,
    delete-orphan"` + `lazy="selectin"` -- die Collection wird beim Laden EINMAL
    eifrig abgefragt und danach NICHT automatisch synchronisiert, wenn ein
    Credential per direkter FK-Zuweisung (`cred.host_id = anderer_host.id`) einem
    ANDEREN Host zugeordnet wird (kein `back_populates`, keine automatische Pflege
    der Gegenseite). `delete_host()` kaskadierte deshalb ueber die noch alte, im
    Speicher gecachte Collection und loeschte ein Credential mit, das laengst
    einem anderen Host gehoerte. Reproduziert exakt die Vorfall-Sequenz: Credential
    umziehen (FK-Zuweisung + Flush), DANACH den alten Host ueber den bestehenden
    Service-Pfad loeschen -- das Credential muss ueberleben."""
    old_host = await hosts_service.create_host(db_session, name="old", address="1.1.1.1")
    new_host = await hosts_service.create_host(db_session, name="new", address="2.2.2.2")
    credential = await hosts_service.add_credential(
        db_session, test_settings, host_id=old_host.id, kind="ssh_key",
        username="root", port=22, secret_value="geheim123",
    )

    # `old_host.credentials` MUSS vor der FK-Umzuweisung geladen sein, sonst
    # reproduziert dieser Test den Vorfall nicht: eine Collection, die erst NACH
    # dem Umziehen zum ersten Mal geladen wird, ist automatisch korrekt (kein
    # Stale-Cache-Zustand) -- der Fehler trat nur auf, weil `manual.credentials`
    # bereits VOR dem Umziehen eifrig geladen war (derselbe Ablauf wie im echten
    # Vorfall: der Host wurde zuerst per `session.get()` geladen, DANACH erst das
    # Credential umgehaengt).
    from nodvard_deck.db import refresh_relationships

    await refresh_relationships(db_session, old_host, "credentials")
    assert [c.id for c in old_host.credentials] == [credential.id]

    credential.host_id = new_host.id
    await db_session.flush()

    assert await hosts_service.delete_host(db_session, old_host.id) is True

    remaining = await hosts_service.list_credentials(db_session, new_host.id)
    assert [c.id for c in remaining] == [credential.id]
    assert await hosts_service.list_credentials(db_session, old_host.id) == []


@pytest.mark.asyncio
async def test_add_credential_and_resolve_target(db_session, test_settings):
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    credential = await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="root", port=22, secret_value="geheim123",
    )
    assert credential.is_default is True

    target = await hosts_service.resolve_connection_target(db_session, test_settings, host, credential)
    assert target.secret_value == "geheim123"
    assert target.username == "root"


@pytest.mark.asyncio
async def test_only_one_default_credential_per_host(db_session, test_settings):
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    first = await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="root", port=22, secret_value="a",
    )
    second = await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="admin", port=22, secret_value="b",
    )
    await db_session.refresh(first)
    assert first.is_default is False
    assert second.is_default is True


@pytest.mark.asyncio
async def test_delete_credential_also_deletes_secret(db_session, test_settings):
    from nodvard_deck.core import vault

    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    credential = await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="root", port=22, secret_value="geheim",
    )
    secret_id = credential.secret_id

    assert await hosts_service.delete_credential(db_session, host.id, credential.id) is True

    from nodvard_deck.models import Secret

    assert await db_session.get(Secret, secret_id) is None


@pytest.mark.asyncio
async def test_clear_known_host_key(db_session):
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    db_session.add(KnownHostKey(host_id=host.id, key_type="ssh-ed25519", fingerprint="SHA256:x"))
    await db_session.flush()

    assert await hosts_service.clear_known_host_key(db_session, host.id, "ssh-ed25519") is True
    assert await hosts_service.clear_known_host_key(db_session, host.id, "ssh-ed25519") is False


@pytest.mark.asyncio
async def test_group_membership_roundtrip(db_session):
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    group = await hosts_service.create_group(db_session, name="game-servers")

    await hosts_service.add_group_member(db_session, group.id, host.id)
    in_group = await hosts_service.list_hosts(db_session, group=group.id)
    assert [h.id for h in in_group] == [host.id]

    await hosts_service.remove_group_member(db_session, group.id, host.id)
    in_group_after = await hosts_service.list_hosts(db_session, group=group.id)
    assert in_group_after == []


@pytest.mark.asyncio
async def test_delete_host_also_deletes_vault_secrets(db_session, test_settings):
    """Der Host loescht per Kaskade nur die Credential-Zeilen -- die verschluesselten
    Schluessel blieben bisher als Waisen in `secrets` zurueck."""
    from nodvard_deck.models import Secret

    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    first = await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="root", port=22, secret_value="eins", is_default=True,
    )
    second = await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="admin", port=22, secret_value="zwei", is_default=False,
    )
    other = await hosts_service.create_host(db_session, name="b", address="2.2.2.2")
    kept = await hosts_service.add_credential(
        db_session, test_settings, host_id=other.id, kind="ssh_password",
        username="root", port=22, secret_value="drei",
    )
    secret_ids = [first.secret_id, second.secret_id]

    assert await hosts_service.delete_host(db_session, host.id) is True

    for secret_id in secret_ids:
        assert await db_session.get(Secret, secret_id) is None
    # Das Secret eines anderen Hosts bleibt unberuehrt.
    assert await db_session.get(Secret, kept.secret_id) is not None


@pytest.mark.asyncio
async def test_delete_host_refused_while_an_action_is_executing(db_session, test_settings):
    from nodvard_deck.models import Action, Secret

    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    credential = await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="root", port=22, secret_value="geheim",
    )
    action = Action(
        ext_id="core", action_type="shell.exec", host_id=host.id, status="executing",
        proposed_by_type="user", proposed_by_id="u1", reason="Test",
    )
    db_session.add(action)
    await db_session.flush()

    with pytest.raises(hosts_service.HostBusyError, match="läuft gerade eine Aktion"):
        await hosts_service.delete_host(db_session, host.id)
    # Nichts wurde angefasst.
    assert await db_session.get(Secret, credential.secret_id) is not None
    assert len(await hosts_service.list_credentials(db_session, host.id)) == 1

    # Ist die Aktion fertig, geht das Loeschen wieder.
    action.status = "succeeded"
    await db_session.flush()
    assert await hosts_service.delete_host(db_session, host.id) is True


@pytest.mark.asyncio
async def test_create_and_update_host_load_credentials(db_session, test_settings):
    """`HostOut` bekommt eine Zugangs-Zusammenfassung -- dafuer muss `host.credentials`
    nach create/update ohne Lazy-Load lesbar sein (docs/00 D-12)."""
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    assert host.credentials == []

    await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="root", port=22, secret_value="geheim",
    )
    updated = await hosts_service.update_host(db_session, host.id, display_name="A")
    assert [c.username for c in updated.credentials] == ["root"]


class _RecordingPool:
    """Ersatz fuer den SSH-Pool: merkt sich nur, fuer welche Hosts er geleert wurde."""

    def __init__(self) -> None:
        self.dropped: list[str] = []

    async def drop_host(self, host_id: str) -> None:
        self.dropped.append(host_id)


@pytest.fixture
def pool(monkeypatch):
    from nodvard_deck.core import ssh

    recorder = _RecordingPool()
    monkeypatch.setattr(ssh, "_pool", recorder)
    return recorder


@pytest.mark.asyncio
async def test_delete_credential_drops_pooled_connections(db_session, test_settings, pool):
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    credential = await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password",
        username="root", port=22, secret_value="geheim",
    )
    assert await hosts_service.delete_credential(db_session, host.id, "gibt-es-nicht") is False
    assert pool.dropped == []
    assert await hosts_service.delete_credential(db_session, host.id, credential.id) is True
    assert pool.dropped == [host.id]


@pytest.mark.asyncio
async def test_forgetting_a_known_key_drops_pooled_connections(db_session, pool):
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    db_session.add(KnownHostKey(host_id=host.id, key_type="ssh-ed25519", fingerprint="SHA256:x"))
    await db_session.flush()
    assert await hosts_service.clear_known_host_key(db_session, host.id, "ssh-rsa") is False
    assert pool.dropped == []
    assert await hosts_service.clear_known_host_key(db_session, host.id, "ssh-ed25519") is True
    assert pool.dropped == [host.id]


@pytest.mark.asyncio
async def test_delete_host_drops_pooled_connections_only_when_deleted(db_session, pool):
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    assert await hosts_service.delete_host(db_session, "gibt-es-nicht") is False
    assert pool.dropped == []
    assert await hosts_service.delete_host(db_session, host.id) is True
    assert pool.dropped == [host.id]


@pytest.mark.asyncio
async def test_update_host_drops_pooled_connections_only_on_address_change(db_session, pool):
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    await hosts_service.update_host(db_session, host.id, display_name="Neu")
    await hosts_service.update_host(db_session, host.id, address="1.1.1.1")
    await hosts_service.update_host(db_session, host.id, tags=["x"])
    assert pool.dropped == []
    await hosts_service.update_host(db_session, host.id, address="1.1.1.2")
    assert pool.dropped == [host.id]


@pytest.mark.asyncio
async def test_drop_is_repeated_after_the_commit(db_session, pool):
    """Ein paralleler Leser mit eigener Session kann zwischen dem Drop und dem Commit noch
    die alten Daten sehen -- darum wird nach dem Commit nochmals gedroppt, nicht nach einem Rollback."""
    import asyncio

    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    host_id = host.id
    await db_session.commit()
    await hosts_service.update_host(db_session, host_id, address="1.1.1.2")
    assert pool.dropped == [host_id]
    await db_session.commit()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert pool.dropped == [host_id, host_id]

    await hosts_service.update_host(db_session, host_id, address="1.1.1.3")
    assert pool.dropped == [host_id] * 3
    await db_session.rollback()
    await db_session.commit()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert pool.dropped == [host_id] * 3  # kein zusaetzlicher Drop nach Rollback


@pytest.mark.asyncio
async def test_resolve_connection_target_carries_the_pool_generation(db_session, test_settings):
    from nodvard_deck.core import ssh

    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    credential = await hosts_service.add_credential(
        db_session, test_settings, host_id=host.id, kind="ssh_password", username="root", port=22, secret_value="x",
    )
    before = ssh.get_ssh_pool().generation(host.id)
    target = await hosts_service.resolve_connection_target(db_session, test_settings, host, credential)
    assert target.generation == before
    await ssh.get_ssh_pool().drop_host(host.id)
    assert ssh.get_ssh_pool().generation(host.id) == before + 1
