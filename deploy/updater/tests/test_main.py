"""Hauptprogramm: Schleife, Heartbeat, Status, Vorpruefung, Anforderungen bis zum Ergebnis, Wiederaufnahme, Selbsttest.

Echte Ordner fuer Kanal und Zustand unter `tmp_path` (Besitzer = eigene uid als `expected_uid`). Die Engine ist die
durchgespielte Welt aus `flow_support` (eine aufgezeichnete Aufnahme, dazu Registry, Uhr der Tests und die aendernden
Aufrufe): Eine gueltige Anforderung laeuft also wirklich bis `applied`, `rolled_back` oder `reverted`. Die Fake-Engine
prueft dabei jeden aendernden Aufruf gegen das Journal auf der Platte, die Welt, dass nie zwei Container an denselben
Daten laufen. Am Ende jedes Tests muessen beide Listen leer sein, und das Geheimnis aus der Umgebung des Ziels steht
weder im Protokoll noch im Status.
"""

from __future__ import annotations

import ast
import copy
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
from fake_engine import Response
from flow_support import (
    LATEST,
    SECRET,
    Behavior,
    Crash,
    CrashingEngine,
    Lab,
    Points,
    _hex,
)
from nodvard_deck_updater import __main__ as main
from nodvard_deck_updater import __version__, channel, flow, policy
from nodvard_deck_updater.channel import Channel
from nodvard_deck_updater.engine import PREVIOUS_REPOSITORY, Engine
from nodvard_deck_updater.state import JOURNAL_NAME, State, StateStore, StateUnsafe
from updater_support import NOW, UPDATER_DIR, load_vectors

ENGINE_TEXT = "GEHEIM-ENGINE-TEXT"
"""Steht in einer Fehlermeldung der Engine: darf ins bereinigte Helfer-Protokoll, nie in den Status."""


class Setup:
    """Kanal und Zustand unter `tmp_path`, die durchgespielte Engine (`flow_support.Lab`) und der Helfer mit der Uhr der
    Tests (`clock.t`; das Warten im Ablauf stellt sie nur weiter)."""

    def __init__(self, tmp_path, uid, *, points: Points | None = None) -> None:
        self.lab = Lab(tmp_path, uid)
        self.lab.store.close()  # die Sperre gehoert dem Helfer, nicht dem Labor
        self.uid = uid
        self.root = tmp_path / "channel"
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        (self.root / "requests").mkdir()
        (self.root / "requests").chmod(0o1777)
        self.state_dir = self.lab.state_dir
        self.world = self.lab.world
        self.fake = self.lab.fake
        self.clock = self.lab.clock
        self.helper_container = self.lab.helper
        self.target = self.lab.target
        self.logs: list[dict] = []
        self._make_helper(points)

    def _make_helper(self, points: Points | None) -> None:
        self.channel = Channel(self.root, expected_uid=self.uid)
        self.store = StateStore(self.state_dir, expected_uid=self.uid)
        self.lab.store = self.store
        engine = Engine(self.fake.path) if points is None else CrashingEngine(self.fake.path, points)
        self.helper = main.Helper(channel=self.channel, store=self.store, engine=engine, service="nodvard-deck",
                                  own_id=lambda: self.helper_container["Id"], logger=self.log, clock=self.clock.time,
                                  monotonic=self.clock.monotonic, sleep=self.clock.sleep, uptime=self.lab.uptime,
                                  checkpoint=None if points is None else points.checkpoint)

    def restart(self, points: Points | None = None) -> None:
        """Der Helfer startet neu: die Sperre faellt mit dem Prozess, der neue hat einen frischen Client (noch nichts
        ausgehandelt). Kanal, `/state` und die Engine bleiben, wie sie sind."""
        self.store.close()
        self.channel.close()
        self.logs.append({"event": "--- neu gestartet ---"})
        self._make_helper(points)

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

    def request(self, action="update", version="0.7.1", *, rid=None, created_at=None, raw=None) -> str:
        rid = rid or str(uuid.uuid4())
        created_at = int(self.clock.t) if created_at is None else created_at
        data = raw if raw is not None else json.dumps(
            {"v": 1, "id": rid, "action": action, "version": version, "created_at": created_at}).encode()
        (self.root / "requests" / f"{rid}.json").write_bytes(data)
        return rid

    def pending(self) -> list[str]:
        return sorted(p.name for p in (self.root / "requests").iterdir())

    def result(self, rid) -> dict | None:
        return next((r for r in self.status()["results"] if r["id"] == rid), None)

    def outcome(self, rid) -> tuple | None:
        found = self.result(rid)
        return None if found is None else (found["outcome"], found["code"])

    def tick(self, now):
        """Eine Runde zum Zeitpunkt `now`; die Wanduhr steht zu Beginn auf `now`."""
        self.clock.t = now
        self.helper.tick(now)

    def next_round(self, after: float = 5.0) -> None:
        """Die naechste Runde, `after` Sekunden nach dem jetzigen Stand der Uhr (ein Ablauf hat sie weitergestellt)."""
        self.tick(self.clock.t + after)

    def start(self, now=NOW):
        self.clock.t = now
        self.helper.setup(now)
        self.helper.tick(now)

    def events(self) -> list[str]:
        return [entry["event"] for entry in self.logs]

    def since_restart(self) -> list[dict]:
        marks = [n for n, entry in enumerate(self.logs) if entry["event"] == "--- neu gestartet ---"]
        return self.logs[marks[-1] + 1:] if marks else list(self.logs)

    def mutations(self, kind: str | None = None) -> list[tuple[str, str]]:
        return self.lab.mutations(kind)


def _check_clean(setup: Setup) -> None:
    assert setup.fake.violations == []
    assert setup.world.violations == []
    assert SECRET not in json.dumps(setup.logs)
    status = setup.root / "status.json"
    if status.exists():
        text = status.read_text(encoding="ascii")
        assert SECRET not in text and ENGINE_TEXT not in text


@pytest.fixture
def env(tmp_path, uid):
    setup = Setup(tmp_path, uid)
    yield setup
    setup.close()
    _check_clean(setup)


def vector(name: str) -> dict:
    return next(case["doc"] for case in load_vectors("status.json")["valid"] if case["name"] == name)


