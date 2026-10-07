"""Wiederaufnahme nach einem Absturz des Helfers (`Flow.recover`): die Absturz-Matrix.

Jeder Fall laesst einen Vorgang an genau einer Stelle "abstuerzen" -- vor und nach jedem Schreiben in `/state`
(Journal, `state.json`, Loeschen des Journals) und nach jedem aendernden Engine-Aufruf --, startet den Helfer neu (neue
Sperre, neuer Client, neuer Ablauf; Engine und `/state` bleiben) und nimmt den Vorgang wieder auf. In den
verschachtelten Faellen stuerzt auch die Wiederaufnahme selbst ab, an jeder ihrer Stellen einzeln und mehrmals
hintereinander, bis eine durchlaeuft. Danach gilt immer (`assert_settled`):

* genau ein Container des Dienstes laeuft, unter dem Originalnamen und gesund; kein `<name>-previous` bleibt liegen;
* nie zwei laufende Container an denselben Daten, jeder Start mit den Daten des Ziels (die Welt prueft das bei jedem
  Aufruf); kein Volume geloescht (jedes Entfernen mit `v=0`, der laufende Container hat alle Volumes des Ziels);
* die Anforderung laeuft nicht doppelt: hoechstens ein Pull und ein angelegter Container, eine weitere Wiederaufnahme
  hat nichts mehr zu tun, dieselbe Anforderung noch einmal ist `replay`;
* die Grenze bleibt erhalten: die Aktion zaehlt genau einmal, sobald sie gezaehlt war -- ein Neustart setzt nichts
  zurueck, und die Wiederaufnahme selbst wird von keiner Grenze gebremst;
* das Ergebnis steht fest, passt in den Status (`status.json`) und steht genau einmal in `state.json`.

Wie der Helfer eine Anforderung annimmt (Grenzen pruefen, ID merken, speichern, dann `Flow.run`), spielt `Case.submit`
nach.
"""

from __future__ import annotations

import copy
import json

import pytest
from fake_engine import Response
from flow_support import (
    LATEST,
    Behavior,
    Crash,
    Lab,
    LiveWorld,
    Points,
    _hex,
    status_with,
)
from nodvard_deck_updater import flow, policy, target
from nodvard_deck_updater.engine import PREVIOUS_REPOSITORY
from nodvard_deck_updater.flow import HEALTHY, Outcome, judge
from nodvard_deck_updater.policy import Refusal
from nodvard_deck_updater.state import JOURNAL_NAME, STATE_NAME, JournalCorrupt, State

SCENARIOS = ("update", "failing", "rollback", "replacing")
"""`update`: 0.7.0 -> 0.7.1, gesund. `failing`: 0.7.1 beendet sich nach 20 s, zurueck auf 0.7.0. `rollback`: nach einem
Update auf 0.7.1 auf Wunsch zurueck auf 0.7.0. `replacing`: wie `update`, aber es gibt schon einen Rueckweg auf 0.6.9
(Slot samt Schutz-Tag, von einem frueheren Update); der neue ersetzt ihn, und sein Schutz-Tag geht."""

UPDATE_POINTS = [
    "before:begin", "after:begin", "call:pull", "before:pulled", "after:pulled", "before:state", "after:state",
    "before:protected", "after:protected", "call:tag_image", "before:renamed", "after:renamed", "call:rename_container",
    "before:tagged", "after:tagged", "call:tag_image", "before:creating", "after:creating", "call:create_container",
    "before:created", "after:created", "call:connect_network", "before:old_stopped", "after:old_stopped",
    "call:set_restart_policy", "call:stop_container", "before:started", "after:started", "call:start_container",
    "before:committed", "after:committed", "before:state", "after:state", "before:state", "after:state",
    "call:remove_container", "before:clear", "after:clear",
]
"""Die Stellen eines gesunden Updates in ihrer Reihenfolge: jeder Schritt steht im Journal, bevor die Engine ihn
ausfuehrt (write-ahead), die Aktion zaehlt vor der ersten Aenderung. Gleich nach `committed` steht das Ergebnis in
`state.json` (noch vor jedem Aufruf an der Engine), dann der Slot, beides bevor der alte Container und das Journal
gehen."""
FAILING_POINTS = UPDATE_POINTS[:UPDATE_POINTS.index("before:committed")] + [
    "before:undo", "after:undo", "call:stop_container", "call:remove_container", "call:tag_image",
    "call:rename_container", "call:set_restart_policy", "call:start_container", "call:remove_protect_tag",
    "before:state", "after:state", "before:clear", "after:clear",
]
"""Der neue Container beendet sich: der Rueckbau kuendigt sich im Journal an, bevor er etwas zuruecknimmt. Er stoppt den
neuen Container auch, wenn der schon steht (ein Start kann noch nachkommen; der Docker-Dienst antwortet dann 304)."""
_COMMIT = UPDATE_POINTS.index("call:remove_container")
ROLLBACK_POINTS = ([point for point in UPDATE_POINTS[:_COMMIT + 1] if point != "call:pull"]
                   + ["call:remove_protect_tag", "call:remove_protect_tag"] + UPDATE_POINTS[_COMMIT + 1:])
"""Rueckweg auf Wunsch: das Image liegt noch da (kein Pull), beim Commit gehen beide Schutz-Tags."""
_EARLY = UPDATE_POINTS.index("after:committed") + 3
REPLACING_POINTS = UPDATE_POINTS[:_EARLY] + ["call:remove_protect_tag"] + UPDATE_POINTS[_EARLY:]
"""Update, das einen vorhandenen Slot ersetzt: nach dem Ergebnis, noch bevor der neue Slot gespeichert ist, geht das
Schutz-Tag des alten (die Bindung laesst es nur zu, solange er in `state.json` steht)."""
POINTS = {"update": UPDATE_POINTS, "failing": FAILING_POINTS, "rollback": ROLLBACK_POINTS,
          "replacing": REPLACING_POINTS}
FIRST_FAILING = FAILING_POINTS.index("call:start_container")
"""Bis zum Start des neuen Containers ist `failing` dasselbe wie `update`: die Matrix nimmt dort nur die Stellen ab
hier."""
FINAL = {"update": ("applied", None), "failing": ("rolled_back", policy.EXITED), "rollback": ("reverted", None),
         "replacing": ("applied", None)}
CONTAINERD_SCENARIOS = ("update", "rollback")
"""Diese Szenarien laufen zusaetzlich mit dem containerd-Store (`Lab(image_store="containerd")`): Dort nennt die Engine
den Registry-Digest des alten Images nach dem Umhaengen des beweglichen Tags nicht mehr -- wichtig fuer jeden Commit,
den erst die Wiederaufnahme macht."""


def _cases():
    for scenario in SCENARIOS:
        start = FIRST_FAILING if scenario == "failing" else 0
        for index in range(start, len(POINTS[scenario])):
            yield pytest.param(scenario, index, "graphdriver", id=f"{scenario}-{index:02d}-{POINTS[scenario][index]}")
    for scenario in CONTAINERD_SCENARIOS:
        for index in range(len(POINTS[scenario])):
            yield pytest.param(scenario, index, "containerd",
                               id=f"{scenario}-containerd-{index:02d}-{POINTS[scenario][index]}")


def expected(scenario: str, index: int) -> tuple[str, str | None] | None:
    """Was am Ende herauskommen muss, wenn der Vorgang an der Stelle `index` abstuerzt. `None`, wenn das vor dem ersten
    Eintrag im Journal geschah: dann ist nichts geschehen und es gibt kein Ergebnis."""
    seen = POINTS[scenario][:index + 1]
    written = [point.split(":", 1)[1] for point in seen if point.startswith("after:")]
    if not written:
        return None
    if "committed" in written or "undo" in written:
        return FINAL[scenario]
    step = [name for name in written if name in policy.STEPS][-1]
    if step != "started":
        return "aborted", None  # der neue Container lief nie
    if "call:start_container" not in seen[seen.index("after:started"):]:
        return "rolled_back", policy.START_FAILED  # angekuendigt, aber nie gestartet
    return FINAL[scenario]  # er lief: weiter beobachten, dann Commit oder Rueckbau


def find(scenario: str, point: str, occurrence: int = 1) -> int:
    """Nummer der `occurrence`-ten Stelle `point` im Szenario."""
    positions = [number for number, name in enumerate(POINTS[scenario]) if name == point]
    return positions[occurrence - 1]


def counted(scenario: str, index: int) -> bool:
    """Die Aktion zaehlt, sobald sie nach `pulled` gespeichert ist (vor der ersten Aenderung)."""
    return "after:state" in POINTS[scenario][:index + 1]


# ---------------------------------------------------------------------------
# Ein Fall
# ---------------------------------------------------------------------------


