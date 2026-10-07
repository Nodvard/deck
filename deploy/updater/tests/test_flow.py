"""Ablauf: Update und Rueckweg gegen eine durchgespielte Engine (`flow_support.LiveWorld`) mit eigener Uhr.

Jeder Test laeuft ueber den echten Client (`engine.Engine`) und die Fake-Engine. Die Fake-Engine prueft Allowlist,
Form und die Bindung jedes aendernden Aufrufs gegen das Journal, wie es gerade auf der Platte steht
(`JournalGuard.from_state_dir`) -- ein Ablauf, der einen Schritt ausfuehrt, bevor er ihn ins Journal geschrieben hat,
faellt so auf. Die Welt prueft bei jedem Aufruf, dass nie zwei Container an denselben Daten laufen und jeder Container
beim Start dieselben Daten hat wie das Ziel. Am Ende jedes Tests muessen beide Listen leer sein (`Lab.close`).
"""

from __future__ import annotations

import copy
import datetime
import json
import uuid

import pytest
from fake_engine import Response
from flow_support import LATEST, SECRET, Behavior, Lab, LiveWorld, _hex, docker_time
from nodvard_deck_updater import flow, policy, target
from nodvard_deck_updater.engine import PREVIOUS_REPOSITORY
from nodvard_deck_updater.flow import HEALTHY, judge, shows_rescue_page
from nodvard_deck_updater.policy import REPOSITORY, VERSION_LABEL
from nodvard_deck_updater.state import JOURNAL_NAME, Journal, State

RECORDED = ["docker29-api154", "docker29-api143", "docker29-api141", "docker29-api154-stack", "docker29-api154-anon"]
ALL_STEPS = list(policy.STEPS)


@pytest.fixture
def lab(tmp_path, uid):
    setup = Lab(tmp_path, uid)
    yield setup
    setup.close()
    setup.assert_clean()


def outcome_of(outcome: flow.Outcome) -> tuple:
    return outcome.outcome, outcome.code


# ---------------------------------------------------------------------------
# Gesund: applied und ein Rueckweg-Slot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", RECORDED)
def test_healthy_update_is_applied_and_leaves_a_way_back(tmp_path, uid, fixture):
    lab = Lab(tmp_path, uid, fixture)
    try:
        new_image, _digest = lab.release("0.7.1")
        outcome = lab.run()
        assert (outcome.outcome, outcome.code, outcome.from_version, outcome.to_version, outcome.finished) == (
            "applied", None, "0.7.0", "0.7.1", True)
        deck = lab.only_deck()
        assert deck["Id"] != lab.old_id and deck["Name"] == "/" + lab.name and deck["Image"] == new_image
        assert deck["Config"]["Image"] == lab.target["Config"]["Image"]  # der Tag-Text bleibt unveraendert
        assert deck["HostConfig"]["RestartPolicy"] == lab.restart
        assert LiveWorld.mount_origin(deck, "/app/data") == lab.world.data_origin  # dieselben Daten
        lab.assert_healthy(deck)
        assert lab.old_id not in lab.world.containers  # kein Doppelgaenger, den Compose zurueckstufen koennte
        assert lab.world.tagged(LATEST) == new_image
        assert lab.protect("0.7.0") == lab.old_image  # das alte Image ueberlebt `image prune`
        state = lab.state()
        assert state.slot == policy.Slot(
            from_version="0.7.0", image_id=lab.old_image, repo_digest=f"{REPOSITORY}@{lab.old_digest}",
            installed_container_id=deck["Id"], installed_image_id=new_image,
            until=int(lab.clock.t) + policy.SLOT_TTL_S)
        assert len(state.actions) == 1
        assert not lab.journal_left()
        assert lab.steps == ALL_STEPS + [None]
    finally:
        lab.close()
        lab.assert_clean()


def test_update_and_rollback_with_the_containerd_store(tmp_path, uid):
    # Docker 29 mit containerd-Store, installiert mit `docker compose pull`: Das alte Image hat nur den Namen des
    # beweglichen Tags. Nach dem Umhaengen nennt die Engine seinen Registry-Digest nicht mehr -- der Slot stimmt
    # trotzdem, und der Rueckweg geht, auch nach `image prune -a`.
    lab = Lab(tmp_path, uid, image_store="containerd")
    try:
        lab.release("0.7.1")
        assert outcome_of(lab.run()) == ("applied", None)
        assert policy.registry_digests(lab.world.images[lab.old_image]["RepoDigests"]) == []  # so sieht es die Engine
        assert lab.state().slot.repo_digest == f"{REPOSITORY}@{lab.old_digest}"
        assert lab.old_image in lab.world.prune_all()
        lab.clock.t += policy.ACTION_INTERVAL_S
        assert outcome_of(lab.run("rollback", "0.7.0")) == ("reverted", None)
        assert lab.mutations("pull")[-1] == ("pull", lab.old_digest)
        assert lab.only_deck()["Image"] == lab.old_image and lab.world.tagged(LATEST) == lab.old_image
    finally:
        lab.close()
        lab.assert_clean()


@pytest.mark.parametrize("pruned", [False, True], ids=["old_image_still_there", "after_image_prune"])
def test_a_self_built_image_with_the_containerd_store(tmp_path, uid, pruned):
    # containerd-Store: Das Dashboard laeuft mit einem selbst gebauten, nur als ghcr getaggten Image. Die Engine nennt
    # dafuer trotzdem einen Digest im Repository (aus dem Namen abgeleitet), nur kennt die Registry ihn nicht. Ohne
    # Frage an die Registry sieht das aus wie ein gezogenes Image: Die Vorpruefung ist bereit, das Update holt das
    # offizielle Image, und der Slot traegt den Digest, den nur dieser Rechner kennt. Der Rueckweg geht, solange das
    # alte Image da ist; nach `image prune -a` scheitert er am Ziehen, ohne etwas zu veraendern, und der Slot bleibt.
    lab = Lab(tmp_path, uid, image_store="containerd")
    try:
        del lab.world.registry[lab.old_digest]  # selbst gebaut: nie in der Registry gewesen
        assert lab.world.images[lab.old_image]["RepoDigests"] == [f"{REPOSITORY}@{lab.old_digest}"]
        newer, digest = lab.release("0.7.1")
        assert outcome_of(lab.run()) == ("applied", None)
        assert lab.mutations("pull") == [("pull", digest)]  # nur das offizielle Image, nach seinem Digest
        installed = lab.only_deck()
        assert installed["Image"] == newer
        slot = lab.state().slot
        assert (slot.repo_digest, slot.image_id) == (f"{REPOSITORY}@{lab.old_digest}", lab.old_image)
        lab.clock.t += policy.ACTION_INTERVAL_S
        if not pruned:
            assert outcome_of(lab.run("rollback", "0.7.0")) == ("reverted", None)
            assert lab.mutations("pull") == [("pull", digest)]  # das alte Image lag noch da: ohne Registry
            assert lab.only_deck()["Image"] == lab.old_image and lab.world.tagged(LATEST) == lab.old_image
        else:
            assert lab.old_image in lab.world.prune_all()
            before = list(lab.world.mutations)
            actions = lab.state().actions
            assert outcome_of(lab.run("rollback", "0.7.0")) == ("refused", policy.PULL_FAILED)
            assert lab.world.mutations == before  # nichts veraendert, auch kein gezogenes Image
            assert lab.fake.calls("POST", "/images/create")[-1].query["tag"] == [lab.old_digest]
            assert lab.only_deck()["Id"] == installed["Id"] and lab.world.tagged(LATEST) == newer
            assert lab.state().slot == slot and lab.state().actions == actions  # der Slot bleibt, zaehlt nicht
            assert lab.steps == ["begin", None] and not lab.journal_left()
    finally:
        lab.close()
        lab.assert_clean()


def test_the_steps_run_in_this_order(lab):
    lab.release("0.7.1")
    lab.run()
    kinds = [kind for kind, _ in lab.mutations()]
    assert kinds == ["pull", "tag", "rename", "tag", "create", "connect", "update", "stop", "start", "remove"]
    # das Primaernetz kommt mit `create`, das zweite Netz per `connect` vor dem Start
    assert lab.mutations("tag") == [("tag", f"{PREVIOUS_REPOSITORY}:0.7.0"), ("tag", LATEST)]
    assert lab.mutations("rename") == [("rename", lab.name + "-previous")]
    assert lab.mutations("update") == [("update", "no")]
    assert lab.mutations("stop") == [("stop", lab.old_id)]
    assert lab.mutations("remove") == [("remove", lab.old_id)]


def test_new_image_values_apply_and_the_user_values_stay(lab):
    lab.release("0.7.1")
    lab.run()
    env = lab.only_deck()["Config"]["Env"]
    assert "DECK_IMAGE_ONLY=0.7.1" in env  # neu aus dem neuen Image
    assert "NODVARD_DECK_JWT_SECRET=" + SECRET in env  # vom Nutzer
    assert lab.only_deck()["Config"]["Labels"][VERSION_LABEL] == "0.7.1"