def assert_like(status: dict, name: str, *, ids: list[str] | None = None) -> None:
    """Der Status hat genau die Form des Vektors `name` (Zustand, Grund, Ziel, Vorgang, Rueckweg und die Ergebnisse,
    ohne IDs und Zeiten): So schreibt der Helfer, was die gemeinsamen Vektoren dem Dashboard versprechen."""
    want = vector(name)
    assert {key: status[key] for key in ("state", "ready", "reason", "target")} == \
        {key: want[key] for key in ("state", "ready", "reason", "target")}
    for key, fields in (("busy", ("action", "step")), ("previous", ("version",))):
        if want[key] is None:
            assert status[key] is None, key
        else:
            assert {f: status[key][f] for f in fields} == {f: want[key][f] for f in fields}, key
    results = [r for r in status["results"] if ids is None or r["id"] in ids]
    if ids is not None:
        results.sort(key=lambda r: ids.index(r["id"]))

    def shape(entries):
        return [(r["action"], r["from"], r["to"], r["outcome"], r["code"]) for r in entries]

    assert shape(results) == shape(want["results"])


def saved_state(env) -> dict:
    return json.loads((env.state_dir / "state.json").read_text())


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
    assert stat_mode(env.root / "status.json") == 0o644
    assert stat_mode(env.root / "requests") == 0o1777
    assert env.world.mutations == []


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
        _check_clean(setup)


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
        _check_clean(setup)


def test_second_instance_writes_no_status_and_reads_nothing(env, uid):
    first = StateStore(env.state_dir, expected_uid=uid)
    first.open()
    try:
        rid = env.request("update", "0.7.0")
        env.start()
        assert not (env.root / "status.json").exists()
        assert env.pending() == [f"{rid}.json"]
        assert env.events().count("second_instance") == 1
        env.tick(NOW + 5)
        env.helper.heartbeat(NOW + 6)
        assert not (env.root / "status.json").exists()
    finally:
        first.close()
    env.tick(NOW + 5 + main.RETRY_SETUP_S)  # die erste Instanz ist weg: jetzt arbeitet diese
    assert env.status()["ready"] is True and env.pending() == []
    assert env.outcome(rid) == ("refused", policy.NOT_NEWER)


def test_unreadable_state_is_retried_and_requests_stay(env, monkeypatch):
    env.lab.release("0.7.1")
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
    assert env.pending() == [] and env.outcome(rid) == ("applied", None)


def test_unreadable_journal_is_not_treated_as_no_journal(env, monkeypatch):
    env.lab.release("0.7.1")
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
    assert env.pending() == [f"{rid}.json"] and env.world.mutations == []
    env.tick(NOW + 10)
    assert len(calls) >= 3
    assert env.pending() == [] and env.outcome(rid) == ("applied", None)


def test_corrupt_journal_holds_everything(env):
    (env.state_dir / JOURNAL_NAME).write_text("{kaputt")
    rid = env.request()
    env.start()
    status = env.status()
    assert (status["state"], status["reason"]) == ("unsafe", policy.STATE_UNSAFE)
    assert env.result(rid)["code"] == policy.STATE_UNSAFE
    assert "journal_corrupt" in env.events()
    assert env.world.mutations == []  # kein automatischer Schritt, nie gegen den Nutzer


def test_corrupt_journal_shows_the_hold_without_any_request(env):
    (env.state_dir / JOURNAL_NAME).write_text("{kaputt")
    env.start()
    status = env.status()
    assert (status["state"], status["ready"], status["reason"]) == ("unsafe", False, policy.STATE_UNSAFE)
    assert saved_state(env)["hold_until"] == NOW + 24 * 3600


def test_hold_after_corrupt_state(env):
    (env.state_dir / "state.json").write_text("[]")
    (env.state_dir / "state.json").chmod(0o600)
    rid = env.request()
    env.start()
    status = env.status()
    assert (status["state"], status["reason"]) == ("unsafe", policy.STATE_UNSAFE)
    assert env.result(rid)["code"] == policy.STATE_UNSAFE


# ---------------------------------------------------------------------------
# Anforderung -> Ergebnis
# ---------------------------------------------------------------------------


def test_valid_update_is_applied(env):
    new_image, _ = env.lab.release("0.7.1")
    env.start()
    rid = env.request("update", "0.7.1")
    env.tick(NOW + 5)
    assert env.pending() == []
    status = env.status()
    assert env.result(rid) == {"id": rid, "action": "update", "from": "0.7.0", "to": "0.7.1", "outcome": "applied",
                               "code": None, "finished_at": int(env.clock.t)}
    assert_like(status, "Update eingespielt")
    assert status["previous"]["until"] == int(env.clock.t) + policy.SLOT_TTL_S
    deck = env.lab.only_deck()
    assert deck["Image"] == new_image and deck["Name"] == "/" + env.lab.name
    state = saved_state(env)
    assert rid in state["seen"] and len(state["actions"]) == 1  # angenommen: zaehlt fuer die Grenze
    assert [entry["id"] for entry in state["results"]] == [rid]  # das Ergebnis uebersteht einen Neustart
    assert [e for e in env.logs if e["event"] == "request"] == [
        {"event": "request", "id": rid, "action": "update", "outcome": "applied", "code": None}]
    assert not (env.state_dir / JOURNAL_NAME).exists()


def test_status_shows_each_step_while_the_flow_runs(env):
    # Die Schleife haengt im Ablauf. Nach jedem Schritt im Journal steht er im Status (`busy`, ohne `target`), und der
    # Heartbeat-Thread schreibt weiter frische Zeitstempel.
    env.lab.release("0.7.1")
    env.start()
    seen: dict[str, tuple[dict, dict, int]] = {}

    def look(name):
        def handler(request):
            written = env.status()  # der Ablauf hat den Schritt schon selbst in den Status geschrieben
            env.clock.t += 7  # die Engine braucht eine Weile ...
            env.helper._guarded(env.helper.heartbeat)  # ... und der Heartbeat-Thread schreibt derweil
            seen[name] = (written, env.status(), int(env.clock.t))
            return env.world.answer(request)
        return handler

    env.fake.route("POST", "/containers/create", look("create"))
    env.fake.route("POST", r"/containers/(?!" + env.lab.old_id + r")[0-9a-f]{64}/start", look("start"))
    rid = env.request()
    env.tick(NOW + 5)
    assert_like(seen["create"][0], "beschaeftigt beim Anlegen")
    for name, step in (("create", "creating"), ("start", "started")):
        written, beat, at = seen[name]
        for status in (written, beat):
            assert (status["state"], status["ready"], status["reason"], status["target"]) == (
                "busy", False, "busy", None)
            assert status["busy"]["id"] == rid and status["busy"]["step"] == step
        assert beat["heartbeat_at"] == at and beat["seq"] > written["seq"]
    assert env.status()["busy"] is None and env.outcome(rid) == ("applied", None)