def _slot_from_an_earlier_update(lab: Lab, version: str = "0.6.9") -> str:
    """Ein Rueckweg auf `version` von einem frueheren Update (auf die laufende Version): das Image mit seinem Schutz-Tag
    und der Slot in `state.json`. Gibt die ID des Images zurueck."""
    image_id = "sha256:" + _hex(f"image-{version}")
    lab.world.images[image_id] = {**copy.deepcopy(lab.world.images[lab.old_image]), "Id": image_id,
                                  "RepoTags": [f"{PREVIOUS_REPOSITORY}:{version}"], "RepoDigests": []}
    state = lab.state()
    state.slot = policy.Slot(from_version=version, image_id=image_id,
                             repo_digest=f"{policy.REPOSITORY}@sha256:{'4' * 64}", installed_container_id=lab.old_id,
                             installed_image_id=lab.old_image, until=int(lab.clock.t) + 3600)
    lab.store.save_state(state)
    return image_id


class Case:
    """Ein Labor mit einem vorbereiteten Szenario. `submit` nimmt eine Anforderung an wie der Helfer, `crash_at` fuehrt
    sie bis zu einem Absturz aus, `revive` startet den Helfer neu und nimmt wieder auf."""

    def __init__(self, tmp_path, uid, scenario: str, image_store: str = "graphdriver") -> None:
        tmp_path.mkdir()
        self.scenario = scenario
        self.lab = lab = Lab(tmp_path, uid, image_store=image_store)
        self.previous_image = None
        if scenario == "replacing":
            self.previous_image = _slot_from_an_earlier_update(lab)
        if scenario == "rollback":
            self.newer, _ = lab.release("0.7.1")
            assert self.submit(lab.request("update", "0.7.1")).outcome == "applied"
            lab.clock.t += policy.ACTION_INTERVAL_S  # die Grenze laesst den Rueckweg wieder zu
            self.request = lab.request("rollback", "0.7.0")
        else:
            self.newer, _ = lab.release("0.7.1", behavior=Behavior(exit_after=20) if scenario == "failing" else None)
            self.request = lab.request("update", "0.7.1")
        self.before = copy.deepcopy(lab.only_deck())
        self.slot_before = lab.state().slot
        self.mutations_before = len(lab.world.mutations)
        self.submitted_at = int(lab.clock.t)

    def submit(self, request: policy.Request) -> Outcome:
        lab = self.lab
        state = lab.state()
        lab.store.check(state, request, lab.clock.t)
        state.mark_seen(request.id, lab.clock.t)
        lab.store.save_state(state)
        return lab.flow.run(request, state)

    def crash_at(self, index: int | None) -> Outcome | None:
        """Fuehrt die Anforderung aus und stuerzt an der Stelle `index` ab (`None`: gar nicht). Das Ergebnis, oder
        `None` nach einem Absturz."""
        points = Points(index)
        self.lab.restart_helper(points)
        try:
            return self.submit(self.request)
        except Crash:
            return None
        finally:
            self.seen = points.seen

    def revive(self, points: Points | None = None) -> Outcome | None:
        self.lab.restart_helper(points)
        return self.lab.flow.recover(self.lab.state())

    def revive_until_done(self) -> tuple[Outcome | None, int]:
        """Neu starten und wieder aufnehmen, bis es durchlaeuft; die n-te Wiederaufnahme stuerzt an ihrer n-ten Stelle
        ab. Gibt das letzte Ergebnis und die Zahl der Abstuerze in der Wiederaufnahme zurueck."""
        for attempt in range(100):
            try:
                return self.revive(Points(attempt)), attempt
            except Crash:
                continue
        raise AssertionError("die Wiederaufnahme kommt nie zu Ende")

    def reached(self, point: str) -> bool:
        """Kam der erste Lauf bis zu dieser Stelle?"""
        return point in self.seen

    @property
    def before_version(self) -> str:
        return "0.7.0" if self.scenario != "rollback" else "0.7.1"

    @property
    def journal_left(self) -> bool:
        return (self.lab.state_dir / JOURNAL_NAME).exists()


@pytest.fixture
def make_case(tmp_path, uid):
    made: list[Case] = []

    def make(scenario: str = "update", image_store: str = "graphdriver") -> Case:
        case = Case(tmp_path / f"case{len(made)}", uid, scenario, image_store)
        made.append(case)
        return case

    yield make
    for case in made:
        case.lab.close()
        case.lab.assert_clean()


# ---------------------------------------------------------------------------
# Was nach jedem Absturz gilt
# ---------------------------------------------------------------------------


def assert_settled(case: Case, outcome: Outcome | None, want: tuple[str, str | None] | None, *,
                   was_counted: bool) -> None:
    lab = case.lab
    assert not case.journal_left

    # Genau ein Container des Dienstes, unter dem Originalnamen, laeuft gesund.
    deck = lab.only_deck()
    assert deck["Name"] == "/" + lab.name
    assert [c["Id"] for c in lab.world.containers.values() if c["Name"] == "/" + lab.name] == [deck["Id"]]
    assert not any(c["Name"].endswith("-previous") for c in lab.world.containers.values())
    others = [c for c in lab.world.running() if c["Id"] not in (deck["Id"], lab.helper["Id"])]
    assert [c["Name"] for c in others if LiveWorld.mount_origin(c, "/app/data") is not None] == []
    assert judge(deck, 0) == HEALTHY, deck["State"]
    assert deck["HostConfig"]["RestartPolicy"] == lab.restart

    # Keine Daten verloren: dieselbe Quelle fuer /app/data, alle Volumes des Ziels, jedes Entfernen ohne Volumes.
    assert LiveWorld.mount_origin(deck, "/app/data") == lab.world.data_origin
    volumes = {m["Name"] for m in case.before["Mounts"] if m.get("Type") == "volume"}
    assert volumes <= {m["Name"] for m in deck["Mounts"] if m.get("Type") == "volume"}
    removals = lab.fake.calls("DELETE", r"/containers/[0-9a-f]{64}")
    assert all(r.query == {"v": ["0"], "force": ["0"]} for r in removals)

    # Was am Ende laeuft.
    done = want is not None and want[0] in ("applied", "reverted")
    final_image = (case.newer if case.scenario != "rollback" else lab.old_image) if done else case.before["Image"]
    assert deck["Image"] == final_image and lab.world.tagged(LATEST) == final_image
    state = lab.state()
    if case.scenario == "rollback":
        if done:
            assert lab.protect("0.7.0") is None and lab.protect("0.7.1") is None
            assert state.slot is None and "0.7.1" in state.blocked  # einmal benutzt, die Version 24 h gesperrt
        else:
            assert lab.protect("0.7.0") == lab.old_image and lab.protect("0.7.1") is None
            assert state.slot == case.slot_before  # der Slot bleibt, mit seinem Schutz
    elif done:
        assert lab.protect("0.7.0") == lab.old_image
        assert state.slot is not None and state.slot.installed_container_id == deck["Id"]
        # Ein Rueckweg genau auf das alte Image, auch wenn die Engine dessen Digest nicht mehr nennt (containerd-Store).
        assert (state.slot.image_id, state.slot.repo_digest) == (lab.old_image, f"{policy.REPOSITORY}@{lab.old_digest}")
    else:
        assert lab.protect("0.7.0") is None and state.slot == case.slot_before  # bei `replacing` der von 0.6.9
    if case.scenario == "replacing":
        assert lab.protect("0.6.9") == (None if done else case.previous_image)

    # Nicht doppelt: hoechstens ein Pull und ein angelegter Container fuer diese Anforderung.
    mine = [kind for kind, _ in lab.world.mutations[case.mutations_before:]]
    assert mine.count("create") <= 1 and mine.count("pull") <= 1

    # Das Ergebnis: entschieden, genau einmal in state.json, gueltig fuer den Status.
    entries = [entry for entry in state.results if entry["id"] == case.request.id]
    if want is None:
        assert outcome is None and entries == []
    else:
        assert outcome is None or ((outcome.outcome, outcome.code, outcome.finished) == (*want, True))
        assert [(entry["outcome"], entry["code"]) for entry in entries] == [want]
        # Die Version steht im Journal erst ab `pulled`: ein Abbruch davor nennt sie nicht.
        to = case.request.version if case.reached("after:pulled") else None
        assert (entries[0]["action"], entries[0]["from"], entries[0]["to"]) == (
            case.request.action, case.before_version, to)
        status_with(entries)

    # Die Grenze und der Schutz vor Wiederholung ueberstehen jeden Neustart.
    assert case.request.id in state.seen
    assert len([at for at in state.actions if at >= case.submitted_at]) == (1 if was_counted else 0)

    # Danach ist nichts mehr zu tun, und dieselbe Anforderung noch einmal ist eine Wiederholung.
    mutations = len(lab.world.mutations)
    assert case.revive() is None
    assert len(lab.world.mutations) == mutations
    with pytest.raises(Refusal) as info:
        lab.store.check(lab.state(), case.request, lab.clock.t)
    assert info.value.code == policy.REPLAY