def test_long_migration_is_no_reason_to_give_up(lab):
    # 600 s "unhealthy", dann gesund: kein Abbruch (die Frist sind 15 Minuten ab dem Start).
    lab.release("0.7.1", behavior=Behavior(unhealthy_from=10, healthy_after=600))
    outcome = lab.run()
    assert outcome_of(outcome) == ("applied", None)
    waited = lab.step_at["committed"] - lab.step_at["started"]
    assert 600 <= waited < policy.DEADLINE_S


def test_second_update_leaves_no_twin(lab):
    first, _ = lab.release("0.7.1")
    assert outcome_of(lab.run()) == ("applied", None)
    second, _ = lab.release("0.7.2")
    outcome = lab.run(version="0.7.2")
    assert (outcome.outcome, outcome.from_version) == ("applied", "0.7.1")
    deck = lab.only_deck()
    assert deck["Image"] == second and deck["Name"] == "/" + lab.name
    assert not any(c["Name"].endswith("-previous") for c in lab.world.containers.values())
    assert lab.protect("0.7.0") is None  # der ersetzte Slot hat sein Schutz-Tag abgegeben
    assert lab.protect("0.7.1") == first
    slot = lab.state().slot
    assert (slot.from_version, slot.image_id, slot.installed_container_id) == ("0.7.1", first, deck["Id"])


def test_a_replaced_slot_gives_up_its_protection(lab):
    old_previous = "sha256:" + _hex("image-0.6.9")
    lab.world.images[old_previous] = {**copy.deepcopy(lab.world.images[lab.old_image]), "Id": old_previous,
                                      "RepoTags": [f"{PREVIOUS_REPOSITORY}:0.6.9"], "RepoDigests": []}
    slot = policy.Slot(from_version="0.6.9", image_id=old_previous, repo_digest=f"{REPOSITORY}@sha256:{'4' * 64}",
                       installed_container_id=lab.old_id, installed_image_id=lab.old_image,
                       until=int(lab.clock.t) + 3600)
    lab.store.save_state(State(slot=slot))
    lab.release("0.7.1")
    assert outcome_of(lab.run()) == ("applied", None)
    assert lab.protect("0.6.9") is None and lab.protect("0.7.0") == lab.old_image
    assert lab.state().slot.from_version == "0.7.0"


def test_a_protect_tag_of_the_replaced_slot_that_never_goes_does_not_keep_the_helper_busy(lab):
    # Das Schutz-Tag des ersetzten Slots laesst sich nie entfernen (500). Es geht vor dem Speichern des neuen Slots
    # (danach liesse die Bindung es nicht mehr zu); nach `CLEANUP_ROUNDS` Runden bleibt es liegen, der neue Slot und
    # das Ergebnis werden gespeichert, der alte Container geht, und der Helfer ist wieder frei.
    old_previous = "sha256:" + _hex("image-0.6.9")
    lab.world.images[old_previous] = {**copy.deepcopy(lab.world.images[lab.old_image]), "Id": old_previous,
                                      "RepoTags": [f"{PREVIOUS_REPOSITORY}:0.6.9"], "RepoDigests": []}
    slot = policy.Slot(from_version="0.6.9", image_id=old_previous, repo_digest=f"{REPOSITORY}@sha256:{'4' * 64}",
                       installed_container_id=lab.old_id, installed_image_id=lab.old_image,
                       until=int(lab.clock.t) + 3600)
    lab.store.save_state(State(slot=slot))
    lab.fake.route("DELETE", rf"/images/{PREVIOUS_REPOSITORY}:0.6.9", Response(status=500, body={"message": "busy"}))
    lab.release("0.7.1")
    state = lab.state()
    outcome = lab.flow.run(lab.request(), state)
    for _round in range(2, flow.CLEANUP_ROUNDS + 1):
        assert (outcome.outcome, outcome.finished) == ("applied", False) and lab.journal_left()
        assert lab.state().slot == slot  # erst das Tag, dann der neue Slot
        assert [e["outcome"] for e in lab.state().results] == ["applied"]  # das Ergebnis steht schon vorher fest
        outcome = lab.flow.recover(state)
    assert (outcome.outcome, outcome.finished) == ("applied", True) and not lab.journal_left()
    saved = lab.state()
    assert saved.slot.from_version == "0.7.0" and [e["outcome"] for e in saved.results] == ["applied"]
    assert lab.protect("0.6.9") == old_previous and lab.protect("0.7.0") == lab.old_image
    assert lab.old_id not in lab.world.containers
    assert any(entry["event"] == "protect_tag_left" for entry in lab.logs)


def test_one_off_container_in_parallel_is_left_alone(lab):
    # `docker compose run nodvard-deck ...` (z. B. eine Sicherung) laeuft neben dem Ziel: es ist kein Ziel, und der
    # Ablauf fasst es nicht an.
    one_off = copy.deepcopy(lab.target)
    one_off["Id"] = _hex("one-off")
    one_off["Name"] = f"/{lab.name}-run-1"
    one_off["Config"]["Labels"]["com.docker.compose.oneoff"] = "True"
    lab.world.add_container(one_off)
    lab.release("0.7.1")
    assert outcome_of(lab.run()) == ("applied", None)
    assert lab.world.containers[one_off["Id"]]["Name"] == f"/{lab.name}-run-1"
    assert lab.world.containers[one_off["Id"]]["State"]["Running"] is True
    assert not [m for m in lab.mutations() if m[1] == one_off["Id"]]


# ---------------------------------------------------------------------------
# Vorab abgelehnt: nichts veraendert
# ---------------------------------------------------------------------------


def _duplicate(lab):
    twin = copy.deepcopy(lab.target)
    twin["Id"] = _hex("twin")
    twin["Name"] = f"/{lab.name[:-1]}2"
    for mount in twin["Mounts"]:
        if mount["Destination"] == "/app/data":
            mount.update(Type="volume", Name=_hex("twin-data"))  # jede Kopie mit eigenen anonymen Daten
    lab.world.add_container(twin)


@pytest.mark.parametrize(("change", "code"), [
    (_duplicate, policy.MULTIPLE_TARGETS),  # --scale 2
    (lambda lab: lab.target["Config"]["Labels"].update({"com.docker.swarm.service.name": "deck"}), policy.SWARM),
    (lambda lab: lab.target["State"].update(Running=False, Status="exited"), policy.TARGET_NOT_RUNNING),
    (lambda lab: lab.target["State"]["Health"].update(Status="unhealthy"), policy.TARGET_UNHEALTHY),
    (lambda lab: lab.target["Config"].update(Image=REPOSITORY + ":0.7.0"), policy.PINNED_VERSION),
])
def test_unsuitable_targets_are_refused_without_any_change(lab, change, code):
    change(lab)
    lab.release("0.7.1")
    outcome = lab.run()
    assert outcome_of(outcome) == ("refused", code)
    assert lab.fake.methods <= {"GET"} and lab.world.mutations == []
    assert lab.steps == [] and lab.state().actions == []


@pytest.mark.parametrize(("version", "code"), [
    ("0.7.0", policy.NOT_NEWER),
    ("0.6.9", policy.NOT_NEWER),
])
def test_requests_that_do_not_go_forward(lab, version, code):
    lab.release("0.7.1")
    assert outcome_of(lab.run(version=version)) == ("refused", code)
    assert lab.fake.methods <= {"GET"}


def test_minor_tag_only_takes_its_own_versions(lab):
    lab.target["Config"]["Image"] = REPOSITORY + ":0.7"
    lab.release("0.8.0", tag="0.7")
    assert outcome_of(lab.run(version="0.8.0")) == ("refused", policy.TAG_NOT_ON_VERSION)
    assert lab.fake.methods <= {"GET"}


def _point_latest_at_the_old_image(lab):
    lab.world.tags["latest"] = lab.old_digest


def _registry_names(digest):
    def prepare(lab):
        lab.release("0.7.1")
        lab.fake.route("GET", r"/distribution/.+/json", Response(body={
            "Descriptor": {"mediaType": "application/vnd.oci.image.index.v1+json", "digest": digest, "size": 856}}))
    return prepare


def _pulled_image_shows(**changes):
    """Das Inspect des gerade gezogenen Images (`<repository>@<Digest>`) weicht in `changes` ab."""
    def prepare(lab):
        lab.release("0.7.1")

        def inspect(request):
            answer = lab.world.answer(request)
            return Response(status=answer.status, body={**answer.body, **changes})

        lab.fake.route("GET", r"/images/.+@sha256:[0-9a-f]{64}/json", inspect)
    return prepare


