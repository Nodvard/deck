"""Gemeinsamer SSH-Layer: Verbinden, Ausfuehren, TOFU + Pinning
(docs/00-DECISIONS.md D-05).

Laeuft gegen einen echten lokalen asyncssh-Server (siehe `local_ssh_server`-Fixture in
conftest.py) -- kein Mock der SSH-Semantik selbst, nur kein Zugriff auf die reale
Flotte.
"""

from __future__ import annotations

import pytest

from nodvard_deck.core import ssh
from nodvard_deck.models import KnownHostKey


def _target(host, port, username, password, *, host_id="host-1") -> ssh.ConnectionTarget:
    return ssh.ConnectionTarget(
        host_id=host_id, address=host, port=port, username=username,
        kind="ssh_password", secret_value=password,
    )


@pytest.mark.asyncio
async def test_connect_and_run_roundtrip(local_ssh_server, db_session):
    host, port, username, password, _ = local_ssh_server
    conn = await ssh.connect(db_session, _target(host, port, username, password))
    try:
        exit_code, stdout, stderr = await ssh.run(conn, "echo hallo")
        assert exit_code == 0
        assert stdout == "ran:echo hallo\n"
    finally:
        conn.close()
        await conn.wait_closed()


@pytest.mark.asyncio
async def test_first_contact_stores_fingerprint_tofu(local_ssh_server, db_session):
    host, port, username, password, _ = local_ssh_server
    from sqlalchemy import select

    assert (await db_session.execute(select(KnownHostKey))).scalars().all() == []

    conn = await ssh.connect(db_session, _target(host, port, username, password))
    conn.close()
    await conn.wait_closed()

    rows = (await db_session.execute(select(KnownHostKey))).scalars().all()
    assert len(rows) == 1
    assert rows[0].host_id == "host-1"
    assert rows[0].fingerprint


@pytest.mark.asyncio
async def test_second_connect_with_same_key_succeeds_pinned(local_ssh_server, db_session):
    host, port, username, password, _ = local_ssh_server
    target = _target(host, port, username, password)

    conn1 = await ssh.connect(db_session, target)
    conn1.close()
    await conn1.wait_closed()

    # Zweite Verbindung: derselbe Server, derselbe Schluessel -- muss durchgehen,
    # ohne erneut TOFU zu betreiben (bereits bekannt).
    conn2 = await ssh.connect(db_session, target)
    exit_code, stdout, _ = await ssh.run(conn2, "echo again")
    conn2.close()
    await conn2.wait_closed()

    assert exit_code == 0
    assert "again" in stdout

    from sqlalchemy import select

    rows = (await db_session.execute(select(KnownHostKey))).scalars().all()
    assert len(rows) == 1, "TOFU darf nur einmal je Schluesseltyp schreiben"


@pytest.mark.asyncio
async def test_changed_host_key_is_rejected_visibly(local_ssh_server, db_session):
    """D-05: Host-Key-Aenderung scheitert sichtbar, statt still akzeptiert zu werden."""
    host, port, username, password, _ = local_ssh_server
    target = _target(host, port, username, password)

    conn1 = await ssh.connect(db_session, target)
    conn1.close()
    await conn1.wait_closed()

    # Einen falschen Fingerprint simulieren, als haette der Host einen neuen
    # Schluessel (z. B. nach einer Neuinstallation OHNE Admin-Bestaetigung).
    from sqlalchemy import select

    row = (await db_session.execute(select(KnownHostKey))).scalars().one()
    row.fingerprint = "SHA256:absichtlich-falsch"
    await db_session.flush()

    with pytest.raises(ssh.HostKeyMismatch) as excinfo:
        await ssh.connect(db_session, target)

    assert excinfo.value.host_id == "host-1"
    assert "absichtlich-falsch" in str(excinfo.value)


@pytest.mark.asyncio
async def test_wrong_password_raises_ssh_error(local_ssh_server, db_session):
    host, port, username, _, _ = local_ssh_server
    with pytest.raises(ssh.SshError):
        await ssh.connect(db_session, _target(host, port, username, "falsches-passwort"))