# ---------------------------------------------------------------------------
# Die Matrix
# ---------------------------------------------------------------------------


def test_the_points_are_the_ones_the_matrix_expects(make_case):
    # Ohne Absturz: genau diese Stellen in genau dieser Reihenfolge (sonst passte die Matrix nicht mehr zum Ablauf).
    for scenario in SCENARIOS:
        case = make_case(scenario)
        outcome = case.crash_at(None)
        assert (outcome.outcome, outcome.code) == FINAL[scenario]
        assert case.seen == POINTS[scenario], scenario


@pytest.mark.parametrize(("scenario", "index", "image_store"), list(_cases()))
def test_crash_and_recover(make_case, scenario, index, image_store):
    case = make_case(scenario, image_store)
    assert case.crash_at(index) is None, "an dieser Stelle muss der Helfer abstuerzen"
    assert case.seen[-1] == POINTS[scenario][index]
    outcome = case.revive()
    assert_settled(case, outcome, expected(scenario, index), was_counted=counted(scenario, index))


NESTED = [
    ("update", "after:pulled", 1),  # Journal loeschen
    ("update", "call:rename_container", 1),  # Rueckbau, der alte laeuft noch
    ("update", "call:create_container", 1),  # der eigene Container wird uebernommen und entfernt
    ("update", "call:stop_container", 1),  # Rueckbau, der alte steht und startet wieder
    ("update", "after:started", 1),  # nie gestartet: Rueckbau
    ("update", "call:start_container", 1),  # weiter beobachten, dann Commit
    ("update", "after:committed", 1),  # Aufraeumen nach dem Commit
    ("replacing", "after:committed", 1),  # dazu das Schutz-Tag des ersetzten Slots
    ("failing", "call:start_container", 1),  # beobachten, dann Rueckbau
    ("failing", "call:rename_container", 2),  # mitten im angekuendigten Rueckbau
    ("rollback", "call:start_container", 1),  # Commit des Rueckwegs
    ("rollback", "call:remove_container", 1),  # Aufraeumen nach dem Rueckweg
]


@pytest.mark.parametrize(("scenario", "point", "occurrence"), NESTED, ids=[f"{s}-{p}-{n}" for s, p, n in NESTED])
def test_crash_after_crash_after_crash(make_case, scenario, point, occurrence):
    # Erst stuerzt der Vorgang ab, dann die Wiederaufnahme selbst, immer wieder -- die n-te an ihrer n-ten Stelle --,
    # bis eine durchlaeuft. Jede Wiederaufnahme beginnt dort, wo die vorige liegen blieb.
    index = find(scenario, point, occurrence)
    case = make_case(scenario)
    assert case.crash_at(index) is None
    outcome, crashes = case.revive_until_done()
    assert crashes >= 2, "die Wiederaufnahme ist wirklich abgestuerzt, mehrmals"
    assert_settled(case, outcome, expected(scenario, index), was_counted=counted(scenario, index))


INSIDE = [
    ("update", "call:create_container", 1),  # uebernehmen, ankuendigen, zurueckbauen
    ("update", "call:stop_container", 1),  # Rueckbau mit Neustart des alten
    ("update", "call:start_container", 1),  # Commit und Aufraeumen
    ("replacing", "call:start_container", 1),  # Commit, Schutz-Tag des ersetzten Slots, Aufraeumen
    ("failing", "call:start_container", 1),  # Rueckbau nach dem Beobachten
    ("rollback", "call:start_container", 1),  # Commit des Rueckwegs, beide Schutz-Tags
]


@pytest.mark.parametrize(("scenario", "point", "occurrence"), INSIDE, ids=[f"{s}-{p}-{n}" for s, p, n in INSIDE])
def test_crash_at_every_point_of_the_recovery(tmp_path, uid, scenario, point, occurrence):
    # Fuer jede Stelle der Wiederaufnahme einzeln: Vorgang stuerzt ab, Wiederaufnahme stuerzt genau dort ab, die
    # naechste laeuft durch.
    index = find(scenario, point, occurrence)
    probe = Case(tmp_path / "probe", uid, scenario)
    try:
        assert probe.crash_at(index) is None
        points = Points()
        probe.revive(points)
        inside = list(points.seen)
    finally:
        probe.lab.close()
    probe.lab.assert_clean()
    assert len(inside) >= 6 and inside[-2:] == ["before:clear", "after:clear"]
    for number, label in enumerate(inside):
        case = Case(tmp_path / f"at{number:02d}", uid, scenario)
        try:
            assert case.crash_at(index) is None
            with pytest.raises(Crash, match=label):
                case.revive(Points(number))
            outcome = case.revive()
            assert_settled(case, outcome, expected(scenario, index), was_counted=counted(scenario, index))
        finally:
            case.lab.close()
        case.lab.assert_clean()


# ---------------------------------------------------------------------------
# Absturz in `creating`: der eigene Container steht noch nicht im Journal
# ---------------------------------------------------------------------------


def test_crash_right_after_create_adopts_the_own_container(make_case):
    # `create` ist durch, die Antwort kam nie an: der neue Container traegt die Compose-Labels des Ziels, seine ID steht
    # aber noch nicht im Journal. Die Vorpruefung haelt ihn darum fuer fremd -- die Wiederaufnahme darf sich nicht auf
    # sie stuetzen, sondern erkennt ihn selbst (Zustand `created`, nie gestartet, neues Image, gleiche Labels),
    # traegt ihn als `created` ein und entfernt ihn.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("call:create_container")) is None
    journal = lab.store.load_journal(lab.clock.t)
    assert journal.step == "creating" and journal.new.id is None
    created = lab.mutations("create")[-1][1]
    assert lab.world.containers[created]["Config"]["Labels"]["com.docker.compose.service"] == "nodvard-deck"
    check = target.preflight(lab.engine, self_id=lab.helper["Id"], service="nodvard-deck", exclude=(journal.old.id,))
    assert (check.ready, check.reason, check.target) == (False, policy.EXTERNAL_CHANGE, None)

    lab.steps.clear()
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("aborted", None)
    assert lab.steps[:2] == ["created", "undo"]  # erst eingetragen, dann der Rueckbau angekuendigt
    assert ("remove", created) in lab.mutations() and created not in lab.world.containers
    assert_settled(case, outcome, ("aborted", None), was_counted=True)


def test_a_foreign_container_under_the_name_after_a_crash_in_creating_is_left_alone(make_case):
    # Absturz vor `create`; dann legt jemand selbst einen Container unter dem Namen an. Er gehoert nicht dem Helfer
    # (laeuft schon, anderes Image): nichts anfassen.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("after:creating")) is None
    foreign = copy.deepcopy(case.before)
    foreign.update(Id=_hex("foreign"), Name="/" + lab.name)
    foreign["Config"]["Labels"] = {}
    lab.world.add_container(foreign)
    before = len(lab.world.mutations)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == before and not case.journal_left
    lab.world.violations.clear()  # zwei laufende Container an denselben Daten: das hat der Test angerichtet


def _compose_label(name: str, value: str | None):
    def change(container: dict, lab: Lab) -> None:
        labels = container["Config"]["Labels"]
        if value is None:
            labels.pop(name)
        else:
            labels[name] = value
    return change


def _inspect_shows_name(container: dict, lab: Lab) -> None:
    # Die Liste nennt ihn unter dem Namen des Ziels, sein Inspect unter einem anderen.
    shown = {**container, "Name": "/" + lab.name + "-anders"}
    lab.fake.route("GET", rf"/containers/{container['Id']}/json", Response(body=shown))


NOT_OWN_CREATED = {
    "status": lambda c, lab: c["State"].update(Status="exited"),
    "running": lambda c, lab: c["State"].pop("Running"),
    "started_at": lambda c, lab: c["State"].update(StartedAt="2026-10-02T08:00:00.5Z"),
    "image": lambda c, lab: c.update(Image=lab.old_image),
    "name": _inspect_shows_name,
    "label_missing": _compose_label("com.docker.compose.config-hash", None),
    "label_changed": _compose_label("com.docker.compose.container-number", "2"),
    "label_added": _compose_label("com.docker.compose.replace", "irgendwas"),
    "image_label_old": lambda c, lab: c["Config"]["Labels"].update({"com.docker.compose.image": lab.old_image}),
}
"""Je genau eine Abweichung vom eigenen, gerade angelegten Container (alles andere passt)."""


