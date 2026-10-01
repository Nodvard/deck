"""`policy.py`: reine Regeln des Update-Helfers (Bauplan 2c-2, Ebene 1 der Teststrategie).

Referenzen und Tags (M7), Versionen (M1/M2), Anforderungen (M2), Grenzen (M14), Rueckweg-Slot (M13) und die
festen Codes. Viele Faelle kommen aus den gemeinsamen Vektoren (`vectors/*.json`), die spaeter auch das Dashboard
prueft.
"""

from __future__ import annotations

import json
import re

import pytest
from nodvard_deck_updater import policy
from nodvard_deck_updater.policy import Refusal
from updater_support import NOW, load_vectors

RID = "6f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60"
RID2 = "0f1c2b0e-9a4d-4c1e-8f00-1b2c3d4e5f60"
HEX = "a" * 64
CID = "c" * 64
IMG_OLD = "sha256:" + "1" * 64
IMG_NEW = "sha256:" + "2" * 64
REPO_DIGEST = "ghcr.io/nodvard/deck@sha256:" + "3" * 64


def code_of(func, *args, **kwargs) -> str | None:
    try:
        func(*args, **kwargs)
    except Refusal as exc:
        return exc.code
    return None


def request_bytes(**over) -> bytes:
    doc = {"v": 1, "id": RID, "action": "update", "version": "0.7.1", "created_at": NOW}
    doc.update(over)
    return json.dumps(doc).encode()


def make_request(**over) -> policy.Request:
    return policy.parse_request(request_bytes(**over), now=NOW, file_id=over.get("id", RID))


# ---------------------------------------------------------------------------
# Codes und Protokoll-Konstanten
# ---------------------------------------------------------------------------


def test_every_code_outcome_state_and_step_is_a_fixed_identifier():
    for value in (*policy.CODES, *policy.OUTCOMES, *policy.STATES, *policy.STEPS, *policy.ACTIONS):
        assert re.fullmatch(r"[a-z_]{1,40}", value), value


def test_code_groups_are_disjoint_and_complete():
    groups = (policy.SETUP_CODES, policy.TARGET_CODES, policy.REQUEST_CODES, policy.FLOW_CODES)
    assert sum(len(g) for g in groups) == len(policy.CODES)
    assert len(set(policy.STEPS)) == len(policy.STEPS)


def test_refusal_only_accepts_fixed_codes_and_identifiers():
    exc = Refusal(policy.BAD_REQUEST)
    assert exc.code == "bad_request" and exc.detail is None and str(exc) == "bad_request"
    assert str(Refusal(policy.CHANNEL_UNSAFE, "root_owner")) == "channel_unsafe:root_owner"
    with pytest.raises(ValueError):
        Refusal("Fehler: Docker antwortet nicht")
    with pytest.raises(ValueError):
        Refusal(policy.BAD_REQUEST, "Freitext mit Leerzeichen")


def test_protocol_vector_matches_the_constants():
    vec = load_vectors("protocol.json")
    assert vec["proto"] == policy.PROTOCOL
    assert vec["request_versions"] == list(policy.REQUEST_VERSIONS)
    assert set(vec["request_keys"]) == policy.REQUEST_KEYS
    assert vec["actions"] == list(policy.ACTIONS)
    assert vec["states"] == list(policy.STATES)
    assert vec["outcomes"] == list(policy.OUTCOMES)
    assert vec["steps"] == list(policy.STEPS)
    assert vec["codes"]["setup"] == list(policy.SETUP_CODES)
    assert vec["codes"]["target"] == list(policy.TARGET_CODES)
    assert vec["codes"]["request"] == list(policy.REQUEST_CODES)
    assert vec["codes"]["flow"] == list(policy.FLOW_CODES)
    assert vec["code_pattern"] == policy.CODE_RE.pattern
    assert vec["repository"] == policy.REPOSITORY
    assert vec["version_label"] == policy.VERSION_LABEL
    assert vec["min_version"] == policy.MIN_VERSION
    limits = vec["limits"]
    assert limits == {
        "request_max_bytes": policy.REQUEST_MAX_BYTES,
        "request_max_age_s": policy.REQUEST_MAX_AGE_S,
        "request_max_future_s": policy.REQUEST_MAX_FUTURE_S,
        "status_max_bytes": policy.STATUS_MAX_BYTES,
        "results_max": policy.RESULTS_MAX,
        "action_interval_s": policy.ACTION_INTERVAL_S,
        "block_after_rollback_s": policy.BLOCK_AFTER_ROLLBACK_S,
        "seen_ttl_s": policy.SEEN_TTL_S,
        "slot_ttl_s": policy.SLOT_TTL_S,
    }