@pytest.mark.asyncio
async def test_pool_reuses_connection_for_same_host_and_credential(local_ssh_server, db_session):
    host, port, username, password, _ = local_ssh_server
    target = _target(host, port, username, password)
    pool = ssh.SshPool()
    try:
        conn1 = await pool.get(db_session, target, credential_id="cred-1")
        conn2 = await pool.get(db_session, target, credential_id="cred-1")
        assert conn1 is conn2

        conn3 = await pool.get(db_session, target, credential_id="cred-2")
        assert conn3 is not conn1
    finally:
        await pool.close_all()


@pytest.mark.asyncio
async def test_open_shell_interactive_roundtrip(local_ssh_server, db_session):
    host, port, username, password, _ = local_ssh_server
    conn = await ssh.connect(db_session, _target(host, port, username, password))
    try:
        async with ssh.open_shell(conn, cols=80, rows=24) as process:
            process.stdin.write(b"hallo\n")
            line = await process.stdout.readline()
            assert line == b"shell-ready\n"
            line2 = await process.stdout.readline()
            assert line2 == b"echo:hallo\n"
            process.stdin.write(b"exit\n")
    finally:
        conn.close()
        await conn.wait_closed()


@pytest.mark.asyncio
async def test_sftp_roundtrip(local_ssh_server, db_session):
    host, port, username, password, sftp_root = local_ssh_server
    conn = await ssh.connect(db_session, _target(host, port, username, password))
    try:
        sftp = await ssh.start_sftp(conn)
        async with sftp.open("hallo.txt", "wb") as f:
            await f.write(b"testinhalt")
        assert (sftp_root / "hallo.txt").read_bytes() == b"testinhalt"

        entries = await sftp.listdir(".")
        assert "hallo.txt" in entries

        await sftp.remove("hallo.txt")
        assert not (sftp_root / "hallo.txt").exists()
        sftp.exit()
        await sftp.wait_closed()
    finally:
        conn.close()
        await conn.wait_closed()


class _FakeConn:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


@pytest.mark.asyncio
async def test_pool_hanging_connect_does_not_block_other_hosts(monkeypatch):
    """Ein nicht erreichbarer Host (connect haengt bis zum Timeout) darf
    Verbindungen zu anderen Hosts nicht aufhalten -- weder einen neuen Aufbau noch
    den Abruf einer schon gepoolten, lebenden Verbindung."""
    import asyncio

    release_a = asyncio.Event()
    connects: list[str] = []

    async def _fake_connect(session, target, *, connect_timeout_s=10.0):
        connects.append(target.host_id)
        if target.host_id == "host-a":
            await release_a.wait()
        return _FakeConn()

    monkeypatch.setattr(ssh, "connect", _fake_connect)
    pool = ssh.SshPool()
    target_a = _target("10.0.0.1", 22, "u", "p", host_id="host-a")
    target_b = _target("10.0.0.2", 22, "u", "p", host_id="host-b")
    target_c = _target("10.0.0.3", 22, "u", "p", host_id="host-c")

    pooled_c = await pool.get(None, target_c, credential_id="cred")
    hanging = asyncio.create_task(pool.get(None, target_a, credential_id="cred"))
    await asyncio.sleep(0)  # host-a steckt jetzt im Verbindungsaufbau
    try:
        conn_b = await asyncio.wait_for(pool.get(None, target_b, credential_id="cred"), timeout=3.0)
        assert isinstance(conn_b, _FakeConn)
        again_c = await asyncio.wait_for(pool.get(None, target_c, credential_id="cred"), timeout=3.0)
        assert again_c is pooled_c
        assert not hanging.done()
    finally:
        release_a.set()
        await hanging
    await pool.close_all()


