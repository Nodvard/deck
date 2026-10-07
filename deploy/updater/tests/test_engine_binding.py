"""Die Bindung der aendernden Engine-Aufrufe an das Journal (`engine.Binding`, `Engine.bind`).

Geprueft wird von beiden Seiten:

* **Client:** Jede Probe -- ohne Bindung, beliebige ID, die eigene ID, beliebiger Name, beliebige Image-ID, falsche
  Version, falscher Digest, falscher Schritt -- wirft `NotBound`, bevor etwas an die Engine geht. Die erlaubten
  Aufrufe eines Updates, eines Rueckbaus, eines Rueckwegs und des Aufraeumens gehen durch.
* **Fake-Engine:** Dieselben Proben, am Client vorbei als rohe HTTP-Anfrage geschickt, haelt der Waechter der
  Fake-Engine (`JournalGuard`, unabhaengig nachgebaut) fest und beantwortet sie mit 599. Er prueft gegen das Journal,
  das gerade gilt -- ein Ablauf, der an ein noch nicht geschriebenes Journal bindet, faellt so in den Tests auf.
"""

from __future__ import annotations

import copy
import dataclasses
import itertools
import json
from urllib.parse import urlencode

import pytest
from binding_support import (
    DIGEST,
    NAME,
    NETWORK,
    NEW,
    NEW_IMAGE,
    OLD,
    OLD_IMAGE,
    OTHER,
    OTHER_DIGEST,
    OTHER_IMAGE,
    OWN,
    PREV_DIGEST,
    PREV_IMAGE,
    RESTART,
    TAG_TEXT,
    bound,
    journal_at,
    slot_for,
)
from fake_engine import FakeEngine, JournalGuard, Request, Response, World, form_problem
from nodvard_deck_updater import engine as E
from nodvard_deck_updater import policy
from nodvard_deck_updater.state import Journal, NewImage, StateStore
from updater_support import NOW

PREVIOUS = E.PREVIOUS_REPOSITORY
REPO = policy.REPOSITORY
CREATE_BODY = {"Image": TAG_TEXT, "HostConfig": {}}


def answer_everything(fake: FakeEngine) -> None:
    """Die Fake-Engine beantwortet jeden aendernden Aufruf mit Erfolg (sofern der Waechter ihn durchlaesst)."""
    fake.route("POST", "/containers/create", Response(status=201, body={"Id": NEW, "Warnings": []}))
    fake.route("POST", r"/containers/[0-9a-f]{64}/(start|stop|rename)", Response(status=204))
    fake.route("POST", r"/containers/[0-9a-f]{64}/update", Response(status=200, body={"Warnings": []}))
    fake.route("DELETE", r"/containers/[0-9a-f]{64}", Response(status=204))
    fake.route("POST", r"/networks/[0-9a-f]{64}/connect", Response(status=200, body={}))
    fake.route("POST", r"/images/.+/tag", Response(status=201))
    fake.route("DELETE", r"/images/.+", Response(status=200, body=[]))
    fake.route("POST", "/images/create", Response(chunks=[b'{"status": "ok"}\r\n']))
    fake.route("GET", r"/distribution/.+/json", Response(body={"Descriptor": {"digest": DIGEST}}))


@pytest.fixture
def fake():
    with FakeEngine(World()) as server:
        answer_everything(server)
        yield server
    assert server.violations == []


@pytest.fixture
def client(fake):
    engine = E.Engine(fake.path)
    engine.negotiate()
    return engine


def changes(fake: FakeEngine) -> list:
    return [r for r in fake.requests if r.method != "GET"]


# ---------------------------------------------------------------------------
# Die Liste der gebundenen Endpunkte
# ---------------------------------------------------------------------------


def test_every_endpoint_except_get_needs_a_binding():
    assert {name for name, (method, _) in E.ENDPOINTS.items() if method != "GET"} == E.BOUND_ENDPOINTS
    assert E.BOUND_ENDPOINTS == {
        "create_container", "start_container", "stop_container", "rename_container", "update_container",
        "remove_container", "connect_network", "tag_image", "remove_protect_tag", "pull_image",
    }
    assert issubclass(E.NotBound, E.NotAllowed)


def test_a_bound_endpoint_without_its_own_rule_is_refused(client):
    # Kommt spaeter ein aendernder Endpunkt dazu, gilt fuer ihn nichts, bis er eine eigene Regel hat (fail-closed).
    binding = client.bind(journal_at("created"), OWN)
    with pytest.raises(E.NotBound):
        client._check_binding("kill_container", binding, {"id": OLD}, {}, None)


# ---------------------------------------------------------------------------
# Ohne Bindung
# ---------------------------------------------------------------------------

METHODS = {
    "create_container": lambda c, b: c.create_container(NAME, dict(CREATE_BODY), binding=b),
    "start_container": lambda c, b: c.start_container(OLD, binding=b),
    "stop_container": lambda c, b: c.stop_container(OLD, 30, binding=b),
    "rename_container": lambda c, b: c.rename_container(OLD, NAME + "-previous", binding=b),
    "set_restart_policy": lambda c, b: c.set_restart_policy(OLD, "no", binding=b),
    "remove_container": lambda c, b: c.remove_container(NEW, binding=b),
    "connect_network": lambda c, b: c.connect_network(NETWORK, NEW, {}, binding=b),
    "tag_image": lambda c, b: c.tag_image(NEW_IMAGE, REPO, "latest", binding=b),
    "remove_protect_tag": lambda c, b: c.remove_protect_tag("0.7.0", binding=b),
    "pull": lambda c, b: c.pull(DIGEST, binding=b),
}


@pytest.mark.parametrize("method", sorted(METHODS))
@pytest.mark.parametrize("binding", [
    None, "binding", {"own_id": OWN}, OWN, object(),
    journal_at("created"),          # das Journal selbst ist keine Bindung
    journal_at("created").to_json(),
])
def test_without_a_binding_nothing_goes_out(fake, client, method, binding):
    with pytest.raises(E.NotBound):
        METHODS[method](client, binding)
    assert changes(fake) == []


@pytest.mark.parametrize("method", sorted(METHODS))
def test_the_binding_is_a_required_parameter(client, method):
    call = {
        "create_container": lambda: client.create_container(NAME, dict(CREATE_BODY)),
        "start_container": lambda: client.start_container(OLD),
        "stop_container": lambda: client.stop_container(OLD, 30),
        "rename_container": lambda: client.rename_container(OLD, NAME),
        "set_restart_policy": lambda: client.set_restart_policy(OLD, "no"),
        "remove_container": lambda: client.remove_container(NEW),
        "connect_network": lambda: client.connect_network(NETWORK, NEW, {}),
        "tag_image": lambda: client.tag_image(NEW_IMAGE, REPO, "latest"),
        "remove_protect_tag": lambda: client.remove_protect_tag("0.7.0"), "pull": lambda: client.pull(DIGEST),
    }[method]
    with pytest.raises(TypeError, match="binding"):
        call()


@pytest.mark.parametrize(("method", "pattern", "params", "query", "body"), [
    ("POST", "/containers/create", None, {"name": NAME}, CREATE_BODY),
    ("POST", "/containers/{id}/start", {"id": OLD}, None, None),
    ("POST", "/containers/{id}/stop", {"id": OLD}, {"t": 30}, None),
    ("POST", "/containers/{id}/rename", {"id": OLD}, {"name": NAME}, None),
    ("POST", "/containers/{id}/update", {"id": OLD}, None, {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}}),
    ("DELETE", "/containers/{id}", {"id": NEW}, {"v": "0", "force": "0"}, None),
    ("POST", "/networks/{id}/connect", {"id": NETWORK}, None, {"Container": NEW, "EndpointConfig": {}}),
    ("POST", "/images/{image_id}/tag", {"image_id": NEW_IMAGE}, {"repo": REPO, "tag": "latest"}, None),
    ("DELETE", "/images/{protect}:{version}", {"protect": PREVIOUS, "version": "0.7.0"}, None, None),
    ("POST", "/images/create", None, {"fromImage": REPO, "tag": DIGEST}, None),
])
def test_the_one_request_function_refuses_changes_without_a_binding(fake, client, method, pattern, params, query,
                                                                     body):
    # Auch wer an den Methoden vorbei `_request` ruft, kommt ohne Bindung nicht an die Engine.
    with pytest.raises(E.NotBound):
        client._request(method, pattern, params=params, query=query, body=body)
    assert changes(fake) == []


