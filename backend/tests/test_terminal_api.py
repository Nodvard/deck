"""POST /terminal/sessions + GET /ws/terminal/{id} -- Ende-zu-Ende gegen einen echten
lokalen SSH-Server UND eine echte, laufende FastAPI-App (`running_app`-Fixture,
siehe conftest.py) -- httpx' `ASGITransport` kann keine echten WebSockets bedienen,
ein In-Process-`uvicorn.Server` im selben Event-Loop schon (docs/04-API.md §4).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import AsyncClient

from nodvard_deck.services import extensions as extensions_service
from nodvard_deck.services import hosts as hosts_service

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"


@pytest.fixture(autouse=True)
def _cleanup_sys_path():
    before = list(sys.path)
    yield
    sys.path[:] = before
    for name in list(sys.modules):
        if name.startswith(("nodvard_deck_ext_", "lattice_ext_")):
            del sys.modules[name]


async def _bootstrap_owner(base_url: str) -> str:
    async with AsyncClient(base_url=base_url) as ac:
        await ac.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
        login = await ac.post("/api/v1/auth/login", json={"username": "owner1", "password": "correct-horse-battery"})
        assert login.status_code == 200, login.text
        return login.json()["access_token"]


@pytest.mark.asyncio
async def test_terminal_session_end_to_end(running_app, db_session, test_settings, local_ssh_server):
    import websockets

    http_base, ws_base = running_app
    host_addr, port, username, password, _ = local_ssh_server

    # Terminal-Extension echt aktivieren -- derselbe Mechanismus wie bei hello-world.
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    host = await hosts_service.create_host(db_session, name="test-host", address=host_addr)
    await hosts_service.add_credential(
        db_session, settings, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await db_session.commit()

    from nodvard_deck.main import app

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "terminal")

    token = await _bootstrap_owner(http_base)

    async with AsyncClient(base_url=http_base) as ac:
        created = await ac.post(
            "/api/v1/terminal/sessions",
            json={"host_id": host.id, "cols": 80, "rows": 24},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    ws_url = f"{ws_base}/api/v1/ws/terminal/{session_id}"
    async with websockets.connect(ws_url) as ws:
        first = await ws.recv()
        assert first == b"shell-ready\n"

        await ws.send(b"hallo\n")
        second = await ws.recv()
        assert second == b"echo:hallo\n"

        await ws.send(b"exit\n")
        # Nach "exit" beendet der Test-Server den Prozess -- der Kern meldet das als
        # {"type":"exit",...}-Textnachricht (docs/04 §4), bevor der Socket schliesst.
        exit_message = await ws.recv()
        import json

        payload = json.loads(exit_message)
        assert payload["type"] == "exit"


async def _connect_and_get_close_code(ws_url: str) -> int:
    """`accept()` laeuft immer zuerst (siehe api/v1/terminal.py) -- der Handshake
    gelingt also selbst bei einer Ablehnung; der Server schickt danach sofort einen
    Close-Frame mit dem passenden Code. `recv()` ist der zuverlaessige Weg, ihn zu
    beobachten (blosses Betreten/Verlassen von `async with` wirft nicht garantiert)."""
    import websockets
    from websockets.exceptions import ConnectionClosed

    async with websockets.connect(ws_url) as ws:
        try:
            await ws.recv()
        except ConnectionClosed as exc:
            return exc.rcvd.code if exc.rcvd else -1
    return -1


@pytest.mark.asyncio
async def test_unknown_ticket_is_rejected(running_app):
    _, ws_base = running_app
    code = await _connect_and_get_close_code(f"{ws_base}/api/v1/ws/terminal/does-not-exist")
    assert code == 4404


@pytest.mark.asyncio
async def test_ticket_is_single_use(running_app, db_session, test_settings):
    http_base, ws_base = running_app
    host = await hosts_service.create_host(db_session, name="a", address="1.1.1.1")
    await db_session.commit()

    token = await _bootstrap_owner(http_base)
    async with AsyncClient(base_url=http_base) as ac:
        created = await ac.post(
            "/api/v1/terminal/sessions",
            json={"host_id": host.id, "cols": 80, "rows": 24},
            headers={"Authorization": f"Bearer {token}"},
        )
    session_id = created.json()["session_id"]

    # Erster Verbindungsversuch verbraucht das Ticket (schlaegt fehl, weil keine
    # Extension eine TerminalTarget-Capability anbietet -- das ist hier irrelevant,
    # wichtig ist NUR, dass das Ticket danach weg ist).
    await _connect_and_get_close_code(f"{ws_base}/api/v1/ws/terminal/{session_id}")

    second_code = await _connect_and_get_close_code(f"{ws_base}/api/v1/ws/terminal/{session_id}")
    assert second_code == 4404


async def _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server):  # noqa: ANN202
    host_addr, port, username, password, _ = local_ssh_server
    settings = test_settings.model_copy(update={"extensions_dir": REPO_EXTENSIONS_DIR})
    host = await hosts_service.create_host(db_session, name="ssh-host", address=host_addr)
    await hosts_service.add_credential(
        db_session, settings, host_id=host.id, kind="ssh_password",
        username=username, port=port, secret_value=password,
    )
    await db_session.commit()

    from nodvard_deck.main import app

    await extensions_service.discover_and_sync(db_session, settings)
    await extensions_service.enable_extension(app, db_session, settings, "terminal")
    return host


@pytest.mark.asyncio
async def test_terminal_hosts_lists_only_hosts_a_session_can_open(running_app, db_session, test_settings, local_ssh_server):
    """Terminal-Seite: die Seite bietet nur Hosts an, fuer die eine Sitzung
    moeglich ist -- ein Host ohne SSH-Zugangsdaten taucht nicht auf."""
    http_base, _ = running_app
    ssh_host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    await hosts_service.create_host(db_session, name="no-credentials", address="10.0.0.9")
    await db_session.commit()

    token = await _bootstrap_owner(http_base)
    async with AsyncClient(base_url=http_base) as ac:
        resp = await ac.get("/api/v1/terminal/hosts", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == [ssh_host.id]


@pytest.mark.asyncio
async def test_terminal_session_is_audited_with_open_and_close(running_app, db_session, test_settings, local_ssh_server):
    """docs/04 §4 versprach `terminal.open`/`terminal.close` mit Dauer im Audit-Log --
    geschrieben wurde es nie (beim Bau der Terminal-Seite gefunden)."""
    import asyncio

    import websockets
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    token = await _bootstrap_owner(http_base)
    async with AsyncClient(base_url=http_base) as ac:
        created = await ac.post(
            "/api/v1/terminal/sessions", json={"host_id": host.id, "cols": 80, "rows": 24},
            headers={"Authorization": f"Bearer {token}"},
        )
    async with websockets.connect(f"{ws_base}{created.json()['ws_url']}") as ws:
        assert await ws.recv() == b"shell-ready\n"
        await ws.send(b"exit\n")
        await ws.recv()

    rows: list = []
    for _ in range(100):
        rows = (
            await db_session.execute(select(AuditEntry).where(AuditEntry.action.in_(["terminal.open", "terminal.close"])))
        ).scalars().all()
        if len(rows) == 2:
            break
        await asyncio.sleep(0.05)
    by_action = {r.action: r for r in rows}
    assert by_action["terminal.open"].outcome == "success"
    assert by_action["terminal.open"].target_id == host.id
    assert "duration_s" in by_action["terminal.close"].detail


# ---------------------------------------------------------------------------
# Laufende Sitzungen: Leerlauf, Deaktivieren, Rollenentzug, Passwortwechsel, Abmelden
# ---------------------------------------------------------------------------

_PASSWORD = "correct-horse-battery"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login_as(http_base: str, username: str, *, client_type: str = "web") -> dict:
    async with AsyncClient(base_url=http_base) as ac:
        r = await ac.post(
            "/api/v1/auth/login", json={"username": username, "password": _PASSWORD, "client_type": client_type}
        )
        assert r.status_code == 200, r.text
        return r.json()


async def _create_operator(http_base: str, owner_token: str, name: str = "bedienung") -> str:
    """Legt einen Nutzer mit der Rolle `operator` an (hat `hosts.execute`); gibt seine ID zurueck."""
    async with AsyncClient(base_url=http_base) as ac:
        roles = {r["name"]: r["id"] for r in (await ac.get("/api/v1/roles", headers=_bearer(owner_token))).json()}
        r = await ac.post(
            "/api/v1/users", json={"username": name, "password": _PASSWORD, "role_ids": [roles["operator"]]},
            headers=_bearer(owner_token),
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]


async def _open_ws_session(http_base: str, token: str, host_id: str) -> str:
    async with AsyncClient(base_url=http_base) as ac:
        created = await ac.post(
            "/api/v1/terminal/sessions", json={"host_id": host_id, "cols": 80, "rows": 24}, headers=_bearer(token)
        )
        assert created.status_code == 200, created.text
        return created.json()["ws_url"]


async def _expect_end(ws, *, within: float = 8.0) -> tuple[int, str]:
    """Liest bis zum Ende; gibt (Close-Code, letzte Fehlermeldung) zurueck."""
    import asyncio
    import json

    from websockets.exceptions import ConnectionClosed

    message = ""
    try:
        async with asyncio.timeout(within):
            while True:
                frame = await ws.recv()
                if isinstance(frame, str):
                    data = json.loads(frame)
                    if data.get("type") == "error":
                        message = data["message"]
    except ConnectionClosed as exc:
        return (exc.rcvd.code if exc.rcvd else -1), message
    raise AssertionError("Sitzung wurde nicht beendet")


async def _audit_close(db_session):
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    import asyncio

    for _ in range(100):
        rows = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "terminal.close"))).scalars().all()
        if rows:
            return rows[0]
        await asyncio.sleep(0.05)
    raise AssertionError("kein terminal.close")


@pytest.mark.asyncio
async def test_idle_terminal_session_is_closed(running_app, db_session, test_settings, local_ssh_server):
    import websockets

    test_settings.terminal_idle_timeout_s = 0.8
    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    token = await _bootstrap_owner(http_base)
    ws_url = await _open_ws_session(http_base, token, host.id)

    async with websockets.connect(f"{ws_base}{ws_url}") as ws:
        assert await ws.recv() == b"shell-ready\n"
        code, message = await _expect_end(ws)
    assert code == 4408
    assert "ohne Aktivität" in message
    close = await _audit_close(db_session)
    assert close.detail["ended_by"] == "idle"


@pytest.mark.asyncio
async def test_input_keeps_a_terminal_session_alive(running_app, db_session, test_settings, local_ssh_server):
    import asyncio

    import websockets

    test_settings.terminal_idle_timeout_s = 1.0
    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    token = await _bootstrap_owner(http_base)
    ws_url = await _open_ws_session(http_base, token, host.id)

    async with websockets.connect(f"{ws_base}{ws_url}") as ws:
        assert await ws.recv() == b"shell-ready\n"
        for i in range(6):  # 2,4 s, mehr als das Doppelte der Leerlaufgrenze
            await asyncio.sleep(0.4)
            await ws.send(f"x{i}\n".encode())
            assert await ws.recv() == f"echo:x{i}\n".encode()
        await ws.send(b"exit\n")


def _access_token_without_sid(test_settings, user_id: str) -> str:
    """Zugangs-Token wie vor Einfuehrung des `sid`-Claims: gueltig, aber ohne Bezug zu einer bestimmten
    Anmeldung (siehe `services/session_guard.py`: dann reicht irgendeine gueltige Anmeldung des Kontos)."""
    from nodvard_deck.core import security

    return security.create_jwt(
        subject=user_id, token_type="access", secret=test_settings.get_or_create_jwt_secret(), ttl_seconds=600
    )


async def _run_ended_case(
    running_app, db_session, test_settings, local_ssh_server, *, change, pause_guard, without_sid=False
):
    """Oeffnet als `bedienung` (Rolle operator) eine Sitzung, fuehrt `change` aus und gibt zurueck,
    wie die Sitzung endet. Mit `without_sid` kommt das Ticket von einem Zugangs-Token ohne `sid`."""
    import websockets

    from nodvard_deck.api.v1 import terminal as terminal_api

    test_settings.terminal_recheck_interval_s = 0.2
    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    owner_token = await _bootstrap_owner(http_base)
    user_id = await _create_operator(http_base, owner_token)
    login = await _login_as(http_base, "bedienung", client_type="cli")
    access_token = _access_token_without_sid(test_settings, user_id) if without_sid else login["access_token"]
    ws_url = await _open_ws_session(http_base, access_token, host.id)

    with pause_guard(terminal_api) as lock:
        async with websockets.connect(f"{ws_base}{ws_url}") as ws:
            assert await ws.recv() == b"shell-ready\n"
            async with lock:  # siehe conftest.py `pause_session_guard`
                await change(http_base, owner_token, login, user_id)
                await db_session.commit()
            return await _expect_end(ws)


@pytest.mark.asyncio
async def test_terminal_session_ends_when_the_account_is_deactivated(running_app, db_session, test_settings, local_ssh_server, pause_session_guard):
    async def change(http_base, owner_token, login, user_id):
        async with AsyncClient(base_url=http_base) as ac:
            r = await ac.patch(f"/api/v1/users/{user_id}", json={"is_active": False}, headers=_bearer(owner_token))
            assert r.status_code == 200, r.text

    code, message = await _run_ended_case(running_app, db_session, test_settings, local_ssh_server, change=change, pause_guard=pause_session_guard)
    assert code == 4401
    assert "nicht mehr aktiv" in message
    assert (await _audit_close(db_session)).detail["ended_by"] == "access_ended"


@pytest.mark.asyncio
async def test_terminal_session_ends_when_the_role_loses_the_permission(running_app, db_session, test_settings, local_ssh_server, pause_session_guard):
    async def change(http_base, owner_token, login, user_id):
        async with AsyncClient(base_url=http_base) as ac:
            roles = {r["name"]: r["id"] for r in (await ac.get("/api/v1/roles", headers=_bearer(owner_token))).json()}
            r = await ac.patch(f"/api/v1/users/{user_id}", json={"role_ids": [roles["viewer"]]}, headers=_bearer(owner_token))
            assert r.status_code == 200, r.text

    code, message = await _run_ended_case(running_app, db_session, test_settings, local_ssh_server, change=change, pause_guard=pause_session_guard)
    assert code == 4401
    assert "Berechtigung" in message


@pytest.mark.asyncio
async def test_terminal_session_ends_when_the_password_changes(running_app, db_session, test_settings, local_ssh_server, pause_session_guard):
    async def change(http_base, owner_token, login, user_id):
        async with AsyncClient(base_url=http_base) as ac:
            r = await ac.post(
                "/api/v1/me/password",
                json={"current_password": _PASSWORD, "new_password": "ganz-neues-passwort"},
                headers=_bearer(login["access_token"]),
            )
            assert r.status_code == 204, r.text

    code, message = await _run_ended_case(running_app, db_session, test_settings, local_ssh_server, change=change, pause_guard=pause_session_guard)
    assert code == 4401
    assert "Passwort" in message


@pytest.mark.asyncio
async def test_terminal_session_ends_after_logout(running_app, db_session, test_settings, local_ssh_server, pause_session_guard):
    async def change(http_base, owner_token, login, user_id):
        async with AsyncClient(base_url=http_base) as ac:
            r = await ac.post("/api/v1/auth/logout", json={"refresh_token": login["refresh_token"]})
            assert r.status_code in (200, 204), r.text

    code, message = await _run_ended_case(running_app, db_session, test_settings, local_ssh_server, change=change, pause_guard=pause_session_guard)
    assert code == 4401
    assert "abgemeldet" in message


@pytest.mark.asyncio
async def test_terminal_session_survives_while_nothing_changed(running_app, db_session, test_settings, local_ssh_server):
    """Die regelmaessige Pruefung beendet eine gesunde Sitzung nicht."""
    import asyncio

    import websockets

    test_settings.terminal_recheck_interval_s = 0.1
    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    owner_token = await _bootstrap_owner(http_base)
    await _create_operator(http_base, owner_token)
    login = await _login_as(http_base, "bedienung", client_type="cli")
    ws_url = await _open_ws_session(http_base, login["access_token"], host.id)
    async with websockets.connect(f"{ws_base}{ws_url}") as ws:
        assert await ws.recv() == b"shell-ready\n"
        await asyncio.sleep(1.0)
        await ws.send(b"noch da\n")
        assert await ws.recv() == b"echo:noch da\n"
        await ws.send(b"exit\n")


@pytest.mark.asyncio
async def test_ticket_is_refused_when_the_account_ended_before_connecting(running_app, db_session, test_settings, local_ssh_server):
    import websockets
    from sqlalchemy import select

    from nodvard_deck.models import AuditEntry

    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    owner_token = await _bootstrap_owner(http_base)
    user_id = await _create_operator(http_base, owner_token)
    login = await _login_as(http_base, "bedienung", client_type="cli")
    ws_url = await _open_ws_session(http_base, login["access_token"], host.id)

    async with AsyncClient(base_url=http_base) as ac:
        await ac.patch(f"/api/v1/users/{user_id}", json={"is_active": False}, headers=_bearer(owner_token))
    await db_session.commit()

    async with websockets.connect(f"{ws_base}{ws_url}") as ws:
        code, message = await _expect_end(ws)
    assert code == 4401
    assert "nicht mehr aktiv" in message
    denied = (await db_session.execute(select(AuditEntry).where(AuditEntry.action == "terminal.open"))).scalars().all()
    assert [e.outcome for e in denied] == ["denied"]


@pytest.mark.asyncio
async def test_terminal_session_ends_on_logout_even_with_another_login(
    running_app, db_session, test_settings, local_ssh_server, pause_session_guard
):
    """Die Sitzung haengt an der Anmeldung, aus der sie geoeffnet wurde -- eine zweite Anmeldung
    desselben Kontos (z. B. das Handy) haelt sie nach dem Abmelden nicht mehr am Leben."""

    async def change(http_base, owner_token, login, user_id):
        await _login_as(http_base, "bedienung", client_type="cli")  # zweites Geraet
        async with AsyncClient(base_url=http_base) as ac:
            r = await ac.post("/api/v1/auth/logout", json={"refresh_token": login["refresh_token"]})
            assert r.status_code in (200, 204), r.text

    code, message = await _run_ended_case(
        running_app, db_session, test_settings, local_ssh_server, change=change, pause_guard=pause_session_guard
    )
    assert code == 4401
    assert "abgemeldet" in message


@pytest.mark.asyncio
async def test_terminal_session_without_sid_ends_when_no_login_is_left(
    running_app, db_session, test_settings, local_ssh_server, pause_session_guard
):
    """Zugangs-Token ohne `sid`: die Sitzung haengt an keiner bestimmten Anmeldung, endet aber, sobald das
    Konto gar keine gueltige Anmeldung mehr hat (hier: die einzige wird abgemeldet)."""

    async def change(http_base, owner_token, login, user_id):
        async with AsyncClient(base_url=http_base) as ac:
            r = await ac.post("/api/v1/auth/logout", json={"refresh_token": login["refresh_token"]})
            assert r.status_code in (200, 204), r.text

    code, message = await _run_ended_case(
        running_app, db_session, test_settings, local_ssh_server,
        change=change, pause_guard=pause_session_guard, without_sid=True,
    )
    assert code == 4401
    assert "abgemeldet" in message
    assert (await _audit_close(db_session)).detail["ended_by"] == "access_ended"


@pytest.mark.asyncio
async def test_terminal_session_without_sid_survives_while_a_login_exists(
    running_app, db_session, test_settings, local_ssh_server, pause_session_guard
):
    """Zugangs-Token ohne `sid`: solange das Konto noch irgendeine gueltige Anmeldung hat (hier: ein zweites
    Geraet), laeuft die Sitzung weiter, auch nachdem die Anmeldung, die das Ticket geholt hat, abgemeldet wurde."""
    import asyncio

    import websockets
    from nodvard_deck.api.v1 import terminal as terminal_api

    test_settings.terminal_recheck_interval_s = 0.1
    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    owner_token = await _bootstrap_owner(http_base)
    user_id = await _create_operator(http_base, owner_token)
    first = await _login_as(http_base, "bedienung", client_type="cli")
    await _login_as(http_base, "bedienung", client_type="cli")  # zweites Geraet bleibt angemeldet
    ws_url = await _open_ws_session(http_base, _access_token_without_sid(test_settings, user_id), host.id)

    with pause_session_guard(terminal_api) as lock:
        async with websockets.connect(f"{ws_base}{ws_url}") as ws:
            assert await ws.recv() == b"shell-ready\n"
            async with lock:
                async with AsyncClient(base_url=http_base) as ac:
                    r = await ac.post("/api/v1/auth/logout", json={"refresh_token": first["refresh_token"]})
                    assert r.status_code in (200, 204), r.text
                await db_session.commit()
            await asyncio.sleep(0.8)  # mehrere Pruefungen
            await ws.send(b"noch da\n")
            assert await ws.recv() == b"echo:noch da\n"
            await ws.send(b"exit\n")


@pytest.mark.asyncio
async def test_terminal_idle_timeout_zero_never_closes_an_idle_session(running_app, db_session, test_settings, local_ssh_server):
    """`terminal_idle_timeout_s = 0` schaltet die Leerlauf-Grenze aus: die Sitzung bleibt auch ohne jede
    Eingabe offen (die regelmaessige Pruefung der Anmeldung laeuft trotzdem weiter)."""
    import asyncio

    import websockets

    test_settings.terminal_idle_timeout_s = 0
    test_settings.terminal_recheck_interval_s = 0.1
    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    token = await _bootstrap_owner(http_base)
    ws_url = await _open_ws_session(http_base, token, host.id)

    async with websockets.connect(f"{ws_base}{ws_url}") as ws:
        assert await ws.recv() == b"shell-ready\n"
        await asyncio.sleep(1.5)  # laenger als jede Grenze in den anderen Leerlauf-Tests
        await ws.send(b"noch da\n")
        assert await ws.recv() == b"echo:noch da\n"
        await ws.send(b"exit\n")


@pytest.mark.asyncio
async def test_terminal_session_survives_token_renewal_and_ends_on_logout_of_the_renewed_login(
    running_app, db_session, test_settings, local_ssh_server, pause_session_guard
):
    """Erneuern ersetzt die Anmeldungs-Zeile: die Pruefung folgt der Kette und meldet die Sitzung
    nicht faelschlich ab. Wird die erneuerte Anmeldung abgemeldet, endet sie."""
    import asyncio

    import websockets

    from nodvard_deck.api.v1 import terminal as terminal_api

    test_settings.terminal_recheck_interval_s = 0.1
    http_base, ws_base = running_app
    host = await _enable_terminal_with_ssh_host(db_session, test_settings, local_ssh_server)
    owner_token = await _bootstrap_owner(http_base)
    await _create_operator(http_base, owner_token)
    login = await _login_as(http_base, "bedienung", client_type="cli")
    ws_url = await _open_ws_session(http_base, login["access_token"], host.id)

    with pause_session_guard(terminal_api) as lock:
        async with websockets.connect(f"{ws_base}{ws_url}") as ws:
            assert await ws.recv() == b"shell-ready\n"
            refresh_token = login["refresh_token"]
            for i in range(2):
                async with lock:
                    async with AsyncClient(base_url=http_base) as ac:
                        r = await ac.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
                        assert r.status_code == 200, r.text
                        refresh_token = r.json()["refresh_token"]
                    await db_session.commit()
                await asyncio.sleep(0.5)
                await ws.send(f"noch da {i}\n".encode())
                assert await ws.recv() == f"echo:noch da {i}\n".encode()

            async with lock:
                async with AsyncClient(base_url=http_base) as ac:
                    r = await ac.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
                    assert r.status_code in (200, 204), r.text
                await db_session.commit()
            code, message = await _expect_end(ws)
    assert code == 4401
    assert "abgemeldet" in message