@pytest.mark.asyncio
async def test_pool_parallel_gets_for_same_host_connect_only_once(monkeypatch):
    """Die Sperre je Schluessel verhindert weiterhin doppelte Verbindungen, wenn zwei
    Aufrufer gleichzeitig denselben Host anfragen."""
    import asyncio

    release = asyncio.Event()
    connects: list[str] = []

    async def _fake_connect(session, target, *, connect_timeout_s=10.0):
        connects.append(target.host_id)
        await release.wait()
        return _FakeConn()

    monkeypatch.setattr(ssh, "connect", _fake_connect)
    pool = ssh.SshPool()
    target = _target("10.0.0.1", 22, "u", "p", host_id="host-a")

    first = asyncio.create_task(pool.get(None, target, credential_id="cred"))
    second = asyncio.create_task(pool.get(None, target, credential_id="cred"))
    await asyncio.sleep(0)
    release.set()
    conn1, conn2 = await asyncio.gather(first, second)
    assert conn1 is conn2
    assert connects == ["host-a"]
    await pool.close_all()


async def _start_blackhole_proxy(upstream_host: str, upstream_port: int):
    """TCP-Weiterleitung zum Test-SSH-Server, die auf Knopfdruck alles verschluckt --
    wie eine hart gestoppte VM: kein FIN, kein RST, einfach keine Antwort mehr."""
    import asyncio

    state = {"blackhole": False}
    writers: list[asyncio.StreamWriter] = []

    async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while data := await src.read(65536):
                if not state["blackhole"]:
                    dst.write(data)
                    await dst.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            dst.close()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        up_reader, up_writer = await asyncio.open_connection(upstream_host, upstream_port)
        writers.extend([writer, up_writer])
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def _close() -> None:
        for w in writers:
            w.close()
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), timeout=3.0)
        except TimeoutError:
            pass

    return port, state, _close


@pytest.mark.asyncio
async def test_pool_replaces_connection_to_silently_dead_host(local_ssh_server, db_session, monkeypatch):
    """Verschwindet die Gegenstelle ohne FIN/RST (VM hart gestoppt), muss
    der Keepalive die gepoolte Verbindung als tot erkennen. Sonst gibt der Pool sie
    weiter heraus und jeder Befehl laeuft bis zu seinem eigenen Timeout."""
    import asyncio

    host, port, username, password, _ = local_ssh_server
    proxy_port, proxy, close_proxy = await _start_blackhole_proxy(host, port)
    # Im Test verkuerzt, damit er nicht 45 s dauert -- der Mechanismus ist derselbe.
    monkeypatch.setattr(ssh, "KEEPALIVE_INTERVAL_S", 0.2)
    monkeypatch.setattr(ssh, "KEEPALIVE_COUNT_MAX", 2)
    pool = ssh.SshPool()
    target = _target(host, proxy_port, username, password)
    try:
        conn = await pool.get(db_session, target, credential_id="cred-1")
        exit_code, _, _ = await ssh.run(conn, "echo ok", timeout_s=5.0)
        assert exit_code == 0

        proxy["blackhole"] = True
        deadline = asyncio.get_running_loop().time() + 5.0
        while not conn.is_closed() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
        assert conn.is_closed(), "tote Verbindung wurde nicht erkannt"

        # Der Host ist wieder da: der Pool baut eine neue Verbindung auf.
        proxy["blackhole"] = False
        fresh = await pool.get(db_session, target, credential_id="cred-1")
        assert fresh is not conn
        exit_code, _, _ = await ssh.run(fresh, "echo wieder da", timeout_s=5.0)
        assert exit_code == 0
    finally:
        await pool.close_all()
        await close_proxy()


# ---------------------------------------------------------------------------
# drop_host / retire_host
# ---------------------------------------------------------------------------


def _patch_fake_connect(monkeypatch, *, release=None, created=None):
    async def _fake_connect(session, target, *, connect_timeout_s=10.0):
        if release is not None:
            await release.wait()
        conn = _FakeConn()
        if created is not None:
            created.append(conn)
        return conn

    monkeypatch.setattr(ssh, "connect", _fake_connect)