@pytest.mark.parametrize(("prepare", "code"), [
    (lambda lab: lab.release("0.7.1", label="0.7.2"), policy.TAG_NOT_ON_VERSION),  # das Tag zeigt auf etwas anderes
    (lambda lab: lab.release("0.7.1", label=""), policy.TAG_NOT_ON_VERSION),  # ohne Versions-Label
    (_point_latest_at_the_old_image, policy.TAG_NOT_ON_VERSION),  # umgehaengt auf die alte Version
    (lambda lab: lab.release("0.7.1", Architecture="arm64"), policy.PLATFORM_MISMATCH),
    (lambda lab: lab.release("0.7.1", tag=None), policy.PULL_FAILED),  # die Registry kennt das Tag nicht
    (lambda lab: lab.release("0.7.1", Config=None), policy.IMAGE_CONFIG_MISSING),
    # Die Registry nennt keinen gueltigen Digest; das gezogene Image nennt den aufgeloesten Digest nicht; es hat keine
    # gueltige Image-ID.
    pytest.param(_registry_names("sha256:" + "z" * 64), policy.PULL_FAILED, id="resolved_digest_invalid"),
    pytest.param(_registry_names(None), policy.PULL_FAILED, id="resolved_digest_missing"),
    pytest.param(_pulled_image_shows(RepoDigests=[f"{REPOSITORY}@sha256:{'5' * 64}"]), policy.PULL_FAILED,
                 id="repo_digest_other"),
    pytest.param(_pulled_image_shows(RepoDigests=[]), policy.PULL_FAILED, id="repo_digest_missing"),
    pytest.param(_pulled_image_shows(Id="sha256:kaputt"), policy.PULL_FAILED, id="image_id_invalid"),
])
def test_wrong_pulled_image_changes_nothing(lab, prepare, code):
    prepare(lab)
    outcome = lab.run()
    assert outcome_of(outcome) == ("refused", code)
    assert {kind for kind, _ in lab.mutations()} <= {"pull"}  # hoechstens ein gezogenes Image
    assert lab.only_deck()["Id"] == lab.old_id and lab.world.tagged(LATEST) == lab.old_image
    assert lab.steps[-1] is None and not lab.journal_left()
    assert lab.state().actions == []  # abgelehnt: zaehlt nicht fuer die Grenze


@pytest.mark.parametrize("response", [
    Response(chunks=[b'{"status": "Pulling"}\r\n', b'{"errorDetail": {"message": "denied"}, "error": "denied"}\r\n']),
    Response(status=500, body={"message": "Get https://ghcr.io/v2/: dial tcp: i/o timeout"}),
])
def test_pull_error_changes_nothing(lab, response):
    lab.release("0.7.1")
    lab.fake.route("POST", "/images/create", response)
    outcome = lab.run()
    assert outcome_of(outcome) == ("refused", policy.PULL_FAILED)
    assert lab.world.mutations == []
    assert [r.method for r in lab.fake.requests if r.method != "GET"] == ["POST"]  # nur der Pull
    assert lab.steps == ["begin", None] and lab.state().actions == []


def test_a_running_journal_refuses(lab):
    lab.release("0.7.1")
    outcome = lab.run()
    assert outcome.finished
    journal = Journal.from_json({
        "format": 1, "request_id": str(uuid.uuid4()), "action": "update", "step": "begin",
        "started_at": int(lab.clock.t), "deadline": int(lab.clock.t) + 900,
        "old": {"id": lab.only_deck()["Id"], "name": lab.name, "image_id": lab.only_deck()["Image"],
                "version": "0.7.1", "restart_policy": lab.restart, "tag_text": LATEST,
                "repo_digest": f"{REPOSITORY}@{lab.world.tags['latest']}"},
        "new": None, "undo": False, "code": None})
    lab.store.write_journal(journal)
    before = len(lab.world.mutations)
    lab.release("0.7.2")
    assert outcome_of(lab.run(version="0.7.2")) == ("refused", policy.BUSY)
    assert len(lab.world.mutations) == before


# ---------------------------------------------------------------------------
# Rueckbau vor dem Commit
# ---------------------------------------------------------------------------


def test_unhealthy_until_the_deadline_rolls_back(lab):
    lab.release("0.7.1", behavior=Behavior(healthy_after=None, unhealthy_from=40))
    outcome = lab.run()
    assert outcome_of(outcome) == ("rolled_back", policy.TIMEOUT)
    assert lab.clock.t - lab.step_at["started"] >= policy.DEADLINE_S
    lab.assert_back_to_old()
    state = lab.state()
    assert state.slot is None and len(state.actions) == 1  # ein gescheitertes Update kostet trotzdem einen Neustart


@pytest.mark.parametrize(("behavior", "code"), [
    (Behavior(exit_after=20), policy.EXITED),
    (Behavior(healthy_after=None, restart_after=15), policy.RESTART_LOOP),
    (Behavior(healthy_after=None, rescue_after=15), policy.RESCUE_PAGE),  # zweimal 503: die Notseite
])
def test_hard_failures_roll_back_early(lab, behavior, code):
    lab.release("0.7.1", behavior=behavior)
    outcome = lab.run()
    assert outcome_of(outcome) == ("rolled_back", code)
    started = lab.step_at["started"]
    undo_began = next(e for e in lab.logs if e["event"] == "flow_undo")
    assert undo_began["code"] == code
    new_id = lab.mutations("start")[0][1]
    order = lab.world.mutations
    assert order.index(("remove", new_id)) < order.index(("start", lab.old_id))  # erst der neue weg, dann der alte
    assert lab.clock.t - started < policy.DEADLINE_S / 2  # frueh, nicht erst nach der Frist
    lab.assert_back_to_old()


def test_a_single_503_is_no_rescue_page():
    one = {"State": {"Running": True, "Restarting": False, "Health": {"Status": "starting", "Log": [
        {"Output": ""}, {"Output": "HTTP Error 503: Service Unavailable"}]}}, "RestartCount": 0}
    two = copy.deepcopy(one)
    two["State"]["Health"]["Log"][0]["Output"] = "urllib.error.HTTPError: HTTP Error 503: Service Unavailable"
    assert judge(one, 0) is None and judge(two, 0) == policy.RESCUE_PAGE
    assert not shows_rescue_page([{"Output": "HTTP Error 503"}])
    assert not shows_rescue_page("HTTP Error 503 HTTP Error 503")


@pytest.mark.parametrize(("state", "count", "expected"), [
    ({"Running": True, "Restarting": False, "Health": {"Status": "healthy"}}, 0, HEALTHY),
    ({"Running": True, "Restarting": False, "Health": {"Status": "unhealthy"}}, 0, None),  # unhealthy allein: warten
    ({"Running": True, "Restarting": False, "Health": {"Status": "starting"}}, 0, None),
    ({"Running": True, "Restarting": True, "Health": {"Status": "healthy"}}, 0, policy.RESTART_LOOP),
    ({"Running": True, "Restarting": False, "Health": {"Status": "healthy"}}, 1, policy.RESTART_LOOP),
    ({"Running": False, "Restarting": False, "Health": {"Status": "healthy"}}, 0, policy.EXITED),
    ({"Running": True, "Restarting": False, "Paused": True, "Health": {"Status": "healthy"}}, 0, None),
    ({"Running": True, "Restarting": False}, 0, None),  # ohne Healthcheck nie gesund
])
def test_judge(state, count, expected):
    assert judge({"State": state, "RestartCount": count}, 0) == expected


@pytest.mark.parametrize("text", [
    "2026-10-02T08:00:00Z", "2026-10-02T08:00:00.123456789Z", "2026-10-02T08:00:00.5Z", "2024-02-29T23:59:59.999Z",
    "2000-03-01T00:00:00Z", "2100-12-31T12:30:15.000000001Z", "1970-01-01T00:00:00Z", "2026-10-02T10:00:00+02:00",
    "2026-10-02T03:30:00-04:30",
])
def test_docker_time_reads_what_docker_writes(text):
    # `State.StartedAt` (RFC 3339 mit bis zu neun Nachkommastellen) ohne `datetime` im Helfer: gegen `datetime`.
    head, _, rest = text.partition(".")
    if rest:
        digits = rest.rstrip("Z")
        expected = datetime.datetime.fromisoformat(head + "+00:00").timestamp() + int(digits) / 10 ** len(digits)
    else:
        expected = datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    assert target.docker_time(text) == pytest.approx(expected, abs=1e-6)
    assert target.docker_time(docker_time(expected)) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("value", [
    None, 17, "", "0001-01-01T00:00:00Z", "2026-10-02 08:00:00Z", "2026-10-02T08:00:00", "2026-13-02T08:00:00Z",
    "2026-10-02T24:00:00Z", "2026-10-02T08:00:00.1234567890Z", "2026-10-02T08:00:00Zjunk", "٢026-10-02T08:00:00Z",
])
def test_docker_time_refuses_anything_else(value):
    assert target.docker_time(value) is None


