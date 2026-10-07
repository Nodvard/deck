"""Haertungs-Audit (Lynis), das sehr lange laufen darf, im Dashboard: Der Lauf haengt nicht mehr an einer SSH-Verbindung
mit 30 Minuten Zeitlimit, das Ergebnis kommt auch nach ueber einer Stunde oder einem Neustart des Dashboards an, an der
Obergrenze wird Lynis auf dem Server beendet, und die Seite sieht sofort "laeuft seit ...". Mit einem simulierten Server
und einer Test-Uhr; den Befehl auf dem Server prueft test_ext_shield_audit_job.py."""

from __future__ import annotations

import asyncio
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "extensions" / "shield" / "src"))

RID = "audit_0123456789abcdef"
REPORT = (
    "warning[]=SSH-7408|Consider hardening SSH configuration|-|-|\n"
    "suggestion[]=AUTH-9286|Configure maximum password age|-|-|\n"
    "hardening_index=68\nfinish=true\n"
)

# --- Ablauf im Dashboard: simulierter Server, Test-Uhr ------------------------------------------------------------------

T0 = datetime(2026, 10, 3, 1, 0, tzinfo=UTC).timestamp()
LAUNCHED = "@@launch\n@@method=systemd\n@@pid=4242\n"
RUNNING = "@@running\n@@tail\n"
FINISHED = "@@rc=0\n@@tail\n@@prio=nice -n 19 ionice -c3\n@@lynis-rc=0\n@@report\n" + REPORT


class _Clock:
    def __init__(self) -> None:
        self.t = T0
        self.db_url = "sqlite+aiosqlite:///:memory:"

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.t, UTC)


class _Server:
    """Ein Server, auf dem Lynis `needs_s` Sekunden braucht. Jede Abfrage laesst `step_s` Sekunden der Test-Uhr
    vergehen. Ein Lynis-Aufruf direkt im SSH-Kanal (ohne Abkoppeln) dauert ebenso lange: Ist das laenger als das
    Zeitlimit des Aufrufs, kommt die Zeitueberschreitung wie bei einer echten Verbindung."""

    def __init__(self, clock: _Clock, *, needs_s: float, step_s: float) -> None:
        self.clock = clock
        self.needs_s = needs_s
        self.step_s = step_s
        self.started: float | None = None
        self.stop_reply = "@@stopped\n"
        self.stopped = False
        self.gate: asyncio.Event | None = None  # gesetzt: Abfragen melden "laeuft", bis das Ereignis kommt

    async def run(self, command: str, timeout_s: float) -> str:
        if "@@launch" in command:
            if self.started is None:
                self.started = self.clock.t
            return LAUNCHED
        if "pkill" in command:
            self.stopped = "@@stopped" in self.stop_reply
            return self.stop_reply
        if "@@running" in command:
            if self.gate is not None and not self.gate.is_set():
                await asyncio.sleep(0.01)
                return RUNNING
            self.clock.t += self.step_s
            if self.started is None:
                return "@@unknown\n@@tail\n"
            if self.stopped:
                return "@@lost\n@@tail\n"
            return FINISHED if self.clock.t - self.started >= self.needs_s else RUNNING
        if "lynis audit" in command:
            if self.needs_s > timeout_s:
                self.clock.t += timeout_s
                raise TimeoutError
            self.clock.t += self.needs_s
            return REPORT + "@@done\n"
        return ""


class _Ctx:
    def __init__(self, sessionmaker, server: _Server) -> None:
        from nodvard_sdk import Host

        self._sm = sessionmaker
        self.server = server
        self.commands: list[str] = []
        self.notes: list = []
        self.broadcasts: list = []
        self._host = Host(id="h1", name="docker-vm", display_name="Docker-VM", address="192.168.2.20")
        self.settings = SimpleNamespace(get=self._settings)
        self.hosts = SimpleNamespace(list=self._list, get=self._get)
        self.exec = SimpleNamespace(run=self._run)
        self.db = SimpleNamespace(session=self._session)
        self.audit = SimpleNamespace(log=self._audit)
        self.notify = SimpleNamespace(send=self._notify)
        self.ws = SimpleNamespace(broadcast=self._ws)
        self.api = SimpleNamespace(current_actor=lambda: None)

    async def _settings(self):
        return {}

    async def _list(self, tag=None):
        return [self._host]

    async def _get(self, host_id):
        return self._host if host_id == "h1" else None

    def more_hosts(self, n: int) -> list:
        """`n` weitere Server (pve1 ... pveN), alle mit demselben simulierten Lynis."""
        from nodvard_sdk import Host

        extra = [Host(id=f"pve{i}", name=f"pve{i}", display_name=f"pve{i}", address=f"192.168.2.{30 + i}") for i in range(1, n + 1)]
        by_id = {h.id: h for h in [self._host, *extra]}

        async def _get(host_id):
            return by_id.get(host_id)

        async def _list(tag=None):
            return list(by_id.values())

        self.hosts = SimpleNamespace(list=_list, get=_get)
        return extra

    async def _run(self, host, command, timeout_s=60):
        self.commands.append(command)
        out = await self.server.run(command, timeout_s)
        return SimpleNamespace(exit_code=0, stdout=out, stderr="", duration_ms=5)

    @asynccontextmanager
    async def _session(self):
        async with self._sm() as s:
            yield s
            await s.commit()

    async def _audit(self, **kw):
        return None

    async def _notify(self, n):
        self.notes.append(n)

    async def _ws(self, *a, **k):
        self.broadcasts.append(a)