@pytest.mark.asyncio
async def test_pool_drop_host_closes_only_that_hosts_connections(monkeypatch):
    created: list[_FakeConn] = []
    _patch_fake_connect(monkeypatch, created=created)
    pool = ssh.SshPool()
    a1 = await pool.get(None, _target("10.0.0.1", 22, "u", "p", host_id="host-a"), credential_id="c1")
    a2 = await pool.get(None, _target("10.0.0.1", 22, "u", "p", host_id="host-a"), credential_id="c2")
    b = await pool.get(None, _target("10.0.0.2", 22, "u", "p", host_id="host-b"), credential_id="c1")

    await pool.drop_host("host-a")

    assert a1.closed and a2.closed
    assert not b.closed
    # Der naechste Zugriff verbindet neu; der andere Host bleibt gepoolt.
    a_new = await pool.get(None, _target("10.0.0.1", 22, "u", "p", host_id="host-a"), credential_id="c1")
    assert a_new is not a1 and not a_new.closed
    assert await pool.get(None, _target("10.0.0.2", 22, "u", "p", host_id="host-b"), credential_id="c1") is b
    await pool.drop_host("unbekannt")  # kein Fehler
    await pool.close_all()


@pytest.mark.asyncio
async def test_pool_drop_host_during_connect_discards_the_new_connection(monkeypatch):
    """Ein Aufbau, der beim Verwerfen noch laeuft, wuerde sonst mit den ALTEN
    Verbindungsdaten (alte Adresse, geloeschter Zugang) wieder im Pool landen."""
    import asyncio

    release = asyncio.Event()
    created: list[_FakeConn] = []
    _patch_fake_connect(monkeypatch, release=release, created=created)
    pool = ssh.SshPool()
    target = _target("10.0.0.1", 22, "u", "p", host_id="host-a")

    pending = asyncio.create_task(pool.get(None, target, credential_id="c1"))
    await asyncio.sleep(0)
    await pool.drop_host("host-a")
    release.set()
    with pytest.raises(ssh.SshError, match="geändert"):
        await pending
    assert created and created[0].closed
    # Der Pool ist danach nicht vergiftet.
    conn = await pool.get(None, target, credential_id="c1")
    assert not conn.closed
    await pool.close_all()


@pytest.mark.asyncio
async def test_pool_retire_host_keeps_old_connection_for_the_grace_period(monkeypatch):
    import asyncio

    created: list[_FakeConn] = []
    _patch_fake_connect(monkeypatch, created=created)
    pool = ssh.SshPool()
    target = _target("10.0.0.1", 22, "u", "p", host_id="host-a")
    old = await pool.get(None, target, credential_id="c1")

    pool.retire_host("host-a", grace_s=0.1)

    fresh = await pool.get(None, target, credential_id="c1")
    assert fresh is not old
    assert not old.closed  # laeuft weiter (z. B. ein `docker logs -f`)
    await asyncio.sleep(0.25)
    assert old.closed
    assert not fresh.closed
    await pool.close_all()


@pytest.mark.asyncio
async def test_pool_drop_host_and_close_all_also_close_retired_connections(monkeypatch):
    _patch_fake_connect(monkeypatch)
    pool = ssh.SshPool()
    a = await pool.get(None, _target("10.0.0.1", 22, "u", "p", host_id="host-a"), credential_id="c1")
    b = await pool.get(None, _target("10.0.0.2", 22, "u", "p", host_id="host-b"), credential_id="c1")
    pool.retire_host("host-a", grace_s=900)
    pool.retire_host("host-b", grace_s=900)
    assert not a.closed and not b.closed

    await pool.drop_host("host-a")  # z. B. Schluessel vergessen: auch die alte Verbindung muss weg
    assert a.closed and not b.closed
    await pool.close_all()
    assert b.closed
    assert not pool._retired


@pytest.mark.asyncio
async def test_pool_drop_host_closes_a_real_connection(local_ssh_server, db_session):
    host, port, username, password, _ = local_ssh_server
    pool = ssh.SshPool()
    target = _target(host, port, username, password)
    conn = await pool.get(db_session, target, credential_id="cred-1")
    assert not conn.is_closed()
    await pool.drop_host(target.host_id)
    assert conn.is_closed()
    await pool.close_all()


