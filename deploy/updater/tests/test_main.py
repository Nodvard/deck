"""Hauptprogramm: Schleife, Heartbeat, Status, Vorpruefung, Anforderungen, Selbsttest.

In diesem Schritt fuehrt der Helfer **keine** Aktion aus: jede Anforderung durchlaeuft alle Pruefungen und endet
mit einem festen Code, eine gueltige mit `not_implemented`. Die Engine sieht nur `GET`. Echte Ordner fuer Kanal und
Zustand unter `tmp_path` (Besitzer = eigene uid als `expected_uid`), die Engine ist die Fake-Engine mit einer
aufgezeichneten Welt.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path

import pytest
from fake_engine import FakeEngine, Response, World
from nodvard_deck_updater import __main__ as main
from nodvard_deck_updater import __version__, channel, policy
from nodvard_deck_updater.channel import Channel
from nodvard_deck_updater.engine import Engine
from nodvard_deck_updater.state import (
    JOURNAL_NAME,
    Journal,
    NewImage,
    OldContainer,
    StateStore,
    StateUnsafe,
)
from updater_support import UPDATER_DIR, load_vectors

NOW = 1790812400
SECRET = "GEHEIM123"


class Clock:
    """Eine von Hand gestellte Wanduhr (`Helper(clock=...)`): Der Helfer schreibt den Zeitstempel beim Schreiben von
    ihr, nie vom Rundenbeginn. `Setup.tick` stellt sie auf das `now` der Runde, danach stellen die Tests sie weiter
    (auch rueckwaerts: so springt eine Wanduhr)."""

    def __init__(self) -> None:
        self.t = float(NOW)

    def __call__(self) -> float:
        return self.t


class Setup:
    def __init__(self, tmp_path, uid, world: World | None = None) -> None:
        self.root = tmp_path / "channel"
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        (self.root / "requests").mkdir()
        (self.root / "requests").chmod(0o1777)
        self.state_dir = tmp_path / "state"
        self.state_dir.mkdir(mode=0o700)
        self.uid = uid
        self.world = world or World.from_fixture("docker29-api154")
        self.helper_container = self.world.by_service("updater")
        self.target = self.world.by_service("nodvard-deck")
        self.target["Config"]["Env"].append("NODVARD_DECK_JWT_SECRET=" + SECRET)
        self.fake = FakeEngine(self.world).start()
        self.logs: list[dict] = []
        self.channel = Channel(self.root, expected_uid=uid)
        self.store = StateStore(self.state_dir, expected_uid=uid)
        self.clock = Clock()
        self.helper = main.Helper(channel=self.channel, store=self.store, engine=Engine(self.fake.path),
                                  service="nodvard-deck", own_id=lambda: self.helper_container["Id"],
                                  logger=self.log, clock=self.clock)

    def log(self, event, **fields):
        self.logs.append({"event": event, **fields})

    def close(self):
        self.fake.stop()
        self.store.close()
        self.channel.close()

    def status(self) -> dict:
        doc = json.loads((self.root / "status.json").read_text(encoding="ascii"))
        channel.validate_status(doc)
        return doc

    def request(self, action="update", version="0.7.1", *, rid=None, created_at=NOW, raw=None) -> str:
        rid = rid or str(uuid.uuid4())
        data = raw if raw is not None else json.dumps(
            {"v": 1, "id": rid, "action": action, "version": version, "created_at": created_at}).encode()
        (self.root / "requests" / f"{rid}.json").write_bytes(data)
        return rid

    def pending(self) -> list[str]:
        return sorted(p.name for p in (self.root / "requests").iterdir())

    def result(self, rid) -> dict | None:
        return next((r for r in self.status()["results"] if r["id"] == rid), None)

    def tick(self, now):
        """Eine Runde zum Zeitpunkt `now`; die Wanduhr steht zu Beginn auf `now`."""
        self.clock.t = now
        self.helper.tick(now)

    def start(self, now=NOW):
        self.clock.t = now
        self.helper.setup(now)
        self.helper.tick(now)


@pytest.fixture
def env(tmp_path, uid):
    setup = Setup(tmp_path, uid)
    yield setup
    setup.close()
    assert setup.fake.violations == []
    assert setup.fake.methods <= {"GET"}, setup.fake.methods  # in diesem Schritt nie eine aendernde Anfrage
    everything = json.dumps(setup.logs)
    assert SECRET not in everything


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_start_writes_a_ready_status(env):
    env.start()
    status = env.status()
    assert status["proto"] == policy.PROTOCOL and status["helper_version"] == __version__
    assert status["request_versions"] == [1]
    assert (status["state"], status["ready"], status["reason"]) == ("idle", True, None)
    assert status["target"] == {"current_version": "0.7.0", "floating_tag": "latest", "pinned": False}
    assert status["heartbeat_at"] == NOW and status["busy"] is None and status["previous"] is None
    assert status["results"] == []
    assert SECRET not in (env.root / "status.json").read_text()
    assert stat_mode(env.root / "status.json") == 0o644
    assert stat_mode(env.root / "requests") == 0o1777


def stat_mode(path):
    return os.stat(path).st_mode & 0o7777


def test_seq_and_heartbeat(env):
    env.start()
    first = env.status()
    env.helper.heartbeat(NOW + 30)
    second = env.status()
    assert second["seq"] > first["seq"] and second["heartbeat_at"] == NOW + 30
    env.tick(NOW + 35)
    assert env.status()["seq"] > second["seq"]


def test_a_long_round_does_not_overwrite_the_fresher_heartbeat(env):
    # Die Runde haengt an der Engine; der Heartbeat-Thread schreibt derweil frische Zeitstempel. Der Status am Ende
    # der Runde darf `heartbeat_at` nicht auf den Rundenbeginn zuruecksetzen (das Dashboard zeigte sonst bis zum
    # naechsten Heartbeat "antwortet nicht").
    env.start()
    env.helper._preflight_at = None  # die Vorpruefung ist faellig und ruft die Engine

    def slow(request):
        env.clock.t += 120  # die Engine antwortet erst nach zwei Minuten ...
        env.helper._guarded(env.helper.heartbeat)  # ... der Heartbeat-Thread schreibt in der Zwischenzeit
        assert env.status()["heartbeat_at"] == NOW + 120
        return Response(body=env.world.list_containers(request.query))

    env.fake.route("GET", "/containers/json", slow)
    seq = env.status()["seq"]
    env.tick(NOW)
    status = env.status()
    assert status["seq"] > seq + 1
    assert status["heartbeat_at"] >= NOW + 120, "der Zeitstempel lief rueckwaerts"
    assert status["heartbeat_at"] == NOW + 240  # zwei Aufrufe der Liste (Ziele, alle Container) je 120 s


@pytest.mark.parametrize("jump", [3600, -3600])
def test_wall_clock_jump_during_a_round_keeps_the_heartbeat_on_the_new_time_axis(env, jump):
    # Ein Raspberry Pi ohne Echtzeituhr stellt die Uhr kurz nach dem Start per NTP um Stunden. Heartbeat und Rundenende
    # muessen dieselbe (neue) Zeitachse benutzen: Eine Hochrechnung vom Rundenbeginn (`now` plus gemessene Dauer) landete
    # auf der alten Achse, `heartbeat_at` liefe zurueck, und das Dashboard meldete "antwortet nicht" bzw. "nicht da".
    env.start()
    env.helper._preflight_at = None

    def hang(request):
        env.clock.t += 20 + jump  # die Engine haengt 20 s, mitten drin springt die Wanduhr
        env.helper._guarded(env.helper.heartbeat)
        assert env.status()["heartbeat_at"] == NOW + 20 + jump
        return Response(body=env.world.list_containers(request.query))

    env.fake.route("GET", "/containers/json", hang)
    env.tick(NOW)
    after = NOW + 2 * (20 + jump)  # zwei Listen-Aufrufe
    assert env.clock.t == after
    assert env.status()["heartbeat_at"] == after


def test_preflight_runs_at_start_and_then_every_5_minutes(env):
    env.start()

    def lists():
        return len(env.fake.calls("GET", "/containers/json"))

    after_start = lists()
    env.tick(NOW + 60)
    assert lists() == after_start
    env.tick(NOW + main.PREFLIGHT_INTERVAL_S)
    assert lists() > after_start


def test_preflight_retries_sooner_while_not_ready(env):
    env.target["State"]["Health"]["Status"] = "starting"
    env.start()
    assert env.status()["reason"] == policy.TARGET_UNHEALTHY
    env.target["State"]["Health"]["Status"] = "healthy"
    env.tick(NOW + 10)
    assert env.status()["reason"] == policy.TARGET_UNHEALTHY
    env.tick(NOW + main.PREFLIGHT_RETRY_S)
    assert env.status()["ready"] is True


def test_preflight_after_the_clock_jumps_back(env):
    env.start()
    count = len(env.fake.calls("GET", "/containers/json"))
    env.tick(NOW - 3600)
    assert len(env.fake.calls("GET", "/containers/json")) > count


@pytest.mark.parametrize(("change", "state", "reason"), [
    (lambda e: e.target["State"]["Health"].update(Status="unhealthy"), "idle", policy.TARGET_UNHEALTHY),
    (lambda e: e.target["Config"].update(Image="ghcr.io/nodvard/deck:0.7.0"), "idle", policy.PINNED_VERSION),
    (lambda e: e.world.containers.pop(e.target["Id"]), "idle", policy.NO_TARGET),
    (lambda e: e.target["Mounts"].append({"Type": "bind", "Source": "/var/run/docker.sock",
                                          "Destination": "/var/run/docker.sock", "RW": True}),
     "unsafe", policy.UNSAFE_TARGET),
    (lambda e: e.helper_container["Config"]["Labels"].pop("com.docker.compose.project"), "error",
     policy.NOT_COMPOSE),
    (lambda e: e.world.__setattr__("api", "1.40"), "error", policy.API_TOO_OLD),
])
def test_not_ready_reasons_in_the_status(env, change, state, reason):
    change(env)
    env.start()
    status = env.status()
    assert (status["state"], status["ready"], status["reason"]) == (state, False, reason)


def test_pinned_target_shows_its_version(env):
    env.target["Config"]["Image"] = "ghcr.io/nodvard/deck:0.7.0"
    env.start()
    assert env.status()["target"] == {"current_version": "0.7.0", "floating_tag": None, "pinned": True}


def test_engine_unreachable(tmp_path, uid):
    setup = Setup(tmp_path, uid)
    try:
        setup.helper._engine = Engine(str(tmp_path / "missing.sock"))
        setup.start()
        status = setup.status()
        assert (status["state"], status["ready"], status["reason"]) == ("error", False, policy.ENGINE_UNREACHABLE)
        rid = setup.request()
        setup.tick(NOW + 5)
        assert setup.result(rid)["code"] == policy.ENGINE_UNREACHABLE
    finally:
        setup.close()


def test_self_id_unknown(env):
    def unknown():
        raise policy.Refusal(policy.SELF_UNKNOWN)
    env.helper._own_id = unknown
    env.start()
    assert env.status()["reason"] == policy.SELF_UNKNOWN


def test_cluttered_channel_is_reported_while_ready(env):
    env.start()
    folder = env.root / "requests" / "stuck"
    folder.mkdir()
    (folder / "x").write_text("x")
    env.tick(NOW + 5)
    status = env.status()
    assert (status["ready"], status["reason"]) == (True, policy.CHANNEL_CLUTTERED)
    (folder / "x").unlink()
    env.tick(NOW + 10)
    assert env.status()["reason"] is None


# ---------------------------------------------------------------------------
# Kanal und Zustand unsicher
# ---------------------------------------------------------------------------


def test_channel_unsafe(env):
    env.root.chmod(0o777)
    env.start()
    status = env.status()  # der Status laesst sich trotzdem schreiben (Wurzel ist offen)
    assert (status["state"], status["ready"], status["reason"]) == ("unsafe", False, policy.CHANNEL_UNSAFE)
    env.root.chmod(0o755)
    env.tick(NOW + 5)
    assert env.status()["ready"] is True


def test_requests_are_not_touched_while_the_channel_is_unsafe(env):
    env.start()
    env.root.chmod(0o775)
    rid = env.request()
    env.tick(NOW + 5)
    assert env.pending() == [f"{rid}.json"]
    assert env.status()["reason"] == policy.CHANNEL_UNSAFE


def test_state_dir_unsafe(tmp_path, uid):
    setup = Setup(tmp_path, uid)
    try:
        setup.helper._store = StateStore(setup.state_dir, expected_uid=uid + 1)  # falscher Besitzer
        rid = setup.request()
        setup.start()
        status = setup.status()
        assert (status["state"], status["ready"], status["reason"]) == ("unsafe", False, policy.STATE_UNSAFE)
        assert setup.pending() == [f"{rid}.json"]  # ohne Zustand wird nichts gelesen
    finally:
        setup.close()


def test_second_instance_writes_no_status_and_reads_nothing(env, uid):
    first = StateStore(env.state_dir, expected_uid=uid)
    first.open()
    try:
        rid = env.request()
        env.start()
        assert not (env.root / "status.json").exists()
        assert env.pending() == [f"{rid}.json"]
        assert [e["event"] for e in env.logs].count("second_instance") == 1
        env.tick(NOW + 5)
        env.helper.heartbeat(NOW + 6)
        assert not (env.root / "status.json").exists()
    finally:
        first.close()
    env.tick(NOW + 5 + main.RETRY_SETUP_S)  # die erste Instanz ist weg: jetzt arbeitet diese
    assert env.status()["ready"] is True and env.pending() == []


def test_unreadable_state_is_retried_and_requests_stay(env, monkeypatch):
    real = env.store.load_state
    calls = []

    def flaky(now):
        calls.append(now)
        if len(calls) == 1:
            raise StateUnsafe("state_read")
        return real(now)

    monkeypatch.setattr(env.store, "load_state", flaky)
    rid = env.request()
    env.helper.setup(NOW)
    env.helper.write_status(NOW)
    assert env.status()["reason"] == policy.STATE_UNSAFE
    assert env.pending() == [f"{rid}.json"]
    env.tick(NOW + 5)
    assert env.pending() == [] and env.result(rid)["code"] == policy.NOT_IMPLEMENTED


def test_unreadable_journal_is_not_treated_as_no_journal(env, monkeypatch):
    real = env.store.load_journal
    calls = []

    def flaky(now):
        calls.append(now)
        if len(calls) <= 2:
            raise StateUnsafe("journal_read")
        return real(now)

    monkeypatch.setattr(env.store, "load_journal", flaky)
    rid = env.request()
    env.start()
    assert env.pending() == [f"{rid}.json"]  # nicht gelesen: es koennte ein Vorgang laufen
    assert env.status()["ready"] is False
    env.tick(NOW + 5)  # zweiter Versuch scheitert ebenfalls
    assert env.pending() == [f"{rid}.json"]
    env.tick(NOW + 10)
    assert len(calls) == 3
    assert env.pending() == [] and env.result(rid)["code"] == policy.NOT_IMPLEMENTED


def _journal(env) -> Journal:
    return Journal(
        request_id=str(uuid.uuid4()), action="update", step="started", started_at=NOW - 60, deadline=NOW + 840,
        old=OldContainer(id=env.target["Id"], name=env.target["Name"][1:], image_id=env.target["Image"],
                         version="0.7.0", restart_policy=("unless-stopped", 0), tag_text="ghcr.io/nodvard/deck:latest"),
        new=NewImage(image_id="sha256:" + "4" * 64, digest="sha256:" + "5" * 64, version="0.7.1", id="6" * 64),
    )


def test_running_journal_means_busy(env):
    journal = _journal(env)
    env.store.open()
    env.store.write_journal(journal)
    env.store.close()
    rid = env.request()
    env.start()
    status = env.status()
    assert (status["state"], status["ready"], status["reason"]) == ("busy", False, policy.BUSY)
    assert status["busy"] == {"id": journal.request_id, "action": "update", "step": "started", "since": NOW - 60}
    assert env.result(rid)["code"] == policy.BUSY
    # Waehrend `busy` gibt es kein `target` (mit Journal nie ein Ziel); die Versionen stehen in `busy` und `results`.
    assert status["target"] is None
    vector = next(c["doc"] for c in load_vectors("status.json")["valid"] if c["name"] == "beschaeftigt ohne Ziel")
    assert {key: status[key] for key in ("state", "ready", "reason", "target")} == \
        {key: vector[key] for key in ("state", "ready", "reason", "target")}
    assert set(status["busy"]) == set(vector["busy"]) and status["busy"]["step"] == vector["busy"]["step"]
    # Die IDs aus dem Journal zaehlen nie als Ziel (hier ist der alte Container das einzige, also kein Ziel).
    assert env.helper._preflight.reason == policy.NO_TARGET


def test_corrupt_journal_holds_everything(env):
    (env.state_dir / JOURNAL_NAME).write_text("{kaputt")
    rid = env.request()
    env.start()
    status = env.status()
    assert (status["state"], status["reason"]) == ("unsafe", policy.STATE_UNSAFE)
    assert env.result(rid)["code"] == policy.STATE_UNSAFE
    assert any(e["event"] == "journal_corrupt" for e in env.logs)


# ---------------------------------------------------------------------------
# Anforderungen
# ---------------------------------------------------------------------------


def test_valid_update_is_checked_and_refused_with_not_implemented(env):
    env.start()
    rid = env.request("update", "0.7.1")
    env.tick(NOW + 5)
    assert env.pending() == []
    assert env.result(rid) == {"id": rid, "action": "update", "from": "0.7.0", "to": "0.7.1", "outcome": "refused",
                               "code": policy.NOT_IMPLEMENTED, "finished_at": NOW + 5}
    state = json.loads((env.state_dir / "state.json").read_text())
    assert rid in state["seen"] and state["actions"] == []  # abgelehnt: zaehlt nicht fuer das Limit
    assert env.logs.count({"event": "request", "id": rid, "action": "update", "outcome": "refused",
                           "code": policy.NOT_IMPLEMENTED}) == 1


def test_requests_get_a_fresh_preflight_but_only_one_per_round(env):
    env.start()

    def lists():
        return len(env.fake.calls("GET", "/containers/json"))

    before = lists()
    env.request()
    env.tick(NOW + 5)
    after_one = lists()
    assert after_one > before
    for _ in range(10):
        env.request()
    env.tick(NOW + 10)
    assert lists() - after_one == after_one - before  # zehn Anforderungen: eine Vorpruefung


@pytest.mark.parametrize(("action", "version", "expected"), [
    ("update", "0.7.0", policy.NOT_NEWER),
    ("update", "0.6.9", policy.NOT_NEWER),
    ("update", "1.0.0", policy.NOT_IMPLEMENTED),
    ("rollback", "0.6.9", policy.NO_PREVIOUS),
])
def test_request_checks(env, action, version, expected):
    env.start()
    rid = env.request(action, version)
    env.tick(NOW + 5)
    assert env.result(rid)["code"] == expected


def test_minor_tag_limits_the_versions(env):
    env.target["Config"]["Image"] = "ghcr.io/nodvard/deck:0.7"
    env.start()
    rid = env.request("update", "0.8.0")
    ok = env.request("update", "0.7.2")
    env.tick(NOW + 5)
    assert env.result(rid)["code"] == policy.TAG_NOT_ON_VERSION
    assert env.result(ok)["code"] == policy.NOT_IMPLEMENTED


def _slot(env, **over):
    slot = {"from_version": "0.7.0", "image_id": "sha256:" + "7" * 64,
            "repo_digest": policy.REPOSITORY + "@sha256:" + "8" * 64, "installed_container_id": env.target["Id"],
            "installed_image_id": env.target["Image"], "until": NOW + 3600}
    slot.update(over)
    state = {"format": 1, "slot": slot, "actions": [], "blocked": {}, "seen": {}, "hold_until": None}
    (env.state_dir / "state.json").write_text(json.dumps(state))
    (env.state_dir / "state.json").chmod(0o600)


def test_rollback_with_a_valid_slot_is_checked_and_refused_with_not_implemented(env):
    env.world.images[env.target["Image"]]["Config"]["Labels"][policy.VERSION_LABEL] = "0.7.1"
    _slot(env)
    env.start()
    assert env.status()["previous"] == {"version": "0.7.0", "until": NOW + 3600}
    rid = env.request("rollback", "0.7.0")
    wrong = env.request("rollback", "0.7.1")
    env.tick(NOW + 5)
    assert env.result(rid)["code"] == policy.NOT_IMPLEMENTED
    assert env.result(wrong)["code"] == policy.PREVIOUS_MISMATCH


def _state(env, **fields):
    state = {"format": 1, "slot": None, "actions": [], "blocked": {}, "seen": {}, "hold_until": None, **fields}
    (env.state_dir / "state.json").write_text(json.dumps(state))
    (env.state_dir / "state.json").chmod(0o600)


def test_rate_limited_by_the_own_state(env):
    _state(env, actions=[NOW - 60])
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    assert env.result(rid)["code"] == policy.RATE_LIMITED


def test_blocked_version_after_a_rollback(env):
    _state(env, blocked={"0.7.1": NOW + 3600})
    env.start()
    blocked = env.request("update", "0.7.1")
    other = env.request("update", "0.7.2")
    env.tick(NOW + 5)
    assert env.result(blocked)["code"] == policy.BLOCKED_VERSION
    assert env.result(other)["code"] == policy.NOT_IMPLEMENTED


def test_rollback_below_the_minimum_version(env):
    env.world.images[env.target["Image"]]["Config"]["Labels"][policy.VERSION_LABEL] = "0.7.1"
    _slot(env, from_version="0.6.5")
    env.start()
    rid = env.request("rollback", "0.6.5")
    env.tick(NOW + 5)
    assert env.result(rid)["code"] == policy.VERSION_TOO_OLD


def test_channel_setup_and_heartbeat_do_not_race(env, monkeypatch):
    env.start()
    env.helper._channel_ok = False
    inside = threading.Event()
    real_setup = env.channel.setup

    def slow_setup():
        inside.set()
        time.sleep(0.2)
        real_setup()

    monkeypatch.setattr(env.channel, "setup", slow_setup)
    worker = threading.Thread(target=env.helper.setup, args=(NOW + 5,))
    worker.start()
    assert inside.wait(5)
    started = time.monotonic()
    env.helper.heartbeat(NOW + 6)  # wartet, bis die Einrichtung fertig ist
    assert time.monotonic() - started >= 0.1
    worker.join(5)
    assert env.status()["heartbeat_at"] == NOW + 6


def test_rollback_slot_for_another_container(env):
    _slot(env, installed_container_id="9" * 64)
    env.start()
    rid = env.request("rollback", "0.7.0")
    env.tick(NOW + 5)
    assert env.result(rid)["code"] == policy.PREVIOUS_MISMATCH


def test_expired_slot_is_not_shown(env):
    _slot(env, until=NOW - 1)
    env.start()
    assert env.status()["previous"] is None


def test_replay_of_a_processed_id(env):
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    env.request(rid=rid, created_at=NOW + 5)
    env.tick(NOW + 10)
    assert env.result(rid)["code"] == policy.REPLAY
    assert [e["event"] for e in env.logs if e.get("id") == rid] == ["request"]  # je ID eine Logzeile
    assert len([r for r in env.status()["results"] if r["id"] == rid]) == 1


def test_expired_and_future_requests(env):
    env.start()
    old = env.request(created_at=NOW - 601)
    future = env.request(created_at=NOW + 600)
    env.tick(NOW)
    assert env.result(old)["code"] == policy.EXPIRED
    assert env.result(future)["code"] == policy.BAD_REQUEST


def test_unreadable_request_without_action_has_no_status_entry(env):
    env.start()
    rid = str(uuid.uuid4())
    env.request(rid=rid, raw=b"{kaputt")
    other = str(uuid.uuid4())
    env.request(rid=other, raw=json.dumps({"v": 1, "id": other, "action": "update", "version": "0.7.1",
                                           "created_at": NOW, "extra": SECRET}).encode())
    env.tick(NOW + 5)
    assert env.result(rid) is None
    assert {"event": "request", "id": rid, "action": None, "outcome": "refused",
            "code": policy.BAD_REQUEST} in env.logs
    assert env.result(other)["code"] == policy.BAD_REQUEST and env.result(other)["action"] == "update"
    assert env.pending() == []
    assert SECRET not in (env.root / "status.json").read_text()


def test_fifo_request_does_not_block(env):
    env.start()
    rid = str(uuid.uuid4())
    os.mkfifo(env.root / "requests" / f"{rid}.json")
    done = threading.Event()
    thread = threading.Thread(target=lambda: (env.tick(NOW + 5), done.set()), daemon=True)
    thread.start()
    assert done.wait(10)
    assert env.pending() == []


def test_results_keep_the_last_ten(env):
    env.start()
    ids = [env.request("update", "0.7.0", created_at=NOW) for _ in range(12)]
    env.tick(NOW + 5)
    results = env.status()["results"]
    assert len(results) == policy.RESULTS_MAX
    assert {r["id"] for r in results} <= set(ids)


def test_hold_after_corrupt_state(env):
    (env.state_dir / "state.json").write_text("[]")
    (env.state_dir / "state.json").chmod(0o600)
    rid = env.request()
    env.start()
    status = env.status()
    assert (status["state"], status["reason"]) == ("unsafe", policy.STATE_UNSAFE)
    assert env.result(rid)["code"] == policy.STATE_UNSAFE


def test_a_failing_request_does_not_take_the_others_of_the_round_down(env, monkeypatch):
    env.start()
    first, second = env.request(), env.request()
    real = env.helper._handle

    def handle(item, now):
        if item.id == first:
            raise RuntimeError("boom")
        real(item, now)

    monkeypatch.setattr(env.helper, "_handle", handle)
    env.tick(NOW + 5)
    assert env.pending() == []
    assert env.result(second)["code"] == policy.NOT_IMPLEMENTED
    assert {"event": "internal_error", "id": first, "kind": "RuntimeError"} in env.logs


def test_request_gets_the_reason_of_the_fresh_preflight(env):
    env.start()
    assert env.status()["ready"] is True
    env.target["State"]["Health"]["Status"] = "unhealthy"  # seit der letzten Vorpruefung krank geworden
    rid = env.request()
    env.tick(NOW + 5)
    result = env.result(rid)
    assert (result["outcome"], result["code"], result["from"], result["to"]) == (
        "refused", policy.TARGET_UNHEALTHY, "0.7.0", "0.7.1")
    assert env.status()["reason"] == policy.TARGET_UNHEALTHY


def test_unexpected_error_in_a_round_does_not_stop_the_helper(env, monkeypatch):
    env.start()

    def boom(*_args, **_kwargs):
        raise RuntimeError("x\x1b[31m")

    monkeypatch.setattr(env.helper, "tick", boom)
    env.helper._guarded(env.helper.tick)
    assert env.logs[-1] == {"event": "internal_error", "kind": "RuntimeError"}


# ---------------------------------------------------------------------------
# Lauf mit Threads
# ---------------------------------------------------------------------------


def test_heartbeat_continues_while_the_engine_hangs(env, monkeypatch):
    monkeypatch.setattr(main, "POLL_INTERVAL_S", 0.05)
    monkeypatch.setattr(main, "HEARTBEAT_INTERVAL_S", 0.05)
    env.start(now=time.time())
    seq_before = env.status()["seq"]
    env.fake.route("GET", "/containers/json", Response(body=[], delay=1.5))  # die naechste Vorpruefung haengt
    env.helper._preflight_at = 0  # sofort faellig
    stop = threading.Event()
    runner = threading.Thread(target=env.helper.run, args=(stop,), daemon=True)
    runner.start()
    time.sleep(0.8)
    seqs = env.status()["seq"]
    stop.set()
    runner.join(10)
    assert not runner.is_alive()
    assert seqs > seq_before + 3  # der Heartbeat-Thread schreibt weiter, waehrend die Schleife wartet
    assert env.logs[-1]["event"] == "stop"


# ---------------------------------------------------------------------------
# Selbsttest und Einstieg
# ---------------------------------------------------------------------------


def test_selftest(capsys):
    assert main.selftest() == 0
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "selftest" and line["ok"] is True and line["version"] == __version__


def test_selftest_reports_the_failed_check(monkeypatch, capsys):
    monkeypatch.setattr(main, "_selftest_clone", lambda: False)
    assert main.selftest() == 1
    assert json.loads(capsys.readouterr().out.strip())["check"] == "clone"


def test_main_arguments(monkeypatch, capsys):
    assert main.main(["--unbekannt"]) == 2
    monkeypatch.setenv(policy.SERVICE_ENV, "Bad Name")
    assert main.main([]) == 2
    out = capsys.readouterr().out
    assert '"bad_arguments"' in out and '"bad_service"' in out


def test_run_as_module_in_isolated_mode_without_site_packages():
    # Wie im Image: `python -I -m nodvard_deck_updater --selftest` (hier mit dem Paket aus deploy/updater).
    script = textwrap.dedent(f"""
        import runpy, sys
        sys.path.insert(0, {str(UPDATER_DIR)!r})
        sys.argv = ["nodvard_deck_updater", "--selftest"]
        runpy.run_module("nodvard_deck_updater", run_name="__main__")
    """)
    env = {"PATH": os.environ.get("PATH", ""), policy.SERVICE_ENV: "nodvard-deck"}
    out = subprocess.run([sys.executable, "-I", "-S", "-c", script], capture_output=True, text=True, env=env,
                         timeout=60, check=False)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip())["ok"] is True


def test_log_lines_are_json(capsys):
    main.log("x", code="y", detail=None)
    line = json.loads(capsys.readouterr().out)
    assert line["event"] == "x" and line["code"] == "y" and isinstance(line["ts"], int)


# ---------------------------------------------------------------------------
# Abdeckung aller Codes der Vorpruefung
# ---------------------------------------------------------------------------

CODE_TESTS = {
    # Einrichtung
    policy.CHANNEL_UNSAFE: "test_main.py::test_channel_unsafe",
    policy.CHANNEL_CLUTTERED: "test_main.py::test_cluttered_channel_is_reported_while_ready",
    policy.STATE_UNSAFE: "test_main.py::test_state_dir_unsafe",
    policy.SECOND_INSTANCE: "test_main.py::test_second_instance_writes_no_status_and_reads_nothing",
    policy.SELF_UNKNOWN: "test_target.py::test_self_unknown",
    policy.NOT_COMPOSE: "test_target.py::test_not_compose",
    policy.ENGINE_UNREACHABLE: "test_target.py::test_engine_missing",
    policy.ENGINE_UNSUPPORTED: "test_target.py::test_podman",
    policy.API_TOO_OLD: "test_target.py::test_api_too_old",
    # Ziel
    policy.NO_TARGET: "test_target.py::test_no_target",
    policy.MULTIPLE_TARGETS: "test_target.py::test_doppelgaenger_means_multiple_targets",
    policy.TARGET_NOT_RUNNING: "test_target.py::test_target_not_running",
    policy.TARGET_UNHEALTHY: "test_target.py::test_target_unhealthy",
    policy.NO_HEALTHCHECK: "test_target.py::test_no_healthcheck",
    policy.SWARM: "test_target.py::test_swarm_labels",
    policy.FOREIGN_IMAGE: "test_target.py::test_foreign_image",
    policy.PINNED_VERSION: "test_target.py::test_pinned_version_still_reports_the_running_version",
    policy.NOT_FROM_REGISTRY: "test_target.py::test_not_from_registry",
    policy.NO_VERSION_LABEL: "test_target.py::test_no_version_label",
    policy.VERSION_TOO_OLD: "test_target.py::test_version_too_old",
    policy.IMAGE_CONFIG_MISSING: "test_target.py::test_image_config_missing",
    policy.AUTO_REMOVE: "test_target.py::test_auto_remove",
    policy.CUSTOM_ENTRYPOINT: "test_target.py::test_custom_entrypoint",
    policy.UNSAFE_TARGET: "test_target.py::test_unsafe_target_with_socket_or_state",
    policy.CHANNEL_MISSING_IN_TARGET: "test_target.py::test_channel_missing_in_target",
    policy.DEPENDENT_CONTAINERS: "test_target.py::test_dependent_containers",
    policy.MACVLAN: "test_target.py::test_macvlan",
    policy.NETWORK_UNCLEAR: "test_target.py::test_network_unclear",
    policy.UNKNOWN_FIELD: "test_target.py::test_unknown_field",
    policy.NAME_TAKEN: "test_target.py::test_name_taken",
    # Anforderung
    policy.BAD_REQUEST: "test_main.py::test_expired_and_future_requests",
    policy.EXPIRED: "test_main.py::test_expired_and_future_requests",
    policy.REPLAY: "test_main.py::test_replay_of_a_processed_id",
    policy.BUSY: "test_main.py::test_running_journal_means_busy",
    policy.RATE_LIMITED: "test_main.py::test_rate_limited_by_the_own_state",
    policy.BLOCKED_VERSION: "test_main.py::test_blocked_version_after_a_rollback",
    policy.NOT_NEWER: "test_main.py::test_request_checks",
    policy.TAG_NOT_ON_VERSION: "test_main.py::test_minor_tag_limits_the_versions",
    policy.NO_PREVIOUS: "test_main.py::test_request_checks",
    policy.PREVIOUS_MISMATCH: "test_main.py::test_rollback_with_a_valid_slot_is_checked_and_refused_with_not_implemented",
    policy.NOT_IMPLEMENTED: "test_main.py::test_valid_update_is_checked_and_refused_with_not_implemented",
    # Ablauf: die Nachkontrolle gibt es schon (clone.verify)
    policy.CLONE_MISMATCH: "test_clone.py::test_verify_rejects",
}
"""Je Code ein Test, der ihn am Helfer (Vorpruefung bzw. Anforderung) erwartet."""
CODES_LATER = frozenset({policy.PLATFORM_MISMATCH, policy.PULL_FAILED, *policy.FLOW_CODES}) - {policy.CLONE_MISMATCH}
"""Erst mit dem Ablauf (`flow`, eigener Schritt): Ziehen, Plattform, Anlegen, Stoppen, Starten, Beobachten."""


def test_every_code_of_section_3_has_a_test_or_comes_with_the_flow():
    assert set(CODE_TESTS) | CODES_LATER == policy.CODES and not set(CODE_TESTS) & CODES_LATER
    here = Path(__file__).resolve().parent
    for code_value, ref in CODE_TESTS.items():
        filename, name = ref.split("::")
        source = (here / filename).read_text(encoding="utf-8")
        node = next((n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name), None)
        assert node is not None, ref
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        text = "\n".join(source.splitlines()[start - 1:node.end_lineno])
        assert f"policy.{code_value.upper()}" in text or repr(code_value) in text or f'"{code_value}"' in text, ref