@pytest.mark.parametrize("options", [
    {"versioned": False},             # ohne Praefix gaelte die neueste API-Version des Docker-Dienstes
    {"api_version": (1, 24)},         # eine aeltere API mit anderen Regeln fuer start und create
    {"api_version": (1, 99)},
    {"api_version": "1.54"},
])
def test_the_api_version_of_a_change_is_always_the_negotiated_one(fake, client, options):
    binding = bound(fake, client, journal_at("started"))
    with pytest.raises(E.NotAllowed) as exc:
        client._request("POST", "/containers/{id}/start", params={"id": NEW}, binding=binding, **options)
    assert type(exc.value) is E.NotAllowed
    assert changes(fake) == []


@pytest.mark.parametrize(("method", "pattern", "options"), [
    ("GET", "/_ping", {}),                          # /_ping nur ohne Praefix (so in der Aushandlung)
    ("GET", "/version", {"api_version": (1, 40)}),  # unter der Spanne
    ("GET", "/version", {"api_version": (1, 55)}),  # ueber der Spanne
    ("GET", "/containers/json", {"versioned": False}),
    ("GET", "/containers/json", {"api_version": (1, 54)}),
])
def test_only_the_negotiation_may_choose_the_api_version(fake, client, method, pattern, options):
    with pytest.raises(E.NotAllowed):
        client._request(method, pattern, query={"all": "1"} if "json" in pattern else None, **options)
    assert len(fake.requests) == 2  # nur _ping und version aus negotiate


def test_reads_need_no_binding(fake, client):
    fake.route("GET", r"/containers/[0-9a-f]{64}/json", Response(body={"Id": OLD}))
    assert client.inspect_container(OLD) == {"Id": OLD}
    client.distribution("latest")


# ---------------------------------------------------------------------------
# Erlaubt: die Aufrufe des Ablaufs, jeder im Schritt, in dem er laeuft
# ---------------------------------------------------------------------------

ALLOWED = [
    # Update vorwaerts
    ("pull", ("begin", "update"), {"pull_digest": DIGEST}, lambda c, b: c.pull(DIGEST, binding=b)),
    ("protect", ("protected", "update"), {}, lambda c, b: c.tag_image(OLD_IMAGE, PREVIOUS, "0.7.0", binding=b)),
    ("rename", ("renamed", "update"), {}, lambda c, b: c.rename_container(OLD, NAME + "-previous", binding=b)),
    ("retag", ("tagged", "update"), {}, lambda c, b: c.tag_image(NEW_IMAGE, REPO, "latest", binding=b)),
    ("create", ("creating", "update"), {}, lambda c, b: c.create_container(NAME, dict(CREATE_BODY), binding=b)),
    ("connect", ("created", "update"), {}, lambda c, b: c.connect_network(NETWORK, NEW, {}, binding=b)),
    ("policy_no", ("old_stopped", "update"), {}, lambda c, b: c.set_restart_policy(OLD, "no", binding=b)),
    ("stop_old", ("old_stopped", "update"), {}, lambda c, b: c.stop_container(OLD, 30, binding=b)),
    ("start_new", ("started", "update"), {}, lambda c, b: c.start_container(NEW, binding=b)),
    ("remove_old", ("committed", "update"), {}, lambda c, b: c.remove_container(OLD, binding=b)),
    # Wiederaufnahme im Schritt `creating`: der gefundene eigene Container steht jetzt im Journal (`created`), dann
    # wird zurueckgebaut
    ("remove_found", ("created", "update"), {"undo": True}, lambda c, b: c.remove_container(NEW, binding=b)),
    # Rueckbau vor dem Commit
    ("undo_stop_new", ("started", "update"), {"undo": True}, lambda c, b: c.stop_container(NEW, 30, binding=b)),
    ("undo_remove_new", ("started", "update"), {"undo": True}, lambda c, b: c.remove_container(NEW, binding=b)),
    ("undo_retag", ("started", "update"), {"undo": True},
     lambda c, b: c.tag_image(OLD_IMAGE, REPO, "latest", binding=b)),
    ("undo_rename", ("started", "update"), {"undo": True}, lambda c, b: c.rename_container(OLD, NAME, binding=b)),
    ("undo_policy", ("started", "update"), {"undo": True},
     lambda c, b: c.set_restart_policy(OLD, *RESTART, binding=b)),
    ("undo_start_old", ("started", "update"), {"undo": True}, lambda c, b: c.start_container(OLD, binding=b)),
    ("undo_untag", ("started", "update"), {"undo": True}, lambda c, b: c.remove_protect_tag("0.7.0", binding=b)),
    # Rueckbau in einem fruehen Schritt: nur zurueck, was schon angekuendigt war
    ("undo_early_retag", ("renamed", "update"), {"undo": True},
     lambda c, b: c.tag_image(OLD_IMAGE, REPO, "latest", binding=b)),
    ("undo_early_rename", ("renamed", "update"), {"undo": True},
     lambda c, b: c.rename_container(OLD, NAME, binding=b)),
    ("undo_early_untag", ("protected", "update"), {"undo": True},
     lambda c, b: c.remove_protect_tag("0.7.0", binding=b)),
    ("undo_created_stop_new", ("created", "update"), {"undo": True},
     lambda c, b: c.stop_container(NEW, 30, binding=b)),
    # Commit eines Updates: das Schutz-Tag des ersetzten Slots
    ("commit_untag_slot", ("committed", "update"), {"slot": "update"},
     lambda c, b: c.remove_protect_tag("0.6.9", binding=b)),
    # Rueckweg auf Wunsch: Image aus dem Slot nachladen, zurueckhaengen, Commit raeumt beide Schutz-Tags weg
    ("rollback_pull", ("begin", "rollback"), {"pull_digest": PREV_DIGEST, "slot": "rollback"},
     lambda c, b: c.pull(PREV_DIGEST, binding=b)),
    ("rollback_protect", ("protected", "rollback"), {},
     lambda c, b: c.tag_image(OLD_IMAGE, PREVIOUS, "0.7.1", binding=b)),
    ("rollback_retag", ("tagged", "rollback"), {}, lambda c, b: c.tag_image(PREV_IMAGE, REPO, "latest", binding=b)),
    ("rollback_start", ("started", "rollback"), {}, lambda c, b: c.start_container(NEW, binding=b)),
    ("rollback_undo_start_newer", ("started", "rollback"), {"undo": True},
     lambda c, b: c.start_container(OLD, binding=b)),
    ("rollback_commit_untag_new", ("committed", "rollback"), {},
     lambda c, b: c.remove_protect_tag("0.7.1", binding=b)),
    ("rollback_commit_untag_slot", ("committed", "rollback"), {"slot": "rollback"},
     lambda c, b: c.remove_protect_tag("0.7.0", binding=b)),
    ("rollback_remove_newer", ("committed", "rollback"), {}, lambda c, b: c.remove_container(OLD, binding=b)),
    # Aufraeumen ohne Vorgang: Slot abgelaufen
    ("slot_expired", (None, None), {"slot": "update"}, lambda c, b: c.remove_protect_tag("0.6.9", binding=b)),
]


