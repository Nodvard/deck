"""Aktionen von Nodvard Shield: der root-Befehl entsteht nur aus strukturierten, geprueften
Feldern -- nie aus `payload["command"]`.

Die /defender-Routen schreiben die Felder (ip/jail/unban, package, mode/manager/packages,
finding_id) plus den daraus gebauten Befehl (nur fuer Anzeige und Sperrliste) in den
Payload. Der Executor baut den Befehl beim Ausfuehren neu und fuehrt ihn nur aus, wenn
er genau zu `payload["command"]` passt."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from nodvard_sdk import ActionRequest, Actor, GateDecision, GateOutcome, Host, Risk
from nodvard_sdk.actions import ActionStatus

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))

from nodvard_deck_ext_shield import antivirus as av
from nodvard_deck_ext_shield import intrusion as ix
from nodvard_deck_ext_shield import updates as up

QP = "/var/lib/nexus-quarantine/f1_eicar.com"


class _Ctx:
    """Nur was Routen, Executor und Defender hier brauchen."""

    def __init__(self, sessionmaker) -> None:
        self._sm = sessionmaker
        self.commands: list[str] = []
        self.proposals: list[ActionRequest] = []
        # Die Gegenstelle der SSH-Verbindung, wie der Server sie sieht; None: der Server antwortet nicht.
        self.deck_address: str | None = "10.0.0.9"
        self.next_result: SimpleNamespace | None = None
        self._host = Host(id="h1", name="pi", display_name="Raspberry Pi", address="10.0.0.2")
        self.api = SimpleNamespace(current_actor=lambda: Actor.user("u1", "nico"))
        self.hosts = SimpleNamespace(get=self._get)
        self.exec = SimpleNamespace(run=self._run)
        self.db = SimpleNamespace(session=self._session)
        self.actions = SimpleNamespace(propose=self._propose)
        self.settings = SimpleNamespace(get=self._settings)
        self.settings_value: dict = {}
        self.ws = SimpleNamespace(broadcast=self._noop)
        self.audit = SimpleNamespace(log=self._noop)
        self.notify = SimpleNamespace(send=self._noop)

    async def _get(self, host_id):
        return self._host if host_id == "h1" else None

    async def _run(self, host, command, timeout_s=60):
        if command == ix.DECK_ADDRESS_COMMAND:
            if self.deck_address is None:
                raise ConnectionError("Server antwortet nicht")
            return SimpleNamespace(exit_code=0, stdout=f"{self.deck_address}\n", stderr="", duration_ms=1)
        self.commands.append(command)
        return self.next_result or SimpleNamespace(exit_code=0, stdout="ok\n", stderr="", duration_ms=5)

    @asynccontextmanager
    async def _session(self):
        async with self._sm() as s:
            yield s
            await s.commit()

    async def _propose(self, req: ActionRequest, **_kwargs) -> GateDecision:
        self.proposals.append(req)
        return GateDecision(action_id=f"a{len(self.proposals)}", outcome=GateOutcome.REQUIRE_CONFIRMATION,
                            status=ActionStatus.PROPOSED, rule="test")

    async def _settings(self):
        return self.settings_value

    async def _noop(self, *a, **k):
        return None


class _Updates:
    """Update-Zentrale ohne Server: fester Stand, Einspiel-Laeufe werden mitgeschrieben."""

    def __init__(self) -> None:
        self.security = ["libssl3", "openssl"]
        self.manager = "apt"
        self.runs: list[tuple[str, str]] = []
        self.plans: list[list[str] | None] = []

    async def plan(self, host, mode):
        return self.manager, list(self.security) if mode == "security" else []

    async def execute(self, host, mode, *, command, trigger, plan=None):
        self.runs.append((mode, command))
        self.plans.append(plan)
        return True, "fertig", "", 0


@pytest.fixture
async def soc():
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.defender_api import DefenderExecutor, build_routers
    from nodvard_deck_ext_shield.models import Base, FindingRecord
    from nodvard_deck_ext_shield.patching import save_baseline
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        now = datetime.now(timezone.utc)
        s.add(FindingRecord(id="f1", host_id="h1", host_name="pi", path="/tmp/eicar.com", signature="EICAR",
                            status="quarantined", quarantine_path=QP, original_mode="640", detected_at=now))
        s.add(FindingRecord(id="f2", host_id="other", host_name="x", path="/tmp/a", signature="X",
                            status="quarantined", quarantine_path="/var/lib/nexus-quarantine/f2_a", detected_at=now))
        await s.commit()

    ctx = _Ctx(sm)
    await save_baseline(ctx, "h1", "guard", {"fail2ban": "running", "jails": {"sshd": {"banned": []}}})
    defender = Defender(ctx)
    updates = _Updates()
    _read, manage = build_routers(ctx, defender, updates, None)
    app = FastAPI()
    app.include_router(manage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://soc") as client:
        yield SimpleNamespace(ctx=ctx, defender=defender, updates=updates, client=client, app=app,
                              executor=DefenderExecutor(ctx, defender, updates))
    await engine.dispose()


def _req(action_type: str, payload: dict) -> ActionRequest:
    return ActionRequest(action_type=action_type, payload=payload, host_ref="h1", risk=Risk.LOW,
                         proposed_by=Actor.user("u1", "nico"), reason="Test")


async def _click(soc, path: str, body: dict | None = None) -> ActionRequest:
    """Knopf in der SOC-Oberflaeche: Route aufrufen, den Vorschlag ans Gate abfangen."""
    r = await soc.client.post(path, json=body or {})
    assert r.status_code == 200, r.text
    return soc.ctx.proposals[-1]


# --- Normale Klicks: gleicher Befehl wie vorher, laeuft nach der Freigabe ------------


@pytest.mark.asyncio
async def test_ban_and_unban_click_builds_the_same_command_and_runs_it(soc):
    req = await _click(soc, "/defender/hosts/h1/ban", {"ip": " 203.0.113.9 ", "unban": True})
    want = ix.ban_command("203.0.113.9", "sshd", unban=True)
    assert req.payload == {"ip": "203.0.113.9", "jail": "sshd", "unban": True, "command": want}
    assert want == "fail2ban-client set sshd unbanip 203.0.113.9"
    res = await soc.executor.execute(req)
    assert res.success, res.error
    # Gespeichert und verglichen wird der Befehl selbst; beim Ausführen zuerst über das Hilfsskript, sonst wie bisher.
    assert soc.ctx.commands == [av.via_helper(["unban", "203.0.113.9", "sshd"], want, version=av.HELPER_ACTION_VERSION)]

    req = await _click(soc, "/defender/hosts/h1/ban", {"ip": "2001:db8::1"})
    assert req.payload["command"] == ix.ban_command("2001:db8::1", "sshd", unban=False)
    assert (await soc.executor.execute(req)).success
    assert soc.ctx.commands[-1] == ix.deck_address_guard("2001:db8::1") + av.via_helper(
        ["ban", "2001:db8::1", "sshd"], req.payload["command"], version=av.HELPER_ACTION_VERSION
    )


@pytest.mark.asyncio
async def test_a_typed_address_is_stored_in_the_form_fail2ban_shows(soc):
    req = await _click(soc, "/defender/hosts/h1/ban", {"ip": " 2001:DB8:0::7 "})
    assert req.payload["ip"] == "2001:db8::7"
    req = await _click(soc, "/defender/hosts/h1/ban", {"ip": "::ffff:203.0.113.7"})
    assert req.payload["ip"] == "203.0.113.7"


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", [
    "127.0.0.1", "::1", "0.0.0.0", "::", "224.0.0.1", "255.255.255.255", "::ffff:127.0.0.1",
])
async def test_addresses_of_no_other_machine_are_never_banned(soc, ip):
    r = await soc.client.post("/defender/hosts/h1/ban", json={"ip": ip})
    assert r.status_code == 400 and r.json()["detail"] == ix.BAN_SPECIAL_REFUSED
    assert soc.ctx.proposals == []
    assert (await soc.client.post("/defender/hosts/h1/ban", json={"ip": ip, "unban": True})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(("seen", "ip"), [
    ("10.0.0.9", "10.0.0.9"), ("::ffff:10.0.0.9", "10.0.0.9"), ("10.0.0.9", "::ffff:10.0.0.9"),
    ("fe80::5%eth0", "fe80::5"), ("2001:db8::9", "2001:DB8::9"),
])
async def test_the_address_deck_connects_from_is_never_banned(soc, seen, ip):
    soc.ctx.deck_address = seen
    r = await soc.client.post("/defender/hosts/h1/ban", json={"ip": ip})
    assert r.status_code == 409 and r.json()["detail"] == ix.DECK_SELF_REFUSED
    assert soc.ctx.proposals == []
    # Entsperren bleibt immer möglich.
    assert (await soc.client.post("/defender/hosts/h1/ban", json={"ip": ip, "unban": True})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(("browser", "ip"), [
    ("203.0.113.44", "203.0.113.44"), ("203.0.113.44", "::ffff:203.0.113.44"), ("2001:db8::44", "2001:DB8:0::44"),
    # Lauscht Deck auf `::`, kommen IPv4-Browser in dieser Form an.
    ("::ffff:203.0.113.44", "203.0.113.44"),
])
async def test_the_address_of_the_browser_is_never_banned(soc, browser, ip):
    transport = httpx.ASGITransport(app=soc.app, client=(browser, 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://soc") as client:
        r = await client.post("/defender/hosts/h1/ban", json={"ip": ip})
        assert r.status_code == 409 and r.json()["detail"] == ix.BROWSER_SELF_REFUSED
        assert (await client.post("/defender/hosts/h1/ban", json={"ip": "203.0.113.45"})).status_code == 200
        assert (await client.post("/defender/hosts/h1/ban", json={"ip": ip, "unban": True})).status_code == 200


@pytest.mark.asyncio
async def test_without_ssh_client_on_the_server_nothing_is_proposed(soc):
    soc.ctx.deck_address = ""
    r = await soc.client.post("/defender/hosts/h1/ban", json={"ip": "203.0.113.9"})
    assert r.status_code == 409 and r.json()["detail"] == ix.DECK_ADDRESS_UNKNOWN
    assert soc.ctx.proposals == []


@pytest.mark.asyncio
async def test_a_zone_in_the_address_is_refused(soc):
    r = await soc.client.post("/defender/hosts/h1/ban", json={"ip": "fe80::5%eth0"})
    assert r.status_code == 400 and r.json()["detail"] == "Ungültige IP-Adresse."


@pytest.mark.asyncio
@pytest.mark.parametrize(("exit_code", "stdout", "stderr"), [
    (0, "0\n", ""), (255, "", "ERROR  NOK: ('IP 203.0.113.9 is not banned',)\n"),
])
async def test_unbanning_an_address_that_is_not_banned_is_no_error(soc, exit_code, stdout, stderr):
    req = await _click(soc, "/defender/hosts/h1/ban", {"ip": "203.0.113.9", "unban": True})
    soc.ctx.next_result = SimpleNamespace(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_ms=5)
    res = await soc.executor.execute(req)
    assert res.success and res.detail == {"note": ix.NOT_BANNED_NOTE}


@pytest.mark.asyncio
async def test_a_failed_unban_stays_a_failure(soc):
    req = await _click(soc, "/defender/hosts/h1/ban", {"ip": "203.0.113.9", "unban": True})
    soc.ctx.next_result = SimpleNamespace(exit_code=255, stdout="", stderr="ERROR  NOK: ('sshd',)\n", duration_ms=5)
    res = await soc.executor.execute(req)
    assert not res.success and res.detail == {}


@pytest.mark.asyncio
async def test_without_an_answer_from_the_server_the_ban_is_proposed_and_checked_there(soc):
    soc.ctx.deck_address = None
    req = await _click(soc, "/defender/hosts/h1/ban", {"ip": "203.0.113.9"})
    assert (await soc.executor.execute(req)).success
    assert soc.ctx.commands[-1].startswith(ix.deck_address_guard("203.0.113.9"))


@pytest.mark.asyncio
async def test_only_jails_the_server_has_can_be_used_for_a_ban(soc):
    from nodvard_deck_ext_shield.patching import save_baseline

    def ban(jail: str):
        return soc.client.post("/defender/hosts/h1/ban", json={"ip": "203.0.113.9", "jail": jail})

    r = await ban("nginx")
    assert r.status_code == 409 and r.json()["detail"].endswith("(laut letzter Prüfung: sshd).")
    jails = {"sshd": {"banned": []}, "recidive": {"banned": []}, "x; <b>": {"banned": []}, "logtarget": {"banned": []}}
    await save_baseline(soc.ctx, "h1", "guard", {"fail2ban": "running", "jails": jails})
    r = await ban("nginx")
    assert r.status_code == 409
    assert r.json()["detail"] == "Den Jail „nginx“ gibt es auf diesem Server nicht (laut letzter Prüfung: recidive, sshd)."
    assert (await ban("recidive")).status_code == 200
    for bad in ("sshd; id", "logtarget", "LogTarget", "dbfile"):
        r = await ban(bad)
        assert r.status_code == 400 and r.json()["detail"] == "Ungültiger Jail-Name."


@pytest.mark.asyncio
@pytest.mark.parametrize(("guard", "detail"), [
    ({}, ix.JAILS_UNKNOWN),
    ({"fail2ban": "noaccess", "jails": {}}, ix.JAILS_UNKNOWN),
    ({"fail2ban": "running", "jails": {}}, ix.JAILS_NONE),
])
async def test_without_known_jails_nothing_is_proposed(soc, guard, detail):
    from nodvard_deck_ext_shield.patching import save_baseline

    await save_baseline(soc.ctx, "h1", "guard", guard)
    r = await soc.client.post("/defender/hosts/h1/ban", json={"ip": "203.0.113.9"})
    assert r.status_code == 409 and r.json()["detail"] == detail
    assert soc.ctx.proposals == []
    r = await soc.client.post("/defender/hosts/h1/ban", json={"ip": "203.0.113.9", "unban": True})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_behind_a_proxy_that_is_not_trusted_the_forwarded_address_counts_too(soc):
    from nodvard_deck.api.proxy_headers import ProxyHeadersMiddleware
    from nodvard_deck.core import trusted_proxies

    transport = httpx.ASGITransport(app=ProxyHeadersMiddleware(soc.app), client=("198.51.100.20", 50000))
    headers = {"X-Forwarded-For": "203.0.113.44:51515, 198.51.100.30"}

    async def ban(client, ip):
        return await client.post("/defender/hosts/h1/ban", json={"ip": ip}, headers=headers)

    trusted_proxies.reset()
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://soc") as client:
            for own in ("203.0.113.44", "198.51.100.30", "198.51.100.20"):
                r = await ban(client, own)
                assert r.status_code == 409 and r.json()["detail"] == ix.BROWSER_SELF_REFUSED
            assert (await ban(client, "203.0.113.45")).status_code == 200
            # Eingetragen zählt nur die Adresse, die die Middleware aus der Kette liest.
            trusted_proxies.apply(["198.51.100.20"])
            assert (await ban(client, "198.51.100.30")).json()["detail"] == ix.BROWSER_SELF_REFUSED
            assert (await ban(client, "203.0.113.44")).status_code == 200
    finally:
        trusted_proxies.reset()


@pytest.mark.asyncio
@pytest.mark.parametrize("ip", ["127.0.0.1", "::ffff:127.0.0.1"])
async def test_an_older_proposal_for_a_special_address_does_not_run(soc, ip):
    from nodvard_deck_ext_shield.ids import ACTION_BAN

    payload = {"ip": ip, "jail": "sshd", "unban": False, "command": f"fail2ban-client set sshd banip {ip}"}
    result = await soc.executor.execute(_req(ACTION_BAN, payload))
    assert not result.success and result.error == ix.BAN_SPECIAL_REFUSED
    assert soc.ctx.commands == []


@pytest.mark.asyncio
async def test_unban_runs_without_the_check_on_the_server(soc):
    req = await _click(soc, "/defender/hosts/h1/ban", {"ip": "10.0.0.9", "unban": True})
    assert (await soc.executor.execute(req)).success
    assert soc.ctx.commands == [
        av.via_helper(["unban", "10.0.0.9", "sshd"], req.payload["command"], version=av.HELPER_ACTION_VERSION)
    ]


@pytest.mark.asyncio
async def test_install_click_uses_the_package_key(soc):
    req = await _click(soc, "/defender/hosts/h1/install", {"package": "lynis"})
    assert req.payload == {"package": "lynis", "command": av.INSTALL_COMMANDS["lynis"]}
    assert (await soc.executor.execute(req)).success
    assert soc.ctx.commands == [
        av.via_helper(["install", "lynis"], av.INSTALL_COMMANDS["lynis"], version=av.HELPER_ACTION_VERSION)
    ]
    sig = await _click(soc, "/defender/hosts/h1/install", {"package": "signatures"})
    assert sig.risk == Risk.LOW and sig.payload["command"] == av.INSTALL_COMMANDS["signatures"]


@pytest.mark.asyncio
async def test_install_forgets_the_remembered_state_of_the_server(soc):
    """Nach einer Installation zeigte die Seite bis zu 10 Minuten den gemerkten Stand von vorher ("Lynis installieren"),
    obwohl die Installation geklappt hatte. Jetzt fragt die naechste Abfrage den Server neu."""
    soc.defender._status_cache["h1"] = (9e18, {"lynis_installed": False})
    soc.defender._status_cache["other"] = (9e18, {"lynis_installed": False})
    req = await _click(soc, "/defender/hosts/h1/install", {"package": "lynis"})
    assert (await soc.executor.execute(req)).success
    assert "h1" not in soc.defender._status_cache
    assert "other" in soc.defender._status_cache, "nur der Server der Installation"


@pytest.mark.asyncio
async def test_upgrade_and_reboot_clicks_build_the_same_command(soc):
    req = await _click(soc, "/defender/hosts/h1/upgrade", {"mode": "security"})
    want = up.upgrade_command("apt", "security", ["libssl3", "openssl"])
    assert req.payload == {"mode": "security", "manager": "apt", "packages": ["libssl3", "openssl"], "command": want}
    assert (await soc.executor.execute(req)).success
    assert soc.updates.runs == [("security", want)]
    assert soc.updates.plans == [["apt", "security", "plain", "libssl3", "openssl"]], "derselbe Lauf fuer das Hilfsskript"

    full = await _click(soc, "/defender/hosts/h1/upgrade", {"mode": "all"})
    assert full.payload["command"] == up.upgrade_command("apt", "all") and full.payload["packages"] == []

    reboot = await _click(soc, "/defender/hosts/h1/upgrade", {"mode": "reboot"})
    assert reboot.action_type == "nexus_soc.reboot" and reboot.risk == Risk.HIGH
    assert reboot.payload == {"command": up.REBOOT_COMMAND}
    assert (await soc.executor.execute(reboot)).success
    assert soc.updates.runs[-1] == ("reboot", up.REBOOT_COMMAND) and soc.updates.plans[-1] is None

    soc.updates.security = []
    r = await soc.client.post("/defender/hosts/h1/upgrade", json={"mode": "security"})
    assert r.status_code == 409 and r.json()["detail"] == "Keine Sicherheitsupdates offen."


# --- dist-upgrade steht im Plan, Vorschlag und Ausfuehrung stimmen ueberein ----


@pytest.mark.asyncio
async def test_dist_upgrade_is_part_of_the_plan_so_proposal_and_execution_match(soc):
    soc.ctx.settings_value = {"proxmox_dist_upgrade": True}
    req = await _click(soc, "/defender/hosts/h1/upgrade", {"mode": "all"})
    want = up.upgrade_command("apt", "all", dist_upgrade=True)
    assert req.payload == {"mode": "all", "manager": "apt", "packages": [], "dist_upgrade": True, "command": want}
    assert "dist-upgrade" in want and "dist-upgrade" in req.reason  # der Freigebende sieht es
    # Ob der Server Proxmox ist, weiss erst der Befehl auf dem Server: der Grund behauptet es nicht.
    assert "falls der Server Proxmox ist" in req.reason and "auf Proxmox:" not in req.reason
    # dist-upgrade darf Pakete entfernen: wie ein Neustart "hoch", nicht mehr "mittel" -- bei
    # "Selbststaendig handeln bis mittel" laeuft es also nicht ungesehen durch.
    assert req.risk == Risk.HIGH

    # Die Einstellung wird danach wieder ausgeschaltet: der schon vorgeschlagene Plan gilt trotzdem
    # unveraendert (er steht im Payload), und der Executor fuehrt genau den gezeigten Befehl aus.
    soc.ctx.settings_value = {}
    assert (await soc.executor.execute(req)).success
    assert soc.updates.runs == [("all", want)] and soc.updates.plans == [["apt", "all", "dist"]]

    # Umgekehrt: erst ohne Einstellung vorgeschlagen, dann eingeschaltet -> bleibt beim alten Befehl.
    old = await _click(soc, "/defender/hosts/h1/upgrade", {"mode": "all"})
    assert "dist_upgrade" not in old.payload and old.payload["command"] == up.upgrade_command("apt", "all")
    assert old.risk == Risk.MEDIUM  # ohne die Einstellung bleibt alles wie bisher
    soc.ctx.settings_value = {"proxmox_dist_upgrade": True}
    assert (await soc.executor.execute(old)).success
    assert soc.updates.runs[-1] == ("all", up.upgrade_command("apt", "all")) and "dist-upgrade" not in soc.updates.runs[-1][1]


@pytest.mark.asyncio
async def test_the_upgrade_route_tells_the_page_the_risk_of_its_proposal(soc):
    # Die Seite braucht die Stufe, um zu wissen, ob sie den Vorschlag freigeben darf
    # (`actions.approve:<risiko>`) -- sie darf sie nicht raten.
    plain = await soc.client.post("/defender/hosts/h1/upgrade", json={"mode": "all"})
    assert plain.json()["risk"] == "medium"
    soc.ctx.settings_value = {"proxmox_dist_upgrade": True}
    dist = await soc.client.post("/defender/hosts/h1/upgrade", json={"mode": "all"})
    assert dist.json()["risk"] == "high"
    sec = await soc.client.post("/defender/hosts/h1/upgrade", json={"mode": "security"})
    assert sec.json()["risk"] == "medium"
    reboot = await soc.client.post("/defender/hosts/h1/upgrade", json={"mode": "reboot"})
    assert reboot.json()["risk"] == "high"
    ban = await soc.client.post("/defender/hosts/h1/ban", json={"ip": "203.0.113.9", "jail": "sshd", "unban": False})
    assert ban.json()["risk"] == "low"


@pytest.mark.asyncio
async def test_dist_upgrade_setting_only_changes_all_updates_on_apt(soc):
    soc.ctx.settings_value = {"proxmox_dist_upgrade": True}
    sec = await _click(soc, "/defender/hosts/h1/upgrade", {"mode": "security"})
    assert sec.payload == {"mode": "security", "manager": "apt", "packages": ["libssl3", "openssl"],
                           "command": up.upgrade_command("apt", "security", ["libssl3", "openssl"])}
    assert "dist-upgrade" not in sec.payload["command"] and "dist-upgrade" not in sec.reason
    assert sec.risk == Risk.MEDIUM
    clean = await _click(soc, "/defender/hosts/h1/upgrade", {"mode": "cleanup"})
    assert "dist_upgrade" not in clean.payload and "dist-upgrade" not in clean.payload["command"]
    soc.updates.manager = "dnf"
    dnf = await _click(soc, "/defender/hosts/h1/upgrade", {"mode": "all"})
    assert "dist_upgrade" not in dnf.payload and dnf.payload["command"] == up.upgrade_command("dnf", "all")
    assert dnf.risk == Risk.MEDIUM and clean.risk == Risk.MEDIUM


@pytest.mark.asyncio
async def test_a_dist_upgrade_plan_that_does_not_match_its_command_is_refused(soc):
    from nodvard_deck_ext_shield.defender_api import STALE_MESSAGE

    plain, dist = up.upgrade_command("apt", "all"), up.upgrade_command("apt", "all", dist_upgrade=True)
    base = {"mode": "all", "manager": "apt", "packages": []}
    # Plan sagt dist-upgrade, gezeigter Befehl ist der einfache -- und umgekehrt.
    for payload in ({**base, "dist_upgrade": True, "command": plain}, {**base, "dist_upgrade": False, "command": dist},
                    {**base, "command": dist}):
        res = await soc.executor.execute(_req("nexus_soc.upgrade", payload))
        assert not res.success and res.error == STALE_MESSAGE, payload
    res = await soc.executor.execute(_req("nexus_soc.upgrade", {**base, "dist_upgrade": "ja", "command": dist}))
    assert not res.success and res.error == "Ungültige Update-Angaben."
    assert soc.updates.runs == []
    # Aeltere Vorschlaege ohne die Angabe laufen unveraendert.
    assert (await soc.executor.execute(_req("nexus_soc.upgrade", {**base, "command": plain}))).success
    assert (await soc.executor.execute(_req("nexus_soc.upgrade", {**base, "dist_upgrade": True, "command": dist}))).success
    assert [c for _m, c in soc.updates.runs] == [plain, dist]


@pytest.mark.asyncio
async def test_restore_and_delete_clicks_come_from_the_finding_row(soc):
    req = await _click(soc, "/defender/findings/f1/restore")
    want = av.restore_command(QP)
    assert req.payload == {"finding_id": "f1", "command": want}
    assert req.host_ref == "h1"
    delete = await _click(soc, "/defender/findings/f1/delete")
    assert delete.payload == {"finding_id": "f1", "command": av.delete_command(QP)}

    assert (await soc.executor.execute(req)).success
    name = QP.rsplit("/", 1)[1]
    assert soc.ctx.commands == [
        av.via_helper(["restore", name], want, version=av.HELPER_QUARANTINE_VERSION)
    ]
    assert (await soc.defender.finding("f1"))["status"] == "restored"
    # Danach liegt nichts mehr in der Quarantaene -- auch der alte Loesch-Vorschlag laeuft nicht.
    again = await soc.executor.execute(delete)
    assert not again.success and "Quarantäne" in again.error
    assert len(soc.ctx.commands) == 1


@pytest.mark.asyncio
async def test_place_and_mode_in_the_finding_row_do_not_reach_the_restore_command(soc):
    """Ort und Rechte kommen auch ohne Hilfsskript aus der Ablage auf dem Server, nicht aus einer Sicherung."""
    from nodvard_deck_ext_shield.models import FindingRecord

    req = await _click(soc, "/defender/findings/f1/restore")
    async with soc.ctx._session() as s:
        row = await s.get(FindingRecord, "f1")
        row.path, row.original_mode = "/etc/profile.d/evil.sh", "4755"
        await s.commit()
    assert (await soc.executor.execute(req)).success
    assert "profile.d" not in soc.ctx.commands[0] and "4755" not in soc.ctx.commands[0]
    assert (await soc.defender.finding("f1"))["status"] == "restored"


@pytest.mark.asyncio
@pytest.mark.parametrize(("restored_to", "note"), [
    ("/srv/echt/eicar.com", "Zurückgelegt an den hinterlegten Ort /srv/echt/eicar.com (laut Fund: /tmp/eicar.com)."),
    ("/tmp/eicar.com", None),
    ("/srv/a\n@@msg=gone\nb.com",
     "Zurückgelegt an den hinterlegten Ort /srv/a\n@@msg=gone\nb.com (laut Fund: /tmp/eicar.com)."),
    ("/srv/x@@noroot.sh", "Zurückgelegt an den hinterlegten Ort /srv/x@@noroot.sh (laut Fund: /tmp/eicar.com)."),
])
async def test_the_restore_result_names_the_place_from_the_record(soc, restored_to, note):
    req = await _click(soc, "/defender/findings/f1/restore")
    assert req.reason == "Wiederherstellen an den hinterlegten Ort (laut Fund: /tmp/eicar.com; EICAR)"

    async def run(host, command, timeout_s=60):
        return SimpleNamespace(exit_code=0, stdout=f"@@restored_to={restored_to}\nok\n", stderr="", duration_ms=5)

    soc.ctx.exec = SimpleNamespace(run=run)
    res = await soc.executor.execute(req)
    assert res.success and res.output == f"Zurückgelegt nach {restored_to}."
    assert res.detail == {"note": res.output, "restored_to": restored_to}
    finding = await soc.defender.finding("f1")
    assert finding["status"] == "restored" and finding["note"] == note
    assert (await soc.defender.reopen("f1"))[0]
    assert (await soc.defender.finding("f1"))["note"] is None


@pytest.mark.asyncio
async def test_a_restore_that_names_no_place_ends_as_before(soc):
    req = await _click(soc, "/defender/findings/f1/restore")

    async def run(host, command, timeout_s=60):
        return SimpleNamespace(exit_code=0, stdout="ok\n", stderr="", duration_ms=5)

    soc.ctx.exec = SimpleNamespace(run=run)
    res = await soc.executor.execute(req)
    assert res.success and res.output == "ok\n" and res.detail == {}
    finding = await soc.defender.finding("f1")
    assert finding["status"] == "restored" and finding["note"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("forged", ["/etc/shadow", "/var/lib/nexus-quarantine/.herkunft/f1_eicar.com"])
async def test_a_quarantine_path_from_a_changed_backup_is_neither_proposed_nor_run(soc, forged):
    from nodvard_deck_ext_shield.models import FindingRecord

    req = await _click(soc, "/defender/findings/f1/restore")
    async with soc.ctx._session() as s:
        (await s.get(FindingRecord, "f1")).quarantine_path = forged
        await s.commit()
    for path in ("/defender/findings/f1/restore", "/defender/findings/f1/delete"):
        r = await soc.client.post(path, json={})
        assert r.status_code == 409 and "nicht in der Quarantäne" in r.json()["detail"]
    res = await soc.executor.execute(req)
    assert not res.success and "nicht in der Quarantäne" in res.error
    assert soc.ctx.commands == []
    assert (await soc.defender.finding("f1"))["status"] == "quarantined"


@pytest.mark.asyncio
async def test_quarantine_and_delete_reach_only_a_helper_that_checks_itself(soc):
    """Erst ab Version 8 prüft das Hilfsskript eine Datei vor der Quarantäne selbst und legt nur an den hinterlegten Ort
    zurück (Version 7 tat das auch, verlor aber bei zwei gleichzeitigen Aufrufen die Herkunft). Ein älteres Skript auf
    dem Server bekommt Quarantäne, Wiederherstellen und Löschen darum nicht mehr (dann der alte Weg über root bzw.
    `sudo -n sh -c`)."""
    from nodvard_deck_ext_shield.models import FindingRecord

    assert av.HELPER_QUARANTINE_VERSION == 8 > av.HELPER_ACTION_VERSION
    delete = await _click(soc, "/defender/findings/f1/delete")
    assert (await soc.executor.execute(delete)).success
    name = QP.rsplit("/", 1)[1]
    assert soc.ctx.commands == [av.via_helper(["delete", name], av.delete_command(QP), version=av.HELPER_QUARANTINE_VERSION)]

    async with soc.ctx._session() as s:
        s.add(FindingRecord(id="f3", host_id="h1", host_name="pi", path="/srv/upload/evil.sh", signature="EICAR",
                            status="detected", detected_at=datetime.now(timezone.utc)))
    await soc.defender.quarantine("f3", actor=None)
    qname = av.quarantine_name("f3", "/srv/upload/evil.sh")
    assert soc.ctx.commands[1:] == [av.via_helper(
        av.quarantine_args("/srv/upload/evil.sh", qname), av.quarantine_command("/srv/upload/evil.sh", qname),
        version=av.HELPER_QUARANTINE_VERSION,
    )]
    assert f"-ge {av.HELPER_QUARANTINE_VERSION} ]" in soc.ctx.commands[1]


# --- Eingeschleuste Befehle laufen nie ----------------------------------------------


@pytest.mark.asyncio
async def test_injected_command_in_payload_is_rejected(soc):
    from nodvard_deck_ext_shield.defender_api import STALE_MESSAGE

    evil = "id; cat /etc/shadow"
    cases = [
        ("nexus_soc.ban", {"ip": "203.0.113.9", "jail": "sshd", "unban": False, "command": evil}),
        ("nexus_soc.install", {"package": "lynis", "command": evil}),
        ("nexus_soc.upgrade", {"mode": "all", "manager": "apt", "packages": [], "command": "apt-get purge -y openssh-server"}),
        ("nexus_soc.reboot", {"command": evil}),
        ("nexus_soc.restore", {"finding_id": "f1", "command": "chmod 777 /etc/shadow"}),
        ("nexus_soc.delete", {"finding_id": "f1", "command": "rm -rf /"}),
    ]
    for action_type, payload in cases:
        res = await soc.executor.execute(_req(action_type, payload))
        assert not res.success and res.error == STALE_MESSAGE, action_type
    assert soc.ctx.commands == [] and soc.updates.runs == []


@pytest.mark.asyncio
async def test_forged_fields_are_validated(soc):
    bad = [
        ("nexus_soc.ban", {"ip": "fe80::1%$(touch${IFS}x)", "jail": "sshd", "unban": False,
                           "command": "fail2ban-client set sshd banip fe80::1%$(touch${IFS}x)"}, "Ungültige IP-Adresse."),
        ("nexus_soc.ban", {"ip": "203.0.113.9", "jail": "sshd; id", "unban": False, "command": "x"}, "Ungültiger Jail-Name."),
        ("nexus_soc.ban", {"ip": "203.0.113.9", "jail": "sshd", "unban": "ja", "command": "x"}, "Ungültige Angabe zum Entsperren."),
        ("nexus_soc.install", {"package": "rootkit", "command": "x"}, "Unbekanntes Paket."),
        ("nexus_soc.upgrade", {"mode": "all", "manager": "sh -c id", "packages": [], "command": "x"}, "Nicht unterstützt"),
        ("nexus_soc.upgrade", {"mode": "purge", "manager": "apt", "packages": [], "command": "x"}, "Unbekannte Update-Art."),
        ("nexus_soc.restore", {"finding_id": "f2", "command": av.delete_command("/var/lib/nexus-quarantine/f2_a")},
         "anderen Server"),
        ("nexus_soc.delete", {"finding_id": "nope", "command": "x"}, "Unbekannter Fund."),
    ]
    for action_type, payload, message in bad:
        res = await soc.executor.execute(_req(action_type, payload))
        assert not res.success and message in (res.error or ""), (action_type, res.error)
    # Paketnamen werden gefiltert: der eingeschleuste Name steht nie im Befehl.
    res = await soc.executor.execute(_req("nexus_soc.upgrade", {
        "mode": "security", "manager": "apt", "packages": ["openssl", "x;id"],
        "command": up.upgrade_command("apt", "security", ["openssl"]),
    }))
    assert res.success and soc.updates.runs == [("security", up.upgrade_command("apt", "security", ["openssl"]))]
    assert soc.ctx.commands == []

    r = await soc.client.post("/defender/hosts/h1/ban", json={"ip": "fe80::1%$(id)"})
    assert r.status_code == 400 and not soc.ctx.proposals


# --- Alte Vorschlaege (vor dem Umbau) scheitern sicher ------------------------------


@pytest.mark.asyncio
async def test_old_proposals_without_structured_fields_fail_safely(soc):
    from nodvard_deck_ext_shield.defender_api import OLD_VERSION_MESSAGE

    old = [
        # So sahen die Payloads bisher aus -- der gespeicherte Befehl wird nie ausgefuehrt.
        ("nexus_soc.ban", {"ip": "203.0.113.9", "command": "fail2ban-client set sshd banip 203.0.113.9"}),
        ("nexus_soc.ban", {"command": "id; cat /etc/shadow"}),
        ("nexus_soc.upgrade", {"mode": "security", "command": up.upgrade_command("apt", "security", ["openssl"])}),
        ("nexus_soc.install", {"command": av.INSTALL_COMMANDS["lynis"]}),
        ("nexus_soc.restore", {"command": av.restore_command(QP)}),
    ]
    for action_type, payload in old:
        res = await soc.executor.execute(_req(action_type, payload))
        assert not res.success and res.error == OLD_VERSION_MESSAGE, action_type
    assert soc.ctx.commands == [] and soc.updates.runs == []


@pytest.mark.asyncio
async def test_unknown_host_and_missing_update_center_fail(soc):
    from nodvard_deck_ext_shield.defender_api import DefenderExecutor

    req = _req("nexus_soc.reboot", {"command": up.REBOOT_COMMAND})
    req.host_ref = "gone"
    assert (await soc.executor.execute(req)).error == "Server nicht gefunden."
    without_updates = DefenderExecutor(soc.ctx, soc.defender, None)
    res = await without_updates.execute(_req("nexus_soc.reboot", {"command": up.REBOOT_COMMAND}))
    assert not res.success and "Update-Zentrale" in res.error
    assert soc.ctx.commands == []