@pytest.mark.parametrize("path", ["recover", "undo"])
@pytest.mark.parametrize("change", list(NOT_OWN_CREATED.values()), ids=list(NOT_OWN_CREATED))
def test_a_container_under_the_name_that_differs_in_one_point_is_left_alone(make_case, change, path):
    # Die ID des neuen Containers steht nicht im Journal: der Helfer stuerzt direkt nach `create` ab (`recover`), oder
    # die Antwort auf `create` geht verloren und der Ablauf baut zurueck (`undo`, dort genuegt danach die ID). Der
    # Container unter `<name>` waere der eigene -- bis auf genau eine Sache. Dann ist er es nicht: nichts entfernen,
    # nichts anfassen, das Journal geht.
    case = make_case("update")
    lab = case.lab
    if path == "recover":
        assert case.crash_at(UPDATE_POINTS.index("call:create_container")) is None
        created = lab.mutations("create")[-1][1]
        change(lab.world.containers[created], lab)
        before = len(lab.world.mutations)
        outcome = case.revive()
    else:
        def lost(request):
            answer = lab.world.answer(request)
            change(lab.world.containers[answer.body["Id"]], lab)
            return Response(status=answer.status, body=answer.body, drop=True)

        lab.fake.route("POST", "/containers/create", lost)
        lab.restart_helper()
        outcome = case.submit(case.request)
        created = lab.mutations("create")[-1][1]
        before = len(lab.world.mutations)
    assert (outcome.outcome, outcome.code) == ("external_change", policy.EXTERNAL_CHANGE)
    assert lab.mutations("remove") == [] and created in lab.world.containers
    assert len(lab.world.mutations) == before and not case.journal_left


# ---------------------------------------------------------------------------
# Von aussen geaendert, waehrend der Helfer nicht lief
# ---------------------------------------------------------------------------


def _compose_created_a_new_one(case: Case, *, running: bool) -> dict:
    """Wie `docker compose up -d` waehrend der Helfer nicht lief: ein weiterer Container des Dienstes."""
    lab = case.lab
    twin = copy.deepcopy(case.before)
    twin.update(Id=_hex("compose-twin"), Name=f"/{lab.name[:-1]}2")
    twin["State"] = copy.deepcopy(twin["State"]) if running else {"Status": "created", "Running": False}
    lab.world.add_container(twin)
    return twin


@pytest.mark.parametrize("point", ["after:protected", "call:rename_container", "call:create_container",
                                   "call:stop_container", "call:start_container", "after:undo"])
def test_compose_recreated_in_between_means_no_action(make_case, point):
    scenario = "failing" if point == "after:undo" else "update"
    case = make_case(scenario)
    lab = case.lab
    assert case.crash_at(POINTS[scenario].index(point)) is None
    twin = _compose_created_a_new_one(case, running=False)
    before = len(lab.world.mutations)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == before  # nichts angefasst
    assert twin["Id"] in lab.world.containers and not case.journal_left
    assert [e["outcome"] for e in lab.state().results if e["id"] == case.request.id] == ["external_change"]
    assert case.revive() is None and len(lab.world.mutations) == before


def test_compose_replaced_the_new_container_while_the_helper_was_down(make_case):
    # Der neue Container laeuft schon; Compose hat ihn ersetzt (entfernt und einen eigenen angelegt und gestartet).
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("call:start_container")) is None
    new_id = lab.store.load_journal(lab.clock.t).new.id
    replacement = copy.deepcopy(lab.world.containers.pop(new_id))
    lab.world.started_at.pop(new_id)
    replacement.update(Id=_hex("compose-replacement"))
    lab.world.add_container(replacement)
    lab.world.started_at[replacement["Id"]] = lab.clock.m
    before = len(lab.world.mutations)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == before and replacement["Id"] in lab.world.containers


@pytest.mark.parametrize("point", ["after:started", "call:start_container"])
def test_old_container_started_by_hand_while_the_helper_was_down(make_case, point):
    # Der alte Container laeuft wieder (jemand hat `docker start` gemacht), der neue ist angekuendigt bzw. laeuft schon:
    # nichts anfassen, weder den einen noch den anderen stoppen oder starten.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(find("update", point)) is None
    lab.world._start(lab.old_id, {}, None)
    before = len(lab.world.mutations)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == before
    lab.world.violations.clear()  # zwei laufende Container an denselben Daten: das hat der Test selbst angerichtet


@pytest.mark.parametrize("point", ["call:rename_container", "call:create_container", "call:stop_container",
                                   "call:start_container", "after:undo"])
def test_old_container_removed_by_hand_while_the_helper_was_down(make_case, point):
    scenario = "failing" if point == "after:undo" else "update"
    case = make_case(scenario)
    lab = case.lab
    assert case.crash_at(find(scenario, point)) is None
    lab.world.containers.pop(lab.old_id)
    lab.world.started_at.pop(lab.old_id, None)
    before = len(lab.world.mutations)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == before and not case.journal_left


@pytest.mark.parametrize("point", ["call:tag_image", "call:create_container", "call:start_container"])
def test_old_container_renamed_back_by_hand_while_the_helper_was_down(make_case, point):
    # Nur im angekuendigten Schritt `renamed` darf der alte Container noch seinen Namen haben; danach heisst ein
    # zurueckbenannter alter Container: jemand hat selbst eingegriffen.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(find("update", point, 2 if point == "call:tag_image" else 1)) is None
    for kind, created in lab.world.mutations[case.mutations_before:]:
        if kind == "create":  # auch der neue Container wurde von Hand umbenannt (Namen sind eindeutig)
            lab.world.containers[created]["Name"] = "/von-hand"
    lab.world.containers[lab.old_id]["Name"] = "/" + lab.name
    before = len(lab.world.mutations)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == before


def test_undo_announced_before_any_change_keeps_its_reason(make_case):
    # Die Pruefung vor dem ersten aendernden Schritt scheitert an der Engine: der Rueckbau kuendigt sich noch in
    # `pulled` an (es gibt nichts zurueckzunehmen). Stuerzt der Helfer genau dann ab, bleibt der Grund im Ergebnis.
    case = make_case("update")
    lab = case.lab

    def listing(request):
        journal = lab.state_dir / JOURNAL_NAME
        raw = json.loads(journal.read_text()) if journal.exists() else None
        if raw is not None and raw["step"] == "pulled" and not raw["undo"]:
            return Response(status=500, body={"message": "daemon busy"})
        return lab.world.answer(request)

    lab.fake.route("GET", "/containers/json", listing)
    lab.restart_helper(Points(crash_on="after:undo"))
    with pytest.raises(Crash):
        case.submit(case.request)
    assert json.loads((lab.state_dir / JOURNAL_NAME).read_text())["step"] == "pulled"
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("aborted", policy.ENGINE_UNREACHABLE)
    assert lab.only_deck()["Id"] == lab.old_id and lab.mutations("rename") == []


def test_a_stop_still_under_way_when_the_helper_comes_back_is_waited_for(make_case):
    # Die Antwort auf den Stopp des alten Containers geht verloren, der Helfer stuerzt gleich danach ab; der
    # Docker-Dienst stoppt den Container aber weiter. Die Wiederaufnahme sieht ihn noch laufen -- und wartet, bis der
    # Stopp durch sein kann, statt ihn fuer gesund zu halten. Danach startet sie ihn selbst (von Hand gestoppt startet
    # ihn `unless-stopped` nicht wieder).
    case = make_case("update")
    lab = case.lab

    def stop(request):
        lab.world.end_later(lab.old_id, after=20)
        return Response(raw=b"")

    lab.fake.route("POST", rf"/containers/{lab.old_id}/stop", stop)
    points = Points(crash_on="before:undo")
    lab.restart_helper(points)
    with pytest.raises(Crash):
        case.submit(case.request)
    case.seen = points.seen
    assert lab.store.load_journal(lab.clock.t).step == "old_stopped"
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("aborted", None)
    lab.clock.t += 120
    assert ("start", lab.old_id) in lab.mutations()
    assert_settled(case, outcome, ("aborted", None), was_counted=True)


def test_a_half_undo_without_its_announcement_counts_as_external(make_case):
    # Gegenprobe zur Ankuendigung des Rueckbaus: Ohne `undo` im Journal sieht ein halber Rueckbau (der neue Container
    # ist schon weg) aus wie eine Aenderung von aussen, und die Wiederaufnahme fasst nichts an. Mit der Ankuendigung
    # baut sie weiter zurueck (siehe Matrix).
    case = make_case("failing")
    lab = case.lab
    assert case.crash_at(find("failing", "call:remove_container")) is None
    raw = json.loads((lab.state_dir / JOURNAL_NAME).read_text())
    assert raw["undo"] is True and raw["code"] == policy.EXITED
    raw.update(undo=False, code=None)
    (lab.state_dir / JOURNAL_NAME).write_text(json.dumps(raw))
    before = len(lab.world.mutations)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == before


# ---------------------------------------------------------------------------
# Grenzen, Neustart, Frist
# ---------------------------------------------------------------------------