def test_the_periodic_preflight_rests_while_the_flow_runs(env):
    # Kein `/_ping` (also keine Neu-Aushandlung auf demselben Engine-Objekt) zwischen der ersten und der letzten
    # Aenderung: Die Vorpruefung laeuft im selben Thread wie der Ablauf und kommt erst danach wieder dran.
    env.lab.release("0.7.1")
    env.start()
    env.request()
    env.tick(NOW + 5)
    paths = [(r.method, r.path) for r in env.fake.requests]
    changes = [n for n, (method, _path) in enumerate(paths) if method != "GET"]
    assert len(changes) >= 9
    assert ("GET", "/_ping") not in paths[changes[0]:changes[-1]]
    assert ("GET", "/_ping") in paths[changes[-1]:]  # gleich danach eine frische Vorpruefung
    assert env.status()["target"]["current_version"] == "0.7.1"


def test_failed_update_rolls_back_and_says_why(env):
    env.lab.release("0.7.1", behavior=Behavior(exit_after=20))
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    assert_like(env.status(), "Rueckbau nach einem Fehler")
    env.lab.assert_back_to_old()
    assert len(saved_state(env)["actions"]) == 1  # ein gescheitertes Update kostet trotzdem einen Neustart
    assert env.outcome(rid) == ("rolled_back", policy.EXITED)


def test_old_container_that_does_not_come_back_needs_a_hand(env):
    env.lab.release("0.7.1", behavior=Behavior(exit_after=20))
    env.world.behaviors[env.lab.old_image] = Behavior(healthy_after=None, unhealthy_from=5)
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    assert_like(env.status(), "bitte von Hand pruefen")
    assert env.outcome(rid) == ("failed_manual", policy.ROLLBACK_FAILED)
    assert not (env.state_dir / JOURNAL_NAME).exists()  # kein weiterer Versuch, keine Schleife
    env.next_round()
    assert len(env.mutations("start")) == 2


def test_status_holds_only_fixed_codes_never_the_text_of_the_engine(env):
    env.lab.release("0.7.1")
    env.fake.route("POST", "/containers/create",
                   Response(status=500, body={"message": f"no space left on device {ENGINE_TEXT}\x1b[31m"}))
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    assert env.outcome(rid) == ("aborted", policy.CREATE_FAILED)
    text = (env.root / "status.json").read_text(encoding="ascii")
    assert ENGINE_TEXT not in text and "no space" not in text
    logged = [e for e in env.logs if e.get("message")]
    assert logged and all("\x1b" not in e["message"] for e in logged)  # im Protokoll nur bereinigt
    env.lab.assert_back_to_old()


def test_external_change_stops_everything(env):
    # Waehrend des Updates legt jemand einen weiteren Container des Dienstes an (z. B. `docker compose up -d`): Der
    # Helfer hoert sofort auf und fasst nichts mehr an.
    env.lab.release("0.7.1")
    twin = json.loads(json.dumps(env.target))
    twin.update(Id=_hex("twin"), Name="/deck-twin")
    twin["State"] = {"Status": "created", "Running": False}

    def rename(request):
        answer = env.world.answer(request)
        env.world.add_container(twin)
        return answer

    env.fake.route("POST", rf"/containers/{env.lab.old_id}/rename", rename)
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    assert_like(env.status(), "von aussen geaendert")
    assert env.outcome(rid) == ("external_change", policy.EXTERNAL_CHANGE)
    assert [kind for kind, _ in env.mutations()] == ["pull", "tag", "rename"]
    assert not (env.state_dir / JOURNAL_NAME).exists()


def test_replay_keeps_the_first_result(env):
    env.lab.release("0.7.1")
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    first = env.result(rid)
    assert first["outcome"] == "applied"
    changes = len(env.world.mutations)
    env.request(rid=rid)  # dieselbe ID noch einmal
    env.next_round()
    assert env.pending() == []
    assert len(env.world.mutations) == changes  # nichts doppelt
    assert [r for r in env.status()["results"] if r["id"] == rid] == [first]  # das erste Ergebnis bleibt
    assert [e["event"] for e in env.logs if e.get("id") == rid].count("request") == 1  # je ID eine Logzeile


def test_replay_of_an_id_whose_result_is_gone(env):
    rid = str(uuid.uuid4())
    _state(env, seen={rid: NOW - 60})
    env.start()
    env.request(rid=rid)
    env.tick(NOW + 5)
    assert env.outcome(rid) == ("refused", policy.REPLAY)


def test_rate_limit_after_an_update_and_a_second_update_later(env):
    env.lab.release("0.7.1")
    env.start()
    first = env.request()
    env.tick(NOW + 5)
    assert env.outcome(first) == ("applied", None)
    newer, _ = env.lab.release("0.7.2")
    second = env.request("update", "0.7.2")
    env.next_round(60)
    assert env.outcome(second) == ("refused", policy.RATE_LIMITED)
    counted = saved_state(env)["actions"][-1]
    third = env.request("update", "0.7.2", created_at=counted + policy.ACTION_INTERVAL_S)
    env.tick(counted + policy.ACTION_INTERVAL_S)
    assert env.outcome(third) == ("applied", None)
    assert env.lab.only_deck()["Image"] == newer
    assert env.status()["previous"]["version"] == "0.7.1"
    assert env.lab.protect("0.7.0") is None and env.lab.protect("0.7.1") is not None  # der ersetzte Slot gibt frei


def test_at_most_one_flow_per_round(env):
    # Zwei gueltige Anforderungen in einer Runde: Die erste laeuft (und scheitert hier an der Registry, das zaehlt nicht
    # fuer die Grenze), die zweite bekommt `busy` statt eines zweiten Ablaufs gleich hinterher.
    env.start()
    first, second = env.request(), env.request()
    env.tick(NOW + 5)
    assert sorted([env.outcome(first), env.outcome(second)]) == [
        ("refused", policy.BUSY), ("refused", policy.PULL_FAILED)]
    assert len(env.fake.calls("GET", r"/distribution/.+")) == 1
    assert saved_state(env)["actions"] == []


