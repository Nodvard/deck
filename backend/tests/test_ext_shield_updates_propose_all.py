"""Nodvard Shield, Update-Zentrale: "Alle Updates vorschlagen" (`POST /defender/updates/propose-all`).

Ein Aufruf legt fuer viele Server je einen Update-Vorschlag an. Jeder geht durch dieselbe Hilfsfunktion wie der
Einzelweg (`POST /defender/hosts/{id}/upgrade`): gleiche Felder, gleicher Befehl, gleiches Risiko. Freigegeben wird
danach wie sonst unter "Aktionen".

Der erste Teil laeuft gegen Attrappen (Kontext, Update-Zentrale, Gate) und prueft die Regeln im Einzelnen; der zweite
gegen die echte App mit dem echten Gate: offene Vorschlaege finden, Rechte. Die alte Kennung der Erweiterung (Adresse,
Vorschlaege aus der Zeit davor) pruefen die Alias-Tests in `test_ext_shield_updates_propose_all_legacy.py`."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from nodvard_sdk import (
    REQUEST_WAIT_S,
    ActionRequest,
    Actor,
    GateDecision,
    GateOutcome,
    Host,
    Risk,
)
from nodvard_sdk.actions import ActionStatus

REPO_EXTENSIONS_DIR = Path(__file__).resolve().parents[2] / "extensions"
sys.path.insert(0, str(REPO_EXTENSIONS_DIR / "shield" / "src"))

from nodvard_deck_ext_shield import updates as up

SECURITY = ["libssl3", "openssl"]
NOW = datetime.now(UTC)


# --- Attrappen -----------------------------------------------------------------------------------


def _state(count: int = 3, security: int = 2, manager: str | None = "apt", **extra) -> dict:
    return {"manager": manager, "count": count, "security_count": security, "error": None, **extra}


def _row(host_id: str, name: str, status: dict | None, busy: str | None = None) -> dict:
    return {"host_id": host_id, "host_name": name, "host_status": "up", "status": status, "checking": False, "busy": busy,
            "last_run": None}


def _open(host_id: str, status: str = "proposed", mode: str = "all", *, expires_in: timedelta | None = timedelta(hours=20),
          action_type: str = "nexus_soc.upgrade") -> SimpleNamespace:
    """Eine Zeile, wie `ctx.actions.list` sie liefert (nur die Felder, die der Sammel-Vorschlag liest)."""
    return SimpleNamespace(action_type=action_type, host_id=host_id, status=status, payload={"mode": mode},
                           expires_at=None if expires_in is None else NOW + expires_in)


class _Ctx:
    """Nur was die Routen des Sammel-Vorschlags brauchen."""

    def __init__(self, names: dict[str, str]) -> None:
        self._hosts = {hid: Host(id=hid, name=name.lower(), display_name=name, address="192.168.2.10") for hid, name in names.items()}
        self.proposals: list[ActionRequest] = []
        self.wait_s: list[float | None] = []
        self.open_actions: list[SimpleNamespace] = []
        self.list_limits: list[int] = []
        self.audit_entries: list[dict] = []
        self.settings_value: dict = {}
        self.denied: dict[str, str] = {}  # host_ref -> Grund, das Gate lehnt ab
        self.raising: dict[str, Exception] = {}  # host_ref -> Ausnahme beim Vorschlagen
        self.audit_raises = False
        self.hold: asyncio.Event | None = None  # solange nicht gesetzt, haengt `propose`
        self.api = SimpleNamespace(current_actor=lambda: Actor.user("u1", "nico"))
        self.hosts = SimpleNamespace(get=self._get)
        self.actions = SimpleNamespace(propose=self._propose, list=self._list)
        self.settings = SimpleNamespace(get=self._settings)
        self.audit = SimpleNamespace(log=self._log)

    async def _get(self, host_id):
        return self._hosts.get(host_id)

    async def _settings(self):
        return self.settings_value

    async def _list(self, *, correlation_id=None, limit=20):
        self.list_limits.append(limit)
        return list(self.open_actions)

    async def _log(self, **entry):
        if self.audit_raises:
            raise RuntimeError("Protokoll nicht erreichbar")
        self.audit_entries.append(entry)

    async def _propose(self, req: ActionRequest, *, wait_s=None) -> GateDecision:
        if self.hold is not None:
            await self.hold.wait()
        if req.host_ref in self.raising:
            raise self.raising[req.host_ref]
        self.proposals.append(req)
        self.wait_s.append(wait_s)
        action_id = f"a{len(self.proposals)}"
        if req.host_ref in self.denied:
            return GateDecision(action_id=action_id, outcome=GateOutcome.DENY, status=ActionStatus.DENIED,
                                rule="flap_limit", detail=self.denied[req.host_ref])
        return GateDecision(action_id=action_id, outcome=GateOutcome.REQUIRE_CONFIRMATION, status=ActionStatus.PROPOSED,
                            rule="autonomy:propose")


class _Updates:
    """Update-Zentrale ohne Server: feste Zeilen der Uebersicht, der Plan kommt aus dem Paketmanager hier."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.manager = "apt"
        self.plan_errors: dict[str, Exception] = {}
        self.planned: list[tuple[str, str]] = []

    async def overview(self):
        return {"hosts": self.rows}

    async def plan(self, host, mode):
        self.planned.append((host.id, mode))
        if host.id in self.plan_errors:
            raise self.plan_errors[host.id]
        return self.manager, list(SECURITY) if mode == "security" else []