def test_boot_uptime_is_the_time_since_the_host_started(monkeypatch):
    # Die Laufzeit des Rechners kommt aus `CLOCK_BOOTTIME` (haengt nicht an der Wanduhr). Nennt das System sie nicht,
    # ist sie unbekannt -- dann entscheiden bei der Wiederaufnahme die uebrigen Hinweise.
    value = flow.boot_uptime()
    assert isinstance(value, float) and value > 0
    assert value <= flow.time.clock_gettime(flow.time.CLOCK_BOOTTIME)

    def broken(_clock_id):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(flow.time, "clock_gettime", broken)
    assert flow.boot_uptime() is None
    monkeypatch.delattr(flow.time, "CLOCK_BOOTTIME")
    assert flow.boot_uptime() is None


def test_create_fails_and_the_old_never_stopped(lab):
    lab.release("0.7.1")
    lab.fake.route("POST", "/containers/create", Response(status=500, body={"message": "no space left"}))
    outcome = lab.run()
    assert outcome_of(outcome) == ("aborted", policy.CREATE_FAILED)
    assert lab.mutations("stop") == [] and lab.mutations("start") == []
    lab.assert_back_to_old()


def test_tag_moved_in_between_is_caught_by_the_check(lab):
    # Zwischen dem Umhaengen des Tags und `create` haengt etwas anderes (Watchtower, `compose pull`) das Tag um:
    # der neue Container hat dann nicht das gepruefte Image (`.Image`) und wird nie gestartet.
    lab.release("0.7.1")
    other, other_digest = lab.release("0.7.2", tag=None)
    lab.world.images[other] = copy.deepcopy(lab.world.registry[other_digest])

    def sneaky(request):
        lab.world._tag(other, REPOSITORY, "latest")
        return lab.world.answer(request)

    lab.fake.route("POST", "/containers/create", sneaky)
    outcome = lab.run()
    assert outcome_of(outcome) == ("aborted", policy.CLONE_MISMATCH)
    assert lab.mutations("start") == []
    lab.assert_back_to_old()


@pytest.mark.parametrize("response", [
    Response(status=500, body={"message": "tried to kill container, but did not receive an exit event"}),
    Response(status=204),  # meldet Erfolg, der Container laeuft aber weiter
])
def test_stop_fails_and_the_old_keeps_running(lab, response):
    lab.release("0.7.1")
    lab.fake.route("POST", rf"/containers/{lab.old_id}/stop", response)
    outcome = lab.run()
    assert outcome_of(outcome) == ("aborted", policy.STOP_FAILED)
    assert lab.mutations("update") == [("update", "no"), ("update", lab.restart["Name"])]
    assert lab.mutations("start") == []
    lab.assert_back_to_old()


@pytest.mark.parametrize("answer", [
    Response(status=500, body={"message": "tried to kill container, but did not receive an exit event"}),
    Response(raw=b""),  # die Antwort geht verloren (Zeitlimit, abgerissene Verbindung)
])
def test_a_stop_that_fails_but_ends_the_old_container_later_brings_it_back(lab, answer):
    # Der Docker-Dienst antwortet auf den Stopp mit einem Fehler (der Prozess haengt nach SIGKILL noch, etwa auf einer
    # langsamen SD-Karte) oder gar nicht, stoppt den alten Container aber weiter. Kurz danach ist er aus und gilt als
    # von Hand gestoppt: `unless-stopped` startet ihn nie wieder. Der Rueckbau darf darum nicht gleich glauben, dass er
    # noch laeuft, sondern wartet die Frist des Stopps ab und startet ihn dann selbst.
    lab.release("0.7.1")

    def stop(request):
        lab.world.end_later(lab.old_id, after=20)
        return answer

    lab.fake.route("POST", rf"/containers/{lab.old_id}/stop", stop)
    outcome = lab.run()
    assert outcome_of(outcome) == ("aborted", policy.STOP_FAILED)
    lab.clock.t += 120  # was danach geschieht: der Stopp ist laengst durch
    lab.assert_back_to_old()
    assert lab.mutations("start") == [("start", lab.old_id)]  # der Rueckbau hat ihn selbst wieder gestartet


def test_start_fails_and_the_old_comes_back(lab):
    lab.release("0.7.1")
    lab.fake.route("POST", r"/containers/(?!" + lab.old_id + r")[0-9a-f]{64}/start",
                   Response(status=500, body={"message": "OCI runtime create failed"}))
    outcome = lab.run()
    assert outcome_of(outcome) == ("rolled_back", policy.START_FAILED)
    lab.assert_back_to_old()


def test_a_start_that_times_out_but_happens_later_is_undone_anyway(lab):
    # Die Antwort auf `start` bleibt aus (`start_failed`), der Docker-Dienst startet den neuen Container aber noch --
    # hier genau dann, wenn der Rueckbau ihn entfernen will. Der Rueckbau stoppt ihn trotzdem und entfernt ihn, statt
    # am 409 (laeuft) aufzugeben und den alten Container gestoppt liegen zu lassen.
    lab.release("0.7.1")
    new = {"id": None}

    def start(request):
        new["id"] = request.path.split("/")[2]
        return Response(raw=b"")

    def remove(request):
        if new["id"] is not None and lab.world.containers[new["id"]]["State"]["Running"] is not True \
                and ("start", new["id"]) not in lab.mutations():
            lab.world._start(new["id"], {}, None)  # jetzt laeuft er doch
        return lab.world.answer(request)

    lab.fake.route("POST", r"/containers/(?!" + lab.old_id + r")[0-9a-f]{64}/start", start)
    lab.fake.route("DELETE", r"/containers/[0-9a-f]{64}", remove)
    outcome = lab.run()
    assert outcome_of(outcome) == ("rolled_back", policy.START_FAILED)
    assert ("start", new["id"]) in lab.mutations() and ("stop", new["id"]) in lab.mutations()
    lab.assert_back_to_old()


def test_rename_conflict(lab):
    lab.release("0.7.1")
    lab.fake.route("POST", rf"/containers/{lab.old_id}/rename",
                   Response(status=409, body={"message": "Conflict. The name is already in use"}))
    assert outcome_of(lab.run()) == ("aborted", policy.NAME_TAKEN)
    lab.assert_back_to_old()


def test_create_timeout_with_a_created_container_is_cleaned_up(lab):
    # `create` legt den Container an, die Antwort geht aber verloren: seine ID steht nie im Journal. Der Rueckbau
    # erkennt ihn (angelegt, nie gestartet, neues Image, Compose-Labels), traegt ihn als `created` ein und entfernt ihn.
    new_image, _ = lab.release("0.7.1")

    def lost(request):
        answer = lab.world.answer(request)
        return Response(status=answer.status, body=answer.body, drop=True)

    lab.fake.route("POST", "/containers/create", lost)
    outcome = lab.run()
    assert outcome_of(outcome) == ("aborted", policy.CREATE_FAILED)
    created = lab.mutations("create")[0][1]
    assert lab.steps[lab.steps.index("creating") + 1] == "created"
    assert ("remove", created) in lab.mutations() and created not in lab.world.containers
    assert not any(c["Image"] == new_image for c in lab.world.containers.values())
    lab.assert_back_to_old()


def test_foreign_container_under_the_name_is_left_alone(lab):
    lab.release("0.7.1")
    foreign = {"Id": _hex("foreign"), "Name": "/" + lab.name, "Image": lab.old_image,
               "Config": {"Labels": {}, "Image": "busybox"}, "HostConfig": {}, "Mounts": [],
               "State": {"Status": "running", "Running": True}}

    def taken(request):
        lab.world.add_container(foreign)
        return Response(status=409, body={"message": "Conflict"})

    lab.fake.route("POST", "/containers/create", taken)
    outcome = lab.run()
    assert outcome_of(outcome) == ("external_change", policy.EXTERNAL_CHANGE)
    assert lab.mutations("remove") == [] and not lab.journal_left()
    assert foreign["Id"] in lab.world.containers


def test_undo_is_announced_before_anything_goes_back(lab):
    # Write-ahead auch fuer den Rueckbau: bevor der erste Teil zurueckgeht, steht im Journal `undo` mit dem Grund. Ab
    # dann laesst die Bindung (und die Fake-Engine) nichts mehr vorwaerts zu.
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    seen = []

    def remove(request):
        raw = json.loads((lab.state_dir / JOURNAL_NAME).read_text())
        seen.append((raw["step"], raw["undo"], raw["code"]))
        return lab.world.answer(request)

    lab.fake.route("DELETE", r"/containers/[0-9a-f]{64}", remove)
    assert outcome_of(lab.run()) == ("rolled_back", policy.EXITED)
    assert seen == [("started", True, policy.EXITED)]
    assert lab.steps[-3:] == ["started", "undo", None]