def test_rollback_on_request_goes_back_once(env):
    env.lab.release("0.7.1")
    env.start()
    update = env.request()
    env.tick(NOW + 5)
    assert env.status()["previous"]["version"] == "0.7.0"
    later = saved_state(env)["actions"][-1] + policy.ACTION_INTERVAL_S
    env.clock.t = later
    wrong = env.request("rollback", "0.7.1")
    env.tick(later)
    assert env.outcome(wrong) == ("refused", policy.PREVIOUS_MISMATCH)  # nur genau die Version davor
    rollback = env.request("rollback", "0.7.0")
    env.next_round()
    assert_like(env.status(), "Rueckweg eingespielt", ids=[update, rollback])
    deck = env.lab.only_deck()
    assert deck["Image"] == env.lab.old_image and env.world.tagged(LATEST) == env.lab.old_image
    assert env.lab.protect("0.7.0") is None and env.lab.protect("0.7.1") is None
    again = env.request("update", "0.7.1", created_at=int(env.clock.t) + policy.ACTION_INTERVAL_S)
    env.tick(int(env.clock.t) + policy.ACTION_INTERVAL_S)
    assert env.outcome(again) == ("refused", policy.BLOCKED_VERSION)  # 24 h kein Update auf die verlassene Version


def test_requests_get_a_fresh_preflight_but_only_one_per_round(env):
    env.start()

    def lists():
        return len(env.fake.calls("GET", "/containers/json"))

    before = lists()
    env.request("update", "0.7.0")
    env.tick(NOW + 5)
    after_one = lists()
    assert after_one > before
    for _ in range(10):
        env.request("update", "0.7.0")
    env.tick(NOW + 10)
    assert lists() - after_one == after_one - before  # zehn Anforderungen: eine Vorpruefung


@pytest.mark.parametrize(("action", "version", "expected"), [
    ("update", "0.7.0", policy.NOT_NEWER),
    ("update", "0.6.9", policy.NOT_NEWER),
    ("update", "1.0.0", policy.PULL_FAILED),  # besteht alle Pruefungen, die Registry kennt die Version nicht
    ("rollback", "0.6.9", policy.NO_PREVIOUS),
])
def test_request_checks(env, action, version, expected):
    env.start()
    rid = env.request(action, version)
    env.tick(NOW + 5)
    assert env.result(rid)["code"] == expected
    assert {kind for kind, _ in env.mutations()} <= {"pull"}


def test_minor_tag_limits_the_versions(env):
    env.target["Config"]["Image"] = "ghcr.io/nodvard/deck:0.7"
    env.lab.release("0.7.2", tag="0.7")
    env.start()
    rid = env.request("update", "0.8.0")
    env.tick(NOW + 5)
    assert env.result(rid)["code"] == policy.TAG_NOT_ON_VERSION
    ok = env.request("update", "0.7.2")
    env.next_round()
    assert env.outcome(ok) == ("applied", None)
    assert env.status()["target"] == {"current_version": "0.7.2", "floating_tag": "0.7", "pinned": False}


def _slot(env, **over):
    slot = {"from_version": "0.7.0", "image_id": "sha256:" + "7" * 64,
            "repo_digest": policy.REPOSITORY + "@sha256:" + "8" * 64, "installed_container_id": env.target["Id"],
            "installed_image_id": env.target["Image"], "until": NOW + 3600}
    slot.update(over)
    _state(env, slot=slot)


def _state(env, **fields):
    state = {"format": 1, "slot": None, "actions": [], "blocked": {}, "seen": {}, "hold_until": None, "results": [],
             **fields}
    (env.state_dir / "state.json").write_text(json.dumps(state))
    (env.state_dir / "state.json").chmod(0o600)


def test_rollback_checks_against_the_slot(env):
    env.world.images[env.target["Image"]]["Config"]["Labels"][policy.VERSION_LABEL] = "0.7.1"
    _slot(env)
    env.start()
    assert env.status()["previous"] == {"version": "0.7.0", "until": NOW + 3600}
    wrong = env.request("rollback", "0.7.1")
    env.tick(NOW + 5)
    assert env.result(wrong)["code"] == policy.PREVIOUS_MISMATCH
    assert env.world.mutations == []


def test_rate_limited_by_the_own_state(env):
    _state(env, actions=[NOW - 60])
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    assert env.result(rid)["code"] == policy.RATE_LIMITED


def test_blocked_version_after_a_rollback(env):
    _state(env, blocked={"0.7.1": NOW + 3600})
    env.lab.release("0.7.2")
    env.start()
    blocked = env.request("update", "0.7.1")
    env.tick(NOW + 5)
    assert env.result(blocked)["code"] == policy.BLOCKED_VERSION
    other = env.request("update", "0.7.2")
    env.next_round()
    assert env.outcome(other) == ("applied", None)


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
    assert [entry["id"] for entry in saved_state(env)["results"]] == [other]  # auch Ablehnungen ueberstehen Neustarts


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


def test_a_failing_request_does_not_take_the_others_of_the_round_down(env, monkeypatch):
    env.start()
    first, second = env.request("update", "0.7.0"), env.request("update", "0.7.0")
    real = env.helper._handle

    def handle(item, now):
        if item.id == first:
            raise RuntimeError("boom")
        real(item, now)

    monkeypatch.setattr(env.helper, "_handle", handle)
    env.tick(NOW + 5)
    assert env.pending() == []
    assert env.result(second)["code"] == policy.NOT_NEWER
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
# Neustart des Helfers und Wiederaufnahme
# ---------------------------------------------------------------------------


def _crash_during_an_update(env, point: str) -> str:
    """Der Helfer stuerzt mitten in einem Update ab (wie bei `kill -9`), genau an `point`; gibt die ID zurueck."""
    env.lab.release("0.7.1")
    env.restart(Points(crash_on=point))
    env.start()
    rid = env.request()
    with pytest.raises(Crash):
        env.tick(NOW + 5)
    return rid