def _by_host(body: dict) -> dict[str, dict]:
    return {r["host_id"]: r for r in body["results"]}


@pytest.fixture
async def soc():
    from nodvard_deck_ext_shield.defender_api import build_routers

    ctx = _Ctx({"h-pi": "Raspberry Pi", "h-web": "web", "h-nas": "nas", "h-db": "db", "h-new": "neu"})
    updates = _Updates([
        _row("h-pi", "Raspberry Pi", _state()),
        _row("h-web", "web", _state(count=5, security=1)),
        _row("h-nas", "nas", _state(count=0, security=0)),
        _row("h-db", "db", _state(count=2, security=0)),
        _row("h-new", "neu", None),
    ])
    # Der Sammel-Vorschlag braucht von der Verteidigung nichts (nur `ActionCommands` haelt sie fuer Funde).
    _read, manage = build_routers(ctx, SimpleNamespace(), updates, None)
    app = FastAPI()
    app.include_router(manage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://soc") as client:
        yield SimpleNamespace(ctx=ctx, updates=updates, client=client)


async def _all(soc, mode: str = "security", **extra) -> dict:
    r = await soc.client.post("/defender/updates/propose-all", json={"mode": mode, **extra})
    assert r.status_code == 200, r.text
    return r.json()


# --- Welche Server -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_host_ids_every_server_with_something_open_gets_a_proposal(soc):
    body = await _all(soc, "security")
    # Nur Server mit offenen Sicherheitsupdates: nas (aktuell), db (nur normale Updates) und neu (nie geprueft)
    # sind gar nicht erst Kandidaten und stehen nicht im Ergebnis.
    assert [r["host_id"] for r in body["results"]] == ["h-pi", "h-web"]
    assert body["mode"] == "security"
    assert body["counts"] == {"proposed": 2, "skipped": 0, "failed": 0, "total": 2}
    pi = _by_host(body)["h-pi"]
    assert (pi["name"], pi["result"], pi["status"], pi["risk"], pi["action_id"]) == ("Raspberry Pi", "proposed", "proposed", "medium", "a1")
    assert "reason" not in pi
    assert [(p.host_ref, p.action_type) for p in soc.ctx.proposals] == [("h-pi", "nexus_soc.upgrade"), ("h-web", "nexus_soc.upgrade")]

    soc.ctx.proposals.clear()
    body = await _all(soc, "all")
    # "Alle Updates": jeder Server mit mindestens einem offenen Update (auch db, das nur normale hat).
    assert [r["host_id"] for r in body["results"]] == ["h-pi", "h-web", "h-db"]
    assert body["counts"]["proposed"] == 3


@pytest.mark.asyncio
async def test_a_server_with_an_open_proposal_is_skipped_and_gets_no_second_one(soc):
    soc.ctx.open_actions = [
        _open("h-pi", "proposed", "security"),
        _open("h-web", "executing", "all"),
        _open("h-db", "approved", "all"),
    ]
    body = await _all(soc, "all")
    got = _by_host(body)
    assert got["h-pi"]["result"] == "skipped" and got["h-pi"]["reason"] == "Vorschlag „Sicherheitsupdates“ wartet schon auf Freigabe."
    assert got["h-web"]["result"] == "skipped" and got["h-web"]["reason"] == "„Alle Updates“ ist schon freigegeben und läuft."
    assert got["h-db"]["result"] == "skipped"
    assert body["counts"] == {"proposed": 0, "skipped": 3, "failed": 0, "total": 3}
    assert soc.ctx.proposals == []
    assert soc.updates.planned == [], "nicht einmal der Plan wird fuer einen uebersprungenen Server gebaut"


@pytest.mark.asyncio
async def test_only_open_proposals_of_this_action_type_count(soc):
    soc.ctx.open_actions = [
        _open("h-pi", "succeeded"), _open("h-pi", "failed"), _open("h-pi", "denied"), _open("h-pi", "dismissed"), _open("h-pi", "expired"),
        _open("h-web", "proposed", action_type="nexus_soc.reboot"),  # ein offener Neustart ist kein Update-Vorschlag
        _open("h-db", "proposed", action_type="nexus_soc.install"),
    ]
    body = await _all(soc, "all")
    assert body["counts"] == {"proposed": 3, "skipped": 0, "failed": 0, "total": 3}


@pytest.mark.asyncio
async def test_a_proposal_whose_deadline_passed_does_not_block_even_if_not_yet_marked_expired(soc):
    soc.ctx.open_actions = [
        _open("h-pi", "proposed", expires_in=-timedelta(minutes=1)),  # abgelaufen, die Markierung kommt erst spaeter
        _open("h-web", "proposed", expires_in=None),  # ohne Frist bleibt offen
    ]
    soc.ctx.open_actions[0].expires_at = soc.ctx.open_actions[0].expires_at.replace(tzinfo=None)  # naiv aus der Datenbank
    got = _by_host(await _all(soc, "security"))
    assert got["h-pi"]["result"] == "proposed"
    assert got["h-web"]["result"] == "skipped"


@pytest.mark.asyncio
async def test_the_newest_open_proposal_of_the_server_is_the_one_named(soc):
    # `ctx.actions.list` liefert die neuesten zuerst: die Freigabe von eben steht vor dem aelteren Vorschlag.
    soc.ctx.open_actions = [_open("h-pi", "executing", "security"), _open("h-pi", "proposed", "all")]
    got = _by_host(await _all(soc, "security"))
    assert got["h-pi"]["reason"] == "„Sicherheitsupdates“ ist schon freigegeben und läuft."


@pytest.mark.asyncio
async def test_a_server_that_is_busy_installing_updates_is_skipped(soc):
    soc.updates.rows[0]["busy"] = "all"
    got = _by_host(await _all(soc, "security"))
    assert got["h-pi"] == {"host_id": "h-pi", "name": "Raspberry Pi", "result": "skipped", "reason": "Es läuft gerade ein Einspiel-Lauf."}
    assert got["h-web"]["result"] == "proposed"


@pytest.mark.asyncio
async def test_host_ids_choose_the_servers_unknown_ones_are_skipped_and_doubles_count_once(soc):
    body = await _all(soc, "security", host_ids=["h-web", "h-nope", "h-web", "h-nas", "h-new"])
    got = _by_host(body)
    assert [r["host_id"] for r in body["results"]] == ["h-web", "h-nope", "h-nas", "h-new"], "Reihenfolge der Anfrage, jeder Server einmal"
    assert got["h-web"]["result"] == "proposed"
    assert got["h-nope"]["result"] == "skipped" and "Unbekannter Server" in got["h-nope"]["reason"]
    assert got["h-nas"] == {"host_id": "h-nas", "name": "nas", "result": "skipped", "reason": "Keine Sicherheitsupdates offen."}
    assert got["h-new"]["result"] == "skipped" and got["h-new"]["reason"] == "Noch nicht geprüft."
    assert body["counts"] == {"proposed": 1, "skipped": 3, "failed": 0, "total": 4}
    assert [p.host_ref for p in soc.ctx.proposals] == ["h-web"]
    assert (await _all(soc, "all", host_ids=[]))["results"] == [], "eine leere Auswahl legt nichts an"


@pytest.mark.asyncio
async def test_servers_the_update_center_cannot_handle_are_skipped_with_the_reason(soc):
    soc.updates.rows += [
        _row("h-fw", "Firmware-NAS", _state(count=0, security=0, manager=None, unsupported=True)),
        _row("h-err", "pve2", _state(count=0, security=0, manager=None, error="Zeitüberschreitung")),
    ]
    got = _by_host(await _all(soc, "all", host_ids=["h-fw", "h-err"]))
    assert got["h-fw"]["reason"] == "Kein Paketmanager, den die Update-Zentrale kennt."
    assert got["h-err"]["reason"] == "Zeitüberschreitung"
    assert soc.ctx.proposals == []


@pytest.mark.asyncio
async def test_only_all_and_security_are_modes_of_the_batch_cleanup_and_reboot_stay_single_decisions(soc):
    for mode in ("cleanup", "reboot", "irgendwas"):
        for extra in ({}, {"host_ids": ["h-nas"]}):
            r = await soc.client.post("/defender/updates/propose-all", json={"mode": mode, **extra})
            assert r.status_code == 422, (mode, extra, r.text)
    assert soc.ctx.proposals == []
    # Dieselben Arten gehen weiter einzeln je Server.
    assert (await soc.client.post("/defender/hosts/h-nas/upgrade", json={"mode": "cleanup"})).status_code == 200
    assert soc.ctx.proposals[0].reason == "Aufräumen auf nas"


# --- Derselbe Weg wie der Einzelvorschlag -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_each_proposal_is_exactly_what_the_single_route_proposes(soc):
    for mode in ("security", "all"):
        soc.ctx.proposals.clear()
        single = {}
        for host_id in ("h-pi", "h-web"):
            r = await soc.client.post(f"/defender/hosts/{host_id}/upgrade", json={"mode": mode})
            assert r.status_code == 200, r.text
            single[host_id] = soc.ctx.proposals[-1]
        soc.ctx.proposals.clear()
        await _all(soc, mode, host_ids=["h-pi", "h-web"])
        assert len(soc.ctx.proposals) == 2
        for req in soc.ctx.proposals:
            want = single[req.host_ref]
            assert req.model_dump() == want.model_dump(), mode  # Felder, Befehl, Grund, Risiko, Vorschlagender
    want_command = up.upgrade_command("apt", "security", SECURITY)
    soc.ctx.proposals.clear()
    await _all(soc, "security", host_ids=["h-pi"])
    assert soc.ctx.proposals[0].payload == {"mode": "security", "manager": "apt", "packages": SECURITY, "command": want_command}
    assert soc.ctx.proposals[0].reason == "Sicherheitsupdates auf Raspberry Pi"
    assert soc.ctx.proposals[0].proposed_by == Actor.user("u1", "nico")


@pytest.mark.asyncio
async def test_the_dist_upgrade_option_raises_the_risk_like_on_the_single_route(soc):
    soc.ctx.settings_value = {"proxmox_dist_upgrade": True}
    body = await _all(soc, "all")
    for req in soc.ctx.proposals:
        assert req.payload["dist_upgrade"] is True and "dist-upgrade" in req.payload["command"]
        assert "falls der Server Proxmox ist" in req.reason
        assert req.risk == Risk.HIGH
    assert {r["risk"] for r in body["results"]} == {"high"}
    # Nur "Alle Updates" und nur apt: Sicherheitsupdates bleiben "mittel", ohne dist-upgrade.
    soc.ctx.proposals.clear()
    body = await _all(soc, "security")
    assert {r["risk"] for r in body["results"]} == {"medium"}
    assert all("dist_upgrade" not in p.payload for p in soc.ctx.proposals)
    soc.updates.manager = "dnf"
    soc.ctx.proposals.clear()
    await _all(soc, "all")
    assert all("dist_upgrade" not in p.payload and p.risk == Risk.MEDIUM for p in soc.ctx.proposals)


@pytest.mark.asyncio
async def test_the_batch_does_not_wait_for_results_but_the_single_route_still_does(soc):
    await _all(soc, "security")
    assert soc.ctx.wait_s == [0.0, 0.0]
    soc.ctx.wait_s.clear()
    assert (await soc.client.post("/defender/hosts/h-pi/upgrade", json={"mode": "all"})).status_code == 200
    assert soc.ctx.wait_s == [REQUEST_WAIT_S]


@pytest.mark.asyncio
async def test_the_gate_decides_the_status_so_an_auto_approved_action_is_reported_as_running(soc):
    # "Selbststaendig handeln": das Gate gibt frei, die Aktion laeuft schon. Der Vorschlag steht trotzdem im Ergebnis.
    original = soc.ctx._propose

    async def auto(req, *, wait_s=None):
        decision = await original(req, wait_s=wait_s)
        return decision.model_copy(update={"status": ActionStatus.EXECUTING, "outcome": GateOutcome.ALLOW})

    soc.ctx.actions.propose = auto
    body = await _all(soc, "security")
    assert {(r["result"], r["status"]) for r in body["results"]} == {("proposed", "executing")}


# --- Fehler eines Servers halten die anderen nicht auf ------------------------------------------------------


def _add_host(soc, host_id: str, name: str, plan_error: Exception | None = None) -> None:
    soc.updates.rows.append(_row(host_id, name, _state()))
    soc.ctx._hosts[host_id] = Host(id=host_id, name=name, display_name=name, address="192.168.2.11")
    if plan_error is not None:
        soc.updates.plan_errors[host_id] = plan_error


@pytest.mark.asyncio
async def test_an_error_on_one_server_does_not_stop_the_others(soc):
    soc.updates.plan_errors["h-pi"] = ValueError("Keine Sicherheitsupdates offen.")
    _add_host(soc, "h-ssh", "ssh-weg", ConnectionError("Verbindung abgerissen"))
    soc.ctx.raising["h-web"] = RuntimeError("Datenbank gesperrt")
    _add_host(soc, "h-ok", "ok")

    body = await _all(soc, "security")
    got = _by_host(body)
    # Der Grund des Plans ist ein Satz der Erweiterung fuer Nutzer (wie auf der Einzelroute) und steht im Wortlaut da.
    assert got["h-pi"] == {"host_id": "h-pi", "name": "Raspberry Pi", "result": "failed", "reason": "Keine Sicherheitsupdates offen."}
    assert got["h-ssh"]["result"] == "failed" and got["h-web"]["result"] == "failed"
    assert got["h-ok"]["result"] == "proposed", "der Server nach den Fehlern bekommt seinen Vorschlag"
    assert body["counts"] == {"proposed": 1, "skipped": 0, "failed": 3, "total": 4}
    assert [p.host_ref for p in soc.ctx.proposals] == ["h-ok"]


@pytest.mark.asyncio
async def test_an_unexpected_exception_never_puts_its_text_into_the_answer_but_stays_in_the_log(soc, caplog, monkeypatch):
    from nodvard_deck_ext_shield.defender_api import NOT_PROPOSED_MESSAGE

    # Ein Datenbankfehler traegt SQL-Text und Parameter -- das gehoert ins Protokoll, nicht zum Client.
    sql = ("(sqlite3.OperationalError) database is locked [SQL: INSERT INTO actions (id, payload) VALUES (?, ?)] "
           "[parameters: ('a1b2', '{\"command\": \"apt-get install geheim\"}')]")
    soc.ctx.raising["h-web"] = RuntimeError(sql)
    _add_host(soc, "h-ssh", "ssh-weg", ConnectionError("[Errno 111] Connect call failed ('192.168.2.11', 22)"))
    _add_host(soc, "h-ok", "ok")
    logger = logging.getLogger("nodvard_deck.ext.shield")
    monkeypatch.setattr(logger, "disabled", False)  # ein frueher Alembic-Lauf (fileConfig) kann ihn abschalten

    with caplog.at_level(logging.ERROR, logger="nodvard_deck.ext.shield"):
        r = await soc.client.post("/defender/updates/propose-all", json={"mode": "security"})
    assert r.status_code == 200, r.text
    got = _by_host(r.json())
    assert got["h-web"] == {"host_id": "h-web", "name": "web", "result": "failed", "reason": NOT_PROPOSED_MESSAGE}
    assert got["h-ssh"] == {"host_id": "h-ssh", "name": "ssh-weg", "result": "failed", "reason": NOT_PROPOSED_MESSAGE}
    assert NOT_PROPOSED_MESSAGE == "Der Vorschlag konnte nicht angelegt werden (Einzelheiten im Protokoll)."
    for leaked in ("SQL", "INSERT", "parameters", "geheim", "locked", "Errno", "192.168.2.11"):
        assert leaked not in r.text, leaked
    # Die anderen Server laufen weiter ...
    assert got["h-pi"]["result"] == "proposed" and got["h-ok"]["result"] == "proposed"
    assert r.json()["counts"] == {"proposed": 2, "skipped": 0, "failed": 2, "total": 4}
    # ... und der volle Fehler steht im Protokoll.
    logged = "\n".join(f"{rec.getMessage()}\n{rec.exc_text or ''}\n{rec.exc_info[1] if rec.exc_info else ''}" for rec in caplog.records)
    assert "shield_propose_all_failed host=h-web" in logged and "database is locked" in logged


@pytest.mark.asyncio
async def test_an_error_meant_for_users_keeps_its_wording_and_a_timeout_gets_a_fixed_sentence(soc):
    class ReadableError(Exception):
        """Wie die Fehler der SSH-Verbindung (`core.ssh.SshError`): der Text ist ein fertiger deutscher Satz."""

        readable = True

    _add_host(soc, "h-down", "down", ReadableError("Server antwortet nicht (Zeitüberschreitung bei der Anmeldung)."))
    _add_host(soc, "h-slow", "slow", TimeoutError("interner Zaehlerstand 42"))
    got = _by_host(await _all(soc, "security"))
    assert got["h-down"]["reason"] == "Server antwortet nicht (Zeitüberschreitung bei der Anmeldung)."
    assert got["h-slow"]["result"] == "failed" and "Zeitüberschreitung" in got["h-slow"]["reason"]
    assert "42" not in got["h-slow"]["reason"]


@pytest.mark.asyncio
async def test_a_server_that_vanished_between_overview_and_proposal_fails_alone(soc):
    del soc.ctx._hosts["h-pi"]
    got = _by_host(await _all(soc, "security"))
    assert got["h-pi"] == {"host_id": "h-pi", "name": "Raspberry Pi", "result": "failed", "reason": "Server nicht mehr bekannt."}
    assert got["h-web"]["result"] == "proposed"


@pytest.mark.asyncio
async def test_a_proposal_the_gate_denies_is_a_failure_with_its_reason_and_keeps_the_action_id(soc):
    soc.ctx.denied["h-pi"] = "Bereits mehrfach in kurzer Zeit versucht."
    got = _by_host(await _all(soc, "security"))
    assert got["h-pi"]["result"] == "failed" and got["h-pi"]["reason"] == "Bereits mehrfach in kurzer Zeit versucht."
    assert got["h-pi"]["status"] == "denied" and got["h-pi"]["action_id"] == "a1"
    assert got["h-web"]["result"] == "proposed"


# --- Protokoll und Sperre gegen doppelte Anfragen -------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_batch_is_one_audit_entry_with_the_counts(soc):
    soc.ctx.open_actions = [_open("h-web", "proposed")]
    await _all(soc, "security")
    (entry,) = soc.ctx.audit_entries
    assert entry["action"] == "shield.updates_proposed_all" and entry["outcome"] == "success"
    assert entry["actor"] == Actor.user("u1", "nico")
    assert entry["detail"] == {"mode": "security", "proposed": 1, "skipped": 1, "failed": 0, "total": 2, "host_ids": ["h-pi"]}
    assert entry["reason"] == "Sicherheitsupdates: 1 vorgeschlagen, 1 übersprungen, 0 fehlgeschlagen"
    # Alles gescheitert -> "failure"; nichts zu tun -> kein Fehler.
    soc.ctx.audit_entries.clear()
    soc.ctx.raising.update({"h-pi": RuntimeError("x"), "h-web": RuntimeError("y")})
    await _all(soc, "security")
    assert soc.ctx.audit_entries[0]["outcome"] == "failure"
    soc.ctx.audit_entries.clear()
    await _all(soc, "security", host_ids=[])
    assert soc.ctx.audit_entries[0]["outcome"] == "success"


@pytest.mark.asyncio
async def test_a_failing_audit_line_does_not_hide_the_proposals_that_exist(soc):
    soc.ctx.audit_raises = True
    body = await _all(soc, "security")
    assert body["counts"]["proposed"] == 2


@pytest.mark.asyncio
async def test_a_second_request_during_a_running_batch_is_refused_not_doubled(soc):
    soc.ctx.hold = asyncio.Event()
    first = asyncio.ensure_future(soc.client.post("/defender/updates/propose-all", json={"mode": "security"}))
    try:
        for _ in range(50):
            await asyncio.sleep(0.01)
            if soc.updates.planned:
                break
        assert soc.updates.planned, "der erste Aufruf steckt im Gate"
        # Ohne die Sperre wuerde die zweite Anfrage ebenfalls im Gate haengen: darum mit Frist, damit ein Fehler nicht blockiert.
        second = await asyncio.wait_for(soc.client.post("/defender/updates/propose-all", json={"mode": "security"}), timeout=5)
        assert second.status_code == 409 and "gerade schon Vorschläge" in second.json()["detail"]
    finally:
        soc.ctx.hold.set()
    done = await first
    assert done.status_code == 200 and done.json()["counts"]["proposed"] == 2
    assert (await soc.client.post("/defender/updates/propose-all", json={"mode": "security"})).status_code == 200, "danach geht es wieder"


@pytest.mark.asyncio
async def test_open_proposals_are_looked_up_with_the_largest_window_the_sdk_allows(soc):
    await _all(soc, "security")
    assert soc.ctx.list_limits == [200]
    soc.ctx.list_limits.clear()
    await _all(soc, "security", host_ids=[])
    assert soc.ctx.list_limits == [], "ohne Server keine Abfrage"


# --- Echte App: echtes Gate, echte Rechte, alte Adresse -------------------------------------------------------


@pytest.fixture
async def shield_tables(db_session):
    """Die Tabellen der Erweiterung (die Test-Datenbank legt nur die Kern-Tabellen an) -- ihr Modul, fuer Zeilen."""
    import importlib.util

    name = "_shield_models_for_propose_all_tests"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, REPO_EXTENSIONS_DIR / "shield" / "src" / "nodvard_deck_ext_shield" / "models.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # SQLAlchemy loest `Mapped[...]` ueber sys.modules auf
        spec.loader.exec_module(module)
    conn = await db_session.connection()
    await conn.run_sync(module.Base.metadata.create_all)
    return module


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, username: str, password: str = "correct-horse-battery") -> dict:
    login = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return _auth(login.json()["access_token"])