@pytest.mark.parametrize(("prepare", "want"), [
    (lambda lab: lab.release("0.7.1"), ("applied", None)),
    (lambda lab: lab.release("0.7.1", behavior=Behavior(exit_after=20)), ("rolled_back", policy.EXITED)),
    (lambda lab: lab.release("0.7.1", label="0.7.2"), ("refused", policy.TAG_NOT_ON_VERSION)),
    (lambda lab: lab.target["State"]["Health"].update(Status="unhealthy"), ("refused", policy.TARGET_UNHEALTHY)),
])
def test_every_outcome_is_kept_in_the_state(lab, prepare, want):
    # Jedes Ergebnis steht in `state.json`, bevor das Journal geht: ein Absturz dazwischen verliert es nicht, und der
    # Status kann es nach einem Neustart wieder zeigen.
    prepare(lab)
    outcome = lab.run()
    assert outcome_of(outcome) == want
    entries = lab.state().results
    assert [(e["id"], e["outcome"], e["code"]) for e in entries] == [(outcome.request_id, *want)]
    assert entries == [outcome.result(entries[0]["finished_at"])]


def test_engine_errors_while_undoing_are_retried(lab):
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    failures = {"left": 2}

    def flaky(request):
        if failures["left"]:
            failures["left"] -= 1
            return Response(status=500, body={"message": "daemon busy"})
        return lab.world.answer(request)

    lab.fake.route("DELETE", r"/containers/[0-9a-f]{64}", flaky)
    outcome = lab.run()
    assert outcome_of(outcome) == ("rolled_back", policy.EXITED)
    assert failures["left"] == 0
    lab.assert_back_to_old()


def test_old_container_that_does_not_come_back_needs_a_hand(lab):
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    lab.world.behaviors[lab.old_image] = Behavior(healthy_after=None, unhealthy_from=5)
    outcome = lab.run()
    assert outcome_of(outcome) == ("failed_manual", policy.ROLLBACK_FAILED)
    deck = lab.only_deck()
    assert deck["Id"] == lab.old_id and deck["Name"] == "/" + lab.name  # so weit wie moeglich zurueckgebaut
    assert deck["HostConfig"]["RestartPolicy"] == lab.restart
    assert lab.protect("0.7.0") == lab.old_image  # das Schutz-Tag bleibt fuer den Weg von Hand
    assert not lab.journal_left()  # kein weiterer Versuch, keine Schleife
    assert len(lab.mutations("start")) == 2  # neu einmal, alt einmal


def test_old_slot_that_shares_the_version_keeps_its_tag(lab):
    # Das Ziel laeuft mit der Version, auf die der Slot zurueckfuehrt (von Hand dorthin gegangen): ein Rueckbau entfernt
    # das gemeinsame Schutz-Tag nicht, sonst verloere der Slot seinen Schutz.
    lab.world._tag(lab.old_image, PREVIOUS_REPOSITORY, "0.7.0")
    slot = policy.Slot(from_version="0.7.0", image_id=lab.old_image, repo_digest=f"{REPOSITORY}@{lab.old_digest}",
                       installed_container_id=_hex("gone"), installed_image_id="sha256:" + _hex("gone-image"),
                       until=int(lab.clock.t) + 3600)
    lab.store.save_state(State(slot=slot))
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    assert outcome_of(lab.run()) == ("rolled_back", policy.EXITED)
    assert lab.protect("0.7.0") == lab.old_image
    assert lab.state().slot == slot


# ---------------------------------------------------------------------------
# Engine-Fehler waehrend des Beobachtens
# ---------------------------------------------------------------------------


def _flaky_new_inspect(lab, *, lose_version=False, failures=3):
    left = {"n": failures}

    def handler(request):
        container_id = request.path.split("/")[2]
        if container_id != lab.old_id and container_id in lab.world.started_at and left["n"]:
            left["n"] -= 1
            if lose_version:
                lab.engine.api_version = None  # eine abgelehnte Aushandlung hat die Version verworfen
            return Response(status=500, body={"message": "daemon restarting"})
        return lab.world.answer(request)

    lab.fake.route("GET", r"/containers/[0-9a-f]{64}/json", handler)
    return left


@pytest.mark.parametrize("lose_version", [False, True])
def test_engine_errors_while_watching_do_not_end_the_update(lab, lose_version):
    lab.release("0.7.1", behavior=Behavior(healthy_after=60))
    left = _flaky_new_inspect(lab, lose_version=lose_version)
    pings = len(lab.fake.calls("GET", "/_ping"))
    outcome = lab.run()
    assert outcome_of(outcome) == ("applied", None)
    assert left["n"] == 0
    assert len(lab.fake.calls("GET", "/_ping")) > pings + 1  # nach dem Fehler selbst neu ausgehandelt


def test_external_change_while_watching_stops_everything(lab):
    # Waehrend des Beobachtens legt jemand (z. B. `docker compose up -d`) einen weiteren Container des Dienstes an.
    lab.release("0.7.1", behavior=Behavior(healthy_after=None, unhealthy_from=10))
    intruder = copy.deepcopy(lab.target)
    intruder.update(Id=_hex("intruder"), Name=f"/{lab.name[:-1]}9")
    intruder["State"] = {"Status": "created", "Running": False}
    seen = {"at": None}

    def listing(request):
        started = [cid for kind, cid in lab.mutations("start")]
        if started and lab.clock.m - lab.world.started_at.get(started[0], lab.clock.m) >= 60 and seen["at"] is None:
            lab.world.add_container(intruder)
            seen["at"] = len(lab.world.mutations)
        return lab.world.answer(request)

    lab.fake.route("GET", "/containers/json", listing)
    outcome = lab.run()
    assert outcome_of(outcome) == ("external_change", policy.EXTERNAL_CHANGE)
    assert seen["at"] is not None and len(lab.world.mutations) == seen["at"]  # danach nichts mehr veraendert
    assert not lab.journal_left()
    # Der alte Container steht mit der Restart-Policy `no`, die der Helfer gesetzt hat: das Log nennt die alte.
    hints = [entry for entry in lab.logs if entry["event"] == "old_restart_policy_left"]
    assert [(h["container"], h["policy"], h["retries"]) for h in hints] == [
        (lab.old_id[:12], lab.restart["Name"], lab.restart["MaximumRetryCount"])]
    assert lab.world.containers[lab.old_id]["HostConfig"]["RestartPolicy"]["Name"] == "no"


def _while_watching(lab, change, *, after=30):
    """Fuehrt `change()` einmal aus, sobald der neue Container `after` Sekunden laeuft (beim Lesen der Liste)."""
    seen = {"at": None}

    def listing(request):
        started = [cid for kind, cid in lab.mutations("start")]
        if started and seen["at"] is None and started[0] in lab.world.started_at \
                and lab.clock.m - lab.world.started_at[started[0]] >= after:
            change(started[0])
            seen["at"] = len(lab.world.mutations)
        return lab.world.answer(request)

    lab.fake.route("GET", "/containers/json", listing)
    return seen


def test_old_container_started_from_outside_while_watching(lab):
    # Jemand startet den alten Container von Hand (`docker start`), waehrend der neue noch hochfaehrt: der Ablauf faellt
    # nicht auf einen Rueckbau oder Commit herein, sondern fasst nichts mehr an.
    lab.release("0.7.1", behavior=Behavior(healthy_after=120))
    seen = _while_watching(lab, lambda _new: lab.world._start(lab.old_id, {}, None))
    outcome = lab.run()
    assert outcome_of(outcome) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == seen["at"]
    assert lab.world.violations and all("zwei laufende Container" in v for v in lab.world.violations)
    lab.world.violations.clear()  # das hat der Test selbst angerichtet, nicht der Ablauf


def test_new_container_removed_from_outside_while_watching(lab):
    lab.release("0.7.1", behavior=Behavior(healthy_after=120))

    def remove(new_id):
        lab.world.containers.pop(new_id)
        lab.world.started_at.pop(new_id)

    seen = _while_watching(lab, remove)
    outcome = lab.run()
    assert outcome_of(outcome) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == seen["at"]
    assert lab.clock.t - lab.step_at["started"] < policy.DEADLINE_S  # sofort, nicht erst nach der Frist


# ---------------------------------------------------------------------------
# Journal: write-ahead
# ---------------------------------------------------------------------------


def test_without_write_ahead_the_engine_side_notices(lab, monkeypatch):
    # Gegenprobe zur Pruefung in allen anderen Tests: schreibt der Ablauf den Schritt nicht, lehnt die Fake-Engine den
    # Aufruf ab (Waechter gegen das Journal auf der Platte), und der Ablauf baut zurueck.
    real = lab.store.write_journal

    def skip_renamed(journal):
        if journal.step != "renamed":
            real(journal)

    monkeypatch.setattr(lab.store, "write_journal", skip_renamed)
    lab.release("0.7.1")
    outcome = lab.run()
    assert outcome.outcome == "aborted"
    assert any("renamed" not in v and "rename" in v for v in lab.fake.violations), lab.fake.violations
    lab.fake.violations.clear()  # erwartet: genau das sollte auffallen