@pytest.mark.asyncio
async def test_pool_rejects_a_stale_target_generation_even_when_waiting_for_the_lock(monkeypatch):
    """Ein Wartender, dessen Ziel VOR einem drop_host() gelesen wurde, darf nach dem Warten
    nicht mit den alten Daten verbinden."""
    import asyncio

    release = asyncio.Event()
    created: list[_FakeConn] = []
    _patch_fake_connect(monkeypatch, release=release, created=created)
    pool = ssh.SshPool()
    base = _target("10.0.0.1", 22, "u", "p", host_id="host-a")
    stale = ssh.ConnectionTarget(**{**base.__dict__, "generation": pool.generation("host-a")})

    first = asyncio.create_task(pool.get(None, stale, credential_id="c1"))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(pool.get(None, stale, credential_id="c1"))
    await asyncio.sleep(0)
    await pool.drop_host("host-a")
    release.set()
    with pytest.raises(ssh.SshTargetChanged):
        await first
    with pytest.raises(ssh.SshTargetChanged):
        await waiter
    assert len(created) == 1  # der Wartende hat gar nicht erst verbunden
    # Ein frisch gelesenes Ziel geht wieder.
    fresh = ssh.ConnectionTarget(**{**base.__dict__, "generation": pool.generation("host-a")})
    assert not (await pool.get(None, fresh, credential_id="c1")).closed
    await pool.close_all()


@pytest.mark.asyncio
async def test_pool_rejects_a_target_read_before_the_drop_even_without_any_waiting(monkeypatch):
    _patch_fake_connect(monkeypatch)
    pool = ssh.SshPool()
    base = _target("10.0.0.1", 22, "u", "p", host_id="host-a")
    old = ssh.ConnectionTarget(**{**base.__dict__, "generation": pool.generation("host-a")})
    await pool.drop_host("host-a")
    with pytest.raises(ssh.SshTargetChanged):
        await pool.get(None, old, credential_id="c1")


@pytest.mark.asyncio
async def test_pool_drop_host_keeps_locks_and_closes_connections_in_parallel(monkeypatch):
    import asyncio
    import time

    class _SlowConn(_FakeConn):
        async def wait_closed(self) -> None:
            await asyncio.sleep(0.3)

    async def _connect(session, target, *, connect_timeout_s=10.0):
        return _SlowConn()

    monkeypatch.setattr(ssh, "connect", _connect)
    pool = ssh.SshPool()
    target = _target("10.0.0.1", 22, "u", "p", host_id="host-a")
    for cred in ("c1", "c2", "c3"):
        await pool.get(None, target, credential_id=cred)
    locks_before = dict(pool._locks)
    started = time.monotonic()
    await pool.drop_host("host-a")
    assert time.monotonic() - started < 0.7  # 3 x 0.3 s nacheinander waeren >= 0.9 s
    assert pool._locks == locks_before and len(locks_before) == 3
    await pool.close_all()


# ---------------------------------------------------------------------------
# Ohne TOFU: unbekannter Server-Schluessel -> nichts gesendet, nichts gespeichert
# ---------------------------------------------------------------------------


async def _known_rows(db_session):
    from sqlalchemy import select

    return (await db_session.execute(select(KnownHostKey))).scalars().all()


@pytest.mark.asyncio
async def test_connect_without_tofu_raises_host_key_unknown_and_writes_nothing(local_ssh_server, db_session, ssh_server_stats):
    host, port, username, password, _ = local_ssh_server
    with pytest.raises(ssh.HostKeyUnknown) as excinfo:
        await ssh.connect(db_session, _target(host, port, username, password), tofu=False)

    exc = excinfo.value
    assert exc.host_id == "host-1"
    assert exc.key_type == "ssh-ed25519"
    assert exc.fingerprint and exc.fingerprint.startswith("SHA256:")
    assert "noch nicht bestätigt" in str(exc) and "Verbindung prüfen" in str(exc)
    assert isinstance(exc, ssh.SshError), "bestehende Aufrufer, die SshError abfangen, zeigen die Meldung an"
    assert await _known_rows(db_session) == [], "ohne TOFU wird nichts gemerkt"
    # Der Kern: die Anmeldung hat nie begonnen -- weder Benutzername noch Passwort gingen an den Server.
    assert ssh_server_stats["begin_auth"] == 0
    assert ssh_server_stats["password_tries"] == 0