def test_limits_are_the_ones_from_the_plan():
    assert policy.REPOSITORY == "ghcr.io/nodvard/deck"
    assert (policy.REQUEST_MAX_BYTES, policy.REQUEST_MAX_AGE_S, policy.REQUEST_MAX_FUTURE_S) == (4096, 600, 60)
    assert (policy.ACTION_INTERVAL_S, policy.BLOCK_AFTER_ROLLBACK_S, policy.SEEN_TTL_S) == (600, 86400, 3600)
    assert policy.SLOT_TTL_S == 7 * 86400 and policy.DEADLINE_S == 900
    assert policy.STATUS_MAX_BYTES == 16 * 1024 and policy.RESULTS_MAX == 10
    assert policy.is_version(policy.MIN_VERSION)


# ---------------------------------------------------------------------------
# Versionen (M1, M2)
# ---------------------------------------------------------------------------

VERSIONS = load_vectors("versions.json")


@pytest.mark.parametrize("text", VERSIONS["valid"])
def test_valid_versions(text):
    assert policy.is_version(text)
    assert policy.parse_version(text) == tuple(int(p) for p in text.split("."))


@pytest.mark.parametrize("text", VERSIONS["invalid"] + VERSIONS["not_a_string"])
def test_invalid_versions(text):
    assert not policy.is_version(text)
    assert policy.parse_version(text) is None


@pytest.mark.parametrize(("candidate", "current", "newer"), VERSIONS["newer"])
def test_is_newer(candidate, current, newer):
    assert policy.is_newer(candidate, current) is newer


def test_unicode_digits_never_count_even_if_python_calls_them_digits():
    for text in ("0.6.١", "０.7.0", "१.2.3", "0.\U0001d7ce.0"):
        assert any(ch.isdigit() and not ch.isascii() for ch in text)
        assert not policy.is_version(text)
    assert not policy.MINOR_TAG_RE.fullmatch("0.٧")


def test_version_label_comes_only_from_image_labels():
    assert policy.image_version({policy.VERSION_LABEL: "0.7.1", "other": "x"}) == "0.7.1"
    for labels in ({}, None, [], {policy.VERSION_LABEL: None}, {policy.VERSION_LABEL: "0.7.1-rc1"},
                   {policy.VERSION_LABEL: 7}, {policy.VERSION_LABEL: "v0.7.1"}, {"org.opencontainers.image.Version": "0.7.1"}):
        assert code_of(policy.image_version, labels) == "no_version_label", labels


def test_target_version_minimum():
    assert code_of(policy.check_target_version, policy.MIN_VERSION) is None
    assert code_of(policy.check_target_version, "99.0.0") is None
    assert code_of(policy.check_target_version, "0.6.9") == "version_too_old"
    assert code_of(policy.check_target_version, "0.0.0") == "version_too_old"
    assert code_of(policy.check_target_version, "kaputt") == "no_version_label"


def test_only_forward_and_label_must_match_exactly():
    assert code_of(policy.check_newer, "0.7.1", "0.7.0") is None
    assert code_of(policy.check_newer, "0.7.0", "0.7.0") == "not_newer"
    assert code_of(policy.check_newer, "0.6.9", "0.7.0") == "not_newer"
    assert code_of(policy.check_label, "0.7.1", "0.7.1") is None
    assert code_of(policy.check_label, "0.7.0", "0.7.1") == "tag_not_on_version"  # Tag umgehaengt (Downgrade)
    assert code_of(policy.check_label, "0.8.0", "0.7.1") == "tag_not_on_version"
    assert code_of(policy.check_label, "0.07.1", "0.07.1") == "tag_not_on_version"


# ---------------------------------------------------------------------------
# Image-Referenz und Tag (M7)
# ---------------------------------------------------------------------------

REFS = load_vectors("image_refs.json")


@pytest.mark.parametrize(("image", "expected"), REFS["cases"])
def test_image_reference_table(image, expected):
    assert REFS["repository"] == policy.REPOSITORY
    if expected in policy.CODES:
        assert code_of(policy.floating_tag, image) == expected
    else:
        assert policy.floating_tag(image) == expected