def test_a_journal_at_start_is_recovered_before_anything_else(env):
    crashed = _crash_during_an_update(env, "call:rename_container")
    assert (env.state_dir / JOURNAL_NAME).exists()
    env.restart()
    waiting = env.request()  # liegt schon im Kanal, wenn der Helfer startet
    seen_pending = []

    def rename(request):
        seen_pending.append(env.pending())  # der Rueckbau laeuft, bevor die Anforderung gelesen ist
        return env.world.answer(request)

    env.fake.route("POST", rf"/containers/{env.lab.old_id}/rename", rename)
    env.start(int(env.clock.t) + 5)
    events = [e["event"] for e in env.since_restart()]
    assert events.index("recover") < events.index("preflight")
    assert seen_pending == [[f"{waiting}.json"]]
    assert env.outcome(crashed) == ("aborted", None)
    # Die Aktion war vor dem Absturz gezaehlt: Ein Neustart setzt die Grenze nicht zurueck, die Wiederaufnahme lief
    # trotzdem (sie wird nie gedrosselt).
    assert env.outcome(waiting) == ("refused", policy.RATE_LIMITED)
    assert len(saved_state(env)["actions"]) == 1
    env.lab.assert_back_to_old()
    assert (env.status()["state"], env.status()["ready"]) == ("idle", True)


def test_busy_while_the_recovery_waits_for_the_engine(env):
    crashed = _crash_during_an_update(env, "call:rename_container")
    env.restart()
    down = {"now": True}

    def ping(request):
        return Response(status=500, body={"message": "daemon restarting"}) if down["now"] else env.world.answer(request)

    env.fake.route("GET", "/_ping", ping)
    env.start(int(env.clock.t) + 5)
    status = env.status()
    assert_like(status, "Wiederaufnahme wartet auf die Engine")
    assert status["busy"]["id"] == crashed
    later = saved_state(env)["actions"][-1] + policy.ACTION_INTERVAL_S  # die Grenze laesst wieder eine Aktion zu
    asked = env.request(created_at=later)
    env.tick(later)
    assert env.outcome(asked) == ("refused", policy.BUSY)
    assert [kind for kind, _ in env.mutations()] == ["pull", "tag", "rename"]  # nichts angefasst, solange sie fehlt
    down["now"] = False
    env.next_round()
    assert env.outcome(crashed) == ("aborted", None) and env.status()["busy"] is None
    env.lab.assert_back_to_old()


def test_an_old_preflight_never_shows_as_target_while_busy(env, monkeypatch):
    # Nach dem Neustart ist das Journal zuerst nicht lesbar: Die Vorpruefung laeuft trotzdem (sie liest nur) und sieht
    # den umbenannten alten Container als Ziel. Sobald das Journal gelesen ist, gilt der Helfer als beschaeftigt, und
    # der Status zeigt kein Ziel aus dieser Vorpruefung, auch solange die Wiederaufnahme auf die Engine wartet.
    crashed = _crash_during_an_update(env, "call:rename_container")
    env.restart()
    real = env.store.load_journal
    reads = []

    def flaky(now):
        reads.append(now)
        if len(reads) == 1:
            raise StateUnsafe("journal_read")
        return real(now)

    monkeypatch.setattr(env.store, "load_journal", flaky)
    self_path = rf"/containers/{env.helper_container['Id']}/json"
    broken = {"now": False}

    def inspect_self(request):
        return Response(status=500, body={"message": "busy"}) if broken["now"] else env.world.answer(request)

    env.fake.route("GET", self_path, inspect_self)
    env.start(int(env.clock.t) + 5)
    assert env.helper._preflight is not None and env.helper._preflight.known
    broken["now"] = True  # die Wiederaufnahme kommt nicht an der Engine vorbei
    env.next_round()
    status = env.status()
    assert (status["state"], status["busy"]["id"], status["target"]) == ("busy", crashed, None)
    broken["now"] = False
    env.next_round()
    assert env.outcome(crashed) == ("aborted", None)
    env.lab.assert_back_to_old()


def test_an_unexpected_error_in_the_flow_is_taken_up_in_the_next_round(env, monkeypatch):
    # Ein Fehler im Programm mitten im Ablauf beendet nur diese Anforderung, nicht den Helfer. Das Journal steht noch,
    # und die naechste Runde nimmt den Vorgang wie nach einem Absturz wieder auf.
    env.lab.release("0.7.1")
    env.start()
    current = env.helper._ensure_flow()

    def broken(_run):
        raise RuntimeError("boom")

    monkeypatch.setattr(current, "_stop_old", broken)
    rid = env.request()
    env.tick(NOW + 5)
    assert {"event": "internal_error", "id": rid, "kind": "RuntimeError"} in env.logs
    status = env.status()
    assert (status["state"], status["busy"]["step"]) == ("busy", "created")
    env.next_round()
    assert env.outcome(rid) == ("aborted", None)
    env.lab.assert_back_to_old()
    assert env.status()["busy"] is None


def test_an_error_in_the_recovery_does_not_stop_the_round(env, monkeypatch):
    crashed = _crash_during_an_update(env, "call:rename_container")
    later = saved_state(env)["actions"][-1] + policy.ACTION_INTERVAL_S  # die Grenze laesst wieder eine Aktion zu
    env.restart()

    def broken(_state):
        raise RuntimeError("boom")

    monkeypatch.setattr(env.helper._ensure_flow(), "recover", broken)
    asked = env.request(created_at=later)
    env.start(later)
    assert {"event": "internal_error", "kind": "RuntimeError"} in env.logs
    assert env.outcome(asked) == ("refused", policy.BUSY)  # die Runde lief weiter, der Vorgang bleibt stehen
    assert env.status()["busy"]["id"] == crashed
    monkeypatch.undo()
    env.next_round()
    assert env.outcome(crashed) == ("aborted", None)
    env.lab.assert_back_to_old()


def test_an_error_while_tidying_the_slot_does_not_stop_the_round(env, monkeypatch):
    _slot(env, until=NOW - 1)

    def broken(_state):
        raise RuntimeError("boom")

    monkeypatch.setattr(env.helper._ensure_flow(), "drop_expired_slot", broken)
    rid = env.request("update", "0.7.0")
    env.start()
    assert env.outcome(rid) == ("refused", policy.NOT_NEWER)
    assert {"event": "internal_error", "kind": "RuntimeError"} in env.logs
    assert saved_state(env)["slot"] is not None  # bleibt stehen, der naechste Versuch kommt spaeter


def test_crash_before_anything_changed(env):
    crashed = _crash_during_an_update(env, "after:begin")
    env.restart()
    env.start(int(env.clock.t) + 5)
    assert_like(env.status(), "nach einem Neustart abgebrochen")
    assert env.outcome(crashed) == ("aborted", None)
    assert env.world.mutations == [] and saved_state(env)["actions"] == []