@pytest.mark.asyncio
async def test_target_allow_tofu_false_has_the_same_effect(local_ssh_server, db_session, ssh_server_stats):
    host, port, username, password, _ = local_ssh_server
    target = ssh.ConnectionTarget(
        host_id="host-1", address=host, port=port, username=username, kind="ssh_password",
        secret_value=password, allow_tofu=False,
    )
    with pytest.raises(ssh.HostKeyUnknown):
        await ssh.connect(db_session, target)  # tofu=True allein reicht nicht: beide muessen wahr sein
    with pytest.raises(ssh.HostKeyUnknown):
        await ssh.connect(db_session, target, tofu=True)
    assert await _known_rows(db_session) == []
    assert ssh_server_stats["begin_auth"] == 0


@pytest.mark.asyncio
async def test_default_keeps_tofu_on(local_ssh_server, db_session):
    """Wie bisher: ohne Angabe wird ein neuer Schluessel still gemerkt (Hintergrundjobs,
    bestehende Installationen)."""
    host, port, username, password, _ = local_ssh_server
    target = _target(host, port, username, password)
    assert target.allow_tofu is True
    conn = await ssh.connect(db_session, target)
    conn.close()
    await conn.wait_closed()
    assert len(await _known_rows(db_session)) == 1


@pytest.mark.asyncio
async def test_connect_without_tofu_works_with_a_pinned_key(local_ssh_server, db_session, ssh_server_stats):
    host, port, username, password, _ = local_ssh_server
    report = await ssh.inspect_host_key(host, port, {})
    db_session.add(KnownHostKey(host_id="host-1", key_type=report.key_type, fingerprint=report.fingerprint))
    await db_session.flush()

    conn = await ssh.connect(db_session, _target(host, port, username, password), tofu=False)
    try:
        exit_code, _, _ = await ssh.run(conn, "true")
        assert exit_code == 0
    finally:
        conn.close()
        await conn.wait_closed()
    assert ssh_server_stats["begin_auth"] == 1
    assert len(await _known_rows(db_session)) == 1


@pytest.mark.asyncio
async def test_connect_without_tofu_still_reports_a_changed_key_as_mismatch(local_ssh_server, db_session, ssh_server_stats):
    host, port, username, password, _ = local_ssh_server
    db_session.add(KnownHostKey(host_id="host-1", key_type="ssh-ed25519", fingerprint="SHA256:absichtlich-falsch"))
    await db_session.flush()
    with pytest.raises(ssh.HostKeyMismatch):
        await ssh.connect(db_session, _target(host, port, username, password), tofu=False)
    assert ssh_server_stats["begin_auth"] == 0
    [row] = await _known_rows(db_session)
    assert row.fingerprint == "SHA256:absichtlich-falsch", "der gemerkte Schluessel bleibt unveraendert"


@pytest.mark.asyncio
async def test_failed_login_carries_the_verified_server_key_and_is_an_auth_error(local_ssh_server, db_session):
    host, port, username, _, _ = local_ssh_server
    with pytest.raises(ssh.SshAuthError) as excinfo:
        await ssh.connect(db_session, _target(host, port, username, "falsches-passwort"))
    assert excinfo.value.key_type == "ssh-ed25519" and excinfo.value.fingerprint.startswith("SHA256:")
    assert isinstance(excinfo.value, ssh.SshError)


# ---------------------------------------------------------------------------
# inspect_host_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_host_key_reports_new_known_and_changed_without_logging_in(local_ssh_server, ssh_server_stats):
    host, port, _, _, _ = local_ssh_server
    new = await ssh.inspect_host_key(host, port, {}, timeout_s=5)
    assert (new.key_type, new.status, new.expected) == ("ssh-ed25519", "new", None)
    assert new.fingerprint.startswith("SHA256:")

    known = await ssh.inspect_host_key(host, port, {new.key_type: new.fingerprint}, timeout_s=5)
    assert (known.status, known.expected, known.fingerprint) == ("known", new.fingerprint, new.fingerprint)

    changed = await ssh.inspect_host_key(host, port, {new.key_type: "SHA256:alt"}, timeout_s=5)
    assert (changed.status, changed.expected, changed.fingerprint) == ("changed", "SHA256:alt", new.fingerprint)

    # Ein anderer Schluesseltyp gemerkt: Abweichung, nicht "neu" (siehe die Tests zum Typ-Wechsel).
    other = await ssh.inspect_host_key(host, port, {"ssh-rsa": "SHA256:x"}, timeout_s=5)
    assert other.status == "changed"
    assert ssh_server_stats["begin_auth"] == 0, "es wird nie angemeldet"