@pytest.mark.parametrize("image", [None, 7, b"ghcr.io/nodvard/deck", ["ghcr.io/nodvard/deck"], "x" * 10_000])
def test_image_reference_wrong_types_are_foreign(image):
    assert code_of(policy.floating_tag, image) == "foreign_image"


def test_repository_can_only_be_changed_by_parameter():
    assert policy.floating_tag("registry.test/nd/deck:0.9", repository="registry.test/nd/deck") == "0.9"
    assert code_of(policy.floating_tag, "ghcr.io/nodvard/deck:latest", repository="registry.test/nd/deck") == "foreign_image"


def test_tag_fits_version():
    assert policy.tag_fits_version("latest", "0.7.1")
    assert policy.tag_fits_version("0.7", "0.7.12")
    assert not policy.tag_fits_version("0.7", "0.8.0")
    assert not policy.tag_fits_version("0.7", "1.7.0")
    assert not policy.tag_fits_version("latest", "0.7")
    assert not policy.tag_fits_version("beta", "0.7.0")
    assert code_of(policy.check_tag_fits, "0.7", "0.8.0") == "tag_not_on_version"
    assert code_of(policy.check_tag_fits, "0.7", "0.7.3") is None


def test_registry_digests():
    other = "docker.io/nodvard/deck@sha256:" + HEX
    digests = [REPO_DIGEST, other, "ghcr.io/nodvard/deck@sha256:short", 7, REPO_DIGEST]
    assert policy.registry_digests(digests) == [REPO_DIGEST]
    assert policy.require_registry_digest(digests) == [REPO_DIGEST]
    for value in ([], None, "x", [other], ["ghcr.io/nodvard/deck:latest"]):
        assert code_of(policy.require_registry_digest, value) == "not_from_registry"


def test_id_patterns():
    assert policy.is_container_id(CID) and not policy.is_container_id(CID[:12]) and not policy.is_container_id(CID.upper())
    assert policy.is_digest("sha256:" + HEX) and not policy.is_digest(HEX) and not policy.is_digest("sha256:" + HEX + "\n")
    assert policy.is_request_id(RID) and not policy.is_request_id(RID.upper())


# ---------------------------------------------------------------------------
# Anforderung (M2)
# ---------------------------------------------------------------------------

REQUESTS = load_vectors("requests.json")


@pytest.mark.parametrize("case", REQUESTS["cases"], ids=lambda c: c["name"])
def test_request_vectors(case):
    raw = bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else case["raw"].encode("utf-8")
    expect = case["expect"]
    try:
        request = policy.parse_request(raw, now=REQUESTS["now"], file_id=REQUESTS["file_id"])
    except Refusal as exc:
        assert exc.code == expect
    else:
        assert isinstance(expect, dict), f"angenommen, erwartet {expect}"
        assert request == policy.Request(**expect)


def test_request_without_file_id_only_checks_the_content():
    assert policy.parse_request(request_bytes(id=RID2), now=NOW).id == RID2


def test_exactly_4096_bytes_is_allowed_4097_is_not():
    base = request_bytes()
    padded = base[:-1] + b" " * (4096 - len(base)) + b"}"
    assert len(padded) == 4096 and policy.parse_request(padded, now=NOW).id == RID
    assert code_of(policy.parse_request, padded[:-1] + b" }", now=NOW) == "bad_request"


@pytest.mark.parametrize("raw", [None, "text", 7, bytearray(b"{}"), memoryview(b"{}")])
def test_request_wrong_input_types(raw):
    assert code_of(policy.parse_request, raw, now=NOW) == "bad_request"


def test_deep_nesting_is_only_this_requests_problem():
    # 100 000-fach verschachtelt: RecursionError im JSON-Parser wird zu ValueError bzw. bad_request.
    deep = b"[" * 100_000 + b"]" * 100_000
    with pytest.raises(ValueError) as info:
        policy.loads_strict(deep, max_bytes=len(deep))
    assert info.value.__cause__ is None and str(info.value) == "kein gueltiges JSON"
    deep_obj = b'{"a":' * 50_000 + b"1" + b"}" * 50_000
    with pytest.raises(ValueError):
        policy.loads_strict(deep_obj, max_bytes=len(deep_obj))
    # ... und danach geht die naechste Anforderung ganz normal.
    assert code_of(policy.parse_request, b"[" * 2048 + b"]" * 2048, now=NOW) == "bad_request"
    assert make_request().id == RID