def test_journal_write_failure_rolls_back(lab, monkeypatch):
    real = lab.store.write_journal

    def full(journal):
        if journal.step == "renamed":
            raise OSError(28, "No space left on device")
        real(journal)

    monkeypatch.setattr(lab.store, "write_journal", full)
    lab.release("0.7.1")
    outcome = lab.run()
    assert outcome_of(outcome) == ("aborted", policy.STATE_UNSAFE)
    assert lab.mutations("rename") == []
    lab.assert_back_to_old()


def test_commit_that_cannot_be_written_changes_nothing_more(lab, monkeypatch):
    # Ohne `committed` auf der Platte wird nichts mehr veraendert: das Journal bleibt auf `started`, der neue
    # Container laeuft gesund, der alte steht -- die Wiederaufnahme entscheidet spaeter. Kein Rueckbau nach `healthy`.
    real = lab.store.write_journal

    def full(journal):
        if journal.step == "committed":
            raise OSError(28, "No space left on device")
        real(journal)

    monkeypatch.setattr(lab.store, "write_journal", full)
    lab.release("0.7.1")
    outcome = lab.run()
    assert (outcome.outcome, outcome.finished) == (None, False)
    on_disk = lab.store.load_journal(lab.clock.t)
    assert on_disk.step == "started"
    assert lab.mutations("remove") == [] and lab.old_id in lab.world.containers
    assert lab.world.containers[on_disk.new.id]["State"]["Running"] is True


@pytest.mark.parametrize("action", ["update", "rollback"])
def test_a_full_disk_at_the_commit_loses_neither_slot_nor_result(lab, monkeypatch, action):
    # Nach dem Commit laesst sich `state.json` in dieser Runde nicht schreiben (Datentraeger voll), weder gleich nach
    # `committed` noch mit dem Slot. Slot bzw. Sperre und Ergebnis stehen dann nur im geladenen Zustand. Die naechste
    # Runde bekommt denselben Zustand (wie im Helfer) und muss sie trotzdem speichern, bevor das Journal geht.
    if action == "rollback":
        _updated(lab)
        lab.clock.t += 3600
    real = lab.store.save_state
    calls = []

    def full_once(state):
        calls.append(None)
        if len(calls) in (2, 3):  # das erste Speichern zaehlt die Aktion, die naechsten zwei gehoeren zum Commit
            raise OSError(28, "No space left on device")
        real(state)

    lab.release("0.7.1")
    before = lab.state()
    monkeypatch.setattr(lab.store, "save_state", full_once)
    state = lab.state()
    request = lab.request(action, "0.7.1" if action == "update" else "0.7.0")
    outcome = lab.flow.run(request, state)
    assert (outcome.outcome, outcome.finished) == (("applied" if action == "update" else "reverted"), False)
    assert lab.journal_left() and lab.state().results == before.results  # noch nicht auf der Platte
    outcome = lab.flow.recover(state)
    assert outcome.finished and not lab.journal_left()
    saved = lab.state()
    assert [e["outcome"] for e in saved.results if e["id"] == request.id] == [outcome.outcome]
    if action == "update":
        assert saved.slot is not None and saved.slot.installed_container_id == lab.only_deck()["Id"]
    else:
        assert saved.slot is None and "0.7.1" in saved.blocked


# ---------------------------------------------------------------------------
# Rueckweg auf Wunsch
# ---------------------------------------------------------------------------


def _updated(lab):
    new_image, _ = lab.release("0.7.1")
    assert outcome_of(lab.run()) == ("applied", None)
    return new_image


def test_rollback_on_request_goes_back_once(lab):
    newer = _updated(lab)
    installed = lab.only_deck()["Id"]
    lab.clock.t += 3600
    outcome = lab.run("rollback", "0.7.0")
    assert (outcome.outcome, outcome.code, outcome.from_version, outcome.to_version) == (
        "reverted", None, "0.7.1", "0.7.0")
    deck = lab.only_deck()
    assert deck["Image"] == lab.old_image and deck["Name"] == "/" + lab.name and deck["Id"] != installed
    lab.assert_healthy(deck)
    assert installed not in lab.world.containers
    assert lab.world.tagged(LATEST) == lab.old_image
    assert lab.protect("0.7.0") is None and lab.protect("0.7.1") is None
    state = lab.state()
    assert state.slot is None  # einmal benutzbar, nie verkettet
    assert state.blocked == {"0.7.1": int(lab.clock.t) + policy.BLOCK_AFTER_ROLLBACK_S}
    assert lab.mutations("pull") == [("pull", lab.world.tags["latest"])]  # das Image lag noch da: kein Pull
    assert newer in lab.world.images


def test_rollback_pulls_a_missing_image_by_digest(lab):
    _updated(lab)
    gone = lab.world.prune_all()  # `docker image prune -a`: das Schutz-Tag hilft dagegen nicht
    assert lab.old_image in gone
    outcome = lab.run("rollback", "0.7.0")
    assert outcome_of(outcome) == ("reverted", None)
    assert lab.mutations("pull")[-1] == ("pull", lab.old_digest)
    assert lab.only_deck()["Image"] == lab.old_image


def test_rollback_refuses_an_image_that_is_not_the_one_in_the_slot(lab):
    _updated(lab)
    lab.world.prune_all()
    impostor = copy.deepcopy(lab.world.registry[lab.old_digest])
    impostor["Id"] = "sha256:" + _hex("impostor")
    lab.world.registry[lab.old_digest] = impostor
    before = lab.only_deck()["Id"]
    outcome = lab.run("rollback", "0.7.0")
    assert outcome_of(outcome) == ("refused", policy.PREVIOUS_MISMATCH)
    assert lab.only_deck()["Id"] == before and lab.state().slot is not None


@pytest.mark.parametrize(("prepare", "version", "code"), [
    (lambda lab: None, "0.7.0", policy.NO_PREVIOUS),  # kein Update vorher: kein Slot
    (_updated, "0.6.9", policy.PREVIOUS_MISMATCH),  # nur genau die Version davor
])
def test_rollback_refusals(lab, prepare, version, code):
    prepare(lab)
    mutations = len(lab.world.mutations)
    assert outcome_of(lab.run("rollback", version)) == ("refused", code)
    assert len(lab.world.mutations) == mutations


def test_rollback_after_the_window_is_refused(lab):
    _updated(lab)
    lab.clock.t += policy.SLOT_TTL_S + 1
    assert outcome_of(lab.run("rollback", "0.7.0")) == ("refused", policy.NO_PREVIOUS)


def test_failed_rollback_returns_to_the_newer_version(lab):
    newer = _updated(lab)
    installed = lab.only_deck()["Id"]
    slot = lab.state().slot
    lab.world.behaviors[lab.old_image] = Behavior(exit_after=20)  # die alte Version startet nicht mehr
    outcome = lab.run("rollback", "0.7.0")
    assert outcome_of(outcome) == ("rolled_back", policy.EXITED)
    deck = lab.only_deck()
    assert deck["Id"] == installed and deck["Image"] == newer and deck["Name"] == "/" + lab.name
    lab.assert_healthy(deck)
    assert lab.world.tagged(LATEST) == newer
    assert lab.protect("0.7.1") is None  # das Schutz-Tag dieses Rueckwegs ist weg ...
    assert lab.protect("0.7.0") == lab.old_image  # ... das des Slots bleibt
    assert lab.state().slot == slot and lab.state().blocked == {}


# ---------------------------------------------------------------------------
# Abgelaufener Slot
# ---------------------------------------------------------------------------


def test_expired_slot_gives_up_its_tag(lab):
    _updated(lab)
    state = lab.state()
    assert lab.flow.drop_expired_slot(state) is False  # noch gueltig: nichts passiert
    assert lab.protect("0.7.0") == lab.old_image
    lab.clock.t += policy.SLOT_TTL_S
    state = lab.state()
    assert lab.flow.drop_expired_slot(state) is True
    assert lab.protect("0.7.0") is None and lab.state().slot is None


# ---------------------------------------------------------------------------
# Weitere Faelle
# ---------------------------------------------------------------------------


def test_external_change_between_two_steps_stops_everything(lab):
    # Nach dem Umbenennen legt jemand einen weiteren Container des Dienstes an: vor dem naechsten Schritt faellt das
    # auf, und der Ablauf veraendert nichts mehr.
    lab.release("0.7.1")
    intruder = copy.deepcopy(lab.target)
    intruder.update(Id=_hex("intruder"), Name=f"/{lab.name[:-1]}9")
    intruder["State"] = {"Status": "created", "Running": False}
    seen = {"at": None}

    def rename(request):
        answer = lab.world.answer(request)
        lab.world.add_container(intruder)
        seen["at"] = len(lab.world.mutations)
        return answer

    lab.fake.route("POST", rf"/containers/{lab.old_id}/rename", rename)
    outcome = lab.run()
    assert outcome_of(outcome) == ("external_change", policy.EXTERNAL_CHANGE)
    assert len(lab.world.mutations) == seen["at"]
    assert lab.steps == ["begin", "pulled", "protected", "renamed", None]
    assert not any(entry["event"] == "old_restart_policy_left" for entry in lab.logs)  # noch nicht angefasst