@pytest.mark.asyncio
async def test_inspect_host_key_fails_cleanly_when_nothing_listens():
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(ssh.SshError):
        await ssh.inspect_host_key("127.0.0.1", port, {}, timeout_s=2)


# ---------------------------------------------------------------------------
# Schluesseltyp-Wechsel darf das Pinning nicht umgehen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tofu", [True, False])
async def test_other_key_type_than_the_pinned_one_is_a_mismatch_not_new(
    ssh_server_factory, db_session, ssh_server_stats, tofu
):
    """Gemerkt ist ed25519, der Server (oder ein Mittelsmann) bietet nur ecdsa an: das ist kein
    „neuer Schluessel“, sondern eine Abweichung -- auch mit TOFU wird nichts gemerkt, nichts gesendet."""
    host, port, username, password, keys = await ssh_server_factory(("ecdsa-sha2-nistp256",))
    db_session.add(KnownHostKey(host_id="host-1", key_type="ssh-ed25519", fingerprint="SHA256:gemerkt-ed25519"))
    await db_session.flush()

    with pytest.raises(ssh.HostKeyMismatch) as excinfo:
        await ssh.connect(db_session, _target(host, port, username, password), tofu=tofu)

    assert excinfo.value.key_type == "ecdsa-sha2-nistp256"
    assert excinfo.value.actual == keys["ecdsa-sha2-nistp256"]
    assert "SHA256:gemerkt-ed25519" in str(excinfo.value), "zeigt, was bisher gemerkt war"
    rows = await _known_rows(db_session)
    assert [(r.key_type, r.fingerprint) for r in rows] == [("ssh-ed25519", "SHA256:gemerkt-ed25519")]
    assert ssh_server_stats["begin_auth"] == 0 and ssh_server_stats["password_tries"] == 0


@pytest.mark.asyncio
async def test_inspect_reports_another_key_type_as_changed(ssh_server_factory):
    host, port, _, _, keys = await ssh_server_factory(("ecdsa-sha2-nistp256",))
    report = await ssh.inspect_host_key(host, port, {"ssh-ed25519": "SHA256:gemerkt-ed25519"}, timeout_s=5)
    assert (report.status, report.key_type) == ("changed", "ecdsa-sha2-nistp256")
    assert report.fingerprint == keys["ecdsa-sha2-nistp256"]
    assert "SHA256:gemerkt-ed25519" in (report.expected or "")
    # Ohne jeden gemerkten Schluessel bleibt es „neu“.
    assert (await ssh.inspect_host_key(host, port, {}, timeout_s=5)).status == "new"


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned_type", ["ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"])
async def test_server_with_several_key_types_keeps_using_the_pinned_type(
    ssh_server_factory, db_session, ssh_server_stats, pinned_type
):
    """Ein normaler Server hat mehrere Schluessel und bietet nach eigener Vorliebe einen an: die
    Verbindung bevorzugt den gemerkten Typ, statt ihn wegen eines anderen Angebots abzulehnen."""
    host, port, username, password, keys = await ssh_server_factory(
        ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa")
    )
    db_session.add(KnownHostKey(host_id="host-1", key_type=pinned_type, fingerprint=keys[pinned_type]))
    await db_session.flush()

    for tofu in (True, False):
        conn = await ssh.connect(db_session, _target(host, port, username, password), tofu=tofu)
        try:
            assert conn.get_server_host_key().get_algorithm() == pinned_type
            assert (await ssh.run(conn, "true"))[0] == 0
        finally:
            conn.close()
            await conn.wait_closed()
    assert [(r.key_type, r.fingerprint) for r in await _known_rows(db_session)] == [(pinned_type, keys[pinned_type])]
    report = await ssh.inspect_host_key(host, port, {pinned_type: keys[pinned_type]}, timeout_s=5)
    assert (report.status, report.key_type) == ("known", pinned_type)