@pytest.mark.parametrize("error", [RecursionError, MemoryError, TypeError, KeyError, UnicodeError, ArithmeticError])
def test_any_exception_while_reading_becomes_bad_request(monkeypatch, error):
    def explode(*args, **kwargs):
        raise error("boom")

    monkeypatch.setattr(policy.json, "loads", explode)
    assert code_of(policy.parse_request, request_bytes(), now=NOW) == "bad_request"


def test_error_text_never_contains_the_request():
    secret = request_bytes(version="GEHEIM-123")
    try:
        policy.parse_request(secret, now=NOW)
    except Refusal as exc:
        assert "GEHEIM" not in str(exc) and "GEHEIM" not in repr(exc)
    with pytest.raises(ValueError) as info:
        policy.loads_strict(b'{"x": GEHEIM}', max_bytes=100)
    assert "GEHEIM" not in str(info.value) and info.value.__cause__ is None


def test_huge_integers_do_not_break_the_parser():
    raw = request_bytes().replace(str(NOW).encode(), b"9" * 3900)
    assert len(raw) <= 4096
    assert code_of(policy.parse_request, raw, now=NOW) == "bad_request"
    assert code_of(policy.parse_request, request_bytes(created_at=10**30), now=NOW) == "bad_request"
    # Ueber der Ziffern-Grenze von Python (4300) wirft int() -- auch das bleibt ein ValueError.
    with pytest.raises(ValueError):
        policy.loads_strict(b"9" * 5000, max_bytes=10_000)


def test_loads_strict_rejects_duplicates_at_every_level():
    with pytest.raises(ValueError):
        policy.loads_strict(b'{"a": {"b": 1, "b": 2}}', max_bytes=100)
    assert policy.loads_strict(b'{"a": {"b": 1}, "b": 2}', max_bytes=100) == {"a": {"b": 1}, "b": 2}
    for constant in (b"NaN", b"Infinity", b"-Infinity"):
        with pytest.raises(ValueError):
            policy.loads_strict(b'{"a": ' + constant + b"}", max_bytes=100)
    # Fund 7: Zahlen, die beim Lesen zu +-Unendlich ueberlaufen, sind dasselbe wie `Infinity`.
    for number in (b"1e999", b"-1e999", b"1E+400", b"1" + b"0" * 400 + b".0"):
        for document in (b'{"a": %s}', b"[%s]", b'{"a": {"b": [%s]}}'):
            with pytest.raises(ValueError):
                policy.loads_strict(document % number, max_bytes=1000)
    assert policy.loads_strict(b'{"a": 1.5, "b": -2e3, "c": 1e-999}', max_bytes=100) == {"a": 1.5, "b": -2000.0, "c": 0.0}
    with pytest.raises(ValueError):
        policy.loads_strict(b"{}", max_bytes=1)


def test_dumps_is_ascii_and_compact():
    assert policy.dumps({"b": "ä", "a": 1}) == b'{"a":1,"b":"\\u00e4"}'
    with pytest.raises(ValueError):
        policy.dumps({"a": float("nan")})


# ---------------------------------------------------------------------------
# Grenzen (M14)
# ---------------------------------------------------------------------------


def limits(request=None, *, now=NOW, actions=(), blocked=None, seen=None, hold_until=None):
    return code_of(policy.check_limits, request or make_request(), now=now, actions=list(actions),
                   blocked=blocked or {}, seen=seen or {}, hold_until=hold_until)


def test_one_action_per_ten_minutes():
    assert limits() is None
    assert limits(actions=[NOW - 599]) == "rate_limited"
    assert limits(actions=[NOW - 600]) is None
    assert limits(actions=[NOW - 7200, NOW - 3600]) is None
    assert limits(actions=[NOW + 3600]) == "rate_limited", "Zeitstempel in der Zukunft sperren (Uhr zurueckgesprungen)"


def test_blocked_version_after_rollback_only_blocks_updates_to_it():
    assert limits(blocked={"0.7.1": NOW + 1}) == "blocked_version"
    assert limits(blocked={"0.7.1": NOW}) is None
    assert limits(blocked={"0.7.2": NOW + 86400}) is None
    rollback = make_request(action="rollback", version="0.7.1")
    assert limits(rollback, blocked={"0.7.1": NOW + 1}) is None