@pytest.fixture
def clock(monkeypatch, tmp_path):
    from nodvard_deck_ext_shield import defender

    c = _Clock()
    # Eine Datei statt `:memory:`: Dort teilen sich alle Sitzungen eine Verbindung, und gleichzeitig laufende Audits
    # (wie hier) kaemen sich mit ihren Transaktionen in die Quere -- anders als mit der echten Datenbank.
    c.db_url = f"sqlite+aiosqlite:///{tmp_path / 'shield.db'}"
    monkeypatch.setattr(defender, "_now", c.now)
    # `raising=False`: dieselben Tests laufen zur Gegenprobe auch gegen den Stand vor dem Umbau.
    monkeypatch.setattr(defender, "AUDIT_POLL_INTERVAL_S", 0, raising=False)
    monkeypatch.setattr(defender, "AUDIT_RESUME_DELAY_S", 0, raising=False)
    return c


async def _setup(clock: _Clock, *, needs_s: float, step_s: float = 600):
    from nodvard_deck_ext_shield.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(clock.db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ctx = _Ctx(async_sessionmaker(engine, expire_on_commit=False), _Server(clock, needs_s=needs_s, step_s=step_s))
    return engine, ctx


async def _audits(ctx) -> list:
    from nodvard_deck_ext_shield.models import AuditRecord
    from sqlalchemy import select

    async with ctx._sm() as s:
        return list((await s.execute(select(AuditRecord))).scalars())


async def _states(ctx) -> list:
    from nodvard_deck_ext_shield.models import BaselineRecord
    from sqlalchemy import select

    async with ctx._sm() as s:
        return [r.data for r in (await s.execute(select(BaselineRecord).where(BaselineRecord.kind == "audit_run"))).scalars()]


async def _until(check, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() > end:
            raise AssertionError("Zeitueberschreitung beim Warten")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_an_audit_that_takes_over_an_hour_is_taken_over_when_lynis_is_done(clock):
    """Lynis braucht auf dem Docker-Host 68 Minuten. Frueher gab das Dashboard nach 30 Minuten auf
    ("Zeitueberschreitung") und las den Bericht nie. Jetzt kommt das Ergebnis an."""
    from nodvard_deck_ext_shield.defender import Defender

    engine, ctx = await _setup(clock, needs_s=68 * 60)
    await Defender(ctx).run_audits([ctx._host])
    [audit] = await _audits(ctx)
    assert (audit.status, audit.hardening_index, audit.error) == ("ok", 68, None)
    assert audit.warnings == ["SSH-7408: Consider hardening SSH configuration"]
    assert clock.t - T0 >= 68 * 60
    assert await _states(ctx) == []  # der Stand "laeuft" ist weg
    assert [n.title for n in ctx.notes] == ["Härtungs-Audit: 1 Warnung(en), 0 Fehler"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_at_the_limit_the_dashboard_ends_lynis_on_the_server(clock):
    """Endet ein Lauf auch nach der Obergrenze nicht (auf dem Server fehlt `timeout`), beendet das Dashboard Lynis dort
    und sagt das verstaendlich."""
    from nodvard_deck_ext_shield.defender import (
        AUDIT_LIMIT_S,
        AUDIT_STOP_GRACE_S,
        Defender,
    )

    engine, ctx = await _setup(clock, needs_s=10 * 3600, step_s=3600)
    await Defender(ctx).run_audits([ctx._host])
    [audit] = await _audits(ctx)
    stops = [c for c in ctx.commands if "pkill" in c]
    assert len(stops) == 1 and f"nodvard-shield-audit-{audit.id}.service" in stops[0]
    assert "pkill -TERM -s" in stops[0] and "pkill -KILL -s" in stops[0]
    assert clock.t - T0 >= AUDIT_LIMIT_S + AUDIT_STOP_GRACE_S
    assert (audit.status, audit.error) == ("error", "Das Audit hat länger als 3 Stunden gedauert und wurde auf dem Server beendet.")
    assert await _states(ctx) == []
    assert "länger als 3 Stunden" in ctx.notes[-1].body

    # Lynis ist genau beim Beenden fertig geworden: dann gilt sein Ergebnis.
    ctx.server.started, ctx.server.stopped, ctx.server.stop_reply = None, False, "@@notrunning\n"
    ctx.server.needs_s = 4.5 * 3600
    await Defender(ctx).run_audits([ctx._host])
    assert sorted((a.status, a.hardening_index) for a in await _audits(ctx)) == [("error", None), ("ok", 68)]
    assert len([c for c in ctx.commands if "pkill" in c]) == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_second_click_starts_no_second_run_and_shows_the_running_one(clock):
    """POST /defender/audits antwortet sofort mit "laeuft seit"; ein zweiter Klick startet kein zweites Lynis, sondern
    nennt das laufende. Auch die Uebersicht (nach dem Neuladen der Seite) zeigt es."""
    import httpx
    from fastapi import FastAPI
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.defender_api import build_routers

    engine, ctx = await _setup(clock, needs_s=68 * 60)
    ctx.server.gate = asyncio.Event()
    defender = Defender(ctx)
    read, manage = build_routers(ctx, defender)
    app = FastAPI()
    app.include_router(read)
    app.include_router(manage)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://deck") as client:
        first = (await client.post("/defender/audits", json={"host_ids": ["h1"]})).json()
        assert (first["hosts"], first["started"], first["running"]) == (1, ["h1"], [])
        [a] = first["audits"]
        assert (a["host_id"], a["host_name"], a["requested_at"], a["already_running"]) == ("h1", "Docker-VM", T0, False)
        await _until(lambda: ctx.server.started is not None)

        clock.t += 120
        second = (await client.post("/defender/audits", json={"host_ids": "all"})).json()
        assert (second["started"], second["running"]) == ([], ["h1"])
        [b] = second["audits"]
        assert (b["run_id"], b["started_at"], b["waiting"], b["already_running"]) == (a["run_id"], T0, False, True)
        assert sum("@@launch" in c for c in ctx.commands) == 1

        row = (await client.get("/defender/overview")).json()["hosts"][0]
        assert row["auditing"] is True
        assert row["audit_run"] == {"run_id": a["run_id"], "requested_at": T0, "started_at": T0, "trigger": "manual",
                                    "waiting": False}

        ctx.server.gate.set()
        await _until(lambda: not defender._audits)
        row = (await client.get("/defender/overview")).json()["hosts"][0]
        assert (row["auditing"], row["audit_run"], row["last_audit"]["hardening_index"]) == (False, None, 68)
    assert sum("@@launch" in c for c in ctx.commands) == 1
    [audit] = await _audits(ctx)
    assert audit.id == a["run_id"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_with_all_slots_busy_an_audit_waits_and_says_so(clock):
    """Hoechstens drei Audits laufen gleichzeitig (VMs auf derselben Hardware). Ein viertes wartet und steht als
    "wartet" da, nicht als laufend; es startet, sobald ein Platz frei ist."""
    from nodvard_deck_ext_shield.defender import Defender

    engine, ctx = await _setup(clock, needs_s=60)
    hosts = [ctx._host, *ctx.more_hosts(3)]
    ctx.server.gate = asyncio.Event()
    defender = Defender(ctx)
    started = await defender.start_audits(hosts)
    assert started["started"] == ["h1", "pve1", "pve2", "pve3"]
    await _until(lambda: sum("@@launch" in c for c in ctx.commands) == 3)
    await asyncio.sleep(0.05)
    assert sum("@@launch" in c for c in ctx.commands) == 3
    runs = {h.id: defender.audit_run(h.id) for h in hosts}
    assert [h for h, r in runs.items() if r["waiting"]] == ["pve3"]
    assert runs["pve3"]["started_at"] is None and all(runs[h]["started_at"] == T0 for h in ("h1", "pve1", "pve2"))
    again = await defender.start_audits([hosts[3]])
    assert again["audits"][0]["waiting"] is True and again["audits"][0]["already_running"] is True

    ctx.server.gate.set()
    await _until(lambda: not defender._audits)
    assert sum("@@launch" in c for c in ctx.commands) == 4
    assert sorted(a.host_id for a in await _audits(ctx)) == ["h1", "pve1", "pve2", "pve3"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_dashboard_restart_keeps_the_running_audit_and_takes_over_its_result(clock):
    """Neustart des Dashboards mitten im Audit (Deploy): Danach steht es weiter als laufend da, ein Klick startet kein
    zweites, und das Ergebnis kommt an, sobald Lynis fertig ist -- einmal gemeldet."""
    from nodvard_deck_ext_shield.defender import Defender

    engine, ctx = await _setup(clock, needs_s=68 * 60)
    ctx.server.gate = asyncio.Event()
    old = Defender(ctx)
    await old.start_audits([ctx._host], trigger="schedule")
    await _until(lambda: ctx.server.started is not None)
    [state] = await _states(ctx)
    assert (state["started_at"], state["trigger"]) == (T0, "schedule")
    old.cancel_audit_tasks()  # der alte Prozess endet (on_stop)
    await _until(lambda: not old._audit_tasks and not old._audits)
    assert [s["run_id"] for s in await _states(ctx)] == [state["run_id"]]  # der Stand bleibt fuer den naechsten Start

    new = Defender(ctx)
    assert await new.load_running_audits() == 1
    assert new.audit_run("h1")["started_at"] == T0
    again = await new.start_audits([ctx._host])
    assert (again["started"], again["running"]) == ([], ["h1"])
    assert sum("@@launch" in c for c in ctx.commands) == 1

    ctx.server.gate.set()
    assert await new.resume_audits() == 1
    assert sum("@@launch" in c for c in ctx.commands) == 1  # nie ein zweiter Start
    [audit] = await _audits(ctx)
    assert (audit.id, audit.status, audit.hardening_index) == (state["run_id"], "ok", 68)
    assert await _states(ctx) == [] and new.audit_run("h1") is None
    [note] = ctx.notes
    assert note.body.endswith("Das Dashboard wurde währenddessen neu gestartet.")
    await engine.dispose()


@pytest.mark.asyncio
async def test_after_a_restart_an_audit_unknown_to_the_server_counts_as_aborted(clock):
    from nodvard_deck_ext_shield.defender import ABORTED_BY_RESTART, Defender
    from nodvard_deck_ext_shield.patching import save_baseline

    engine, ctx = await _setup(clock, needs_s=60)
    await save_baseline(ctx, "h1", "audit_run", {"run_id": RID, "trigger": "manual", "requested_at": T0, "started_at": T0})
    await save_baseline(ctx, "weg", "audit_run", {"run_id": "audit_ffffffffffffffff", "trigger": "manual", "requested_at": T0,
                                                  "started_at": T0})
    await save_baseline(ctx, "h2", "audit_run", {"run_id": "kaputt"})
    defender = Defender(ctx)
    assert await defender.load_running_audits() == 2
    assert await defender.resume_audits() == 1
    [audit] = await _audits(ctx)
    assert (audit.id, audit.status, audit.error) == (RID, "error", ABORTED_BY_RESTART)
    assert await _states(ctx) == [] and defender._audits == {}
    await engine.dispose()


@pytest.mark.asyncio
async def test_on_start_loads_running_audits_before_the_first_request(clock):
    from nodvard_deck_ext_shield import Extension
    from nodvard_deck_ext_shield.defender import Defender
    from nodvard_deck_ext_shield.patching import UpdateCenter, save_baseline

    engine, ctx = await _setup(clock, needs_s=60)
    await save_baseline(ctx, "h1", "audit_run", {"run_id": RID, "trigger": "schedule", "requested_at": T0, "started_at": T0})
    spawned: list[str] = []

    def _spawn(coro, name=None):
        spawned.append(name)
        coro.close()

    ctx.spawn = _spawn
    ctx.requirements = []
    ctx.ui = SimpleNamespace(register_host_requirement=ctx.requirements.append)
    ext = Extension()
    ext._defender = Defender(ctx)
    ext._updates = UpdateCenter(ctx, ext._defender)
    ext._watcher = SimpleNamespace(run_forever=lambda: asyncio.sleep(0))
    await ext.on_start(ctx)
    assert ext._defender.audit_run("h1")["started_at"] == T0
    assert "shield-audit-resume" in spawned

    # Beim Beenden der Erweiterung: nicht weiter nachfragen, der Stand bleibt fuer den naechsten Start.
    ctx.server.gate = asyncio.Event()
    ext._defender = Defender(ctx)
    await ext._defender.start_audits([ctx._host])
    await _until(lambda: ctx.server.started is not None)
    await ext.on_stop(ctx)
    await _until(lambda: not ext._defender._audits)
    assert len(await _states(ctx)) == 1
    await engine.dispose()