def test_recovery_is_never_held_back_by_the_limits(make_case):
    # Die Wiederaufnahme prueft keine Grenze: weder die Aktion von eben noch eine gesperrte Version noch die 24-h-Sperre
    # nach einem kaputten Zustand halten sie auf.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("call:stop_container")) is None
    state = lab.state()
    state.actions.append(int(lab.clock.t))
    state.blocked["0.7.0"] = int(lab.clock.t) + 3600
    state.hold_until = int(lab.clock.t) + 3600
    lab.store.save_state(state)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("aborted", None)
    deck = lab.only_deck()
    assert deck["Id"] == lab.old_id and judge(deck, 0) == HEALTHY


def test_a_restart_resets_nothing(make_case):
    # Nach dem Absturz (Aktion gezaehlt, ID gemerkt) und dem Neustart: eine neue Anforderung ist durch die Grenze
    # gesperrt, dieselbe ist eine Wiederholung -- vor und nach der Wiederaufnahme.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("after:renamed")) is None
    lab.restart_helper()

    def refused(request) -> str:
        with pytest.raises(Refusal) as info:
            lab.store.check(lab.state(), request, lab.clock.t)
        return info.value.code

    assert refused(lab.request("update", "0.7.1")) == policy.RATE_LIMITED
    assert refused(case.request) == policy.REPLAY
    assert (case.revive().outcome) == "aborted"
    assert refused(lab.request("update", "0.7.1")) == policy.RATE_LIMITED
    assert refused(case.request) == policy.REPLAY
    assert len(lab.state().actions) == 1


def test_a_new_request_while_the_journal_stands_is_busy(make_case):
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("call:rename_container")) is None
    lab.restart_helper()
    lab.clock.t += policy.ACTION_INTERVAL_S
    outcome = case.submit(lab.request("update", "0.7.1"))
    assert (outcome.outcome, outcome.code) == ("refused", policy.BUSY)
    assert case.journal_left
    assert case.revive().outcome == "aborted"


def test_the_deadline_is_not_extended_by_a_restart(make_case):
    # Der neue Container wird nie gesund. Der Helfer stuerzt gleich nach dem Start ab und kommt erst 20 Minuten spaeter
    # wieder: die gespeicherte Frist ist dann vorbei, er baut sofort zurueck, statt neu 15 Minuten zu warten.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(healthy_after=None, unhealthy_from=10)
    assert case.crash_at(UPDATE_POINTS.index("call:start_container")) is None
    deadline = lab.store.load_journal(lab.clock.t).deadline
    lab.clock.t += 20 * 60
    resumed_at = lab.clock.t
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("rolled_back", policy.TIMEOUT)
    assert resumed_at > deadline
    assert lab.clock.t - resumed_at < 5 * 60  # nur der Rueckbau (der alte wird nach 30 s gesund), kein neues Warten


def test_a_slow_migration_goes_on_after_a_restart(make_case):
    # Der neue Container braucht 10 Minuten (Migration). Der Helfer kommt nach 5 Minuten wieder, beobachtet bis zur
    # gespeicherten Frist weiter und schliesst ab.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(unhealthy_from=10, healthy_after=600)
    assert case.crash_at(UPDATE_POINTS.index("call:start_container")) is None
    deadline = lab.store.load_journal(lab.clock.t).deadline
    lab.clock.t += 5 * 60
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("applied", None)
    assert lab.clock.t < deadline
    assert_settled(case, outcome, ("applied", None), was_counted=True)


def test_a_clock_set_back_before_the_restart_does_not_extend_the_deadline(make_case):
    # Die Frist im Journal stammt von einer vorgehenden Uhr, die danach um Stunden zurueckgestellt wurde. Die
    # Wiederaufnahme beobachtet hoechstens noch eine ganze Frist, nicht bis die Wanduhr die alte Frist wieder erreicht.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(healthy_after=None, unhealthy_from=10)
    assert case.crash_at(UPDATE_POINTS.index("call:start_container")) is None
    lab.clock.jump(-6 * 3600)
    resumed = lab.clock.m
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("rolled_back", policy.TIMEOUT)
    assert lab.clock.m - resumed < policy.DEADLINE_S + 5 * 60
    lab.assert_back_to_old()  # (`assert_settled` rechnet mit der Wanduhr, die hier sechs Stunden zurueckliegt)


def _power_cut_while_watched(case: Case, *, off: float, saved_before: float | None = None, ran: float = 0.0,
                             helper_after: float | None = None, ntp_after: float = 60.0) -> float:
    """Strom weg, waehrend das Journal auf `started` steht und der neue Container seit `ran` Sekunden laeuft; der
    Rechner ist `off` Sekunden spaeter wieder da (`Lab.boot`). Der Docker-Dienst startet den neuen Container neu (seine
    Restart-Policy) und mit ihm den Helfer, der alte bleibt aus (`no`). Gibt die Wanduhr beim Neustart des Containers
    zurueck.

    `saved_before`: Ohne Echtzeituhr (Raspberry Pi) startet der Rechner mit der zuletzt gespeicherten Zeit, hier so
    viele Sekunden vor dem Stromausfall. `ntp_after` Sekunden nach dem Neustart des Containers stellt NTP die Uhr
    richtig -- noch vor der Wiederaufnahme. `helper_after`: Der Docker-Dienst startet den Helfer erst so viele Sekunden
    nach dem neuen Container (davor oder danach stellt NTP die Uhr, je nach `ntp_after`)."""
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("call:start_container")) is None
    lab.clock.t += ran
    lab.clock.t += off
    wrong_by = 0.0 if saved_before is None else -(off + saved_before)
    lab.clock.jump(wrong_by)
    restarted = lab.clock.t
    lab.boot()
    lab.world.restart_daemon()
    events = [(ntp_after, "ntp")] if saved_before is not None else []
    if helper_after is not None:
        events.append((helper_after, "helper"))
    passed = 0.0
    for at, what in sorted(events):
        lab.clock.t += at - passed
        passed = at
        if what == "ntp":
            lab.clock.jump(-wrong_by)
        else:
            lab.world.restart_container(lab.helper["Id"])
    return restarted


def test_a_migration_that_starts_again_after_a_power_cut_gets_its_time(make_case):
    # Strom weg um 03:07, die Frist im Journal endet um 03:20, um 03:45 ist der Rechner wieder da. Der Docker-Dienst hat
    # den neuen Container neu gestartet, seine Migration beginnt von vorn (10 Minuten). Er bekommt eine ganze Frist ab
    # diesem Neustart -- statt nach einem einzigen Blick als `timeout` zurueckgebaut zu werden.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(unhealthy_from=10, healthy_after=600)
    _power_cut_while_watched(case, off=38 * 60)
    assert lab.clock.t > lab.store.load_journal(lab.clock.t).deadline
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("applied", None)
    assert_settled(case, outcome, ("applied", None), was_counted=True)


def _clock_cases():
    for ran, saved_before in [(0, 5 * 60), (0, 2 * 3600), (90, 30), (90, 60)]:
        for known in ("all", "no_uptime", "no_helper_start", "journal_only"):
            if ran and known == "journal_only":
                continue  # ohne beide Hinweise sieht das aus wie der eigene Start (siehe `Flow._resume_left`)
            yield pytest.param(ran, saved_before, known, id=f"{ran}-{saved_before}-{known}")


@pytest.mark.parametrize(("ran", "saved_before", "known"), list(_clock_cases()))
def test_a_clock_set_right_after_the_power_cut_does_not_cut_the_migration_short(make_case, ran, saved_before, known):
    # Wie oben, aber die Uhr geht nach dem Hochfahren falsch und wird erst nach dem Neustart des Containers richtig
    # gestellt. Wie lange der Container schon laeuft, ist mit der Wanduhr nicht zu sagen. `State.StartedAt` liegt vor dem
    # Eintrag `started` (so kann es beim eigenen Start nie sein): eine ganze Frist ab der Wiederaufnahme, auch ohne die
    # Laufzeit des Rechners und den Start des Helfers. Fiel der Strom 90 s nach dem Start aus und wurde die Zeit 30 bzw.
    # 60 s davor gespeichert, liegt es kurz danach, genau wie beim eigenen Start: Dann zeigt die Laufzeit des Rechners
    # oder der Start des Helfers (der Docker-Dienst hat beide zusammen gestartet), dass er neu gestartet wurde.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(unhealthy_from=10, healthy_after=600)
    _power_cut_while_watched(case, off=38 * 60, saved_before=saved_before, ran=ran)
    if known in ("no_uptime", "journal_only"):
        lab.booted = None
    if known in ("no_helper_start", "journal_only"):
        _helper_start_unknown(lab)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("applied", None)
    assert_settled(case, outcome, ("applied", None), was_counted=True)


@pytest.mark.parametrize(("helper_after", "ntp_after"), [(90, 60), (flow.TOGETHER_S + 30, 300)],
                         ids=["helper_after_ntp", "helper_much_later"])