async def _real_app(client, db_session, test_settings, shield_tables, monkeypatch):
    """Shield eingeschaltet, drei Server mit gespeichertem Update-Stand, dazu ein Leser ohne Recht `soc.manage`."""
    from nodvard_deck.core import security
    from nodvard_deck.ext.runtime import get_extension_runtime
    from nodvard_deck.models import User
    from nodvard_deck.services import auth as auth_service
    from nodvard_deck.services import extensions as extensions_service

    test_settings.extensions_dir = REPO_EXTENSIONS_DIR
    await extensions_service.discover_and_sync(db_session, test_settings)
    await client.post("/api/v1/auth/bootstrap", json={"username": "owner1", "password": "correct-horse-battery", "setup_code": "TEST-CODE-2345"})
    owner = await _login(client, "owner1")
    enabled = await client.post("/api/v1/extensions/shield/enable", headers=owner)
    assert enabled.status_code == 200, enabled.text

    roles = await auth_service.ensure_builtin_roles(db_session)
    viewer = User(username="leser1", password_hash=security.hash_password("correct-horse-battery"), is_active=True)
    viewer.roles.append(roles["viewer"])
    db_session.add(viewer)

    ids: dict[str, str] = {}
    for name, address in (("pi", "192.168.2.10"), ("web", "192.168.2.11"), ("nas", "192.168.2.12")):
        created = await client.post("/api/v1/hosts", json={"name": name, "address": address}, headers=owner)
        assert created.status_code in (200, 201), created.text
        ids[name] = created.json()["id"]
    packages = [
        {"name": "openssl", "new_version": "3.0.14", "current_version": "3.0.13", "repo": "security", "security": True},
        {"name": "base-files", "new_version": "12.4", "current_version": "12.3", "repo": "stable", "security": False},
    ]
    stands = {"pi": (2, 1, packages), "web": (2, 1, packages), "nas": (0, 0, [])}
    for name, (count, security_count, pkgs) in stands.items():
        db_session.add(shield_tables.BaselineRecord(
            id=f"{ids[name]}:updates", host_id=ids[name], kind="updates", updated_at=NOW,
            data={"manager": "apt", "count": count, "security_count": security_count, "packages": pkgs, "error": None,
                  "reboot_required": False, "checked_at": NOW.timestamp(), "attempted_at": NOW.timestamp()},
        ))
    await db_session.commit()

    loaded = get_extension_runtime().loaded["shield"]

    async def every_host():
        return await loaded.ctx.hosts.list()  # die Test-Server haben keinen SSH-Zugang und waeren sonst nicht "pruefbar"

    monkeypatch.setattr(loaded.instance._defender, "target_hosts", every_host)
    return SimpleNamespace(owner=owner, viewer=await _login(client, "leser1"), ids=ids, client=client)