def test_the_deadline_starts_with_the_new_container(lab):
    # Der Pull dauert auf einem kleinen Rechner lange (hier 20 Minuten). Die Frist von 15 Minuten gilt ab dem Start des
    # neuen Containers: eine Migration von 10 Minuten danach ist kein Grund zum Abbruch.
    lab.release("0.7.1", behavior=Behavior(unhealthy_from=10, healthy_after=600))

    def slow_pull(request):
        lab.clock.t += 20 * 60
        return lab.world.answer(request)

    lab.fake.route("POST", "/images/create", slow_pull)
    outcome = lab.run()
    assert outcome_of(outcome) == ("applied", None)
    assert lab.step_at["started"] - lab.step_at["begin"] >= 20 * 60


def _jump_when_the_new_container_starts(lab, seconds):
    """Die Wanduhr springt, gleich nachdem der neue Container gestartet ist (NTP stellt die Uhr eines Raspberry Pi ohne
    Echtzeituhr kurz nach dem Hochfahren)."""
    def start(request):
        answer = lab.world.answer(request)
        lab.clock.jump(seconds)
        return answer

    lab.fake.route("POST", r"/containers/(?!" + lab.old_id + r")[0-9a-f]{64}/start", start)


def test_a_clock_jump_forward_after_the_start_does_not_abort_a_healthy_update(lab):
    lab.release("0.7.1", behavior=Behavior(healthy_after=60))
    _jump_when_the_new_container_starts(lab, 3600)
    started = lab.clock.m
    outcome = lab.run()
    assert outcome_of(outcome) == ("applied", None)
    assert lab.clock.m - started < 5 * 60  # gesund nach einer Minute, nicht nach einer Frist


def test_a_clock_jump_back_after_the_start_does_not_extend_the_deadline(lab):
    lab.release("0.7.1", behavior=Behavior(healthy_after=None, unhealthy_from=10))
    _jump_when_the_new_container_starts(lab, -3600)
    started = lab.clock.m
    outcome = lab.run()
    assert outcome_of(outcome) == ("rolled_back", policy.TIMEOUT)
    assert lab.clock.m - started < policy.DEADLINE_S + 5 * 60  # eine Frist und der Rueckbau, nicht eine Stunde mehr
    lab.assert_back_to_old()


def test_a_clock_jump_while_waiting_for_the_old_container_is_no_failure(lab):
    # Der neue Container beendet sich, der Rueckbau startet den alten und wartet auf `healthy`. Springt die Wanduhr
    # genau dann um eine Stunde vor, ist das kein Grund, den Rueckweg fuer gescheitert zu erklaeren.
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    jumped = {"done": False}

    def inspect(request):
        answer = lab.world.answer(request)
        if lab.old_id in lab.world.started_at and not jumped["done"]:
            jumped["done"] = True
            lab.clock.jump(3600)
        return answer

    lab.fake.route("GET", rf"/containers/{lab.old_id}/json", inspect)
    outcome = lab.run()
    assert jumped["done"]
    assert outcome_of(outcome) == ("rolled_back", policy.EXITED)
    lab.assert_back_to_old()


def test_on_step_errors_do_not_stop_the_update(lab):
    def broken(_journal):
        raise RuntimeError("status kaputt")

    lab.flow._on_step = broken
    lab.release("0.7.1")
    assert outcome_of(lab.run()) == ("applied", None)
    assert any(e["event"] == "on_step_failed" for e in lab.logs)


def test_undo_gives_up_when_the_engine_never_answers(lab):
    # Der neue Container laesst sich bis zur Frist nicht entfernen: kein weiterer Versuch, keine Schleife, und der alte
    # Container wird nicht neben dem neuen gestartet.
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    lab.fake.route("DELETE", r"/containers/[0-9a-f]{64}", Response(status=500, body={"message": "busy"}))
    outcome = lab.run()
    assert outcome_of(outcome) == ("failed_manual", policy.ROLLBACK_FAILED)
    assert ("start", lab.old_id) not in lab.world.mutations
    assert not lab.journal_left()
    assert lab.protect("0.7.0") == lab.old_image


def _unreachable_for(lab, seconds):
    """Ein Handler, der die Verbindung ohne Antwort schliesst (Docker-Dienst haengt oder startet neu), `seconds` lang ab
    dem ersten Aufruf; danach antwortet wieder die Welt."""
    down = {"until": None, "calls": 0}

    def handler(request):
        if down["until"] is None:
            down["until"] = lab.clock.m + seconds
        if lab.clock.m < down["until"]:
            down["calls"] += 1
            return Response(raw=b"")
        return lab.world.answer(request)

    return handler, down


def _old_started_by_the_undo(lab):
    return ("start", lab.old_id) in lab.mutations()


@pytest.mark.parametrize("where", ["remove_new", "watch_old"])
def test_engine_away_longer_than_the_deadline_while_undoing_waits_instead_of_giving_up(lab, where):
    # Der Docker-Dienst ist laenger als die Frist des Rueckbaus nicht erreichbar (haengt, startet neu), der Helfer
    # laeuft weiter. Ein endgueltiges `failed_manual` loeschte das Journal: Der alte Container bliebe als
    # `<name>-previous` gestoppt und ohne Restart-Policy liegen, und niemand raeumte mehr auf. Stattdessen bleibt das
    # Journal mit dem angekuendigten Rueckbau stehen, und die Wiederaufnahme baut zurueck, sobald die Engine wieder da
    # ist.
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    handler, down = _unreachable_for(lab, policy.DEADLINE_S + 60)
    if where == "remove_new":
        lab.fake.route("DELETE", r"/containers/[0-9a-f]{64}", handler)
    else:
        lab.fake.route("GET", rf"/containers/{lab.old_id}/json",
                       lambda request: handler(request) if _old_started_by_the_undo(lab) else lab.world.answer(request))
    outcome = lab.run()
    assert (outcome.outcome, outcome.finished) == (None, False)
    assert down["calls"] > 10
    journal = lab.store.load_journal(lab.clock.t)
    assert (journal.step, journal.undo, journal.code) == ("started", True, policy.EXITED)
    assert [e for e in lab.state().results if e["id"] == outcome.request_id] == []  # noch nicht entschieden

    lab.clock.t += 120  # die Engine ist wieder da
    outcome = lab.flow.recover(lab.state())
    assert (outcome.outcome, outcome.code, outcome.finished) == ("rolled_back", policy.EXITED, True)
    lab.assert_back_to_old()
    assert [(e["outcome"], e["code"]) for e in lab.state().results] == [("rolled_back", policy.EXITED)]


def test_a_full_disk_right_after_create_waits_for_space_instead_of_giving_up(lab, monkeypatch):
    # Der Datentraeger laeuft genau nach `create` voll: Der neue Container laesst sich weder als `created` eintragen
    # noch vom Rueckbau uebernehmen. Ein endgueltiges `failed_manual` liesse den alten Container als `<name>-previous`
    # und den neuen unter `<name>` liegen, das bewegliche Tag schon auf dem neuen Image. Stattdessen bleibt das Journal
    # in `creating` stehen; ist wieder Platz, baut die Wiederaufnahme zurueck.
    real = lab.store.write_journal
    disk = {"full": False}

    def write(journal):
        if journal.step == "created" and not disk.get("freed"):
            disk["full"] = True
        if disk["full"]:
            raise OSError(28, "No space left on device")
        real(journal)

    monkeypatch.setattr(lab.store, "write_journal", write)
    lab.release("0.7.1")
    outcome = lab.run()
    assert (outcome.outcome, outcome.finished) == (None, False)
    assert lab.store.load_journal(lab.clock.t).step == "creating"
    assert lab.mutations("create") and lab.mutations("stop") == []

    disk.update(full=False, freed=True)
    outcome = lab.flow.recover(lab.state())
    assert (outcome.outcome, outcome.finished) == ("aborted", True)
    lab.assert_back_to_old()
    assert not any(c["Image"] != lab.old_image for c in lab.decks())