def test_the_uptime_shows_a_power_cut_whatever_the_clock_does(make_case, helper_after, ntp_after):
    # Fiel der Strom 90 s nach dem Start aus und wurde die Zeit 30 s davor gespeichert, startet der Docker-Dienst den
    # neuen Container mit einer Zeit kurz nach dem Eintrag `started` -- wie beim eigenen Start. Der Helfer startet aber
    # nicht mit ihm zusammen: erst nachdem NTP die Uhr gestellt hat, oder mehr als `TOGETHER_S` spaeter. Dann zeigt nur
    # die Laufzeit des Rechners, dass die Migration von vorn begann: Sie bekommt ihre Zeit, statt nach einem einzigen
    # Blick als `timeout` zurueckgebaut zu werden.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(unhealthy_from=10, healthy_after=600)
    _power_cut_while_watched(case, off=38 * 60, saved_before=30, ran=90, helper_after=helper_after,
                             ntp_after=ntp_after)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("applied", None)
    assert_settled(case, outcome, ("applied", None), was_counted=True)


def test_a_recent_boot_does_not_extend_the_deadline_of_an_update_started_after_it(make_case):
    # Der Rechner laeuft erst seit fuenf Minuten, als das Update beginnt; der Helfer stuerzt gleich nach dem Start des
    # neuen Containers ab und kommt drei Minuten spaeter wieder (nicht mehr wie ein gemeinsamer Start). Der Container laeuft seit seinem eigenen Start: Es
    # bleibt bei der Frist im Journal (die Laufzeit des Rechners gibt nie mehr als eine Frist ab dem Hochfahren).
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(healthy_after=None, unhealthy_from=10)
    lab.clock.t -= 5 * 60  # (nur die Wanduhr: das Hochfahren liegt so fuenf Minuten vor dem Update)
    lab.boot()
    lab.clock.t += 5 * 60
    assert case.crash_at(UPDATE_POINTS.index("call:start_container")) is None
    deadline = lab.store.load_journal(lab.clock.t).deadline
    lab.clock.t += flow.TOGETHER_S + 60
    lab.world.restart_container(lab.helper["Id"])
    assert lab.uptime() < policy.DEADLINE_S
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("rolled_back", policy.TIMEOUT)
    assert deadline <= lab.step_at["undo"] < deadline + 10
    lab.assert_back_to_old()


def _helper_start_unknown(lab: Lab) -> None:
    """Das Inspect des Helfers nennt keinen Start (`State.StartedAt` fehlt)."""
    helper_id = lab.helper["Id"]

    def inspect(request):
        response = lab.world.answer(request)
        body = copy.deepcopy(response.body)
        body["State"].pop("StartedAt", None)
        return Response(body=body)

    lab.fake.route("GET", rf"/containers/{helper_id}/json", inspect)


@pytest.mark.parametrize("helper", ["started_later", "started_with_it", "start_unknown"])
def test_a_container_restarted_by_docker_has_one_deadline_from_its_restart(make_case, helper):
    # Nach dem Neustart des Rechners wird der neue Container nie gesund. Startet der Helfer erst fuenf Minuten nach ihm
    # (oder nennt das Inspect seinen Start nicht), geht es zurueck nach einer ganzen Frist ab dem Neustart des neuen
    # Containers, nicht spaeter. Hat der Docker-Dienst beide zusammen gestartet, ist die Uhrzeit dieses Neustarts nicht
    # sicher (sie kann falsch gegangen sein): eine ganze Frist ab der Wiederaufnahme -- kommt die erst fuenf Minuten
    # spaeter, wartet er so laenger als noetig, aber nie zu kurz.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(healthy_after=None, unhealthy_from=10)
    restarted = _power_cut_while_watched(case, off=30 * 60)
    lab.clock.t += 5 * 60
    if helper == "started_later":
        lab.world.restart_container(lab.helper["Id"])
    elif helper == "start_unknown":
        _helper_start_unknown(lab)
    resumed = lab.clock.t
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("rolled_back", policy.TIMEOUT)
    since = resumed if helper == "started_with_it" else restarted
    assert policy.DEADLINE_S <= lab.step_at["undo"] - since < policy.DEADLINE_S + 10
    lab.assert_back_to_old()


@pytest.mark.parametrize("after", [10 * 60, 60])
def test_a_restart_of_the_helper_alone_does_not_extend_the_deadline(make_case, after):
    # Nur der Helfer startet neu (Absturz, seine Restart-Policy); der neue Container laeuft seit seinem eigenen Start
    # weiter und wird nie gesund. Startet der Helfer viel spaeter als er, bleibt es bei der Frist im Journal. Startet er
    # zufaellig kurz danach (hier nach einer Minute), sieht das aus wie ein gemeinsamer Start: Dann wartet er eine ganze
    # Frist ab jetzt -- hoechstens `TOGETHER_S` laenger als die Frist im Journal, nie kuerzer.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(healthy_after=None, unhealthy_from=10)
    assert case.crash_at(UPDATE_POINTS.index("call:start_container")) is None
    deadline = lab.store.load_journal(lab.clock.t).deadline
    lab.clock.t += after
    lab.world.restart_container(lab.helper["Id"])
    resumed = lab.clock.t
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("rolled_back", policy.TIMEOUT)
    undo = lab.step_at["undo"]
    if after > flow.TOGETHER_S:
        assert deadline <= undo < deadline + 10
    else:
        assert policy.DEADLINE_S <= undo - resumed < policy.DEADLINE_S + 10
        assert deadline < undo <= deadline + flow.TOGETHER_S + 10
    lab.assert_back_to_old()


@pytest.mark.parametrize("saved_before", [None, 5 * 60])
def test_a_clock_set_back_after_the_restart_gives_at_most_one_deadline(make_case, saved_before):
    # Die Uhr wird nach dem Neustart des Containers um Stunden zurueckgestellt: `State.StartedAt` liegt dann in der
    # Zukunft. Mehr als eine ganze Frist ab der Wiederaufnahme gibt es trotzdem nicht.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = Behavior(healthy_after=None, unhealthy_from=10)
    _power_cut_while_watched(case, off=30 * 60, saved_before=saved_before)
    lab.clock.jump(-6 * 3600)
    resumed = lab.clock.m
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("rolled_back", policy.TIMEOUT)
    assert policy.DEADLINE_S <= lab.clock.m - resumed < policy.DEADLINE_S + 5 * 60
    lab.assert_back_to_old()


# ---------------------------------------------------------------------------
# Engine und Zustand bei der Wiederaufnahme
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("point", ["after:begin", "call:pull", "after:pulled", "after:state"])
def test_before_any_change_the_recovery_needs_no_engine(make_case, point):
    # Bis `pulled` hat der Helfer nichts veraendert (hoechstens ein Image gezogen): das Journal geht weg, ohne die
    # Engine zu fragen -- auch wenn sie gerade nicht antwortet oder inzwischen ein weiterer Container des Dienstes
    # dasteht.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(find("update", point)) is None
    lab.fake.route("GET", "/_ping", Response(status=500, body={"message": "daemon starting"}))
    _compose_created_a_new_one(case, running=False)
    requests = len(lab.fake.requests)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code, outcome.finished) == ("aborted", None, True)
    assert len(lab.fake.requests) == requests and not case.journal_left


def test_engine_away_during_recovery_changes_nothing_and_tries_again(make_case):
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("call:stop_container")) is None
    lab.fake.route("GET", "/_ping", Response(status=500, body={"message": "daemon starting"}))
    before = len(lab.world.mutations)
    outcome = case.revive()
    assert (outcome.outcome, outcome.finished) == (None, False)
    assert len(lab.world.mutations) == before and case.journal_left
    lab.fake.route("GET", "/_ping", lambda request: lab.world.answer(request))
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("aborted", None)
    assert_settled(case, outcome, ("aborted", None), was_counted=True)


def test_a_corrupt_journal_is_never_acted_on(make_case):
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("call:stop_container")) is None
    (lab.state_dir / JOURNAL_NAME).write_text('{"format": 1')
    before = len(lab.world.mutations)
    lab.restart_helper()
    with pytest.raises(JournalCorrupt):
        lab.flow.recover(lab.state())
    assert len(lab.world.mutations) == before
    assert lab.state().hold_until is not None  # gesperrt, bis jemand nachsieht