def _bind(fake, client, where, options):
    step, action = where
    options = dict(options)
    if "slot" in options:
        options["slot"] = slot_for(options["slot"])
    journal = None if step is None else journal_at(step, action)
    return bound(fake, client, journal, **options)


@pytest.mark.parametrize(("label", "where", "options", "call"), ALLOWED, ids=[row[0] for row in ALLOWED])
def test_the_calls_of_the_flow_are_allowed(fake, client, label, where, options, call):
    if label == "pull":
        client.distribution("latest")  # die Fake-Engine kennt den Digest erst nach der Aufloesung
    call(client, _bind(fake, client, where, options))
    assert len(changes(fake)) == 1


def test_every_bound_endpoint_has_an_allowed_call():
    paths = {
        "pull": "pull_image", "protect": "tag_image", "rename": "rename_container", "create": "create_container",
        "connect": "connect_network", "policy_no": "update_container", "stop_old": "stop_container",
        "start_new": "start_container", "remove_old": "remove_container", "undo_untag": "remove_protect_tag",
    }
    assert set(paths) <= {row[0] for row in ALLOWED}
    assert set(paths.values()) == E.BOUND_ENDPOINTS


# ---------------------------------------------------------------------------
# Abgelehnt im Client: die Proben
# ---------------------------------------------------------------------------

REFUSED = [
    # beliebige ID und die eigene ID
    *[(f"{name}_{who}", ("created", "update"), {}, call)
      for who, ref in (("other", OTHER), ("own", OWN))
      for name, call in (
          ("start", lambda c, b, r=ref: c.start_container(r, binding=b)),
          ("stop", lambda c, b, r=ref: c.stop_container(r, 30, binding=b)),
          ("rename", lambda c, b, r=ref: c.rename_container(r, NAME + "-previous", binding=b)),
          ("policy", lambda c, b, r=ref: c.set_restart_policy(r, "no", binding=b)),
          ("remove", lambda c, b, r=ref: c.remove_container(r, binding=b)),
          ("connect", lambda c, b, r=ref: c.connect_network(NETWORK, r, {}, binding=b)),
      )],
    ("remove_other_after_commit", ("committed", "update"), {}, lambda c, b: c.remove_container(OTHER, binding=b)),
    # beliebiger Name
    ("create_other_name", ("creating", "update"), {},
     lambda c, b: c.create_container("anderer-container", dict(CREATE_BODY), binding=b)),
    ("create_previous_name", ("creating", "update"), {},
     lambda c, b: c.create_container(NAME + "-previous", dict(CREATE_BODY), binding=b)),
    ("rename_other_name", ("renamed", "update"), {}, lambda c, b: c.rename_container(OLD, "anderer", binding=b)),
    ("rename_other_previous", ("renamed", "update"), {},
     lambda c, b: c.rename_container(OLD, "anderer-previous", binding=b)),
    ("rename_twice_previous", ("renamed", "update"), {},
     lambda c, b: c.rename_container(OLD, NAME + "-previous-previous", binding=b)),
    # beliebige Image-ID, falsches Tag, falsche Richtung
    ("retag_other_image", ("tagged", "update"), {}, lambda c, b: c.tag_image(OTHER_IMAGE, REPO, "latest", binding=b)),
    ("retag_old_image_forward", ("tagged", "update"), {},
     lambda c, b: c.tag_image(OLD_IMAGE, REPO, "latest", binding=b)),
    ("retag_new_image_in_undo", ("started", "update"), {"undo": True},
     lambda c, b: c.tag_image(NEW_IMAGE, REPO, "latest", binding=b)),
    ("retag_before_pull", ("begin", "update"), {}, lambda c, b: c.tag_image(NEW_IMAGE, REPO, "latest", binding=b)),
    ("retag_other_floating_tag", ("tagged", "update"), {},
     lambda c, b: c.tag_image(NEW_IMAGE, REPO, "0.7", binding=b)),
    ("protect_other_image", ("protected", "update"), {},
     lambda c, b: c.tag_image(OTHER_IMAGE, PREVIOUS, "0.7.0", binding=b)),
    ("protect_new_image", ("protected", "update"), {},
     lambda c, b: c.tag_image(NEW_IMAGE, PREVIOUS, "0.7.1", binding=b)),
    ("protect_other_version", ("protected", "update"), {},
     lambda c, b: c.tag_image(OLD_IMAGE, PREVIOUS, "0.6.9", binding=b)),
    ("protect_in_undo", ("started", "update"), {"undo": True},
     lambda c, b: c.tag_image(OLD_IMAGE, PREVIOUS, "0.7.0", binding=b)),
    # create: Schritt, Image-Text, Rueckbau
    *[(f"create_in_{step}", (step, "update"), {}, lambda c, b: c.create_container(NAME, dict(CREATE_BODY), binding=b))
      for step in ("begin", "pulled", "tagged", "created", "started", "committed")],
    ("create_other_tag_text", ("creating", "update"), {},
     lambda c, b: c.create_container(NAME, {"Image": REPO + ":0.7", "HostConfig": {}}, binding=b)),
    ("create_without_tag", ("creating", "update"), {},
     lambda c, b: c.create_container(NAME, {"Image": REPO, "HostConfig": {}}, binding=b)),
    ("create_in_undo", ("creating", "update"), {"undo": True},
     lambda c, b: c.create_container(NAME, dict(CREATE_BODY), binding=b)),
    # connect: nur der neue Container, erst wenn er im Journal steht
    ("connect_before_created", ("creating", "update"), {}, lambda c, b: c.connect_network(NETWORK, NEW, {}, binding=b)),
    ("connect_old", ("created", "update"), {}, lambda c, b: c.connect_network(NETWORK, OLD, {}, binding=b)),
    ("connect_in_undo", ("created", "update"), {"undo": True},
     lambda c, b: c.connect_network(NETWORK, NEW, {}, binding=b)),
    # remove: vor dem Commit nur der neue, danach nur der alte
    ("remove_old_before_commit", ("old_stopped", "update"), {}, lambda c, b: c.remove_container(OLD, binding=b)),
    ("remove_old_in_undo", ("started", "update"), {"undo": True}, lambda c, b: c.remove_container(OLD, binding=b)),
    ("remove_new_after_commit", ("committed", "update"), {}, lambda c, b: c.remove_container(NEW, binding=b)),
    ("remove_new_not_in_journal", ("creating", "update"), {}, lambda c, b: c.remove_container(NEW, binding=b)),
    # update: nur am alten Container, nur `no` oder seine alte Restart-Policy
    ("policy_new", ("created", "update"), {}, lambda c, b: c.set_restart_policy(NEW, "no", binding=b)),
    ("policy_always", ("created", "update"), {}, lambda c, b: c.set_restart_policy(OLD, "always", binding=b)),
    ("policy_other_retries", ("created", "update"), {},
     lambda c, b: c.set_restart_policy(OLD, "on-failure", 3, binding=b)),
    ("policy_no_with_retries", ("created", "update"), {}, lambda c, b: c.set_restart_policy(OLD, "no", 5, binding=b)),
    # pull: nur im Schritt begin, nur der aufgeloeste Digest
    ("pull_other_digest", ("begin", "update"), {"pull_digest": DIGEST},
     lambda c, b: c.pull(OTHER_DIGEST, binding=b)),
    ("pull_without_resolution", ("begin", "update"), {}, lambda c, b: c.pull(DIGEST, binding=b)),
    ("pull_after_begin", ("pulled", "update"), {"pull_digest": DIGEST}, lambda c, b: c.pull(DIGEST, binding=b)),
    ("pull_in_undo", ("begin", "update"), {"pull_digest": DIGEST, "undo": True},
     lambda c, b: c.pull(DIGEST, binding=b)),
    # Schutz-Tag entfernen: nur Versionen aus Journal bzw. Slot, je nach Stand
    ("untag_any_version", ("created", "update"), {}, lambda c, b: c.remove_protect_tag("0.1.0", binding=b)),
    ("untag_new_version_before_commit", ("created", "update"), {},
     lambda c, b: c.remove_protect_tag("0.7.1", binding=b)),
    ("untag_slot_before_commit", ("created", "update"), {"slot": "update"},
     lambda c, b: c.remove_protect_tag("0.6.9", binding=b)),
    ("untag_old_after_update_commit", ("committed", "update"), {"slot": "update"},
     lambda c, b: c.remove_protect_tag("0.7.0", binding=b)),
    ("untag_new_after_update_commit", ("committed", "update"), {"slot": "update"},
     lambda c, b: c.remove_protect_tag("0.7.1", binding=b)),
    ("untag_slot_without_slot", ("committed", "update"), {}, lambda c, b: c.remove_protect_tag("0.6.9", binding=b)),
    ("untag_without_journal_and_slot", (None, None), {}, lambda c, b: c.remove_protect_tag("0.6.9", binding=b)),
    ("untag_other_than_slot", (None, None), {"slot": "update"},
     lambda c, b: c.remove_protect_tag("0.7.0", binding=b)),
    ("untag_rollback_slot_before_commit", ("started", "rollback"), {"slot": "rollback", "undo": True},
     lambda c, b: c.remove_protect_tag("0.7.0", binding=b)),
    # vorwaerts nur in dem Schritt, den das Journal ankuendigt: nie zwei Dashboards zugleich
    *[(f"start_new_in_{step}", (step, "update"), {}, lambda c, b: c.start_container(NEW, binding=b))
      for step in ("created", "old_stopped", "committed")],
    ("start_new_in_undo", ("started", "update"), {"undo": True}, lambda c, b: c.start_container(NEW, binding=b)),
    ("start_old_forward", ("started", "update"), {}, lambda c, b: c.start_container(OLD, binding=b)),
    ("start_old_after_commit", ("committed", "update"), {}, lambda c, b: c.start_container(OLD, binding=b)),
    *[(f"stop_old_in_{step}", (step, "update"), {}, lambda c, b: c.stop_container(OLD, 30, binding=b))
      for step in ("tagged", "created", "started", "committed")],
    ("stop_old_in_undo", ("old_stopped", "update"), {"undo": True},
     lambda c, b: c.stop_container(OLD, 30, binding=b)),
    ("stop_new_forward", ("started", "update"), {}, lambda c, b: c.stop_container(NEW, 30, binding=b)),
    ("stop_new_after_commit", ("committed", "update"), {}, lambda c, b: c.stop_container(NEW, 30, binding=b)),
    ("rename_previous_too_early", ("protected", "update"), {},
     lambda c, b: c.rename_container(OLD, NAME + "-previous", binding=b)),
    ("rename_previous_in_undo", ("started", "update"), {"undo": True},
     lambda c, b: c.rename_container(OLD, NAME + "-previous", binding=b)),
    ("rename_back_forward", ("renamed", "update"), {}, lambda c, b: c.rename_container(OLD, NAME, binding=b)),
    ("rename_new", ("created", "update"), {"undo": True}, lambda c, b: c.rename_container(NEW, NAME, binding=b)),
    ("rename_new_previous", ("created", "update"), {},
     lambda c, b: c.rename_container(NEW, NAME + "-previous", binding=b)),
    ("rename_after_commit", ("committed", "update"), {}, lambda c, b: c.rename_container(OLD, NAME, binding=b)),
    ("policy_no_too_early", ("created", "update"), {}, lambda c, b: c.set_restart_policy(OLD, "no", binding=b)),
    ("policy_no_in_undo", ("started", "update"), {"undo": True},
     lambda c, b: c.set_restart_policy(OLD, "no", binding=b)),
    ("policy_old_forward", ("old_stopped", "update"), {},
     lambda c, b: c.set_restart_policy(OLD, *RESTART, binding=b)),
    ("policy_after_commit", ("committed", "update"), {}, lambda c, b: c.set_restart_policy(OLD, "no", binding=b)),
    ("retag_new_too_early", ("renamed", "update"), {}, lambda c, b: c.tag_image(NEW_IMAGE, REPO, "latest", binding=b)),
    ("retag_new_after_commit", ("committed", "update"), {},
     lambda c, b: c.tag_image(NEW_IMAGE, REPO, "latest", binding=b)),
    ("retag_old_after_commit", ("committed", "update"), {},
     lambda c, b: c.tag_image(OLD_IMAGE, REPO, "latest", binding=b)),
    ("protect_too_early", ("pulled", "update"), {}, lambda c, b: c.tag_image(OLD_IMAGE, PREVIOUS, "0.7.0", binding=b)),
    ("protect_after_rollback_commit", ("committed", "rollback"), {},
     lambda c, b: c.tag_image(OLD_IMAGE, PREVIOUS, "0.7.1", binding=b)),
    ("connect_later", ("old_stopped", "update"), {}, lambda c, b: c.connect_network(NETWORK, NEW, {}, binding=b)),
    ("connect_after_commit", ("committed", "update"), {},
     lambda c, b: c.connect_network(NETWORK, NEW, {}, binding=b)),
    ("remove_new_forward", ("started", "update"), {}, lambda c, b: c.remove_container(NEW, binding=b)),
    ("untag_own_forward", ("tagged", "update"), {}, lambda c, b: c.remove_protect_tag("0.7.0", binding=b)),
    # Rueckweg: nur der Digest des Slots wird nachgeladen
    ("rollback_pull_resolved_digest", ("begin", "rollback"), {"pull_digest": DIGEST, "slot": "rollback"},
     lambda c, b: c.pull(DIGEST, binding=b)),
    ("rollback_pull_without_slot", ("begin", "rollback"), {"pull_digest": PREV_DIGEST},
     lambda c, b: c.pull(PREV_DIGEST, binding=b)),
    # ohne Journal: nur das Schutz-Tag des Slots
    *[(f"{name}_without_journal", (None, None), {"slot": "update"}, call) for name, call in (
        ("start", lambda c, b: c.start_container(OLD, binding=b)),
        ("remove", lambda c, b: c.remove_container(OLD, binding=b)),
        ("retag", lambda c, b: c.tag_image(PREV_IMAGE, REPO, "latest", binding=b)),
        ("create", lambda c, b: c.create_container(NAME, dict(CREATE_BODY), binding=b)),
    )],
]