def test_results_survive_a_restart(env):
    env.lab.release("0.7.1")
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    before = env.result(rid)
    env.restart()
    env.start(int(env.clock.t) + 5)
    assert env.result(rid) == before
    assert env.status()["previous"]["version"] == "0.7.0"


def test_cleanup_after_the_commit_is_finished_in_a_later_round(env):
    env.lab.release("0.7.1")
    refuse = {"now": True}

    def remove(request):
        return Response(status=500, body={"message": "daemon busy"}) if refuse["now"] else env.world.answer(request)

    env.fake.route("DELETE", rf"/containers/{env.lab.old_id}", remove)
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    status = env.status()
    assert status["state"] == "busy" and status["busy"]["step"] == "committed"
    # Entschieden und schon gespeichert (Slot und Ergebnis gehen vor dem alten Container), das Aufraeumen laeuft noch.
    assert (env.result(rid)["outcome"], env.result(rid)["code"]) == ("applied", None)
    assert env.status()["previous"]["version"] == "0.7.0"
    refuse["now"] = False
    env.next_round()
    assert env.outcome(rid) == ("applied", None) and env.status()["busy"] is None
    assert env.lab.old_id not in env.world.containers
    assert env.status()["target"]["current_version"] == "0.7.1"


def _slot_from(env, version: str = "0.6.9") -> policy.Slot:
    """Ein Rueckweg von einem frueheren Update (auf die laufende Version) samt Schutz-Tag, gespeichert in `/state`
    (vor dem Start des Helfers)."""
    lab = env.lab
    image_id = "sha256:" + _hex(f"image-{version}")
    lab.world.images[image_id] = {**copy.deepcopy(lab.world.images[lab.old_image]), "Id": image_id,
                                  "RepoTags": [f"{PREVIOUS_REPOSITORY}:{version}"], "RepoDigests": []}
    slot = policy.Slot(from_version=version, image_id=image_id, repo_digest=f"{policy.REPOSITORY}@sha256:{'4' * 64}",
                       installed_container_id=lab.old_id, installed_image_id=lab.old_image,
                       until=int(env.clock.t) + 3600)
    store = StateStore(env.state_dir, expected_uid=env.uid)
    store.open()
    try:
        store.save_state(State(slot=slot))
    finally:
        store.close()
    return slot


def _rollback_ready(env) -> str:
    """Ein Update auf 0.7.1 ist eingespielt, die Grenze laesst den Rueckweg zu. Gibt die ID des Updates zurueck."""
    env.lab.release("0.7.1")
    env.start()
    update = env.request()
    env.tick(NOW + 5)
    assert env.outcome(update) == ("applied", None)
    env.clock.t = saved_state(env)["actions"][-1] + policy.ACTION_INTERVAL_S
    return update


CLEANUP_CALLS = [("DELETE", r"/containers/[0-9a-f]{64}"), ("DELETE", rf"/images/{PREVIOUS_REPOSITORY}:.+")]
"""Was der Helfer beim Aufraeumen nach dem Commit an der Engine aendert: Container und Schutz-Tags entfernen."""


@pytest.mark.parametrize("case", ["update", "update_replacing_a_slot", "rollback"])
def test_the_result_is_in_the_status_before_the_cleanup_changes_anything(env, case):
    # Mit `committed` ist der Vorgang entschieden. Noch bevor der Helfer beim Aufraeumen irgendetwas an der Engine
    # aendert, steht das Ergebnis in `state.json` und im Status: Das Dashboard sieht `busy.step` = `committed` nie ohne
    # das Ergebnis dieser Anforderung.
    seen: list[tuple[str, dict, dict]] = []

    def look(request):
        seen.append((request.path, env.status(), saved_state(env)))
        return env.world.answer(request)

    if case == "rollback":
        _rollback_ready(env)
        action, version = "rollback", "0.7.0"
    else:
        env.lab.release("0.7.1")
        if case == "update_replacing_a_slot":
            _slot_from(env)
        env.start()
        action, version = "update", "0.7.1"
    for method, path in CLEANUP_CALLS:
        env.fake.route(method, path, look)
    rid = env.request(action, version)
    env.next_round()
    want = ("reverted" if action == "rollback" else "applied", None)
    assert env.outcome(rid) == want and env.status()["busy"] is None
    path, status, state = seen[0]
    if case == "update_replacing_a_slot":
        assert path == f"/images/{PREVIOUS_REPOSITORY}:0.6.9"  # das Tag des ersetzten Slots geht zuerst
    assert status["busy"]["id"] == rid and status["busy"]["step"] == "committed"
    assert [(r["outcome"], r["code"]) for r in status["results"] if r["id"] == rid] == [want]
    assert [(r["outcome"], r["code"]) for r in state["results"] if r["id"] == rid] == [want]


def test_while_the_engine_hangs_at_the_cleanup_the_status_already_has_the_result(env):
    # Das Schutz-Tag des ersetzten Slots laesst sich nicht entfernen, weil der Docker-Dienst haengt (Verbindung ohne
    # Antwort). Das zaehlt keine Runde, der Helfer bleibt so lange beschaeftigt -- aber das Ergebnis steht die ganze Zeit
    # im Status, und `previous` zeigt noch den ersetzten Slot, bis dessen Tag weg ist.
    env.lab.release("0.7.1")
    _slot_from(env)
    down = {"now": True}

    def untag(request):
        return Response(raw=b"") if down["now"] else env.world.answer(request)

    env.fake.route("DELETE", rf"/images/{PREVIOUS_REPOSITORY}:0.6.9", untag)
    env.start()
    rid = env.request()
    env.next_round()
    for _round in range(flow.CLEANUP_ROUNDS + 2):
        status = env.status()
        assert status["busy"]["id"] == rid and status["busy"]["step"] == "committed"
        assert_like(status, "beschaeftigt, Aufraeumen", ids=[rid])
        assert [(r["id"], r["outcome"]) for r in saved_state(env)["results"]] == [(rid, "applied")]
        env.next_round()
    down["now"] = False
    env.next_round()
    status = env.status()
    assert status["busy"] is None and env.outcome(rid) == ("applied", None)
    assert status["previous"]["version"] == "0.7.0" and env.lab.protect("0.6.9") is None
    assert env.lab.old_id not in env.world.containers


def _written_statuses(env) -> list[dict]:
    """Jeder Status, den der Helfer ab jetzt schreibt (aus dem Ablauf, am Ende einer Runde, als Herzschlag)."""
    written: list[dict] = []
    real = env.channel.write_status

    def capture(doc, *, durable=True):
        written.append(copy.deepcopy(doc))
        return real(doc, durable=durable)

    env.channel.write_status = capture
    return written