async def _upgrade_actions(db_session) -> list:
    from nodvard_deck.models import Action
    from sqlalchemy import select

    await db_session.commit()
    db_session.expire_all()
    return list((await db_session.execute(select(Action).where(Action.action_type == "nexus_soc.upgrade").order_by(Action.created_at))).scalars())


@pytest.mark.asyncio
async def test_real_app_proposes_through_the_gate_and_finds_its_own_open_proposals(client, db_session, test_settings, shield_tables, monkeypatch):
    app = await _real_app(client, db_session, test_settings, shield_tables, monkeypatch)
    url = "/api/v1/ext/shield/defender/updates/propose-all"

    first = await client.post(url, json={"mode": "security"}, headers=app.owner)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["counts"] == {"proposed": 2, "skipped": 0, "failed": 0, "total": 2}
    assert {r["host_id"] for r in body["results"]} == {app.ids["pi"], app.ids["web"]}
    assert {(r["status"], r["risk"]) for r in body["results"]} == {("proposed", "medium")}

    rows = await _upgrade_actions(db_session)
    assert {r.host_id for r in rows} == {app.ids["pi"], app.ids["web"]}
    for row in rows:
        assert (row.ext_id, row.status, row.risk) == ("shield", "proposed", "medium")
        assert row.payload["mode"] == "security" and row.payload["packages"] == ["openssl"]
        assert row.payload["command"] == up.upgrade_command("apt", "security", ["openssl"])
        assert row.proposed_by_type == "user"  # der Mensch hinter dem Klick, nicht die Erweiterung
        assert row.reason in ("Sicherheitsupdates auf pi", "Sicherheitsupdates auf web")

    # Ein zweiter Klick findet die offenen Vorschlaege wieder: nichts Neues.
    again = await client.post(url, json={"mode": "security"}, headers=app.owner)
    assert again.json()["counts"] == {"proposed": 0, "skipped": 2, "failed": 0, "total": 2}
    assert {r["reason"] for r in again.json()["results"]} == {"Vorschlag „Sicherheitsupdates“ wartet schon auf Freigabe."}
    assert len(await _upgrade_actions(db_session)) == 2

    # Ein abgelehnter Vorschlag ist nicht mehr offen: dieser Server bekommt einen neuen, der andere bleibt uebersprungen.
    dismissed = await client.post(f"/api/v1/actions/{rows[0].id}/dismiss", headers=app.owner)
    assert dismissed.status_code == 200, dismissed.text
    third = await client.post(url, json={"mode": "security"}, headers=app.owner)
    got = {r["host_id"]: r["result"] for r in third.json()["results"]}
    assert got == {rows[0].host_id: "proposed", rows[1].host_id: "skipped"}
    assert len(await _upgrade_actions(db_session)) == 3

    audit = await client.get("/api/v1/audit", params={"action": "shield.updates_proposed_all"}, headers=app.owner)
    assert audit.status_code == 200 and len(audit.json()) == 3
    newest = audit.json()[0]
    assert newest["detail"]["proposed"] == 1 and newest["detail"]["mode"] == "security" and newest["actor_type"] == "user"


@pytest.mark.asyncio
async def test_real_app_needs_the_right_to_manage(client, db_session, test_settings, shield_tables, monkeypatch):
    app = await _real_app(client, db_session, test_settings, shield_tables, monkeypatch)
    url = "/api/v1/ext/shield/defender/updates/propose-all"

    assert (await client.post(url, json={"mode": "all"})).status_code == 401
    denied = await client.post(url, json={"mode": "all"}, headers=app.viewer)
    assert denied.status_code == 403, denied.text
    assert await _upgrade_actions(db_session) == [], "ohne Recht entsteht nichts"

    # Die Einzelroute verlangt dasselbe Recht -- der Sammelweg ist nicht schwaecher.
    single = await client.post(f"/api/v1/ext/shield/defender/hosts/{app.ids['pi']}/upgrade", json={"mode": "all"}, headers=app.viewer)
    assert single.status_code == 403

    assert (await client.post(url, json={"mode": "all"}, headers=app.owner)).status_code == 200
    assert (await client.post(url, json={"mode": "reboot"}, headers=app.owner)).status_code == 422