@pytest.mark.parametrize(("label", "where", "options", "call"), REFUSED, ids=[row[0] for row in REFUSED])
def test_probes_are_refused_before_anything_goes_out(fake, client, label, where, options, call):
    options = dict(options)
    if "slot" in options:
        options["slot"] = slot_for(options["slot"])
    journal = None if where[0] is None else journal_at(*where)
    binding = client.bind(journal, OWN, **options)
    with pytest.raises(E.NotBound):
        call(client, binding)
    assert changes(fake) == []


def test_untag_after_update_commit_never_removes_the_tag_of_the_new_slot(fake, client):
    # Ein veralteter Slot, dessen Vorgaenger zufaellig die alte Version ist (von Hand zurueckgestellt): sein Schutz-Tag
    # ist jetzt das des neuen Slots und bleibt.
    stale = slot_for("update", from_version="0.7.0", installed_container_id=OTHER)
    binding = client.bind(journal_at("committed"), OWN, slot=stale)
    assert binding.protect_versions == frozenset()
    with pytest.raises(E.NotBound):
        client.remove_protect_tag("0.7.0", binding=binding)


def test_protect_versions_by_state():
    engine = E.Engine("/nicht/da")
    update, rollback = slot_for("update"), slot_for("rollback")
    assert engine.bind(None, OWN).protect_versions == frozenset()
    assert engine.bind(None, OWN, slot=update).protect_versions == {"0.6.9"}
    for step in policy.STEPS[:-1]:
        # Vor dem Commit nur im Rueckbau das Schutz-Tag dieses Vorgangs, vorwaerts keins.
        assert engine.bind(journal_at(step), OWN, slot=update, undo=True).protect_versions == {"0.7.0"}, step
        assert engine.bind(journal_at(step, "rollback"), OWN, slot=rollback,
                           undo=True).protect_versions == {"0.7.1"}, step
        assert engine.bind(journal_at(step), OWN, slot=update).protect_versions == frozenset(), step
    assert engine.bind(journal_at("committed"), OWN, slot=update).protect_versions == {"0.6.9"}
    assert engine.bind(journal_at("committed"), OWN).protect_versions == frozenset()
    assert engine.bind(journal_at("committed", "rollback"), OWN).protect_versions == {"0.7.1", "0.7.0"}