def _results_of(doc: dict, rid: str) -> list[tuple]:
    return [(r["outcome"], r["code"]) for r in doc["results"] if r["id"] == rid]


@pytest.mark.parametrize("case", ["update", "update_replacing_a_slot", "rollback"])
def test_no_status_ever_shows_committed_without_its_result(env, case):
    # Jeder Status, den der Helfer waehrend eines Vorgangs schreibt -- auch der erste zu `committed` --, zeigt
    # `busy.step` = `committed` nur zusammen mit dem Ergebnis dieser Anforderung.
    if case == "rollback":
        _rollback_ready(env)
        action, version = "rollback", "0.7.0"
    else:
        env.lab.release("0.7.1")
        if case == "update_replacing_a_slot":
            _slot_from(env)
        env.start()
        action, version = "update", "0.7.1"
    written = _written_statuses(env)
    rid = env.request(action, version)
    env.next_round()
    want = ("reverted" if action == "rollback" else "applied", None)
    assert env.outcome(rid) == want and env.status()["busy"] is None
    committed = [doc for doc in written if doc["busy"] is not None and doc["busy"]["step"] == "committed"]
    assert len(committed) >= 2  # gleich nach dem Commit und sobald Slot bzw. Sperre gespeichert sind
    assert [_results_of(doc, rid) for doc in committed] == [[want]] * len(committed)


def _crash_at_the_commit(env, action: str) -> tuple[str, tuple]:
    """Der Helfer stuerzt ab, gleich nachdem `committed` im Journal steht, noch bevor das Ergebnis gespeichert ist.
    Gibt die ID und das Ergebnis zurueck, das am Ende dastehen muss."""
    if action == "rollback":
        _rollback_ready(env)
        version, start = "0.7.0", int(env.clock.t)
    else:
        env.lab.release("0.7.1")
        version, start = "0.7.1", NOW
    points = Points(crash_on="after:committed")
    env.restart(points)
    save = env.store.save_state

    def save_while_alive(state):
        if points.crash_on is not None:  # nach dem Absturz (wie `kill -9`) schreibt auch kein `finally` mehr
            save(state)

    env.store.save_state = save_while_alive
    env.start(start)
    rid = env.request(action, version)
    with pytest.raises(Crash):
        env.next_round()
    assert [r for r in saved_state(env)["results"] if r["id"] == rid] == []
    env.restart()
    return rid, ("reverted" if action == "rollback" else "applied", None)


@pytest.mark.parametrize("action", ["update", "rollback"])
def test_after_a_crash_at_the_commit_the_status_has_the_result_while_the_engine_is_away(env, action):
    # Nach dem Neustart antwortet der Docker-Dienst nicht. Schon der erste Herzschlag (vor der ersten Runde) und jeder
    # Status danach zeigt `committed` mit dem Ergebnis, und es steht in `state.json` -- ohne die Engine. Ist sie wieder
    # da, raeumt der Helfer fertig auf; das Ergebnis behaelt seinen ersten Zeitpunkt.
    rid, want = _crash_at_the_commit(env, action)
    env.fake.route("GET", "/_ping", lambda request: Response(status=500, body={"message": "daemon restarting"}))
    written = _written_statuses(env)
    now = int(env.clock.t) + 5
    env.clock.t = now
    env.helper.setup(now)
    env.helper.heartbeat(now)
    assert len(written) == 1
    for _round in range(3):
        env.next_round(30)
        env.helper.heartbeat(env.clock.t)
    assert all(doc["busy"] is not None and doc["busy"]["step"] == "committed" for doc in written)
    assert [_results_of(doc, rid) for doc in written] == [[want]] * len(written)
    saved = [r for r in saved_state(env)["results"] if r["id"] == rid]
    assert [(r["outcome"], r["code"]) for r in saved] == [want]
    env.fake.route("GET", "/_ping", lambda request: env.world.answer(request))
    env.next_round()
    assert env.status()["busy"] is None and [env.result(rid)] == saved


def test_after_a_crash_at_the_commit_the_status_has_the_result_while_the_own_id_is_unknown(env):
    # Nach dem Neustart kennt der Helfer seine eigene ID nicht (die Wiederaufnahme wartet): Entschieden ist der Vorgang
    # trotzdem, der Status zeigt `committed` mit dem Ergebnis, und es steht in `state.json`.
    rid, want = _crash_at_the_commit(env, "update")

    def unknown():
        raise policy.Refusal(policy.SELF_UNKNOWN)

    own = env.helper._own_id
    env.helper._own_id = unknown
    env.start(int(env.clock.t) + 5)
    env.next_round()
    status = env.status()
    assert status["busy"]["id"] == rid and status["busy"]["step"] == "committed"
    assert _results_of(status, rid) == [want]
    assert [(r["outcome"], r["code"]) for r in saved_state(env)["results"] if r["id"] == rid] == [want]
    assert "recover_waiting" in [e["event"] for e in env.since_restart()]
    env.helper._own_id = own
    env.next_round()
    assert env.status()["busy"] is None and env.outcome(rid) == want


@pytest.mark.parametrize("replacing", [False, True], ids=["first", "replacing"])
def test_the_new_way_back_shows_as_soon_as_it_is_saved(env, replacing):
    # Gleich nach dem Speichern des neuen Slots schreibt der Helfer den Status: `previous` zeigt den neuen Rueckweg schon,
    # waehrend er den alten Container entfernt -- nicht erst mit dem naechsten Herzschlag.
    env.lab.release("0.7.1")
    if replacing:
        _slot_from(env)
    seen: list[dict] = []

    def remove(request):
        seen.append(env.status())
        return env.world.answer(request)

    env.fake.route("DELETE", rf"/containers/{env.lab.old_id}", remove)
    env.start()
    env.request()
    env.next_round()
    (status,) = seen
    slot = saved_state(env)["slot"]
    assert status["busy"]["step"] == "committed"
    assert status["previous"] == {"version": "0.7.0", "until": slot["until"]} and slot["from_version"] == "0.7.0"