def test_replay_of_a_processed_id_within_one_hour():
    assert limits(seen={RID: NOW - 3599}) == "replay"
    assert limits(seen={RID: NOW - 3600}) is None
    assert limits(seen={RID2: NOW}) is None
    assert limits(seen={RID: NOW + 100}) == "replay"


def test_hold_after_broken_state_blocks_everything():
    assert limits(hold_until=NOW + 1) == "state_unsafe"
    assert limits(hold_until=NOW) is None
    assert limits(seen={RID: NOW}, hold_until=NOW + 1) == "replay"


# ---------------------------------------------------------------------------
# Rueckweg-Slot (M13)
# ---------------------------------------------------------------------------


def slot(**over) -> policy.Slot:
    values = {"from_version": "0.7.0", "image_id": IMG_OLD, "repo_digest": REPO_DIGEST, "installed_container_id": CID,
              "installed_image_id": IMG_NEW, "now": NOW}
    values.update(over)
    return policy.new_slot(**values)


def rollback(the_slot, *, version="0.7.0", container=CID, image=IMG_NEW, now=NOW + 60):
    request = make_request(action="rollback", version=version)
    return code_of(policy.check_rollback, request, the_slot, target_container_id=container,
                   target_image_id=image, now=now)


def test_slot_valid_exactly_for_the_installed_container_and_previous_version():
    assert slot().until == NOW + 7 * 86400
    assert rollback(slot()) is None


def test_slot_missing_or_expired():
    assert rollback(None) == "no_previous"
    assert rollback(slot(), now=NOW + 7 * 86400) == "no_previous"
    assert rollback(slot(), now=NOW + 7 * 86400 - 1) is None


def test_slot_with_implausible_end_after_clock_jump_is_not_used():
    assert rollback(slot(), now=NOW - 61) == "no_previous"
    assert rollback(slot(), now=NOW - 60) is None


def test_slot_wrong_target_or_version():
    assert rollback(slot(), container="d" * 64) == "previous_mismatch"
    assert rollback(slot(), image=IMG_OLD) == "previous_mismatch"
    assert rollback(slot(), version="0.6.9") == "previous_mismatch"
    assert rollback(slot(), version="0.7.1") == "previous_mismatch"


def test_slot_below_min_version():
    old = slot(from_version="0.6.0")
    assert rollback(old, version="0.6.0") == "version_too_old"


def test_check_rollback_is_only_for_rollback_requests():
    with pytest.raises(ValueError):
        policy.check_rollback(make_request(), slot(), target_container_id=CID, target_image_id=IMG_NEW, now=NOW)


def test_slot_json_roundtrip_and_strict_validation():
    good = slot()
    assert policy.Slot.from_json(good.to_json()) == good
    broken = [
        {**good.to_json(), "extra": 1},
        {k: v for k, v in good.to_json().items() if k != "until"},
        {**good.to_json(), "from_version": "0.7"},
        {**good.to_json(), "image_id": HEX},
        {**good.to_json(), "installed_image_id": None},
        {**good.to_json(), "installed_container_id": CID[:12]},
        {**good.to_json(), "repo_digest": "docker.io/nodvard/deck@sha256:" + HEX},
        {**good.to_json(), "repo_digest": "ghcr.io/nodvard/deck:latest"},
        {**good.to_json(), "until": True},
        {**good.to_json(), "until": -1},
        {**good.to_json(), "until": 1.5},
        [good.to_json()],
    ]
    for obj in broken:
        with pytest.raises(ValueError):
            policy.Slot.from_json(obj)
    with pytest.raises(ValueError):
        slot(installed_container_id="kaputt")


# ---------------------------------------------------------------------------
# Einstellung aus der Umgebung
# ---------------------------------------------------------------------------


def test_service_name():
    assert policy.SERVICE_ENV == "NODVARD_DECK_UPDATER_SERVICE"
    assert policy.service_name(None) == policy.service_name("") == "nodvard-deck"
    assert policy.service_name("deck_2.prod") == "deck_2.prod"
    for value in ("Deck", "-deck", "deck/x", "deck x", "a" * 64, "deck\n", 7, "déck"):
        with pytest.raises(ValueError):
            policy.service_name(value)