def test_container_ids_by_step():
    engine = E.Engine("/nicht/da")
    assert engine.bind(None, OWN).container_ids == frozenset()
    for step in policy.STEPS:
        expected = {OLD, NEW} if policy.STEPS.index(step) >= policy.STEPS.index("created") else {OLD}
        assert engine.bind(journal_at(step), OWN).container_ids == expected, step


# ---------------------------------------------------------------------------
# Die Bindung selbst
# ---------------------------------------------------------------------------


def _journal(**changes):
    """Ein Journal ohne Pruefung beim Anlegen (wie es aus einem Fehler im Ablauf kaeme)."""
    return dataclasses.replace(journal_at("created"), **changes)


@pytest.mark.parametrize(("journal", "options"), [
    (journal_at("created"), {"own_id": "9" * 63}),
    (journal_at("created"), {"own_id": OLD.upper()}),        # Grossbuchstaben: keine ID
    (journal_at("created"), {"own_id": None}),
    (journal_at("created"), {"own_id": OLD}),             # das Journal nennt den Helfer als alten Container
    (journal_at("created"), {"own_id": NEW}),             # ... oder als neuen
    (_journal(new=NewImage(image_id=NEW_IMAGE, digest=DIGEST, version="0.7.1", id=OLD)), {}),  # neuer == alter
    (_journal(new=NewImage(image_id=NEW_IMAGE, digest=DIGEST, version="0.7.1", id=None)), {}),  # ungueltig
    (_journal(step="pulled", new=None), {}),
    (_journal(old=dataclasses.replace(journal_at("created").old, id="kurz")), {}),
    (_journal(old=dataclasses.replace(journal_at("created").old, tag_text="docker.io/fremd:latest")), {}),
    (journal_at("created").to_json(), {}),
    ("journal", {}),
    (journal_at("committed"), {"undo": True}),           # nach dem Commit gibt es keinen Rueckbau
    (journal_at("started", undo=True), {}),               # Rueckbau angekuendigt: vorwaerts geht nichts mehr
    (journal_at("begin", undo=True), {"pull_digest": DIGEST}),
    (journal_at("created"), {"undo": 1}),
    (journal_at("created"), {"undo": "ja"}),
    (journal_at("begin"), {"pull_digest": "sha256:" + "d" * 63}),
    (journal_at("begin"), {"pull_digest": REPO + "@" + DIGEST}),
    (None, {"undo": True}),
    (None, {"pull_digest": DIGEST}),
    (journal_at("created"), {"slot": slot_for("update").to_json()}),
    (journal_at("created"), {"slot": dataclasses.replace(slot_for("update"), until=-1)}),
    (journal_at("created"), {"slot": dataclasses.replace(slot_for("update"), repo_digest="docker.io/x@" + DIGEST)}),
])
def test_bind_refuses_parts_that_do_not_fit(journal, options):
    options = dict(options)
    own_id = options.pop("own_id", OWN)
    with pytest.raises(E.NotBound):
        E.Engine("/nicht/da").bind(journal, own_id, **options)


def test_binding_is_immutable_and_rechecked_on_replace():
    binding = E.Engine("/nicht/da").bind(journal_at("created"), OWN)
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.own_id = OTHER  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.undo = True  # type: ignore[misc]
    with pytest.raises(E.NotBound):
        dataclasses.replace(binding, own_id=OLD)  # auch eine Kopie wird beim Anlegen geprueft
    with pytest.raises(E.NotBound):
        dataclasses.replace(binding, undo=True, journal=journal_at("committed"))


def test_only_the_latest_binding_of_this_engine_counts(fake, client):
    # Nach jedem geschriebenen Schritt holt der Ablauf eine neue Bindung; eine aeltere gilt dann nicht mehr -- auch
    # wenn sie den Aufruf fuer sich erlaubt haette.
    tagged = bound(fake, client, journal_at("tagged"))
    bound(fake, client, journal_at("creating"))
    with pytest.raises(E.NotBound, match="zuletzt"):
        client.tag_image(NEW_IMAGE, REPO, "latest", binding=tagged)
    # Eine Kopie gilt so wenig wie eine ohne `Engine.bind` gebaute, selbst mit denselben Werten.
    current = bound(fake, client, journal_at("started"))
    for other in (dataclasses.replace(current), copy.copy(current),
                  E.Binding(own_id=OWN, journal=journal_at("started"), slot=None, pull_digest=None, undo=False,
                            repository=REPO)):
        assert other == current and other is not current
        with pytest.raises(E.NotBound, match="zuletzt"):
            client.start_container(NEW, binding=other)
    client.start_container(NEW, binding=current)
    assert len(changes(fake)) == 1


def test_a_failed_bind_leaves_no_binding_in_force(fake, client):
    binding = bound(fake, client, journal_at("started"))
    with pytest.raises(E.NotBound):
        client.bind(journal_at("started"), OLD)  # das Journal nennt die "eigene" ID
    with pytest.raises(E.NotBound, match="zuletzt"):
        client.start_container(NEW, binding=binding)
    assert changes(fake) == []


def test_binding_of_another_engine_with_the_same_repository_is_refused(fake, client):
    other = E.Engine(fake.path)
    other.negotiate()
    binding = bound(fake, other, journal_at("started"))
    with pytest.raises(E.NotBound, match="zuletzt"):
        client.start_container(NEW, binding=binding)
    other.start_container(NEW, binding=binding)
    assert len(changes(fake)) == 1


def test_binding_of_another_engine_is_refused(fake, client):
    other_repository = "registry.test/x/deck"
    other = E.Engine(fake.path, repository=other_repository)
    binding = other.bind(journal_at("created", tag_text=other_repository + ":latest", repository=other_repository),
                         OWN)
    with pytest.raises(E.NotBound):
        client.start_container(OLD, binding=binding)
    with pytest.raises(E.NotBound):
        client.bind(journal_at("created", tag_text=other_repository + ":latest", repository=other_repository), OWN)
    assert changes(fake) == []


def test_the_floating_tag_comes_from_the_journal(fake, client):
    journal = journal_at("tagged", tag_text=REPO + ":0.7")
    binding = bound(fake, client, journal)
    with pytest.raises(E.NotBound):
        client.tag_image(NEW_IMAGE, REPO, "latest", binding=binding)
    client.tag_image(NEW_IMAGE, REPO, "0.7", binding=binding)
    assert len(changes(fake)) == 1


def test_restart_policy_may_go_back_to_exactly_the_old_one(fake, client):
    binding = bound(fake, client, journal_at("started", restart=("on-failure", 3)), undo=True)
    client.set_restart_policy(OLD, "on-failure", 3, binding=binding)
    with pytest.raises(E.NotBound):
        client.set_restart_policy(OLD, "on-failure", 4, binding=binding)
    with pytest.raises(E.NotBound):
        client.set_restart_policy(OLD, "unless-stopped", 0, binding=binding)
    assert len(changes(fake)) == 1