@pytest.mark.parametrize("scenario", ["update", "rollback"])
def test_after_a_crash_at_the_commit_the_result_is_kept_before_the_engine_is_asked(make_case, scenario):
    # Absturz gleich nachdem `committed` im Journal steht, noch bevor das Ergebnis gespeichert ist. Beim Neustart
    # antwortet der Docker-Dienst nicht (er startet gerade neu). Entschieden ist der Vorgang trotzdem: Die
    # Wiederaufnahme traegt das Ergebnis ein, bevor sie die Engine fragt, und meldet den Schritt fuer den Status -- jede
    # Runde, bis die Engine wieder da ist. Danach raeumt sie fertig auf, das Ergebnis behaelt seinen ersten Zeitpunkt.
    case = make_case(scenario)
    lab = case.lab
    assert case.crash_at(find(scenario, "after:committed")) is None
    assert [e for e in lab.state().results if e["id"] == case.request.id] == []
    lab.fake.route("GET", "/_ping", Response(raw=b""))  # Verbindung ohne Antwort
    first = None
    for _round in range(3):
        lab.steps.clear()
        lab.clock.t += 30
        outcome = case.revive()
        assert (outcome.outcome, outcome.finished) == (None, False) and case.journal_left
        assert lab.steps == ["committed"]  # der Status kam mit dem Ergebnis
        entries = [e for e in lab.state().results if e["id"] == case.request.id]
        assert [(e["outcome"], e["code"]) for e in entries] == [FINAL[scenario]]
        first = first or entries
        assert entries == first
    lab.fake.route("GET", "/_ping", lambda request: lab.world.answer(request))
    outcome = case.revive()
    assert [e for e in lab.state().results if e["id"] == case.request.id] == first
    assert_settled(case, outcome, FINAL[scenario], was_counted=True)


def test_cleanup_after_the_commit_waits_for_the_engine(make_case):
    # Nach dem Commit laesst sich der alte Container eine Weile nicht entfernen (500): das Journal bleibt auf
    # `committed`, das Ergebnis steht schon fest; die naechste Wiederaufnahme raeumt fertig auf.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("after:committed")) is None
    lab.fake.route("DELETE", rf"/containers/{lab.old_id}", Response(status=500, body={"message": "busy"}))
    outcome = case.revive()
    assert (outcome.outcome, outcome.finished) == ("applied", False) and case.journal_left
    lab.fake.route("DELETE", rf"/containers/{lab.old_id}", lambda request: lab.world.answer(request))
    outcome = case.revive()
    assert (outcome.outcome, outcome.finished) == ("applied", True)
    assert_settled(case, outcome, ("applied", None), was_counted=True)


def _saved_commit(case: Case) -> None:
    """Slot bzw. Sperre und das Ergebnis des Vorgangs stehen in `state.json`."""
    lab = case.lab
    state = lab.state()
    journal = lab.store.load_journal(lab.clock.t)
    if case.scenario == "update":
        assert state.slot is not None and state.slot.from_version == "0.7.0"
        assert state.slot.installed_container_id == journal.new.id
    else:
        assert state.slot is None and "0.7.1" in state.blocked
    assert [(e["outcome"], e["code"]) for e in state.results if e["id"] == case.request.id] == [FINAL[case.scenario]]


@pytest.mark.parametrize("scenario", ["update", "rollback"])
def test_an_old_container_that_never_goes_does_not_keep_the_helper_busy_for_good(make_case, scenario):
    # Der alte Container laesst sich nach dem Commit nie entfernen (500, etwa "device or resource busy" von overlay2).
    # Slot bzw. Sperre und Ergebnis stehen trotzdem schon nach dem ersten Versuch in `state.json`. Nach
    # `CLEANUP_ROUNDS` Runden bleibt er liegen wie bei einer endgueltigen Ablehnung, und der Helfer ist wieder frei.
    case = make_case(scenario)
    lab = case.lab
    old_id = case.before["Id"]
    lab.fake.route("DELETE", rf"/containers/{old_id}", Response(status=500, body={"message": "device or resource busy"}))
    lab.restart_helper()
    outcome = case.submit(case.request)
    assert (outcome.outcome, outcome.finished) == (FINAL[scenario][0], False) and case.journal_left
    _saved_commit(case)
    state = lab.state()  # wie im Helfer: derselbe Zustand fuer alle Runden
    for _round in range(2, flow.CLEANUP_ROUNDS):
        outcome = lab.flow.recover(state)
        assert (outcome.outcome, outcome.finished) == (FINAL[scenario][0], False) and case.journal_left
    outcome = lab.flow.recover(state)
    assert (outcome.outcome, outcome.code, outcome.finished) == (*FINAL[scenario], True) and not case.journal_left
    assert old_id in lab.world.containers
    assert any(entry["event"] == "old_container_left" for entry in lab.logs)
    saved = lab.state()
    assert [(e["outcome"], e["code"]) for e in saved.results if e["id"] == case.request.id] == [FINAL[scenario]]
    if scenario == "rollback":
        assert lab.protect("0.7.0") is None and lab.protect("0.7.1") is None
    assert lab.flow.recover(saved) is None


@pytest.mark.parametrize("scenario", ["update", "rollback"])
def test_flow_done_is_logged_once_however_many_rounds_the_cleanup_takes(make_case, scenario):
    # Das Aufraeumen nach dem Commit braucht mehrere Runden (der alte Container laesst sich erst spaeter entfernen):
    # Jede Runde meldet nur, dass es noch offen ist; "flow_done" steht genau einmal im Log, wenn der Vorgang fertig ist.
    case = make_case(scenario)
    lab = case.lab
    old_id = case.before["Id"]
    lab.fake.route("DELETE", rf"/containers/{old_id}", Response(status=500, body={"message": "busy"}))
    lab.restart_helper()
    state = lab.state()
    outcome = lab.flow.run(case.request, state)
    for _round in range(2):
        assert (outcome.outcome, outcome.finished) == (FINAL[scenario][0], False)
        outcome = lab.flow.recover(state)
    lab.fake.route("DELETE", rf"/containers/{old_id}", lambda request: lab.world.answer(request))
    outcome = lab.flow.recover(state)
    assert (outcome.outcome, outcome.finished) == (FINAL[scenario][0], True)
    done = [entry for entry in lab.logs if entry["event"] == "flow_done" and entry["id"] == case.request.id]
    assert done == [{"event": "flow_done", "id": case.request.id, "action": case.request.action,
                     "outcome": FINAL[scenario][0], "code": None}]
    assert len([entry for entry in lab.logs if entry["event"] == "commit_cleanup_pending"]) == 3


def test_an_unreachable_engine_after_the_commit_counts_no_round(make_case):
    # Ist die Engine nur nicht erreichbar, zaehlt das nicht: Aufgeben hiesse dann nur, den alten Container liegen zu
    # lassen, obwohl er sich spaeter entfernen liesse.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("after:committed")) is None
    lab.restart_helper()
    lab.fake.route("DELETE", rf"/containers/{lab.old_id}", Response(raw=b""))  # Verbindung ohne Antwort zu
    state = lab.state()
    for _round in range(flow.CLEANUP_ROUNDS + 2):
        outcome = lab.flow.recover(state)
        assert (outcome.outcome, outcome.finished) == ("applied", False)
    lab.fake.route("DELETE", rf"/containers/{lab.old_id}", lambda request: lab.world.answer(request))
    outcome = lab.flow.recover(state)
    assert (outcome.outcome, outcome.finished) == ("applied", True)
    assert_settled(case, outcome, ("applied", None), was_counted=True)


def test_old_container_started_by_hand_after_the_commit_is_left_and_the_helper_is_not_stuck(make_case):
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("after:committed")) is None
    lab.world._start(lab.old_id, {}, None)
    outcome = case.revive()
    assert (outcome.outcome, outcome.code, outcome.finished) == ("applied", None, True)
    assert lab.old_id in lab.world.containers and not case.journal_left  # 409: liegen lassen, nicht ewig warten
    assert any(entry["event"] == "old_container_left" for entry in lab.logs)
    lab.world.violations.clear()


@pytest.mark.parametrize("scenario", ["update", "rollback"])
def test_what_is_saved_is_not_written_again(make_case, scenario):
    # Absturz nach dem Speichern, vor dem Loeschen des Journals: die Wiederaufnahme verlaengert weder den Slot noch die
    # Sperre nach dem Rueckweg, und das Ergebnis behaelt seinen Zeitpunkt.
    case = make_case(scenario)
    lab = case.lab
    assert case.crash_at(find(scenario, "before:clear")) is None
    saved = lab.state()
    assert saved.slot is not None if scenario == "update" else "0.7.1" in saved.blocked
    lab.clock.t += 3600
    outcome = case.revive()
    assert (outcome.outcome, outcome.finished) == (FINAL[scenario][0], True)
    after = lab.state()
    assert (after.slot, after.blocked, after.results) == (saved.slot, saved.blocked, saved.results)