def test_preferred_host_key_algs():
    assert ssh._preferred_host_key_algs({}) is None
    algs = ssh._preferred_host_key_algs({"ssh-rsa": "x", "ecdsa-sha2-nistp256": "y"})
    assert algs[:4] == ["ecdsa-sha2-nistp256", "rsa-sha2-512", "rsa-sha2-256", "ssh-rsa"]
    assert "ssh-ed25519" in algs, "die uebrigen Typen bleiben erlaubt (die Abweichung meldet der Pruefer)"
    assert len(algs) == len(set(algs))
    assert ssh._preferred_host_key_algs({"ssh-ed25519": "x"})[0] == "ssh-ed25519"
    assert ssh._preferred_host_key_algs({"unbekannter-typ": "x"}) is not None


# ---------------------------------------------------------------------------
# Keine Schluessel, kein Agent, keine Konfiguration des Rechners, auf dem Nodvard Deck laeuft
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Ein HOME mit einem Standard-Schluessel, einer ssh-Konfiguration, die nur mit gelesener Datei
    stoert, und einem „ssh-agent“, der zaehlt, wer sich meldet."""
    import asyncio

    import asyncssh

    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(home / ".ssh" / "id_ed25519"))
    # Wuerde gelesen, koennte sich der Server nicht mehr mit Nodvard Deck auf einen Schluessel einigen.
    (home / ".ssh" / "config").write_text("Host *\n    HostKeyAlgorithms ssh-rsa\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    agent = {"connections": 0}

    async def _agent_client(reader, writer):
        agent["connections"] += 1
        writer.close()

    sock = str(tmp_path / "agent.sock")
    monkeypatch.setenv("SSH_AUTH_SOCK", sock)
    return agent, sock, _agent_client, asyncio


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ssh_password", "ssh_key"])
async def test_connect_uses_no_default_keys_no_agent_and_no_ssh_config(
    fake_home, ssh_server_factory, db_session, ssh_server_stats, kind
):
    import asyncssh

    agent, sock, handler, asyncio = fake_home
    agent_server = await asyncio.start_unix_server(handler, path=sock)
    try:
        host, port, username, password, _ = await ssh_server_factory(("ssh-ed25519",), offer_keys=True)
        if kind == "ssh_password":
            target = _target(host, port, username, password)
        else:
            own = asyncssh.generate_private_key("ssh-ed25519").export_private_key("openssh").decode()
            target = ssh.ConnectionTarget(
                host_id="host-1", address=host, port=port, username=username, kind="ssh_key", secret_value=own
            )
        if kind == "ssh_key":
            with pytest.raises(ssh.SshAuthError):  # der Server kennt diesen Schluessel nicht
                await ssh.connect(db_session, target)
            assert ssh_server_stats["public_key_tries"] == 1, "angeboten wird nur der eigene Schluessel"
        else:
            conn = await ssh.connect(db_session, target)
            conn.close()
            await conn.wait_closed()
            assert ssh_server_stats["public_key_tries"] == 0, "kein Schluessel aus ~/.ssh wird angeboten"
            assert ssh_server_stats["password_tries"] == 1
        assert agent["connections"] == 0, "der ssh-agent des Rechners wird nicht gefragt"
    finally:
        agent_server.close()
        await agent_server.wait_closed()


@pytest.mark.asyncio
async def test_inspect_host_key_ignores_home_and_agent(fake_home, ssh_server_factory, ssh_server_stats):
    agent, sock, handler, asyncio = fake_home
    agent_server = await asyncio.start_unix_server(handler, path=sock)
    try:
        host, port, _, _, _ = await ssh_server_factory(("ssh-ed25519",), offer_keys=True)
        report = await ssh.inspect_host_key(host, port, {}, timeout_s=5)
        assert report.status == "new", "die ssh-Konfiguration des Rechners wurde nicht gelesen"
        assert agent["connections"] == 0 and ssh_server_stats["begin_auth"] == 0
    finally:
        agent_server.close()
        await agent_server.wait_closed()