def test_engine_docstring_describes_the_binding_and_its_limit():
    doc = E.__doc__
    assert "Noch nicht umgesetzt" not in doc
    assert "Engine.bind(journal, own_id" in doc and "NotBound" in doc
    for rule in ("`create`", "`start`", "`stop`", "`rename`", "`update`", "`remove_container`", "`connect`",
                 "`tag_image`", "`remove_protect_tag`", "`pull`"):
        assert rule in doc, rule
    # Gegen eine falsche Zielwahl hilft sie nicht -- das muss im Kopf des Moduls stehen.
    text = " ".join(doc.split())
    assert "keine gegen eine falsche Zielwahl" in text and "target.py" in text
    assert "erneute Pruefung der Container vor jedem Schritt" in text


# ---------------------------------------------------------------------------
# Die Fake-Engine prueft dieselben Regeln (am Client vorbei)
# ---------------------------------------------------------------------------


def raw(fake: FakeEngine, method: str, path: str, query: dict | None = None, body: object = None) -> int:
    """Eine Anfrage am Client vorbei (ohne Allowlist und ohne Bindung); gibt den Status zurueck."""
    conn = E._UnixConnection("localhost", timeout=5)
    conn.socket_path = fake.path
    url = "/v1.54" + path + ("?" + urlencode(query) if query else "")
    payload = None if body is None else json.dumps(body).encode()
    headers = {"Host": "localhost"} if payload is None else {"Host": "localhost", "Content-Type": "application/json"}
    try:
        conn.request(method, url, body=payload, headers=headers)
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


@pytest.fixture
def watched():
    """Eine Fake-Engine, deren Verstoesse der Test selbst ansieht."""
    with FakeEngine(World()) as server:
        answer_everything(server)
        yield server


def guard(fake, step, action="update", *, slot=None, own_id=OWN, **journal_options):
    journal = None if step is None else journal_at(step, action, **journal_options).to_json()
    fake.guard = JournalGuard.fixed(own_id, journal, None if slot is None else slot.to_json())


RAW_REFUSED = [
    ("start_other", "created", {}, ("POST", f"/containers/{OTHER}/start", None, None)),
    ("start_own", "created", {}, ("POST", f"/containers/{OWN}/start", None, None)),
    ("stop_other", "created", {}, ("POST", f"/containers/{OTHER}/stop", {"t": 30}, None)),
    ("rename_other_name", "renamed", {}, ("POST", f"/containers/{OLD}/rename", {"name": "anderer"}, None)),
    ("rename_other", "renamed", {}, ("POST", f"/containers/{OTHER}/rename", {"name": NAME}, None)),
    ("update_new", "created", {}, ("POST", f"/containers/{NEW}/update", None,
                                   {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}})),
    ("update_always", "created", {}, ("POST", f"/containers/{OLD}/update", None,
                                      {"RestartPolicy": {"Name": "always", "MaximumRetryCount": 0}})),
    ("remove_old_before_commit", "created", {}, ("DELETE", f"/containers/{OLD}", {"v": "0", "force": "0"}, None)),
    ("remove_new_after_commit", "committed", {}, ("DELETE", f"/containers/{NEW}", {"v": "0", "force": "0"}, None)),
    ("remove_own", "committed", {}, ("DELETE", f"/containers/{OWN}", {"v": "0", "force": "0"}, None)),
    ("connect_other", "created", {}, ("POST", f"/networks/{NETWORK}/connect", None,
                                      {"Container": OTHER, "EndpointConfig": {}})),
    ("connect_before_created", "creating", {}, ("POST", f"/networks/{NETWORK}/connect", None,
                                                {"Container": NEW, "EndpointConfig": {}})),
    ("create_other_name", "creating", {}, ("POST", "/containers/create", {"name": "anderer"}, CREATE_BODY)),
    ("create_in_tagged", "tagged", {}, ("POST", "/containers/create", {"name": NAME}, CREATE_BODY)),
    ("create_other_image", "creating", {}, ("POST", "/containers/create", {"name": NAME},
                                            {"Image": "docker.io/fremd:latest", "HostConfig": {}})),
    ("retag_other_image", "tagged", {}, ("POST", f"/images/{OTHER_IMAGE}/tag", {"repo": REPO, "tag": "latest"}, None)),
    ("retag_other_tag", "tagged", {}, ("POST", f"/images/{NEW_IMAGE}/tag", {"repo": REPO, "tag": "0.7"}, None)),
    ("protect_new_image", "protected", {}, ("POST", f"/images/{NEW_IMAGE}/tag",
                                            {"repo": PREVIOUS, "tag": "0.7.1"}, None)),
    ("protect_other_version", "protected", {}, ("POST", f"/images/{OLD_IMAGE}/tag",
                                                {"repo": PREVIOUS, "tag": "0.6.9"}, None)),
    ("pull_unresolved", "begin", {}, ("POST", "/images/create", {"fromImage": REPO, "tag": OTHER_DIGEST}, None)),
    ("pull_after_begin", "pulled", {}, ("POST", "/images/create", {"fromImage": REPO, "tag": DIGEST}, None)),
    ("pull_other_repository", "begin", {}, ("POST", "/images/create", {"fromImage": "docker.io/fremd", "tag": DIGEST},
                                            None)),
    ("pull_from_source", "begin", {}, ("POST", "/images/create",
                                       {"fromImage": REPO, "tag": DIGEST, "fromSrc": "http://x"}, None)),
    ("pull_by_tag", "begin", {}, ("POST", "/images/create", {"fromImage": REPO, "tag": "latest"}, None)),
    ("untag_any", "created", {}, ("DELETE", f"/images/{PREVIOUS}:0.1.0", None, None)),
    ("untag_old_after_commit", "committed", {"slot": "update"}, ("DELETE", f"/images/{PREVIOUS}:0.7.0", None, None)),
    ("untag_without_journal", None, {}, ("DELETE", f"/images/{PREVIOUS}:0.6.9", None, None)),
    ("start_without_journal", None, {"slot": "update"}, ("POST", f"/containers/{OLD}/start", None, None)),
    # vorwaerts nur im angekuendigten Schritt
    ("start_new_too_early", "old_stopped", {}, ("POST", f"/containers/{NEW}/start", None, None)),
    ("start_old_after_commit", "committed", {}, ("POST", f"/containers/{OLD}/start", None, None)),
    ("stop_old_too_early", "created", {}, ("POST", f"/containers/{OLD}/stop", {"t": 30}, None)),
    ("stop_new_after_commit", "committed", {}, ("POST", f"/containers/{NEW}/stop", {"t": 30}, None)),
    ("rename_previous_too_early", "protected", {}, ("POST", f"/containers/{OLD}/rename",
                                                    {"name": NAME + "-previous"}, None)),
    ("rename_new", "created", {}, ("POST", f"/containers/{NEW}/rename", {"name": NAME}, None)),
    ("rename_after_commit", "committed", {}, ("POST", f"/containers/{OLD}/rename", {"name": NAME}, None)),
    ("update_no_too_early", "created", {}, ("POST", f"/containers/{OLD}/update", None,
                                            {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}})),
    ("retag_new_too_early", "renamed", {}, ("POST", f"/images/{NEW_IMAGE}/tag", {"repo": REPO, "tag": "latest"},
                                            None)),
    ("retag_old_after_commit", "committed", {}, ("POST", f"/images/{OLD_IMAGE}/tag", {"repo": REPO, "tag": "latest"},
                                                 None)),
    ("protect_too_early", "pulled", {}, ("POST", f"/images/{OLD_IMAGE}/tag", {"repo": PREVIOUS, "tag": "0.7.0"},
                                         None)),
    ("connect_later", "old_stopped", {}, ("POST", f"/networks/{NETWORK}/connect", None,
                                          {"Container": NEW, "EndpointConfig": {}})),
    # die Form, unabhaengig vom Journal
    ("remove_v1", "created", {}, ("DELETE", f"/containers/{NEW}", {"v": "1", "force": "0"}, None)),
    ("remove_force", "created", {}, ("DELETE", f"/containers/{NEW}", {"v": "0", "force": "1"}, None)),
    ("remove_without_query", "created", {}, ("DELETE", f"/containers/{NEW}", None, None)),
    ("remove_link", "created", {}, ("DELETE", f"/containers/{NEW}", {"v": "0", "force": "0", "link": "1"}, None)),
    ("untag_force", "started", {}, ("DELETE", f"/images/{PREVIOUS}:0.7.0", {"force": "1"}, None)),
    ("untag_noprune", "started", {}, ("DELETE", f"/images/{PREVIOUS}:0.7.0", {"noprune": "1"}, None)),
    ("tag_force", "tagged", {}, ("POST", f"/images/{NEW_IMAGE}/tag", {"repo": REPO, "tag": "latest", "force": "1"},
                                 None)),
    ("stop_signal", "old_stopped", {}, ("POST", f"/containers/{OLD}/stop", {"t": 30, "signal": "SIGKILL"}, None)),
    ("stop_t_text", "old_stopped", {}, ("POST", f"/containers/{OLD}/stop", {"t": "-1"}, None)),
    ("stop_t_too_long", "old_stopped", {}, ("POST", f"/containers/{OLD}/stop", {"t": E.MAX_STOP_T + 1}, None)),
    ("stop_t_far_too_long", "old_stopped", {}, ("POST", f"/containers/{OLD}/stop", {"t": 9999}, None)),
    ("start_with_body", "started", {}, ("POST", f"/containers/{NEW}/start", None, {"x": 1})),
    ("update_more", "old_stopped", {}, ("POST", f"/containers/{OLD}/update", None,
                                        {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}, "Memory": 1})),
    ("update_bool", "old_stopped", {}, ("POST", f"/containers/{OLD}/update", None,
                                        {"RestartPolicy": {"Name": "no", "MaximumRetryCount": False}})),
    ("connect_second_spelling", "created", {}, ("POST", f"/networks/{NETWORK}/connect", None,
                                                {"Container": NEW, "EndpointConfig": {}, "container": OTHER})),
    ("create_platform", "creating", {}, ("POST", "/containers/create", {"name": NAME, "platform": "linux/arm64"},
                                         CREATE_BODY)),
    ("create_entrypoint", "creating", {}, ("POST", "/containers/create", {"name": NAME},
                                           {**CREATE_BODY, "entrypoint": ["sh"]})),
    ("create_autoremove", "creating", {}, ("POST", "/containers/create", {"name": NAME},
                                           {"Image": TAG_TEXT, "HostConfig": {"AutoRemove": True}})),
    ("create_second_image", "creating", {}, ("POST", "/containers/create", {"name": NAME},
                                             {**CREATE_BODY, "image": "docker.io/fremd:latest"})),
]