@pytest.mark.parametrize("scenario", ["update", "rollback"])
def test_the_result_saved_right_after_the_commit_keeps_its_time(make_case, scenario):
    # Absturz gleich nachdem das Ergebnis gespeichert ist, noch vor Slot bzw. Sperre. Die Wiederaufnahme traegt es nicht
    # noch einmal ein (es behaelt seinen Zeitpunkt) und speichert Slot bzw. Sperre dazu.
    case = make_case(scenario)
    lab = case.lab
    assert case.crash_at(find(scenario, "after:state", 2)) is None
    assert case.seen[-3:] == ["after:committed", "before:state", "after:state"]
    saved = lab.state()
    first = [entry for entry in saved.results if entry["id"] == case.request.id]
    assert [(entry["outcome"], entry["code"]) for entry in first] == [FINAL[scenario]]
    assert saved.slot == case.slot_before  # Slot bzw. Sperre kommen erst danach
    lab.clock.t += 120
    outcome = case.revive()
    assert [entry for entry in lab.state().results if entry["id"] == case.request.id] == first
    assert_settled(case, outcome, FINAL[scenario], was_counted=True)


@pytest.mark.parametrize("full", [("result",), ("slot",), ("result", "slot"), ("slot", "again"),
                                  ("result", "slot", "again")])
def test_a_full_disk_while_a_slot_is_replaced(make_case, monkeypatch, full):
    # Das Update ersetzt einen vorhandenen Slot. `state.json` laesst sich nicht schreiben: gleich nach dem Commit (das
    # Ergebnis), beim neuen Slot und auch in der naechsten Runde noch einmal. Die Runden bekommen denselben Zustand
    # (wie im Helfer). Am Ende ist das Schutz-Tag des alten Slots weg, der neue Slot und das Ergebnis stehen genau
    # einmal in `state.json`.
    case = make_case("replacing")
    lab = case.lab
    real = lab.store.save_state
    order = ["accept", "count", "result", "slot", "again"]
    calls: list[str] = []

    def full_now(state):
        what = order[len(calls)] if len(calls) < len(order) else "later"
        calls.append(what)
        if what in full:
            raise OSError(28, "No space left on device")
        real(state)

    monkeypatch.setattr(lab.store, "save_state", full_now)
    state = lab.state()
    lab.store.check(state, case.request, lab.clock.t)
    state.mark_seen(case.request.id, lab.clock.t)
    lab.store.save_state(state)
    outcome = lab.flow.run(case.request, state)
    rounds = 0
    while not outcome.finished:
        rounds += 1
        assert rounds <= 3
        outcome = lab.flow.recover(state)
    assert rounds == len({"slot", "again"} & set(full))
    monkeypatch.undo()
    case.seen = ["after:pulled"]  # (fuer `assert_settled`: die Version steht im Journal)
    assert lab.state().slot.from_version == "0.7.0" and lab.protect("0.6.9") is None
    assert_settled(case, outcome, ("applied", None), was_counted=True)


@pytest.mark.parametrize("image_store", ["graphdriver", "containerd"])
def test_old_image_gone_before_the_commit_cleanup_still_leaves_a_way_back(make_case, image_store):
    # Nach dem Commit, noch bevor der Slot gespeichert ist, entfernt jemand den alten Container und raeumt mit `image
    # prune -a` auf. Der Digest des alten Images steht seit `begin` im Journal: Der Slot entsteht trotzdem, und ein
    # Rueckweg zieht das Image nach diesem Digest wieder (genau dieselbe ID und Version, sonst `previous_mismatch`) --
    # wie nach einem Commit ohne Absturz.
    case = make_case("update", image_store)
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("after:committed")) is None
    assert lab.state().slot is None
    lab.world.containers.pop(lab.old_id)
    assert lab.old_image in lab.world.prune_all()
    outcome = case.revive()
    assert (outcome.outcome, outcome.finished) == ("applied", True)
    slot = lab.state().slot
    assert (slot.from_version, slot.image_id, slot.repo_digest) == (
        "0.7.0", lab.old_image, f"{policy.REPOSITORY}@{lab.old_digest}")
    lab.clock.t += policy.ACTION_INTERVAL_S
    outcome = lab.run("rollback", "0.7.0")
    assert (outcome.outcome, outcome.code) == ("reverted", None)
    assert lab.mutations("pull")[-1] == ("pull", lab.old_digest)
    assert lab.only_deck()["Image"] == lab.old_image


def test_old_image_gone_still_replaces_the_previous_slot(make_case):
    # Wie oben, aber es gab schon einen Slot (von 0.6.9): Der neue ersetzt ihn, das Schutz-Tag des alten geht.
    case = make_case("update")
    lab = case.lab
    old_slot = policy.Slot(from_version="0.6.9", image_id="sha256:" + _hex("image-0.6.9"),
                           repo_digest=f"{policy.REPOSITORY}@sha256:{'4' * 64}", installed_container_id=lab.old_id,
                           installed_image_id=lab.old_image, until=int(lab.clock.t) + 3600)
    state = lab.state()
    state.slot = old_slot
    lab.store.save_state(state)
    assert case.crash_at(UPDATE_POINTS.index("after:committed")) is None
    assert lab.state().slot == old_slot  # noch nicht ersetzt
    lab.world.containers.pop(lab.old_id)
    assert lab.old_image in lab.world.prune_all()
    outcome = case.revive()
    assert (outcome.outcome, outcome.finished) == ("applied", True)
    assert lab.state().slot.from_version == "0.7.0" and lab.protect("0.6.9") is None


@pytest.mark.parametrize("image_store", ["graphdriver", "containerd"])
def test_docker_restarts_while_the_new_container_is_watched(make_case, image_store):
    # Der Docker-Dienst (oder der ganze Rechner) startet neu, waehrend das Journal auf `started` steht. Der alte
    # Container bleibt aus (Restart-Policy `no`), der neue kommt mit seiner uebernommenen Policy wieder; nie laufen
    # beide. Die Wiederaufnahme beobachtet weiter und schliesst ab -- mit einem Rueckweg-Slot, auch wenn die Engine den
    # Digest des alten Images inzwischen nicht mehr nennt (containerd-Store).
    case = make_case("update", image_store)
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("call:start_container")) is None
    lab.world.restart_daemon()
    assert lab.world.containers[lab.old_id]["State"]["Running"] is False
    if image_store == "containerd":
        assert policy.registry_digests(lab.world.images[lab.old_image]["RepoDigests"]) == []
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("applied", None)
    assert_settled(case, outcome, ("applied", None), was_counted=True)


@pytest.mark.parametrize(("point", "running_after"), [
    ("call:rename_container", True),  # der alte laeuft noch als `<name>-previous` mit seiner Policy: er kommt wieder
    ("call:set_restart_policy", False),  # schon auf `no`, aber noch nicht gestoppt: er bleibt aus
    ("call:stop_container", False),  # gestoppt mit `no`, der neue nur angelegt: keiner laeuft
])
def test_docker_restarts_before_the_new_container_ran(make_case, point, running_after):
    # Der Rechner startet neu, bevor der neue Container lief: Danach laeuft hoechstens der alte (nie beide, nie der
    # nur angelegte neue), und die Wiederaufnahme baut zurueck -- der alte laeuft wieder unter seinem Namen.
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(find("update", point)) is None
    lab.world.restart_daemon()
    assert [c["Id"] for c in lab.world.running() if c["Id"] != lab.helper["Id"]] == ([lab.old_id] if running_after
                                                                                     else [])
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("aborted", None)
    lab.clock.t += 60  # ein gerade wieder angelaufener alter Container braucht einen Moment bis `healthy`
    assert_settled(case, outcome, ("aborted", None), was_counted=True)


@pytest.mark.parametrize(("behavior", "code"), [
    (Behavior(healthy_after=None, restart_after=15), policy.RESTART_LOOP),
    (Behavior(healthy_after=None, rescue_after=15), policy.RESCUE_PAGE),
])
def test_the_recovery_sees_the_same_hard_failures(make_case, behavior, code):
    # Der Helfer kommt eine Minute nach dem Start des neuen Containers wieder. Der steckt inzwischen in einer
    # Neustart-Schleife bzw. zeigt die Notseite: Die Wiederaufnahme erkennt das wie der erste Lauf und baut zurueck.
    case = make_case("update")
    lab = case.lab
    lab.world.behaviors[case.newer] = behavior
    assert case.crash_at(UPDATE_POINTS.index("call:start_container")) is None
    lab.clock.t += 60
    outcome = case.revive()
    assert (outcome.outcome, outcome.code) == ("rolled_back", code)
    assert_settled(case, outcome, ("rolled_back", code), was_counted=True)


def test_results_survive_a_restart_and_are_valid_for_the_status(make_case):
    case = make_case("update")
    lab = case.lab
    assert case.crash_at(UPDATE_POINTS.index("after:clear")) is None
    assert case.revive() is None  # nichts mehr zu tun ...
    entries = lab.state().results
    assert [(e["id"], e["outcome"]) for e in entries] == [(case.request.id, "applied")]  # ... das Ergebnis steht da
    status_with(entries)
    raw = json.loads((lab.state_dir / STATE_NAME).read_text())
    assert raw["results"] == entries
    assert State.from_json(raw).results == entries