def test_a_lost_answer_to_the_rename_back_is_no_reason_to_give_up(lab):
    # Der Docker-Dienst benennt den alten Container im Rueckbau zurueck, die Antwort geht aber verloren (Zeitlimit auf
    # einem ueberlasteten Rechner, abgerissene Verbindung). Ein zweiter Versuch auf denselben Namen bekaeme 400 ("same
    # name as its current name"): kein Grund aufzugeben, der Name stimmt ja schon. Sonst bliebe der alte Container
    # gestoppt und mit der Restart-Policy `no` liegen, und es liefe gar kein Dashboard mehr.
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    renames = {"n": 0}

    def rename(request):
        renames["n"] += 1
        answer = lab.world.answer(request)
        if renames["n"] == 2:  # das Zurueckbenennen im Rueckbau: ausgefuehrt, aber ohne Antwort
            return Response(raw=b"")
        return answer

    lab.fake.route("POST", rf"/containers/{lab.old_id}/rename", rename)
    outcome = lab.run()
    assert outcome_of(outcome) == ("rolled_back", policy.EXITED)
    assert renames["n"] == 2  # nach dem Fehler nachgesehen: der Name stimmte schon, kein weiterer Versuch
    lab.assert_back_to_old()


def test_the_world_refuses_a_rename_to_the_current_name_like_docker(lab):
    answer = lab.world._rename(lab.old_id, {"name": lab.name}, None)
    assert answer.status == 400 and lab.mutations("rename") == []


def test_client_errors_while_undoing_are_final(lab):
    # Ein 4xx der Engine aendert sich durch Warten nicht: sofort aufgeben statt 15 Minuten zu wiederholen.
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    real = lab.world.answer
    renames = {"n": 0}

    def rename(request):
        renames["n"] += 1
        if renames["n"] == 1:
            return real(request)
        return Response(status=409, body={"message": "Conflict"})

    lab.fake.route("POST", rf"/containers/{lab.old_id}/rename", rename)
    before = lab.clock.t
    outcome = lab.run()
    assert outcome_of(outcome) == ("failed_manual", policy.ROLLBACK_FAILED)
    assert renames["n"] == 2
    assert lab.clock.t - before < policy.DEADLINE_S


def test_expired_slot_whose_tag_is_already_gone(lab):
    _updated(lab)
    lab.world._untag(f"{PREVIOUS_REPOSITORY}:0.7.0")
    lab.clock.t += policy.SLOT_TTL_S + 1
    state = lab.state()
    assert lab.flow.drop_expired_slot(state) is True and lab.state().slot is None


def test_expired_slot_stays_while_the_engine_fails(lab):
    _updated(lab)
    lab.fake.route("DELETE", r"/images/.*", Response(status=500, body={"message": "busy"}))
    lab.clock.t += policy.SLOT_TTL_S + 1
    state = lab.state()
    assert lab.flow.drop_expired_slot(state) is False
    assert lab.state().slot is not None and lab.protect("0.7.0") == lab.old_image


def test_expired_slot_whose_tag_is_refused_for_good_goes_anyway(lab):
    # Der Docker-Dienst lehnt das Entfernen des Schutz-Tags endgueltig ab (409: ein liegen gebliebener alter Container
    # benutzt das Image noch, und das Tag ist sein letzter Name). Ein weiterer Versuch aenderte daran nichts: Das Tag
    # bleibt liegen (es haelt nur ein Image fest), der Slot geht trotzdem -- statt alle 30 s ohne Ende neu zu fragen.
    _updated(lab)
    lab.fake.route("DELETE", r"/images/.*", Response(status=409, body={
        "message": "conflict: unable to remove repository reference (must force) - container is using its image"}))
    lab.clock.t += policy.SLOT_TTL_S + 1
    state = lab.state()
    assert lab.flow.drop_expired_slot(state) is True
    assert lab.state().slot is None and lab.protect("0.7.0") == lab.old_image
    assert lab.flow.drop_expired_slot(lab.state()) is False  # nichts mehr zu tun
    assert len(lab.fake.calls("DELETE", r"/images/.*")) == 1
    assert [e["event"] for e in lab.logs].count("protect_tag_left") == 1


# ---------------------------------------------------------------------------
# Die Welt prueft wirklich
# ---------------------------------------------------------------------------


def test_world_notices_two_containers_on_the_same_data(lab):
    # Gegenprobe zu "keine Verstoesse" in allen Tests: die Welt haelt einen zweiten laufenden Container an denselben
    # Daten und einen Start mit anderen Daten fest.
    twin = copy.deepcopy(lab.target)
    twin.update(Id=_hex("twin"), Name="/twin", State=created_state_for_test())
    lab.world.add_container(twin)
    lab.world._start(twin["Id"], {}, None)
    lab.world.refresh()
    lab.world._check_invariants("test")
    assert any("zwei laufende Container" in v for v in lab.world.violations)
    lab.world.violations.clear()
    lab.world._stop(twin["Id"], {}, None)
    other = copy.deepcopy(twin)
    other.update(Id=_hex("other"), Name="/other")
    for mount in other["Mounts"]:
        if mount["Destination"] == "/app/data":
            mount["Name"] = "leer"
    lab.world.add_container(other)
    lab.world._start(other["Id"], {}, None)
    assert any("anderer Quelle" in v for v in lab.world.violations)
    lab.world.violations.clear()
    lab.world._stop(other["Id"], {}, None)


def test_the_world_restarts_containers_like_docker(lab):
    # Gegenprobe zur Nachbildung eines Neustarts des Docker-Dienstes, auf die sich die Tests mit Neustart verlassen.
    def running_after_a_restart(policy_name):
        lab.world.containers[lab.old_id]["HostConfig"]["RestartPolicy"] = {"Name": policy_name,
                                                                            "MaximumRetryCount": 0}
        lab.world.restart_daemon()
        return lab.world.containers[lab.old_id]["State"]["Running"]

    assert running_after_a_restart("unless-stopped") is True
    assert running_after_a_restart("no") is False
    lab.world._start(lab.old_id, {}, None)
    lab.world.refresh()
    assert lab.world._stop(lab.old_id, {}, None).status == 204  # von Hand gestoppt
    assert running_after_a_restart("unless-stopped") is False
    assert running_after_a_restart("always") is True
    never = copy.deepcopy(lab.target)
    never.update(Id=_hex("never-started"), Name="/never", State=created_state_for_test())
    never["Config"]["Labels"] = {}
    lab.world.add_container(never)
    lab.world.restart_daemon()
    assert lab.world.containers[never["Id"]]["State"]["Running"] is False  # nie gestartet: bleibt angelegt


def test_the_containerd_world_derives_repo_digests_from_the_names(tmp_path, uid):
    # Gegenprobe zum containerd-Store: `RepoDigests` folgt den Namen. Verlaesst das bewegliche Tag das alte Image, nennt
    # die Engine dessen Digest im Repository nicht mehr, nur noch unter dem Schutz-Tag.
    lab = Lab(tmp_path, uid, image_store="containerd")
    try:
        old = lab.world.images[lab.old_image]
        assert old["RepoTags"] == [LATEST] and old["RepoDigests"] == [f"{REPOSITORY}@{lab.old_digest}"]
        lab.world._tag(lab.old_image, PREVIOUS_REPOSITORY, "0.7.0")
        newer, digest = lab.release("0.7.1")
        lab.world._pull(REPOSITORY, digest)
        lab.world._tag(newer, REPOSITORY, "latest")
        assert old["RepoDigests"] == [f"{PREVIOUS_REPOSITORY}@{lab.old_digest}"]
        assert lab.world.images[newer]["RepoDigests"] == [f"{REPOSITORY}@{digest}"]
        assert lab.world.image(f"{REPOSITORY}@{lab.old_digest}") is None
    finally:
        lab.close()
        lab.assert_clean()


def created_state_for_test() -> dict:
    return {"Status": "created", "Running": False, "Restarting": False, "StartedAt": "0001-01-01T00:00:00Z"}


def test_no_renegotiation_during_a_healthy_update(lab):
    # Die API-Version wird einmal vor dem Vorgang ausgehandelt (Vorpruefung), danach nur nach einem Engine-Fehler.
    lab.release("0.7.1", behavior=Behavior(healthy_after=300))
    lab.run()
    assert len(lab.fake.calls("GET", "/_ping")) == 1


@pytest.mark.parametrize(("stop_timeout", "t"), [(20, "30"), (None, "30"), (120, "120")])
def test_stop_waits_at_least_30_seconds(lab, stop_timeout, t):
    lab.target["Config"]["StopTimeout"] = stop_timeout
    lab.release("0.7.1")
    assert outcome_of(lab.run()) == ("applied", None)
    assert lab.fake.calls("POST", rf"/containers/{lab.old_id}/stop")[0].query["t"] == [t]


def test_a_protect_tag_that_will_not_go_does_not_spoil_the_way_back(lab):
    lab.release("0.7.1", behavior=Behavior(exit_after=20))
    lab.fake.route("DELETE", r"/images/.*", Response(status=500, body={"message": "busy"}))
    outcome = lab.run()
    assert outcome_of(outcome) == ("rolled_back", policy.EXITED)
    deck = lab.only_deck()
    assert deck["Id"] == lab.old_id and deck["Name"] == "/" + lab.name
    assert lab.protect("0.7.0") == lab.old_image  # bleibt liegen, haelt nur das Image fest
    assert any(e["event"] == "protect_tag_left" for e in lab.logs)