@pytest.mark.parametrize(("label", "step", "options", "request_parts"), RAW_REFUSED, ids=[r[0] for r in RAW_REFUSED])
def test_the_fake_engine_refuses_the_same_probes(watched, label, step, options, request_parts):
    if label.startswith("pull"):
        raw(watched, "GET", f"/distribution/{REPO}:latest/json")  # die Fake-Engine kennt jetzt DIGEST
    slot = slot_for(options["slot"]) if "slot" in options else None
    guard(watched, step, slot=slot)
    method, path, query, body = request_parts
    assert raw(watched, method, path, query, body) == 599
    assert len(watched.violations) == 1 and path in watched.violations[0]


RAW_ALLOWED = [
    ("start_new", "started", ("POST", f"/containers/{NEW}/start", None, None)),
    ("rename_previous", "renamed", ("POST", f"/containers/{OLD}/rename", {"name": NAME + "-previous"}, None)),
    ("update_no", "old_stopped", ("POST", f"/containers/{OLD}/update", None,
                                  {"RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}})),
    ("update_back", "started", ("POST", f"/containers/{OLD}/update", None,
                                {"RestartPolicy": {"Name": RESTART[0], "MaximumRetryCount": RESTART[1]}})),
    ("stop_old", "old_stopped", ("POST", f"/containers/{OLD}/stop", {"t": 30}, None)),
    ("stop_old_longest", "old_stopped", ("POST", f"/containers/{OLD}/stop", {"t": E.MAX_STOP_T}, None)),
    ("stop_new", "started", ("POST", f"/containers/{NEW}/stop", {"t": 30}, None)),
    ("start_old", "started", ("POST", f"/containers/{OLD}/start", None, None)),
    ("rename_back", "started", ("POST", f"/containers/{OLD}/rename", {"name": NAME}, None)),
    ("remove_new", "created", ("DELETE", f"/containers/{NEW}", {"v": "0", "force": "0"}, None)),
    ("remove_old_after_commit", "committed", ("DELETE", f"/containers/{OLD}", {"v": "0", "force": "0"}, None)),
    ("connect_new", "created", ("POST", f"/networks/{NETWORK}/connect", None, {"Container": NEW,
                                                                              "EndpointConfig": {}})),
    ("create", "creating", ("POST", "/containers/create", {"name": NAME}, CREATE_BODY)),
    ("retag_new", "tagged", ("POST", f"/images/{NEW_IMAGE}/tag", {"repo": REPO, "tag": "latest"}, None)),
    ("retag_old", "started", ("POST", f"/images/{OLD_IMAGE}/tag", {"repo": REPO, "tag": "latest"}, None)),
    ("protect", "protected", ("POST", f"/images/{OLD_IMAGE}/tag", {"repo": PREVIOUS, "tag": "0.7.0"}, None)),
    ("pull_resolved", "begin", ("POST", "/images/create", {"fromImage": REPO, "tag": DIGEST}, None)),
    ("untag_own", "started", ("DELETE", f"/images/{PREVIOUS}:0.7.0", None, None)),
]


@pytest.mark.parametrize(("label", "step", "request_parts"), RAW_ALLOWED, ids=[r[0] for r in RAW_ALLOWED])
def test_the_fake_engine_lets_the_calls_of_the_flow_through(watched, label, step, request_parts):
    if label.startswith("pull"):
        raw(watched, "GET", f"/distribution/{REPO}:latest/json")
    guard(watched, step)
    method, path, query, body = request_parts
    assert raw(watched, method, path, query, body) != 599
    assert watched.violations == []


def test_the_fake_engine_takes_the_digest_of_the_slot_for_a_rollback(watched):
    guard(watched, "begin", "rollback", slot=slot_for("rollback"))
    assert raw(watched, "POST", "/images/create", {"fromImage": REPO, "tag": PREV_DIGEST}) == 200
    assert watched.violations == []
    # ... und beim Rueckweg nur diesen, auch wenn das bewegliche Tag eben aufgeloest wurde
    raw(watched, "GET", f"/distribution/{REPO}:latest/json")
    assert raw(watched, "POST", "/images/create", {"fromImage": REPO, "tag": DIGEST}) == 599
    # ... und beim Update nie den des Slots
    guard(watched, "begin", slot=slot_for("update"))
    assert raw(watched, "POST", "/images/create", {"fromImage": REPO, "tag": PREV_DIGEST}) == 599
    assert len(watched.violations) == 2


def test_the_fake_engine_refuses_changes_without_a_guard(watched):
    assert watched.guard is None
    assert raw(watched, "POST", f"/containers/{OLD}/start") == 599
    assert watched.violations == [f"aendernd ohne Journal-Waechter: POST /containers/{OLD}/start"]
    assert raw(watched, "GET", f"/distribution/{REPO}:latest/json") == 200  # Lesen braucht keinen


def test_a_binding_to_a_journal_not_yet_written_is_caught_by_the_fake_engine(watched, tmp_path, uid):
    # Write-ahead: der Ablauf schreibt den Schritt, *bevor* er ihn ausfuehrt. Bindet er an ein Journal, das noch nicht
    # auf der Platte steht, laesst der Client den Aufruf zwar zu -- die Fake-Engine, die das Journal auf der Platte
    # liest, aber nicht.
    store = StateStore(tmp_path, expected_uid=uid)
    store.open()
    try:
        store.write_journal(journal_at("tagged"))
        watched.guard = JournalGuard.from_state_dir(tmp_path, OWN)
        engine = E.Engine(watched.path)
        engine.negotiate()
        early = engine.bind(journal_at("creating"), OWN)  # noch nicht geschrieben
        with pytest.raises(E.EngineError) as exc:
            engine.create_container(NAME, dict(CREATE_BODY), binding=early)
        assert exc.value.status == 599
        assert len(watched.violations) == 1 and "creating" not in watched.violations[0]
        watched.violations.clear()
        store.write_journal(journal_at("creating"))
        assert engine.create_container(NAME, dict(CREATE_BODY), binding=early) == (NEW, 0)
        assert watched.violations == []
    finally:
        store.close()


def test_the_fake_engine_reads_the_slot_from_the_state_dir(watched, tmp_path, uid):
    store = StateStore(tmp_path, expected_uid=uid)
    store.open()
    try:
        state = store.load_state(NOW)
        state.slot = slot_for("update")
        store.save_state(state)
        watched.guard = JournalGuard.from_state_dir(tmp_path, OWN)
        assert raw(watched, "DELETE", f"/images/{PREVIOUS}:0.6.9") == 200  # ohne Journal: das Tag des Slots
        assert raw(watched, "DELETE", f"/images/{PREVIOUS}:0.7.0") == 599
        assert len(watched.violations) == 1
    finally:
        store.close()


def test_the_journal_helper_builds_valid_journals_for_every_step():
    for action in policy.ACTIONS:
        for step in policy.STEPS:
            journal = journal_at(step, action)
            assert isinstance(journal, Journal)
            assert Journal.from_json(journal.to_json()) == journal


# ---------------------------------------------------------------------------
# Client und Fake-Engine entscheiden gleich (ueber alle Schritte, Aktionen und Aufrufe)
# ---------------------------------------------------------------------------


def _candidates():
    """Viele aendernde Aufrufe, jeder als Aufruf des Clients und als die Anfrage, die er schickt."""
    floating = (REPO, "latest")
    for ref in (OLD, NEW, OWN, OTHER):
        yield (lambda c, b, r=ref: c.start_container(r, binding=b), "POST", f"/containers/{ref}/start", {}, None)
        yield (lambda c, b, r=ref: c.stop_container(r, 30, binding=b), "POST", f"/containers/{ref}/stop",
               {"t": ["30"]}, None)
        yield (lambda c, b, r=ref: c.remove_container(r, binding=b), "DELETE", f"/containers/{ref}",
               {"v": ["0"], "force": ["0"]}, None)
        yield (lambda c, b, r=ref: c.connect_network(NETWORK, r, {}, binding=b), "POST",
               f"/networks/{NETWORK}/connect", {}, {"Container": ref, "EndpointConfig": {}})
        for name in (NAME, NAME + "-previous", "anderer"):
            yield (lambda c, b, r=ref, n=name: c.rename_container(r, n, binding=b), "POST",
                   f"/containers/{ref}/rename", {"name": [name]}, None)
        for restart in (("no", 0), RESTART, ("always", 0)):
            yield (lambda c, b, r=ref, p=restart: c.set_restart_policy(r, *p, binding=b), "POST",
                   f"/containers/{ref}/update", {},
                   {"RestartPolicy": {"Name": restart[0], "MaximumRetryCount": restart[1]}})
    for name, image in itertools.product((NAME, NAME + "-previous"), (TAG_TEXT, REPO + ":0.7")):
        body = {"Image": image, "HostConfig": {}}
        yield (lambda c, b, n=name, x=body: c.create_container(n, dict(x), binding=b), "POST", "/containers/create",
               {"name": [name]}, body)
    for image in (OLD_IMAGE, NEW_IMAGE, PREV_IMAGE, OTHER_IMAGE):
        for repo, tag in (floating, (REPO, "0.7"), (PREVIOUS, "0.7.0"), (PREVIOUS, "0.7.1"), (PREVIOUS, "0.6.9")):
            yield (lambda c, b, i=image, r=repo, t=tag: c.tag_image(i, r, t, binding=b), "POST", f"/images/{image}/tag",
                   {"repo": [repo], "tag": [tag]}, None)
    for version in ("0.7.0", "0.7.1", "0.6.9", "0.1.0"):
        yield (lambda c, b, v=version: c.remove_protect_tag(v, binding=b), "DELETE", f"/images/{PREVIOUS}:{version}",
               {}, None)
    for digest in (DIGEST, PREV_DIGEST, OTHER_DIGEST):
        yield (lambda c, b, d=digest: c.pull(d, binding=b), "POST", "/images/create",
               {"fromImage": [REPO], "tag": [digest]}, None)


def _contexts():
    """(Journal, Slot, Digest fuer den Pull, moegliche Werte fuer `undo`) -- ohne Journal, in jedem Schritt und mit
    angekuendigtem Rueckbau (dann gibt es nur noch die Bindung mit `undo`)."""
    for slot in (None, slot_for("update")):
        yield None, slot, None, (False,)
    for action, step in itertools.product(policy.ACTIONS, policy.STEPS):
        digest = DIGEST if action == "update" else PREV_DIGEST
        undo = (False,) if step == "committed" else (False, True)
        yield journal_at(step, action), slot_for(action), digest, undo
        if step != "committed":
            yield journal_at(step, action, undo=True, code=policy.TIMEOUT), slot_for(action), digest, (True,)


def test_client_and_fake_engine_allow_exactly_the_same_calls():
    # Die Fake-Engine sieht nicht, ob der Client gerade zurueckbaut: sie erlaubt, was der Client vorwaerts *oder* im
    # Rueckbau erlaubt -- und nichts sonst. Laeuft beides auseinander, haette der Ablauf in den Tests eine andere
    # Schranke als im Betrieb.
    engine = E.Engine("/nicht/da")  # nie ausgehandelt: was die Bindung durchlaesst, endet bei `not_negotiated`
    candidates = list(_candidates())
    mismatches, allowed = [], 0
    for journal, slot, digest, undo_values in _contexts():
        fake_guard = JournalGuard.fixed(OWN, None if journal is None else journal.to_json(),
                                        None if slot is None else slot.to_json())
        for call, method, path, query, body in candidates:
            client_ok = False
            for undo in undo_values:
                binding = engine.bind(journal, OWN, slot=slot, pull_digest=digest, undo=undo)
                try:
                    call(engine, binding)
                except E.NotBound:
                    continue
                except E.EngineError as exc:
                    assert exc.reason == "not_negotiated"
                    client_ok = True
            request = Request(method=method, path=path, raw_path=path, version="1.54", query=query, body=body,
                              headers={})
            assert form_problem(request) is None, path
            fake_ok = fake_guard.problem(request, {DIGEST}) is None
            allowed += client_ok
            if client_ok != fake_ok:
                where = "ohne Journal" if journal is None else f"{journal.action}/{journal.step}"
                mismatches.append(f"{where}: {method} {path} {query} {body}: Client {client_ok}, Fake {fake_ok}")
    assert mismatches == []
    assert allowed >= 100  # die Probe deckt die erlaubten Aufrufe wirklich ab, nicht nur Ablehnungen