def test_an_undo_that_waits_for_the_engine_is_finished_in_a_later_round(env):
    # Der Docker-Dienst haengt laenger als die Frist des Rueckbaus (Verbindung ohne Antwort): Der Helfer bleibt
    # beschaeftigt statt aufzugeben, und eine spaetere Runde baut fertig zurueck.
    env.lab.release("0.7.1", behavior=Behavior(exit_after=20))
    down = {"now": True}

    def remove(request):
        return Response(raw=b"") if down["now"] else env.world.answer(request)

    env.fake.route("DELETE", r"/containers/[0-9a-f]{64}", remove)
    env.start()
    rid = env.request()
    env.tick(NOW + 5)
    status = env.status()
    assert status["state"] == "busy" and status["busy"]["id"] == rid and env.result(rid) is None
    down["now"] = False
    env.next_round()
    assert env.outcome(rid) == ("rolled_back", policy.EXITED) and env.status()["busy"] is None
    env.lab.assert_back_to_old()


def test_an_expired_slot_is_tidied_up(env):
    env.lab.release("0.7.1")
    env.start()
    env.request()
    env.tick(NOW + 5)
    until = saved_state(env)["slot"]["until"]
    assert env.lab.protect("0.7.0") is not None
    failing = {"now": True}

    def untag(request):
        return Response(status=500, body={"message": "busy"}) if failing["now"] else env.world.answer(request)

    env.fake.route("DELETE", r"/images/nodvard-deck-previous:.+", untag)
    env.tick(until)
    assert saved_state(env)["slot"] is not None and env.status()["previous"] is None
    env.tick(until + 10)
    assert len(env.fake.calls("DELETE", r"/images/nodvard-deck-previous:.+")) == 1  # erst nach `SLOT_RETRY_S` erneut
    failing["now"] = False
    env.tick(until + main.SLOT_RETRY_S)
    assert saved_state(env)["slot"] is None and env.lab.protect("0.7.0") is None


def test_an_expired_slot_whose_tag_is_refused_for_good_is_not_asked_again_and_again(env):
    # 409 beim Entfernen des Schutz-Tags (ein liegen gebliebener Container benutzt das Image): endgueltig. Der Slot geht
    # beim ersten Mal, und die folgenden Runden fragen die Engine nicht alle 30 s erneut.
    env.lab.release("0.7.1")
    env.start()
    env.request()
    env.tick(NOW + 5)
    until = saved_state(env)["slot"]["until"]
    env.fake.route("DELETE", r"/images/nodvard-deck-previous:.+",
                   Response(status=409, body={"message": "conflict: unable to remove repository reference"}))
    for later in range(0, 10 * int(main.SLOT_RETRY_S), int(main.SLOT_RETRY_S)):
        env.tick(until + later)
    assert len(env.fake.calls("DELETE", r"/images/nodvard-deck-previous:.+")) == 1
    assert saved_state(env)["slot"] is None and env.lab.protect("0.7.0") is not None
    assert env.events().count("slot_cleanup_failed") == 0


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


@pytest.mark.parametrize("check", ["clone", "flow"])
def test_selftest_reports_the_failed_check(monkeypatch, capsys, check):
    monkeypatch.setattr(main, f"_selftest_{check}", lambda: False)
    assert main.selftest() == 1
    assert json.loads(capsys.readouterr().out.strip())["check"] == check


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
# Abdeckung aller Codes
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
    policy.REPLAY: "test_main.py::test_replay_of_an_id_whose_result_is_gone",
    policy.BUSY: "test_main.py::test_busy_while_the_recovery_waits_for_the_engine",
    policy.RATE_LIMITED: "test_main.py::test_rate_limit_after_an_update_and_a_second_update_later",
    policy.BLOCKED_VERSION: "test_main.py::test_rollback_on_request_goes_back_once",
    policy.NOT_NEWER: "test_main.py::test_request_checks",
    policy.TAG_NOT_ON_VERSION: "test_main.py::test_minor_tag_limits_the_versions",
    policy.PLATFORM_MISMATCH: "test_flow.py::test_wrong_pulled_image_changes_nothing",
    policy.PULL_FAILED: "test_main.py::test_at_most_one_flow_per_round",
    policy.NO_PREVIOUS: "test_main.py::test_request_checks",
    policy.PREVIOUS_MISMATCH: "test_main.py::test_rollback_checks_against_the_slot",
    # Ablauf
    policy.CREATE_FAILED: "test_main.py::test_status_holds_only_fixed_codes_never_the_text_of_the_engine",
    policy.CLONE_MISMATCH: "test_flow.py::test_tag_moved_in_between_is_caught_by_the_check",
    policy.STOP_FAILED: "test_flow.py::test_stop_fails_and_the_old_keeps_running",
    policy.START_FAILED: "test_flow.py::test_start_fails_and_the_old_comes_back",
    policy.EXITED: "test_main.py::test_failed_update_rolls_back_and_says_why",
    policy.RESTART_LOOP: "test_flow.py::test_hard_failures_roll_back_early",
    policy.RESCUE_PAGE: "test_flow.py::test_hard_failures_roll_back_early",
    policy.TIMEOUT: "test_flow.py::test_unhealthy_until_the_deadline_rolls_back",
    policy.EXTERNAL_CHANGE: "test_main.py::test_external_change_stops_everything",
    policy.ROLLBACK_FAILED: "test_main.py::test_old_container_that_does_not_come_back_needs_a_hand",
}
"""Je Code ein Test, der ihn am Helfer erwartet (Vorpruefung, Anforderung oder Ablauf)."""
CODES_RESERVED = frozenset({policy.NOT_IMPLEMENTED})
"""Im Protokoll, aber fuer Update und Rueckweg nie geschrieben (Codes fallen nie weg)."""


def test_every_code_has_a_test():
    assert set(CODE_TESTS) | CODES_RESERVED == policy.CODES and not set(CODE_TESTS) & CODES_RESERVED
    here = Path(__file__).resolve().parent
    for code_value, ref in CODE_TESTS.items():
        filename, name = ref.split("::")
        source = (here / filename).read_text(encoding="utf-8")
        node = next((n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name), None)
        assert node is not None, ref
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        text = "\n".join(source.splitlines()[start - 1:node.end_lineno])
        assert f"policy.{code_value.upper()}" in text or repr(code_value) in text or f'"{code_value}"' in text, ref


def test_valid_requests_never_end_with_not_implemented():
    # Update und Rueckweg fuehrt der Helfer aus: kein Modul schreibt den reservierten Code.
    package = UPDATER_DIR / "nodvard_deck_updater"
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        uses = [node.lineno for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and node.attr == "NOT_IMPLEMENTED"]
        assert uses == [], path.name
